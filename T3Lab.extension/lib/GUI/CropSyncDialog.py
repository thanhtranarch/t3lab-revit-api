# -*- coding: utf-8 -*-
"""
CropSyncDialog
==============
Controller for Crop Sync: Synchronize view crop region shape, dimensions,
and annotation crop across views, with ability to select/change source view.

Author: T3Lab
"""
import os
import sys

from pyrevit import revit, script
from Autodesk.Revit import DB
from Autodesk.Revit.UI import TaskDialog
import System.Windows

from GUI.WPF_Base import T3WPFWindow, to_items_source
from Snippets.crop_sync import (
    get_view_crop_data,
    sync_crop_to_view,
)
from Snippets._compat import disposing

XAML_FILE = os.path.join(os.path.dirname(__file__), "Tools", "CropSync.xaml")


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


class CropSyncDialog(T3WPFWindow):
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

        self._source_crop_data = None
        self._is_initializing = True

        self._load_source_views()
        self._populate_source_views()
        self._set_source_view(self._source_view)
        self._is_initializing = False

    def _load_source_views(self):
        """Collect all non-template views in project capable of crop regions."""
        collector = (DB.FilteredElementCollector(self._doc)
                     .OfClass(DB.View)
                     .WhereElementIsNotElementType())

        self._all_source_views = []
        for v in collector:
            if v.IsTemplate:
                continue
            if hasattr(v, "GetCropRegionShapeManager") and hasattr(v, "CropBoxActive"):
                try:
                    mgr = v.GetCropRegionShapeManager()
                    if mgr is not None:
                        self._all_source_views.append(v)
                except Exception:
                    continue

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
        """Update active source view, analyze crop parameters, and reload target views."""
        self._source_view = view
        self.lbl_source_view_type.Text = str(self._source_view.ViewType)

        self._source_crop_data = get_view_crop_data(self._source_view)
        if self._source_crop_data:
            if self._source_crop_data.is_shaped:
                self.lbl_crop_shape_type.Text = "Custom Non-Rectangular Shape"
            else:
                self.lbl_crop_shape_type.Text = "Standard Rectangular Crop"

            dim_str = "{:.1f}m × {:.1f}m".format(
                self._source_crop_data.width / 1000.0,
                self._source_crop_data.height / 1000.0
            ) if self._source_crop_data.width > 0 else "Unbounded"
            self.lbl_crop_dimensions.Text = dim_str
        else:
            self.lbl_crop_shape_type.Text = "No crop manager available"
            self.lbl_crop_dimensions.Text = "-"

        self._load_target_views()
        self._populate_type_filters()
        self._apply_filters()

    def _load_target_views(self):
        """Load candidate target views excluding current source view and templates."""
        collector = (DB.FilteredElementCollector(self._doc)
                     .OfClass(DB.View)
                     .WhereElementIsNotElementType())

        self._all_view_items = []
        for v in collector:
            if v.IsTemplate or v.Id == self._source_view.Id:
                continue
            if hasattr(v, "GetCropRegionShapeManager") and hasattr(v, "CropBoxActive"):
                try:
                    mgr = v.GetCropRegionShapeManager()
                    if mgr is not None:
                        self._all_view_items.append(ViewItem(v))
                except Exception:
                    continue

        self._all_view_items.sort(key=lambda x: (x.view_type, x.name))

    def _populate_type_filters(self):
        """Populate the view type dropdown."""
        types = sorted(list(set(item.view_type for item in self._all_view_items)))
        self.cmb_type_filter.Items.Clear()
        self.cmb_type_filter.Items.Add("All View Types")
        for t in types:
            self.cmb_type_filter.Items.Add(t)

        src_type = str(self._source_view.ViewType)
        if src_type in types:
            self.cmb_type_filter.SelectedItem = src_type
        else:
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
        TaskDialog.Show("Crop Sync", "Active view '{}' does not support crop region.".format(act_view.Name))

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
            TaskDialog.Show("Crop Sync", "Please select at least one target view.")
            return

        if not self._source_crop_data:
            TaskDialog.Show("Crop Sync", "Source view does not have valid crop data.")
            return

        apply_shape = bool(self.chk_apply_shape.IsChecked)
        set_active = bool(self.chk_set_active.IsChecked)
        set_visible = bool(self.chk_set_visible.IsChecked)
        sync_annotation = bool(self.chk_sync_annotation.IsChecked)

        synced_count = 0

        with disposing(DB.TransactionGroup(self._doc, "T3Lab: Crop Sync")) as tg:
            tg.Start()
            for tgt_view in selected_targets:
                with disposing(DB.Transaction(self._doc, "Apply Crop to " + tgt_view.Name)) as t:
                    t.Start()
                    ok = sync_crop_to_view(
                        self._doc, tgt_view, self._source_crop_data,
                        apply_shape=apply_shape,
                        set_active=set_active,
                        set_visible=set_visible,
                        sync_annotation_crop=sync_annotation
                    )
                    if ok:
                        synced_count += 1
                    t.Commit()
            tg.Assimilate()

        msg = "Successfully applied crop settings from '{}' to {} of {} views.".format(
            self._source_view.Name, synced_count, len(selected_targets)
        )
        TaskDialog.Show("Crop Sync", msg)
        self.Close()


def show_crop_sync():
    """Main entry point for Crop Sync."""
    dlg = CropSyncDialog()
    dlg.ShowDialog()
