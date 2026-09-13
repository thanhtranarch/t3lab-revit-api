# -*- coding: utf-8 -*-
"""Wall Adjust Base — Dialog and execution logic for Auto Adjust Base Offset."""

import os
import sys

from pyrevit import forms

try:
    from GUI import RevitTheme as _theme
except Exception:
    _theme = None

from GUI.WPF_Base import T3WPFWindow
from Snippets._compat import eid_value

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'WallAdjustBase.xaml')

try:
    import Autodesk.Revit.DB as DB
    from Autodesk.Revit.DB import (
        FilteredElementCollector,
        BuiltInCategory,
        BuiltInParameter,
        Transaction,
        ElementId,
        Wall,
        Floor,
        FamilyInstance,
        Level,
        IFailuresPreprocessor,
        FailureProcessingResult,
        FailureSeverity,
    )
    from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
except Exception:
    DB = None


class WarningSwallower(IFailuresPreprocessor if DB else object):
    __namespace__ = "T3Lab.WallAdjustBase_Failures"

    def PreprocessFailures(self, fa):
        for f in fa.GetFailureMessages():
            if f.GetSeverity() == FailureSeverity.Warning:
                fa.DeleteWarning(f)
        return FailureProcessingResult.Continue


class ElementSelectionFilter(ISelectionFilter if DB else object):
    __namespace__ = "T3Lab.WallAdjustBase_Filter"

    def AllowElement(self, elem):
        if isinstance(elem, (Wall, Floor)):
            return True
        if isinstance(elem, FamilyInstance) and elem.Category:
            cat_id = eid_value(elem.Category.Id)
            if cat_id in (BuiltInCategory.OST_StructuralColumns.value__,
                          BuiltInCategory.OST_Columns.value__,
                          BuiltInCategory.OST_StructuralFraming.value__):
                return True
        return False

    def AllowReference(self, reference, position):
        return False


class LevelItem(object):
    def __init__(self, level):
        self.level = level
        self.name = level.Name
        self.elevation_mm = round(level.Elevation * 304.8, 1)
        self.display = "{} ({} mm)".format(self.name, self.elevation_mm)

    def __str__(self):
        return self.display


class WallAdjustBaseWindow(T3WPFWindow):
    def __init__(self, doc, uidoc):
        T3WPFWindow.__init__(self, _XAML)
        self._doc = doc
        self._uidoc = uidoc
        self._elements = []
        self._levels = []

        self._adopt_host_font()
        self._apply_theme()
        self._load_levels()
        self._check_initial_selection()

    def _adopt_host_font(self):
        if _theme is None:
            return
        family, size = _theme.host_font()
        if family:
            try:
                self.FontFamily = family
                if size and size > 0:
                    self.FontSize = size
            except Exception:
                pass

    def _apply_theme(self, theme=None):
        if _theme is None:
            return
        try:
            _theme.apply(self, theme)
        except Exception:
            pass

    def _load_levels(self):
        if not self._doc:
            return
        collector = FilteredElementCollector(self._doc).OfClass(Level)
        levels = list(collector)
        levels.sort(key=lambda x: x.Elevation)
        self._levels = [LevelItem(lvl) for lvl in levels]

        if hasattr(self, 'cmb_levels') and self.cmb_levels:
            self.cmb_levels.ItemsSource = [item.display for item in self._levels]
            if len(self._levels) > 0:
                self.cmb_levels.SelectedIndex = 0

    def _check_initial_selection(self):
        if not self._uidoc:
            return
        selection = self._uidoc.Selection.GetElementIds()
        elems = []
        filter_inst = ElementSelectionFilter()
        for eid in selection:
            el = self._doc.GetElement(eid)
            if el and filter_inst.AllowElement(el):
                elems.append(el)

        self._elements = elems
        self._update_selection_ui()

    def _update_selection_ui(self):
        count = len(self._elements)
        if hasattr(self, 'txt_selection_status') and self.txt_selection_status:
            if count == 0:
                self.txt_selection_status.Text = "No valid elements selected (Walls, Floors, Columns, Beams)"
            else:
                self.txt_selection_status.Text = "{} element(s) selected ready to adjust".format(count)

    def btn_pick_clicked(self, sender, e):
        if not self._uidoc:
            return
        self.Hide()
        try:
            refs = self._uidoc.Selection.PickObjects(
                ObjectType.Element,
                ElementSelectionFilter(),
                "Select elements (Walls, Floors, Columns, Beams), then click Finish"
            )
            elems = []
            if refs:
                for r in refs:
                    el = self._doc.GetElement(r.ElementId)
                    if el:
                        elems.append(el)
            self._elements = elems
        except Exception:
            pass
        finally:
            self.Show()
            self._update_selection_ui()

    def btn_cancel_clicked(self, sender, e):
        self.Close()

    def win_minimize_clicked(self, sender, e):
        self.WindowState = System.Windows.WindowState.Minimized

    def win_close_clicked(self, sender, e):
        self.Close()

    def btn_apply_clicked(self, sender, e):
        if not self._elements:
            forms.alert("Please select at least one valid element!", title="Wall Adjust Base")
            return

        sel_idx = self.cmb_levels.SelectedIndex
        if sel_idx < 0 or sel_idx >= len(self._levels):
            forms.alert("Please select a target level!", title="Wall Adjust Base")
            return

        target_level = self._levels[sel_idx].level
        adjust_base = True
        if hasattr(self, 'rb_top') and self.rb_top and self.rb_top.IsChecked:
            adjust_base = False

        adjusted = 0
        failed = 0

        t = Transaction(self._doc, "T3Lab: Auto Adjust Offset")
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(WarningSwallower())
        t.SetFailureHandlingOptions(opts)

        t.Start()
        try:
            for elem in self._elements:
                success = self._adjust_element(elem, target_level, adjust_base)
                if success:
                    adjusted += 1
                else:
                    failed += 1
            t.Commit()
        except Exception as ex:
            t.RollBack()
            forms.alert("Error adjusting offsets:\n{}".format(str(ex)), title="Wall Adjust Base")
            return

        msg = "Adjusted {} element(s) to level '{}' preserving 3D position.".format(
            adjusted, target_level.Name
        )
        if failed > 0:
            msg += "\n{} element(s) could not be updated.".format(failed)

        self.Close()
        forms.alert(msg, title="Auto Adjust Base Offset")

    def _adjust_element(self, elem, new_level, adjust_base=True):
        try:
            if isinstance(elem, Wall):
                param_lvl = BuiltInParameter.WALL_BASE_CONSTRAINT if adjust_base else BuiltInParameter.WALL_HEIGHT_TYPE
                param_off = BuiltInParameter.WALL_BASE_OFFSET if adjust_base else BuiltInParameter.WALL_TOP_OFFSET
            elif isinstance(elem, Floor):
                param_lvl = BuiltInParameter.LEVEL_PARAM
                param_off = BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM
            elif isinstance(elem, FamilyInstance):
                cat_id = eid_value(elem.Category.Id)
                if cat_id in (BuiltInCategory.OST_StructuralColumns.value__, BuiltInCategory.OST_Columns.value__):
                    param_lvl = BuiltInParameter.FAMILY_BASE_LEVEL_PARAM if adjust_base else BuiltInParameter.FAMILY_TOP_LEVEL_PARAM
                    param_off = BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM if adjust_base else BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM
                elif cat_id == BuiltInCategory.OST_StructuralFraming.value__:
                    param_lvl = BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM
                    param_off = BuiltInParameter.STRUCTURAL_BEAM_END0_ELEVATION
                else:
                    return False
            else:
                return False

            p_lvl = elem.get_Parameter(param_lvl)
            p_off = elem.get_Parameter(param_off)
            if not p_lvl or not p_off or p_lvl.IsReadOnly or p_off.IsReadOnly:
                return False

            old_lvl_id = p_lvl.AsElementId()
            if old_lvl_id == ElementId.InvalidElementId:
                return False

            old_level = self._doc.GetElement(old_lvl_id)
            if not old_level or not isinstance(old_level, Level):
                return False

            old_offset = p_off.AsDouble()
            actual_elevation = old_level.Elevation + old_offset
            new_offset = actual_elevation - new_level.Elevation

            p_lvl.Set(new_level.Id)
            p_off.Set(new_offset)
            return True
        except Exception:
            return False


def show_wall_adjust_base(doc, uidoc):
    if not doc or not uidoc:
        forms.alert("No active Revit document found.", title="Wall Adjust Base")
        return
    win = WallAdjustBaseWindow(doc, uidoc)
    win.ShowDialog()
