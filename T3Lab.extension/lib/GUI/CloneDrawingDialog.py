# -*- coding: utf-8 -*-
"""
CloneDrawingDialog.py
=====================
WPF dialog for Clone Drawing (Tekla: clone drawing). Three pages on a chip
tab strip:

* **Source & targets** - pick the assembly whose drawing is finished, then
  tick the target assemblies; each target carries a similarity score and the
  reasons behind it.
* **Options**          - which layers run (T1 views and sheet, T2 free
  annotations, T3 tags and dimensions), what happens to targets that already
  have views, matching tolerance and naming patterns.
* **Results**          - one row per target, the full log (unmatched elements
  included, with their reason) and a tally strip.

Modal (spec D8). "Open sheet" stores the sheet id and closes the window; the
pushbutton script activates the sheet once the dialog has returned (R12).
Revit API work lives in ``Snippets/_drawing_clone.py``; this module is UI only.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"

import os
import time

import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from System.Windows import Clipboard, RoutedEventHandler, Visibility
from System.Windows.Controls import CheckBox, Orientation, StackPanel, TextBlock

from GUI.WPF_Base import T3WPFWindow
from GUI.T3Dialog import confirm, show_error

from Snippets import _drawing_clone as dc
from Snippets._assembly import (STATUS_FAILED, STATUS_OK, STATUS_SKIPPED,
                                collect_assemblies, collect_assembly_views)
from Snippets._compat import make_eid, short_error

GUI_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'CloneDrawing.xaml')
TITLE = "Clone Drawing"

TAB_SELECT = 0
TAB_OPTIONS = 1
TAB_RESULTS = 2

TARGETS_SIMILAR = "similar"
TARGETS_CATEGORY = "category"
TARGETS_ALL = "all"

NONE_TEXT = u"— none —"
PROFILE_PROGRESS_FROM = 30          # show the progress bar when scoring this many assemblies

LOG_STYLES = {dc.LEVEL_OK: "T3.Log.Ok", dc.LEVEL_SKIPPED: "T3.Log.Skipped",
              dc.LEVEL_FAILED: "T3.Log.Failed", dc.LEVEL_PLAIN: "T3.Log.Plain"}


def _plural(n, word, many=None):
    return u"%d %s" % (n, word if n == 1 else (many or word + u"s"))


# ── ROWS ─────────────────────────────────────────────────────────────────────

class TargetRow(object):
    """One candidate target assembly. Every bound property is text."""

    def __init__(self, record, same_type):
        self.record = record
        self.assembly_id = record.id
        self.same_type = same_type
        self.score = 0
        self.reasons = []
        self.clonable = True
        self.is_selected = False
        self.StatusText = u"Ready"
        self.Severity = "Success"

    @property
    def has_drawing(self):
        return bool(self.record.view_ids or self.record.sheet_ids)

    @property
    def Mark(self):
        return self.record.mark or u"assembly %d" % self.record.id

    @property
    def Similarity(self):
        return u"%d %%" % self.score

    @property
    def Why(self):
        return u"; ".join(self.reasons) if self.reasons else NONE_TEXT

    @property
    def Views(self):
        return u"%d" % len(self.record.view_ids)

    @property
    def Sheets(self):
        return u"%d" % len(self.record.sheet_ids)


class ResultRow(object):
    """One target after a run (a TargetOutcome as text)."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.sheet_id = outcome.sheet_id
        tally = outcome.tally
        self.Assembly = outcome.mark
        self.Views = u"%d / %d" % (outcome.views_created, outcome.views_planned) \
            if outcome.views_planned else u"%d" % outcome.views_created
        self.Sheet = outcome.sheet_number or NONE_TEXT
        self.Copied = u"%d" % outcome.copied
        self.Tags = u"%d / %d" % (tally.tags_ok, tally.tags_total)
        self.Dims = u"%d / %d" % (tally.dims_ok, tally.dims_total)
        self.Unmatched = u"%d" % outcome.unmatched_count
        if outcome.status == STATUS_FAILED:
            self.StatusText, self.Severity = u"Failed", "Danger"
        elif outcome.status == STATUS_SKIPPED:
            self.StatusText, self.Severity = u"Skipped: %s" % outcome.detail, "Warning"
        elif outcome.failures or outcome.unmatched_count:
            self.StatusText, self.Severity = u"Cloned, check log", "Warning"
        else:
            self.StatusText, self.Severity = u"Cloned", "Success"


# ── WINDOW ───────────────────────────────────────────────────────────────────

class CloneDrawingDialog(T3WPFWindow):
    """Main window class for Clone Drawing."""

    def __init__(self, doc):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc
        self.open_sheet_id = None
        self._loading = True
        self._busy = False
        self._records = []
        self._records_by_id = {}
        self._views_index = {}
        self._profiles = {}
        self._sources = []            # AssemblyRecords that own at least one view
        self._source = None
        self._src_specs = []
        self._rows = []
        self._results = []
        self._log_lines = []

        self.tb_sheet_pattern.Text = dc.DEFAULT_SHEET_PATTERN
        self.tb_view_pattern.Text = dc.DEFAULT_VIEW_PATTERN
        self._wire_row_events()
        try:
            self.Closing += self._on_closing
        except Exception:
            pass
        self._load_sources()
        self._loading = False
        self._refresh_state()

    # ── SETUP ────────────────────────────────────────────────────────────────

    def _wire_row_events(self):
        """Row checkbox clicks bubble to the grid; a handler inside the
        DataTemplate would never be wired (template namescope)."""
        try:
            self.grid_targets.AddHandler(
                CheckBox.ClickEvent, RoutedEventHandler(self.grid_targets_checkbox_clicked), True)
        except Exception:
            pass

    def _brush(self, key):
        try:
            return self.FindResource(key)
        except Exception:
            return None

    def _style(self, key):
        try:
            return self.FindResource(key)
        except Exception:
            return None

    # ── LOADING ──────────────────────────────────────────────────────────────

    def _load_sources(self):
        """Read every assembly once; sources = assemblies that own a view or sheet."""
        try:
            self._records = collect_assemblies(self.doc)
            self._views_index = collect_assembly_views(self.doc)
        except Exception as exc:
            self._records = []
            self._set_status(u"Could not read the assemblies of this model: %s. "
                             u"Close the window and try again." % short_error(exc), "Danger")
        self._records_by_id = dict((r.id, r) for r in self._records)
        self._sources = sorted([r for r in self._records if r.view_ids or r.sheet_ids],
                               key=lambda r: (r.mark or u"").lower())
        labels = [u"%s — %s, %s" % (r.mark or u"assembly %d" % r.id,
                                         _plural(len(r.view_ids), u"view"),
                                         _plural(len(r.sheet_ids), u"sheet"))
                  for r in self._sources]
        self.set_items_source(self.cb_source, labels)
        if self._sources:
            self.cb_source.SelectedIndex = 0
            self._select_source(0)
        else:
            self.txt_source_summary.Text = (
                u"No assembly has views yet. Select one assembly, run Modify › Assembly "
                u"› Create Views, finish its drawing, then open Clone Drawing again.")
            self._load_targets()

    def _profile(self, record):
        if record.id not in self._profiles:
            try:
                element = self.doc.GetElement(make_eid(record.id))
                self._profiles[record.id] = dc.assembly_profile(self.doc, element)
            except Exception:
                self._profiles[record.id] = {"category": record.naming_category,
                                             "bbox_mm": (0.0, 0.0, 0.0),
                                             "rebar_count": len(record.rebar_ids),
                                             "member_count": len(record.members)}
        return self._profiles[record.id]

    def _select_source(self, index):
        if index is None or index < 0 or index >= len(self._sources):
            self._source = None
            self._src_specs = []
            self._load_targets()
            return
        self._source = self._sources[index]
        try:
            element = self.doc.GetElement(make_eid(self._source.id))
            self._src_specs = dc.read_source(self.doc, element, self._views_index)
        except Exception as exc:
            self._src_specs = []
            self._set_status(u"Could not read the views of %s: %s. Pick another source."
                             % (self._source.mark, short_error(exc)), "Danger")
        summary = dc.summarize_specs([s for s in self._src_specs if not s.skip_reason])
        blocked = [s for s in self._src_specs if s.skip_reason]
        if blocked:
            summary += u"\n%s cannot be cloned: %s" % (
                _plural(len(blocked), u"view"),
                u"; ".join(u"%s (%s)" % (s.name or s.kind, s.skip_reason) for s in blocked[:3]))
            if len(blocked) > 3:
                summary += u"; ..."
        self.txt_source_summary.Text = summary
        self._load_targets()

    def _load_targets(self):
        """One row per other assembly, scored against the source."""
        rows = []
        source = self._source
        for record in self._records:
            if source is not None and record.id == source.id:
                continue
            same_type = source is not None and record.type_id == source.type_id
            rows.append(TargetRow(record, same_type))
        self._rows = rows
        self._score_targets()

    def _score_targets(self):
        source = self._source
        if source is None:
            for row in self._rows:
                row.score, row.reasons = 0, [u"no source picked"]
            self._update_statuses(reset_ticks=True)
            return
        many = len(self._rows) >= PROFILE_PROGRESS_FROM and \
            any(r.id not in self._profiles for r in self._records)
        if many:
            self.begin_progress(len(self._rows))
        try:
            src_profile = self._profile(source)
            for index, row in enumerate(self._rows):
                if many and index % 10 == 0:
                    self.step_progress(index, u"Comparing assemblies — %d / %d"
                                       % (index, len(self._rows)))
                row.score, row.reasons = dc.similarity(src_profile, self._profile(row.record))
        finally:
            if many:
                self.end_progress()
        self._rows.sort(key=lambda r: (-r.score, r.Mark.lower()))
        self._update_statuses(reset_ticks=True)

    # ── STATE ────────────────────────────────────────────────────────────────

    def _options(self):
        """CloneOptions from the Options page, or (None, error text)."""
        try:
            tol = float((self.tb_tolerance.Text or u"").strip().replace(u",", u"."))
        except ValueError:
            tol = -1.0
        if tol <= 0:
            return None, (u"Position tolerance must be a number above 0 mm. "
                          u"Fix it on the Options page.")
        options = dc.CloneOptions(
            t1=bool(self.chk_t1.IsChecked), t2=bool(self.chk_t2.IsChecked),
            t3=bool(self.chk_t3.IsChecked), add_missing=bool(self.rb_add_missing.IsChecked),
            tol_mm=tol, allow_mirror=bool(self.chk_mirror.IsChecked),
            sheet_pattern=(self.tb_sheet_pattern.Text or u"").strip() or dc.DEFAULT_SHEET_PATTERN,
            view_pattern=(self.tb_view_pattern.Text or u"").strip() or dc.DEFAULT_VIEW_PATTERN)
        if not (options.t1 or options.t2 or options.t3):
            return None, u"No layer is ticked. Tick T1, T2 or T3 on the Options page."
        return options, None

    def _update_statuses(self, reset_ticks=False):
        add_missing = bool(self.rb_add_missing.IsChecked)
        for row in self._rows:
            text, severity, clonable = dc.target_status(row.same_type, row.has_drawing, add_missing)
            row.StatusText, row.Severity, row.clonable = text, severity, clonable
            if reset_ticks:
                row.is_selected = clonable and row.score >= dc.SIMILAR_MIN_SCORE
            elif not clonable:
                row.is_selected = False
        self._apply_target_filter()

    def _targets_mode(self):
        if self.chip_tgt_all.IsChecked:
            return TARGETS_ALL
        if self.chip_tgt_sametype.IsChecked:
            return TARGETS_CATEGORY
        return TARGETS_SIMILAR

    def _apply_target_filter(self):
        mode = self._targets_mode()
        text = (self.tb_target_search.Text or u"").strip().lower()
        src_category = self._source.naming_category if self._source is not None else None
        visible = []
        for row in self._rows:
            if mode == TARGETS_SIMILAR and row.score < dc.SIMILAR_MIN_SCORE:
                continue
            if mode == TARGETS_CATEGORY and row.record.naming_category != src_category:
                continue
            if text and text not in row.Mark.lower():
                continue
            visible.append(row)
        self.set_items_source(self.grid_targets, visible)
        self.grid_targets_empty.Visibility = Visibility.Collapsed if visible else Visibility.Visible
        if not visible:
            if not self._rows:
                self.grid_targets_empty.Text = (
                    u"No other assemblies in the model.\nCreate the target assemblies first "
                    u"(Cast Unit Manager or Create Assembly).")
            else:
                self.grid_targets_empty.Text = (
                    u"No assembly matches this filter.\nPick All, or clear the search box.")
        self.sync_header_checkbox(self.chk_all_grid_targets, self.grid_targets, "is_selected")
        self._refresh_state()

    def _chosen_rows(self):
        return [r for r in self._rows if r.is_selected and r.clonable]

    def _set_status(self, text, severity=None):
        self.status_text.Text = text
        if severity:
            brush = self._brush("T3.%s.Accent" % severity)
            if brush is not None:
                self.dot_status.Fill = brush

    def _refresh_state(self):
        """Primary label, its enabled state and the status line."""
        if getattr(self, '_loading', True) or self._busy:
            return
        chosen = self._chosen_rows()
        options, error = self._options()
        n = len(chosen)
        self.txt_clone_label.Text = u"Clone to %s" % _plural(n, u"assembly", u"assemblies") \
            if n else u"Clone"
        ok = self._source is not None and n > 0 and options is not None
        self.btn_clone.IsEnabled = ok
        if self._source is None:
            self._set_status(u"No source yet — an assembly needs views before it can be cloned.",
                             "Warning")
        elif error:
            self._set_status(error, "Warning")
        elif not n:
            self._set_status(u"Ready — tick the target assemblies to clone %s to."
                             % self._source.mark, "Success")
        else:
            self._set_status(u"Ready — %s from %s to %s"
                             % (_plural(len([s for s in self._src_specs if not s.skip_reason]),
                                        u"view"),
                                self._source.mark, _plural(n, u"assembly", u"assemblies")),
                             "Success")

    # ── RUN ──────────────────────────────────────────────────────────────────

    def _confirm_text(self, options, chosen):
        clonable = [s for s in self._src_specs if not s.skip_reason]
        n_views = len([s for s in clonable if s.kind != "sheet"]) * len(chosen)
        n_sheets = len([s for s in clonable if s.kind == "sheet"]) * len(chosen)
        targets = _plural(len(chosen), u"assembly", u"assemblies")
        head = u"Clone the drawing of %s to %s?" % (self._source.mark, targets)
        if options.t1:
            body = u"This creates %s%s and %s." % (
                u"up to " if options.add_missing else u"",
                _plural(n_views, u"view"), _plural(n_sheets, u"sheet"))
        else:
            body = u"Views are not created (T1 is off); annotations go onto the views the targets already have."
        layers = [name for flag, name in ((options.t1, u"T1 views and sheet"),
                                          (options.t2, u"T2 free annotations"),
                                          (options.t3, u"T3 tags and dimensions")) if flag]
        details = (u"Layers: %s.\nNothing is deleted. The whole run is one undo step (Ctrl+Z)."
                   % u", ".join(layers))
        return head + u" " + body, details, u"Clone to %s" % targets

    def _run_clone(self, options, chosen):
        target_ids = [r.assembly_id for r in chosen]
        self._busy = True
        self.begin_progress(len(target_ids), disable=[
            self.btn_clone, self.btn_open_sheet, self.btn_log_copy, self.cb_source,
            self.grid_targets, self.chip_tab_options])
        total = len(target_ids)

        def step(index, count, label):
            return self.step_progress(index - 1, u"Cloning drawing — %d / %d · %s"
                                      % (index, count, label))

        try:
            outcomes = dc.run_clone(self.doc, self._source.id, target_ids, options, progress=step)
        except Exception as exc:
            self.end_progress()
            self._busy = False
            self._set_status(u"Clone Drawing failed and nothing was changed.", "Danger")
            show_error(u"Clone Drawing failed and nothing was changed in the model. "
                       u"Source: %s. Check the source views, then try again." % self._source.mark,
                       title=TITLE, details=short_error(exc))
            self._refresh_state()
            return
        self.end_progress()
        self._busy = False
        self._report(outcomes, total)

    def _report(self, outcomes, total):
        self._results = [ResultRow(o) for o in outcomes]
        self.set_items_source(self.grid_results, self._results)
        self.grid_results_empty.Visibility = Visibility.Collapsed if self._results \
            else Visibility.Visible
        self._log(dc.LEVEL_PLAIN, u"Clone %s to %s" % (
            self._source.mark, _plural(total, u"assembly", u"assemblies")))
        for outcome in outcomes:
            for level, text in outcome.log:
                self._log(level, text)

        totals = dc.summarize_outcomes(outcomes)
        tags_ok = sum(o.tally.tags_ok for o in outcomes)
        tags_all = sum(o.tally.tags_total for o in outcomes)
        dims_ok = sum(o.tally.dims_ok for o in outcomes)
        dims_all = sum(o.tally.dims_total for o in outcomes)
        self.txt_tally.Text = (u"%d ok · %d skipped · %d failed · %s · %s "
                               u"· tags %d / %d · dims %d / %d · %d unmatched"
                               % (totals[STATUS_OK], totals[STATUS_SKIPPED], totals[STATUS_FAILED],
                                  _plural(totals["views"], u"view"),
                                  _plural(totals["sheets"], u"sheet"),
                                  tags_ok, tags_all, dims_ok, dims_all, totals["unmatched"]))

        skipped_why = sorted(set(o.detail for o in outcomes if o.status == STATUS_SKIPPED))
        text = u"Cloned to %s · %d skipped%s · %d failed · %d unmatched" % (
            _plural(totals[STATUS_OK], u"assembly", u"assemblies"), totals[STATUS_SKIPPED],
            u" (%s)" % u", ".join(skipped_why[:2]) if skipped_why else u"",
            totals[STATUS_FAILED], totals["unmatched"])
        if not totals[STATUS_OK] and totals[STATUS_SKIPPED] and not totals[STATUS_FAILED]:
            text = u"Nothing created — %d skipped (%s)." % (
                totals[STATUS_SKIPPED], u", ".join(skipped_why[:2]))
        severity = "Danger" if totals[STATUS_FAILED] else (
            "Warning" if totals[STATUS_SKIPPED] or totals["unmatched"] else "Success")

        self._refresh_views_after_run()
        self.chip_tab_results.IsChecked = True
        self._refresh_state()
        self._set_status(text, severity)

    def _refresh_views_after_run(self):
        """Views / Sheets columns and 'has drawing' follow the model after a run."""
        try:
            self._views_index = collect_assembly_views(self.doc)
        except Exception:
            return
        for record in self._records:
            views, sheets = self._views_index.get(record.id, ([], []))
            record.view_ids, record.sheet_ids = list(views), list(sheets)
        self._update_statuses(reset_ticks=False)

    def _log(self, level, text):
        stamp = time.strftime("%H:%M:%S")
        self._log_lines.append(u"%s %s" % (stamp, text))
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        time_block = TextBlock()
        time_block.Text = stamp
        message = TextBlock()
        message.Text = text
        for block, key in ((time_block, "T3.Log.Time"),
                           (message, LOG_STYLES.get(level, "T3.Log.Plain"))):
            style = self._style(key)
            if style is not None:
                block.Style = style
        panel.Children.Add(time_block)
        panel.Children.Add(message)
        self.lst_log.Items.Add(panel)
        self.lst_log_empty.Visibility = Visibility.Collapsed

    # ── HANDLERS ─────────────────────────────────────────────────────────────

    def _on_closing(self, sender, e):
        """Closing mid-run would leave the transaction group open: stop instead."""
        if self._busy:
            e.Cancel = True
            self.stop_clicked()

    def close_button_clicked(self, sender=None, e=None):
        if self._busy:
            self.stop_clicked()
            return
        self.Close()

    def tab_chip_checked(self, sender, e):
        try:
            self.tab_control.SelectedIndex = int(sender.Tag)
        except Exception:
            pass

    def source_changed(self, sender, e):
        if getattr(self, '_loading', True) or self._busy:
            return
        self._select_source(self.cb_source.SelectedIndex)

    def targets_chip_checked(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._apply_target_filter()

    def target_search_changed(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._apply_target_filter()

    def grid_targets_checkbox_clicked(self, sender, e):
        # T3WPFWindow has already written the tick back to the row (string bridge).
        self.sync_header_checkbox(self.chk_all_grid_targets, self.grid_targets, "is_selected")
        self._refresh_state()

    def select_all_grid_targets_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_targets, "is_selected", sender.IsChecked)
        self._refresh_state()

    def options_changed(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._update_statuses(reset_ticks=False)

    def results_selected(self, sender, e):
        row = self.grid_results.SelectedItem
        self.btn_open_sheet.IsEnabled = isinstance(row, ResultRow) and row.sheet_id > 0

    def open_sheet_clicked(self, sender, e):
        row = self.grid_results.SelectedItem
        if not isinstance(row, ResultRow) or row.sheet_id <= 0:
            self._set_status(u"Select a result row that has a sheet, then press Open sheet.",
                             "Warning")
            return
        # Revit cannot switch views under a modal window: the script opens it
        # once this dialog has returned (spec R12).
        self.open_sheet_id = row.sheet_id
        self.Close()

    def log_copy_clicked(self, sender, e):
        if not self._log_lines:
            self._set_status(u"The log is empty — run Clone first.", "Warning")
            return
        try:
            Clipboard.SetText(u"\r\n".join(self._log_lines))
            self._set_status(u"Copied %s to the clipboard."
                             % _plural(len(self._log_lines), u"log line"), "Success")
        except Exception as exc:
            self._set_status(u"Could not copy the log: %s. Try again." % short_error(exc), "Danger")

    def clone_clicked(self, sender, e):
        if self._busy:
            return
        options, error = self._options()
        chosen = self._chosen_rows()
        if self._source is None:
            self._set_status(u"Pick a source assembly that has views first.", "Warning")
            return
        if error:
            self._set_status(error, "Warning")
            self.chip_tab_options.IsChecked = True
            return
        if not chosen:
            self._set_status(u"Tick at least one target assembly on the Source & targets page.",
                             "Warning")
            return
        if not [s for s in self._src_specs if not s.skip_reason]:
            self._set_status(u"No view of %s can be cloned — see the source summary."
                             % self._source.mark, "Warning")
            return
        message, details, ok_text = self._confirm_text(options, chosen)
        if not confirm(message, title=TITLE, ok_text=ok_text, details=details):
            return
        self._run_clone(options, chosen)


def show_clone_drawing(doc):
    """Entry point used by the pushbutton. Returns the sheet id to open, or None."""
    dialog = CloneDrawingDialog(doc)
    dialog.ShowDialog()
    return dialog.open_sheet_id
