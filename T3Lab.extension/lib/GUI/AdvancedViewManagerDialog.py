# -*- coding: utf-8 -*-
"""
Advanced View Manager Dialog
GUI classes and event handling for the Advanced View Manager.
"""

import os
import sys
import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('System.Windows.Forms')

import System
from System.Windows import (Window, MessageBox, MessageBoxButton, MessageBoxImage, 
                            GridLength, GridUnitType, Thickness, WindowState)
from System.Windows.Controls import (Grid, RowDefinition, ColumnDefinition, Border,
                                      StackPanel, TextBlock, TextBox, Button,
                                      ComboBox, ComboBoxItem, DataGrid, Orientation,
                                      DataGridTextColumn, ScrollViewer, ListBox, ListBoxItem,
                                      ContextMenu, MenuItem)
from System.Windows.Media import SolidColorBrush, Color, Brushes
from System.Windows.Forms import SaveFileDialog, OpenFileDialog, DialogResult
from System.Collections.ObjectModel import ObservableCollection
from System import Object

from pyrevit import forms, revit, DB
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Autodesk.Revit.DB import (
    FilteredElementCollector, View, ViewType, ElementId,
    BuiltInParameter, ViewDetailLevel, StorageType, Transaction
)

# Import Core/Execution functions
from core.advanced_view_manager import (
    _eid_int,
    _make_eid,
    EnhancedViewItem,
    build_viewport_map,
    update_view_name,
    update_view_template,
    update_scale,
    update_detail_level,
    update_title_on_sheet,
    duplicate_views,
    delete_views,
    create_views_from_defs,
    apply_excel_updates,
    write_xlsx,
    read_xlsx
)

# XAML Paths
GUI_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'AdvancedViewManager.xaml')
BATCH_XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'AdvancedViewManagerBatchRename.xaml')


# =====================================================
# PREVIEW ITEM FOR BATCH RENAME
# =====================================================

class PreviewItem(object):
    def __init__(self, old_name, new_name):
        self.old_name = old_name
        self.new_name = new_name


# =====================================================
# BATCH RENAME DIALOG
# =====================================================

class BatchRenameDialog(T3WPFWindow):
    """Dialog for batch renaming views"""
    
    def __init__(self, views, doc):
        T3WPFWindow.__init__(self, BATCH_XAML_FILE)
        self.views = views
        self.doc = doc
        self.preview_items = ObservableCollection[Object]()
        
        # Bind events
        self.find_box.TextChanged += self._on_option_changed
        self.replace_box.TextChanged += self._on_option_changed
        self.prefix_box.TextChanged += self._on_option_changed
        self.suffix_box.TextChanged += self._on_option_changed
        self.case_combo.SelectionChanged += self._on_option_changed
        
        self.apply_btn.Click += self._on_apply
        
        # Set ItemsSource of preview_grid
        self.preview_grid.ItemsSource = self.preview_items
        
        # Set Title and title_text:
        self.title_text.Text = "Batch Rename {0} View(s)".format(len(self.views))
        
        self._update_preview()
        
    def close_button_clicked(self, sender, e):
        self.Close()
    
    def _apply_rename_rules(self, name):
        new_name = name
        
        if self.find_box.Text:
            new_name = new_name.replace(self.find_box.Text, self.replace_box.Text)
        
        if self.prefix_box.Text:
            new_name = self.prefix_box.Text + new_name
        
        if self.suffix_box.Text:
            new_name = new_name + self.suffix_box.Text
        
        case_option = str(self.case_combo.SelectedItem) if self.case_combo.SelectedItem else "No Change"
        if case_option == "UPPERCASE":
            new_name = new_name.upper()
        elif case_option == "lowercase":
            new_name = new_name.lower()
        elif case_option == "Title Case":
            new_name = new_name.title()
        
        return new_name
    
    def _update_preview(self):
        self.preview_items.Clear()
        
        preview_count = min(20, len(self.views))
        
        for i in range(preview_count):
            view = self.views[i]
            old_name = view.name
            new_name = self._apply_rename_rules(old_name)
            
            item = PreviewItem(old_name, new_name)
            self.preview_items.Add(item)
    
    def _on_option_changed(self, sender, args):
        self._update_preview()
    
    def _on_apply(self, sender, args):
        t = Transaction(self.doc, "Batch Rename Views")
        t.Start()
        
        try:
            renamed = 0
            failed = 0
            
            for view_item in self.views:
                old_name = view_item.name
                new_name = self._apply_rename_rules(old_name)
                
                if new_name == old_name:
                    continue
                
                try:
                    view_item.element.Name = new_name
                    view_item.name = new_name
                    renamed += 1
                except:
                    failed += 1
            
            t.Commit()
            
            msg = "Renamed {0} view(s)".format(renamed)
            if failed > 0:
                msg += "\nFailed: {0} view(s)".format(failed)
            
            MessageBox.Show(msg, "Complete")
            self.DialogResult = True
            self.Close()
            
        except Exception as e:
            t.RollBack()
            MessageBox.Show("Error: {0}".format(str(e)), "Error")
    
    def _on_cancel(self, sender, args):
        self.DialogResult = False
        self.Close()


# =====================================================
# MAIN WINDOW - SHEET MANAGER STYLE
# =====================================================

class AdvancedViewManagerWindow(T3WPFWindow):
    """Advanced view manager with Sheet Manager style UI"""
    
    def __init__(self, doc, uidoc):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc
        self.uidoc = uidoc
        self.all_views = []
        self.filtered_views = ObservableCollection[Object]()
        self.custom_columns = {}
        
        # Bind events
        self.search_box.TextChanged += self._on_search_changed
        self.type_combo.SelectionChanged += self._on_filter_changed
        self.template_combo.SelectionChanged += self._on_filter_changed
        self.sheets_combo.SelectionChanged += self._on_filter_changed
        
        self.select_all_btn.Click += self._on_select_all
        self.clear_btn.Click += self._on_clear_all
        
        self.excel_btn.Click += self._on_excel
        self.refresh_btn.Click += self._on_refresh
        self.rename_btn.Click += self._on_batch_rename
        self.dup_btn.Click += self._on_duplicate
        self.del_btn.Click += self._on_delete
        self.close_btn.Click += self._on_close
        
        self.data_grid.SelectionChanged += self._on_selection_changed
        self.data_grid.CellEditEnding += self._on_cell_edit
        self.data_grid.MouseRightButtonUp += self._on_header_right_click
        
        # Bind items for standard combo columns
        self.template_items = self._get_all_templates()
        self.col_template.ItemsSource = to_items_source(self.template_items)
        self.col_detail.ItemsSource = to_items_source(["Coarse", "Medium", "Fine"])
        
        # Then load data (will update summary cards)
        self._load_all_views()
        self._apply_filters()
        
        
    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self.Close()
        
    def _load_all_views(self):
        """Load views"""
        self.all_views = []
        viewport_map = build_viewport_map(self.doc)

        collector = FilteredElementCollector(self.doc)\
            .OfClass(View)\
            .WhereElementIsNotElementType()

        for view in collector:
            if view.ViewType in [ViewType.ProjectBrowser, ViewType.SystemBrowser,
                                ViewType.Undefined, ViewType.Internal]:
                continue

            if view.IsTemplate:
                continue

            try:
                item = EnhancedViewItem(view, self.doc, viewport_map)
                self.all_views.append(item)
            except:
                pass
        
        self._update_summary_cards()
    
    def _get_all_templates(self):
        """Get all view templates"""
        templates = ["None"]
        
        collector = FilteredElementCollector(self.doc)\
            .OfClass(View)\
            .WhereElementIsNotElementType()
        
        for view in collector:
            if view.IsTemplate:
                templates.append(view.Name)
        
        return templates
    
    def _apply_filters(self):
        """Apply filters"""
        self.filtered_views.Clear()
        
        type_filter = str(self.type_combo.SelectedItem) if self.type_combo.SelectedItem else "All Sheets"
        template_filter = str(self.template_combo.SelectedItem) if self.template_combo.SelectedItem else "All Views"
        sheets_filter = str(self.sheets_combo.SelectedItem) if self.sheets_combo.SelectedItem else "All Views"
        search_text = self.search_box.Text.lower() if hasattr(self, 'search_box') and self.search_box.Text else ""
        
        for view in self.all_views:
            # Search filter
            if search_text and search_text not in view.name.lower():
                continue
            
            # Type filter
            if type_filter != "All Sheets" and view.view_type != type_filter:
                continue
            
            # Template filter
            if template_filter == "With Template" and view.view_template == "None":
                continue
            elif template_filter == "Without Template" and view.view_template != "None":
                continue
            
            # Sheets filter
            if sheets_filter == "On Sheets" and view.on_sheets == 0:
                continue
            elif sheets_filter == "Not On Sheets" and view.on_sheets > 0:
                continue
            
            self.filtered_views.Add(view)
        
        self._update_summary_cards()
    
    def _update_summary_cards(self):
        """Update summary card values"""
        # TOTAL
        if hasattr(self, 'total_value_text') and self.total_value_text is not None:
            total = len(self.all_views)
            self.total_value_text.Text = str(total)
            self.total_value_text.InvalidateVisual()
            self.total_value_text.UpdateLayout()
        
        # CATEGORIES
        if hasattr(self, 'types_value_text') and self.types_value_text is not None:
            types = set(v.view_type for v in self.all_views)
            type_count = len(types)
            self.types_value_text.Text = str(type_count)
            self.types_value_text.InvalidateVisual()
            self.types_value_text.UpdateLayout()
        
        # FILTERS
        if hasattr(self, 'filters_value_text') and self.filters_value_text is not None:
            self.filters_value_text.Text = "Active"
            self.filters_value_text.InvalidateVisual()
            self.filters_value_text.UpdateLayout()
        
        # Force update
        self.InvalidateVisual()
        self.UpdateLayout()
    
    def _on_select_all(self, sender, args):
        """Select all views in grid"""
        self.data_grid.SelectAll()
    
    def _on_clear_all(self, sender, args):
        """Clear all selections"""
        self.data_grid.UnselectAll()
    
    def _on_selection_changed(self, sender, args):
        """Update selected count"""
        if hasattr(self, 'selected_value_text'):
            selected = self.data_grid.SelectedItems
            self.selected_value_text.Text = str(len(selected))
    
    def _on_search_changed(self, sender, args):
        """Search changed"""
        self._apply_filters()
    
    def _on_filter_changed(self, sender, args):
        """Filter changed"""
        self._apply_filters()
    
    def _on_cell_edit(self, sender, args):
        """Handle cell edit"""
        from System.Windows.Controls import DataGridEditAction
        if args.EditAction == DataGridEditAction.Cancel:
            return
        
        try:
            item = args.Row.Item
            column = args.Column
            
            if column.Header == "View Name":
                new_name = args.EditingElement.Text
                update_view_name(self.doc, item, new_name)
            
            elif column.Header == "View Template":
                new_template = args.EditingElement.SelectedItem
                update_view_template(self.doc, item, new_template)
            
            elif column.Header == "Scale":
                new_scale = args.EditingElement.Text
                update_scale(self.doc, item, new_scale)
            
            elif column.Header == "Detail Level":
                new_detail = args.EditingElement.SelectedItem
                update_detail_level(self.doc, item, new_detail)
            
            elif column.Header == "Title on Sheet":
                new_title = args.EditingElement.Text
                update_title_on_sheet(self.doc, item, new_title)
        
        except Exception as e:
            MessageBox.Show("Error: {0}".format(str(e)), "Error")
    
    def _on_batch_rename(self, sender, args):
        """Batch rename"""
        selected = list(self.data_grid.SelectedItems)
        
        if not selected:
            MessageBox.Show("Select views", "No Selection")
            return
        
        dialog = BatchRenameDialog(selected, self.doc)
        dialog.Owner = self
        result = dialog.ShowDialog()
        
        if result:
            self._load_all_views()
            self._apply_filters()
    
    def _on_duplicate(self, sender, args):
        """Duplicate"""
        selected = list(self.data_grid.SelectedItems)
        if not selected:
            MessageBox.Show("Select views", "No Selection")
            return
        
        try:
            count = duplicate_views(self.doc, selected)
            MessageBox.Show("Duplicated {0} view(s)".format(count), "Success")
            self._load_all_views()
            self._apply_filters()
        except Exception as e:
            MessageBox.Show("Error: {0}".format(str(e)), "Error")
    
    def _on_delete(self, sender, args):
        """Delete"""
        selected = list(self.data_grid.SelectedItems)
        if not selected:
            MessageBox.Show("Select views", "No Selection")
            return
        
        result = MessageBox.Show("Delete {0} view(s)?".format(len(selected)), 
                                "Confirm", 
                                MessageBoxButton.YesNo)
        
        if result != MessageBoxResult.Yes:
            return
        
        try:
            count = delete_views(self.doc, selected)
            MessageBox.Show("Deleted {0} view(s)".format(count), "Success")
            self._load_all_views()
            self._apply_filters()
        except Exception as e:
            MessageBox.Show("Error: {0}".format(str(e)), "Error")
    
    def _on_excel(self, sender, args):
        """Excel Export/Import menu"""
        menu = ContextMenu()
        
        export_item = MenuItem()
        export_item.Header = "Export to Excel..."
        export_item.Click += self._on_export_excel
        menu.Items.Add(export_item)
        
        import_item = MenuItem()
        import_item.Header = "Import from Excel (Update Existing)..."
        import_item.Click += self._on_import_excel
        menu.Items.Add(import_item)
        
        sep = System.Windows.Controls.Separator()
        menu.Items.Add(sep)
        
        create_item = MenuItem()
        create_item.Header = "Import from Excel (Create New Views)..."
        create_item.Click += self._on_create_views_from_excel
        menu.Items.Add(create_item)
        
        menu.PlacementTarget = sender
        menu.IsOpen = True
    
    def _on_export_excel(self, sender, args):
        """Export views to Excel"""
        try:
            dialog = SaveFileDialog()
            dialog.Filter = "Excel Files (*.xlsx)|*.xlsx"
            dialog.Title = "Export Views to Excel"
            dialog.FileName = "Views_Export.xlsx"
            
            if dialog.ShowDialog() != DialogResult.OK:
                return
            
            filepath = dialog.FileName
            
            # Headers
            base_headers = ["Element ID", "View Name", "Type", "Level", "View Template", "Scale", "Detail Level", 
                          "Title on Sheet", "Sheet Number", "Sheet Name", "On Sheets",
                          "Crop Active", "Crop Visible", "Crop Min", "Crop Max"]
            
            all_headers = base_headers[:]
            if self.custom_columns:
                for col_name in self.custom_columns.keys():
                    all_headers.append(col_name)
            
            # Build rows
            rows = []
            for view_item in self.filtered_views:
                try:
                    row = [
                        _eid_int(view_item.id),
                        view_item.name or "",
                        view_item.view_type or "",
                        view_item.level_name or "",
                        view_item.view_template or "",
                        view_item.scale or "",
                        view_item.detail_level or "",
                        view_item.title_on_sheet or "",
                        view_item.sheet_number or "",
                        view_item.sheet_name or "",
                        view_item.on_sheets or "",
                        view_item.crop_active or "",
                        view_item.crop_visible or "",
                        view_item.crop_min or "",
                        view_item.crop_max or "",
                    ]
                    
                    # Custom parameter columns
                    for i in range(len(self.custom_columns)):
                        binding_name = "param_{}".format(i)
                        value = getattr(view_item, binding_name, "") if hasattr(view_item, binding_name) else ""
                        row.append(value or "")
                    
                    rows.append(row)
                except:
                    continue
            
            header_colors = {0: 'D4E6A5'}
            for ci in range(1, len(base_headers)):
                header_colors[ci] = '0F172A'
            for ci in range(len(base_headers), len(all_headers)):
                header_colors[ci] = 'CCEBFF'
            
            write_xlsx(filepath, all_headers, rows, hidden_cols=[0], header_colors=header_colors)
            
            custom_msg = ""
            if self.custom_columns:
                custom_msg = "\n\nIncluding {} custom parameter column(s): {}".format(
                    len(self.custom_columns), 
                    ", ".join(self.custom_columns.keys())
                )
            
            MessageBox.Show(
                "Exported {0} views to:\n{1}\n\nTip: Don't delete column A (Element ID) - it's needed for import!{2}".format(
                    len(rows), filepath, custom_msg),
                "Export Successful",
                MessageBoxButton.OK,
                MessageBoxImage.Information
            )
        
        except Exception as e:
            MessageBox.Show("Export error: {0}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
    
    def _on_import_excel(self, sender, args):
        """Import views from Excel - update existing views"""
        try:
            dialog = OpenFileDialog()
            dialog.Filter = "Excel Files (*.xlsx)|*.xlsx"
            dialog.Title = "Import Views from Excel (Update Existing)"
            
            if dialog.ShowDialog() != DialogResult.OK:
                return
            
            filepath = dialog.FileName
            
            headers, rows = read_xlsx(filepath)
            
            if not headers or not rows:
                MessageBox.Show("No data found in Excel file", "No Data",
                               MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            
            header_map = {}
            for i, h in enumerate(headers):
                header_map[h.strip()] = i
            
            base_names = {"Element ID", "View Name", "Type", "Level", "View Template", "Scale", 
                          "Detail Level", "Title on Sheet", "Sheet Number", "Sheet Name", "On Sheets",
                          "Crop Active", "Crop Visible", "Crop Min", "Crop Max"}
            custom_param_cols = {}
            for h, ci in header_map.items():
                if h and h not in base_names:
                    custom_param_cols[ci] = h
            
            updates = []
            for row in rows:
                def _get(col_name):
                    idx = header_map.get(col_name)
                    if idx is not None and idx < len(row):
                        return row[idx]
                    return None
                
                element_id = _get("Element ID")
                view_name = _get("View Name")
                
                if not element_id and not view_name:
                    continue
                
                update = {
                    'element_id': int(float(element_id)) if element_id else None,
                    'view_name': str(view_name) if view_name else None,
                    'template': str(_get("View Template") or ""),
                    'scale': _get("Scale"),
                    'detail_level': str(_get("Detail Level") or ""),
                    'title': None,
                    'custom_params': {},
                    'crop_active': str(_get("Crop Active") or "").strip(),
                    'crop_visible': str(_get("Crop Visible") or "").strip(),
                    'crop_min': str(_get("Crop Min") or "").strip(),
                    'crop_max': str(_get("Crop Max") or "").strip(),
                }
                
                for ci, param_name in custom_param_cols.items():
                    if ci < len(row) and row[ci]:
                        update['custom_params'][param_name] = str(row[ci])
                
                updates.append(update)
            
            if not updates:
                MessageBox.Show("No valid data found in Excel file", "No Data",
                               MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            
            custom_msg = ""
            if custom_param_cols:
                custom_msg = "\n\nIncluding {} custom parameter column(s)".format(len(custom_param_cols))
            
            result = MessageBox.Show(
                "Update {0} existing views from Excel?{1}".format(len(updates), custom_msg),
                "Confirm Import",
                MessageBoxButton.YesNo,
                MessageBoxImage.Question
            )
            
            if result != MessageBoxResult.Yes:
                return
            
            count, skipped, cp_updates, cp_errors = apply_excel_updates(self.doc, updates, self.all_views)
            
            msg = "Updated {0} views from Excel!".format(count)
            if skipped > 0:
                msg += "\n{0} views skipped (not found).".format(skipped)
            if cp_updates > 0:
                msg += "\n\nCustom parameters: {0} updates applied successfully!".format(cp_updates)
            
            if cp_errors:
                msg += "\n\nWarnings:"
                for param_name, error in cp_errors[:5]:
                    msg += "\n- {}: {}".format(param_name, error)
                if len(cp_errors) > 5:
                    msg += "\n... and {} more errors".format(len(cp_errors) - 5)
            
            MessageBox.Show(msg, "Import Successful" if count > 0 else "Import Complete",
                          MessageBoxButton.OK, MessageBoxImage.Information)
            
            self._refresh_all_data()
        
        except Exception as e:
            MessageBox.Show("Import error: {0}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
    
    def _on_create_views_from_excel(self, sender, args):
        """Create new views from Excel file"""
        try:
            dialog = OpenFileDialog()
            dialog.Filter = "Excel Files (*.xlsx)|*.xlsx"
            dialog.Title = "Import Excel - Create New Views"
            
            if dialog.ShowDialog() != DialogResult.OK:
                return
            
            filepath = dialog.FileName
            
            headers, rows = read_xlsx(filepath)
            
            if not headers or not rows:
                MessageBox.Show("No data found in Excel file", "No Data",
                               MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            
            header_map = {}
            for i, h in enumerate(headers):
                header_map[h.strip()] = i
            
            view_defs = []
            for row in rows:
                def _get(col_name):
                    idx = header_map.get(col_name)
                    if idx is not None and idx < len(row):
                        return row[idx]
                    return None
                
                view_name = _get("View Name")
                view_type = _get("Type")
                
                if not view_name or not view_type:
                    continue
                
                view_def = {
                    'name': str(view_name).strip(),
                    'type': str(view_type).strip(),
                    'level': str(_get("Level") or "").strip(),
                    'template': str(_get("View Template") or ""),
                    'scale': _get("Scale"),
                    'detail_level': str(_get("Detail Level") or ""),
                    'crop_active': str(_get("Crop Active") or "").strip(),
                    'crop_visible': str(_get("Crop Visible") or "").strip(),
                    'crop_min': str(_get("Crop Min") or "").strip(),
                    'crop_max': str(_get("Crop Max") or "").strip(),
                }
                view_defs.append(view_def)
            
            if not view_defs:
                MessageBox.Show("No valid view definitions found.\n\n"
                              "Required columns:\n"
                              "  B: View Name\n"
                              "  C: Type (Floor Plan, Ceiling Plan, Drafting View, etc.)",
                              "No Data", MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            
            supported_types = ["Floor Plan", "Ceiling Plan", "Structural Plan",
                              "Drafting View", "Area Plan", "3D View", "Section", "Legend"]
            
            creatable = []
            skipped = []
            for vd in view_defs:
                if vd['type'] in supported_types:
                    creatable.append(vd)
                else:
                    skipped.append(vd)
            
            if not creatable:
                skip_msg = "\n".join(["  - {} ({})".format(s['name'], s['type']) for s in skipped[:10]])
                MessageBox.Show(
                    "No views can be created.\n\n"
                    "Supported types: {}\n\n"
                    "Skipped:\n{}".format(", ".join(supported_types), skip_msg),
                    "Cannot Create", MessageBoxButton.OK, MessageBoxImage.Warning)
                return
            
            type_counts = {}
            for vd in creatable:
                t = vd['type']
                type_counts[t] = type_counts.get(t, 0) + 1
            
            summary_lines = ["  {} x {}".format(cnt, tp) for tp, cnt in type_counts.items()]
            skip_msg = ""
            if skipped:
                skip_msg = "\n\nSkipped ({} unsupported):\n".format(len(skipped))
                skip_msg += "\n".join(["  - {} ({})".format(s['name'], s['type']) for s in skipped[:5]])
                if len(skipped) > 5:
                    skip_msg += "\n  ... and {} more".format(len(skipped) - 5)
            
            result = MessageBox.Show(
                "Create {} new views?\n\n{}{}\n\n"
                "Level matching: Uses 'Level' column if available, "
                "otherwise tries to match level name from view name.\n"
                "Views with names already existing will be skipped.".format(
                    len(creatable), "\n".join(summary_lines), skip_msg),
                "Confirm Create Views",
                MessageBoxButton.YesNo,
                MessageBoxImage.Question
            )
            
            if result != MessageBoxResult.Yes:
                return
            
            created, dup_skipped, failed = create_views_from_defs(self.doc, creatable)
            
            msg = "Created {} new view(s)!".format(created)
            if dup_skipped > 0:
                msg += "\n{} skipped (name already exists).".format(dup_skipped)
            if failed:
                msg += "\n\nFailed ({}):\n".format(len(failed))
                for name, reason in failed[:15]:
                    short_reason = reason.split('\n')[0] if '\n' in reason else reason
                    if len(short_reason) > 80:
                        short_reason = short_reason[:77] + "..."
                    msg += "  - {}: {}\n".format(name, short_reason)
                if len(failed) > 15:
                    msg += "  ... and {} more\n".format(len(failed) - 15)
            
            MessageBox.Show(msg, "Create Views Complete",
                          MessageBoxButton.OK, MessageBoxImage.Information)
            self._refresh_all_data()
        
        except Exception as e:
            MessageBox.Show("Create views error: {0}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
            
    def _refresh_all_data(self):
        """Refresh views and custom parameter values"""
        self._load_all_views()
        
        if self.custom_columns:
            for i, (col_name, param_name) in enumerate(self.custom_columns.items()):
                binding_name = "param_{}".format(i)
                for item in self.all_views:
                    try:
                        param = item.element.LookupParameter(param_name)
                        if param and param.HasValue:
                            if param.StorageType == StorageType.String:
                                value = param.AsString() or ""
                            elif param.StorageType == StorageType.Integer:
                                value = str(param.AsInteger())
                            elif param.StorageType == StorageType.Double:
                                value = str(param.AsDouble())
                            elif param.StorageType == StorageType.ElementId:
                                elem_id = param.AsElementId()
                                if elem_id and _eid_int(elem_id) > 0:
                                    elem = self.doc.GetElement(elem_id)
                                    value = elem.Name if elem else str(_eid_int(elem_id))
                                else:
                                    value = ""
                            else:
                                value = param.AsValueString() or ""
                        else:
                            value = ""
                        
                        setattr(item, binding_name, value)
                    except:
                        setattr(item, binding_name, "")
        
        self._apply_filters()
        self.data_grid.Items.Refresh()
    
    def _on_refresh(self, sender, args):
        """Refresh button handler"""
        try:
            self._refresh_all_data()
            MessageBox.Show("Views refreshed successfully!", "Refresh Complete",
                          MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as e:
            MessageBox.Show("Error refreshing views: {0}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
    
    def _on_close(self, sender, args):
        """Close"""
        self.Close()
        
    def _on_header_right_click(self, sender, args):
        """Show context menu on header right-click"""
        try:
            hit_test = System.Windows.Media.VisualTreeHelper.HitTest(self.data_grid, args.GetPosition(self.data_grid))
            
            if hit_test and hit_test.VisualHit:
                element = hit_test.VisualHit
                while element:
                    if isinstance(element, System.Windows.Controls.Primitives.DataGridColumnHeader):
                        menu = ContextMenu()
                        
                        add_item = MenuItem()
                        add_item.Header = "Add Parameter Column..."
                        add_item.Click += self._on_add_parameter_column
                        menu.Items.Add(add_item)
                        
                        if self.custom_columns:
                            separator = System.Windows.Controls.Separator()
                            menu.Items.Add(separator)
                            
                            for col_name in self.custom_columns.keys():
                                remove_item = MenuItem()
                                remove_item.Header = "Remove '{}'".format(col_name)
                                remove_item.Tag = col_name
                                remove_item.Click += self._on_remove_parameter_column
                                menu.Items.Add(remove_item)
                        
                        menu.PlacementTarget = element
                        menu.IsOpen = True
                        args.Handled = True
                        return
                    
                    element = System.Windows.Media.VisualTreeHelper.GetParent(element)
        except:
            pass
            
    def _on_add_parameter_column(self, sender, args):
        """Add a custom parameter column"""
        try:
            if not self.all_views:
                MessageBox.Show("No views found in project", "Error",
                              MessageBoxButton.OK, MessageBoxImage.Error)
                return
            
            all_params = set()
            sample_size = min(100, len(self.all_views))
            
            for view_item in self.all_views[:sample_size]:
                if not view_item.element:
                    continue
                
                view = view_item.element
                for param in view.Parameters:
                    if param.Definition and param.Definition.Name:
                        param_name = param.Definition.Name
                        all_params.add(param_name)
            
            if not all_params:
                MessageBox.Show("No parameters found", "Error",
                              MessageBoxButton.OK, MessageBoxImage.Error)
                return
            
            params = sorted(list(all_params))
            
            dialog = Window()
            dialog.Title = "Add Parameter Column"
            dialog.Width = 500
            dialog.Height = 400
            dialog.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
            dialog.Background = Brushes.White
            dialog.FontFamily = System.Windows.Media.FontFamily("Inter")
            
            main_grid = Grid()
            main_grid.Margin = Thickness(20)
            main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(50)))
            main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Star)))
            main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(60)))
            
            title_panel = StackPanel()
            Grid.SetRow(title_panel, 0)
            
            title = TextBlock()
            title.Text = "Select Parameter to Add as Column"
            title.FontSize = 14
            title.FontWeight = System.Windows.FontWeights.Bold
            title.Foreground = SolidColorBrush(Color.FromRgb(15, 23, 42))
            title.Margin = Thickness(0, 0, 0, 5)
            title_panel.Children.Add(title)
            
            instruction = TextBlock()
            instruction.Text = "Choose a view parameter from the list below:"
            instruction.FontSize = 11
            gray_color = Color.FromRgb(100, 116, 139)
            instruction.Foreground = SolidColorBrush(gray_color)
            title_panel.Children.Add(instruction)
            main_grid.Children.Add(title_panel)
            
            list_container = Grid()
            list_container.Margin = Thickness(0, 10, 0, 10)
            Grid.SetRow(list_container, 1)
            list_container.RowDefinitions.Add(RowDefinition(Height=GridLength(35)))
            list_container.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Star)))
            
            search_label = TextBlock()
            search_label.Text = "Search:"
            search_label.Margin = Thickness(0, 0, 0, 5)
            search_label.FontSize = 10
            search_label.Foreground = SolidColorBrush(Color.FromRgb(15, 23, 42))
            Grid.SetRow(search_label, 0)
            list_container.Children.Add(search_label)
            
            search_box = TextBox()
            search_box.Margin = Thickness(50, 0, 0, 5)
            search_box.Padding = Thickness(6, 4, 6, 4)
            search_box.BorderBrush = SolidColorBrush(Color.FromRgb(203, 213, 225))
            Grid.SetRow(search_box, 0)
            
            scroll = ScrollViewer()
            scroll.VerticalScrollBarVisibility = System.Windows.Controls.ScrollBarVisibility.Auto
            gray_border = Color.FromRgb(226, 232, 240)
            scroll.BorderBrush = SolidColorBrush(gray_border)
            scroll.BorderThickness = Thickness(1)
            Grid.SetRow(scroll, 1)
            
            param_listbox = ListBox()
            param_listbox.Padding = Thickness(5)
            
            for param_name in params:
                item = ListBoxItem()
                item.Content = param_name
                item.Padding = Thickness(8, 6, 8, 6)
                item.FontSize = 12
                param_listbox.Items.Add(item)
            
            if param_listbox.Items.Count > 0:
                param_listbox.SelectedIndex = 0
            
            scroll.Content = param_listbox
            list_container.Children.Add(scroll)
            
            def on_search_changed(s, e):
                search_text = search_box.Text.lower()
                param_listbox.Items.Clear()
                for param_name in params:
                    if not search_text or search_text in param_name.lower():
                        item = ListBoxItem()
                        item.Content = param_name
                        item.Padding = Thickness(8, 6, 8, 6)
                        item.FontSize = 12
                        param_listbox.Items.Add(item)
                if param_listbox.Items.Count > 0:
                    param_listbox.SelectedIndex = 0
            
            search_box.TextChanged += on_search_changed
            list_container.Children.Add(search_box)
            main_grid.Children.Add(list_container)
            
            info_text = TextBlock()
            info_text.Text = "{} parameters available".format(len(params))
            info_text.FontSize = 10
            gray_info = Color.FromRgb(100, 116, 139)
            info_text.Foreground = SolidColorBrush(gray_info)
            info_text.HorizontalAlignment = System.Windows.HorizontalAlignment.Left
            info_text.Margin = Thickness(0, 0, 0, 10)
            Grid.SetRow(info_text, 2)
            main_grid.Children.Add(info_text)
            
            btn_panel = StackPanel()
            btn_panel.Orientation = Orientation.Horizontal
            btn_panel.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
            btn_panel.VerticalAlignment = System.Windows.VerticalAlignment.Bottom
            Grid.SetRow(btn_panel, 2)
            
            result_holder = [False]
            def on_ok(s, e):
                if param_listbox.SelectedIndex < 0:
                    MessageBox.Show("Please select a parameter", "Info",
                                  MessageBoxButton.OK, MessageBoxImage.Information)
                    return
                result_holder[0] = True
                dialog.Close()
            
            def on_cancel(s, e):
                result_holder[0] = False
                dialog.Close()
            
            ok_btn = Button()
            ok_btn.Content = "Add Column"
            ok_btn.Width = 100
            ok_btn.Height = 32
            ok_btn.Margin = Thickness(5, 0, 5, 0)
            green_color = Color.FromRgb(16, 185, 129)
            ok_btn.Background = SolidColorBrush(green_color)
            ok_btn.Foreground = Brushes.White
            ok_btn.BorderThickness = Thickness(0)
            ok_btn.FontWeight = System.Windows.FontWeights.SemiBold
            ok_btn.Cursor = System.Windows.Input.Cursors.Hand
            ok_btn.Click += on_ok
            btn_panel.Children.Add(ok_btn)
            
            cancel_btn = Button()
            cancel_btn.Content = "Cancel"
            cancel_btn.Width = 100
            cancel_btn.Height = 32
            cancel_btn.Background = Brushes.White
            cancel_btn.Foreground = SolidColorBrush(Color.FromRgb(15, 23, 42))
            cancel_btn.BorderBrush = SolidColorBrush(Color.FromRgb(15, 23, 42))
            cancel_btn.BorderThickness = Thickness(1)
            cancel_btn.Cursor = System.Windows.Input.Cursors.Hand
            cancel_btn.Click += on_cancel
            btn_panel.Children.Add(cancel_btn)
            main_grid.Children.Add(btn_panel)
            
            copyright_block = TextBlock()
            copyright_block.Text = u"© Copyright by T3Lab"
            copyright_block.FontSize = 11
            copyright_block.Foreground = SolidColorBrush(Color.FromRgb(245, 158, 11))
            copyright_block.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
            copyright_block.VerticalAlignment = System.Windows.VerticalAlignment.Bottom
            copyright_block.IsHitTestVisible = False
            copyright_block.Margin = Thickness(0, 0, 14, 8)
            System.Windows.Controls.Panel.SetZIndex(copyright_block, 999)
            Grid.SetRowSpan(copyright_block, 3)
            main_grid.Children.Add(copyright_block)
            
            dialog.Content = main_grid
            dialog.ShowDialog()
            
            if not result_holder[0] or param_listbox.SelectedIndex < 0:
                return
            
            param_name = param_listbox.SelectedItem.Content
            
            if param_name in self.custom_columns.values():
                MessageBox.Show("This parameter is already displayed", "Info",
                              MessageBoxButton.OK, MessageBoxImage.Information)
                return
            
            col_index = len(self.custom_columns)
            col_name = param_name
            binding_name = "param_{}".format(col_index)
            
            new_col = DataGridTextColumn()
            new_col.Header = col_name
            new_col.Binding = System.Windows.Data.Binding(binding_name)
            new_col.Width = DataGridLength(150)
            new_col.IsReadOnly = True
            self.data_grid.Columns.Add(new_col)
            
            self.custom_columns[col_name] = param_name
            
            populated_count = 0
            for item in self.all_views:
                try:
                    param = item.element.LookupParameter(param_name)
                    if param and param.HasValue:
                        if param.StorageType == StorageType.String:
                            value = param.AsString() or ""
                        elif param.StorageType == StorageType.Integer:
                            value = str(param.AsInteger())
                        elif param.StorageType == StorageType.Double:
                            value = str(param.AsDouble())
                        elif param.StorageType == StorageType.ElementId:
                            elem_id = param.AsElementId()
                            if elem_id and _eid_int(elem_id) > 0:
                                elem = self.doc.GetElement(elem_id)
                                value = elem.Name if elem else str(_eid_int(elem_id))
                            else:
                                value = ""
                        else:
                            value = param.AsValueString() or ""
                        
                        if value:
                            populated_count += 1
                    else:
                        value = ""
                    
                    setattr(item, binding_name, value)
                except:
                    setattr(item, binding_name, "")
            
            self.data_grid.Items.Refresh()
            
            msg = "Parameter column '{}' added successfully!".format(param_name)
            empty_count = len(self.all_views) - populated_count
            if empty_count > 0:
                msg += "\n\n{} of {} views have this parameter with values.".format(
                    populated_count, len(self.all_views))
                msg += "\n{} views don't have this parameter or have empty values.".format(empty_count)
            
            MessageBox.Show(msg, "Success", MessageBoxButton.OK, MessageBoxImage.Information)
        
        except Exception as e:
            MessageBox.Show("Error adding parameter: {}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
            
    def _on_remove_parameter_column(self, sender, args):
        """Remove a custom parameter column"""
        try:
            col_name = sender.Tag
            if col_name not in self.custom_columns:
                return
            
            col_to_remove = None
            for col in self.data_grid.Columns:
                if col.Header == col_name:
                    col_to_remove = col
                    break
            
            if col_to_remove:
                self.data_grid.Columns.Remove(col_to_remove)
                del self.custom_columns[col_name]
                MessageBox.Show("Parameter column '{}' removed".format(col_name),
                              "Success", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as e:
            MessageBox.Show("Error removing column: {}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)


