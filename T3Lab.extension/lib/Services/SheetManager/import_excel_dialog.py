# -*- coding: utf-8 -*-
"""
Sheet Manager - Import Excel Dialog

Copyright (c) T3Lab
"""

from System.Windows import Window, MessageBox, MessageBoxButton, MessageBoxImage, Thickness, GridLength
from System.Windows.Controls import (Grid, Label, Button, TextBox, DataGrid, DataGridTextColumn,
                                      StackPanel, ScrollViewer, RowDefinition, ColumnDefinition)
from System.Windows.Data import Binding
import System


class ImportExcelDialog(Window):
    """Dialog for importing sheet data from Excel"""
    
    def __init__(self, excel_service, revit_service, doc):
        self.excel_service = excel_service
        self.revit_service = revit_service
        self.doc = doc
        self.import_data = None
        self.result = False
        
        self._build_ui()
    
    def _build_ui(self):
        """Build dialog UI"""
        self.Title = "Import from Excel"
        self.Width = 900
        self.Height = 600
        self.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        
        # Main grid
        main_grid = Grid()
        main_grid.Margin = Thickness(20)
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(60)))  # File selection
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(40)))  # Info
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, System.Windows.GridUnitType.Star)))  # Preview
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(50)))  # Buttons
        
        # File selection
        file_panel = StackPanel()
        file_panel.Orientation = System.Windows.Controls.Orientation.Horizontal
        Grid.SetRow(file_panel, 0)
        
        file_label = Label()
        file_label.Content = "Excel File:"
        file_label.Width = 80
        file_label.VerticalAlignment = System.Windows.VerticalAlignment.Center
        file_panel.Children.Add(file_label)
        
        self.file_textbox = TextBox()
        self.file_textbox.Width = 600
        self.file_textbox.Margin = Thickness(5, 0, 5, 0)
        self.file_textbox.IsReadOnly = True
        file_panel.Children.Add(self.file_textbox)
        
        browse_btn = Button()
        browse_btn.Content = "Browse..."
        browse_btn.Width = 100
        browse_btn.Click += self.on_browse_click
        file_panel.Children.Add(browse_btn)
        
        main_grid.Children.Add(file_panel)
        
        # Info label
        self.info_label = Label()
        self.info_label.Content = "Select an Excel file to import sheet data"
        self.info_label.Foreground = System.Windows.Media.Brushes.Gray
        Grid.SetRow(self.info_label, 1)
        main_grid.Children.Add(self.info_label)
        
        # Preview grid
        preview_label = Label()
        preview_label.Content = "Preview:"
        preview_label.FontWeight = System.Windows.FontWeights.Bold
        Grid.SetRow(preview_label, 2)
        main_grid.Children.Add(preview_label)
        
        self.preview_grid = DataGrid()
        self.preview_grid.Margin = Thickness(0, 30, 0, 0)
        self.preview_grid.IsReadOnly = True
        self.preview_grid.AutoGenerateColumns = False
        self.preview_grid.CanUserAddRows = False
        self.preview_grid.CanUserDeleteRows = False
        Grid.SetRow(self.preview_grid, 2)
        
        # Columns
        from System.Windows.Controls import DataGridLength, DataGridLengthUnitType
        
        col_number = DataGridTextColumn()
        col_number.Header = "Sheet Number"
        col_number.Binding = Binding("sheet_number")
        col_number.Width = DataGridLength(120)
        self.preview_grid.Columns.Add(col_number)
        
        col_name = DataGridTextColumn()
        col_name.Header = "Sheet Name"
        col_name.Binding = Binding("sheet_name")
        col_name.Width = DataGridLength(250)
        self.preview_grid.Columns.Add(col_name)
        
        col_designed = DataGridTextColumn()
        col_designed.Header = "Designed By"
        col_designed.Binding = Binding("designed_by")
        col_designed.Width = DataGridLength(100)
        self.preview_grid.Columns.Add(col_designed)
        
        col_checked = DataGridTextColumn()
        col_checked.Header = "Checked By"
        col_checked.Binding = Binding("checked_by")
        col_checked.Width = DataGridLength(100)
        self.preview_grid.Columns.Add(col_checked)
        
        col_drawn = DataGridTextColumn()
        col_drawn.Header = "Drawn By"
        col_drawn.Binding = Binding("drawn_by")
        col_drawn.Width = DataGridLength(100)
        self.preview_grid.Columns.Add(col_drawn)
        
        col_approved = DataGridTextColumn()
        col_approved.Header = "Approved By"
        col_approved.Binding = Binding("approved_by")
        col_approved.Width = DataGridLength(100)
        self.preview_grid.Columns.Add(col_approved)
        
        main_grid.Children.Add(self.preview_grid)
        
        # Buttons
        btn_panel = StackPanel()
        btn_panel.Orientation = System.Windows.Controls.Orientation.Horizontal
        btn_panel.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
        Grid.SetRow(btn_panel, 3)
        
        self.import_btn = Button()
        self.import_btn.Content = "Import"
        self.import_btn.Width = 100
        self.import_btn.Height = 35
        self.import_btn.Margin = Thickness(0, 0, 10, 0)
        self.import_btn.IsEnabled = False
        self.import_btn.Click += self.on_import_click
        btn_panel.Children.Add(self.import_btn)
        
        cancel_btn = Button()
        cancel_btn.Content = "Cancel"
        cancel_btn.Width = 100
        cancel_btn.Height = 35
        cancel_btn.Click += self.on_cancel_click
        btn_panel.Children.Add(cancel_btn)
        
        main_grid.Children.Add(btn_panel)
        
        self.Content = main_grid
    
    def on_browse_click(self, sender, args):
        """Browse for Excel file"""
        import clr
        clr.AddReference('System.Windows.Forms')
        from System.Windows.Forms import OpenFileDialog, DialogResult
        
        dialog = OpenFileDialog()
        dialog.Filter = "Excel Files (*.xlsx;*.csv)|*.xlsx;*.csv|All Files (*.*)|*.*"
        dialog.Title = "Select Excel File"
        
        if dialog.ShowDialog() != DialogResult.OK:
            return
        
        filepath = dialog.FileName
        self.file_textbox.Text = filepath
        
        # Load and preview
        self.load_excel_data(filepath)
    
    def load_excel_data(self, filepath):
        """Load Excel data and show preview"""
        try:
            # Check file type
            if filepath.lower().endswith('.csv'):
                # Load CSV
                import csv
                data = []
                
                with open(filepath, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get('Sheet Number'):  # Has sheet number
                            data.append({
                                'sheet_number': row.get('Sheet Number', ''),
                                'sheet_name': row.get('Sheet Name', ''),
                                'designed_by': row.get('Designed By', '-'),
                                'checked_by': row.get('Checked By', '-'),
                                'drawn_by': row.get('Drawn By', '-'),
                                'approved_by': row.get('Approved By', '-')
                            })
            else:
                # Load Excel
                data = self.excel_service.import_sheets_from_excel(filepath)
            
            if not data:
                MessageBox.Show("No data found in file or file is empty", "Error",
                              MessageBoxButton.OK, MessageBoxImage.Error)
                return
            
            self.import_data = data
            
            # Convert to observable collection
            from System.Collections.ObjectModel import ObservableCollection
            from System import Object
            preview_items = ObservableCollection[Object]()
            
            for row in data:
                # Create anonymous object for preview
                item = type('PreviewItem', (), row)()
                preview_items.Add(item)
            
            self.preview_grid.ItemsSource = preview_items
            
            # Update info
            self.info_label.Content = "Found {} sheets in file".format(len(data))
            self.info_label.Foreground = System.Windows.Media.Brushes.Green
            self.import_btn.IsEnabled = True
            
        except Exception as e:
            MessageBox.Show("Error loading file: {}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
            import traceback
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass
    
    def on_import_click(self, sender, args):
        """Import data into Revit"""
        if not self.import_data:
            return
        
        result = MessageBox.Show(
            "Import {} sheets?\n\nThis will update existing sheets and create new ones.".format(
                len(self.import_data)),
            "Confirm Import",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question)
        
        if result != System.Windows.MessageBoxResult.Yes:
            return
        
        # Perform import
        from Autodesk.Revit.DB import Transaction
        
        t = Transaction(self.doc, "Import from Excel")
        t.Start()
        
        try:
            updated_count = 0
            created_count = 0
            error_count = 0
            
            for row in self.import_data:
                sheet_number = row['sheet_number']
                
                # Find existing sheet
                existing = None
                for sheet in self.revit_service.get_all_sheets():
                    if sheet.SheetNumber == sheet_number:
                        existing = sheet
                        break
                
                if existing:
                    # Update existing sheet
                    try:
                        existing.Name = row['sheet_name']
                        
                        # Update parameters
                        for param_name, value in [
                            ('Designed By', row.get('designed_by', '-')),
                            ('Checked By', row.get('checked_by', '-')),
                            ('Drawn By', row.get('drawn_by', '-')),
                            ('Approved By', row.get('approved_by', '-'))
                        ]:
                            param = existing.LookupParameter(param_name)
                            if param and not param.IsReadOnly and value:
                                param.Set(value)
                        
                        updated_count += 1
                    except:
                        error_count += 1
                else:
                    # Create new sheet (need titleblock)
                    # For now, skip creation
                    error_count += 1
            
            t.Commit()
            
            MessageBox.Show(
                "Import complete!\n\nUpdated: {}\nCreated: {}\nErrors: {}".format(
                    updated_count, created_count, error_count),
                "Import Complete",
                MessageBoxButton.OK,
                MessageBoxImage.Information)
            
            self.result = True
            self.Close()
            
        except Exception as e:
            t.RollBack()
            MessageBox.Show("Import failed: {}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
    
    def on_cancel_click(self, sender, args):
        """Cancel import"""
        self.Close()