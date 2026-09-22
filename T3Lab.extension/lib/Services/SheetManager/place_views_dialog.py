# -*- coding: utf-8 -*-
"""
Sheet Manager - Place Views Dialog (v2.1)
Fixes:
  - place_views method added to PlaceViewsService inline
  - Search box for views
  - Column order: Sheet Number | Sheet Name | View Name

Copyright (c) Dang Quoc Truong (DQT) 2026
"""

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('System.Windows.Forms')

import System
from System import Array
from System.Windows import (Window, MessageBox, MessageBoxButton, MessageBoxImage,
                             Thickness, GridLength, HorizontalAlignment, VerticalAlignment,
                             FontWeights, Visibility)
from System.Windows.Controls import (Grid, Label, Button, TextBox, ListBox, RadioButton,
                                      ComboBox, StackPanel, GroupBox, ScrollViewer,
                                      CheckBox, Separator, DataGrid, DataGridTextColumn,
                                      DataGridCheckBoxColumn, SelectionMode, Orientation,
                                      RowDefinition, ColumnDefinition, Border)
from System.Windows.Media import BrushConverter, SolidColorBrush
import System.Windows.Controls as WPFControls

from Autodesk.Revit.DB import (FilteredElementCollector, View, ViewType, Viewport,
                                 XYZ, BoundingBoxUV, Transaction, TransactionStatus, ViewSheet)

# ─── DQT Brand Colors ───────────────────────────────────────────────────────
def _brush(hex_color):
    return BrushConverter().ConvertFromString(hex_color)

GOLD      = _brush("#0F172A")
CREAM     = _brush("#F8FAFC")
BORDER    = _brush("#CBD5E1")
DARK      = _brush("#5D4E37")
WHITE     = _brush("#FFFFFF")
LIGHT_ROW = _brush("#F1F5F9")
BLUE_SEL  = _brush("#3A7BD5")
GREEN     = _brush("#2E7D32")
RED_      = _brush("#C62828")
GRAY      = _brush("#9E9E9E")


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _btn(text, width=110, primary=True):
    b = Button()
    b.Content = text
    b.Width = width
    b.Height = 32
    b.Margin = Thickness(4, 0, 4, 0)
    b.FontSize = 12
    b.FontWeight = FontWeights.SemiBold
    if primary:
        b.Background = GOLD
        b.Foreground = DARK
    else:
        b.Background = WHITE
        b.Foreground = DARK
    b.BorderBrush = BORDER
    b.BorderThickness = Thickness(1)
    b.Cursor = System.Windows.Input.Cursors.Hand
    return b


def _label(text, bold=False, size=12):
    lbl = Label()
    lbl.Content = text
    lbl.FontSize = size
    lbl.Foreground = DARK
    if bold:
        lbl.FontWeight = FontWeights.Bold
    return lbl


# ─── PlaceViewsDialog ────────────────────────────────────────────────────────
class PlaceViewsDialog(Window):
    """Dialog for placing views on selected sheets."""

    def __init__(self, place_views_service, doc, selected_sheets):
        self._svc   = place_views_service
        self._doc   = doc
        self._sheets = selected_sheets          # already-selected sheets
        self._all_views = []                    # list of (view_name, view_type_str, view_element, sheet_num_if_placed)
        self._filtered_views = []

        self._build_ui()
        self._load_views()

    # ── UI Construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        self.Title  = "Place Views on Sheets"
        self.Width  = 950
        self.Height = 680
        self.Background = CREAM
        self.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        self.ResizeMode = System.Windows.ResizeMode.CanResize

        outer = Grid()
        outer.Margin = Thickness(0)

        # Rows: header | body | footer
        for h in [50, 1, 48]:
            rd = RowDefinition()
            rd.Height = GridLength(h) if h != 1 else GridLength(1, System.Windows.GridUnitType.Star)
            outer.RowDefinitions.Add(rd)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = Border()
        hdr.Background = GOLD
        hdr.BorderBrush = BORDER
        hdr.BorderThickness = Thickness(0, 0, 0, 2)
        hdr_sp = StackPanel()
        hdr_sp.Orientation = Orientation.Horizontal
        hdr_sp.VerticalAlignment = VerticalAlignment.Center
        hdr_sp.Margin = Thickness(16, 0, 0, 0)

        title_lbl = Label()
        title_lbl.Content = "Place Views on Sheets"
        title_lbl.FontSize = 15
        title_lbl.FontWeight = FontWeights.Bold
        title_lbl.Foreground = DARK
        hdr_sp.Children.Add(title_lbl)

        self._subtitle_lbl = Label()
        self._subtitle_lbl.FontSize = 12
        self._subtitle_lbl.Foreground = DARK
        self._subtitle_lbl.Margin = Thickness(12, 0, 0, 0)
        hdr_sp.Children.Add(self._subtitle_lbl)

        hdr.Child = hdr_sp
        Grid.SetRow(hdr, 0)
        outer.Children.Add(hdr)

        # ── Body (3-column: views | settings | placement list) ─────────────
        body = Grid()
        body.Margin = Thickness(12, 10, 12, 6)
        Grid.SetRow(body, 1)

        for w in [0.38, 0.30, 0.32]:
            cd = ColumnDefinition()
            cd.Width = GridLength(w, System.Windows.GridUnitType.Star)
            body.ColumnDefinitions.Add(cd)

        outer.Children.Add(body)

        # ── LEFT: Available Views ───────────────────────────────────────────
        left_gb = GroupBox()
        left_gb.Header = "Available Views"
        left_gb.BorderBrush = BORDER
        left_gb.Margin = Thickness(0, 0, 6, 0)
        left_gb.Foreground = DARK
        left_gb.FontWeight = FontWeights.SemiBold
        Grid.SetColumn(left_gb, 0)
        body.Children.Add(left_gb)

        left_grid = Grid()
        left_grid.Margin = Thickness(4)

        for h in [28, 28, 1, 36]:
            rd = RowDefinition()
            rd.Height = GridLength(h) if h != 1 else GridLength(1, System.Windows.GridUnitType.Star)
            left_grid.RowDefinitions.Add(rd)

        # Filter row
        filter_sp = StackPanel()
        filter_sp.Orientation = Orientation.Horizontal
        filter_sp.VerticalAlignment = VerticalAlignment.Center
        filter_sp.Margin = Thickness(0, 0, 0, 2)
        Grid.SetRow(filter_sp, 0)

        filter_lbl = Label()
        filter_lbl.Content = "Filter:"
        filter_lbl.FontSize = 11
        filter_lbl.Foreground = DARK
        filter_lbl.Padding = Thickness(0, 0, 4, 0)
        filter_lbl.VerticalAlignment = VerticalAlignment.Center
        filter_sp.Children.Add(filter_lbl)

        self._filter_combo = ComboBox()
        self._filter_combo.Width = 140
        self._filter_combo.Height = 24
        self._filter_combo.FontSize = 11
        for opt in ["All Views", "Not on Sheets", "On Sheets"]:
            self._filter_combo.Items.Add(opt)
        self._filter_combo.SelectedIndex = 1
        self._filter_combo.SelectionChanged += self._on_filter_changed
        filter_sp.Children.Add(self._filter_combo)

        left_grid.Children.Add(filter_sp)

        # Search row
        search_sp = StackPanel()
        search_sp.Orientation = Orientation.Horizontal
        search_sp.VerticalAlignment = VerticalAlignment.Center
        search_sp.Margin = Thickness(0, 0, 0, 2)
        Grid.SetRow(search_sp, 1)

        search_lbl = Label()
        search_lbl.Content = "Search:"
        search_lbl.FontSize = 11
        search_lbl.Foreground = DARK
        search_lbl.Padding = Thickness(0, 0, 4, 0)
        search_lbl.VerticalAlignment = VerticalAlignment.Center
        search_sp.Children.Add(search_lbl)

        self._search_box = TextBox()
        self._search_box.Width = 155
        self._search_box.Height = 24
        self._search_box.FontSize = 11
        self._search_box.BorderBrush = BORDER
        self._search_box.VerticalContentAlignment = VerticalAlignment.Center
        self._search_box.TextChanged += self._on_search_changed
        search_sp.Children.Add(self._search_box)

        left_grid.Children.Add(search_sp)

        # View ListBox
        sv = ScrollViewer()
        sv.VerticalScrollBarVisibility = WPFControls.ScrollBarVisibility.Auto
        Grid.SetRow(sv, 2)

        self._view_listbox = ListBox()
        self._view_listbox.SelectionMode = SelectionMode.Extended
        self._view_listbox.BorderThickness = Thickness(1)
        self._view_listbox.BorderBrush = BORDER
        self._view_listbox.Background = WHITE
        self._view_listbox.FontSize = 11
        self._view_listbox.SelectionChanged += self._on_view_selection_changed
        sv.Content = self._view_listbox
        left_grid.Children.Add(sv)

        # Bottom: Select All / Select None
        sel_sp = StackPanel()
        sel_sp.Orientation = Orientation.Horizontal
        sel_sp.Margin = Thickness(0, 4, 0, 0)
        Grid.SetRow(sel_sp, 3)

        btn_all  = _btn("Select All",  90, False)
        btn_none = _btn("Select None", 90, False)
        btn_all.Click  += self._on_select_all
        btn_none.Click += self._on_select_none
        sel_sp.Children.Add(btn_all)
        sel_sp.Children.Add(btn_none)
        left_grid.Children.Add(sel_sp)

        left_gb.Content = left_grid

        # ── MIDDLE: Placement Settings ───────────────────────────────────────
        mid_stack = StackPanel()
        mid_stack.Margin = Thickness(6, 0, 6, 0)
        Grid.SetColumn(mid_stack, 1)
        body.Children.Add(mid_stack)

        # Placement method
        pm_gb = GroupBox()
        pm_gb.Header = "Placement Method"
        pm_gb.BorderBrush = BORDER
        pm_gb.Margin = Thickness(0, 0, 0, 8)
        pm_gb.Foreground = DARK
        pm_gb.FontWeight = FontWeights.SemiBold

        pm_sp = StackPanel()
        pm_sp.Margin = Thickness(6, 4, 6, 6)

        self._rb_manual = RadioButton()
        self._rb_manual.Content = "Manual Selection"
        self._rb_manual.IsChecked = True
        self._rb_manual.FontWeight = FontWeights.SemiBold
        self._rb_manual.Foreground = DARK
        self._rb_manual.Margin = Thickness(0, 0, 0, 2)
        self._rb_manual.Checked += self._on_mode_changed

        manual_hint = Label()
        manual_hint.Content = "Select views and placement mode below"
        manual_hint.FontSize = 10
        manual_hint.Foreground = GRAY
        manual_hint.Margin = Thickness(16, 0, 0, 6)

        self._rb_excel = RadioButton()
        self._rb_excel.Content = "From Excel File"
        self._rb_excel.Foreground = DARK
        self._rb_excel.Margin = Thickness(0, 0, 0, 2)
        self._rb_excel.Checked += self._on_mode_changed

        excel_hint = Label()
        excel_hint.Content = "Load Sheet → View mapping from Excel"
        excel_hint.FontSize = 10
        excel_hint.Foreground = GRAY
        excel_hint.Margin = Thickness(16, 0, 0, 0)

        for ctrl in [self._rb_manual, manual_hint, self._rb_excel, excel_hint]:
            pm_sp.Children.Add(ctrl)

        pm_gb.Content = pm_sp
        mid_stack.Children.Add(pm_gb)

        # Place Mode (one-per-sheet / distribute)
        self._mode_gb = GroupBox()
        self._mode_gb.Header = "Sheet Mode"
        self._mode_gb.BorderBrush = BORDER
        self._mode_gb.Margin = Thickness(0, 0, 0, 8)
        self._mode_gb.Foreground = DARK
        self._mode_gb.FontWeight = FontWeights.SemiBold

        mode_sp = StackPanel()
        mode_sp.Margin = Thickness(6, 4, 6, 6)

        self._rb_one = RadioButton()
        self._rb_one.Content = "One view per sheet"
        self._rb_one.IsChecked = True
        self._rb_one.Foreground = DARK

        self._rb_all = RadioButton()
        self._rb_all.Content = "All views on each sheet"
        self._rb_all.Foreground = DARK
        self._rb_all.Margin = Thickness(0, 4, 0, 0)

        self._rb_dist = RadioButton()
        self._rb_dist.Content = "Distribute across sheets"
        self._rb_dist.Foreground = DARK
        self._rb_dist.Margin = Thickness(0, 4, 0, 0)

        for ctrl in [self._rb_one, self._rb_all, self._rb_dist]:
            mode_sp.Children.Add(ctrl)
        self._mode_gb.Content = mode_sp
        mid_stack.Children.Add(self._mode_gb)

        # Auto-Arrange
        self._arrange_gb = GroupBox()
        self._arrange_gb.Header = "Auto-Arrange"
        self._arrange_gb.BorderBrush = BORDER
        self._arrange_gb.Foreground = DARK
        self._arrange_gb.FontWeight = FontWeights.SemiBold

        arr_sp = StackPanel()
        arr_sp.Margin = Thickness(6, 4, 6, 6)

        grid_lbl = Label()
        grid_lbl.Content = "Grid Layout:"
        grid_lbl.FontWeight = FontWeights.SemiBold
        grid_lbl.Foreground = DARK
        arr_sp.Children.Add(grid_lbl)

        row_col_sp = StackPanel()
        row_col_sp.Orientation = Orientation.Horizontal
        row_col_sp.Margin = Thickness(0, 4, 0, 0)

        rows_lbl = Label()
        rows_lbl.Content = "Rows:"
        rows_lbl.Foreground = DARK
        rows_lbl.FontSize = 11
        rows_lbl.VerticalAlignment = VerticalAlignment.Center
        row_col_sp.Children.Add(rows_lbl)

        self._rows_combo = ComboBox()
        self._rows_combo.Width = 55
        self._rows_combo.Height = 24
        for i in range(1, 6):
            self._rows_combo.Items.Add(str(i))
        self._rows_combo.SelectedIndex = 1
        row_col_sp.Children.Add(self._rows_combo)

        cols_lbl = Label()
        cols_lbl.Content = "Columns:"
        cols_lbl.Foreground = DARK
        cols_lbl.FontSize = 11
        cols_lbl.Margin = Thickness(8, 0, 0, 0)
        cols_lbl.VerticalAlignment = VerticalAlignment.Center
        row_col_sp.Children.Add(cols_lbl)

        self._cols_combo = ComboBox()
        self._cols_combo.Width = 55
        self._cols_combo.Height = 24
        for i in range(1, 6):
            self._cols_combo.Items.Add(str(i))
        self._cols_combo.SelectedIndex = 1
        row_col_sp.Children.Add(self._cols_combo)

        arr_sp.Children.Add(row_col_sp)

        self._grid_hint = Label()
        self._grid_hint.Content = "Views will be arranged in a 2x2 grid\non each sheet"
        self._grid_hint.FontSize = 10
        self._grid_hint.Foreground = GRAY
        self._grid_hint.Margin = Thickness(0, 4, 0, 0)
        arr_sp.Children.Add(self._grid_hint)

        self._rows_combo.SelectionChanged += self._update_grid_hint
        self._cols_combo.SelectionChanged += self._update_grid_hint

        self._arrange_gb.Content = arr_sp
        mid_stack.Children.Add(self._arrange_gb)

        # ── RIGHT: Target sheets (placement preview) ─────────────────────────
        right_gb = GroupBox()
        right_gb.Header = "Target Sheets"
        right_gb.BorderBrush = BORDER
        right_gb.Margin = Thickness(6, 0, 0, 0)
        right_gb.Foreground = DARK
        right_gb.FontWeight = FontWeights.SemiBold
        Grid.SetColumn(right_gb, 2)
        body.Children.Add(right_gb)

        right_grid = Grid()
        right_grid.Margin = Thickness(4)
        rd1 = RowDefinition()
        rd1.Height = GridLength(1, System.Windows.GridUnitType.Star)
        rd2 = RowDefinition()
        rd2.Height = GridLength(28)
        right_grid.RowDefinitions.Add(rd1)
        right_grid.RowDefinitions.Add(rd2)

        # ── DataGrid with Sheet Number | Sheet Name | View Name columns ──────
        self._sheet_grid = DataGrid()
        self._sheet_grid.AutoGenerateColumns = False
        self._sheet_grid.IsReadOnly = True
        self._sheet_grid.CanUserSortColumns = True
        self._sheet_grid.CanUserResizeRows = False
        self._sheet_grid.HeadersVisibility = WPFControls.DataGridHeadersVisibility.Column
        self._sheet_grid.SelectionMode = WPFControls.DataGridSelectionMode.Extended
        self._sheet_grid.BorderBrush = BORDER
        self._sheet_grid.BorderThickness = Thickness(1)
        self._sheet_grid.Background = WHITE
        self._sheet_grid.AlternatingRowBackground = LIGHT_ROW
        self._sheet_grid.FontSize = 11
        self._sheet_grid.RowHeight = 22
        self._sheet_grid.GridLinesVisibility = WPFControls.DataGridGridLinesVisibility.Horizontal

        # Column: Sheet Number
        col_num = DataGridTextColumn()
        col_num.Header = "Sheet No."
        col_num.Binding = System.Windows.Data.Binding("SheetNumber")
        col_num.Width = WPFControls.DataGridLength(70)
        col_num.IsReadOnly = True
        self._sheet_grid.Columns.Add(col_num)

        # Column: Sheet Name
        col_sname = DataGridTextColumn()
        col_sname.Header = "Sheet Name"
        col_sname.Binding = System.Windows.Data.Binding("SheetName")
        col_sname.Width = WPFControls.DataGridLength(1, WPFControls.DataGridLengthUnitType.Star)
        col_sname.IsReadOnly = True
        self._sheet_grid.Columns.Add(col_sname)

        # Column: View Name (assigned view)
        col_vname = DataGridTextColumn()
        col_vname.Header = "View Name"
        col_vname.Binding = System.Windows.Data.Binding("ViewName")
        col_vname.Width = WPFControls.DataGridLength(1, WPFControls.DataGridLengthUnitType.Star)
        col_vname.IsReadOnly = True
        self._sheet_grid.Columns.Add(col_vname)

        Grid.SetRow(self._sheet_grid, 0)
        right_grid.Children.Add(self._sheet_grid)

        self._sheet_count_lbl = Label()
        self._sheet_count_lbl.FontSize = 10
        self._sheet_count_lbl.Foreground = GRAY
        self._sheet_count_lbl.VerticalAlignment = VerticalAlignment.Center
        Grid.SetRow(self._sheet_count_lbl, 1)
        right_grid.Children.Add(self._sheet_count_lbl)

        right_gb.Content = right_grid

        # ── Footer ─────────────────────────────────────────────────────────
        footer = Border()
        footer.Background = GOLD
        footer.BorderBrush = BORDER
        footer.BorderThickness = Thickness(0, 2, 0, 0)
        Grid.SetRow(footer, 2)

        foot_sp = StackPanel()
        foot_sp.Orientation = Orientation.Horizontal
        foot_sp.HorizontalAlignment = HorizontalAlignment.Right
        foot_sp.VerticalAlignment = VerticalAlignment.Center
        foot_sp.Margin = Thickness(0, 0, 16, 0)

        self._status_lbl = Label()
        self._status_lbl.FontSize = 11
        self._status_lbl.Foreground = DARK
        self._status_lbl.Margin = Thickness(0, 0, 16, 0)
        self._status_lbl.VerticalAlignment = VerticalAlignment.Center
        foot_sp.Children.Add(self._status_lbl)

        btn_place  = _btn("Place Views", 110, True)
        btn_cancel = _btn("Cancel", 80, False)
        btn_place.Click  += self._on_place_views
        btn_cancel.Click += lambda s, e: self.Close()

        foot_sp.Children.Add(btn_place)
        foot_sp.Children.Add(btn_cancel)

        footer.Child = foot_sp
        outer.Children.Add(footer)

        self.Content = outer
        self._update_subtitle()

    # ── Data Loading ─────────────────────────────────────────────────────────
    def _load_views(self):
        """Collect all placeable views from document."""
        try:
            collector = FilteredElementCollector(self._doc).OfClass(View)

            # Build a quick set of view ids already on sheets
            placed_view_ids = set()
            vp_collector = FilteredElementCollector(self._doc).OfClass(Viewport)
            for vp in vp_collector:
                placed_view_ids.add(vp.ViewId.Value if hasattr(vp.ViewId, 'Value') else vp.ViewId.IntegerValue)

            self._all_views = []
            skip_types = [ViewType.DrawingSheet, ViewType.ProjectBrowser,
                          ViewType.SystemBrowser, ViewType.Undefined]

            for v in collector:
                if v.IsTemplate:
                    continue
                if v.ViewType in skip_types:
                    continue
                vid = v.Id.Value if hasattr(v.Id, 'Value') else v.Id.IntegerValue
                on_sheet = vid in placed_view_ids
                self._all_views.append({
                    'view':     v,
                    'name':     v.Name,
                    'type':     str(v.ViewType),
                    'on_sheet': on_sheet
                })

            self._all_views.sort(key=lambda x: x['name'])
            self._apply_filter()
            self._populate_sheet_grid()

        except Exception as ex:
            MessageBox.Show("Error loading views:\n{}".format(str(ex)),
                            "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _apply_filter(self):
        """Filter views list by combo + search text."""
        filt  = self._filter_combo.SelectedItem or "All Views"
        query = (self._search_box.Text or "").strip().lower()

        result = []
        for item in self._all_views:
            if filt == "Not on Sheets" and item['on_sheet']:
                continue
            if filt == "On Sheets" and not item['on_sheet']:
                continue
            if query and query not in item['name'].lower():
                continue
            result.append(item)

        self._filtered_views = result
        self._refresh_listbox()

    def _refresh_listbox(self):
        self._view_listbox.Items.Clear()
        for item in self._filtered_views:
            tag = "  [on sheet]" if item['on_sheet'] else ""
            self._view_listbox.Items.Add("{} [{}]{}".format(
                item['name'], item['type'], tag))

    def _populate_sheet_grid(self):
        """Populate target sheet DataGrid."""
        import System.Collections.ObjectModel as OCM
        from System import Object
        rows = OCM.ObservableCollection[Object]()

        for sheet in self._sheets:
            row = SheetRow()
            row.SheetNumber = sheet.SheetNumber
            row.SheetName   = sheet.Name
            row.ViewName    = ""
            rows.Add(row)

        self._sheet_grid.ItemsSource = rows
        self._sheet_count_lbl.Content = "{} sheet(s) selected".format(len(self._sheets))

    def _update_subtitle(self):
        self._subtitle_lbl.Content = "— {} sheet(s) selected".format(len(self._sheets))

    def _update_grid_hint(self, sender=None, args=None):
        rows = self._rows_combo.SelectedItem or "2"
        cols = self._cols_combo.SelectedItem or "2"
        self._grid_hint.Content = "Views will be arranged in a {}x{} grid\non each sheet".format(rows, cols)

    # ── Event Handlers ────────────────────────────────────────────────────────
    def _on_filter_changed(self, sender, args):
        self._apply_filter()

    def _on_search_changed(self, sender, args):
        self._apply_filter()

    def _on_view_selection_changed(self, sender, args):
        count = self._view_listbox.SelectedItems.Count
        self._status_lbl.Content = "{} view(s) selected".format(count) if count else ""

    def _on_select_all(self, sender, args):
        self._view_listbox.SelectAll()

    def _on_select_none(self, sender, args):
        self._view_listbox.UnselectAll()

    def _on_mode_changed(self, sender, args):
        manual = self._rb_manual.IsChecked
        self._mode_gb.IsEnabled    = bool(manual)
        self._arrange_gb.IsEnabled = bool(manual)

    # ── Place Views (Manual) ─────────────────────────────────────────────────
    def _on_place_views(self, sender, args):
        try:
            if self._rb_excel.IsChecked:
                MessageBox.Show("Excel mode not implemented yet.\nUse Manual Selection.",
                                "Info", MessageBoxButton.OK, MessageBoxImage.Information)
                return

            # Get selected view indices
            selected_indices = list(self._view_listbox.SelectedItems)
            if not selected_indices:
                MessageBox.Show("Please select at least one view.",
                                "No Views Selected", MessageBoxButton.OK, MessageBoxImage.Warning)
                return

            # Map back to view elements
            idx_list = []
            for lb_item in selected_indices:
                idx = self._view_listbox.Items.IndexOf(lb_item)
                if 0 <= idx < len(self._filtered_views):
                    idx_list.append(self._filtered_views[idx])

            selected_views = [item['view'] for item in idx_list]

            if not self._sheets:
                MessageBox.Show("No sheets selected.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return

            # Determine mode
            if self._rb_one.IsChecked:
                mode = 'one_per_sheet'
            elif self._rb_all.IsChecked:
                mode = 'all_on_each'
            else:
                mode = 'distribute'

            rows = int(self._rows_combo.SelectedItem or "2")
            cols = int(self._cols_combo.SelectedItem or "2")

            confirm = MessageBox.Show(
                "Place {} view(s) on {} sheet(s)?\nMode: {}".format(
                    len(selected_views), len(self._sheets), mode),
                "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)

            if confirm != System.Windows.MessageBoxResult.Yes:
                return

            t = Transaction(self._doc, "T3Lab: Place Views on Sheets")
            t.Start()
            try:
                success = self._do_place(selected_views, mode, rows, cols)
                if t.Commit() != TransactionStatus.Committed:
                    raise RuntimeError("Revit did not commit the viewport placement")
                expected = len(selected_views) * (len(self._sheets) if mode == 'all_on_each' else 1)
                MessageBox.Show(
                    "Placed {} of {} requested viewport(s).".format(success, expected),
                    "Done", MessageBoxButton.OK, MessageBoxImage.Information)
                self._populate_sheet_grid()

            except Exception as ex:
                if t.GetStatus() == TransactionStatus.Started:
                    t.RollBack()
                raise

        except Exception as ex:
            MessageBox.Show("Error: {}".format(str(ex)), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)
            import traceback
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass

    def _do_place(self, views, mode, rows, cols):
        """Use the same placement geometry and capacity checks as the service."""
        return len(self._svc.batch_place_views(self._sheets, views, mode, rows, cols))


# ── Helper data class ─────────────────────────────────────────────────────────
class SheetRow(object):
    """Simple bindable row for the DataGrid."""
    def __init__(self):
        self.SheetNumber = ""
        self.SheetName   = ""
        self.ViewName    = ""