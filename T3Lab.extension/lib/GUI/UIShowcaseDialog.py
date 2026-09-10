# -*- coding: utf-8 -*-
"""
T3Lab UI Standard Showcase Dialog
Reference implementation of the unified T3Lab UI Standard.
"""

import os
import sys
import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

import System
from System.Windows import (Thickness, GridLength, GridUnitType,
                            HorizontalAlignment, VerticalAlignment, FontWeights,
                            MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult,
                            WindowState, Visibility, Clipboard)
from System.Windows.Controls import (RowDefinition, ColumnDefinition, Border,
                                      StackPanel, TextBlock, TextBox, Button,
                                      ComboBox, ComboBoxItem, DataGrid, Orientation,
                                      DataGridTextColumn, DataGridCheckBoxColumn,
                                      ScrollViewer, TabControl, TabItem, CheckBox, ListBoxItem)
from System.Windows.Media import SolidColorBrush, BrushConverter
from System.Windows.Data import Binding
from System.Collections.ObjectModel import ObservableCollection
from System import Object

from pyrevit import revit, DB, forms
from GUI.WPF_Base import T3WPFWindow, to_items_source

# Revit light/dark palette bridge
try:
    from GUI import RevitTheme as _theme
except Exception:
    try:
        import RevitTheme as _theme
    except Exception:
        _theme = None

GUI_DIR = os.path.dirname(__file__)  # [repo]/T3Lab.extension/lib/GUI
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'UIStandardShowcase.xaml')


class ShowcaseItem(object):
    """Row item representing an element in the unified audit & selection table."""

    def __init__(self, item_id, name, category, template, status_text,
                 severity, issues, is_selected=False):
        self._id = item_id
        self._name = name
        self._category = category
        self._template = template
        self._status_text = status_text
        self._severity = severity
        self._issues = issues
        self._is_selected = is_selected

    @property
    def id(self):
        return self._id

    @property
    def name(self):
        return self._name

    @property
    def category(self):
        return self._category

    @property
    def template(self):
        return self._template

    @property
    def issues(self):
        return self._issues

    @property
    def StatusText(self):
        return self._status_text

    @property
    def Severity(self):
        return self._severity

    @property
    def status(self):
        return self._status_text

    @property
    def is_selected(self):
        return self._is_selected

    @is_selected.setter
    def is_selected(self, val):
        self._is_selected = bool(val)


class UIShowcaseWindow(T3WPFWindow):
    """Window class for the unified T3Lab UI Standard Showcase."""

    def __init__(self):
        T3WPFWindow.__init__(self, XAML_FILE)
        self._skin = None
        self._pinned = False
        self._all_items = []
        self._filtered_items = []
        self._adopt_host_font()
        self._apply_theme()
        self._load_logo()
        self._load_sample_data()
        self._setup_event_handlers()
        self._update_selection_summary()

    def _load_logo(self):
        """Ensure logo image and window icon are bound."""
        try:
            logo_path = None
            for _cand in ('T3Lab_logo_tight.png', 'T3Lab_logo.png'):
                _p = os.path.join(GUI_DIR, _cand)
                if os.path.exists(_p):
                    logo_path = _p
                    break

            if logo_path and os.path.exists(logo_path):
                from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
                from System import Uri, UriKind
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

        try:
            self.Activated += self._window_activated
        except Exception:
            pass

    def _setup_event_handlers(self):
        """Wire interactive event handlers programmatically for maximum reliability."""
        try:
            if hasattr(self, 'btn_copy_log') and self.btn_copy_log:
                self.btn_copy_log.Click += self.copy_log_clicked
            if hasattr(self, 'btn_export_csv') and self.btn_export_csv:
                self.btn_export_csv.Click += self.export_csv_clicked
            if hasattr(self, 'btn_execute') and self.btn_execute:
                self.btn_execute.Click += self.execute_clicked
            if hasattr(self, 'btn_select_all') and self.btn_select_all:
                self.btn_select_all.Click += self.select_all_clicked
            if hasattr(self, 'btn_select_none') and self.btn_select_none:
                self.btn_select_none.Click += self.select_none_clicked
            if hasattr(self, 'cb_scope') and self.cb_scope:
                self.cb_scope.SelectionChanged += self.scope_changed
            if hasattr(self, 'tb_search') and self.tb_search:
                self.tb_search.TextChanged += self.search_text_changed
            if hasattr(self, 'cb_category') and self.cb_category:
                self.cb_category.SelectionChanged += self.category_filter_changed
            if hasattr(self, 'sample_grid') and self.sample_grid:
                self.sample_grid.SelectionChanged += self.grid_selection_changed
            for chip_name in ['chip_all', 'chip_compliant', 'chip_review', 'chip_failed']:
                if hasattr(self, chip_name):
                    chip = getattr(self, chip_name)
                    if chip:
                        chip.Checked += self.chip_filter_changed
            for nav_name in ['nav_toggle_params', 'nav_toggle_elements', 'nav_toggle_monitor', 'nav_toggle_settings']:
                if hasattr(self, nav_name):
                    btn = getattr(self, nav_name)
                    if btn:
                        btn.Click += self.nav_toggle_clicked
        except Exception as ex:
            print("Warning in _setup_event_handlers: {}".format(ex))

    def nav_toggle_clicked(self, sender, e):
        """Handle sidebar rail navigation tile selection."""
        nav_buttons = [
            getattr(self, 'nav_toggle_params', None),
            getattr(self, 'nav_toggle_elements', None),
            getattr(self, 'nav_toggle_monitor', None),
            getattr(self, 'nav_toggle_settings', None)
        ]
        for btn in nav_buttons:
            if btn and btn != sender:
                btn.IsChecked = False
        if sender:
            sender.IsChecked = True
            try:
                tip = sender.ToolTip
                self.status_text.Text = "Active view: {}".format(tip or "Overview")
            except Exception:
                pass
            if hasattr(self, 'main_view_tabs') and self.main_view_tabs:
                try:
                    idx = nav_buttons.index(sender)
                    if 0 <= idx < self.main_view_tabs.Items.Count:
                        self.main_view_tabs.SelectedIndex = idx
                except Exception:
                    pass

    # ── Theme ────────────────────────────────────────────────────────────────

    def _adopt_host_font(self):
        """Take the OS message font when available."""
        if _theme is None:
            return
        family, size = _theme.host_font()
        if family is None:
            return
        try:
            self.FontFamily = family
            if size and size > 0:
                self.FontSize = size
        except Exception:
            pass

    def _apply_theme(self, theme=None):
        """Write the T3Theme* brushes into Window.Resources."""
        if _theme is None:
            return
        try:
            self._skin = _theme.apply(self, theme)
        except Exception as ex:
            print("Warning: theme not applied: {}".format(ex))

    def _window_activated(self, sender, e):
        """Follow Revit if theme changed while tool was inactive."""
        if _theme is None or self._pinned:
            return
        try:
            if _theme.current_theme() != self._skin:
                self._apply_theme()
        except Exception:
            pass

    # ── Chrome & Navigation ──────────────────────────────────────────────────

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            try:
                self.btn_maximize.ToolTip = "Maximize"
            except Exception:
                pass
        else:
            self.WindowState = WindowState.Maximized
            try:
                self.btn_maximize.ToolTip = "Restore"
            except Exception:
                pass

    def close_button_clicked(self, sender, e):
        self.Close()

    def title_bar_mouse_down(self, sender, e):
        try:
            from System.Windows.Input import MouseButtonState
            if e.LeftButton == MouseButtonState.Pressed:
                self.DragMove()
        except Exception:
            pass

    # ── Data & Filtering ─────────────────────────────────────────────────────

    def _load_sample_data(self):
        """Load comprehensive production-grade sample elements."""
        self._all_items = [
            ShowcaseItem("104231", "01_Plan_Column setting-out", "Floor Plan",
                         "ARC_Plan_1-100", "Compliant", "Success", "0", True),
            ShowcaseItem("104232", "02_Plan_Column dimensions", "Floor Plan",
                         "ARC_Plan_1-100", "Compliant", "Success", "0", True),
            ShowcaseItem("104235", "Detail_Column rebar splice", "Detail View",
                         "-- none --", "Template missing", "Danger", "1", False),
            ShowcaseItem("104240", "3D_Overall perspective", "3D View",
                         "-- none --", "Naming standard", "Danger", "2", False),
            ShowcaseItem("104245", "Section_Longitudinal section", "Section",
                         "ARC_Sect_1-50", "Needs review", "Warning", "1", True),
            ShowcaseItem("104250", "Elevation_Grid A-D", "Elevation",
                         "ARC_Elev_1-100", "Compliant", "Success", "0", False),
            ShowcaseItem("104255", "Schedule_Beam rebar take-off", "Schedule",
                         "n/a", "Compliant", "Success", "0", True),
            ShowcaseItem("104261", "Detail_Pad footing M1", "Detail View",
                         "STR_Det_1-25", "Naming standard", "Danger", "1", False),
            ShowcaseItem("104266", "Plan_Services ceiling", "Floor Plan",
                         "ARC_Plan_1-100", "Compliant", "Success", "0", False),
            ShowcaseItem("104270", "Elevation_Grid 1-9", "Elevation",
                         "ARC_Elev_1-100", "Needs review", "Warning", "3", False),
        ]
        self._apply_filter()

    def _apply_filter(self):
        """Apply active chip, search query, and category filters."""
        if not hasattr(self, '_all_items') or not self._all_items:
            return

        chip_mode = "all"
        try:
            if hasattr(self, 'chip_compliant') and self.chip_compliant.IsChecked:
                chip_mode = "compliant"
            elif hasattr(self, 'chip_review') and self.chip_review.IsChecked:
                chip_mode = "review"
            elif hasattr(self, 'chip_failed') and self.chip_failed.IsChecked:
                chip_mode = "failed"
        except Exception:
            pass

        query = ""
        try:
            if hasattr(self, 'tb_search') and self.tb_search.Text:
                query = self.tb_search.Text.strip().lower()
        except Exception:
            pass

        category = "all"
        try:
            if hasattr(self, 'cb_category') and self.cb_category.SelectedItem:
                selected_content = str(self.cb_category.SelectedItem.Content)
                if selected_content != "All categories":
                    category = selected_content.lower()
        except Exception:
            pass

        filtered = []
        for item in self._all_items:
            # Chip severity filter
            if chip_mode == "compliant" and item.Severity != "Success":
                continue
            if chip_mode == "review" and item.Severity != "Warning":
                continue
            if chip_mode == "failed" and item.Severity != "Danger":
                continue

            # Category filter
            if category != "all" and item.category.lower() != category:
                continue

            # Search query filter (name or ID)
            if query and (query not in item.name.lower() and query not in item.id.lower()):
                continue

            filtered.append(item)

        self._filtered_items = filtered
        try:
            self.sample_grid.ItemsSource = to_items_source(self._filtered_items)
            if len(self._filtered_items) == 0:
                self.grid_empty.Visibility = Visibility.Visible
            else:
                self.grid_empty.Visibility = Visibility.Collapsed
        except Exception:
            pass

        self._update_selection_summary()

    def chip_filter_changed(self, sender, e):
        self._apply_filter()

    def search_text_changed(self, sender, e):
        self._apply_filter()

    def category_filter_changed(self, sender, e):
        self._apply_filter()

    def scope_changed(self, sender, e):
        try:
            scope_text = str(self.cb_scope.SelectedItem.Content)
            self.status_text.Text = "Scope updated: {}".format(scope_text)
        except Exception:
            pass

    # ── Selection Handlers ───────────────────────────────────────────────────

    def select_all_clicked(self, sender, e):
        for item in self._filtered_items:
            item.is_selected = True
        try:
            self.sample_grid.Items.Refresh()
        except Exception:
            pass
        self._update_selection_summary()
        # Giu checkbox select-all o header khop voi nut nay.
        self.sync_header_checkbox(
            self.FindName("chk_all_sample_grid"), self.sample_grid, "is_selected")

    def select_none_clicked(self, sender, e):
        for item in self._filtered_items:
            item.is_selected = False
        try:
            self.sample_grid.Items.Refresh()
        except Exception:
            pass
        self._update_selection_summary()
        # Giu checkbox select-all o header khop voi nut nay.
        self.sync_header_checkbox(
            self.FindName("chk_all_sample_grid"), self.sample_grid, "is_selected")

    def grid_selection_changed(self, sender, e):
        self._update_selection_summary()

    def _update_selection_summary(self):
        selected_count = sum(1 for item in self._all_items if item.is_selected)
        total_count = len(self._all_items)

        try:
            self.lbl_selection_count.Text = "{} of {} selected".format(
                selected_count, len(self._filtered_items))
        except Exception:
            pass

        try:
            self.btn_execute.Content = "Execute Batch ({})".format(selected_count)
        except Exception:
            pass

        try:
            self.status_text.Text = "Ready · {} sheets in scope · {} selected".format(
                total_count, selected_count)
        except Exception:
            pass

    # ── Actions ──────────────────────────────────────────────────────────────

    def copy_log_clicked(self, sender, e):
        log_text = (
            "12:04:31 Transaction opened\n"
            "12:04:31 Collected 245 sheets in active scope\n"
            "12:04:32 A-1001 -> A-101   ok\n"
            "12:04:33 A-1003   skipped, already matches pattern\n"
            "12:04:34 A-1005   failed, sheet is checked out by TCB\n"
        )
        try:
            Clipboard.SetText(log_text)
            self.status_text.Text = "Log copied to clipboard (5 lines)"
        except Exception:
            pass

    def export_csv_clicked(self, sender, e):
        try:
            self.status_text.Text = "CSV report exported to project logs folder"
        except Exception:
            pass

    def execute_clicked(self, sender, e):
        try:
            selected_count = sum(1 for item in self._all_items if item.is_selected)
            self.status_text.Text = "Batch complete · {} elements processed successfully".format(selected_count)
            self.exec_progress.Value = self.exec_progress.Maximum
        except Exception:
            pass

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_sample_grid_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua sample_grid."""
        self.toggle_all_rows(self.sample_grid, "is_selected", sender.IsChecked)


def show_ui_standard_showcase():
    """Launch the UI Standard Showcase Dialog"""
    try:
        window = UIShowcaseWindow()
        window.ShowDialog()
    except Exception as e:
        # Do NOT print() or traceback.print_exc() here. Under the CPython engine
        # sys.stdout/stderr is a pyRevit ScriptIO with no write(), so both raise
        # 'ScriptIO' object has no attribute 'write' — burying the real error
        # before the MessageBox below ever runs. Format the traceback instead
        # and show it, so the actual cause reaches the user.
        import traceback
        detail = traceback.format_exc()
        MessageBox.Show(
            "Error starting UI Standard Showcase:\n\n{}".format(detail[-1500:]),
            "Error",
            MessageBoxButton.OK,
            MessageBoxImage.Error
        )
