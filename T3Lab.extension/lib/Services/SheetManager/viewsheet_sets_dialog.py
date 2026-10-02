# -*- coding: utf-8 -*-
"""
Sheet Manager - ViewSheet Sets Dialog

Copyright (c) T3Lab
"""

from System.Windows import Window, MessageBox, MessageBoxButton, MessageBoxImage, Thickness, GridLength
from System.Windows.Controls import (Grid, Label, Button, TextBox, ListBox, CheckBox,
                                      StackPanel, RowDefinition, ColumnDefinition)
import System


class ViewSheetSetsDialog(Window):
    """Dialog for managing ViewSheet Sets"""
    
    def __init__(self, sets_service, doc, selected_sheets=None):
        self.sets_service = sets_service
        self.doc = doc
        self.current_set = None
        self.all_sheets = []
        self.selected_sheets = selected_sheets or []  # Store pre-selected sheets
        
        self._build_ui()
        self._load_data()
    
    def _build_ui(self):
        """Build dialog UI"""
        self.Title = "ViewSheet Sets Management"
        self.Width = 800
        self.Height = 600
        self.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        
        # Main grid
        main_grid = Grid()
        main_grid.Margin = Thickness(20)
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, System.Windows.GridUnitType.Star)))
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(50)))
        main_grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(300)))
        main_grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(20)))
        main_grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(1, System.Windows.GridUnitType.Star)))
        
        # Left panel - Sets list
        left_panel = Grid()
        left_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(40)))
        left_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(1, System.Windows.GridUnitType.Star)))
        left_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(50)))
        Grid.SetRow(left_panel, 0)
        Grid.SetColumn(left_panel, 0)
        
        # Sets label
        sets_label = Label()
        sets_label.Content = "Sheet Sets:"
        sets_label.FontWeight = System.Windows.FontWeights.Bold
        Grid.SetRow(sets_label, 0)
        left_panel.Children.Add(sets_label)
        
        # Sets listbox
        self.sets_listbox = ListBox()
        self.sets_listbox.SelectionChanged += self.on_set_selected
        Grid.SetRow(self.sets_listbox, 1)
        left_panel.Children.Add(self.sets_listbox)
        
        # Sets buttons
        sets_btn_panel = StackPanel()
        sets_btn_panel.Orientation = System.Windows.Controls.Orientation.Horizontal
        sets_btn_panel.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        Grid.SetRow(sets_btn_panel, 2)
        
        add_set_btn = Button()
        add_set_btn.Content = "New"
        add_set_btn.Width = 70
        add_set_btn.Height = 30
        add_set_btn.Margin = Thickness(5, 0, 5, 0)
        add_set_btn.Click += self.on_new_set_click
        sets_btn_panel.Children.Add(add_set_btn)
        
        rename_set_btn = Button()
        rename_set_btn.Content = "Rename"
        rename_set_btn.Width = 70
        rename_set_btn.Height = 30
        rename_set_btn.Margin = Thickness(5, 0, 5, 0)
        rename_set_btn.Click += self.on_rename_set_click
        sets_btn_panel.Children.Add(rename_set_btn)
        
        delete_set_btn = Button()
        delete_set_btn.Content = "Delete"
        delete_set_btn.Width = 70
        delete_set_btn.Height = 30
        delete_set_btn.Margin = Thickness(5, 0, 5, 0)
        delete_set_btn.Click += self.on_delete_set_click
        sets_btn_panel.Children.Add(delete_set_btn)
        
        left_panel.Children.Add(sets_btn_panel)
        main_grid.Children.Add(left_panel)
        
        # Right panel - Sheets in set
        right_panel = Grid()
        right_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(40)))
        right_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(1, System.Windows.GridUnitType.Star)))
        right_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(50)))
        Grid.SetRow(right_panel, 0)
        Grid.SetColumn(right_panel, 2)
        
        # Sheets label
        sheets_label = Label()
        sheets_label.Content = "Sheets in Set:"
        sheets_label.FontWeight = System.Windows.FontWeights.Bold
        Grid.SetRow(sheets_label, 0)
        right_panel.Children.Add(sheets_label)
        
        # Sheets listbox with checkboxes
        from System.Windows.Controls import ScrollViewer
        scroll = ScrollViewer()
        scroll.VerticalScrollBarVisibility = System.Windows.Controls.ScrollBarVisibility.Auto
        Grid.SetRow(scroll, 1)
        
        self.sheets_panel = StackPanel()
        scroll.Content = self.sheets_panel
        right_panel.Children.Add(scroll)
        
        # Sheets buttons
        sheets_btn_panel = StackPanel()
        sheets_btn_panel.Orientation = System.Windows.Controls.Orientation.Horizontal
        sheets_btn_panel.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        Grid.SetRow(sheets_btn_panel, 2)
        
        select_all_btn = Button()
        select_all_btn.Content = "Select All"
        select_all_btn.Width = 90
        select_all_btn.Height = 30
        select_all_btn.Margin = Thickness(5, 0, 5, 0)
        select_all_btn.Click += self.on_select_all_click
        sheets_btn_panel.Children.Add(select_all_btn)
        
        select_none_btn = Button()
        select_none_btn.Content = "Select None"
        select_none_btn.Width = 90
        select_none_btn.Height = 30
        select_none_btn.Margin = Thickness(5, 0, 5, 0)
        select_none_btn.Click += self.on_select_none_click
        sheets_btn_panel.Children.Add(select_none_btn)
        
        apply_btn = Button()
        apply_btn.Content = "Apply"
        apply_btn.Width = 90
        apply_btn.Height = 30
        apply_btn.Margin = Thickness(5, 0, 5, 0)
        apply_btn.Click += self.on_apply_sheets_click
        sheets_btn_panel.Children.Add(apply_btn)
        
        right_panel.Children.Add(sheets_btn_panel)
        main_grid.Children.Add(right_panel)
        
        # Close button
        close_btn = Button()
        close_btn.Content = "Close"
        close_btn.Width = 100
        close_btn.Height = 35
        close_btn.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
        close_btn.Margin = Thickness(0, 10, 0, 0)
        close_btn.Click += lambda s, e: self.Close()
        Grid.SetRow(close_btn, 1)
        Grid.SetColumn(close_btn, 2)
        main_grid.Children.Add(close_btn)
        
        self.Content = main_grid
    
    def _load_data(self):
        """Load sheet sets and all sheets"""
        # Load sets
        self.refresh_sets()
        
        # Load all sheets
        from Autodesk.Revit.DB import FilteredElementCollector, ViewSheet
        collector = FilteredElementCollector(self.doc).OfClass(ViewSheet).WhereElementIsNotElementType()
        
        self.all_sheets = []
        for sheet in collector:
            if not sheet.IsPlaceholder:
                self.all_sheets.append({
                    'element': sheet,
                    'number': sheet.SheetNumber,
                    'name': sheet.Name,
                    'id': sheet.Id
                })
    
    def refresh_sets(self):
        """Refresh sets list"""
        sets = self.sets_service.get_all_sheet_sets()
        
        self.sets_listbox.Items.Clear()
        for set_info in sets:
            self.sets_listbox.Items.Add(set_info['name'])
    
    def on_set_selected(self, sender, args):
        """Set selected"""
        if self.sets_listbox.SelectedIndex < 0:
            return
        
        set_name = self.sets_listbox.SelectedItem
        
        # Find set
        sets = self.sets_service.get_all_sheet_sets()
        for set_info in sets:
            if set_info['name'] == set_name:
                self.current_set = set_info['element']
                break
        
        # Load sheets in set
        self.load_sheets_in_set()
    
    def load_sheets_in_set(self):
        """Load sheets in current set"""
        if not self.current_set:
            return
        
        sheets_in_set = self.sets_service.get_sheets_in_set(self.current_set)
        
        # Clear and rebuild checkboxes
        self.sheets_panel.Children.Clear()
        
        for sheet_info in self.all_sheets:
            cb = CheckBox()
            cb.Content = "{} - {}".format(sheet_info['number'], sheet_info['name'])
            cb.Tag = sheet_info['id']
            cb.IsChecked = sheet_info['id'] in sheets_in_set
            cb.Margin = Thickness(5, 2, 5, 2)
            self.sheets_panel.Children.Add(cb)
    
    def on_new_set_click(self, sender, args):
        """Create new set"""
        from System.Windows import MessageBox
        
        # Simple auto-generated name (InputBox not available in IronPython)
        set_name = "New Set {}".format(self.sets_listbox.Items.Count + 1)
        
        from Autodesk.Revit.DB import Transaction
        t = Transaction(self.doc, "Create Sheet Set")
        t.Start()
        
        try:
            new_set = self.sets_service.create_sheet_set(set_name)
            t.Commit()
            
            # Refresh and select the new set
            self.refresh_sets()
            
            # Find and select the new set
            for i in range(self.sets_listbox.Items.Count):
                if self.sets_listbox.Items[i] == set_name:
                    self.sets_listbox.SelectedIndex = i
                    break
            
            MessageBox.Show("Sheet set created: {}".format(set_name), "Success",
                          MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as e:
            t.RollBack()
            MessageBox.Show("Error creating sheet set:\n\n{}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
            import traceback
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass
    
    def on_rename_set_click(self, sender, args):
        """Rename selected set"""
        from System.Windows import MessageBox
        
        if not self.current_set:
            MessageBox.Show("Please select a set to rename", "Info",
                          MessageBoxButton.OK, MessageBoxImage.Information)
            return
        
        # Get current name
        current_name = self.current_set.Name
        
        # Create simple input dialog
        input_window = Window()
        input_window.Title = "Rename Sheet Set"
        input_window.Width = 400
        input_window.Height = 150
        input_window.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        
        grid = Grid()
        grid.Margin = Thickness(20)
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(40)))
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(40)))
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(40)))
        
        # Label
        label = Label()
        label.Content = "New name:"
        Grid.SetRow(label, 0)
        grid.Children.Add(label)
        
        # TextBox
        textbox = TextBox()
        textbox.Text = current_name
        textbox.Padding = Thickness(5)
        Grid.SetRow(textbox, 1)
        grid.Children.Add(textbox)
        
        # Buttons
        btn_panel = StackPanel()
        btn_panel.Orientation = System.Windows.Controls.Orientation.Horizontal
        btn_panel.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
        Grid.SetRow(btn_panel, 2)
        
        result_holder = [False]  # Use list to store result
        
        def on_ok(s, e):
            result_holder[0] = True
            input_window.Close()
        
        def on_cancel(s, e):
            result_holder[0] = False
            input_window.Close()
        
        ok_btn = Button()
        ok_btn.Content = "OK"
        ok_btn.Width = 80
        ok_btn.Height = 30
        ok_btn.Margin = Thickness(5, 0, 5, 0)
        ok_btn.Click += on_ok
        btn_panel.Children.Add(ok_btn)
        
        cancel_btn = Button()
        cancel_btn.Content = "Cancel"
        cancel_btn.Width = 80
        cancel_btn.Height = 30
        cancel_btn.Click += on_cancel
        btn_panel.Children.Add(cancel_btn)
        
        grid.Children.Add(btn_panel)
        input_window.Content = grid
        
        # Show dialog
        input_window.ShowDialog()
        
        # Process result
        if not result_holder[0]:
            return
        
        new_name = textbox.Text.strip()
        if not new_name or new_name == current_name:
            return
        
        # Rename in transaction
        from Autodesk.Revit.DB import Transaction
        t = Transaction(self.doc, "Rename Sheet Set")
        t.Start()
        
        try:
            success = self.sets_service.rename_sheet_set(self.current_set, new_name)
            
            if success:
                t.Commit()
                
                # Refresh list and reselect
                self.refresh_sets()
                
                # Find and select renamed set
                for i in range(self.sets_listbox.Items.Count):
                    if self.sets_listbox.Items[i] == new_name:
                        self.sets_listbox.SelectedIndex = i
                        break
                
                MessageBox.Show("Sheet set renamed to: {}".format(new_name), "Success",
                              MessageBoxButton.OK, MessageBoxImage.Information)
            else:
                t.RollBack()
                MessageBox.Show("Failed to rename sheet set", "Error",
                              MessageBoxButton.OK, MessageBoxImage.Error)
        except Exception as e:
            t.RollBack()
            MessageBox.Show("Error renaming sheet set:\n\n{}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
            import traceback
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass
    
    def on_delete_set_click(self, sender, args):
        """Delete selected set"""
        if not self.current_set:
            return
        
        set_name = self.current_set.Name
        
        result = MessageBox.Show("Delete this sheet set?\n\n{}".format(set_name), "Confirm",
                               MessageBoxButton.YesNo, MessageBoxImage.Question)
        
        if result != System.Windows.MessageBoxResult.Yes:
            return
        
        from Autodesk.Revit.DB import Transaction
        t = Transaction(self.doc, "Delete Sheet Set")
        t.Start()
        
        try:
            self.sets_service.delete_sheet_set(set_name)  # Pass name, not ID
            t.Commit()
            
            self.current_set = None
            self.refresh_sets()
            self.sheets_panel.Children.Clear()
            
            MessageBox.Show("Sheet set deleted: {}".format(set_name), "Success",
                          MessageBoxButton.OK, MessageBoxImage.Information)
            
        except Exception as e:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
            import traceback
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass
    
    def on_select_all_click(self, sender, args):
        """Select all sheets"""
        for child in self.sheets_panel.Children:
            if isinstance(child, CheckBox):
                child.IsChecked = True
    
    def on_select_none_click(self, sender, args):
        """Deselect all sheets"""
        for child in self.sheets_panel.Children:
            if isinstance(child, CheckBox):
                child.IsChecked = False
    
    def on_apply_sheets_click(self, sender, args):
        """Apply sheet selection to set"""
        if not self.current_set:
            return
        
        # Get selected sheets
        selected_ids = []
        for child in self.sheets_panel.Children:
            if isinstance(child, CheckBox) and child.IsChecked:
                selected_ids.append(child.Tag)
        
        from Autodesk.Revit.DB import Transaction
        t = Transaction(self.doc, "Update Sheet Set")
        t.Start()
        
        try:
            # Clear current sheets
            current_sheets = self.sets_service.get_sheets_in_set(self.current_set)
            if current_sheets:
                self.sets_service.remove_sheets_from_set(self.current_set, current_sheets)
            
            # Add selected sheets
            if selected_ids:
                self.sets_service.add_sheets_to_set(self.current_set, selected_ids)
            
            t.Commit()
            
            # REFRESH the sheets list to show updated state
            self.load_sheets_in_set()
            
            MessageBox.Show("Sheet set updated: {} sheets".format(len(selected_ids)),
                          "Success", MessageBoxButton.OK, MessageBoxImage.Information)
            
        except Exception as e:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
            import traceback
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass