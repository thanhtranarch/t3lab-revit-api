# -*- coding: utf-8 -*-
"""
CastUnitDialog.py
=================
Cast Unit Manager — assemblies (Tekla: cast units) with their rebar, in bulk.

Three pages behind a left rail:

* **Batch create**      — one assembly per host (or all hosts in one) from the
                          current selection or a category / level / workset /
                          type filter, hosted rebar included, optional name series.
* **Manage**            — every assembly with its members, rebar, rebar that is
                          hosted but not a member ("Unsynced"), views and sheets;
                          Sync rebar, Rename as a series, Open sheet.
* **Partition by rule** — write the rebar Partition parameter from a rule such
                          as ``{AssemblyMark}-{Level}``; Revit numbers inside it.

All Revit work goes through ``Snippets._assembly`` / ``Snippets._rebar``; this
module is UI, previews and the TransactionGroup around each click (rule A6).
The window is modal (spec D8). "Pick in model" closes it, picks on the clean
Revit UI thread and opens it again (``show_cast_unit`` loop) — PickObjects from
inside a modal WPF window crashes Revit (see TileLayoutDialog).

Spec: dev/plan/rebar-tekla-implementation-spec.md section 5.2.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"
__title__ = "Cast Unit Manager"

import os
import re

import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from System import Uri, UriKind
from System.Windows import RoutedEventHandler, Visibility
from System.Windows.Controls import CheckBox
from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage

from GUI.WPF_Base import T3WPFWindow
from GUI.T3Dialog import confirm as t3_confirm, show_error, show_info, show_warning

from Snippets import _assembly, _rebar
from Snippets._compat import disposing, eid_value, elem_name, make_eid, net_list, short_error
from Snippets._host import resolve_uidoc

GUI_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'CastUnit.xaml')

TOOL_TITLE = u"Cast Unit Manager"

PAGE_CREATE = 0
PAGE_MANAGE = 1
PAGE_PARTITION = 2
PAGE_BY_NAME = {"create": PAGE_CREATE, "manage": PAGE_MANAGE, "partition": PAGE_PARTITION}
NAV_TILES = ("nav_create", "nav_manage", "nav_partition")

# Glyph of the single primary button per page (all from the T3 glyph table).
PRIMARY_GLYPHS = {
    PAGE_CREATE: u"",       # Add
    PAGE_MANAGE: u"",       # Refresh = sync
    PAGE_PARTITION: u"",    # Tag = classify / assign
}

SEV_OK = "Success"
SEV_WARN = "Warning"
SEV_BAD = "Danger"

NONE_TEXT = u"— none —"           # the XAML Trigger greys exactly this text

# (label, BuiltInCategory name or None = every host category)
CATEGORY_CHOICES = (
    (u"Structural Framing", "OST_StructuralFraming"),
    (u"Structural Columns", "OST_StructuralColumns"),
    (u"Structural Foundations", "OST_StructuralFoundation"),
    (u"Floors", "OST_Floors"),
    (u"Walls", "OST_Walls"),
    (u"All host categories", None),
)
ALL_LEVELS = u"All levels"
ALL_WORKSETS = u"All worksets"
ALL_TYPES = u"All types"

CUSTOM_RULE = u"Custom..."
RULE_PRESETS = (u"{AssemblyMark}", u"{Level}", u"{HostType}", u"{Level}-{HostType}",
                u"{Workset}", CUSTOM_RULE)
DEFAULT_RULE = u"{AssemblyMark}"

MAX_DIGITS = 8

E_SEL_0 = (u"Nothing to work on. Select hosts, rebar or assemblies in the model, "
           u"or use the filter, then try again.")
E_TICK_0 = u"Nothing is ticked. Tick rows in the table first, then try again."
E_SERIES = (u"The series fields are not valid: start and step must be whole numbers "
            u"(step 1 or more) and digits 0 to %d. Fix them, then try again." % MAX_DIGITS)
E_NOT_MODEL_VIEW = (u"Isolate needs a model view. Open a plan, section or 3D view "
                    u"and try again.")


# ── SMALL HELPERS (pure) ─────────────────────────────────────────────────────

def _n(value):
    """1286 -> '1 286' (the spec's thousands separator)."""
    try:
        return u"{:,}".format(int(value)).replace(u",", u" ")
    except Exception:
        return u"%s" % value


def _plural(count, word, plural=None):
    return u"%s %s" % (_n(count), word if count == 1 else (plural or word + u"s"))


def _natural_key(text):
    """'C-2' < 'C-10'. re.split with a group keeps str at even, digits at odd slots."""
    parts = re.split(r"(\d+)", text or u"")
    return [int(p) if i % 2 else p.lower() for i, p in enumerate(parts)]


def _parse_series(prefix, start, step, digits):
    """((prefix, start, step, digits), None) or (None, error text)."""
    try:
        start_v = int((start or u"").strip())
        step_v = int((step or u"").strip())
        digits_v = int((digits or u"").strip())
    except ValueError:
        return None, E_SERIES
    if step_v < 1 or digits_v < 0 or digits_v > MAX_DIGITS:
        return None, E_SERIES
    return (prefix or u"", start_v, step_v, digits_v), None


_SKIP_SHORT = {
    _assembly.SKIP_NOT_VALID: u"not valid",
    _assembly.SKIP_NO_HOST: u"host missing",
}


def _skip_label(reason):
    """Short pill text for a skip reason from _assembly.SKIP_*."""
    if not reason:
        return u"Skipped"
    if reason.startswith(u"already in assembly"):
        return u"Skip: in assembly"
    return u"Skip: %s" % _SKIP_SHORT.get(reason, reason)


def _skip_breakdown(results):
    """'2 in group, 1 from link' for the skipped rows of a batch."""
    counts = {}
    for row in results:
        if row.status != _assembly.STATUS_SKIPPED:
            continue
        key = row.detail or u"skipped"
        if key.startswith(u"already in assembly"):
            key = u"in another assembly"
        counts[key] = counts.get(key, 0) + 1
    return u", ".join(u"%s %s" % (_n(v), k) for k, v in sorted(counts.items()))


def _outcome_text(verb_done, noun, noun_plural, results):
    """'Created 12 assemblies · 2 skipped (in group) · 0 failed' + severity."""
    summary = _assembly.summarize_results(results)
    ok = summary[_assembly.STATUS_OK]
    skipped = summary[_assembly.STATUS_SKIPPED]
    failed = summary[_assembly.STATUS_FAILED]
    text = u"%s %s" % (verb_done, _plural(ok, noun, noun_plural))
    if skipped:
        text += u" · %s skipped (%s)" % (_n(skipped), _skip_breakdown(results))
    else:
        text += u" · 0 skipped"
    text += u" · %s failed" % _n(failed)
    severity = SEV_BAD if failed else (SEV_WARN if skipped else SEV_OK)
    return text, severity


def _first_failure(results):
    for row in results:
        if row.status == _assembly.STATUS_FAILED:
            return row
    return None


# ── ROWS ─────────────────────────────────────────────────────────────────────
# Plain attributes: WPF reads them through pythonnet; checkboxes go through the
# string bridge (rule 24) and StatusText / Severity feed T3.StatusPill.

class HostRow(object):
    """One host on the Batch create page."""

    def __init__(self, record):
        self.record = record
        self.id = record.id
        self.is_selected = False
        self.host_text = u"%s  [%d]" % (record.name or u"(unnamed)", record.id)
        self.category = record.category or u""
        self.rebar_text = u"0"
        self.assembly_text = NONE_TEXT
        self.loose_rebar = 0
        self.blocked_rebar = 0
        self.skip_reason = None
        self.StatusText = u"Ready"
        self.Severity = SEV_OK

    @property
    def creatable(self):
        return self.skip_reason is None


class AssemblyRow(object):
    """One AssemblyInstance on the Manage page."""

    def __init__(self, record, unsynced):
        self.record = record
        self.id = record.id
        self.type_id = record.type_id
        self.is_selected = False
        self.mark = record.mark or u"(no name)"
        self.instances_text = _n(record.instances)
        self.members_text = _n(len(record.members))
        self.rebar_text = _n(len(record.rebar_ids))
        self.unsynced = len(unsynced)
        self.unsynced_ids = list(unsynced)
        self.unsynced_text = _n(self.unsynced)
        self.views_text = _n(len(record.view_ids))
        self.sheets_text = _n(len(record.sheet_ids))
        self.level = record.level or u""
        if self.unsynced:
            self.StatusText, self.Severity = u"%s not synced" % _n(self.unsynced), SEV_WARN
        elif not record.view_ids:
            self.StatusText, self.Severity = u"No views", SEV_WARN
        elif not record.sheet_ids:
            self.StatusText, self.Severity = u"No sheet", SEV_WARN
        else:
            self.StatusText, self.Severity = u"In sync", SEV_OK


class PartitionRow(object):
    """One reinforcement element on the Partition by rule page."""

    def __init__(self, record, host_text, assembly_text):
        self.record = record
        self.id = record.id
        self.is_selected = False
        label = record.bar_type or record.kind
        self.rebar_text = u"%d  %s" % (record.id, label)
        self.host_text = host_text
        self.assembly_text = assembly_text or NONE_TEXT
        self.current_text = record.partition or NONE_TEXT
        self.new_value = u""
        self.new_text = u""
        self.actionable = False
        self.StatusText = u"Ready"
        self.Severity = SEV_OK


# ── WINDOW ───────────────────────────────────────────────────────────────────

class CastUnitDialog(T3WPFWindow):
    """Cast Unit Manager window (modal)."""

    def __init__(self, doc, page=None, ids=None, state=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc
        self.uidoc = resolve_uidoc()
        self.action = None          # ("pick",) | ("open_sheet", sheet_id) — run after close
        self._loading = True
        self._busy = False
        self._page = PAGE_CREATE
        self._run_phase = u""
        state = state or {}

        self._index = state.get("index")
        self._index_note = u""
        self._assemblies = []
        self._asm_by_id = {}
        self._member_owner = {}     # element id -> assembly id
        self._hosts = []
        self._host_rows = []
        self._asm_rows = []
        self._part_rows = []
        self._asm_ticks = None      # ids to tick on the next rebuild (after a run)
        self._asm_touched = False   # False = default ticks (rows with unsynced rebar)
        self._create_results = {}   # host id -> (text, severity) after a run
        self._category_cache = {}   # BuiltInCategory name / "*" -> [HostRecord]
        self._filter_records = []
        self._levels = [ALL_LEVELS]
        self._worksets = [ALL_WORKSETS]
        self._types = [ALL_TYPES]
        self._host_ctx = {}         # host id -> partition context
        self._selection_ids = None
        self._selection_hosts = None
        self._scope_override = [eid_value(i) for i in ids] if ids else None
        self._partition_warning = None
        self._warning_checked = False
        self._rendered = False

        self._load_logo()
        self._wire_row_events()
        self._init_combos()
        self._restore_state(state)
        if self._index is None:
            self._index = _rebar.RebarIndex()
            self._index_pending = True
        else:
            self._index_pending = False
        self._reload_assemblies()
        self._reload_hosts()
        self._preview_partition()
        self._loading = False

        self._show_page(PAGE_BY_NAME.get(page, PAGE_CREATE))
        if self._index_pending:
            self._set_status(u"Reading reinforcement from the model…", SEV_OK)
        else:
            self._set_ready_status()
        try:
            self.ContentRendered += self._after_first_render
            self.Closing += self._guard_closing
        except Exception:
            pass

    # ── SETUP ────────────────────────────────────────────────────────────────

    def _load_logo(self):
        """Load and bind the T3Lab logo to the title bar and the window icon."""
        try:
            logo_path = os.path.join(GUI_DIR, 'T3Lab_logo.png')
            if os.path.exists(logo_path):
                bitmap = BitmapImage()
                bitmap.BeginInit()
                bitmap.CacheOption = BitmapCacheOption.OnLoad
                bitmap.UriSource = Uri(logo_path, UriKind.Absolute)
                bitmap.EndInit()
                bitmap.Freeze()
                if getattr(self, 'logo_image', None) is not None:
                    self.logo_image.Source = bitmap
                self.Icon = bitmap
        except Exception:
            pass

    def _wire_row_events(self):
        """Row checkboxes live in a DataTemplate: catch their Click on the grid."""
        for grid, handler in ((self.grid_create, self.grid_create_checkbox_clicked),
                              (self.grid_manage, self.grid_manage_checkbox_clicked),
                              (self.grid_partition, self.grid_partition_checkbox_clicked)):
            try:
                grid.AddHandler(CheckBox.ClickEvent, RoutedEventHandler(handler), True)
            except Exception:
                pass

    def _init_combos(self):
        self.set_items_source(self.cb_category, [label for label, _ in CATEGORY_CHOICES])
        self.cb_category.SelectedIndex = 0
        self._fill_combo(self.cb_level, self._levels)
        self._fill_combo(self.cb_workset, self._worksets)
        self._fill_combo(self.cb_type, self._types)
        self.set_items_source(self.cb_rule_preset, list(RULE_PRESETS))
        self.cb_rule_preset.SelectedIndex = 0
        self.tb_rule.Text = DEFAULT_RULE

    def _fill_combo(self, combo, items, keep=None):
        self.set_items_source(combo, items)
        index = items.index(keep) if keep in items else 0
        combo.SelectedIndex = index

    def _restore_state(self, state):
        """Options carried over a Pick-in-model round trip."""
        if not state:
            return
        self.rb_src_selection.IsChecked = True
        self.chk_include_rebar.IsChecked = bool(state.get("include_rebar", True))
        if state.get("one_per_host", True):
            self.rb_one_per_host.IsChecked = True
        else:
            self.rb_all_in_one.IsChecked = True
        self.chk_name_series.IsChecked = bool(state.get("name_series", False))
        for name in ("tb_prefix", "tb_start", "tb_step", "tb_digits"):
            if name in state:
                getattr(self, name).Text = state[name]

    def export_state(self):
        """What the next window needs after Pick in model (index is reused)."""
        return {
            "index": self._index,
            "include_rebar": bool(self.chk_include_rebar.IsChecked),
            "one_per_host": bool(self.rb_one_per_host.IsChecked),
            "name_series": bool(self.chk_name_series.IsChecked),
            "tb_prefix": self.tb_prefix.Text or u"",
            "tb_start": self.tb_start.Text or u"",
            "tb_step": self.tb_step.Text or u"",
            "tb_digits": self.tb_digits.Text or u"",
        }

    def _after_first_render(self, sender, e):
        """Read the rebar index once the window is visible, with progress."""
        if self._rendered:
            return
        self._rendered = True
        try:
            if self._index_pending:
                self._build_index()
                self._refresh_all()
                self._set_ready_status()
        except Exception as exc:
            self._set_status(u"Could not read the model: %s. Close the window and run "
                             u"Cast Unit Manager again." % short_error(exc), SEV_BAD)

    def _guard_closing(self, sender, e):
        """Esc / X while a run is going: stop after the current item instead."""
        if self._busy:
            try:
                e.Cancel = True
            except Exception:
                pass
            self.stop_clicked()

    # ── INDEX & MODEL READS ──────────────────────────────────────────────────

    def _action_controls(self):
        return [self.btn_primary, self.btn_select, self.btn_isolate, self.btn_pick,
                self.btn_rename, self.btn_open_sheet, self.btn_refresh,
                self.nav_create, self.nav_manage, self.nav_partition]

    def _build_index(self):
        """_rebar.build_rebar_index with the footer progress bar (once per open)."""
        self._busy = True
        self.begin_progress(maximum=100, disable=self._action_controls())
        try:
            index = _rebar.build_rebar_index(self.doc, progress=self._index_progress)
            self._index_note = (u"Reinforcement scan stopped — rebar counts are partial. "
                                u"Press Refresh on the Manage page to read it again."
                                if index.stopped else u"")
        except Exception as exc:
            index = _rebar.RebarIndex()
            self._index_note = (u"Could not read the reinforcement: %s. Rebar counts show 0; "
                                u"press Refresh on the Manage page to try again." % short_error(exc))
        finally:
            self.end_progress()
            self._busy = False
        self._index = index
        self._index_pending = False

    def _index_progress(self, done, total, label):
        try:
            self.pb_run.Maximum = max(1, total)
        except Exception:
            pass
        return self.step_progress(done, u"Reading reinforcement — %s / %s"
                                  % (_n(done), _n(total)))

    def _reload_assemblies(self):
        try:
            self._assemblies = _assembly.collect_assemblies(self.doc)
        except Exception as exc:
            self._assemblies = []
            self._index_note = (u"Could not read the assemblies: %s. Press Refresh to try "
                                u"again." % short_error(exc))
        self._asm_by_id = dict((a.id, a) for a in self._assemblies)
        self._member_owner = {}
        for record in self._assemblies:
            for member in record.members:
                self._member_owner[member] = record.id
        self._sync_index_membership()
        self._build_asm_rows()
        self._apply_filter_manage()

    def _sync_index_membership(self):
        """Keep RebarRecord.assembly_id in line with the assemblies just read."""
        for row in getattr(self._index, "rows", ()):
            owner = self._member_owner.get(row.id)
            if owner is not None:
                row.assembly_id = owner
            elif row.assembly_id >= 0 and row.assembly_id not in self._asm_by_id:
                row.assembly_id = _assembly.NO_ASSEMBLY

    def _refresh_all(self):
        self._sync_index_membership()
        self._build_asm_rows()
        self._apply_filter_manage()
        self._build_host_rows()
        self._apply_filter_create()
        self._preview_partition()
        self._update_actions()

    def _mark_of(self, assembly_id):
        record = self._asm_by_id.get(assembly_id)
        if record is None:
            return u"" if assembly_id < 0 else u"#%d" % assembly_id
        return record.mark or u"#%d" % assembly_id

    # ── STATUS ───────────────────────────────────────────────────────────────

    def _set_status(self, text, severity=None):
        try:
            self.status_text.Text = text
        except Exception:
            pass
        if severity:
            try:
                self.dot_status.Fill = self.FindResource("T3.%s.Accent" % severity)
            except Exception:
                pass

    def _set_ready_status(self):
        rebar = len(getattr(self._index, "rows", ()))
        text = u"Ready — %s, %s rebar" % (_plural(len(self._assemblies), u"assembly", u"assemblies"),
                                          _n(rebar))
        if self._index_note:
            self._set_status(text + u" · " + self._index_note, SEV_WARN)
        elif self._page == PAGE_PARTITION and self._scope_override is not None:
            self._set_status(u"%s handed over — check the rule, then assign. %s ready."
                             % (_plural(len(self._part_rows), u"bar"),
                                _n(len(self._assign_rows()))), SEV_OK)
        elif self._page == PAGE_CREATE and not self._hosts:
            self._set_source_status()
        else:
            self._set_status(text, SEV_OK)

    # ── PAGES ────────────────────────────────────────────────────────────────

    def _show_page(self, page):
        tile = getattr(self, NAV_TILES[page], None)
        if tile is not None and not tile.IsChecked:
            tile.IsChecked = True       # may fire nav_toggle_clicked; _apply_page is idempotent
        self._apply_page(page)

    def _apply_page(self, page):
        self._page = page
        try:
            self.tab_control.SelectedIndex = page
        except Exception:
            pass
        if page == PAGE_PARTITION:
            self._check_partition_warning()
        self._update_actions()

    def nav_toggle_clicked(self, sender, e):
        # PythonNet may hand back different wrappers for one control: route by Name.
        name = getattr(sender, "Name", None)
        if name in NAV_TILES:
            self._apply_page(NAV_TILES.index(name))

    def _update_actions(self):
        """Primary label / glyph / enabled state for the active page + side buttons."""
        if self._loading:
            return
        page = self._page
        if page == PAGE_CREATE:
            count, _hosts, _rebar_n, error = self._create_counts()
            if count:
                label = u"Create %s" % _plural(count, u"assembly", u"assemblies")
            else:
                label = u"Create assemblies"
            enabled = bool(count) and not error
        elif page == PAGE_MANAGE:
            rows = self._sync_rows()
            label = (u"Sync rebar into %s" % _plural(len(rows), u"assembly", u"assemblies")
                     if rows else u"Sync rebar")
            enabled = bool(rows)
        else:
            rows = self._assign_rows()
            label = (u"Assign partition to %s" % _plural(len(rows), u"bar")
                     if rows else u"Assign partition")
            enabled = bool(rows)
        self.btn_primary_label.Text = label
        self.btn_primary_icon.Text = PRIMARY_GLYPHS[page]
        self.btn_primary.IsEnabled = enabled and not self._busy
        self._update_manage_buttons()

    # ── PAGE 1 · BATCH CREATE ────────────────────────────────────────────────

    def _selection_host_records(self):
        if self._selection_hosts is None:
            try:
                self._selection_hosts = (_assembly.host_records_from_selection(self.doc, self.uidoc)
                                         if self.uidoc is not None else [])
            except Exception:
                self._selection_hosts = []
        return self._selection_hosts

    def _category_records(self):
        index = self.cb_category.SelectedIndex
        if index is None or index < 0 or index >= len(CATEGORY_CHOICES):
            index = 0
        bic_name = CATEGORY_CHOICES[index][1]
        key = bic_name or "*"
        if key not in self._category_cache:
            try:
                if bic_name:
                    from Autodesk.Revit.DB import BuiltInCategory
                    member = getattr(BuiltInCategory, bic_name, None)
                    records = (_assembly.collect_hosts(self.doc, [member])
                               if member is not None else [])
                else:
                    records = _assembly.collect_hosts(self.doc)
            except Exception as exc:
                records = []
                self._set_status(u"Could not read the %s of this model: %s. Pick another "
                                 u"category or use the selection."
                                 % (CATEGORY_CHOICES[index][0], short_error(exc)), SEV_BAD)
            self._category_cache[key] = records
        return self._category_cache[key]

    def _refill_filter_choices(self):
        """Level / workset / type lists from the records of the chosen category."""
        records = self._filter_records
        keep = (self._combo_value(self.cb_level, self._levels),
                self._combo_value(self.cb_workset, self._worksets),
                self._combo_value(self.cb_type, self._types))
        self._levels = [ALL_LEVELS] + sorted(set(r.level for r in records if r.level),
                                             key=_natural_key)
        self._worksets = [ALL_WORKSETS] + sorted(set(r.workset for r in records if r.workset),
                                                 key=_natural_key)
        self._types = [ALL_TYPES] + sorted(set(r.type_name for r in records if r.type_name),
                                           key=_natural_key)
        self._fill_combo(self.cb_level, self._levels, keep[0])
        self._fill_combo(self.cb_workset, self._worksets, keep[1])
        self._fill_combo(self.cb_type, self._types, keep[2])
        self.cb_workset.IsEnabled = len(self._worksets) > 1

    @staticmethod
    def _combo_value(combo, items):
        index = combo.SelectedIndex
        if index is None or index <= 0 or index >= len(items):
            return None
        return items[index]

    def _filtered_hosts(self):
        level = self._combo_value(self.cb_level, self._levels)
        workset = self._combo_value(self.cb_workset, self._worksets)
        type_name = self._combo_value(self.cb_type, self._types)
        out = []
        for record in self._filter_records:
            if level and record.level != level:
                continue
            if workset and record.workset != workset:
                continue
            if type_name and record.type_name != type_name:
                continue
            out.append(record)
        return out

    def _reload_hosts(self):
        if self.rb_src_filter.IsChecked:
            self._hosts = self._filtered_hosts()
        else:
            self._hosts = list(self._selection_host_records())
        self._build_host_rows()
        self._apply_filter_create()

    def _build_host_rows(self):
        previous = dict((row.id, row.is_selected) for row in self._host_rows)
        by_host = getattr(self._index, "by_host", {})
        rows = []
        for record in self._hosts:
            owner = self._member_owner.get(record.id)
            if owner is not None:
                record.assembly_id = owner
            row = HostRow(record)
            ok, blocked = _assembly.filter_assembly_candidates(by_host.get(record.id, ()))
            row.loose_rebar = len(ok)
            row.blocked_rebar = len(blocked)
            if record.assembly_id >= 0:
                row.assembly_text = self._mark_of(record.assembly_id)
            _, skipped = _assembly.filter_assembly_candidates([record])
            if skipped:
                reason = skipped[0][1]
                if record.assembly_id >= 0 and not record.in_group and not record.is_link:
                    reason = _assembly.SKIP_IN_ASSEMBLY % self._mark_of(record.assembly_id)
                row.skip_reason = reason
            row.is_selected = previous.get(record.id, row.creatable)
            rows.append(row)
        self._host_rows = rows
        self._preview_create()

    def _ticked_hosts(self):
        return [row for row in self._host_rows if row.is_selected]

    def _create_plans(self):
        """BatchPlans for the ticked hosts (skipped hosts carry their reason)."""
        rows = self._ticked_hosts()
        return _assembly.plan_batch_create(
            [row.record for row in rows],
            getattr(self._index, "by_host", {}),
            one_per_host=bool(self.rb_one_per_host.IsChecked),
            include_rebar=bool(self.chk_include_rebar.IsChecked))

    def _series_or_error(self):
        if not self.chk_name_series.IsChecked:
            return None, None
        return _parse_series(self.tb_prefix.Text, self.tb_start.Text,
                             self.tb_step.Text, self.tb_digits.Text)

    def _create_counts(self):
        """(assemblies, hosts, rebar, series error) the primary button would create."""
        creatable = [row for row in self._ticked_hosts() if row.creatable]
        include = bool(self.chk_include_rebar.IsChecked)
        hosts = len(creatable)
        rebar = sum(row.loose_rebar for row in creatable) if include else 0
        if not hosts:
            count = 0
        elif self.rb_one_per_host.IsChecked:
            count = hosts
        else:
            count = 1
        _, error = self._series_or_error()
        return count, hosts, rebar, error

    def _preview_create(self):
        """Rebar counts, expected marks and statuses of the Batch create rows."""
        include = bool(self.chk_include_rebar.IsChecked)
        one_per_host = bool(self.rb_one_per_host.IsChecked)
        series, _error = self._series_or_error()
        names = []
        if series:
            prefix, start, step, digits = series
            wanted = sum(1 for r in self._host_rows if r.is_selected and r.creatable)
            names = _assembly.series_names(wanted if one_per_host else min(1, wanted),
                                           prefix, start, step, digits)
        used = 0
        for row in self._host_rows:
            row.rebar_text = _n(row.loose_rebar if include else 0)
            result = self._create_results.get(row.id)
            if result is not None:
                row.StatusText, row.Severity = result
                continue
            if not row.creatable:
                row.StatusText, row.Severity = _skip_label(row.skip_reason), SEV_WARN
                continue
            row.StatusText, row.Severity = u"Ready", SEV_OK
            if row.is_selected and used < len(names):
                row.StatusText = u"Ready · %s" % names[used]
                used += 1
        try:
            self.grid_create.Items.Refresh()
        except Exception:
            pass
        self._update_create_count()
        self._update_actions()

    def _update_create_count(self):
        ticked = sum(1 for row in self._host_rows if row.is_selected)
        self.lbl_create_count.Text = u"%s · %s ticked" % (
            _plural(len(self._host_rows), u"host"), _n(ticked))
        self.sync_header_checkbox(self.chk_all_grid_create, self.grid_create, "is_selected")

    def _visible_host_rows(self):
        needle = (self.tb_create_search.Text or u"").strip().lower()
        out = []
        for row in self._host_rows:
            if self.chip_create_in.IsChecked and row.record.assembly_id < 0:
                continue
            if self.chip_create_loose.IsChecked and row.record.assembly_id >= 0:
                continue
            if needle and needle not in (u"%s %s %s" % (row.host_text, row.category,
                                                        row.assembly_text)).lower():
                continue
            out.append(row)
        return out

    def _apply_filter_create(self):
        shown = self._visible_host_rows()
        self.set_items_source(self.grid_create, shown)
        self.grid_create_empty.Visibility = Visibility.Collapsed if shown else Visibility.Visible
        self._update_create_count()

    def source_changed(self, sender, e):
        if self._loading:
            return
        use_filter = bool(self.rb_src_filter.IsChecked)
        self.pnl_filter.IsEnabled = use_filter
        self._create_results = {}
        if use_filter:
            self._loading = True
            try:
                self._filter_records = self._category_records()
                self._refill_filter_choices()
            finally:
                self._loading = False
        self._reload_hosts()
        self._set_source_status()

    def filter_changed(self, sender, e):
        if self._loading or not self.rb_src_filter.IsChecked:
            return
        self._create_results = {}
        if getattr(sender, "Name", None) == "cb_category":
            self._loading = True
            try:
                self._filter_records = self._category_records()
                self._refill_filter_choices()
            finally:
                self._loading = False
        self._reload_hosts()
        self._set_source_status()

    def _set_source_status(self):
        if not self._hosts:
            if self.rb_src_filter.IsChecked:
                self._set_status(u"No valid rebar host matches this filter. Widen the level, "
                                 u"workset or type, or pick another category.", SEV_WARN)
            else:
                self._set_status(E_SEL_0, SEV_WARN)
            return
        skipped = sum(1 for row in self._host_rows if not row.creatable)
        text = u"%s found" % _plural(len(self._hosts), u"host")
        if skipped:
            text += u" · %s will be skipped (group, link or already in an assembly)" % _n(skipped)
        self._set_status(text, SEV_WARN if skipped else SEV_OK)

    def options_changed(self, sender, e):
        if self._loading:
            return
        self._create_results = {}
        self._preview_create()
        _, error = self._series_or_error()
        if error:
            self._set_status(error, SEV_WARN)

    def mode_changed(self, sender, e):
        if self._loading:
            return
        self._create_results = {}
        self._preview_create()

    def series_changed(self, sender, e):
        if self._loading:
            return
        self._create_results = {}
        self._preview_create()
        _, error = self._series_or_error()
        if error:
            self._set_status(error, SEV_WARN)

    def create_chip_checked(self, sender, e):
        if self._loading:
            return
        self._apply_filter_create()

    def create_search_changed(self, sender, e):
        if self._loading:
            return
        self._apply_filter_create()

    def grid_create_checkbox_clicked(self, sender, e):
        if self._loading:
            return
        self._preview_create()

    def select_all_grid_create_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_create, "is_selected", sender.IsChecked)
        self._preview_create()

    def pick_clicked(self, sender, e):
        if self._busy:
            return
        self.action = ("pick",)
        self.Close()

    def _run_batch_create(self):
        count, hosts, rebar_n, error = self._create_counts()
        if error:
            self._set_status(error, SEV_WARN)
            return
        if not count:
            self._set_status(E_SEL_0 if not self._host_rows else E_TICK_0, SEV_WARN)
            return
        plans = self._create_plans()
        noun = _plural(count, u"assembly", u"assemblies")
        message = u"Create %s from %s (%s rebar)?" % (noun, _plural(hosts, u"host"), _n(rebar_n))
        details = (u"Hosts in a group, from a link or already in an assembly are skipped. "
                   u"Ctrl+Z undoes the whole batch in one step.")
        if not t3_confirm(message, title=TOOL_TITLE, ok_text=u"Create %s" % noun,
                          details=details, owner=self):
            return

        series, _ = self._series_or_error()
        prefix, start, step, digits = series if series else (u"", 1, 1, 3)
        from Autodesk.Revit.DB import TransactionGroup
        try:
            before = _assembly.assembly_type_map(self.doc)
        except Exception:
            before = {}
        created = []
        results = []
        self._begin_run(u"Creating assemblies", len(plans))
        try:
            with disposing(TransactionGroup(self.doc, u"T3Lab: Create assemblies")) as group:
                group.Start()
                try:
                    results = _assembly.batch_create(self.doc, plans, progress=self._step,
                                                     prefix=prefix, start=start, step=step,
                                                     digits=digits, created=created)
                    group.Assimilate()
                except Exception:
                    if not group.HasEnded():
                        group.RollBack()
                    raise
        except Exception as exc:
            self._end_run()
            show_error(u"Could not create the assemblies: %s. Nothing was changed — "
                       u"check the hosts named in the table and try again." % short_error(exc),
                       title=TOOL_TITLE, details=u"%s" % exc, owner=self)
            self._set_status(u"Create assemblies failed — nothing was changed.", SEV_BAD)
            return
        self._end_run()
        self._after_create(plans, results, created, before)

    def _after_create(self, plans, results, created, before):
        try:
            after = _assembly.assembly_type_map(self.doc)
        except Exception:
            after = {}
        pre_after = dict((i, after[i]) for i in before if i in after)
        split, merged, changed = _assembly.diff_type_split(before, pre_after)
        new_types = len(set(after[i] for i in created if i in after))

        self._reload_assemblies()
        self._create_results = {}
        for plan, row in zip(plans, results):
            if row.status == _assembly.STATUS_OK:
                owner = self._member_owner.get(plan.naming_host_id)
                state = (u"Created · %s" % self._mark_of(owner) if owner is not None
                         else u"Created", SEV_OK)
            elif row.status == _assembly.STATUS_SKIPPED:
                state = (_skip_label(row.detail), SEV_WARN)
            else:
                state = (u"Failed", SEV_BAD)
            for host_id in (plan.host_ids or [plan.naming_host_id]):
                self._create_results[host_id] = state
        for row in self._host_rows:
            if row.id in self._create_results and row.is_selected:
                row.is_selected = False
        self._build_host_rows()
        self._apply_filter_create()
        if created and self.uidoc is not None:
            _assembly.select_in_revit(self.uidoc, created)

        text, severity = _outcome_text(u"Created", u"assembly", u"assemblies", results)
        if created:
            text = text.replace(_plural(len(created), u"assembly", u"assemblies"),
                                u"%s (%s)" % (_plural(len(created), u"assembly", u"assemblies"),
                                              _plural(new_types, u"type")), 1)
            text += u" · new assemblies are selected in Revit"
        self._set_status(text, severity)
        failure = _first_failure(results)
        if failure is not None:
            show_warning(u"%s could not be made into an assembly: %s. Check it in the model "
                         u"(Edit Assembly / ungroup), then run Batch create again."
                         % (failure.name, failure.detail), title=TOOL_TITLE, owner=self)
        self._report_type_split(split, merged, changed)

    # ── PAGE 2 · MANAGE ──────────────────────────────────────────────────────

    def _unsynced_ids(self, record):
        """Loose rebar hosted by this assembly's hosts (the Sync count)."""
        by_host = getattr(self._index, "by_host", {})
        rebar = set(record.rebar_ids)
        out = []
        for member in record.members:
            if member in rebar:
                continue
            rows, _ = _assembly.filter_assembly_candidates(by_host.get(member, ()))
            out.extend(row.id for row in rows)
        return out

    def _build_asm_rows(self):
        previous = self._asm_ticks
        if previous is None and self._asm_touched:
            previous = set(row.id for row in self._asm_rows if row.is_selected)
        rows = []
        for record in sorted(self._assemblies, key=lambda a: _natural_key(a.mark)):
            row = AssemblyRow(record, self._unsynced_ids(record))
            row.is_selected = (row.id in previous) if previous is not None else bool(row.unsynced)
            rows.append(row)
        self._asm_rows = rows
        self._asm_ticks = None

    def _visible_asm_rows(self):
        needle = (self.tb_manage_search.Text or u"").strip().lower()
        out = []
        for row in self._asm_rows:
            if self.chip_no_views.IsChecked and row.record.view_ids:
                continue
            if self.chip_no_sheet.IsChecked and row.record.sheet_ids:
                continue
            if needle and needle not in (u"%s %s" % (row.mark, row.level)).lower():
                continue
            out.append(row)
        return out

    def _apply_filter_manage(self):
        shown = self._visible_asm_rows()
        self.set_items_source(self.grid_manage, shown)
        self.grid_manage_empty.Visibility = Visibility.Collapsed if shown else Visibility.Visible
        self._after_manage_ticks()

    def _after_manage_ticks(self):
        ticked = sum(1 for row in self._asm_rows if row.is_selected)
        unsynced = sum(row.unsynced for row in self._asm_rows)
        self.lbl_manage_count.Text = u"%s · %s ticked · %s unsynced rebar" % (
            _plural(len(self._asm_rows), u"assembly", u"assemblies"), _n(ticked), _n(unsynced))
        self.sync_header_checkbox(self.chk_all_grid_manage, self.grid_manage, "is_selected")
        self._update_actions()

    def _ticked_asm_rows(self):
        return [row for row in self._asm_rows if row.is_selected]

    def _sync_rows(self):
        return [row for row in self._ticked_asm_rows() if row.unsynced]

    def _rename_type_ids(self):
        """Distinct types of the ticked rows, in natural order of their current mark."""
        rows = sorted(self._ticked_asm_rows(), key=lambda r: _natural_key(r.record.mark))
        out = []
        for row in rows:
            if row.type_id not in out:
                out.append(row.type_id)
        return out

    def _rename_series(self):
        return _parse_series(self.tb_ren_prefix.Text, self.tb_ren_start.Text,
                             self.tb_ren_step.Text, self.tb_ren_digits.Text)

    def _update_manage_buttons(self):
        types = self._rename_type_ids()
        series, error = self._rename_series()
        self.btn_rename_label.Text = (u"Rename %s as series" % _plural(len(types), u"type")
                                      if types else u"Rename as series")
        self.btn_rename.IsEnabled = bool(types) and not error and not self._busy
        ticked = self._ticked_asm_rows()
        self.btn_open_sheet.IsEnabled = (len(ticked) == 1 and bool(ticked[0].record.sheet_ids)
                                         and not self._busy)

    def manage_search_changed(self, sender, e):
        if self._loading:
            return
        self._apply_filter_manage()

    def manage_chip_checked(self, sender, e):
        if self._loading:
            return
        self._apply_filter_manage()

    def grid_manage_checkbox_clicked(self, sender, e):
        if self._loading:
            return
        self._asm_touched = True
        self._after_manage_ticks()
        if self.rb_scope_assemblies.IsChecked:
            self._preview_partition()

    def select_all_grid_manage_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_manage, "is_selected", sender.IsChecked)
        self._asm_touched = True
        self._after_manage_ticks()
        if self.rb_scope_assemblies.IsChecked:
            self._preview_partition()

    def rename_fields_changed(self, sender, e):
        if self._loading:
            return
        self._update_manage_buttons()
        _, error = self._rename_series()
        if error:
            self._set_status(error, SEV_WARN)

    def refresh_clicked(self, sender, e):
        if self._busy:
            return
        self._category_cache = {}
        self._host_ctx = {}
        self._selection_hosts = None
        self._create_results = {}
        self._build_index()
        self._reload_assemblies()
        if self.rb_src_filter.IsChecked:
            self._filter_records = self._category_records()
        self._hosts = (self._filtered_hosts() if self.rb_src_filter.IsChecked
                       else list(self._selection_host_records()))
        self._refresh_all()
        self._set_ready_status()

    def open_sheet_clicked(self, sender, e):
        if self._busy:
            return
        ticked = self._ticked_asm_rows()
        if len(ticked) != 1 or not ticked[0].record.sheet_ids:
            self._set_status(u"Tick exactly one assembly that has a sheet, then press "
                             u"Open sheet.", SEV_WARN)
            return
        self.action = ("open_sheet", ticked[0].record.sheet_ids[0])
        self.Close()

    def _run_sync(self):
        rows = self._sync_rows()
        if not rows:
            self._set_status(u"No ticked assembly has unsynced rebar. Tick rows whose "
                             u"Unsynced count is above 0.", SEV_WARN)
            return
        bars = sum(row.unsynced for row in rows)
        noun = _plural(len(rows), u"assembly", u"assemblies")
        if not t3_confirm(u"Add %s hosted rebar to %s?" % (_n(bars), noun),
                          title=TOOL_TITLE, ok_text=u"Sync rebar into %s" % noun,
                          details=u"Only rebar that is in no assembly, group or link is added. "
                                  u"Ctrl+Z undoes the whole sync in one step.",
                          owner=self):
            return
        from Autodesk.Revit.DB import TransactionGroup
        ids = [row.id for row in rows]
        results = []
        self._begin_run(u"Syncing rebar", len(ids))
        try:
            with disposing(TransactionGroup(self.doc, u"T3Lab: Sync rebar")) as group:
                group.Start()
                try:
                    results = _assembly.sync_rebar(self.doc, ids, self._index,
                                                   progress=self._step)
                    group.Assimilate()
                except Exception:
                    if not group.HasEnded():
                        group.RollBack()
                    raise
        except Exception as exc:
            self._end_run()
            show_error(u"Could not sync rebar: %s. Nothing was changed — refresh the "
                       u"Manage page and try again." % short_error(exc),
                       title=TOOL_TITLE, details=u"%s" % exc, owner=self)
            self._set_status(u"Sync rebar failed — nothing was changed.", SEV_BAD)
            return
        self._end_run()
        self._asm_ticks = set(ids)
        self._asm_touched = True
        self._reload_assemblies()
        self._refresh_all()
        added = sum(row.count for row in results if row.status == _assembly.STATUS_OK)
        text, severity = _outcome_text(u"Synced", u"assembly", u"assemblies", results)
        self._set_status(u"%s · %s added" % (text, _plural(added, u"rebar element")), severity)
        failure = _first_failure(results)
        if failure is not None:
            show_warning(u"Rebar could not be added to %s: %s. Open it with Edit Assembly to "
                         u"check its members, then sync again." % (failure.name, failure.detail),
                         title=TOOL_TITLE, owner=self)

    def _run_rename(self):
        type_ids = self._rename_type_ids()
        series, error = self._rename_series()
        if error:
            self._set_status(error, SEV_WARN)
            return
        if not type_ids:
            self._set_status(E_TICK_0, SEV_WARN)
            return
        prefix, start, step, digits = series
        names = _assembly.series_names(len(type_ids), prefix, start, step, digits)
        span = names[0] if len(names) == 1 else u"%s … %s" % (names[0], names[-1])
        noun = _plural(len(type_ids), u"type")
        if not t3_confirm(u"Rename %s assembly %s as %s?" % (_n(len(type_ids)),
                                                              u"type" if len(type_ids) == 1 else u"types",
                                                              span),
                          title=TOOL_TITLE, ok_text=u"Rename %s" % noun,
                          details=u"Identical assemblies share one type and keep one name. If "
                                  u"Revit splits or merges types, the result says so.",
                          owner=self):
            return
        try:
            results, (split, merged, changed) = _assembly.rename_series(
                self.doc, type_ids, prefix, start, step, digits)
        except Exception as exc:
            show_error(u"Could not rename the assembly types: %s. Nothing was changed — "
                       u"check that the names are not used by another assembly type."
                       % short_error(exc), title=TOOL_TITLE, details=u"%s" % exc, owner=self)
            self._set_status(u"Rename failed — nothing was changed.", SEV_BAD)
            return
        self._asm_ticks = set(row.id for row in self._ticked_asm_rows())
        self._asm_touched = True
        self._reload_assemblies()
        self._refresh_all()
        text, severity = _outcome_text(u"Renamed", u"type", None, results)
        self._set_status(text, severity)
        self._report_type_split(split, merged, changed)

    def _report_type_split(self, split, merged, changed):
        """E-TYPE-SPLIT (rule A4): never silent when Revit split or merged types."""
        if not (split or merged):
            return
        marks = [self._mark_of(i) for i in changed[:5]]
        more = u" and %s more" % _n(len(changed) - 5) if len(changed) > 5 else u""
        show_info(u"Revit split %s and merged %s while renaming — marks changed on: %s%s. "
                  u"Check the Assembly schedule."
                  % (_plural(split, u"assembly type"), _n(merged),
                     u", ".join(marks) or u"none listed", more),
                  title=TOOL_TITLE, owner=self)

    # ── PAGE 3 · PARTITION BY RULE ───────────────────────────────────────────

    def _check_partition_warning(self):
        if self._warning_checked:
            return
        self._warning_checked = True
        try:
            self._partition_warning = _rebar.partition_warning_2027(self.doc)
        except Exception:
            self._partition_warning = None
        if self._partition_warning:
            self.txt_partition_warn.Text = self._partition_warning
            self.partition_warn.Visibility = Visibility.Visible
        else:
            self.partition_warn.Visibility = Visibility.Collapsed

    def _selection_element_ids(self):
        if self._selection_ids is None:
            try:
                self._selection_ids = [eid_value(i) for i in self.uidoc.Selection.GetElementIds()]
            except Exception:
                self._selection_ids = []
        return self._selection_ids

    def _rebar_of_assembly(self, record):
        return list(record.rebar_ids) + self._unsynced_ids(record)

    def _partition_scope_ids(self):
        """Ids of reinforcement elements in the chosen scope (unique, ordered)."""
        index = self._index
        by_id = getattr(index, "by_id", {})
        by_host = getattr(index, "by_host", {})
        if self.rb_scope_model.IsChecked:
            raw = [row.id for row in getattr(index, "rows", ())]
        elif self.rb_scope_assemblies.IsChecked:
            raw = []
            for row in self._ticked_asm_rows():
                raw.extend(self._rebar_of_assembly(row.record))
        elif self._scope_override is not None:
            raw = list(self._scope_override)
        else:
            raw = []
            for element_id in self._selection_element_ids():
                if element_id in by_id:
                    raw.append(element_id)
                elif element_id in self._asm_by_id:
                    raw.extend(self._rebar_of_assembly(self._asm_by_id[element_id]))
                elif element_id in by_host:
                    raw.extend(row.id for row in by_host[element_id])
        out = []
        seen = set()
        for element_id in raw:
            row = by_id.get(element_id)
            # RebarInSystem takes its Partition from its area / path system.
            if row is None or row.kind == "RebarInSystem" or element_id in seen:
                continue
            seen.add(element_id)
            out.append(element_id)
        return out

    def _host_context(self, host_id):
        """Level / type / mark / workset / category / assembly of a host (cached)."""
        if host_id in self._host_ctx:
            return self._host_ctx[host_id]
        ctx = {"Level": u"", "HostType": u"", "HostMark": u"", "Workset": u"",
               "Category": u"", "_assembly": _assembly.NO_ASSEMBLY, "_label": u""}
        element = None
        if host_id >= 0:
            try:
                element = self.doc.GetElement(make_eid(host_id))
            except Exception:
                element = None
        if element is not None:
            ctx["Level"] = _assembly.level_name_of(self.doc, element)
            ctx["Workset"] = _assembly.workset_name_of(self.doc, element)
            try:
                type_el = self.doc.GetElement(element.GetTypeId())
                ctx["HostType"] = elem_name(type_el) if type_el is not None else u""
            except Exception:
                pass
            try:
                from Autodesk.Revit.DB import BuiltInParameter
                param = element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                ctx["HostMark"] = (param.AsString() or u"") if param is not None else u""
            except Exception:
                pass
            try:
                ctx["Category"] = element.Category.Name or u""
            except Exception:
                pass
            try:
                ctx["_assembly"] = eid_value(element.AssemblyInstanceId)
            except Exception:
                pass
            ctx["_label"] = ctx["HostType"] or ctx["Category"] or (u"%d" % host_id)
        owner = self._member_owner.get(host_id)
        if owner is not None:
            ctx["_assembly"] = owner
        self._host_ctx[host_id] = ctx
        return ctx

    def _effective_assembly(self, record, host_ctx):
        if record.assembly_id >= 0:
            return record.assembly_id
        return host_ctx.get("_assembly", _assembly.NO_ASSEMBLY)

    def _partition_context(self, rebar_id):
        """The dict _rebar.render_partition expects, for one reinforcement element."""
        record = getattr(self._index, "by_id", {}).get(rebar_id)
        host_ctx = self._host_context(record.host_id if record is not None else -1)
        ctx = dict((k, v) for k, v in host_ctx.items() if not k.startswith("_"))
        assembly_id = (self._effective_assembly(record, host_ctx) if record is not None
                       else _assembly.NO_ASSEMBLY)
        ctx["AssemblyMark"] = self._mark_of(assembly_id) if assembly_id >= 0 else u""
        return ctx

    def _preview_partition(self):
        """Rebuild the preview rows of the Partition page from scope + rule."""
        previous = dict((row.id, (row.is_selected, row.actionable)) for row in self._part_rows)
        rule = (self.tb_rule.Text or u"").strip()
        skip_assigned = bool(self.chk_skip_assigned.IsChecked)
        by_id = getattr(self._index, "by_id", {})
        rows = []
        for rebar_id in self._partition_scope_ids():
            record = by_id[rebar_id]
            host_ctx = self._host_context(record.host_id)
            assembly_id = self._effective_assembly(record, host_ctx)
            row = PartitionRow(record,
                               host_ctx.get("_label") or (u"%d" % record.host_id
                                                          if record.host_id >= 0 else NONE_TEXT),
                               self._mark_of(assembly_id) if assembly_id >= 0 else u"")
            self._classify_partition_row(row, rule, skip_assigned)
            was = previous.get(rebar_id)
            row.is_selected = was[0] if was is not None and was[1] == row.actionable \
                else row.actionable
            rows.append(row)
        self._part_rows = rows
        self._apply_filter_partition()

    def _classify_partition_row(self, row, rule, skip_assigned):
        current = row.record.partition or u""
        if not rule:
            row.new_text = u""
            row.StatusText, row.Severity, row.actionable = u"No rule", SEV_WARN, False
            return
        value = _rebar.render_partition(rule, self._partition_context(row.id))
        row.new_value = value
        row.new_text = value or u"(empty)"
        if skip_assigned and current:
            row.StatusText, row.Severity, row.actionable = u"Skip: has one", SEV_WARN, False
        elif not value:
            row.StatusText, row.Severity, row.actionable = u"Skip: empty value", SEV_WARN, False
        elif value == current:
            row.StatusText, row.Severity, row.actionable = u"No change", SEV_OK, False
        elif _rebar.check_partition(value):
            row.StatusText, row.Severity, row.actionable = (
                u"Long: %d chars" % len(value), SEV_WARN, True)
        else:
            row.StatusText, row.Severity, row.actionable = u"Ready", SEV_OK, True

    def _visible_part_rows(self):
        needle = (self.tb_partition_search.Text or u"").strip().lower()
        out = []
        for row in self._part_rows:
            if self.chip_part_in.IsChecked and row.record.assembly_id < 0:
                continue
            if self.chip_part_loose.IsChecked and row.record.assembly_id >= 0:
                continue
            if needle and needle not in (u"%s %s %s %s %s" % (
                    row.rebar_text, row.host_text, row.assembly_text,
                    row.current_text, row.new_text)).lower():
                continue
            out.append(row)
        return out

    def _apply_filter_partition(self):
        shown = self._visible_part_rows()
        self.set_items_source(self.grid_partition, shown)
        self.grid_partition_empty.Visibility = (Visibility.Collapsed if shown
                                                else Visibility.Visible)
        self._after_partition_ticks()

    def _after_partition_ticks(self):
        self.sync_header_checkbox(self.chk_all_grid_partition, self.grid_partition, "is_selected")
        self._update_actions()

    def _assign_rows(self):
        return [row for row in self._part_rows if row.is_selected and row.actionable]

    def partition_scope_changed(self, sender, e):
        if self._loading:
            return
        if getattr(sender, "Name", None) != "rb_scope_selection":
            self._scope_override = None
        self._preview_partition()
        self._set_status(u"%s in scope · %s ready to assign" % (
            _plural(len(self._part_rows), u"bar"), _n(len(self._assign_rows()))), SEV_OK)

    def partition_chip_checked(self, sender, e):
        if self._loading:
            return
        self._apply_filter_partition()

    def partition_search_changed(self, sender, e):
        if self._loading:
            return
        self._apply_filter_partition()

    def rule_preset_changed(self, sender, e):
        if self._loading:
            return
        index = self.cb_rule_preset.SelectedIndex
        if index is None or index < 0 or index >= len(RULE_PRESETS):
            return
        preset = RULE_PRESETS[index]
        if preset == CUSTOM_RULE:
            try:
                self.tb_rule.Focus()
            except Exception:
                pass
            return
        self._loading = True
        try:
            self.tb_rule.Text = preset
        finally:
            self._loading = False
        self._preview_partition()

    def rule_changed(self, sender, e):
        if self._loading:
            return
        text = (self.tb_rule.Text or u"").strip()
        self._loading = True
        try:
            self.cb_rule_preset.SelectedIndex = (RULE_PRESETS.index(text) if text in RULE_PRESETS
                                                 else len(RULE_PRESETS) - 1)
        finally:
            self._loading = False
        self._preview_partition()

    def skip_assigned_clicked(self, sender, e):
        if self._loading:
            return
        self._preview_partition()

    def grid_partition_checkbox_clicked(self, sender, e):
        if self._loading:
            return
        self._after_partition_ticks()

    def select_all_grid_partition_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_partition, "is_selected", sender.IsChecked)
        self._after_partition_ticks()

    def _run_partition(self):
        rows = self._assign_rows()
        if not rows:
            self._set_status(u"No ticked bar gets a new partition. Change the rule or the "
                             u"scope, or untick Skip bars that already have a partition.", SEV_WARN)
            return
        rule = (self.tb_rule.Text or u"").strip()
        noun = _plural(len(rows), u"bar")
        if not t3_confirm(u"Set Partition on %s? Revit will renumber them inside their "
                          u"partitions." % noun,
                          title=TOOL_TITLE, ok_text=u"Assign partition to %s" % noun,
                          details=u"Rule: %s. Rebar Number is never written — Revit numbers "
                                  u"the bars. Ctrl+Z undoes it in one step." % rule,
                          owner=self):
            return
        ids = [row.id for row in rows]
        results = []
        self._begin_run(u"Assigning partitions", len(ids))
        try:
            results = _rebar.assign_partition(self.doc, ids, rule, self._partition_context,
                                              progress=self._step)
        except Exception as exc:
            self._end_run()
            show_error(u"Could not assign partitions: %s. Nothing was changed — check that "
                       u"the bars are editable (not in a linked model) and try again."
                       % short_error(exc), title=TOOL_TITLE, details=u"%s" % exc, owner=self)
            self._set_status(u"Assign partition failed — nothing was changed.", SEV_BAD)
            return
        self._end_run()
        by_id = getattr(self._index, "by_id", {})
        values = dict((row.id, row.new_value) for row in rows)
        for result in results:
            if result.status != _assembly.STATUS_OK:
                continue
            try:
                element_id = int(result.name)
            except ValueError:
                continue
            record = by_id.get(element_id)
            if record is not None:
                record.partition = values.get(element_id, record.partition)
        self._preview_partition()
        text, severity = _outcome_text(u"Assigned partition to", u"bar", None, results)
        if self._partition_warning:
            text += u" · numbers change only once Numbering partitions by Partition"
            severity = SEV_WARN if severity == SEV_OK else severity
        self._set_status(text, severity)
        failure = _first_failure(results)
        if failure is not None:
            show_warning(u"Partition could not be set on element %s: %s. Select it in Revit "
                         u"and check that its Partition parameter is editable."
                         % (failure.name, failure.detail), title=TOOL_TITLE, owner=self)

    # ── RUN PLUMBING ─────────────────────────────────────────────────────────

    def _begin_run(self, phase, total):
        self._busy = True
        self._run_phase = phase
        self.begin_progress(maximum=max(1, total), disable=self._action_controls())

    def _end_run(self):
        self.end_progress()
        self._busy = False
        self._update_actions()

    def _step(self, index, total, label):
        """Progress callback for the Snippets batches; False = Stop pressed."""
        try:
            self.pb_run.Maximum = max(1, total)
        except Exception:
            pass
        message = u"%s — %s / %s" % (self._run_phase, _n(index), _n(total))
        if label:
            message += u" · %s" % label
        return self.step_progress(max(0, index - 1), message)

    # ── FOOTER ───────────────────────────────────────────────────────────────

    def _page_ids(self, for_isolate=False):
        """Element ids behind the ticked rows of the active page."""
        if self._page == PAGE_CREATE:
            return [row.id for row in self._ticked_hosts()]
        if self._page == PAGE_MANAGE:
            ids = []
            for row in self._ticked_asm_rows():
                ids.append(row.id)
                if for_isolate:
                    ids.extend(row.record.members)
            return ids
        return [row.id for row in self._part_rows if row.is_selected]

    def primary_button_clicked(self, sender, e):
        if self._busy:
            return
        if self._page == PAGE_CREATE:
            self._run_batch_create()
        elif self._page == PAGE_MANAGE:
            self._run_sync()
        else:
            self._run_partition()

    def rename_clicked(self, sender, e):
        if self._busy:
            return
        self._run_rename()

    def select_clicked(self, sender, e):
        if self._busy:
            return
        self._select_in_revit(self._page_ids())

    def isolate_clicked(self, sender, e):
        if self._busy:
            return
        self._isolate(self._page_ids(for_isolate=True))

    def _select_in_revit(self, ids):
        if not ids:
            self._set_status(E_TICK_0, SEV_WARN)
            return
        if self.uidoc is None:
            self._set_status(u"Cannot reach the Revit window to change the selection. "
                             u"Close this window and run the tool again.", SEV_BAD)
            return
        count = _assembly.select_in_revit(self.uidoc, ids)
        if count:
            self._set_status(u"Selected %s in Revit — close this window to see them."
                             % _plural(count, u"element"), SEV_OK)
        else:
            self._set_status(u"Revit refused the selection. Some elements may have been "
                             u"deleted — press Refresh and try again.", SEV_BAD)

    def _isolate(self, ids):
        if not ids:
            self._set_status(E_TICK_0, SEV_WARN)
            return
        view = None
        try:
            view = self.uidoc.ActiveView
        except Exception:
            view = None
        can_isolate = getattr(view, "CanUseTemporaryVisibilityModes", None)
        try:
            usable = view is not None and (can_isolate is None or bool(can_isolate()))
        except Exception:
            usable = False
        if not usable:
            show_warning(E_NOT_MODEL_VIEW, title=TOOL_TITLE, owner=self)
            return
        from Autodesk.Revit.DB import ElementId, Transaction
        unique = []
        for element_id in ids:
            if element_id not in unique:
                unique.append(element_id)
        try:
            with disposing(Transaction(self.doc, u"T3Lab: Isolate in view")) as txn:
                txn.Start()
                try:
                    view.IsolateElementsTemporary(
                        net_list(ElementId, [make_eid(i) for i in unique]))
                    txn.Commit()
                except Exception:
                    if not txn.HasEnded():
                        txn.RollBack()
                    raise
        except Exception as exc:
            self._set_status(u"Could not isolate in %s: %s. Open a model view and try again."
                             % (elem_name(view) or u"this view", short_error(exc)), SEV_BAD)
            return
        try:
            self.uidoc.RefreshActiveView()
        except Exception:
            pass
        self._set_status(u"Isolated %s in %s — close this window to see them; reset with "
                         u"Temporary Hide/Isolate." % (_plural(len(unique), u"element"),
                                                       elem_name(view) or u"the active view"),
                         SEV_OK)


# ── ENTRY POINT ──────────────────────────────────────────────────────────────

def _pick_into_selection(uidoc):
    """PickObjects on the clean Revit UI thread; the picks become the selection.

    Returns True when something was picked. Esc keeps the previous selection.
    """
    if uidoc is None:
        show_warning(u"Cannot reach the Revit window to pick elements. Select the hosts "
                     u"in the model first, then run Cast Unit Manager again.", title=TOOL_TITLE)
        return False
    try:
        from Autodesk.Revit.UI.Selection import ObjectType
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            u"Pick beams, columns, footings, rebar or assemblies, then press Finish")
    except Exception:
        return False        # Esc (OperationCanceledException) or no model view
    ids = [ref.ElementId for ref in refs] if refs else []
    if not ids:
        return False
    return bool(_assembly.select_in_revit(uidoc, ids))


def _open_sheet(doc, uidoc, sheet_id):
    """Open a sheet after the modal window closed (R12)."""
    sheet = None
    try:
        sheet = doc.GetElement(make_eid(sheet_id))
        uidoc.RequestViewChange(sheet)
        return
    except Exception:
        pass
    try:
        uidoc.ActiveView = sheet
    except Exception as exc:
        try:
            name = elem_name(sheet)
        except Exception:
            name = u"%s" % sheet_id
        show_warning(u"Could not open sheet %s: %s. Open it from the Project Browser."
                     % (name, short_error(exc)), title=TOOL_TITLE)


def show_cast_unit(doc, page=None, ids=None):
    """Open Cast Unit Manager.

    ``page``: "create" (default) | "manage" | "partition". ``ids``: element ids
    of reinforcement to preset as the Partition scope (Rebar Check hands its
    "No partition" rows over this way).
    """
    state = None
    while True:
        dlg = CastUnitDialog(doc, page=page, ids=ids, state=state)
        dlg.ShowDialog()
        action = dlg.action
        if not action:
            return
        if action[0] == "pick":
            _pick_into_selection(dlg.uidoc)
            state = dlg.export_state()
            page, ids = "create", None
            continue
        if action[0] == "open_sheet":
            _open_sheet(doc, dlg.uidoc, action[1])
        return
