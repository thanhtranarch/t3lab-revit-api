# -*- coding: utf-8 -*-
"""Renumber Elements Along Spline - Utility Path"""

import clr
import System
from System.Collections.Generic import List

from System.Windows import Window, SizeToContent, WindowStartupLocation, Thickness, HorizontalAlignment, VerticalAlignment, TextAlignment, FontWeights, GridLength, GridUnitType
from System.Windows.Controls import (
    StackPanel, Label, ComboBox, ComboBoxItem, TextBox, Button,
    Grid as WPFGrid, RowDefinition, ColumnDefinition, Orientation, TextBlock
)
from System.Windows.Media import BrushConverter

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Architecture import *
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

from pyrevit import revit, forms, script
from Snippets._compat import disposing

_BC = BrushConverter()

DQT_GOLD       = _BC.ConvertFromString("#0F172A")
DQT_DARK       = _BC.ConvertFromString("#0F172A")
DQT_CREAM      = _BC.ConvertFromString("#F8FAFC")
DQT_BORDER     = _BC.ConvertFromString("#CBD5E1")
DQT_BROWN      = _BC.ConvertFromString("#5D4E37")
DQT_WHITE      = _BC.ConvertFromString("#FFFFFF")
DQT_ACCENT     = _BC.ConvertFromString("#E0D5C0")

CAT_BAN_LIST = {
    -2000260, -2000261, -2000954, -2000045, -2000067, -2000262, -2000920,
    -2000919, -2000123, -2000173, -2000171, -2000530, -2000127, -2000947,
    -2000946, -2002000, -2000150, -2001260, -2000280
}


def _eid_int(eid):
    try:
        return eid.Value          # Revit 2025+
    except AttributeError:
        return eid.IntegerValue   # Revit 2024

def _is_text_param(param):
    if param.StorageType != DB.StorageType.String:
        return False
    if param.IsReadOnly:
        return False
    try:
        return param.Definition.GetDataType() == DB.SpecTypeId.String.Text
    except:
        pass
    try:
        return param.Definition.ParameterType == DB.ParameterType.Text
    except:
        pass
    return False


class CategorySelectionFilter(ISelectionFilter):
    __namespace__ = "T3Lab.RenumberAlongSpline"
    def __init__(self, bic_int):
        self.bic_int = bic_int
    def AllowElement(self, elem):
        if elem and elem.Category:
            return _eid_int(elem.Category.Id) == self.bic_int
        return False
    def AllowReference(self, ref, point):
        return True


class LineSelectionFilter(ISelectionFilter):
    __namespace__ = "T3Lab.RenumberAlongSpline"
    def AllowElement(self, elem):
        if elem and elem.Category:
            cat_id = _eid_int(elem.Category.Id)
            return cat_id == -2000051
        return False
    def AllowReference(self, ref, point):
        return True


def get_bic_from_category(cat):
    return System.Enum.ToObject(DB.BuiltInCategory, _eid_int(cat.Id))


def get_available_categories(doc):
    family_instances = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    cat_dict = {}
    for fi in family_instances:
        if not fi.Category:
            continue
        cat_id = _eid_int(fi.Category.Id)
        if cat_id in CAT_BAN_LIST:
            continue
        cat_name = fi.Category.Name
        if cat_name not in cat_dict:
            cat_dict[cat_name] = fi.Category

    try:
        cat_rooms = DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_Rooms)
        if cat_rooms:
            cat_dict[cat_rooms.Name] = cat_rooms
    except:
        pass
    return cat_dict


def get_text_parameters(doc, bic):
    elements = (
        DB.FilteredElementCollector(doc)
        .WhereElementIsNotElementType()
        .OfCategory(bic)
        .ToElements()
    )
    if not elements or len(elements) == 0:
        return []
    param_names = set()
    check_count = min(5, len(elements))
    for i in range(check_count):
        for p in elements[i].Parameters:
            if _is_text_param(p):
                param_names.add(p.Definition.Name)
    return sorted(list(param_names))


def get_element_location(elem, doc):
    loc = elem.Location
    if loc:
        if hasattr(loc, 'Point'):
            return loc.Point
        if hasattr(loc, 'Curve'):
            crv = loc.Curve
            return crv.Evaluate(0.5, True)
    bb = elem.get_BoundingBox(None)
    if not bb:
        bb = elem.get_BoundingBox(doc.ActiveView)
    if bb:
        return DB.XYZ(
            (bb.Min.X + bb.Max.X) / 2.0,
            (bb.Min.Y + bb.Max.Y) / 2.0,
            (bb.Min.Z + bb.Max.Z) / 2.0
        )
    return None


def get_curve_from_element(elem):
    if hasattr(elem, 'GeometryCurve') and elem.GeometryCurve:
        return elem.GeometryCurve
    loc = elem.Location
    if loc and hasattr(loc, 'Curve'):
        return loc.Curve
    return None


def _make_label(text, bold=False):
    lbl = TextBlock()
    lbl.Text = text
    lbl.Foreground = DQT_BROWN
    lbl.Margin = Thickness(0, 6, 0, 2)
    if bold:
        lbl.FontWeight = FontWeights.SemiBold
    return lbl


def _make_combobox(items, width=320):
    cb = ComboBox()
    cb.Width = width
    cb.HorizontalAlignment = HorizontalAlignment.Left
    for item in items:
        cbi = ComboBoxItem()
        cbi.Content = item
        cb.Items.Add(cbi)
    if cb.Items.Count > 0:
        cb.SelectedIndex = 0
    return cb


def _make_textbox(default_text, width=320):
    tb = TextBox()
    tb.Text = default_text
    tb.Width = width
    tb.HorizontalAlignment = HorizontalAlignment.Left
    tb.Padding = Thickness(4, 3, 4, 3)
    return tb


def show_category_dialog(cat_names):
    win = Window()
    win.Title = "DQT - Renumber Along Spline"
    win.SizeToContent = SizeToContent.WidthAndHeight
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen
    win.ResizeMode = System.Windows.ResizeMode.NoResize
    win.Background = DQT_CREAM

    main_panel = StackPanel()
    main_panel.Margin = Thickness(0)

    header = TextBlock()
    header.Text = "Renumber Along Spline"
    header.FontSize = 15
    header.FontWeight = FontWeights.Bold
    header.Foreground = DQT_DARK
    header.Background = DQT_GOLD
    header.Padding = Thickness(16, 10, 16, 10)
    main_panel.Children.Add(header)

    content = StackPanel()
    content.Margin = Thickness(16, 10, 16, 10)
    content.Children.Add(_make_label("Select Category:", bold=True))
    cb_cat = _make_combobox(cat_names)
    content.Children.Add(cb_cat)
    main_panel.Children.Add(content)

    footer = StackPanel()
    footer.Orientation = Orientation.Horizontal
    footer.HorizontalAlignment = HorizontalAlignment.Right
    footer.Margin = Thickness(16, 6, 16, 14)

    btn = Button()
    btn.Content = "  Next  "
    btn.Padding = Thickness(16, 6, 16, 6)
    btn.Background = DQT_GOLD
    btn.Foreground = DQT_DARK
    btn.FontWeight = FontWeights.SemiBold
    btn.IsDefault = True

    result = {"value": None}

    def on_click(sender, args):
        if cb_cat.SelectedItem:
            result["value"] = cb_cat.SelectedItem.Content
        win.Close()

    btn.Click += on_click
    footer.Children.Add(btn)
    main_panel.Children.Add(footer)

    copy_lbl = TextBlock()
    copy_lbl.Text = "Copyright by Dang Quoc Truong - DQT (c) 2026"
    copy_lbl.FontSize = 10
    copy_lbl.Foreground = DQT_ACCENT
    copy_lbl.HorizontalAlignment = HorizontalAlignment.Center
    copy_lbl.Margin = Thickness(0, 0, 0, 8)
    main_panel.Children.Add(copy_lbl)

    win.Content = main_panel
    win.ShowDialog()
    return result["value"]


def show_parameter_dialog(param_names, cat_name):
    win = Window()
    win.Title = "DQT - Renumber Settings"
    win.SizeToContent = SizeToContent.WidthAndHeight
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen
    win.ResizeMode = System.Windows.ResizeMode.NoResize
    win.Background = DQT_CREAM

    main_panel = StackPanel()
    main_panel.Margin = Thickness(0)

    header = TextBlock()
    header.Text = "Numbering Settings - " + cat_name
    header.FontSize = 15
    header.FontWeight = FontWeights.Bold
    header.Foreground = DQT_DARK
    header.Background = DQT_GOLD
    header.Padding = Thickness(16, 10, 16, 10)
    main_panel.Children.Add(header)

    content = StackPanel()
    content.Margin = Thickness(16, 10, 16, 10)

    content.Children.Add(_make_label("Parameter to Write:", bold=True))
    cb_param = _make_combobox(param_names)
    content.Children.Add(cb_param)

    content.Children.Add(_make_label("Prefix:", bold=True))
    tb_prefix = _make_textbox("X00_")
    content.Children.Add(tb_prefix)

    content.Children.Add(_make_label("Leading Zeros (number of digits):", bold=True))
    tb_leading = _make_textbox("3")
    content.Children.Add(tb_leading)

    content.Children.Add(_make_label("Starting Number:", bold=True))
    tb_start = _make_textbox("1")
    content.Children.Add(tb_start)

    main_panel.Children.Add(content)

    footer = StackPanel()
    footer.Orientation = Orientation.Horizontal
    footer.HorizontalAlignment = HorizontalAlignment.Right
    footer.Margin = Thickness(16, 6, 16, 14)

    btn = Button()
    btn.Content = "  Renumber  "
    btn.Padding = Thickness(16, 6, 16, 6)
    btn.Background = DQT_GOLD
    btn.Foreground = DQT_DARK
    btn.FontWeight = FontWeights.SemiBold
    btn.IsDefault = True

    result = {"param": None, "prefix": "", "leading": 3, "start": 1}

    def on_click(sender, args):
        if cb_param.SelectedItem:
            result["param"] = cb_param.SelectedItem.Content
        result["prefix"] = tb_prefix.Text if tb_prefix.Text else ""
        try: result["leading"] = int(tb_leading.Text)
        except: result["leading"] = 0
        try: result["start"] = int(tb_start.Text)
        except: result["start"] = 1
        win.Close()

    btn.Click += on_click
    footer.Children.Add(btn)
    main_panel.Children.Add(footer)

    copy_lbl = TextBlock()
    copy_lbl.Text = "Copyright by Dang Quoc Truong - DQT (c) 2026"
    copy_lbl.FontSize = 10
    copy_lbl.Foreground = DQT_ACCENT
    copy_lbl.HorizontalAlignment = HorizontalAlignment.Center
    copy_lbl.Margin = Thickness(0, 0, 0, 8)
    main_panel.Children.Add(copy_lbl)

    win.Content = main_panel
    win.ShowDialog()
    return result


def run():
    doc = revit.doc
    uidoc = revit.uidoc

    cat_dict = get_available_categories(doc)
    if not cat_dict:
        forms.alert("No suitable categories found in the project.")
        return

    cat_names = sorted(cat_dict.keys())
    selected_cat_name = show_category_dialog(cat_names)
    if not selected_cat_name:
        return

    selected_cat = cat_dict[selected_cat_name]
    bic = get_bic_from_category(selected_cat)

    param_names = get_text_parameters(doc, bic)
    if not param_names:
        forms.alert("No writable text parameters found for '{}'.".format(selected_cat_name))
        return

    settings = show_parameter_dialog(param_names, selected_cat_name)
    if not settings["param"]:
        forms.alert("No parameter selected.")
        return

    param_name = settings["param"]
    prefix = settings["prefix"]
    leading = settings["leading"]
    start_count = settings["start"]

    try:
        forms.alert("Select a spline or line.\nThe start of the line defines the first element.", title="DQT - Select Spline")
        spline_ref = uidoc.Selection.PickObject(ObjectType.Element, LineSelectionFilter(), "Select a Spline or Line")
        spline_elem = doc.GetElement(spline_ref)
    except:
        forms.alert("Selection cancelled - no spline selected.")
        return

    spline_curve = get_curve_from_element(spline_elem)
    if not spline_curve:
        forms.alert("Selected element has no valid curve geometry.")
        return

    try:
        forms.alert("Select all elements to renumber.\nCategory: {}".format(selected_cat_name), title="DQT - Select Elements")
        bic_int = _eid_int(selected_cat.Id)
        element_refs = uidoc.Selection.PickObjects(ObjectType.Element, CategorySelectionFilter(bic_int), "Select Elements to Renumber")
    except:
        forms.alert("Selection cancelled - no elements selected.")
        return

    if not element_refs or len(element_refs) == 0:
        forms.alert("No elements selected.")
        return

    el_param_list = []
    skipped = 0

    for eref in element_refs:
        elem = doc.GetElement(eref)
        pt = get_element_location(elem, doc)
        if not pt:
            skipped += 1
            continue

        try:
            proj_result = spline_curve.Project(pt)
            if proj_result:
                norm_param = spline_curve.ComputeNormalizedParameter(proj_result.Parameter)
                el_param_list.append((elem, norm_param))
            else:
                skipped += 1
        except Exception:
            skipped += 1

    if not el_param_list:
        forms.alert("Could not project any elements onto the spline.")
        return

    el_param_list.sort(key=lambda x: x[1])

    counter = start_count
    failed = 0

    with disposing(Transaction(doc, "DQT - Renumber Along Spline")) as t:
        t.Start()
        for elem, _ in el_param_list:
            if leading and leading > 0:
                num_str = str(counter).zfill(leading)
            else:
                num_str = str(counter)
            value = prefix + num_str

            p = elem.LookupParameter(param_name)
            if p and not p.IsReadOnly:
                try: p.Set(value)
                except: failed += 1
            else:
                failed += 1
            counter += 1
        t.Commit()

    total = len(el_param_list)
    success = total - failed
    msg = "{} of {} {} renumbered successfully.".format(success, total, selected_cat_name)
    if skipped > 0:
        msg += "\n{} element(s) skipped (no valid location).".format(skipped)
    if failed > 0:
        msg += "\n{} element(s) failed to write parameter.".format(failed)

    forms.alert(msg, title="DQT - Renumber Complete")
