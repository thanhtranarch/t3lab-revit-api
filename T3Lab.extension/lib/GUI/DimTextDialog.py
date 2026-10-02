# -*- coding: utf-8 -*-
"""
Dim Text

Edit dimension text overrides on selected dimensions,
with optional segment-length filter rules (AND / OR).

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Dim Text"
__version__ = "2.1.0"

# ── IMPORTS ───────────────────────────────────────────────────────────────────
import os
import clr
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('System')

import System
from System.Windows import WindowState, Thickness, Visibility, VerticalAlignment
from System.Windows.Controls import (
    StackPanel, ComboBox, ComboBoxItem, TextBox, Button, TextBlock
)
from System.Windows.Controls import Orientation as WPFOrientation
from Autodesk.Revit.DB import Dimension, FilteredElementCollector, Transaction
from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow
from Snippets._units import project_length_unit

# ── VARIABLES ─────────────────────────────────────────────────────────────────
# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
# point report the real problem.
try:
    uidoc = revit.uidoc
except Exception:
    uidoc = None
try:
    doc = revit.doc
except Exception:
    doc = None
# Length unit of the project: segment-length rules are typed and labelled in
# it. Re-read with the document per launch (show_dialog here, and
# ManaAnnoDialog._refresh_active_document for the Dim Text page).
unit = project_length_unit(doc)
logger = script.get_logger()

XAML_PATH = os.path.join(os.path.dirname(__file__), "Tools", "DimText.xaml")

_OPERATORS = [
    "equals",
    "does not equal",
    "is greater than",
    "is greater than or equal to",
    "is less than",
    "is less than or equal to",
    "between",
    "has a value",
    "has no value",
]

# operators that need no value input at all
_NO_VALUE_OPS  = {"has a value", "has no value"}
# operators that need two value inputs
_TWO_VALUE_OPS = {"between"}


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _length_of(item):
    """Value of a Dimension / DimensionSegment (Nullable<double>, internal
    feet) as a Python float, or None when it has no value."""
    value = item.Value
    return None if value is None else float(value)


def _set_dim_text(dim, prefix, suffix, above, below, override, filter_fn=None):
    """Apply text overrides, optionally only to the segments whose length
    passes `filter_fn` (internal feet, as Revit reports Value)."""
    if dim.HasOneSegment():
        length = _length_of(dim)
        if filter_fn is None or (length is not None and filter_fn(length)):
            dim.Prefix        = prefix
            dim.Suffix        = suffix
            dim.Above         = above
            dim.Below         = below
            dim.ValueOverride = override
    else:
        for seg in dim.Segments:
            length = _length_of(seg)
            if filter_fn is None or (length is not None and filter_fn(length)):
                seg.Prefix        = prefix
                seg.Suffix        = suffix
                seg.Above         = above
                seg.Below         = below
                seg.ValueOverride = override


def _turn_off_leader(dim):
    for para in dim.GetOrderedParameters():
        if para.Definition.Name == "Leader":
            para.Set(0)
            break


def _get_dims_in_view():
    return list(
        FilteredElementCollector(doc, uidoc.ActiveView.Id)
        .OfClass(Dimension)
        .ToElements()
    )


def _get_selected_dims():
    return [
        doc.GetElement(eid)
        for eid in uidoc.Selection.GetElementIds()
        if isinstance(doc.GetElement(eid), Dimension)
    ]


# ── WINDOW ────────────────────────────────────────────────────────────────────
# ── SHARED RULE-ROW / FILTER / APPLY (dùng chung với ManaAnno > DimText) ──
def _t3_style(host, key):
    """Style T3 từ window chủ; None nếu không có (không bao giờ ném)."""
    try:
        return host.TryFindResource(key)
    except Exception:
        return None


def _length_unit(length_unit=None):
    """The unit rule values are in: the caller's, else this launch's project unit."""
    return length_unit if length_unit is not None else unit


def _value_tooltip(length_unit):
    """Tooltip of a rule value box: the unit a bare number is in, and that a
    typed unit always wins."""
    return (u"Length in {}. A typed unit wins: 1200 mm, 1.2 m, 3'-6\" "
            u"or 42\".").format(length_unit.tag)


def create_rule_row(host, on_remove, length_unit=None):
    """Một dòng rule lọc theo chiều dài: toán tử · giá trị · (and giá trị) · Remove.

    Mọi màu / font / cỡ lấy từ style T3 của `host` — không hardcode.
    `on_remove(rd)` được gọi khi bấm Remove. Nhãn đơn vị cạnh ô giá trị là đơn
    vị chiều dài của project (`length_unit`, mặc định: unit của lần mở này).
    """
    length_unit = _length_unit(length_unit)
    rd = {}
    row = StackPanel()
    row.Orientation = WPFOrientation.Horizontal
    row.Margin = Thickness(0, 0, 0, 8)
    rd["panel"] = row

    combo = ComboBox()
    combo.Style = _t3_style(host, "T3.ComboBox")
    combo.Width = 200
    combo.Margin = Thickness(0, 0, 8, 0)
    for op in _OPERATORS:
        item = ComboBoxItem()
        item.Content = op
        combo.Items.Add(item)
    combo.SelectedIndex = 0
    rd["combo"] = combo
    row.Children.Add(combo)

    def _value_box():
        box = TextBox()
        box.Style = _t3_style(host, "T3.TextBox.Mono")
        box.Width = 96                     # fits 12' - 6 1/2" in a ft-in project
        box.Margin = Thickness(0, 0, 4, 0)
        box.ToolTip = _value_tooltip(length_unit)
        return box

    def _caption(text):
        lbl = TextBlock()
        lbl.Style = _t3_style(host, "T3.Caption")
        lbl.Text = text
        lbl.Margin = Thickness(0, 0, 8, 0)
        lbl.VerticalAlignment = VerticalAlignment.Center
        return lbl

    rd["txt1"] = _value_box()
    rd["lbl_unit"] = _caption(length_unit.tag)
    rd["lbl_and"] = _caption("and")
    rd["txt2"] = _value_box()
    rd["lbl_unit2"] = _caption(length_unit.tag)
    for key in ("txt1", "lbl_unit", "lbl_and", "txt2", "lbl_unit2"):
        row.Children.Add(rd[key])

    btn = Button()
    btn.Style = _t3_style(host, "T3.Button.Ghost")
    btn.Content = "Remove"
    btn.ToolTip = "Remove this rule"
    btn.Click += lambda s, e: on_remove(rd)
    row.Children.Add(btn)

    def _sync(sender=None, args=None):
        sel = combo.SelectedItem
        op = sel.Content if sel is not None else ""
        v1 = Visibility.Collapsed if op in _NO_VALUE_OPS else Visibility.Visible
        v2 = Visibility.Visible if op in _TWO_VALUE_OPS else Visibility.Collapsed
        rd["txt1"].Visibility = rd["lbl_unit"].Visibility = v1
        rd["lbl_and"].Visibility = rd["txt2"].Visibility = rd["lbl_unit2"].Visibility = v2

    combo.SelectionChanged += _sync
    _sync()
    return rd


def _rule_length(box, length_unit, number):
    """A rule value box → internal feet. A bare number is in the project unit,
    a typed unit wins ("1200 mm", "3'-6\""). Not a length → ValueError that
    names the rule, so the caller shows it and changes nothing."""
    try:
        return length_unit.parse(box.Text)
    except ValueError as error:
        raise ValueError(u"Length rule {}: {}".format(number, error))


def build_filter_fn(rules, use_and, length_unit=None):
    """Hàm lọc theo chiều dài segment từ danh sách rule; None nếu không có rule.

    Giá trị gõ theo đơn vị chiều dài của project (`length_unit`); hàm lọc nhận
    chiều dài nội bộ của Revit (feet). "equals" = đọc ra giống nhau trong đơn vị
    project (mm: ±0.5 mm, ft-in: ±1/16"). Giá trị không phải chiều dài →
    ValueError (nêu số thứ tự rule) — người gọi báo lỗi, không ghi gì.
    """
    length_unit = _length_unit(length_unit)
    parsed = []
    for number, rd in enumerate(rules, 1):
        sel = rd["combo"].SelectedItem
        if sel is None:
            continue
        op = sel.Content
        v1 = None if op in _NO_VALUE_OPS else _rule_length(rd["txt1"], length_unit, number)
        v2 = _rule_length(rd["txt2"], length_unit, number) if op in _TWO_VALUE_OPS else None
        shown = length_unit.text(v1) if v1 is not None else None
        parsed.append((op, v1, v2, shown))
    if not parsed:
        return None

    def filter_fn(length):
        """`length` in internal feet (Dimension / DimensionSegment .Value)."""
        if length is None:
            return False
        results = []
        for op, v1, v2, shown in parsed:
            if   op == "equals":                      results.append(length_unit.text(length) == shown)
            elif op == "does not equal":              results.append(length_unit.text(length) != shown)
            elif op == "is greater than":             results.append(length >  v1)
            elif op == "is greater than or equal to": results.append(length >= v1)
            elif op == "is less than":                results.append(length <  v1)
            elif op == "is less than or equal to":    results.append(length <= v1)
            elif op == "between":                     results.append(min(v1, v2) <= length <= max(v1, v2))
            elif op == "has a value":                 results.append(True)
            elif op == "has no value":                results.append(False)
        if not results:
            return True
        return all(results) if use_and else any(results)

    return filter_fn


def apply_dim_text(dims, prefix, suffix, above, below, override,
                   leader_off=False, filter_fn=None):
    """Ghi override cho `dims` trong MỘT transaction; lỗi → rollback rồi ném lại."""
    t = Transaction(doc, "Dim Text Override")
    t.Start()
    try:
        for dim in dims:
            _set_dim_text(dim, prefix, suffix, above, below, override, filter_fn)
            if leader_off:
                _turn_off_leader(dim)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


class DimTextWindow(T3WPFWindow):

    def __init__(self):
        T3WPFWindow.__init__(self, XAML_PATH)
        self._unit = unit  # project length unit, re-read by show_dialog per launch
        self._rules = []  # list of dicts: {panel, combo, txt1, txt2, lbl_unit, lbl_and, lbl_unit2}
        # Pre-cache all named controls immediately so they remain accessible after
        # the content grid is detached from this Window and embedded into a parent.
        for _n in ("txt_prefix", "txt_suffix", "txt_above", "txt_below", "txt_override",
                   "wrap_presets", "chk_leader", "rb_selection", "rb_view",
                   "chk_filter_enable", "sp_filter_config", "combo_combine",
                   "sp_rules", "btn_clear_fields", "btn_apply", "lbl_status"):
            setattr(self, _n, self.FindName(_n))

    # ── window chrome ──────────────────────────────────────────────────────────
    def minimize_button_clicked(self, sender, args):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, args):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def close_button_clicked(self, sender, args):
        self.Close()

    # ── presets ────────────────────────────────────────────────────────────────
    def preset_below_clicked(self, sender, args):
        self.txt_below.Text = sender.Tag

    # ── clear fields ───────────────────────────────────────────────────────────
    def clear_fields_clicked(self, sender, args):
        self.txt_prefix.Text   = ""
        self.txt_suffix.Text   = ""
        self.txt_above.Text    = ""
        self.txt_below.Text    = ""
        self.txt_override.Text = ""
        self.lbl_status.Text   = "Fields cleared."

    # ── filter section ─────────────────────────────────────────────────────────
    def filter_toggle(self, sender, args):
        if self.chk_filter_enable.IsChecked:
            self.sp_filter_config.Visibility = Visibility.Visible
        else:
            self.sp_filter_config.Visibility = Visibility.Collapsed

    def add_rule_clicked(self, sender, args):
        rd = self._create_rule_row()
        self._rules.append(rd)
        self.sp_rules.Children.Add(rd["panel"])

    def _create_rule_row(self):
        return create_rule_row(self, self._remove_rule, self._unit)

    def _remove_rule(self, rd):
        self.sp_rules.Children.Remove(rd["panel"])
        if rd in self._rules:
            self._rules.remove(rd)

    def _build_filter_fn(self):
        if not self.chk_filter_enable.IsChecked or not self._rules:
            return None
        return build_filter_fn(self._rules, self.combo_combine.SelectedIndex == 0,
                               self._unit)

    # ── apply ──────────────────────────────────────────────────────────────────
    def apply_clicked(self, sender, args):
        prefix   = self.txt_prefix.Text.strip()
        suffix   = self.txt_suffix.Text.strip()
        above    = self.txt_above.Text.strip()
        below    = self.txt_below.Text.strip()
        override = self.txt_override.Text.strip()
        leader_off = bool(self.chk_leader.IsChecked)
        try:
            filter_fn = self._build_filter_fn()
        except ValueError as ex:
            self.lbl_status.Text = u"{} Nothing was changed.".format(ex)
            return

        if self.rb_view.IsChecked:
            dims = _get_dims_in_view()
            scope_label = "view"
        else:
            dims = _get_selected_dims()
            scope_label = "selection"

        if not dims:
            self.lbl_status.Text = "No dimensions found in {}. Select dimensions or switch to 'All dims in active view'.".format(scope_label)
            return

        try:
            apply_dim_text(dims, prefix, suffix, above, below, override, leader_off, filter_fn)
        except Exception as ex:
            self.lbl_status.Text = "Dim text not changed: {}. Nothing was modified.".format(ex)
            logger.error("DimText failed: {}".format(ex))
            return

        filter_note = " (length filter active)" if filter_fn else ""
        self.lbl_status.Text = "Applied to {} dim(s) in {}{}.".format(
            len(dims), scope_label, filter_note
        )
        logger.info("DimText applied: {} dims, scope={}, filter={}".format(
            len(dims), scope_label, filter_fn is not None
        ))


def show_dialog():
    # Imported once per Revit session: re-read the active document so the
    # window never works on the project that was open at first import. The
    # length unit belongs to the document, so it is re-read with it.
    global doc, uidoc, unit
    try:
        uidoc = revit.uidoc
    except Exception:
        uidoc = None
    try:
        doc = revit.doc
    except Exception:
        doc = None
    unit = project_length_unit(doc)
    DimTextWindow().ShowDialog()

if __name__ == '__main__':
    show_dialog()
