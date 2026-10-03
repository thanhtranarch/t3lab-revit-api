# -*- coding: utf-8 -*-
"""
CloneDrawingDialog.py
=====================
WPF dialog for Clone Drawing V2 (Tekla: clone drawing). Three pages on a chip
tab strip - design: dev/plan/clone-drawing-v2-design.md section 7.

* **Source & targets** - pick the assembly whose drawing is finished (Tekla:
  cloning template), tick which of its views clone their dimensions (Tekla:
  dimension creation method in this view), then tick the target assemblies -
  from the grid, from the current Revit selection or picked in the model.
* **Clone settings**   - one row per Tekla object type (Clone / Clone + create
  / Skip), presets saved per user, the existing-drawings policy, matching
  tolerance and naming patterns.
* **Results**          - one row per target, the filterable log (unmatched
  elements included, with their reason) and a tally strip.

Modal (spec D8). "Open sheet" stores the sheet id and closes the window; the
pushbutton script activates the sheet once the dialog has returned (R12).
"Pick in model" hides the window, picks, shows it again. Revit API work lives
in ``Snippets/_drawing_clone.py``; this module is UI only.

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

LOG_ALL = "all"
LOG_UNMATCHED = "unmatched"
LOG_FAILED = "failed"

NONE_TEXT = u"— none —"
PROFILE_PROGRESS_FROM = 30          # show the progress bar when scoring this many assemblies

LOG_STYLES = {dc.LEVEL_OK: "T3.Log.Ok", dc.LEVEL_SKIPPED: "T3.Log.Skipped",
              dc.LEVEL_FAILED: "T3.Log.Failed", dc.LEVEL_PLAIN: "T3.Log.Plain"}

# settings field -> combo box x:Name (one per SETTINGS_ROWS entry)
SETTING_COMBOS = dict((field, "cb_set_%s" % field) for field in dc.SETTINGS_FIELDS)
EXISTING_RADIOS = (("rb_existing_skip", dc.EXISTING_SKIP), ("rb_existing_add", dc.EXISTING_ADD),
                   ("rb_existing_new", dc.EXISTING_NEW),
                   ("rb_existing_replace", dc.EXISTING_REPLACE))


def _plural(n, word, many=None):
    return u"%d %s" % (n, word if n == 1 else (many or word + u"s"))


# ── ROWS ─────────────────────────────────────────────────────────────────────

class SourceViewRow(object):
    """One view of the source assembly; ``clone_dims`` is Tekla's per-view method."""

    def __init__(self, spec):
        self.spec = spec
        self.clone_dims = bool(spec.clone_dims) and spec.kind in dc.MODEL_VIEW_KINDS \
            and not spec.skip_reason
        self.Name = spec.name or spec.sheet_number or spec.kind
        self.Kind = dc.kind_label(spec) if not spec.skip_reason else u"Not cloned"


class TargetRow(object):
    """One candidate target assembly. Every bound property is text."""

    def __init__(self, record, same_type, same_category):
        self.record = record
        self.assembly_id = record.id
        self.same_type = same_type
        self.same_category = same_category
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
    def Members(self):
        return u"%d" % len(self.record.members)

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
        if tally.tags_created:
            self.Tags += u" +%d" % tally.tags_created
        self.Dims = u"%d / %d" % (tally.dims_ok, tally.dims_total)
        self.Spots = u"%d / %d" % (tally.spots_ok, tally.spots_total)
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

    def __init__(self, doc, uidoc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc
        self.uidoc = uidoc
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
        self._src_rows = []
        self._rows = []
        self._results = []
        self._log_entries = []        # [(level, stamp, text)]
        self._presets = {}            # user presets {name: CloneSettings}
        self._preset_names = []       # combo order (builtins first)
        self._preset_path = dc.preset_path()

        self._wire_row_events()
        try:
            self.Closing += self._on_closing
        except Exception:
            pass
        self._load_presets()
        self._load_sources()
        self._loading = False
        self._apply_log_filter()
        self._refresh_state()

    # ── SETUP ────────────────────────────────────────────────────────────────

    def _wire_row_events(self):
        """Row checkbox clicks bubble to the grid; a handler inside the
        DataTemplate would never be wired (template namescope)."""
        for grid, handler in ((self.grid_targets, self.grid_targets_checkbox_clicked),
                              (self.grid_source_views, self.grid_source_views_checkbox_clicked)):
            try:
                grid.AddHandler(CheckBox.ClickEvent, RoutedEventHandler(handler), True)
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

    # ── PRESETS ──────────────────────────────────────────────────────────────

    def _load_presets(self):
        presets, last, warnings = dc.load_presets(self._preset_path)
        self._presets = presets
        self._fill_preset_combo(last)
        for warning in warnings:
            self._set_status(warning, "Warning")
        self._apply_settings(self._preset_settings(last))

    def _fill_preset_combo(self, select_name):
        self._preset_names = list(dc.BUILTIN_PRESET_NAMES) + sorted(self._presets.keys())
        self._loading_presets = True
        try:
            self.set_items_source(self.cb_preset, list(self._preset_names))
            index = self._preset_names.index(select_name) if select_name in self._preset_names else 0
            self.cb_preset.SelectedIndex = index
        finally:
            self._loading_presets = False

    def _preset_settings(self, name):
        builtins = dc.builtin_presets()
        if name in builtins:
            return builtins[name]
        return self._presets.get(name) or builtins[dc.PRESET_TEKLA]

    def _current_preset_name(self):
        index = self.cb_preset.SelectedIndex
        if 0 <= index < len(self._preset_names):
            return self._preset_names[index]
        return dc.PRESET_TEKLA

    def _combo_value(self, combo, default):
        try:
            item = combo.SelectedItem
            tag = getattr(item, "Tag", None)
            return u"%s" % tag if tag else default
        except Exception:
            return default

    def _set_combo(self, combo, value):
        try:
            for index in range(combo.Items.Count):
                if u"%s" % getattr(combo.Items[index], "Tag", u"") == value:
                    combo.SelectedIndex = index
                    return
            combo.SelectedIndex = 0
        except Exception:
            pass

    def _apply_settings(self, settings):
        """Push a CloneSettings onto the page controls."""
        self._loading_settings = True
        try:
            for field, name in SETTING_COMBOS.items():
                self._set_combo(getattr(self, name), getattr(settings, field))
            for name, value in EXISTING_RADIOS:
                getattr(self, name).IsChecked = settings.existing == value
            self.tb_tolerance.Text = (u"%g" % settings.tol_mm)
            self.chk_mirror.IsChecked = bool(settings.allow_mirror)
            self.tb_sheet_pattern.Text = settings.sheet_pattern
            self.tb_view_pattern.Text = settings.view_pattern
        finally:
            self._loading_settings = False

    def _settings(self):
        """CloneSettings from the page, or (None, error text)."""
        data = {}
        for field, name in SETTING_COMBOS.items():
            data[field] = self._combo_value(getattr(self, name), dc.CloneSettings().to_dict()[field])
        data["existing"] = dc.EXISTING_SKIP
        for name, value in EXISTING_RADIOS:
            if getattr(self, name).IsChecked:
                data["existing"] = value
        try:
            data["tol_mm"] = float((self.tb_tolerance.Text or u"").strip().replace(u",", u"."))
        except ValueError:
            data["tol_mm"] = -1.0
        data["allow_mirror"] = bool(self.chk_mirror.IsChecked)
        data["sheet_pattern"] = (self.tb_sheet_pattern.Text or u"").strip()
        data["view_pattern"] = (self.tb_view_pattern.Text or u"").strip()
        settings, invalid = dc.normalize_settings(data)
        if "tol_mm" in invalid:
            return None, (u"Position tolerance must be a number above 0 mm. "
                          u"Fix it on the Clone settings page.")
        if not settings.any_layer:
            return None, u"Every row is set to Skip. Set at least one object type to Clone."
        return settings, None

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
        labels = [u"%s — %s, %s, %s" % (r.mark or u"assembly %d" % r.id,
                                             _plural(len(r.members), u"member"),
                                             _plural(len(r.view_ids), u"view"),
                                             _plural(len(r.sheet_ids), u"sheet"))
                  for r in self._sources]
        self.set_items_source(self.cb_source, labels)
        if self._sources:
            self.cb_source.SelectedIndex = 0
            self._select_source(0)
        else:
            self.txt_source_summary.Text = (
                u"No assembly has views yet. Select one assembly, run Modify > Assembly "
                u"> Create Views, finish its drawing, then open Clone Drawing again.")
            self._show_source_views()
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
            self._show_source_views()
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
        self._show_source_views()
        self._load_targets()

    def _show_source_views(self):
        self._src_rows = [SourceViewRow(s) for s in self._src_specs]
        self.set_items_source(self.grid_source_views, self._src_rows)
        self.grid_source_views_empty.Visibility = Visibility.Collapsed if self._src_rows \
            else Visibility.Visible
        self.sync_header_checkbox(self.chk_all_grid_source_views, self.grid_source_views, "clone_dims")
        self._update_source_views_count()

    def _update_source_views_count(self):
        dims_on = len([r for r in self._src_rows if r.clone_dims])
        clonable = len([r for r in self._src_rows if not r.spec.skip_reason])
        self.txt_source_views_count.Text = u"%s · %d clone dimensions" % (
            _plural(clonable, u"view"), dims_on)

    def _load_targets(self):
        """One row per other assembly, scored against the source."""
        rows = []
        source = self._source
        for record in self._records:
            if source is not None and record.id == source.id:
                continue
            same_type = source is not None and record.type_id == source.type_id
            same_category = source is None or record.naming_category == source.naming_category
            rows.append(TargetRow(record, same_type, same_category))
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

    def _policy(self):
        for name, value in EXISTING_RADIOS:
            try:
                if getattr(self, name).IsChecked:
                    return value
            except Exception:
                continue
        return dc.EXISTING_SKIP

    def _update_statuses(self, reset_ticks=False):
        policy = self._policy()
        for row in self._rows:
            text, severity, clonable = dc.target_status(row.same_type, row.same_category,
                                                        row.has_drawing, policy)
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
        visible = []
        for row in self._rows:
            if mode == TARGETS_SIMILAR and (row.score < dc.SIMILAR_MIN_SCORE or not row.same_category):
                continue
            if mode == TARGETS_CATEGORY and not row.same_category:
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
        self.txt_targets_count.Text = u"%s shown · %d ticked" % (
            _plural(len(visible), u"assembly", u"assemblies"), len(self._chosen_rows()))
        self._refresh_state()

    def _chosen_rows(self):
        return [r for r in self._rows if r.is_selected and r.clonable]

    def _tick_ids(self, ids, origin):
        """Tick the target rows for `ids`; report what could not be ticked."""
        by_id = dict((r.assembly_id, r) for r in self._rows)
        ticked = 0
        blocked = []
        missing = 0
        for value in ids:
            row = by_id.get(value)
            if row is None:
                missing += 1
            elif row.clonable:
                row.is_selected = True
                ticked += 1
            else:
                blocked.append(u"%s (%s)" % (row.Mark, row.StatusText.lower()))
        if not ids:
            self._set_status(u"No assembly in the %s. Select assemblies or their members in Revit, "
                             u"then try again." % origin, "Warning")
        else:
            text = u"Ticked %s from the %s." % (_plural(ticked, u"assembly", u"assemblies"), origin)
            if blocked:
                text += u" Not ticked: %s." % u", ".join(blocked[:3])
            if missing:
                text += u" %d selected elements are the source or not an assembly." % missing
            self._set_status(text, "Warning" if blocked or missing else "Success")
        if ticked and self._targets_mode() != TARGETS_ALL:
            self.chip_tgt_all.IsChecked = True       # show every ticked row, whatever its score
        self._apply_target_filter()

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
        settings, error = self._settings()
        n = len(chosen)
        self.txt_clone_label.Text = u"Clone to %s" % _plural(n, u"assembly", u"assemblies") \
            if n else u"Clone"
        ok = self._source is not None and n > 0 and settings is not None
        self.btn_clone.IsEnabled = ok
        try:
            self.btn_preset_delete.IsEnabled = self._current_preset_name() not in dc.BUILTIN_PRESET_NAMES
        except Exception:
            pass
        if self._source is None:
            self._set_status(u"No source yet — an assembly needs views before it can be cloned.",
                             "Warning")
        elif error:
            self._set_status(error, "Warning")
        elif not n:
            self._set_status(u"Ready — tick the target assemblies to clone %s to."
                             % self._source.mark, "Success")
        else:
            self._set_status(u"Ready — %s from %s to %s · preset %s"
                             % (_plural(len([s for s in self._src_specs if not s.skip_reason]),
                                        u"view"),
                                self._source.mark, _plural(n, u"assembly", u"assemblies"),
                                self._current_preset_name()),
                             "Success")

    # ── RUN ──────────────────────────────────────────────────────────────────

    def _replace_plan(self, chosen):
        """{target_id: replaceable ids} for the delete confirmation (replace policy)."""
        plan = {}
        for row in chosen:
            if not row.has_drawing:
                continue
            try:
                element = self.doc.GetElement(make_eid(row.assembly_id))
                plan[row.assembly_id] = dc.replaceable_ids(self.doc, element, self._views_index)
            except Exception:
                plan[row.assembly_id] = []
        return plan

    def _run_clone(self, settings, chosen, replace_plan):
        target_ids = [r.assembly_id for r in chosen]
        for row in self._src_rows:
            row.spec.clone_dims = bool(row.clone_dims)
        self._busy = True
        self.begin_progress(len(target_ids), disable=[
            self.btn_clone, self.btn_open_sheet, self.btn_log_copy, self.cb_source,
            self.grid_targets, self.grid_source_views, self.chip_tab_options,
            self.btn_from_selection, self.btn_pick])
        total = len(target_ids)

        def step(index, count, label):
            return self.step_progress(index - 1, u"Cloning drawing — %d / %d · %s"
                                      % (index, count, label))

        try:
            outcomes = dc.run_clone(self.doc, self._source.id, target_ids, settings,
                                    progress=step, replace_plan=replace_plan)
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
        self._apply_log_filter()
        self.txt_tally.Text = dc.tally_text(outcomes)

        totals = dc.summarize_outcomes(outcomes)
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

    # ── LOG ──────────────────────────────────────────────────────────────────

    def _log(self, level, text):
        self._log_entries.append((level, time.strftime("%H:%M:%S"), text))

    def _log_mode(self):
        try:
            if self.chip_log_unmatched.IsChecked:
                return LOG_UNMATCHED
            if self.chip_log_failed.IsChecked:
                return LOG_FAILED
        except Exception:
            pass
        return LOG_ALL

    def _apply_log_filter(self):
        mode = self._log_mode()
        self.lst_log.Items.Clear()
        shown = 0
        for level, stamp, text in self._log_entries:
            if mode == LOG_UNMATCHED and not text.startswith(u"unmatched "):
                continue
            if mode == LOG_FAILED and level != dc.LEVEL_FAILED:
                continue
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
            shown += 1
        if not self._log_entries:
            self.lst_log_empty.Text = (u"The log is empty.\nEach cloned view, copied annotation "
                                       u"and unmatched element is listed here.")
        elif not shown:
            self.lst_log_empty.Text = u"No %s lines in this run.\nPick All to see everything." % mode
        self.lst_log_empty.Visibility = Visibility.Collapsed if shown else Visibility.Visible

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
        self.txt_targets_count.Text = u"%s shown · %d ticked" % (
            _plural(self.grid_targets.Items.Count, u"assembly", u"assemblies"),
            len(self._chosen_rows()))
        self._refresh_state()

    def select_all_grid_targets_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_targets, "is_selected", sender.IsChecked)
        for row in self._rows:
            if not row.clonable:
                row.is_selected = False
        self.grid_targets.Items.Refresh()
        self.grid_targets_checkbox_clicked(sender, e)

    def grid_source_views_checkbox_clicked(self, sender, e):
        self.sync_header_checkbox(self.chk_all_grid_source_views, self.grid_source_views, "clone_dims")
        self._update_source_views_count()

    def select_all_grid_source_views_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_source_views, "clone_dims", sender.IsChecked)
        self._update_source_views_count()

    def from_selection_clicked(self, sender, e):
        if self._busy:
            return
        if self.uidoc is None:
            self._set_status(u"Revit has no active window to read the selection from — "
                             u"tick the targets in the grid.", "Warning")
            return
        try:
            ids = dc.selected_assembly_ids(self.doc, self.uidoc)
        except Exception as exc:
            self._set_status(u"Could not read the Revit selection: %s." % short_error(exc), "Danger")
            return
        self._tick_ids([i for i in ids if self._source is None or i != self._source.id],
                       u"Revit selection")

    def pick_in_model_clicked(self, sender, e):
        """Hide -> PickObjects -> Show (spec D8). [NV] G24."""
        if self._busy:
            return
        if self.uidoc is None:
            self._set_status(u"Pick in model is not available here — tick the targets in the grid.",
                             "Warning")
            return
        try:
            self.Hide()
        except Exception:
            pass
        try:
            ids, error = dc.pick_assembly_ids(self.doc, self.uidoc)
        except Exception as exc:
            ids, error = [], short_error(exc)
        finally:
            try:
                self.Show()
            except Exception:
                pass
        if error:
            self._set_status(u"Pick in model is not available here (%s) — tick the targets "
                             u"in the grid." % error, "Warning")
            return
        self._tick_ids([i for i in ids if self._source is None or i != self._source.id],
                       u"model pick")

    def options_changed(self, sender, e):
        if getattr(self, '_loading', True) or getattr(self, '_loading_settings', False):
            return
        self._update_statuses(reset_ticks=False)

    def settings_changed(self, sender, e):
        if getattr(self, '_loading', True) or getattr(self, '_loading_settings', False):
            return
        self._refresh_state()

    def preset_changed(self, sender, e):
        if getattr(self, '_loading', True) or getattr(self, '_loading_presets', False):
            return
        name = self._current_preset_name()
        self._apply_settings(self._preset_settings(name))
        self.tb_preset_name.Text = u"" if name in dc.BUILTIN_PRESET_NAMES else name
        self._update_statuses(reset_ticks=False)
        self._set_status(u"Preset %s applied." % name, "Success")

    def preset_save_clicked(self, sender, e):
        name = (self.tb_preset_name.Text or u"").strip()
        if not name:
            self._set_status(u"Type a preset name next to the preset list, then press Save.",
                             "Warning")
            return
        if name in dc.BUILTIN_PRESET_NAMES:
            self._set_status(u"%s is a built-in preset — choose another name." % name, "Warning")
            return
        settings, error = self._settings()
        if settings is None:
            self._set_status(error, "Warning")
            return
        self._presets[name] = settings
        error = dc.save_presets(self._preset_path, self._presets, name)
        if error:
            self._set_status(u"Preset %s not saved: %s. Check %s." % (name, error, self._preset_path),
                             "Danger")
            return
        self._fill_preset_combo(name)
        self._set_status(u"Preset %s saved (%d user presets)." % (name, len(self._presets)), "Success")

    def preset_delete_clicked(self, sender, e):
        name = self._current_preset_name()
        if name in dc.BUILTIN_PRESET_NAMES or name not in self._presets:
            self._set_status(u"%s is built in and cannot be deleted." % name, "Warning")
            return
        if not confirm(u"Delete preset %s?" % name, title=TITLE, ok_text=u"Delete", danger=True,
                       details=u"The clone settings on this page stay as they are."):
            return
        del self._presets[name]
        error = dc.save_presets(self._preset_path, self._presets, dc.PRESET_TEKLA)
        self._fill_preset_combo(dc.PRESET_TEKLA)
        if error:
            self._set_status(u"Preset %s removed from the list but the file was not updated: %s"
                             % (name, error), "Danger")
        else:
            self._set_status(u"Preset %s deleted." % name, "Success")
        self._refresh_state()

    def log_filter_checked(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._apply_log_filter()

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
        if not self._log_entries:
            self._set_status(u"The log is empty — run Clone first.", "Warning")
            return
        try:
            Clipboard.SetText(u"\r\n".join(u"%s %s" % (stamp, text)
                                           for _, stamp, text in self._log_entries))
            self._set_status(u"Copied %s to the clipboard."
                             % _plural(len(self._log_entries), u"log line"), "Success")
        except Exception as exc:
            self._set_status(u"Could not copy the log: %s. Try again." % short_error(exc), "Danger")

    def clone_clicked(self, sender, e):
        if self._busy:
            return
        settings, error = self._settings()
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
        replace_plan = {}
        delete_count = 0
        if settings.existing == dc.EXISTING_REPLACE:
            replace_plan = self._replace_plan(chosen)
            delete_count = sum(len(ids) for plan in replace_plan.values() for _, ids in plan)
        message, details, ok_text = dc.confirm_text(self._source.mark, self._src_specs,
                                                    len(chosen), settings, delete_count)
        if not confirm(message, title=TITLE, ok_text=ok_text, details=details):
            return
        if settings.existing == dc.EXISTING_REPLACE and delete_count:
            if not confirm(u"Delete %s on the target views before cloning?"
                           % _plural(delete_count, u"annotation element"),
                           title=TITLE, ok_text=u"Delete and clone", danger=True,
                           details=(u"Tags, dimensions, text notes, detail items and symbols owned by "
                                    u"the views of %s. Views, sheets, viewports and model elements "
                                    u"are never deleted. Ctrl+Z undoes the whole run."
                                    % _plural(len(replace_plan), u"assembly", u"assemblies"))):
                return
        self._run_clone(settings, chosen, replace_plan)


def show_clone_drawing(doc, uidoc=None):
    """Entry point used by the pushbutton. Returns the sheet id to open, or None."""
    dialog = CloneDrawingDialog(doc, uidoc)
    dialog.ShowDialog()
    return dialog.open_sheet_id
