# -*- coding: utf-8 -*-
"""
ManaGroupDialog.py
==================
WPF Dialog for managing Revit groups — model groups, detail groups and attached
detail groups — in four tabs:

* **Rename**  — batch rename group types with find/replace, prefix, suffix,
                letter case and an illegal-character cleanup, with a live preview
                of every new name before anything is written.
* **Workset** — move the instances of the selected group types onto one workset,
                optionally taking the elements inside each group with them.
* **Cleanup** — audit every group type (unused, single instance, mixed worksets,
                name problems) then purge unused types or ungroup instances.
* **Placement** — plot every placed instance on a plan, coloured by group type,
                filtered by kind (model / detail / attached) and by level or
                view, so "where are these groups?" has a visual answer. Click a
                marker to select that instance in Revit.

Revit API work lives in ``Snippets/_group_ops.py``; this module is UI only.

Part of T3Lab Extension.
Author: T3Lab
"""

import os

import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from System.Windows import Visibility, RoutedEventHandler
from System.Windows.Controls import Canvas, CheckBox, TextBlock
from System.Windows.Input import Cursors
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
from System.Windows.Shapes import Ellipse, Line
from System import Uri, UriKind

from pyrevit import revit

from GUI.WPF_Base import T3WPFWindow, to_items_source
from GUI.T3Dialog import confirm as t3_confirm, show_warning as t3_warning

from Snippets import _group_ops

GUI_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'ManaGroup.xaml')

TAB_RENAME = 0
TAB_WORKSET = 1
TAB_CLEANUP = 2
TAB_PLACE = 3

PRIMARY_LABELS = {
    TAB_RENAME: "Apply Rename",
    TAB_WORKSET: "Apply Workset",
    TAB_CLEANUP: "Rescan Model",
    TAB_PLACE: "Rescan Model",
}

# ── Placement tab ────────────────────────────────────────────────────────────
PLOT_ALL_LEVELS = "All levels and views"
PLOT_PALETTE_SIZE = 8          # T3.Plot.1 .. T3.Plot.8, then it wraps
PLOT_PADDING = 24.0            # px kept clear around the fitted extent
PLOT_MARKER_RADIUS = 4.0
PLOT_LABEL_SIZE = 10.0
PLOT_ZOOM_STEP = 1.2
PLOT_WALL_WIDTH = 1.0          # interior walls / core
PLOT_WALL_WIDTH_EXT = 1.8      # exterior walls = building outline

KIND_ALL = "All groups"
KIND_FILTERS = (
    (KIND_ALL, None),
    ("Model groups", _group_ops.KIND_MODEL),
    ("Detail groups", _group_ops.KIND_DETAIL),
    ("Attached detail groups", _group_ops.KIND_ATTACHED),
)


# ── ROW ITEMS ────────────────────────────────────────────────────────────────

class GroupRow(object):
    """One group type as shown in a grid. Three rows share one record."""

    def __init__(self, record, status_text="Ready", severity="Success",
                 is_selected=False, is_enabled=True):
        self.record = record
        self._new_name = record.name
        self._manual = False
        # Plain attribute, not a property: the amber DataTrigger in the XAML
        # binds straight to it, and GroupRow carries no INotifyPropertyChanged,
        # so it is only re-read when the grid is refreshed.
        self.dirty_NewName = False
        self._status_text = status_text
        self._severity = severity
        self._is_selected = bool(is_selected)
        self._is_enabled = bool(is_enabled)

    # -- read-only columns ---------------------------------------------------

    @property
    def GroupName(self):
        return self.record.name

    @property
    def Kind(self):
        return self.record.kind

    @property
    def InstanceCount(self):
        return str(self.record.instance_count)

    @property
    def MemberCount(self):
        return str(self.record.member_count) if self.record.member_count else "—"

    @property
    def WorksetSummary(self):
        return self.record.workset_summary

    @property
    def Issues(self):
        found = self.record.audit_issues()
        return ", ".join(found) if found else "None"

    # -- editable / stateful -------------------------------------------------

    @property
    def NewName(self):
        return self._new_name

    @NewName.setter
    def NewName(self, value):
        self._new_name = value or ""

    @property
    def StatusText(self):
        return self._status_text

    @StatusText.setter
    def StatusText(self, value):
        self._status_text = value

    @property
    def Severity(self):
        return self._severity

    @Severity.setter
    def Severity(self, value):
        self._severity = value

    @property
    def IsSelected(self):
        return self._is_selected

    @IsSelected.setter
    def IsSelected(self, value):
        self._is_selected = bool(value)

    @property
    def IsEnabled(self):
        return self._is_enabled

    @IsEnabled.setter
    def IsEnabled(self, value):
        self._is_enabled = bool(value)

    def set_status(self, text, severity="Success"):
        self._status_text = text
        self._severity = severity


class PlotLegendRow(object):
    """One row of the Placement legend: a group type, its colour and its count."""

    def __init__(self, type_id, type_name, kind, count, swatch, is_visible=True):
        self.type_id = type_id
        self.count = count
        self._type_name = type_name
        self._kind = kind
        self._swatch = swatch
        self._is_visible = is_visible

    @property
    def TypeName(self):
        return self._type_name

    @property
    def KindLabel(self):
        return self._kind

    @property
    def Swatch(self):
        return self._swatch

    @property
    def CountLabel(self):
        return str(self.count)

    @property
    def Tooltip(self):
        return "%s — %s group, %d placed instance%s" % (
            self._type_name, self._kind.lower(), self.count,
            "" if self.count == 1 else "s")

    @property
    def IsVisible(self):
        return self._is_visible

    @IsVisible.setter
    def IsVisible(self, value):
        self._is_visible = bool(value)


class ManaGroupDialog(T3WPFWindow):
    """Main window class for Group Manager."""

    def __init__(self, doc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc or getattr(revit, 'doc', None)
        self._loading = True
        self._is_busy = False
        self._active_tab = TAB_RENAME

        self._records = []
        self._ren_rows = []
        self._ws_rows = []
        self._cln_rows = []
        self._worksets = []          # [(id_int, name)]

        # Placement tab
        self._placements = []           # [GroupPlacement] for the whole model
        self._plot_levels = []          # level/view labels behind cb_plot_level
        self._plot_legend_rows = []
        self._plot_hidden = set()       # type_ids the user unticked in the legend
        self._plot_colour_by_type = {}
        self._plot_transform = None     # None = refit on the next draw
        self._plot_markers = {}         # instance_id -> GroupPlacement drawn
        self._plot_unlocated = 0
        self._plot_dragging = False
        self._plot_drag_from = None
        self._plot_marker_stroke = self._brush("T3.Surface")
        self._plot_label_brush = self._brush("T3.TextSecondary")
        self._plot_wall_brush = self._brush("T3.BorderStrong")
        self._plot_wall_brush_ext = self._brush("T3.TextSecondary")
        self._outline_cache = {}        # level name -> ([OutlineSegment], truncated)
        self._outline_truncated = False

        self._load_logo()
        self._wire_row_events()
        self._init_filters()
        self._reload_model()
        self._loading = False

    # ── SETUP ────────────────────────────────────────────────────────────────

    def _wire_row_events(self):
        """Attach the per-row handlers the XAML cannot attach for itself.

        An event handler written inside a <DataTemplate> is silently never
        wired: the template owns its own namescope, so the loader's FindName
        cannot reach the control, and the binding is dropped. Ticking a row
        therefore left the "N checked" counter, the header tri-state and the
        rename preview all frozen.

        Click and CellEditEnding both bubble, so one handler on the grid catches
        every row beneath it — and unlike a template handler, this one is real.
        """
        for grid, handler in ((self.grid_rename, self.rename_checkbox_clicked),
                              (self.grid_workset, self.workset_checkbox_clicked),
                              (self.grid_cleanup, self.cleanup_checkbox_clicked)):
            try:
                grid.AddHandler(CheckBox.ClickEvent, RoutedEventHandler(handler), True)
            except Exception:
                pass
        try:
            self.list_plot_legend.AddHandler(
                CheckBox.ClickEvent, RoutedEventHandler(self.plot_legend_toggled), True)
        except Exception:
            pass
        try:
            self.grid_rename.CellEditEnding += self.new_name_cell_edited
        except Exception:
            pass

    def _brush(self, key):
        """A brush from the T3 stylesheet, or None if the key is missing."""
        try:
            return self.FindResource(key)
        except Exception:
            return None

    def _load_logo(self):
        """Load and bind the T3Lab logo to title bar and window icon."""
        try:
            logo_path = os.path.join(GUI_DIR, 'T3Lab_logo.png')
            if os.path.exists(logo_path):
                bitmap = BitmapImage()
                bitmap.BeginInit()
                bitmap.CacheOption = BitmapCacheOption.OnLoad
                bitmap.UriSource = Uri(logo_path, UriKind.Absolute)
                bitmap.EndInit()
                bitmap.Freeze()
                if hasattr(self, 'logo_image') and self.logo_image:
                    self.logo_image.Source = bitmap
                self.Icon = bitmap
        except Exception:
            pass

    def _init_filters(self):
        """Fill the kind filter and the letter-case dropdown."""
        self.cb_kind.ItemsSource = to_items_source([label for label, _ in KIND_FILTERS])
        self.cb_kind.SelectedIndex = 0

        self.cb_case.ItemsSource = to_items_source(list(_group_ops.CASE_MODES))
        self.cb_case.SelectedIndex = 0

    # ── MODEL LOADING ────────────────────────────────────────────────────────

    def _reload_model(self):
        """Re-read every group type in the document and rebuild all three tabs."""
        self._records = _group_ops.collect_group_types(self.doc)

        # The Rename tab opens with everything checked so the preview column is
        # meaningful straight away; the other two tabs act on the model, so they
        # start empty and the user opts in.
        self._ren_rows = [GroupRow(r, is_selected=True) for r in self._records]
        self._ws_rows = [GroupRow(r, is_enabled=bool(r.instance_count))
                         for r in self._records]
        self._cln_rows = [GroupRow(r) for r in self._records]

        for row in self._ws_rows:
            if not row.record.instance_count:
                row.set_status("Not placed", "Warning")
        for row in self._cln_rows:
            found = row.record.audit_issues()
            if not found:
                row.set_status("Clean", "Success")
            elif "Unused" in found:
                row.set_status("Unused", "Warning")
            else:
                row.set_status("%d finding%s" % (len(found), "" if len(found) == 1 else "s"),
                               "Warning")

        self._load_worksets()
        self._load_placements()
        self._recompute_names()
        self._apply_filter()
        self._rebuild_plot_levels()
        self._rebuild_plot_legend()
        self._plot_transform = None
        self._draw_plan()
        for checkbox, rows in ((self.chk_ren_header, self._ren_rows),
                               (self.chk_ws_header, self._ws_rows),
                               (self.chk_cln_header, self._cln_rows)):
            self._sync_header_checkbox(checkbox, self._visible(rows))
        self._set_status("%d group type%s in this model — %d model, %d detail, %d attached."
                         % (len(self._records),
                            "" if len(self._records) == 1 else "s",
                            self._count_kind(_group_ops.KIND_MODEL),
                            self._count_kind(_group_ops.KIND_DETAIL),
                            self._count_kind(_group_ops.KIND_ATTACHED)))

    def _count_kind(self, kind):
        return sum(1 for r in self._records if r.kind == kind)

    def _load_worksets(self):
        """Fill the target workset dropdown, or explain why it is unavailable."""
        self._worksets = _group_ops.user_worksets(self.doc)
        usable = bool(self._worksets)

        self.cb_workset.ItemsSource = to_items_source([name for _, name in self._worksets])
        if usable:
            self.cb_workset.SelectedIndex = 0
        self.cb_workset.IsEnabled = usable
        self.chk_members.IsEnabled = usable
        self.banner_no_worksharing.Visibility = \
            Visibility.Collapsed if usable else Visibility.Visible

        if not usable:
            workshared = False
            try:
                workshared = bool(self.doc and self.doc.IsWorkshared)
            except Exception:
                pass
            self.lbl_no_worksharing.Text = (
                "No user workset was found in this model, so groups cannot be moved. "
                "Create a workset in Revit first — renaming and cleanup still work."
                if workshared else
                "This model is not workshared, so groups cannot be moved between worksets. "
                "Enable worksharing in Revit first — renaming and cleanup still work.")

    # ── FILTERING ────────────────────────────────────────────────────────────

    def _search_text(self):
        try:
            return (self.tb_search.Text or "").strip().lower()
        except Exception:
            return ""

    def _kind_filter(self):
        index = self.cb_kind.SelectedIndex
        if index is None or index < 0 or index >= len(KIND_FILTERS):
            return None
        return KIND_FILTERS[index][1]

    def _visible(self, rows):
        needle = self._search_text()
        kind = self._kind_filter()
        out = []
        for row in rows:
            if kind and row.record.kind != kind:
                continue
            if needle and needle not in row.record.name.lower():
                continue
            out.append(row)
        return out

    def _tabs(self):
        """(rows, grid, empty state, count label) for each of the three tabs."""
        return (
            (self._ren_rows, self.grid_rename, self.txt_ren_empty, self.lbl_ren_count),
            (self._ws_rows, self.grid_workset, self.txt_ws_empty, self.lbl_ws_count),
            (self._cln_rows, self.grid_cleanup, self.txt_cln_empty, self.lbl_cln_count))

    def _apply_filter(self):
        """Push the filtered rows into all three grids and update the counters.

        Reassigns ItemsSource, so it only runs on load or when the filter really
        changes — doing it on every checkbox click would reset the scroll
        position and drop the focus out of the NEW NAME box.
        """
        for rows, grid, empty, _label in self._tabs():
            shown = self._visible(rows)
            grid.ItemsSource = to_items_source(shown)
            empty.Visibility = Visibility.Collapsed if shown else Visibility.Visible
        self._update_counts()

    def _update_counts(self):
        """Refresh the "N group types · M checked" caption above each grid."""
        for rows, _grid, _empty, label in self._tabs():
            shown = self._visible(rows)
            checked = sum(1 for r in shown if r.IsSelected)
            label.Text = "%d group type%s · %d checked" % (
                len(shown), "" if len(shown) == 1 else "s", checked)

    def _refresh_grids(self):
        """Redraw every grid — the rows carry no INotifyPropertyChanged."""
        self.grid_rename.Items.Refresh()
        self.grid_workset.Items.Refresh()
        self.grid_cleanup.Items.Refresh()

    def _refresh_current_grid(self):
        """Redraw only the grid the user is looking at."""
        if self._active_tab == TAB_PLACE:
            return                      # the Placement tab has no grid
        if self._active_tab == TAB_WORKSET:
            self.grid_workset.Items.Refresh()
        elif self._active_tab == TAB_CLEANUP:
            self.grid_cleanup.Items.Refresh()
        else:
            self.grid_rename.Items.Refresh()

    def _current_rows(self):
        """The row list belonging to the active tab."""
        if self._active_tab == TAB_PLACE:
            return []                   # the Placement tab has no row list
        if self._active_tab == TAB_WORKSET:
            return self._ws_rows
        if self._active_tab == TAB_CLEANUP:
            return self._cln_rows
        return self._ren_rows

    def _checked(self, rows):
        """Checked rows that are visible under the current filter and enabled."""
        return [r for r in self._visible(rows) if r.IsSelected and r.IsEnabled]

    # ── SHARED HELPERS ───────────────────────────────────────────────────────

    def _set_status(self, text):
        self.status_text.Text = text

    def _begin_busy(self, phase):
        self._is_busy = True
        self.lbl_phase.Text = phase
        self.lbl_current_item.Text = ""
        self.progress_bar.Value = 0
        self.pnl_progress.Visibility = Visibility.Visible
        self.btn_primary.IsEnabled = False
        self.btn_cancel.IsEnabled = False
        self._do_events()

    def _step_busy(self, phase, index, total, label):
        self.lbl_phase.Text = "%s — %d / %d" % (phase, index, total)
        self.lbl_current_item.Text = label or ""
        self.progress_bar.Value = int((float(index - 1) / max(1, total)) * 100)
        self._do_events()

    def _end_busy(self, summary):
        self._is_busy = False
        self.progress_bar.Value = 100
        self.btn_primary.IsEnabled = True
        self.btn_cancel.IsEnabled = True
        self._set_status(summary)
        self._do_events()

    @staticmethod
    def _sync_header_checkbox(checkbox, rows):
        """Tri-state the header checkbox from the rows below it."""
        if checkbox is None:
            return
        selectable = [r for r in rows if r.IsEnabled]
        checked = sum(1 for r in selectable if r.IsSelected)
        if not selectable or checked == 0:
            checkbox.IsChecked = False
        elif checked == len(selectable):
            checkbox.IsChecked = True
        else:
            checkbox.IsChecked = None

    def _header_for(self, tab=None):
        tab = self._active_tab if tab is None else tab
        if tab == TAB_PLACE:
            return None                 # the Placement tab has no header checkbox
        if tab == TAB_WORKSET:
            return self.chk_ws_header
        if tab == TAB_CLEANUP:
            return self.chk_cln_header
        return self.chk_ren_header

    def _after_selection_change(self):
        self._sync_header_checkbox(self._header_for(), self._visible(self._current_rows()))
        if self._active_tab == TAB_RENAME:
            self._recompute_names()
        self._update_counts()
        self._refresh_current_grid()

    # ── TAB 1: RENAME ────────────────────────────────────────────────────────

    def _rule_values(self):
        return dict(
            find=(self.tb_find.Text or ""),
            replace=(self.tb_replace.Text or ""),
            match_case=bool(self.chk_match_case.IsChecked),
            prefix=(self.tb_prefix.Text or ""),
            suffix=(self.tb_suffix.Text or ""),
            case_mode=self._case_mode(),
            cleanup=bool(self.chk_cleanup.IsChecked))

    def _case_mode(self):
        index = self.cb_case.SelectedIndex
        if index is None or index < 0 or index >= len(_group_ops.CASE_MODES):
            return _group_ops.CASE_KEEP
        return _group_ops.CASE_MODES[index]

    def _recompute_names(self):
        """Re-apply the rename rules and re-validate every proposed name."""
        rules = self._rule_values()

        for row in self._ren_rows:
            if not row._manual:
                row.NewName = _group_ops.build_new_name(row.record.name, **rules)

        # Every name that will exist after the rename, to catch collisions.
        planned = {}
        for row in self._ren_rows:
            final = (row.NewName if row.IsSelected else row.record.name) or ""
            key = final.strip().lower()
            planned[key] = planned.get(key, 0) + 1

        for row in self._ren_rows:
            wanted = (row.NewName or "").strip()
            # Cleared first: only the rows that survive every check below are
            # actually going to be written, and those are the ones worth amber.
            row.dirty_NewName = False
            if not row.IsSelected:
                row.set_status("Not selected", "Warning")
                continue
            if not wanted:
                row.set_status("Empty name", "Danger")
                continue
            bad = _group_ops.illegal_chars_in(wanted)
            if bad:
                row.set_status("Illegal %s" % " ".join(bad), "Danger")
                continue
            if wanted == row.record.name:
                row.set_status("Unchanged", "Success")
                continue
            if planned.get(wanted.lower(), 0) > 1:
                row.set_status("Duplicate name", "Danger")
                continue
            row.set_status("Will rename", "Success")
            row.dirty_NewName = True

    def _apply_rename(self):
        """Rename every checked group type whose proposed name is valid."""
        rows = self._checked(self._ren_rows)
        if not rows:
            self._set_status("Check at least one group type on the Rename tab first.")
            return

        blocked = [r for r in rows if r.Severity == "Danger"]
        pairs = [(r.record, (r.NewName or "").strip())
                 for r in rows
                 if r.Severity != "Danger" and (r.NewName or "").strip() != r.record.name]
        if not pairs:
            if blocked:
                self._set_status(
                    "Nothing to rename — %d checked name%s still has a problem to fix "
                    "(see the STATUS column)." % (
                        len(blocked), "" if len(blocked) == 1 else "s"))
            else:
                self._set_status(
                    "Nothing to rename — the %d checked group type%s already carry these names."
                    % (len(rows), "" if len(rows) == 1 else "s"))
            return

        if not t3_confirm(
                "Rename %d group type%s in this model?" % (
                    len(pairs), "" if len(pairs) == 1 else "s"),
                title="Rename groups",
                ok_text="Rename",
                details="Every placed instance keeps its geometry — only the type name changes.",
                owner=self):
            return

        by_record = {}
        for row in self._ren_rows:
            by_record[row.record.type_id] = row

        self._begin_busy("Renaming groups")
        try:
            results = _group_ops.rename_group_types(
                self.doc, pairs,
                progress=lambda i, t, label: self._step_busy("Renaming groups", i, t, label))
        except Exception as exc:
            self._end_busy("Rename failed — review the error before retrying.")
            t3_warning("The rename did not complete successfully. Resolve any Revit failure "
                       "dialog and check the model before retrying.",
                       title="Rename failed", details=str(exc), owner=self)
            return

        renamed = failed = 0
        for record, ok, message in results:
            row = by_record.get(record.type_id)
            if ok:
                renamed += 1
                if row is not None:
                    row._manual = False
                    row.NewName = record.name
                    row.set_status("Renamed", "Success")
            else:
                failed += 1
                if row is not None:
                    row.set_status(message, "Danger")

        self._apply_filter()
        self._refresh_grids()
        self._end_busy("Renamed %d group type%s, %d failed." % (
            renamed, "" if renamed == 1 else "s", failed))

    # ── TAB 2: WORKSET ───────────────────────────────────────────────────────

    def _apply_workset(self):
        """Move the instances of every checked group type onto the target workset."""
        if not self._worksets:
            self._set_status("This model is not workshared — there is no workset to move to.")
            return

        rows = self._checked(self._ws_rows)
        if not rows:
            self._set_status("Check at least one placed group type on the Workset tab first.")
            return

        index = self.cb_workset.SelectedIndex
        if index is None or index < 0 or index >= len(self._worksets):
            self._set_status("Pick the workset the groups should move to.")
            return
        workset_value, workset_name = self._worksets[index]

        include_members = bool(self.chk_members.IsChecked)
        instances = sum(r.record.instance_count for r in rows)
        members = sum(r.record.instance_count * r.record.member_count
                      for r in rows) if include_members else 0

        detail = "%d group instance%s%s will move to \"%s\"." % (
            instances, "" if instances == 1 else "s",
            " and about %d member element%s" % (members, "" if members == 1 else "s")
            if include_members else "",
            workset_name)
        if not t3_confirm(detail,
                          title="Move groups to another workset",
                          ok_text="Move them",
                          owner=self):
            return

        by_record = {row.record.type_id: row for row in self._ws_rows}

        self._begin_busy("Setting worksets")
        try:
            results = _group_ops.apply_workset(
                self.doc, [r.record for r in rows], workset_value,
                include_members=include_members,
                progress=lambda i, t, label: self._step_busy("Setting worksets", i, t, label))
        except Exception as exc:
            self._end_busy("Workset change failed — review the error before retrying.")
            t3_warning("The workset change did not complete successfully. Resolve any Revit "
                       "failure dialog and check the model before retrying.",
                       title="Workset change failed", details=str(exc), owner=self)
            return

        moved_total = failed_total = 0
        for record, moved, _skipped, failed, message in results:
            moved_total += moved
            failed_total += failed
            row = by_record.get(record.type_id)
            if row is not None:
                row.set_status(message, "Danger" if failed else "Success")

        self._refresh_worksets()
        self._end_busy("Moved %d element%s to \"%s\", %d failed." % (
            moved_total, "" if moved_total == 1 else "s", workset_name, failed_total))

    def _refresh_worksets(self):
        """Re-read the workset of every instance so the grid shows the new state."""
        for record in self._records:
            seen = []
            for instance in record.instances:
                name = _group_ops.instance_workset_name(self.doc, instance)
                if name and name not in seen:
                    seen.append(name)
            record.workset_names = seen
        self._apply_filter()
        self._refresh_grids()

    # ── TAB 3: CLEANUP ───────────────────────────────────────────────────────

    def _purge_selected(self):
        """Delete the checked group types that are no longer placed."""
        rows = self._checked(self._cln_rows)
        if not rows:
            self._set_status("Check at least one group type on the Cleanup tab first.")
            return

        unused = [r for r in rows if r.record.instance_count == 0]
        placed = len(rows) - len(unused)
        if not unused:
            self._set_status(
                "Nothing to purge — all %d checked group type%s are still placed in the model."
                % (len(rows), "" if len(rows) == 1 else "s"))
            return

        detail = "%d unused group type%s will be deleted from the project browser." % (
            len(unused), "" if len(unused) == 1 else "s")
        if placed:
            detail += " %d still-placed type%s stay untouched." % (
                placed, "" if placed == 1 else "s")
        if not t3_confirm(detail,
                          title="Purge unused group types",
                          ok_text="Purge",
                          danger=True,
                          owner=self):
            return

        self._begin_busy("Purging group types")
        try:
            results = _group_ops.purge_group_types(
                self.doc, [r.record for r in unused],
                progress=lambda i, t, label: self._step_busy("Purging group types", i, t, label))
        except Exception as exc:
            self._end_busy("Purge failed — review the error before retrying.")
            t3_warning("The purge did not complete successfully. Resolve any Revit failure "
                       "dialog and check the model before retrying.",
                       title="Purge failed", details=str(exc), owner=self)
            return

        purged = sum(1 for _r, ok, _m in results if ok)
        failed = len(results) - purged
        self._reload_model()
        self._end_busy("Purged %d group type%s, %d failed." % (
            purged, "" if purged == 1 else "s", failed))

    def _ungroup_selected(self):
        """Explode every instance of the checked group types."""
        rows = self._checked(self._cln_rows)
        if not rows:
            self._set_status("Check at least one group type on the Cleanup tab first.")
            return

        placed = [r for r in rows if r.record.instance_count]
        if not placed:
            self._set_status("Nothing to ungroup — none of the checked group types is placed.")
            return

        instances = sum(r.record.instance_count for r in placed)
        if not t3_confirm(
                "Ungroup %d instance%s of %d group type%s?" % (
                    instances, "" if instances == 1 else "s",
                    len(placed), "" if len(placed) == 1 else "s"),
                title="Ungroup instances",
                ok_text="Ungroup",
                danger=True,
                details="Members stay in the model as loose elements. "
                        "Ctrl+Z undoes the whole ungroup in one step.",
                owner=self):
            return

        self._begin_busy("Ungrouping")
        try:
            results = _group_ops.ungroup_instances(
                self.doc, [r.record for r in placed],
                progress=lambda i, t, label: self._step_busy("Ungrouping", i, t, label))
        except Exception as exc:
            self._end_busy("Ungroup failed — review the error before retrying.")
            t3_warning("The ungroup did not complete successfully. Resolve any Revit failure "
                       "dialog and check the model before retrying.",
                       title="Ungroup failed", details=str(exc), owner=self)
            return

        ungrouped = sum(count for _r, count, _f, _m in results)
        failed = sum(f for _r, _c, f, _m in results)
        self._reload_model()
        self._end_busy("Ungrouped %d instance%s, %d failed." % (
            ungrouped, "" if ungrouped == 1 else "s", failed))

    # ── EVENT HANDLERS: WINDOW & TABS ────────────────────────────────────────

    def cancel_button_clicked(self, sender, e):
        if self._is_busy:
            return
        self.Close()

    def tab_chip_checked(self, sender, e):
        if getattr(self, '_loading', True):
            return
        try:
            self._active_tab = int(sender.Tag)
        except Exception:
            self._active_tab = TAB_RENAME
        self.tab_control.SelectedIndex = self._active_tab
        self.btn_primary.Content = PRIMARY_LABELS.get(self._active_tab, "Apply")

        # The Placement tab carries its own kind chips, so the global "Show"
        # dropdown would be a second control for the same idea, set to a
        # different value. Hide it there rather than let the two disagree.
        on_plan = self._active_tab == TAB_PLACE
        hidden = Visibility.Collapsed if on_plan else Visibility.Visible
        self.lbl_kind.Visibility = hidden
        self.cb_kind.Visibility = hidden

        if on_plan:
            self._refresh_plot(refit=False)
        else:
            self._sync_header_checkbox(self._header_for(),
                                       self._visible(self._current_rows()))

    def primary_button_clicked(self, sender, e):
        if self._is_busy:
            return
        if self._active_tab == TAB_RENAME:
            self._apply_rename()
        elif self._active_tab == TAB_WORKSET:
            self._apply_workset()
        else:
            self._reload_model()

    def refresh_clicked(self, sender, e):
        if self._is_busy:
            return
        self._reload_model()

    # ── EVENT HANDLERS: FILTERS ──────────────────────────────────────────────

    def search_text_changed(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._apply_filter()
        # The search box sits in the shared toolbar, so it narrows the plan's
        # legend too — otherwise typing a name would visibly do nothing here.
        if self._active_tab == TAB_PLACE:
            self._refresh_plot(refit=False)

    def kind_filter_changed(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._apply_filter()

    # ── EVENT HANDLERS: SELECTION ────────────────────────────────────────────

    def select_all_clicked(self, sender, e):
        for row in self._visible(self._current_rows()):
            if row.IsEnabled:
                row.IsSelected = True
        self._after_selection_change()

    def select_none_clicked(self, sender, e):
        for row in self._visible(self._current_rows()):
            row.IsSelected = False
        self._after_selection_change()

    def invert_selection_clicked(self, sender, e):
        for row in self._visible(self._current_rows()):
            if row.IsEnabled:
                row.IsSelected = not row.IsSelected
        self._after_selection_change()

    def select_unused_clicked(self, sender, e):
        for row in self._visible(self._cln_rows):
            row.IsSelected = row.record.instance_count == 0
        self._after_selection_change()

    def select_issues_clicked(self, sender, e):
        for row in self._visible(self._cln_rows):
            row.IsSelected = bool(row.record.audit_issues())
        self._after_selection_change()

    def rename_header_clicked(self, sender, e):
        self._header_clicked(sender, self._ren_rows)

    def workset_header_clicked(self, sender, e):
        self._header_clicked(sender, self._ws_rows)

    def cleanup_header_clicked(self, sender, e):
        self._header_clicked(sender, self._cln_rows)

    def _header_clicked(self, sender, rows):
        wanted = bool(sender.IsChecked)
        for row in self._visible(rows):
            if row.IsEnabled:
                row.IsSelected = wanted
        self._after_selection_change()

    def rename_checkbox_clicked(self, sender, e):
        self._after_selection_change()

    def workset_checkbox_clicked(self, sender, e):
        self._after_selection_change()

    def cleanup_checkbox_clicked(self, sender, e):
        self._after_selection_change()

    # ── EVENT HANDLERS: RENAME RULES ─────────────────────────────────────────

    def rule_changed(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._recompute_names()
        self.grid_rename.Items.Refresh()

    def rule_toggled(self, sender, e):
        self.rule_changed(sender, e)

    def new_name_cell_edited(self, sender, e):
        """A NEW NAME cell was committed — it wins over the rename rules.

        Raised by the grid itself, so unlike the old TextChanged handler buried
        in a DataTemplate it actually fires. The edit is staged only: the row
        goes amber and Apply Rename is what writes it to the model.
        """
        if getattr(self, '_loading', True):
            return
        try:
            from System.Windows.Controls import DataGridEditAction
            if e.EditAction == DataGridEditAction.Cancel:
                return
        except Exception:
            pass

        row = getattr(e, 'Row', None)
        row = getattr(row, 'Item', None)
        if not isinstance(row, GroupRow):
            return
        try:
            typed = e.EditingElement.Text
        except Exception:
            return

        row._manual = (typed or "").strip() != row.record.name
        row.NewName = typed
        self._recompute_names()
        self._update_counts()
        self._refresh_rename_later()

    def _refresh_rename_later(self):
        """Repaint the Rename grid once the cell has finished committing.

        GroupRow carries no INotifyPropertyChanged, so the amber DataTrigger
        only re-reads `dirty_NewName` on a refresh — and refreshing while the
        cell is still committing throws "not allowed during an EditItem
        transaction".
        """
        try:
            from System.Windows.Threading import DispatcherPriority
            from System import Action
            self.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                Action(lambda: self.grid_rename.Items.Refresh()))
        except Exception:
            try:
                self.grid_rename.Items.Refresh()
            except Exception:
                pass

    def reset_names_clicked(self, sender, e):
        for row in self._ren_rows:
            row._manual = False
        self._recompute_names()
        self.grid_rename.Items.Refresh()
        self._set_status("New names reset to the current rename rules.")

    # ── EVENT HANDLERS: WORKSET & CLEANUP ────────────────────────────────────

    def members_toggled(self, sender, e):
        if getattr(self, '_loading', True):
            return
        if self.chk_members.IsChecked:
            self.lbl_ws_hint.Text = ("Group instances and every element inside them move to "
                                     "the chosen workset.")
        else:
            self.lbl_ws_hint.Text = ("Group instances move to the chosen workset. Members keep "
                                     "their own workset unless the option above is ticked.")

    def ungroup_clicked(self, sender, e):
        if self._is_busy:
            return
        self._ungroup_selected()

    def purge_clicked(self, sender, e):
        if self._is_busy:
            return
        self._purge_selected()

    # ── TAB 4: PLACEMENT ─────────────────────────────────────────────────────
    # The plan is drawn by hand onto a Canvas rather than bound: a few thousand
    # markers as bound ContentPresenters is slow to lay out, and every redraw
    # (zoom, pan, filter) would rebuild the whole visual tree.

    def _plot_kind(self):
        """Kind selected by the Placement chips, or None for all kinds."""
        for chip in (self.chip_plot_model, self.chip_plot_detail,
                     self.chip_plot_attached, self.chip_plot_all):
            if chip.IsChecked:
                return str(chip.Tag) or None
        return _group_ops.KIND_MODEL

    def _plot_level(self):
        """Level/view chosen in the Placement filter, or None for every level."""
        index = self.cb_plot_level.SelectedIndex
        if index is None or index <= 0 or index > len(self._plot_levels):
            return None
        return self._plot_levels[index - 1]

    def _load_placements(self):
        """Read every group instance's position once per model reload."""
        self._outline_cache = {}        # the model may have changed under us
        try:
            self._placements = _group_ops.collect_placements(self.doc, self._records)
        except Exception:
            self._placements = []
        placed = len(self._placements)
        total = _group_ops.placed_instance_total(self._records)
        self._plot_unlocated = max(0, total - placed)

    def _rebuild_plot_levels(self):
        """Refill the level filter, keeping the current choice when it survives."""
        previous = None
        try:
            if self.cb_plot_level.SelectedIndex > 0:
                previous = self._plot_levels[self.cb_plot_level.SelectedIndex - 1]
        except Exception:
            previous = None

        kind = self._plot_kind()
        in_kind = _group_ops.filter_placements(self._placements, kind=kind)
        self._plot_levels = _group_ops.level_names_of(in_kind)

        labels = [PLOT_ALL_LEVELS] + list(self._plot_levels)
        self.cb_plot_level.ItemsSource = to_items_source(labels)
        if previous and previous in self._plot_levels:
            self.cb_plot_level.SelectedIndex = self._plot_levels.index(previous) + 1
        else:
            self.cb_plot_level.SelectedIndex = 0
        self._update_context_availability()

    def _rebuild_plot_legend(self):
        """One legend row per group type of the chosen kind, with its colour."""
        kind = self._plot_kind()
        needle = self._search_text()
        in_scope = _group_ops.filter_placements(self._placements, kind=kind,
                                                level=self._plot_level())
        counts = _group_ops.count_by_type(in_scope)

        previous_hidden = set(self._plot_hidden)
        rows = []
        index = 0
        for record in self._records:
            if kind and record.kind != kind:
                continue
            if needle and needle not in record.name.lower():
                continue
            count = counts.get(record.type_id, 0)
            rows.append(PlotLegendRow(
                type_id=record.type_id,
                type_name=record.name,
                kind=record.kind,
                count=count,
                swatch=self._plot_brush(index),
                is_visible=record.type_id not in previous_hidden))
            index += 1

        self._plot_legend_rows = rows
        self._plot_colour_by_type = dict(
            (row.type_id, row.Swatch) for row in rows)
        self.list_plot_legend.ItemsSource = to_items_source(rows)
        self.txt_plot_legend_empty.Visibility = (
            Visibility.Collapsed if rows else Visibility.Visible)
        self._update_plot_legend_label()

    def _update_plot_legend_label(self):
        """Refresh the "N group types · M plotted" caption above the legend."""
        rows = self._plot_legend_rows
        plotted = sum(r.count for r in rows if r.IsVisible)
        self.lbl_plot_legend.Text = "%d group type%s · %d plotted" % (
            len(rows), "" if len(rows) == 1 else "s", plotted)

    def _plot_brush(self, index):
        """Categorical brush `index`, wrapping round the palette."""
        key = "T3.Plot.%d" % ((index % PLOT_PALETTE_SIZE) + 1)
        try:
            return self.FindResource(key)
        except Exception:
            return None

    def _current_outline(self):
        """Wall segments for the chosen level, or [] when there is nothing to draw.

        Only drawn for ONE level: overlaying every storey of a tower turns the
        plan into a grey smear, which is worse than no context at all.
        """
        if not self.chk_plot_context.IsChecked:
            self._outline_truncated = False
            return []
        level = self._plot_level()
        if not level:
            self._outline_truncated = False
            return []
        if level not in self._outline_cache:
            try:
                self._outline_cache[level] = _group_ops.collect_level_outline(
                    self.doc, level)
            except Exception:
                self._outline_cache[level] = ([], False)
        segments, truncated = self._outline_cache[level]
        self._outline_truncated = truncated
        return segments

    def _visible_placements(self):
        """Placements that pass every Placement-tab filter."""
        shown_ids = set(row.type_id for row in self._plot_legend_rows if row.IsVisible)
        return _group_ops.filter_placements(
            self._placements,
            kind=self._plot_kind(),
            level=self._plot_level(),
            type_ids=shown_ids)

    def _refresh_plot(self, refit=True):
        """Rebuild legend + level list, then redraw the plan."""
        if getattr(self, '_loading', True):
            return
        self._rebuild_plot_legend()
        if refit:
            self._plot_transform = None
        self._draw_plan()

    def _draw_plan(self):
        """Paint the markers. Cheap enough to call on every zoom and pan tick."""
        canvas = self.plan_canvas
        canvas.Children.Clear()
        self._plot_markers = {}

        placements = self._visible_placements()
        outline = self._current_outline()
        width = float(canvas.ActualWidth or 0)
        height = float(canvas.ActualHeight or 0)
        if width < 2 or height < 2:
            return                      # not laid out yet; SizeChanged calls back

        marker_extent = _group_ops.placements_extent(placements)
        self.txt_plot_empty.Visibility = (
            Visibility.Collapsed if (placements or outline) else Visibility.Visible)
        if not placements and not outline:
            self.lbl_plot_info.Text = self._plot_info_text(0, None)
            return

        if self._plot_transform is None:
            # Fit to markers AND context together: fitting to the markers alone
            # would push the building outline off screen the moment a level has
            # its groups clustered in one corner.
            self._plot_transform = _group_ops.PlanTransform.fit(
                _group_ops.union_extent(marker_extent,
                                        _group_ops.outline_extent(outline)),
                width, height, padding=PLOT_PADDING)
        transform = self._plot_transform

        self._draw_outline(canvas, outline, transform, width, height)

        show_labels = bool(self.chk_plot_labels.IsChecked)
        radius = PLOT_MARKER_RADIUS
        for placement in placements:
            px, py = transform.to_canvas(placement.x, placement.y)
            if px < -radius or py < -radius or px > width + radius or py > height + radius:
                continue                # off screen: skip the visual entirely
            brush = self._plot_colour_by_type.get(placement.type_id)
            dot = Ellipse()
            dot.Width = radius * 2
            dot.Height = radius * 2
            if brush is not None:
                dot.Fill = brush
            dot.Stroke = self._plot_marker_stroke
            dot.StrokeThickness = 1.0
            dot.Cursor = Cursors.Hand
            dot.ToolTip = "%s\n%s · %s%s" % (
                placement.type_name, placement.kind, placement.location_label,
                ("\nWorkset: " + placement.workset_name) if placement.workset_name else "")
            dot.Tag = placement
            Canvas.SetLeft(dot, px - radius)
            Canvas.SetTop(dot, py - radius)
            dot.MouseLeftButtonUp += self.plan_marker_clicked
            canvas.Children.Add(dot)
            self._plot_markers[placement.instance_id] = placement

            if show_labels:
                label = TextBlock()
                label.Text = placement.type_name
                label.FontSize = PLOT_LABEL_SIZE
                label.Foreground = self._plot_label_brush
                label.IsHitTestVisible = False
                Canvas.SetLeft(label, px + radius + 3)
                Canvas.SetTop(label, py - PLOT_LABEL_SIZE)
                canvas.Children.Add(label)

        self.lbl_plot_info.Text = self._plot_info_text(
            len(placements),
            _group_ops.union_extent(marker_extent,
                                    _group_ops.outline_extent(outline)),
            len(outline))

    def _draw_outline(self, canvas, segments, transform, width, height):
        """Paint the level's walls behind the markers.

        Exterior walls get the heavier stroke, so the building edge reads as the
        boundary and everything inside it reads as core and partitions.
        """
        if not segments:
            return
        for segment in segments:
            x1, y1 = transform.to_canvas(segment.x1, segment.y1)
            x2, y2 = transform.to_canvas(segment.x2, segment.y2)
            # Cull whole segments that cannot touch the viewport.
            if max(x1, x2) < 0 or min(x1, x2) > width:
                continue
            if max(y1, y2) < 0 or min(y1, y2) > height:
                continue
            line = Line()
            line.X1, line.Y1, line.X2, line.Y2 = x1, y1, x2, y2
            if segment.is_exterior:
                line.Stroke = self._plot_wall_brush_ext
                line.StrokeThickness = PLOT_WALL_WIDTH_EXT
            else:
                line.Stroke = self._plot_wall_brush
                line.StrokeThickness = PLOT_WALL_WIDTH
            line.IsHitTestVisible = False      # never steal a marker's click
            canvas.Children.Add(line)

    def _plot_info_text(self, shown, extent, wall_count=0):
        """The sentence under the plan: how many, how big, what was left out."""
        parts = ["%d instance%s plotted" % (shown, "" if shown == 1 else "s")]
        if extent:
            parts.append(_group_ops.extent_label(extent))
        if wall_count:
            parts.append("%d wall segment%s" % (
                wall_count, "" if wall_count == 1 else "s"))
        if self._outline_truncated:
            parts.append("plan context cut short — this level has too many walls")
        if self._plot_unlocated:
            parts.append("%d instance%s have no location Revit can report"
                         % (self._plot_unlocated,
                            "" if self._plot_unlocated == 1 else "s"))
        return " · ".join(parts)

    def _selected_instance_ids(self):
        """Element ids of every instance currently drawn on the plan."""
        return [p.instance_id for p in self._visible_placements()]

    def _select_in_revit(self, instance_ids, description):
        """Push a selection into Revit and say what happened.

        Selection is a UI operation, so no transaction — but the dialog is modal,
        so the user only sees the result once they close it. The status line says
        so rather than leaving them wondering whether the click did anything.
        """
        if not instance_ids:
            self._set_status("Nothing to select — no instances are plotted.")
            return
        try:
            uidoc = revit.uidoc
        except Exception:
            uidoc = None        # pyRevit's revit.uidoc raises, it does not return None
        if uidoc is None:
            self._set_status("Cannot reach the Revit UI to change the selection.")
            return
        try:
            count = _group_ops.select_instances(uidoc, instance_ids)
            self._set_status("Selected %d %s in Revit — close this window to see them."
                             % (count, description))
        except Exception as exc:
            self._set_status("Could not change the Revit selection: %s"
                             % _group_ops._short_error(exc, "unknown error"))

    # ── EVENT HANDLERS: PLACEMENT ────────────────────────────────────────────

    def plot_kind_checked(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._rebuild_plot_levels()
        self._refresh_plot()

    def plot_level_changed(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._update_context_availability()
        self._refresh_plot()

    def _update_context_availability(self):
        """Plan context needs one level; grey the toggle out on "All levels"."""
        one_level = bool(self._plot_level())
        self.chk_plot_context.IsEnabled = one_level
        self.chk_plot_context.ToolTip = (
            "Draw the walls of the chosen level behind the markers" if one_level
            else "Pick a single level to draw its walls behind the markers")

    def plot_legend_toggled(self, sender, e):
        if getattr(self, '_loading', True):
            return
        row = getattr(sender, 'DataContext', None)
        if row is not None:
            # Read the CheckBox, NOT row.IsVisible. Under pythonnet a TwoWay
            # binding does not reliably write back into a Python property, so
            # the tick can flip on screen while the object still says True —
            # which showed up as "9 boxes unticked, 130 instances still plotted".
            # The control's own state is the one that is always right.
            visible = bool(sender.IsChecked)
            row.IsVisible = visible
            if visible:
                self._plot_hidden.discard(row.type_id)
            else:
                self._plot_hidden.add(row.type_id)
        self._update_plot_legend_label()
        self._draw_plan()

    def plot_legend_zoom(self, sender, e):
        """Double-clicking a legend row zooms the plan onto that group type.

        Double-click, not selection: clicking the row's checkbox selects the row
        too, so zooming on selection would yank the view every time somebody
        toggled a group's visibility.
        """
        if getattr(self, '_loading', True):
            return
        row = self.list_plot_legend.SelectedItem
        if row is None:
            return
        matches = _group_ops.filter_placements(
            self._placements, kind=self._plot_kind(), level=self._plot_level(),
            type_ids=[row.type_id])
        if not matches:
            self._set_status("%s has no placed instance under the current filter."
                             % row.TypeName)
            return
        self._plot_transform = _group_ops.PlanTransform.fit(
            _group_ops.placements_extent(matches),
            float(self.plan_canvas.ActualWidth or 1),
            float(self.plan_canvas.ActualHeight or 1),
            padding=PLOT_PADDING * 2)
        self._draw_plan()
        self._set_status("%s — %d instance%s on %s." % (
            row.TypeName, len(matches), "" if len(matches) == 1 else "s",
            row.KindLabel))

    def plot_show_all_clicked(self, sender, e):
        self._plot_hidden.clear()
        self._refresh_plot()

    def plot_show_none_clicked(self, sender, e):
        for row in self._plot_legend_rows:
            self._plot_hidden.add(row.type_id)
        self._refresh_plot(refit=False)

    def plot_labels_toggled(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._draw_plan()

    def plot_context_toggled(self, sender, e):
        if getattr(self, '_loading', True):
            return
        # Turning context on can widen the extent a lot, so refit rather than
        # leave the building drawn half outside the panel.
        self._plot_transform = None
        self._draw_plan()

    def plot_fit_clicked(self, sender, e):
        self._plot_transform = None
        self._draw_plan()

    def plot_select_clicked(self, sender, e):
        self._select_in_revit(self._selected_instance_ids(), "group instances")

    def plan_marker_clicked(self, sender, e):
        placement = getattr(sender, 'Tag', None)
        if not isinstance(placement, _group_ops.GroupPlacement):
            # pythonnet may hand the Tag back boxed; fall back to the id map.
            try:
                placement = self._plot_markers.get(int(sender.Tag))
            except Exception:
                placement = None
        if placement is None:
            return
        self._plot_dragging = False
        self._select_in_revit([placement.instance_id],
                              "instance of %s" % placement.type_name)
        e.Handled = True

    def plan_canvas_size_changed(self, sender, e):
        if getattr(self, '_loading', True):
            return
        self._draw_plan()

    def plan_canvas_wheel(self, sender, e):
        if self._plot_transform is None:
            return
        point = e.GetPosition(self.plan_canvas)
        factor = PLOT_ZOOM_STEP if e.Delta > 0 else (1.0 / PLOT_ZOOM_STEP)
        self._plot_transform.zoom_at(point.X, point.Y, factor)
        self._draw_plan()
        e.Handled = True

    def plan_canvas_mouse_down(self, sender, e):
        # Deliberately no CaptureMouse(): capturing on the Canvas routes every
        # later event to it, so the markers would never see their own click and
        # "click a marker to select it" would silently stop working. The cost is
        # that a drag ends when the pointer leaves the canvas, which MouseLeave
        # already handles.
        self._plot_dragging = True
        self._plot_drag_from = e.GetPosition(self.plan_canvas)

    def plan_canvas_mouse_move(self, sender, e):
        if not self._plot_dragging or self._plot_transform is None:
            return
        point = e.GetPosition(self.plan_canvas)
        self._plot_transform.pan_by(point.X - self._plot_drag_from.X,
                                    point.Y - self._plot_drag_from.Y)
        self._plot_drag_from = point
        self._draw_plan()

    def plan_canvas_mouse_up(self, sender, e):
        self._plot_dragging = False
        self._plot_drag_from = None


def show_group_manager(doc=None):
    """Entry point used by the pushbutton."""
    ManaGroupDialog(doc).ShowDialog()
