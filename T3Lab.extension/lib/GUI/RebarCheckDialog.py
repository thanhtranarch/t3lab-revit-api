# -*- coding: utf-8 -*-
"""
RebarCheckDialog.py
===================
WPF dialog for Rebar Check (Tekla: model checks): a single results page (pattern
P4) listing rebar without a valid host, rebar missing from its host's assembly,
assemblies without drawings, duplicate rebar numbers, bars outside their host,
bars without a partition and bars of unknown shape.

* The scan is read-only and starts by itself once the window has painted, so the
  progress bar and Stop button are live while the model is read.
* "Duplicate number" and "Outside host" read bar geometry, which is slow on big
  models: they run the first time their chip is chosen (or on Rescan while it is
  active), with progress and Stop (risk R11).
* Fix selected: "Sync into assembly" rows run ``_assembly.sync_rebar`` inside one
  TransactionGroup; "Assign partition" rows are handed to Cast Unit Manager
  (page "Partition by rule") - the fix logic is never re-implemented here.
* Select in Revit sets the selection and tells the user to close the window; the
  window is modal (decision D8).

The checks and every Revit call live in ``Snippets/_rebar_check.py``; this module
is UI only.

Part of T3Lab Extension.
Author: Tran Tien Thanh
"""

import os

import clr
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from System.Windows import RoutedEventHandler, Visibility
from System.Windows.Controls import CheckBox

from GUI.WPF_Base import T3WPFWindow
from GUI.T3Dialog import (confirm as t3_confirm, show_error as t3_error,
                          show_info as t3_info, show_warning as t3_warning)

from Snippets import _rebar_check as rc
from Snippets._assembly import select_in_revit
from Snippets._compat import short_error
from Snippets._host import resolve_uidoc

GUI_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'RebarCheck.xaml')

DOT_KEYS = {
    "ok": "T3.Success.Accent",
    "warn": "T3.Warning.Accent",
    "fail": "T3.Danger.Accent",
    "progress": "T3.Progress.Fill",
}

PARTITION_FALLBACK = (
    u"Cast Unit Manager is not available in this build. The %d bar(s) are selected in "
    u"Revit now: close this window, open Cast Unit Manager › Partition by rule, "
    u"pick the scope \"Current selection\" and assign the partition there.")
RESULT_LINES_SHOWN = 5


# ── ROW ITEM ─────────────────────────────────────────────────────────────────

class IssueRow(object):
    """One issue as shown in the grid.

    Every text cell is a plain ``str`` attribute; ``is_selected`` is read through
    the checkbox string bridge in the XAML; ``StatusText`` / ``Severity`` feed the
    ``T3.StatusPill`` template ("Muted" has no tint on purpose: Manual rows are
    the ones nothing can fix for you).
    """

    def __init__(self, issue):
        self.issue = issue
        self.is_selected = False
        self.Check = issue.check_label
        element = u"%d" % issue.element_id
        if len(issue.element_ids) > 1:
            element += u" +%d" % (len(issue.element_ids) - 1)
        self.Element = element
        self.Detail = issue.detail
        self.Category = issue.category
        self.Assembly = issue.assembly_mark or rc.NO_ASSEMBLY_TEXT
        self.PartNum = u"%s / %s" % (issue.partition or u"—", issue.number or u"—")
        self.StatusText = issue.fix_label
        self.Severity = u"Warning" if issue.fixable else u"Muted"


# ── WINDOW ───────────────────────────────────────────────────────────────────

class RebarCheckDialog(T3WPFWindow):
    """Main window of Rebar Check."""

    def __init__(self, doc):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc
        self._loading = True
        self._busy = False
        self._close_after = False
        self._phase = u""

        self._scan_data = None        # rc.ScanData of the last scan
        self._legs = None             # {bar_id: leg chain} once the geometry was read
        self._boxes = None            # rc.BoxData once the boxes were read
        self._deep_partial = False    # a geometry read was stopped part way
        self._issues = []             # every rc.Issue
        self._rows = []               # an IssueRow per issue (keeps its tick)
        self._shown = []              # rows in the grid under the current filter

        self._check_filter = None     # one check id, None = all
        self._scope = rc.SCOPE_ALL

        self._wire_row_events()
        self._set_status(u"Reading the model…", "progress")
        try:
            self.Closing += self._on_closing
        except Exception:
            pass
        # The first scan waits for the first paint: run inline here, the user would
        # stare at a frozen ribbon with no window and no progress bar.
        self.ContentRendered += self._on_first_render
        self._loading = False

    # ── SETUP ────────────────────────────────────────────────────────────────

    def _wire_row_events(self):
        """Row ticks reach Python through the grid: an event written inside a
        DataTemplate is never wired (the template has its own namescope)."""
        try:
            self.grid_issues.AddHandler(
                CheckBox.ClickEvent, RoutedEventHandler(self.grid_issues_checkbox_clicked), True)
        except Exception:
            pass

    def _brush(self, key):
        """A brush from the T3 stylesheet, or None when the key is missing."""
        try:
            return self.FindResource(key)
        except Exception:
            return None

    def _on_first_render(self, sender=None, e=None):
        try:
            self.ContentRendered -= self._on_first_render
        except Exception:
            pass
        try:
            self._scan_model()
        except Exception as exc:
            self._end_progress()
            self._fail(u"Rebar Check could not read the model", exc,
                       u"Click Rescan to try again.")

    def _on_closing(self, sender, e):
        """Never close under a running scan or fix: stop it, close when it ends."""
        if self._busy:
            e.Cancel = True
            self._close_after = True
            self.stop_clicked()

    # ── STATUS, PROGRESS ─────────────────────────────────────────────────────

    def _set_status(self, text, level="ok"):
        """Status line with its dot; the words always carry the meaning."""
        try:
            self.status_text.Text = text
            brush = self._brush(DOT_KEYS.get(level, DOT_KEYS["ok"]))
            if brush is not None:
                self.dot_status.Fill = brush
        except Exception:
            pass

    def _fail(self, what, exc, next_step):
        """Error dialog in the what / where / next shape, plus the status line."""
        self._set_status(u"%s." % what, "fail")
        t3_error(u"%s. %s" % (what, next_step), title=u"Rebar Check",
                 details=short_error(exc), owner=self)

    def _progress_cb(self, index, total, label):
        """Progress callback handed to the Snippets; False means Stop was pressed."""
        try:
            self.pb_run.Maximum = max(1, total)
        except Exception:
            pass
        text = u"%s — %s / %s" % (self._phase, rc.fmt_count(index), rc.fmt_count(total))
        if label and not label.startswith(u"Reading"):
            text += u" · %s" % label        # e.g. the assembly being synced
        return self.step_progress(index, text)

    def _begin_progress(self, phase):
        self._busy = True
        self._phase = phase
        self.begin_progress(100, disable=[self.btn_fix, self.btn_isolate,
                                          self.btn_select, self.btn_rescan])
        self._set_status(u"%s…" % phase, "progress")

    def _end_progress(self):
        self._busy = False
        self.end_progress()
        self._update_buttons()
        if self._close_after:
            self._close_after = False
            self.Close()

    # ── SCAN ─────────────────────────────────────────────────────────────────

    def _scan_model(self):
        """Read the model once (read-only), then run every check that needs no geometry."""
        self._begin_progress(u"Reading the model")
        try:
            data = rc.scan(self.doc, progress=self._progress_cb)
        except Exception as exc:
            self._end_progress()
            self._fail(u"Rebar Check could not read the model", exc,
                       u"Click Rescan to try again; if it keeps failing, report it to T3Lab.")
            return
        self._end_progress()
        self._scan_data = data
        self._legs = None
        self._boxes = None
        self._deep_partial = False
        if self._check_filter in rc.DEEP_CHECKS and not data.stopped:
            self._read_deep(self._check_filter)
        self._rebuild_issues()

    def _read_deep(self, check):
        """Read the geometry behind `check` once. True when new data was read."""
        data = self._scan_data
        if data is None or not data.rows:
            return False
        try:
            if check == rc.CHECK_DUP and self._legs is None:
                ids = rc.legs_needed(data.rows)
                if not ids:
                    self._legs = {}
                    return True
                self._begin_progress(u"Reading bar shapes")
                try:
                    legs, stopped = rc.collect_legs(self.doc, ids, progress=self._progress_cb)
                finally:
                    self._end_progress()
                self._legs = legs
                self._deep_partial = self._deep_partial or stopped
                return True
            if check == rc.CHECK_BBOX and self._boxes is None and data.hosts is not None:
                self._begin_progress(u"Reading bar boxes")
                try:
                    boxes, stopped = rc.collect_boxes(self.doc, data.rows, data.hosts,
                                                      progress=self._progress_cb)
                finally:
                    self._end_progress()
                self._boxes = boxes
                self._deep_partial = self._deep_partial or stopped
                return True
        except Exception as exc:
            self._fail(u"Rebar Check could not read the bar geometry", exc,
                       u"The other checks still work; click Rescan to try again.")
        return False

    def _rebuild_issues(self):
        """Re-run the checks on the data already read, then refresh everything."""
        data = self._scan_data
        if data is None:
            return
        self._issues = rc.run_checks(data.rows, data.hosts, data.assemblies,
                                     legs=self._legs, boxes=self._boxes)
        self._rows = [IssueRow(issue) for issue in self._issues]
        self._apply_filter()
        self._update_summary()
        self._set_ready_status()

    def _set_ready_status(self):
        data = self._scan_data
        summary = rc.summarize(data.rows, self._issues, data.assemblies)
        text = rc.ready_text(summary)
        notes = []
        if data.stopped:
            notes.append(u"scan stopped, results are partial")
        if self._deep_partial:
            notes.append(u"geometry read stopped, results are partial")
        if notes:
            text += u" \u00b7 " + u"; ".join(notes)
        self._set_status(text, "warn" if (self._issues or notes) else "ok")

    # ── GRID ─────────────────────────────────────────────────────────────────

    def _apply_filter(self):
        """Push the rows that pass the chips and the search box into the grid."""
        issues = rc.filter_issues(self._issues, check=self._check_filter,
                                  scope=self._scope, text=self.tb_search.Text)
        by_issue = dict((id(row.issue), row) for row in self._rows)
        self._shown = [by_issue[id(issue)] for issue in issues if id(issue) in by_issue]
        self.set_items_source(self.grid_issues, self._shown)
        total_rebar = len(self._scan_data.rows) if self._scan_data is not None else 0
        self.grid_issues_empty.Text = rc.empty_text(total_rebar, len(self._issues))
        self.grid_issues_empty.Visibility = (Visibility.Collapsed if self._shown
                                             else Visibility.Visible)
        self.txt_shown.Text = u"%d of %s shown" % (len(self._shown),
                                                    rc.plural(len(self._issues), u"issue"))
        self._sync_header()
        self._update_buttons()

    def _update_summary(self):
        data = self._scan_data
        summary = rc.summarize(data.rows, self._issues, data.assemblies)
        self.txt_n_total.Text = rc.fmt_count(summary["total"])
        self.txt_n_issues.Text = rc.fmt_count(summary["issues"])
        self.txt_n_asm.Text = rc.fmt_count(summary["assemblies"])
        self.txt_pass_rate.Text = u"%d%%" % summary["pass_rate"]
        self.meter_ok.Value = summary["pass_rate"]
        brush = self._brush("T3.Success.Text" if summary["issues"] == 0 else "T3.Warning.Text")
        if brush is not None:
            self.txt_n_issues.Foreground = brush

    def _sync_header(self):
        try:
            self.sync_header_checkbox(self.FindName("chk_all_grid_issues"),
                                      self.grid_issues, "is_selected")
        except Exception:
            pass

    def _ticked(self):
        """Ticked rows that are visible under the current filter."""
        return [row for row in self._shown if row.is_selected]

    def _target_rows(self):
        """Rows Select / Isolate act on: the ticked ones, else every row shown."""
        return self._ticked() or list(self._shown)

    def _update_buttons(self):
        """Button state and counts from the ticks; nothing here touches Revit."""
        if self._busy:
            return
        ticked = self._ticked()
        plan = rc.plan_fix([row.issue for row in ticked])
        self.btn_fix.IsEnabled = plan.fixable
        self.btn_fix_label.Text = (u"Fix selected (%d)" % len(ticked)) if ticked else u"Fix selected"
        if ticked and plan.manual:
            self.btn_fix.ToolTip = (u"%s need a manual fix. Untick them to enable Fix."
                                    % rc.plural(len(plan.manual), u"ticked row"))
        elif not ticked:
            self.btn_fix.ToolTip = u"Tick rows to fix. Only Sync into assembly and Assign partition rows can be fixed here."
        else:
            self.btn_fix.ToolTip = (u"Sync into assembly, or hand the bars to Cast Unit Manager "
                                    u"to assign a partition")
        ids = rc.issue_element_ids([row.issue for row in self._target_rows()])
        self.btn_select.IsEnabled = bool(ids)
        self.btn_isolate.IsEnabled = bool(ids)
        self.btn_select_label.Text = (u"Select in Revit (%d)" % len(ids)) if ids else u"Select in Revit"

    # ── EVENT HANDLERS: FILTER, TICKS ────────────────────────────────────────

    def check_chip_checked(self, sender, e):
        tag = sender.Tag
        self._check_filter = (u"%s" % tag) if tag else None
        if self._loading or self._busy or self._scan_data is None:
            return
        if self._check_filter in rc.DEEP_CHECKS and self._read_deep(self._check_filter):
            self._rebuild_issues()
        else:
            self._apply_filter()

    def scope_chip_checked(self, sender, e):
        self._scope = u"%s" % (sender.Tag or rc.SCOPE_ALL)
        if self._loading or self._busy or self._scan_data is None:
            return
        self._apply_filter()

    def search_text_changed(self, sender, e):
        if self._loading or self._busy or self._scan_data is None:
            return
        self._apply_filter()

    def select_all_grid_issues_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_issues, "is_selected", sender.IsChecked)
        self._update_buttons()

    def grid_issues_checkbox_clicked(self, sender, e):
        if self._loading:
            return
        self._sync_header()
        self._update_buttons()

    def rescan_clicked(self, sender, e):
        if self._busy:
            return
        self._scan_model()

    # ── EVENT HANDLERS: ACTIONS ──────────────────────────────────────────────

    def select_clicked(self, sender, e):
        """Select the ticked elements (or every row shown) in the Revit UI.

        A UI operation, no transaction. The window is modal, so the user only sees
        the selection once it is closed; the status line says so.
        """
        ids = rc.issue_element_ids([row.issue for row in self._target_rows()])
        if not ids:
            self._set_status(u"Nothing to select. Tick rows or change the filter.", "warn")
            return
        uidoc = resolve_uidoc()
        if uidoc is None:
            self._set_status(u"Cannot reach the Revit UI to change the selection. "
                             u"Close this window and run Rebar Check from the ribbon.", "fail")
            return
        count = select_in_revit(uidoc, ids)
        if count:
            self._set_status(u"Selected %s in Revit — close this window to see them."
                             % rc.plural(count, u"element"), "ok")
        else:
            self._set_status(u"Revit did not accept the selection. Rescan and try again.", "fail")

    def isolate_clicked(self, sender, e):
        ids = rc.issue_element_ids([row.issue for row in self._target_rows()])
        if not ids:
            self._set_status(u"Nothing to isolate. Tick rows or change the filter.", "warn")
            return
        try:
            count, error = rc.isolate_in_active_view(self.doc, resolve_uidoc(), ids)
        except Exception as exc:
            self._fail(u"Rebar Check could not isolate the elements", exc,
                       u"Open a plan, section or 3D view and try again.")
            return
        if error:
            self._set_status(u"Isolate needs a model view.", "warn")
            t3_warning(error, title=u"Rebar Check", owner=self)
            return
        self._set_status(u"Isolated %s in the active view — close this window to see it; "
                         u"Reset Temporary Hide/Isolate restores the view."
                         % rc.plural(count, u"element"), "ok")

    def fix_clicked(self, sender, e):
        """Fix the ticked rows: Sync directly, partition through Cast Unit Manager."""
        if self._busy:
            return
        plan = rc.plan_fix([row.issue for row in self._ticked()])
        if not plan.fixable:
            self._set_status(u"Tick only rows marked “Sync into assembly” or "
                             u"“Assign partition” to use Fix.", "warn")
            return

        if plan.sync_assembly_ids and not self._sync(plan):
            return                          # confirmation declined or the group failed
        if plan.assign_ids:
            self._open_partition_page(plan.assign_ids)

    def _sync(self, plan):
        """Confirm, then Sync rebar into the assemblies. False = nothing was changed."""
        n_asm = len(plan.sync_assembly_ids)
        n_bars = len(set(plan.sync_rebar_ids))
        label = rc.plural(n_asm, u"assembly", u"assemblies")
        confirmed = t3_confirm(
            u"Add rebar to %s?" % label,
            title=u"Sync rebar",
            ok_text=u"Sync %s" % label,
            details=(u"Revit adds every loose rebar element hosted by the members of %s — "
                     u"the %s ticked and any others that are missing. One Ctrl+Z undoes "
                     u"the whole sync." % (u"this assembly" if n_asm == 1 else u"these assemblies",
                                           rc.plural(n_bars, u"bar"))),
            owner=self)
        if not confirmed:
            self._set_status(u"Sync cancelled — nothing was changed.", "warn")
            return False
        self._begin_progress(u"Syncing rebar")
        try:
            results = rc.sync_assemblies(self.doc, plan.sync_assembly_ids,
                                         self._scan_data.index, progress=self._progress_cb)
        except Exception as exc:
            self._end_progress()
            self._fail(u"Rebar Check could not sync the assemblies", exc,
                       u"Nothing was changed. Rescan and try again.")
            return False
        self._end_progress()
        self._rebuild_issues()
        self._report_sync(results)
        return True

    def _report_sync(self, results):
        """Status line plus, when something was not synced, the reasons."""
        counts = dict((s, sum(1 for r in results if r.status == s))
                      for s in ("ok", "skipped", "failed"))
        level = "fail" if counts["failed"] else ("warn" if counts["skipped"] else "ok")
        self._set_status(rc.fix_summary(results), level)
        problems = [r for r in results if r.status != "ok"]
        if problems:
            lines = [u"%s: %s" % (r.name, r.detail) for r in problems[:RESULT_LINES_SHOWN]]
            if len(problems) > RESULT_LINES_SHOWN:
                lines.append(u"… and %d more" % (len(problems) - RESULT_LINES_SHOWN))
            t3_warning(u"%s not synced. Fix those by hand, then rescan."
                       % rc.plural(len(problems), u"assembly", u"assemblies"),
                       title=u"Sync rebar", details=u"\n".join(lines), owner=self)

    def _open_partition_page(self, ids):
        """Hand the bars to Cast Unit Manager › Partition by rule, then rescan.

        Cast Unit Manager is built separately: if it is missing (or does not accept
        the hand-off yet) the bars are left selected in Revit and the user is told
        where to go.
        """
        ids = sorted(set(ids))
        show_cast_unit = None
        try:
            from GUI.CastUnitDialog import show_cast_unit
        except ImportError:
            show_cast_unit = None
        if show_cast_unit is not None:
            try:
                import inspect
                accepted = inspect.signature(show_cast_unit).parameters
                if "page" not in accepted or "ids" not in accepted:
                    show_cast_unit = None
            except Exception:
                pass            # cannot inspect: try the call and report what happens
        if show_cast_unit is None:
            uidoc = resolve_uidoc()
            if uidoc is not None:
                select_in_revit(uidoc, ids)
            self._set_status(u"Cast Unit Manager is not available — %s selected in Revit."
                             % rc.plural(len(ids), u"bar"), "warn")
            t3_info(PARTITION_FALLBACK % len(ids), title=u"Assign partition", owner=self)
            return
        try:
            show_cast_unit(self.doc, page="partition", ids=ids)
        except Exception as exc:
            self._fail(u"Cast Unit Manager could not open", exc,
                       u"Open it from the ribbon and use Partition by rule.")
            return
        self._scan_model()


def show_rebar_check(doc):
    """Entry point used by the pushbutton."""
    RebarCheckDialog(doc).ShowDialog()
