# -*- coding: utf-8 -*-
"""FoundationVolume — Dialog and logic for writing Revit computed volume
into selected parameters on Structural Foundation elements.
"""

import os
import sys

from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

from System.Windows import WindowState, Visibility, Thickness
from System.Windows.Controls import ListBoxItem

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter,
    Transaction, StorageType, UnitUtils
)

try:
    from Autodesk.Revit.DB import UnitTypeId
    HAS_UNIT_TYPE_ID = True
except Exception:
    HAS_UNIT_TYPE_ID = False

XAML_FILE = os.path.join(os.path.dirname(__file__), 'Tools', 'FoundationVolume.xaml')
logger = script.get_logger()


def ft3_to_m3(value):
    """Convert Revit internal cubic feet to cubic metres."""
    try:
        if HAS_UNIT_TYPE_ID:
            return UnitUtils.ConvertFromInternalUnits(value, DB.UnitTypeId.CubicMeters)
        else:
            from Autodesk.Revit.DB import DisplayUnitType
            return UnitUtils.ConvertFromInternalUnits(value, DisplayUnitType.DUT_CUBIC_METERS)
    except Exception:
        return value * 0.0283168466  # fallback constant


def get_foundations(doc):
    """Collect all Structural Foundation instances in the document."""
    if not doc:
        return []
    return list(
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralFoundation)
        .WhereElementIsNotElementType()
        .ToElements()
    )


def get_volume_m3(element):
    """Try HOST_VOLUME_COMPUTED first, fallback to geometry solid sum."""
    try:
        p = element.get_Parameter(BuiltInParameter.HOST_VOLUME_COMPUTED)
        if p and p.HasValue and p.AsDouble() > 0:
            return ft3_to_m3(p.AsDouble())
    except Exception:
        pass
    # Geometry fallback
    try:
        opts = DB.Options()
        opts.ComputeReferences = False
        geom = element.get_Geometry(opts)
        total = 0.0
        for obj in geom:
            if isinstance(obj, DB.Solid) and obj.Volume > 0:
                total += obj.Volume
            elif isinstance(obj, DB.GeometryInstance):
                for sub in obj.GetInstanceGeometry():
                    if isinstance(sub, DB.Solid) and sub.Volume > 0:
                        total += sub.Volume
        if total > 0:
            return ft3_to_m3(total)
    except Exception:
        pass
    return None


def get_writable_params(foundations):
    """Return sorted list of writable instance parameter names from foundations."""
    param_names = set()
    for f in foundations[:20]:  # sample first 20 for speed
        for p in f.Parameters:
            if p.IsReadOnly:
                continue
            if p.StorageType not in (StorageType.Double, StorageType.String):
                continue
            name = p.Definition.Name
            if name:
                param_names.add(name)
    return sorted(param_names)


class FoundationVolumeWindow(T3WPFWindow):
    """WPF dialog for writing computed volume into Structural Foundation elements."""

    def __init__(self, doc=None, uidoc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc or revit.doc
        self.uidoc = uidoc

        self.foundations = get_foundations(self.doc)
        self.all_params = get_writable_params(self.foundations)
        self.selected_param = None
        self._current_tab = 0

        # Bind controls and initial state
        if hasattr(self, "FoundationCount") and self.FoundationCount:
            self.FoundationCount.Text = "{0} foundation(s)".format(len(self.foundations))

        self._populate_list(self.all_params)
        self._go_to_tab(0)

        # Connect events
        if hasattr(self, "SearchBox") and self.SearchBox:
            self.SearchBox.TextChanged += self._on_search
        if hasattr(self, "ParamList") and self.ParamList:
            self.ParamList.MouseDoubleClick += self._on_list_double_click
            self.ParamList.SelectionChanged += self._on_selection_changed
        if hasattr(self, "RunButton") and self.RunButton:
            self.RunButton.Click += self._on_next
        if hasattr(self, "back_button") and self.back_button:
            self.back_button.Click += self._on_back
        if hasattr(self, "CloseButton") and self.CloseButton:
            self.CloseButton.Click += lambda s, e: self.Close()
        if hasattr(self, "nav_toggle_select") and self.nav_toggle_select:
            self.nav_toggle_select.Click += self._on_nav_select
        if hasattr(self, "nav_toggle_write") and self.nav_toggle_write:
            self.nav_toggle_write.Click += self._on_nav_write

    # ── List helpers ──────────────────────────────────────────────────────
    def _populate_list(self, names):
        if not hasattr(self, "ParamList") or not self.ParamList:
            return
        self.ParamList.Items.Clear()
        for n in names:
            item = ListBoxItem()
            item.Content = n
            self.ParamList.Items.Add(item)
        if self.ParamList.Items.Count > 0:
            self.ParamList.SelectedIndex = 0

    def _on_search(self, sender, e):
        query = self.SearchBox.Text.strip().lower() if self.SearchBox else ""
        filtered = [n for n in self.all_params if query in n.lower()] if query else self.all_params
        self._populate_list(filtered)

    def _on_selection_changed(self, sender, e):
        item = self.ParamList.SelectedItem if self.ParamList else None
        if item:
            self.selected_param = item.Content
            if hasattr(self, "SelectedLabel") and self.SelectedLabel:
                self.SelectedLabel.Text = self.selected_param
        else:
            self.selected_param = None
            if hasattr(self, "SelectedLabel") and self.SelectedLabel:
                self.SelectedLabel.Text = "(none)"

    def _on_list_double_click(self, sender, e):
        if self.selected_param:
            self._go_to_tab(1)

    # ── Status helper ─────────────────────────────────────────────────────
    def _show_status(self, title, detail, success=True):
        if not hasattr(self, "StatusBorder") or not self.StatusBorder:
            return

        try:
            if success:
                self.StatusBorder.Background = self.FindResource("T3.Success.Fill")
                self.StatusBorder.BorderBrush = self.FindResource("T3.Success.Accent")
                self.StatusTitle.Foreground = self.FindResource("T3.Success.Text")
                self.StatusText.Foreground = self.FindResource("T3.Success.Text")
            else:
                self.StatusBorder.Background = self.FindResource("T3.Warning.Fill")
                self.StatusBorder.BorderBrush = self.FindResource("T3.Warning.Accent")
                self.StatusTitle.Foreground = self.FindResource("T3.Warning.Text")
                self.StatusText.Foreground = self.FindResource("T3.Warning.Text")
        except Exception:
            pass

        if hasattr(self, "StatusTitle") and self.StatusTitle:
            self.StatusTitle.Text = title
        if hasattr(self, "StatusText") and self.StatusText:
            self.StatusText.Text = detail
        self.StatusBorder.Visibility = Visibility.Visible
        if hasattr(self, "WriteHintBorder") and self.WriteHintBorder:
            self.WriteHintBorder.Visibility = Visibility.Collapsed

    # ── Run logic ─────────────────────────────────────────────────────────
    def _on_run(self, sender, e):
        if not self.selected_param:
            self._show_status(
                "No parameter selected",
                "Please select a target parameter from the list above.",
                success=False
            )
            return
        if not self.foundations:
            self._show_status(
                "No foundations found",
                "No Structural Foundation elements were found in the active document.",
                success=False
            )
            return

        target_param_name = self.selected_param
        updated = 0
        skipped_no_vol = 0
        skipped_no_param = 0
        skipped_readonly = 0
        errors = 0

        with Transaction(self.doc, "DQT - Write Foundation Volume") as t:
            t.Start()
            for f in self.foundations:
                try:
                    vol_m3 = get_volume_m3(f)
                    if vol_m3 is None or vol_m3 <= 0:
                        skipped_no_vol += 1
                        continue

                    p = f.LookupParameter(target_param_name)
                    if p is None:
                        skipped_no_param += 1
                        continue
                    if p.IsReadOnly:
                        skipped_readonly += 1
                        continue

                    # Write value according to StorageType
                    if p.StorageType == StorageType.Double:
                        try:
                            is_volume_spec = False
                            try:
                                if HAS_UNIT_TYPE_ID and hasattr(p.Definition, "GetSpecTypeId"):
                                    spec_id = p.Definition.GetSpecTypeId()
                                    is_volume_spec = (spec_id == DB.SpecTypeId.Volume)
                                else:
                                    from Autodesk.Revit.DB import ParameterType
                                    is_volume_spec = (p.Definition.ParameterType == ParameterType.Volume)
                            except Exception:
                                pass

                            if is_volume_spec:
                                try:
                                    if HAS_UNIT_TYPE_ID:
                                        internal_val = UnitUtils.ConvertToInternalUnits(vol_m3, DB.UnitTypeId.CubicMeters)
                                    else:
                                        from Autodesk.Revit.DB import DisplayUnitType
                                        internal_val = UnitUtils.ConvertToInternalUnits(vol_m3, DisplayUnitType.DUT_CUBIC_METERS)
                                    p.Set(internal_val)
                                except Exception:
                                    p.Set(vol_m3)
                            else:
                                p.Set(vol_m3)
                        except Exception:
                            p.Set(vol_m3)

                    elif p.StorageType == StorageType.String:
                        p.Set(str(round(vol_m3, 4)))

                    else:
                        skipped_no_param += 1
                        continue

                    updated += 1

                except Exception as ex:
                    logger.error("Foundation volume write error: {}".format(ex))
                    errors += 1

            t.Commit()

        # Build result
        success = updated > 0
        if success:
            title = "Completed — {0} of {1} foundation(s) updated".format(
                updated, len(self.foundations))
        else:
            title = "No foundations were updated"

        detail_lines = []
        if skipped_no_vol > 0:
            detail_lines.append("• {0} skipped — volume = 0 or unavailable".format(skipped_no_vol))
        if skipped_no_param > 0:
            detail_lines.append("• {0} skipped — parameter \"{1}\" not found on element".format(skipped_no_param, target_param_name))
        if skipped_readonly > 0:
            detail_lines.append("• {0} skipped — parameter is read-only".format(skipped_readonly))
        if errors > 0:
            detail_lines.append("• {0} error(s) encountered during write".format(errors))
        if not detail_lines:
            detail_lines.append("All foundations processed successfully.")

        self._show_status(title, "\n".join(detail_lines), success=success)

    # ── Tab navigation ────────────────────────────────────────────────────
    def _go_to_tab(self, index):
        self._current_tab = index
        if hasattr(self, "main_tabs") and self.main_tabs:
            self.main_tabs.SelectedIndex = index
        if hasattr(self, "nav_toggle_select") and self.nav_toggle_select:
            self.nav_toggle_select.IsChecked = (index == 0)
        if hasattr(self, "nav_toggle_write") and self.nav_toggle_write:
            self.nav_toggle_write.IsChecked = (index == 1)
        if index == 0:
            if hasattr(self, "back_button") and self.back_button:
                self.back_button.Visibility = Visibility.Collapsed
            if hasattr(self, "next_button_text") and self.next_button_text:
                self.next_button_text.Text = "Next"
            if hasattr(self, "next_button_icon") and self.next_button_icon:
                self.next_button_icon.Text = " →"
        else:
            if hasattr(self, "back_button") and self.back_button:
                self.back_button.Visibility = Visibility.Visible
            if hasattr(self, "next_button_text") and self.next_button_text:
                self.next_button_text.Text = "Write Volume"
            if hasattr(self, "next_button_icon") and self.next_button_icon:
                self.next_button_icon.Text = ""

    def _on_next(self, sender, e):
        if self._current_tab == 0:
            if not self.selected_param:
                return
            self._go_to_tab(1)
        else:
            self._on_run(sender, e)

    def _on_back(self, sender, e):
        self._go_to_tab(0)

    def _on_nav_select(self, sender, e):
        self._go_to_tab(0)

    def _on_nav_write(self, sender, e):
        if self.selected_param:
            self._go_to_tab(1)
        else:
            if hasattr(self, "nav_toggle_write") and self.nav_toggle_write:
                self.nav_toggle_write.IsChecked = False


def show_foundation_volume_dialog(doc=None, uidoc=None):
    """Display Foundation Volume dialog."""
    d = doc or revit.doc
    if not d:
        forms.alert("No active Revit document found.", title="Foundation Volume")
        return
    u = uidoc
    if u is None:
        try:
            u = revit.uidoc
        except Exception:
            u = None
    win = FoundationVolumeWindow(d, u)
    win.ShowDialog()
