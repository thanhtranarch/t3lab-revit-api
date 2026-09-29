# -*- coding: utf-8 -*-
"""Copy Annotations Between Models - Dialog

Copy annotations (dimensions, tags, text notes, detail items, detail lines, etc.)
from views in the active document to matching views in another open document.

Dang Quoc Truong - DQT (c) 2026
"""

import clr
import System
import os
import sys

clr.AddReference('System')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Collections.Generic import List
from System.Windows import (
    Window, Thickness, HorizontalAlignment, VerticalAlignment,
    TextWrapping, Visibility, GridLength, GridUnitType,
    WindowStartupLocation, ResizeMode, CornerRadius,
)
from System.Windows.Controls import (
    StackPanel, DockPanel, TextBlock, Button, CheckBox,
    ComboBox, ComboBoxItem, ScrollViewer, Border, ProgressBar,
    Orientation, ScrollBarVisibility, Dock, TextBox,
    Grid as WPFGrid, ColumnDefinition, RowDefinition,
)
from System.Windows.Media import BrushConverter, FontFamily
from System.Windows.Input import Key

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    FilteredElementCollector, ElementTransformUtils, CopyPasteOptions,
    Transaction, ViewType, BuiltInCategory, IndependentTag,
    IDuplicateTypeNamesHandler, DuplicateTypeAction,
    IFailuresPreprocessor, FailureProcessingResult,
)

from pyrevit import forms, script, revit
from Snippets._compat import disposing

# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
try:
    from Snippets._host import resolve_doc, resolve_uidoc
except ImportError:
    try:
        import importlib
        import Snippets._host
        importlib.reload(Snippets._host)
        from Snippets._host import resolve_doc, resolve_uidoc
    except Exception:
        from Snippets._host import resolve_doc
        def resolve_uidoc(candidate=None):
            if candidate is not None and hasattr(candidate, 'Document') and candidate.Document is not None:
                return candidate
            try:
                from pyrevit import revit
                return revit.uidoc
            except Exception:
                return None

try:
    uidoc = resolve_uidoc(getattr(revit, 'uidoc', None))
except Exception:
    uidoc = None
try:
    doc = resolve_doc(getattr(revit, 'doc', None)).doc
except Exception:
    doc = None


def _get_app(target_doc=None):
    d = target_doc or resolve_doc(doc)
    if isinstance(d, tuple):
        d = d[0]
    if d and hasattr(d, 'Application'):
        return d.Application
    try:
        if '__revit__' in globals():
            return __revit__.Application
    except Exception:
        pass
    return None

# ==============================================================================
# BRAND COLORS
# ==============================================================================
CLR_HEADER      = "#0F172A"
CLR_HEADER_TEXT = "#0F172A"
CLR_HEADER_SUB  = "#475569"
CLR_ACCENT      = "#5D4E37"
CLR_BG          = "#F8FAFC"
CLR_WHITE       = "#FFFFFF"
CLR_BORDER      = "#CBD5E1"
CLR_FOOTER      = "#F5F0E0"
CLR_TEXT        = "#0F172A"
CLR_MUTED       = "#94A3B8"
CLR_STATUS_BG   = "#FFFBF0"
CLR_ERROR       = "#EF4444"
CLR_SUCCESS     = "#10B981"
CLR_WARNING     = "#F59E0B"
CLR_BTN_PRI_BG  = "#5D4E37"
CLR_BTN_PRI_FG  = "#0F172A"
CLR_BTN_SEC_BG  = "#FFFFFF"
CLR_BTN_SEC_BD  = "#CBD5E1"
CLR_SCAN_BG     = "#5D4E37"

FONT = FontFamily("Segoe UI")
def _b(h):
    return BrushConverter().ConvertFromString(h)

TAGS_ALL_KEY = "TAGS_ALL"

ANNOTATION_CATEGORIES = [
    BuiltInCategory.OST_Dimensions,
    TAGS_ALL_KEY,
    BuiltInCategory.OST_TextNotes,
    BuiltInCategory.OST_DetailComponents,
    BuiltInCategory.OST_Lines,
    BuiltInCategory.OST_GenericAnnotation,
    BuiltInCategory.OST_SpotElevations,
    BuiltInCategory.OST_SpotCoordinates,
    BuiltInCategory.OST_SpotSlopes,
    BuiltInCategory.OST_FilledRegion,
    BuiltInCategory.OST_InsulationLines,
    BuiltInCategory.OST_Matchline,
    BuiltInCategory.OST_ReferenceLines,
]

CATEGORY_NAMES = {
    BuiltInCategory.OST_Dimensions:       "Dimensions",
    TAGS_ALL_KEY:                          "Tags (Room/Door/Wall/...)",
    BuiltInCategory.OST_TextNotes:        "Text Notes",
    BuiltInCategory.OST_DetailComponents: "Detail Components",
    BuiltInCategory.OST_Lines:            "Detail Lines",
    BuiltInCategory.OST_GenericAnnotation:"Generic Annotations",
    BuiltInCategory.OST_SpotElevations:   "Spot Elevations",
    BuiltInCategory.OST_SpotCoordinates:  "Spot Coordinates",
    BuiltInCategory.OST_SpotSlopes:       "Spot Slopes",
    BuiltInCategory.OST_FilledRegion:     "Filled Regions",
    BuiltInCategory.OST_InsulationLines:  "Insulation Lines",
    BuiltInCategory.OST_Matchline:        "Matchlines",
    BuiltInCategory.OST_ReferenceLines:   "Reference Lines",
}

DEFAULT_CHECKED = [
    BuiltInCategory.OST_Dimensions,
    TAGS_ALL_KEY,
    BuiltInCategory.OST_TextNotes,
    BuiltInCategory.OST_DetailComponents,
    BuiltInCategory.OST_Lines,
    BuiltInCategory.OST_GenericAnnotation,
    BuiltInCategory.OST_FilledRegion,
]

VALID_VIEW_TYPES = [
    ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan,
    ViewType.AreaPlan, ViewType.Elevation, ViewType.Section,
    ViewType.DraftingView, ViewType.Detail,
]

SORT_OPTIONS = [
    "Name (A-Z)",
    "Name (Z-A)",
    "View Type",
]

class UseDestinationHandler(IDuplicateTypeNamesHandler):
    def OnDuplicateTypeNamesFound(self, args):
        return DuplicateTypeAction.UseDestinationTypes

class SilentFailurePreprocessor(IFailuresPreprocessor):
    __namespace__ = "T3Lab.CopyAnnotation"

    def PreprocessFailures(self, fa):
        for f in fa.GetFailureMessages():
            fa.DeleteWarning(f)
        return FailureProcessingResult.Continue

def get_all_documents():
    current_app = _get_app()
    if not current_app:
        d = resolve_doc(doc)
        if isinstance(d, tuple):
            d = d[0]
        return [d] if d else []
    docs = []
    for d in current_app.Documents:
        if not d.IsLinked and not d.IsFamilyDocument:
            docs.append(d)
    return docs

def get_valid_views(document):
    views = []
    for v in FilteredElementCollector(document).OfClass(DB.View).WhereElementIsNotElementType():
        if not v.IsTemplate and v.ViewType in VALID_VIEW_TYPES:
            views.append(v)
    return sorted(views, key=lambda v: (str(v.ViewType), v.Name))

def _collect_tag_ids(document, view_id):
    ids = []
    seen = set()
    try:
        for eid in FilteredElementCollector(document, view_id) \
                .OfClass(IndependentTag).ToElementIds():
            h = eid.GetHashCode()
            if h not in seen:
                seen.add(h)
                ids.append(eid)
    except:
        pass
    for sc in [BuiltInCategory.OST_RoomTags, BuiltInCategory.OST_AreaTags,
               BuiltInCategory.OST_MEPSpaceTags]:
        try:
            for eid in FilteredElementCollector(document, view_id) \
                    .OfCategory(sc).WhereElementIsNotElementType().ToElementIds():
                h = eid.GetHashCode()
                if h not in seen:
                    seen.add(h)
                    ids.append(eid)
        except:
            pass
    return ids

def count_annotations(document, view_id, categories):
    total = 0
    for cat in categories:
        try:
            if cat == TAGS_ALL_KEY:
                total += len(_collect_tag_ids(document, view_id))
            else:
                total += FilteredElementCollector(document, view_id) \
                    .OfCategory(cat).WhereElementIsNotElementType().GetElementCount()
        except:
            pass
    return total

def collect_annotation_ids(document, view_id, categories):
    all_ids = []
    seen = set()
    for cat in categories:
        try:
            if cat == TAGS_ALL_KEY:
                for eid in _collect_tag_ids(document, view_id):
                    h = eid.GetHashCode()
                    if h not in seen:
                        seen.add(h)
                        all_ids.append(eid)
            else:
                for eid in FilteredElementCollector(document, view_id) \
                        .OfCategory(cat).WhereElementIsNotElementType().ToElementIds():
                    h = eid.GetHashCode()
                    if h not in seen:
                        seen.add(h)
                        all_ids.append(eid)
        except:
            pass
    return all_ids

def match_views(source_views, dest_views):
    dest_dict = {}
    for v in dest_views:
        dest_dict[v.Name] = v
    matched = []
    for sv in source_views:
        if sv.Name in dest_dict:
            matched.append((sv, dest_dict[sv.Name]))
    return matched

def view_type_label(vtype):
    s = str(vtype)
    s = s.replace("FloorPlan", "Plan").replace("CeilingPlan", "RCP")
    s = s.replace("EngineeringPlan", "Str.Plan").replace("DraftingView", "Drafting")
    return s

def lbl(text, size=12, bold=False, color=CLR_TEXT):
    tb = TextBlock()
    tb.Text = text
    tb.FontSize = size
    tb.FontFamily = FONT
    tb.Foreground = _b(color)
    if bold:
        tb.FontWeight = System.Windows.FontWeights.SemiBold
    return tb

def btn(text, width=80, height=26, primary=False):
    b = Button()
    b.Content = text
    b.MinWidth = width
    b.Height = height
    b.FontSize = 11
    b.FontFamily = FONT
    if primary:
        b.Background = _b(CLR_BTN_PRI_BG)
        b.Foreground = _b(CLR_BTN_PRI_FG)
        b.FontWeight = System.Windows.FontWeights.Bold
        b.FontSize = 13
    else:
        b.Background = _b(CLR_BTN_SEC_BG)
        b.Foreground = _b(CLR_ACCENT)
        b.BorderBrush = _b(CLR_BORDER)
    return b

def section(text):
    t = lbl(text, 12, True, CLR_ACCENT)
    t.Margin = Thickness(0, 10, 0, 4)
    return t

class CopyAnnotationsWindow(Window):
    def __init__(self):
        self.Title = "DQT - Copy Annotations Between Models"
        self.Width = 720
        self.Height = 760
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.CanResizeWithGrip
        self.Background = _b(CLR_BG)
        self.MinHeight = 550
        self.MinWidth = 550

        self.all_docs = get_all_documents()
        self.source_doc = doc
        self.dest_doc = None
        self.cat_cbs = []
        self.view_cbs = []
        self.matched_pairs = []
        self._build_ui()

    def _build_ui(self):
        root = DockPanel()
        root.LastChildFill = True

        hdr = Border()
        hdr.Background = _b(CLR_HEADER)
        hdr.Padding = Thickness(20, 14, 20, 14)
        hdr.CornerRadius = CornerRadius(0, 0, 4, 4)
        hp = StackPanel()
        t1 = TextBlock()
        t1.Text = "Copy Annotations Between Models"
        t1.FontSize = 18
        t1.FontWeight = System.Windows.FontWeights.Bold
        t1.Foreground = _b(CLR_HEADER_TEXT)
        t1.FontFamily = FONT
        hp.Children.Add(t1)
        t2 = lbl("Transfer dims, tags, text notes & details across documents", 10, False, CLR_HEADER_SUB)
        t2.Margin = Thickness(0, 2, 0, 0)
        hp.Children.Add(t2)
        hdr.Child = hp
        DockPanel.SetDock(hdr, Dock.Top)
        root.Children.Add(hdr)

        ftr = Border()
        ftr.Background = _b(CLR_FOOTER)
        ftr.Padding = Thickness(16, 10, 16, 10)
        ftr.BorderBrush = _b(CLR_BORDER)
        ftr.BorderThickness = Thickness(0, 1, 0, 0)
        fd = DockPanel()

        cr = lbl("DQT - Dang Quoc Truong \xa9 2026", 9, False, CLR_MUTED)
        cr.VerticalAlignment = VerticalAlignment.Center
        DockPanel.SetDock(cr, Dock.Left)
        fd.Children.Add(cr)

        bp = StackPanel()
        bp.Orientation = Orientation.Horizontal
        bp.HorizontalAlignment = HorizontalAlignment.Right

        bc = btn("Close", 70, 30)
        bc.Margin = Thickness(0, 0, 8, 0)
        bc.Click += self._close
        bp.Children.Add(bc)

        self.btn_copy = btn("Copy Annotations", 160, 30, True)
        self.btn_copy.Click += self._copy_click
        bp.Children.Add(self.btn_copy)

        DockPanel.SetDock(bp, Dock.Right)
        fd.Children.Add(bp)
        ftr.Child = fd
        DockPanel.SetDock(ftr, Dock.Bottom)
        root.Children.Add(ftr)

        sb = Border()
        sb.Background = _b(CLR_STATUS_BG)
        sb.Padding = Thickness(16, 6, 16, 6)
        sb.BorderBrush = _b(CLR_BORDER)
        sb.BorderThickness = Thickness(0, 1, 0, 0)
        stp = StackPanel()
        self.lbl_status = lbl("Step 1: Select models > Step 2: Select views > Step 3: Scan > Step 4: Copy", 10, False, CLR_MUTED)
        self.lbl_status.TextWrapping = TextWrapping.Wrap
        stp.Children.Add(self.lbl_status)
        self.progress = ProgressBar()
        self.progress.Height = 4
        self.progress.Margin = Thickness(0, 4, 0, 0)
        self.progress.Visibility = Visibility.Collapsed
        self.progress.Foreground = _b(CLR_HEADER)
        stp.Children.Add(self.progress)
        sb.Child = stp
        DockPanel.SetDock(sb, Dock.Bottom)
        root.Children.Add(sb)

        bscr = ScrollViewer()
        bscr.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        bscr.Padding = Thickness(20, 8, 20, 8)
        body = StackPanel()

        body.Children.Add(section("Source Model (copy FROM):"))
        self.cb_src = ComboBox()
        self.cb_src.Height = 28
        self.cb_src.FontSize = 11
        self.cb_src.FontFamily = FONT
        self._fill_combo(self.cb_src, doc.Title)
        self.cb_src.SelectionChanged += self._doc_changed
        body.Children.Add(self.cb_src)

        body.Children.Add(section("Destination Model (copy TO):"))
        self.cb_dst = ComboBox()
        self.cb_dst.Height = 28
        self.cb_dst.FontSize = 11
        self.cb_dst.FontFamily = FONT
        dd = ""
        for d in self.all_docs:
            if d.Title != doc.Title:
                dd = d.Title
                break
        self._fill_combo(self.cb_dst, dd)
        self.cb_dst.SelectionChanged += self._doc_changed
        body.Children.Add(self.cb_dst)

        sr = StackPanel()
        sr.Orientation = Orientation.Horizontal
        sr.Margin = Thickness(0, 4, 0, 0)
        bsw = btn("Swap Source / Dest", 140, 24)
        bsw.Click += self._swap
        sr.Children.Add(bsw)
        body.Children.Add(sr)

        body.Children.Add(section("Annotation Categories:"))
        cb_border = Border()
        cb_border.BorderBrush = _b(CLR_BORDER)
        cb_border.BorderThickness = Thickness(1)
        cb_border.CornerRadius = CornerRadius(4)
        cb_border.Background = _b(CLR_WHITE)
        cb_border.Padding = Thickness(10, 8, 10, 8)

        cg = WPFGrid()
        for _ in range(3):
            cd = ColumnDefinition()
            cd.Width = GridLength(1, GridUnitType.Star)
            cg.ColumnDefinitions.Add(cd)
        rc = (len(ANNOTATION_CATEGORIES) + 2) // 3
        for _ in range(rc):
            rd = RowDefinition()
            rd.Height = GridLength(24)
            cg.RowDefinitions.Add(rd)
        for idx in range(len(ANNOTATION_CATEGORIES)):
            cat = ANNOTATION_CATEGORIES[idx]
            c = CheckBox()
            c.Content = CATEGORY_NAMES.get(cat, str(cat))
            c.Tag = cat
            if cat in DEFAULT_CHECKED:
                c.IsChecked = True
            else:
                c.IsChecked = False
            c.FontSize = 10
            c.Foreground = _b(CLR_TEXT)
            c.FontFamily = FONT
            c.Margin = Thickness(2, 1, 2, 1)
            WPFGrid.SetRow(c, idx // 3)
            WPFGrid.SetColumn(c, idx % 3)
            cg.Children.Add(c)
            self.cat_cbs.append(c)
        cb_border.Child = cg
        body.Children.Add(cb_border)

        cbp = StackPanel()
        cbp.Orientation = Orientation.Horizontal
        cbp.Margin = Thickness(0, 4, 0, 0)
        ba = btn("All", 45, 22)
        ba.Click += self._cat_all
        cbp.Children.Add(ba)
        bn = btn("None", 45, 22)
        bn.Margin = Thickness(4, 0, 0, 0)
        bn.Click += self._cat_none
        cbp.Children.Add(bn)
        body.Children.Add(cbp)

        vtr = StackPanel()
        vtr.Orientation = Orientation.Horizontal
        vt = section("Matched Views:")
        vtr.Children.Add(vt)
        self.lbl_count = lbl("(0)", 11, False, CLR_MUTED)
        self.lbl_count.VerticalAlignment = VerticalAlignment.Bottom
        self.lbl_count.Margin = Thickness(8, 0, 0, 4)
        vtr.Children.Add(self.lbl_count)
        body.Children.Add(vtr)

        search_row = WPFGrid()
        c1 = ColumnDefinition()
        c1.Width = GridLength(1, GridUnitType.Star)
        c2 = ColumnDefinition()
        c2.Width = GridLength(150, GridUnitType.Pixel)
        search_row.ColumnDefinitions.Add(c1)
        search_row.ColumnDefinitions.Add(c2)
        search_row.Margin = Thickness(0, 0, 0, 4)

        self.txt_search = TextBox()
        self.txt_search.Height = 26
        self.txt_search.FontSize = 11
        self.txt_search.FontFamily = FONT
        self.txt_search.Foreground = _b(CLR_TEXT)
        self.txt_search.BorderBrush = _b(CLR_BORDER)
        self.txt_search.Margin = Thickness(0, 0, 8, 0)
        self.txt_search.ToolTip = "Search view name..."
        self.txt_search.KeyUp += self._on_search
        WPFGrid.SetColumn(self.txt_search, 0)
        search_row.Children.Add(self.txt_search)

        self.cb_sort = ComboBox()
        self.cb_sort.Height = 26
        self.cb_sort.FontSize = 10
        self.cb_sort.FontFamily = FONT
        for s in SORT_OPTIONS:
            si = ComboBoxItem()
            si.Content = s
            self.cb_sort.Items.Add(si)
        self.cb_sort.SelectedIndex = 0
        self.cb_sort.SelectionChanged += self._on_sort
        WPFGrid.SetColumn(self.cb_sort, 1)
        search_row.Children.Add(self.cb_sort)

        body.Children.Add(search_row)

        vbp = StackPanel()
        vbp.Orientation = Orientation.Horizontal
        vbp.Margin = Thickness(0, 0, 0, 4)
        v1 = btn("All", 45, 22)
        v1.Click += self._view_all
        vbp.Children.Add(v1)
        v2 = btn("None", 45, 22)
        v2.Margin = Thickness(4, 0, 0, 0)
        v2.Click += self._view_none
        vbp.Children.Add(v2)
        body.Children.Add(vbp)

        vb = Border()
        vb.BorderBrush = _b(CLR_BORDER)
        vb.BorderThickness = Thickness(1)
        vb.CornerRadius = CornerRadius(4)
        vb.Background = _b(CLR_WHITE)
        vb.MinHeight = 100
        vb.MaxHeight = 250
        vs = ScrollViewer()
        vs.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        vs.Padding = Thickness(6, 4, 6, 4)
        self.view_panel = StackPanel()
        vs.Content = self.view_panel
        vb.Child = vs
        body.Children.Add(vb)

        scan_row = StackPanel()
        scan_row.Orientation = Orientation.Horizontal
        scan_row.Margin = Thickness(0, 10, 0, 0)
        self.btn_scan = Button()
        self.btn_scan.Content = "  Scan Annotations in Selected Views  "
        self.btn_scan.Height = 32
        self.btn_scan.MinWidth = 300
        self.btn_scan.FontSize = 12
        self.btn_scan.FontFamily = FONT
        self.btn_scan.FontWeight = System.Windows.FontWeights.SemiBold
        self.btn_scan.Background = _b(CLR_SCAN_BG)
        self.btn_scan.Foreground = _b(CLR_HEADER)
        self.btn_scan.Click += self._scan_click
        scan_row.Children.Add(self.btn_scan)

        self.lbl_scan = lbl("", 10, False, CLR_MUTED)
        self.lbl_scan.VerticalAlignment = VerticalAlignment.Center
        self.lbl_scan.Margin = Thickness(10, 0, 0, 0)
        self.lbl_scan.TextWrapping = TextWrapping.Wrap
        scan_row.Children.Add(self.lbl_scan)
        body.Children.Add(scan_row)

        bscr.Content = body
        root.Children.Add(bscr)
        self.Content = root

        self._update_docs()
        self._refresh_views()

    def _fill_combo(self, combo, sel_title):
        combo.Items.Clear()
        si = 0
        for i in range(len(self.all_docs)):
            d = self.all_docs[i]
            item = ComboBoxItem()
            item.Content = d.Title
            item.Tag = d
            combo.Items.Add(item)
            if d.Title == sel_title:
                si = i
        if combo.Items.Count > 0:
            combo.SelectedIndex = si

    def _get_doc(self, combo):
        if combo.SelectedItem is not None:
            s = combo.SelectedItem
            if hasattr(s, 'Tag') and s.Tag is not None:
                return s.Tag
        return None

    def _update_docs(self):
        self.source_doc = self._get_doc(self.cb_src)
        self.dest_doc = self._get_doc(self.cb_dst)

    def _doc_changed(self, s, e):
        self._update_docs()
        self._refresh_views()

    def _swap(self, s, e):
        a = self.cb_src.SelectedIndex
        b = self.cb_dst.SelectedIndex
        self.cb_src.SelectedIndex = b
        self.cb_dst.SelectedIndex = a
        self._update_docs()
        self._refresh_views()

    def _refresh_views(self):
        self.view_panel.Children.Clear()
        self.view_cbs = []
        self.matched_pairs = []
        self.lbl_scan.Text = ""

        if self.source_doc is None or self.dest_doc is None:
            self.lbl_count.Text = "(0)"
            return

        if self.source_doc.Title == self.dest_doc.Title:
            self.lbl_count.Text = "(same model!)"
            m = lbl("Source and Destination must be different.", 11, False, CLR_ERROR)
            m.Margin = Thickness(4, 8, 4, 8)
            self.view_panel.Children.Add(m)
            return

        sv_list = get_valid_views(self.source_doc)
        dv_list = get_valid_views(self.dest_doc)
        self.matched_pairs = match_views(sv_list, dv_list)

        self.lbl_count.Text = "({} matched)".format(len(self.matched_pairs))
        self._render_view_list()

        self.lbl_status.Text = "Select views, then click 'Scan Annotations'."
        self.lbl_status.Foreground = _b(CLR_MUTED)

    def _render_view_list(self):
        self.view_panel.Children.Clear()
        self.view_cbs = []

        search_text = self.txt_search.Text.strip().lower() if self.txt_search.Text else ""
        sort_idx = self.cb_sort.SelectedIndex

        filtered = []
        for pair in self.matched_pairs:
            sv = pair[0]
            if search_text == "" or search_text in sv.Name.lower():
                filtered.append(pair)

        if sort_idx == 0:
            filtered.sort(key=lambda p: p[0].Name)
        elif sort_idx == 1:
            filtered.sort(key=lambda p: p[0].Name, reverse=True)
        elif sort_idx == 2:
            filtered.sort(key=lambda p: (str(p[0].ViewType), p[0].Name))

        for pair in filtered:
            sv = pair[0]
            cb = CheckBox()
            vtl = view_type_label(sv.ViewType)
            cb.Content = "[{}]  {}".format(vtl, sv.Name)
            cb.IsChecked = False
            cb.Tag = pair
            cb.FontSize = 10.5
            cb.Foreground = _b(CLR_TEXT)
            cb.FontFamily = FONT
            cb.Margin = Thickness(2, 1, 2, 1)
            self.view_panel.Children.Add(cb)
            self.view_cbs.append(cb)

        if len(filtered) == 0:
            m = lbl("No views match filter.", 11, False, CLR_MUTED)
            m.Margin = Thickness(4, 8, 4, 8)
            self.view_panel.Children.Add(m)

    def _on_search(self, s, e):
        self._render_view_list()

    def _on_sort(self, s, e):
        self._render_view_list()

    def _scan_click(self, s, e):
        try:
            self._do_scan()
        except Exception as ex:
            self.lbl_status.Text = "Scan error: " + str(ex)
            self.lbl_status.Foreground = _b(CLR_ERROR)

    def _do_scan(self):
        self._update_docs()
        if self.source_doc is None:
            return

        cats = []
        for c in self.cat_cbs:
            if c.IsChecked == True:
                cats.append(c.Tag)
        if len(cats) == 0:
            self.lbl_status.Text = "Select at least one category."
            self.lbl_status.Foreground = _b(CLR_ERROR)
            return

        sel = []
        for c in self.view_cbs:
            if c.IsChecked == True:
                sel.append(c)
        if len(sel) == 0:
            self.lbl_status.Text = "Select at least one view to scan."
            self.lbl_status.Foreground = _b(CLR_ERROR)
            return

        self.btn_scan.IsEnabled = False
        self.progress.Visibility = Visibility.Visible
        self.progress.Maximum = len(sel)
        self.progress.Value = 0
        self.lbl_status.Text = "Scanning..."
        self.lbl_status.Foreground = _b(CLR_MUTED)

        total = 0
        with_anno = 0

        for i in range(len(sel)):
            c = sel[i]
            pair = c.Tag
            sv = pair[0]
            self.progress.Value = i

            cnt = count_annotations(self.source_doc, sv.Id, cats)
            total += cnt
            vtl = view_type_label(sv.ViewType)

            if cnt > 0:
                with_anno += 1
                c.Content = "[{}]  {}  \u2014 {} annotations".format(vtl, sv.Name, cnt)
                c.Foreground = _b(CLR_TEXT)
                c.IsChecked = True
            else:
                c.Content = "[{}]  {}  \u2014 0".format(vtl, sv.Name)
                c.Foreground = _b(CLR_MUTED)
                c.IsChecked = False

        self.progress.Value = len(sel)
        self.progress.Visibility = Visibility.Collapsed
        self.btn_scan.IsEnabled = True

        self.lbl_scan.Text = "{} annotations in {} of {} views".format(total, with_anno, len(sel))

        if total > 0:
            self.lbl_status.Text = "Scan done. {} annotations ready. Click 'Copy Annotations'.".format(total)
            self.lbl_status.Foreground = _b(CLR_SUCCESS)
        else:
            self.lbl_status.Text = "No annotations found in selected views."
            self.lbl_status.Foreground = _b(CLR_WARNING)

    def _cat_all(self, s, e):
        for c in self.cat_cbs:
            c.IsChecked = True
    def _cat_none(self, s, e):
        for c in self.cat_cbs:
            c.IsChecked = False
    def _view_all(self, s, e):
        for c in self.view_cbs:
            c.IsChecked = True
    def _view_none(self, s, e):
        for c in self.view_cbs:
            c.IsChecked = False
    def _close(self, s, e):
        self.Close()

    def _copy_click(self, s, e):
        try:
            self._do_copy()
        except Exception as ex:
            self.lbl_status.Text = "ERROR: " + str(ex)
            self.lbl_status.Foreground = _b(CLR_ERROR)
            self.btn_copy.IsEnabled = True
            self.progress.Visibility = Visibility.Collapsed
            forms.alert("Copy failed:\n\n{}".format(str(ex)), title="DQT - Error")

    def _do_copy(self):
        self._update_docs()
        if self.source_doc is None or self.dest_doc is None:
            forms.alert("Select both Source and Destination.", title="DQT")
            return
        if self.source_doc.Title == self.dest_doc.Title:
            forms.alert("Source and Destination must be different.", title="DQT")
            return

        cats = []
        for c in self.cat_cbs:
            if c.IsChecked == True:
                cats.append(c.Tag)
        if len(cats) == 0:
            forms.alert("Select at least one category to copy.", title="DQT")
            return

        sel_views = []
        for c in self.view_cbs:
            if c.IsChecked == True:
                sel_views.append(c.Tag)
        if len(sel_views) == 0:
            forms.alert("Select at least one view with annotations to copy.", title="DQT")
            return

        self.btn_copy.IsEnabled = False
        self.progress.Visibility = Visibility.Visible
        self.progress.Maximum = len(sel_views)
        self.progress.Value = 0

        copier_options = CopyPasteOptions()
        copier_options.SetDuplicateTypeNamesHandler(UseDestinationHandler())

        total_copied = 0
        view_success = 0

        for i in range(len(sel_views)):
            self.progress.Value = i
            src_view, dest_view = sel_views[i]

            self.lbl_status.Text = "Copying view: {} ...".format(src_view.Name)

            ids_to_copy = collect_annotation_ids(self.source_doc, src_view.Id, cats)
            if not ids_to_copy:
                continue

            ids_list = List[ElementId]()
            for eid in ids_to_copy:
                ids_list.Add(eid)

            with disposing(Transaction(self.dest_doc, "DQT - Copy Annotations: " + dest_view.Name)) as t:
                t.Start()
                # Swallowing warnings
                options = t.GetFailureHandlingOptions()
                options.SetFailuresPreprocessor(SilentFailurePreprocessor())
                t.SetFailureHandlingOptions(options)

                try:
                    copied_ids = ElementTransformUtils.CopyElements(
                        src_view,
                        ids_list,
                        dest_view,
                        None,
                        copier_options
                    )
                    t.Commit()
                    total_copied += copied_ids.Count
                    view_success += 1
                except Exception as ex:
                    t.RollBack()

        self.progress.Value = len(sel_views)
        self.progress.Visibility = Visibility.Collapsed
        self.btn_copy.IsEnabled = True

        self.lbl_status.Text = "Copy completed. Copied {} elements across {} views.".format(total_copied, view_success)
        self.lbl_status.Foreground = _b(CLR_SUCCESS)
        forms.alert("Successfully copied {} elements across {} views!".format(total_copied, view_success), title="DQT - Copy Annotations")


def show_dialog():
    win = CopyAnnotationsWindow()
    win.ShowDialog()
