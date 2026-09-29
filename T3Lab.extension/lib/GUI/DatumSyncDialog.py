# -*- coding: utf-8 -*-
"""
DatumSyncDialog
===============
Controller for Datum Sync: Synchronize 2D extents, bubble visibility, and leaders
of Grids and Levels between views, with ability to select/change source view.

Author: T3Lab
"""
import os
import sys

from pyrevit import revit, script
from Autodesk.Revit import DB
from Autodesk.Revit.UI import TaskDialog
import System.Windows

from GUI.WPF_Base import T3WPFWindow, to_items_source
from Snippets.datum_sync import (
    is_plan_view,
    is_elevation_or_section,
    get_view_grids_data,
    get_view_levels_data,
    sync_grids_to_view,
    sync_levels_to_view,
)
from Snippets._compat import disposing

XAML_FILE = os.path.join(os.path.dirname(__file__), "Tools", "DatumSync.xaml")


class ViewItem(object):
    def __init__(self, view, is_selected=False):
        self.view = view
        self.name = view.Name
        self.view_type = str(view.ViewType)
        sheet_param = view.get_Parameter(DB.BuiltInParameter.VIEWPORT_SHEET_NUMBER)
        self.sheet_info = sheet_param.AsString() if sheet_param and sheet_param.AsString() else "-"
        self._is_selected = bool(is_selected)

    @property
    def is_selected(self):
        # A real bool: the bridge TextBlock renders it as "True"/"False", and
        # sync_header_checkbox() reads bool(row.is_selected) - bool("False") is True.
        return self._is_selected

    @is_selected.setter
    def is_selected(self, val):
        if isinstance(val, str):
            self._is_selected = (val.lower() == "true")
        else:
            self._is_selected = bool(val)


class DatumSyncDialog(T3WPFWindow):
    # The row checkbox's Click= lives in a DataTemplate; without this flag it
    # is never wired and the status line ignores row clicks.
    WIRE_TEMPLATED_CLICKS = True

    def __init__(self, doc=None, uidoc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self._doc = doc or revit.doc
        self._uidoc = uidoc or revit.uidoc
        self._source_view = self._doc.ActiveView

        self._all_source_views = []
        self._all_view_items = []
        self._filtered_view_items = []

        self._source_grids_data = {}
        self._source_levels_data = {}
        self._is_initializing = True

        self._load_source_views()
        self._populate_source_views()
        self._set_source_view(self._source_view)
        self._is_initializing = False

    def _load_source_views(self):
        """Collect all non-template views in project capable of datum display."""
        collector = (DB.FilteredElementCollector(self._doc)
                     .OfClass(DB.View)
                     .WhereElementIsNotElementType())

        self._all_source_views = []
        for v in collector:
            if v.IsTemplate:
                continue
            if is_plan_view(v) or is_elevation_or_section(v):
                self._all_source_views.append(v)

        self._all_source_views.sort(key=lambda v: (str(v.ViewType), v.Name))

    def _populate_source_views(self):
        """Populate the source view ComboBox."""
        self.cmb_source_view.Items.Clear()
        selected_idx = 0
        for idx, v in enumerate(self._all_source_views):
            label = "[{}] {}".format(v.ViewType, v.Name)
            self.cmb_source_view.Items.Add(label)
            if self._source_view and v.Id == self._source_view.Id:
                selected_idx = idx

        if self._all_source_views:
            self.cmb_source_view.SelectedIndex = selected_idx

    def _set_source_view(self, view):
        """Update active source view, analyze datums, and reload target views."""
        self._source_view = view
        self.lbl_source_view_type.Text = str(self._source_view.ViewType)

        # 1. Analyze grids
        self._source_grids_data = get_view_grids_data(self._doc, self._source_view)
        grid_count = len(self._source_grids_data)

        # 2. Analyze levels
        level_count = 0
        if is_elevation_or_section(self._source_view):
            self._source_levels_data = get_view_levels_data(self._doc, self._source_view)
            level_count = len(self._source_levels_data)
            self.chk_sync_levels.IsEnabled = True
            self.chk_sync_levels.IsChecked = True
        else:
            self._source_levels_data = {}
            self.chk_sync_levels.IsEnabled = False
            self.chk_sync_levels.IsChecked = False

        self.lbl_datum_counts.Text = "{} Grids · {} Levels detected".format(grid_count, level_count)

        # 3. Reload target views
        self._load_target_views()
        self._populate_type_filters()
        self._apply_filters()

    def _load_target_views(self):
        """Load candidate target views compatible with the current source view."""
        collector = (DB.FilteredElementCollector(self._doc)
                     .OfClass(DB.View)
                     .WhereElementIsNotElementType())

        self._all_view_items = []
        is_source_plan = is_plan_view(self._source_view)
        is_source_vertical = is_elevation_or_section(self._source_view)

        for v in collector:
            if v.IsTemplate or v.Id == self._source_view.Id:
                continue

            if is_source_plan:
                if is_plan_view(v):
                    self._all_view_items.append(ViewItem(v))
            elif is_source_vertical:
                if is_elevation_or_section(v):
                    self._all_view_items.append(ViewItem(v))
            else:
                if is_plan_view(v) or is_elevation_or_section(v):
                    self._all_view_items.append(ViewItem(v))

        self._all_view_items.sort(key=lambda x: (x.view_type, x.name))

    def _populate_type_filters(self):
        """Populate the view type dropdown."""
        types = sorted(list(set(item.view_type for item in self._all_view_items)))
        self.cmb_type_filter.Items.Clear()
        self.cmb_type_filter.Items.Add("All View Types")
        for t in types:
            self.cmb_type_filter.Items.Add(t)
        self.cmb_type_filter.SelectedIndex = 0

    def _apply_filters(self):
        """Filter views based on search text and type combo."""
        search_txt = self.txt_search.Text.strip().lower() if self.txt_search.Text else ""
        selected_type = str(self.cmb_type_filter.SelectedItem or "")

        self._filtered_view_items = []
        for item in self._all_view_items:
            if search_txt and search_txt not in item.name.lower():
                continue
            if selected_type and selected_type != "All View Types" and item.view_type != selected_type:
                continue
            self._filtered_view_items.append(item)

        self.dg_views.ItemsSource = to_items_source(self._filtered_view_items)
        self._update_status()
        self.sync_header_checkbox(self.chk_all_dg_views, self.dg_views, "is_selected")

    def _update_status(self):
        sel_count = sum(1 for item in self._filtered_view_items if item._is_selected)
        total_count = len(self._filtered_view_items)
        self.status_text.Text = "Selected {} of {} views".format(sel_count, total_count)

    # ── Source View Event Handlers ───────────────────────────────────────────
    def source_view_changed(self, sender, args):
        if self._is_initializing:
            return
        idx = self.cmb_source_view.SelectedIndex
        if 0 <= idx < len(self._all_source_views):
            new_source = self._all_source_views[idx]
            if self._source_view is None or new_source.Id != self._source_view.Id:
                self._set_source_view(new_source)

    def use_active_view_clicked(self, sender, args):
        act_view = self._doc.ActiveView
        for idx, v in enumerate(self._all_source_views):
            if v.Id == act_view.Id:
                self.cmb_source_view.SelectedIndex = idx
                return
        TaskDialog.Show("Datum Sync", "Active view '{}' is not a valid 2D plan or elevation view.".format(act_view.Name))

    # ── Window Control Event Handlers ─────────────────────────────────────────
    def minimize_button_clicked(self, sender, args):
        self.WindowState = System.Windows.WindowState.Minimized

    def maximize_button_clicked(self, sender, args):
        if self.WindowState == System.Windows.WindowState.Maximized:
            self.WindowState = System.Windows.WindowState.Normal
        else:
            self.WindowState = System.Windows.WindowState.Maximized

    def close_button_clicked(self, sender, args):
        self.Close()

    def search_text_changed(self, sender, args):
        self._apply_filters()

    def type_filter_changed(self, sender, args):
        self._apply_filters()

    def select_all_btn_clicked(self, sender, args):
        for item in self._filtered_view_items:
            item._is_selected = True
        self.dg_views.Items.Refresh()
        self._update_status()
        self.sync_header_checkbox(self.chk_all_dg_views, self.dg_views, "is_selected")

    def select_none_btn_clicked(self, sender, args):
        for item in self._filtered_view_items:
            item._is_selected = False
        self.dg_views.Items.Refresh()
        self._update_status()
        self.sync_header_checkbox(self.chk_all_dg_views, self.dg_views, "is_selected")

    def select_all_dg_views_clicked(self, sender, args):
        self.toggle_all_rows(self.dg_views, "is_selected", sender.IsChecked)
        self._update_status()

    def row_checkbox_clicked(self, sender, args):
        # T3WPFWindow's checkbox bridge has already written the tick into the
        # row before Click fires - toggling here again would undo the click.
        self._update_status()
        self.sync_header_checkbox(self.chk_all_dg_views, self.dg_views, "is_selected")

    def sync_button_clicked(self, sender, args):
        selected_targets = [item.view for item in self._all_view_items if item._is_selected]
        if not selected_targets:
            TaskDialog.Show("Datum Sync", "Please select at least one target view.")
            return

        sync_grids = bool(self.chk_sync_grids.IsChecked)
        sync_levels = bool(self.chk_sync_levels.IsChecked)
        sync_bubbles = bool(self.chk_sync_bubbles.IsChecked)
        sync_leaders = bool(self.chk_sync_leaders.IsChecked)

        total_grids_synced = 0
        total_levels_synced = 0

        with disposing(DB.TransactionGroup(self._doc, "T3Lab: Datum Sync")) as tg:
            tg.Start()
            for tgt_view in selected_targets:
                with disposing(DB.Transaction(self._doc, "Sync Datums to " + tgt_view.Name)) as t:
                    t.Start()
                    if sync_grids and self._source_grids_data:
                        total_grids_synced += sync_grids_to_view(
                            self._doc, tgt_view, self._source_grids_data,
                            sync_extents=True, sync_bubbles=sync_bubbles, sync_leaders=sync_leaders
                        )
                    if sync_levels and self._source_levels_data:
                        total_levels_synced += sync_levels_to_view(
                            self._doc, tgt_view, self._source_levels_data,
                            sync_extents=True, sync_bubbles=sync_bubbles, sync_leaders=sync_leaders
                        )
                    t.Commit()
            tg.Assimilate()

        msg = "Successfully synchronized datums from '{}' to {} target views:\n• {} grid updates\n• {} level updates".format(
            self._source_view.Name, len(selected_targets), total_grids_synced, total_levels_synced
        )
        TaskDialog.Show("Datum Sync", msg)
        self.Close()


def show_datum_sync():
    """Main entry point for Datum Sync."""
    dlg = DatumSyncDialog()
    dlg.ShowDialog()
