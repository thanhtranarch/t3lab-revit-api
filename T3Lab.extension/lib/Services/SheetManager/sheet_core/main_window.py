# -*- coding: utf-8 -*-
"""
Sheet Manager - Main Window
CLEANED - Sheet List Only

Copyright © Dang Quoc Truong (DQT)
"""

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Windows import Window, Thickness, GridLength, GridUnitType
from System.Windows.Controls import Grid, StackPanel, Button, Orientation, RowDefinition, ColumnDefinition
from System.Windows.Media import SolidColorBrush, Color, Brushes
import System

try:
    from Services.SheetManager.sheet_core.config import Config
except Exception:
    from .config import Config


class MainWindow(Window):
    """Main application window - Sheet List Only"""
    
    def __init__(self, doc, revit_service, change_tracker):
        self.doc = doc
        self.revit_service = revit_service
        self.change_tracker = change_tracker
        
        # Initialize new services
        try:
            import sys
            import os

            # Excel service
            from Services.SheetManager.excel_service import ExcelService
            self.excel_service = ExcelService()
            print("DEBUG: Excel service initialized")
        except Exception as e:
            print("WARNING: Could not initialize Excel service: {}".format(str(e)))
            self.excel_service = None

        try:
            # ViewSheet Sets service
            from Services.SheetManager.viewsheet_sets_service import ViewSheetSetsService
            self.sheet_sets_service = ViewSheetSetsService(doc)
            print("DEBUG: ViewSheet Sets service initialized")
        except Exception as e:
            print("WARNING: Could not initialize ViewSheet Sets service: {}".format(str(e)))
            self.sheet_sets_service = None

        try:
            # Place Views service
            from Services.SheetManager.place_views_service import PlaceViewsService
            self.place_views_service = PlaceViewsService(doc)
            print("DEBUG: Place Views service initialized")
        except Exception as e:
            print("WARNING: Could not initialize Place Views service: {}".format(str(e)))
            self.place_views_service = None

        try:
            # Custom Parameters service
            from Services.SheetManager.custom_parameters_service import CustomParametersService
            self.params_service = CustomParametersService(doc, __revit__.Application)
            print("DEBUG: Custom Parameters service initialized")
        except Exception as e:
            print("WARNING: Could not initialize Parameters service: {}".format(str(e)))
            self.params_service = None
        
        # Reference to sheet list
        self.sheet_list_tab = None
        
        # Build UI
        self._build_ui()
    
    def _build_ui(self):
        """Build the main window UI"""
        self.Title = "Sheet Manager - Copyright © Dang Quoc Truong (DQT)"
        self.Width = Config.MAIN_WINDOW_WIDTH
        self.Height = Config.MAIN_WINDOW_HEIGHT
        self.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        
        # Background color
        argb = Config.get_color_argb(Config.BACKGROUND_COLOR)
        self.Background = SolidColorBrush(Color.FromArgb(*argb))
        
        # Main grid
        main_grid = Grid()
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(60)))  # Header
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Star)))  # Content
        main_grid.RowDefinitions.Add(RowDefinition(Height=GridLength(80)))  # Footer
        
        # Header
        header_panel = self._create_header()
        Grid.SetRow(header_panel, 0)
        main_grid.Children.Add(header_panel)
        
        # Content - Sheet List directly (no tabs)
        from ui.sheet_list_tab import SheetListTab
        self.sheet_list_tab = SheetListTab(self, self.doc)
        Grid.SetRow(self.sheet_list_tab.content, 1)
        main_grid.Children.Add(self.sheet_list_tab.content)
        
        # Footer
        footer = self._create_footer()
        Grid.SetRow(footer, 2)
        main_grid.Children.Add(footer)
        
        self.Content = main_grid
        
        # Initialize
        self.update_status("Ready")
    
    def _create_header(self):
        """Create header panel"""
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        panel.Margin = Thickness(20, 10, 20, 10)
        
        argb = Config.get_color_argb(Config.BACKGROUND_COLOR)
        panel.Background = SolidColorBrush(Color.FromArgb(*argb))
        
        # Title
        from System.Windows.Controls import TextBlock
        title = TextBlock()
        title.Text = "Sheet Manager"
        title.FontSize = 28
        title.FontWeight = System.Windows.FontWeights.Bold
        argb_primary = Config.get_color_argb(Config.PRIMARY_COLOR)
        title.Foreground = SolidColorBrush(Color.FromArgb(*argb_primary))
        title.VerticalAlignment = System.Windows.VerticalAlignment.Center
        panel.Children.Add(title)
        
        # Version
        version = TextBlock()
        version.Text = "  v1.0.0"
        version.FontSize = 14
        version.Foreground = Brushes.Gray
        version.VerticalAlignment = System.Windows.VerticalAlignment.Bottom
        version.Margin = Thickness(0, 0, 0, 5)
        panel.Children.Add(version)
        
        return panel
    
    def _create_footer(self):
        """Create footer with status and buttons"""
        footer = Grid()
        footer.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(1, GridUnitType.Star)))
        footer.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(200)))
        footer.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(200)))
        footer.Margin = Thickness(20, 10, 20, 20)
        
        # Status text
        from System.Windows.Controls import TextBlock
        self.status_text = TextBlock()
        self.status_text.Text = "Ready"
        self.status_text.FontSize = 14
        self.status_text.VerticalAlignment = System.Windows.VerticalAlignment.Center
        Grid.SetColumn(self.status_text, 0)
        footer.Children.Add(self.status_text)
        
        # Apply button
        apply_btn = Button()
        apply_btn.Content = "Apply"
        apply_btn.Height = 40
        apply_btn.Margin = Thickness(0, 0, 10, 0)
        argb = Config.get_color_argb(Config.PRIMARY_COLOR)
        apply_btn.Background = SolidColorBrush(Color.FromArgb(*argb))
        apply_btn.Foreground = Brushes.White
        apply_btn.FontSize = 14
        apply_btn.FontWeight = System.Windows.FontWeights.Bold
        apply_btn.Click += self.on_apply_click
        Grid.SetColumn(apply_btn, 1)
        footer.Children.Add(apply_btn)
        
        # Close button
        close_btn = Button()
        close_btn.Content = "Close"
        close_btn.Height = 40
        close_btn.Background = Brushes.LightGray
        close_btn.FontSize = 14
        close_btn.Click += self.on_close_click
        Grid.SetColumn(close_btn, 2)
        footer.Children.Add(close_btn)
        
        return footer
    
    def update_status(self, message):
        """Update status bar message"""
        if hasattr(self, 'status_text'):
            self.status_text.Text = message
    
    
    def on_apply_click(self, sender, args):
        """Apply all changes"""
        if not self.change_tracker.has_changes():
            from System.Windows import MessageBox, MessageBoxButton, MessageBoxImage
            MessageBox.Show("No changes to apply", "Info",
                          MessageBoxButton.OK, MessageBoxImage.Information)
            return
        
        # Confirm
        from System.Windows import MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult
        modified = len(self.change_tracker.modified_items)
        created = len(self.change_tracker.created_items)
        deleted = len(self.change_tracker.deleted_items)
        
        msg = "Apply changes?\n\n"
        msg += "Modified: {}\n".format(modified)
        msg += "Created: {}\n".format(created)
        msg += "Deleted: {}".format(deleted)
        
        result = MessageBox.Show(msg, "Confirm", MessageBoxButton.YesNo,
                                MessageBoxImage.Question)
        
        if result == MessageBoxResult.Yes:
            self._apply_all_changes()
    
    def _apply_all_changes(self):
        """Apply all tracked changes"""
        from Autodesk.Revit.DB import Transaction
        from System.Windows import MessageBox, MessageBoxButton, MessageBoxImage
        
        t = Transaction(self.doc, "Apply Sheet Manager Changes")
        t.Start()
        
        try:
            success_count = 0
            error_count = 0
            
            # Update modified sheets
            for item in self.change_tracker.modified_items:
                try:
                    if self.revit_service.update_sheet(item):
                        item.commit_changes()
                        success_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    print("Error updating sheet {}: {}".format(item.sheet_number, str(e)))
                    error_count += 1
            
            t.Commit()
            
            # Clear change tracker
            self.change_tracker.clear_all()
            
            # Refresh sheet list
            if self.sheet_list_tab:
                self.sheet_list_tab.refresh_data()
            
            # Show result
            if error_count > 0:
                MessageBox.Show(
                    "Updates completed with errors:\n\nSuccess: {}\nFailed: {}".format(success_count, error_count),
                    "Partial Success",
                    MessageBoxButton.OK,
                    MessageBoxImage.Warning
                )
            else:
                MessageBox.Show(
                    "{} sheet(s) updated successfully".format(success_count),
                    "Success",
                    MessageBoxButton.OK,
                    MessageBoxImage.Information
                )
            
            # Update status
            self.update_status("Changes applied")
            
        except Exception as e:
            t.RollBack()
            MessageBox.Show(
                "Error applying changes:\n\n{}".format(str(e)),
                "Error",
                MessageBoxButton.OK,
                MessageBoxImage.Error
            )
            self.update_status("Error: {}".format(str(e)))
    
    def on_close_click(self, sender, args):
        """Close window"""
        # Check for unsaved changes
        if self.change_tracker.has_changes():
            from System.Windows import MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult
            result = MessageBox.Show(
                "You have unsaved changes. Close anyway?",
                "Unsaved Changes",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning
            )
            if result != MessageBoxResult.Yes:
                return
        
        self.Close()