# -*- coding: utf-8 -*-
"""
Sheet Manager - Custom Parameters Dialog

Copyright (c) T3Lab
"""

from System.Windows import Window, MessageBox, MessageBoxButton, MessageBoxImage, Thickness, GridLength
from System.Windows.Controls import (Grid, Label, Button, TextBox, ListBox, ComboBox,
                                      StackPanel, RowDefinition, ColumnDefinition, GroupBox, ScrollViewer)
import System


class CustomParametersDialog(Window):
    """Dialog for managing custom sheet parameters"""
    
    def __init__(self, params_service, doc, selected_sheets):
        self.params_service = params_service
        self.doc = doc
        self.selected_sheets = selected_sheets
        self.all_parameters = []
        self.templates = {}  # Stored templates
        
        self._build_ui()
        self._load_parameters()
    
    def _build_ui(self):
        """Build dialog UI"""
        self.Title = "Custom Parameters Management"
        self.Width = 800
        self.Height = 600
        self.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        
        # Main grid
        main_grid = Grid()
        main_grid.Margin = Thickness(20)
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(40)))  # Info
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, System.Windows.GridUnitType.Star)))  # Content
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(50)))  # Buttons
        
        # Info
        info_label = Label()
        info_label.Content = "Edit parameters for {} selected sheet(s)".format(len(self.selected_sheets))
        info_label.FontSize = 14
        info_label.FontWeight = System.Windows.FontWeights.Bold
        Grid.SetRow(info_label, 0)
        main_grid.Children.Add(info_label)
        
        # Content - Two panels
        content_grid = Grid()
        content_grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(350)))
        content_grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(20)))
        content_grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(1, System.Windows.GridUnitType.Star)))
        Grid.SetRow(content_grid, 1)
        
        # Left - Parameters list
        left_panel = Grid()
        left_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(40)))
        left_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(1, System.Windows.GridUnitType.Star)))
        Grid.SetColumn(left_panel, 0)
        
        params_label = Label()
        params_label.Content = "Available Parameters:"
        params_label.FontWeight = System.Windows.FontWeights.Bold
        Grid.SetRow(params_label, 0)
        left_panel.Children.Add(params_label)
        
        self.params_listbox = ListBox()
        self.params_listbox.SelectionChanged += self.on_parameter_selected
        Grid.SetRow(self.params_listbox, 1)
        left_panel.Children.Add(self.params_listbox)
        
        content_grid.Children.Add(left_panel)
        
        # Right - Edit & Templates
        right_panel = Grid()
        right_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(1, System.Windows.GridUnitType.Star)))
        right_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(20)))
        right_panel.RowDefinitions.Add(RowDefinition(Height=GridLength(1, System.Windows.GridUnitType.Star)))
        Grid.SetColumn(right_panel, 2)
        
        # Edit group
        edit_group = GroupBox()
        edit_group.Header = "Bulk Edit Parameter"
        Grid.SetRow(edit_group, 0)
        
        edit_panel = StackPanel()
        edit_panel.Margin = Thickness(10)
        
        self.selected_param_label = Label()
        self.selected_param_label.Content = "Select a parameter from the list"
        self.selected_param_label.Foreground = System.Windows.Media.Brushes.Gray
        edit_panel.Children.Add(self.selected_param_label)
        
        value_label = Label()
        value_label.Content = "New Value:"
        value_label.Margin = Thickness(0, 10, 0, 5)
        edit_panel.Children.Add(value_label)
        
        self.value_textbox = TextBox()
        self.value_textbox.Height = 30
        self.value_textbox.IsEnabled = False
        edit_panel.Children.Add(self.value_textbox)
        
        apply_param_btn = Button()
        apply_param_btn.Content = "Apply to Selected Sheets"
        apply_param_btn.Height = 35
        apply_param_btn.Margin = Thickness(0, 15, 0, 0)
        apply_param_btn.IsEnabled = False
        apply_param_btn.Click += self.on_apply_parameter_click
        edit_panel.Children.Add(apply_param_btn)
        
        self.apply_param_btn = apply_param_btn
        
        edit_group.Content = edit_panel
        right_panel.Children.Add(edit_group)
        
        # Templates group
        templates_group = GroupBox()
        templates_group.Header = "Parameter Templates"
        Grid.SetRow(templates_group, 2)
        
        templates_panel = StackPanel()
        templates_panel.Margin = Thickness(10)
        
        templates_label = Label()
        templates_label.Content = "Quick apply common parameter sets"
        templates_label.Foreground = System.Windows.Media.Brushes.Gray
        templates_panel.Children.Add(templates_label)
        
        # Predefined templates
        template_btns = StackPanel()
        template_btns.Margin = Thickness(0, 10, 0, 0)
        
        standard_btn = Button()
        standard_btn.Content = "Standard Template"
        standard_btn.Height = 35
        standard_btn.Margin = Thickness(0, 5, 0, 0)
        standard_btn.Click += lambda s, e: self.apply_template('standard')
        template_btns.Children.Add(standard_btn)
        
        standard_desc = Label()
        standard_desc.Content = "  Designer, Checker, Approver fields"
        standard_desc.Foreground = System.Windows.Media.Brushes.Gray
        standard_desc.FontSize = 10
        template_btns.Children.Add(standard_desc)
        
        clear_btn = Button()
        clear_btn.Content = "Clear All Parameters"
        clear_btn.Height = 35
        clear_btn.Margin = Thickness(0, 10, 0, 0)
        clear_btn.Click += lambda s, e: self.apply_template('clear')
        template_btns.Children.Add(clear_btn)
        
        clear_desc = Label()
        clear_desc.Content = "  Set all editable parameters to '-'"
        clear_desc.Foreground = System.Windows.Media.Brushes.Gray
        clear_desc.FontSize = 10
        template_btns.Children.Add(clear_desc)
        
        templates_panel.Children.Add(template_btns)
        templates_group.Content = templates_panel
        right_panel.Children.Add(templates_group)
        
        content_grid.Children.Add(right_panel)
        main_grid.Children.Add(content_grid)
        
        # Buttons
        btn_panel = StackPanel()
        btn_panel.Orientation = System.Windows.Controls.Orientation.Horizontal
        btn_panel.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
        Grid.SetRow(btn_panel, 2)
        
        close_btn = Button()
        close_btn.Content = "Close"
        close_btn.Width = 100
        close_btn.Height = 35
        close_btn.Click += lambda s, e: self.Close()
        btn_panel.Children.Add(close_btn)
        
        main_grid.Children.Add(btn_panel)
        self.Content = main_grid
    
    def _load_parameters(self):
        """Load available parameters"""
        self.all_parameters = self.params_service.get_all_sheet_parameters()
        
        self.params_listbox.Items.Clear()
        for param in self.all_parameters:
            display = "{} [{}]{}".format(
                param['name'],
                param['type'],
                " (Read-only)" if param['is_read_only'] else ""
            )
            self.params_listbox.Items.Add(display)
    
    def on_parameter_selected(self, sender, args):
        """Parameter selected"""
        if self.params_listbox.SelectedIndex < 0:
            return
        
        param = self.all_parameters[self.params_listbox.SelectedIndex]
        
        self.selected_param_label.Content = "Parameter: {}".format(param['name'])
        self.selected_param_label.Foreground = System.Windows.Media.Brushes.Black
        
        if param['is_read_only']:
            self.value_textbox.IsEnabled = False
            self.apply_param_btn.IsEnabled = False
            self.selected_param_label.Content += " (Read-only - cannot edit)"
        else:
            self.value_textbox.IsEnabled = True
            self.apply_param_btn.IsEnabled = True
            self.value_textbox.Text = ""
            self.value_textbox.Focus()
    
    def on_apply_parameter_click(self, sender, args):
        """Apply parameter value to selected sheets"""
        if self.params_listbox.SelectedIndex < 0:
            MessageBox.Show("Please select a parameter first", "Info",
                          MessageBoxButton.OK, MessageBoxImage.Information)
            return
        
        param = self.all_parameters[self.params_listbox.SelectedIndex]
        value = self.value_textbox.Text
        
        if not value:
            MessageBox.Show("Please enter a value", "Info",
                          MessageBoxButton.OK, MessageBoxImage.Information)
            return
        
        # Check if parameter is read-only
        if param['is_read_only']:
            MessageBox.Show(
                "Parameter '{}' is read-only and cannot be modified.".format(param['name']),
                "Cannot Modify",
                MessageBoxButton.OK,
                MessageBoxImage.Warning)
            return
        
        result = MessageBox.Show(
            "Set '{}' = '{}' for {} sheet(s)?".format(
                param['name'], value, len(self.selected_sheets)),
            "Confirm",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question)
        
        if result != System.Windows.MessageBoxResult.Yes:
            return
        
        # Apply parameter
        from Autodesk.Revit.DB import Transaction
        
        t = Transaction(self.doc, "Update Parameters")
        t.Start()
        
        try:
            print("DEBUG: Starting bulk update of '{}' to '{}'".format(param['name'], value))
            success_count = self.params_service.bulk_update_parameter(
                self.selected_sheets, param['name'], value)
            
            t.Commit()
            
            if success_count == len(self.selected_sheets):
                MessageBox.Show(
                    "Updated {} sheet(s) successfully!".format(success_count),
                    "Success",
                    MessageBoxButton.OK,
                    MessageBoxImage.Information)
            elif success_count > 0:
                MessageBox.Show(
                    "Updated {} of {} sheet(s).\n\nSome sheets could not be updated. Check console for details.".format(
                        success_count, len(self.selected_sheets)),
                    "Partial Success",
                    MessageBoxButton.OK,
                    MessageBoxImage.Warning)
            else:
                MessageBox.Show(
                    "Failed to update any sheets.\n\nCheck console for error details.",
                    "Failed",
                    MessageBoxButton.OK,
                    MessageBoxImage.Error)
            
        except Exception as e:
            t.RollBack()
            MessageBox.Show("Error: {}\n\nCheck console for details.".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)
            import traceback
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass
    
    def apply_template(self, template_name):
        """Apply parameter template"""
        if template_name == 'standard':
            # Standard template
            template = {
                'name': 'Standard',
                'parameters': {
                    'Designed By': 'Designer',
                    'Checked By': 'Checker',
                    'Drawn By': 'Drafter',
                    'Approved By': 'Approver'
                }
            }
        elif template_name == 'clear':
            # Clear template - set common params to "-"
            template = {
                'name': 'Clear',
                'parameters': {
                    'Designed By': '-',
                    'Checked By': '-',
                    'Drawn By': '-',
                    'Approved By': '-'
                }
            }
        else:
            return
        
        result = MessageBox.Show(
            "Apply '{}' template to {} sheet(s)?".format(
                template['name'], len(self.selected_sheets)),
            "Confirm",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question)
        
        if result != System.Windows.MessageBoxResult.Yes:
            return
        
        # Apply template
        from Autodesk.Revit.DB import Transaction
        
        t = Transaction(self.doc, "Apply Template")
        t.Start()
        
        try:
            results = self.params_service.apply_parameter_template(
                self.selected_sheets, template)
            
            t.Commit()
            
            total = sum(results.values())
            MessageBox.Show(
                "Applied template successfully!\n\nTotal updates: {}".format(total),
                "Success",
                MessageBoxButton.OK,
                MessageBoxImage.Information)
            
        except Exception as e:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(e)), "Error",
                          MessageBoxButton.OK, MessageBoxImage.Error)