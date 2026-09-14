# -*- coding: utf-8 -*-
"""
Parameter Selector Dialog

GUI dialog for selecting Revit parameters.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Parameter Selector Dialog"

import os
import sys
import clr
import json
import codecs
from collections import OrderedDict

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System')

import System
from System.Windows import Window, WindowStartupLocation, WindowStyle, Visibility
from System.Windows.Markup import XamlReader
from System.Windows.Controls import ListBox
from System.Collections.ObjectModel import ObservableCollection
from System import Object
from System import Uri, UriKind

from pyrevit import revit, DB, forms
from GUI.WPF_Base import T3WPFWindow
from Autodesk.Revit.DB import (
    BuiltInParameter, ViewSheet, View,
    StorageType
)


# Cache file path for storing filename pattern settings
CACHE_FILE_PATH = os.path.join(os.path.expanduser('~'), '.batchout_filename_cache.json')


class ParameterItem:
    """Represents a parameter item that can be selected."""

    def __init__(self, name, display_name, is_builtin=True, builtin_param=None):
        self.Name = name
        self.DisplayName = display_name
        self.IsBuiltIn = is_builtin
        self.BuiltInParameter = builtin_param

    def __str__(self):
        return self.DisplayName

    def __repr__(self):
        return "ParameterItem({})".format(self.DisplayName)

    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            'Name': self.Name,
            'DisplayName': self.DisplayName,
            'IsBuiltIn': self.IsBuiltIn
        }

    @staticmethod
    def from_dict(data):
        """Create ParameterItem from dictionary."""
        return ParameterItem(
            data.get('Name', ''),
            data.get('DisplayName', ''),
            data.get('IsBuiltIn', False),
            None  # BuiltInParameter cannot be serialized
        )


class ParameterSelectorDialog(T3WPFWindow):
    """Dialog for selecting parameters to build custom filenames."""

    def __init__(self, doc, element_type='sheet'):
        """Initialize the parameter selector dialog.

        Args:
            doc: Revit document
            element_type: 'sheet' or 'view' - determines which parameters to load
        """
        xaml_file = os.path.join(os.path.dirname(__file__), 'Tools', 'ParameterSelector.xaml')
        T3WPFWindow.__init__(self, xaml_file)

        self.doc = doc
        self.element_type = element_type
        self.selected_result = None
        self.field_separator = '-'

        # Set up event handlers
        if hasattr(self, 'button_close') and self.button_close:
            self.button_close.Click += self._on_close
        if hasattr(self, 'button_cancel') and self.button_cancel:
            self.button_cancel.Click += self._on_close
        if hasattr(self, 'button_add_parameter') and self.button_add_parameter:
            self.button_add_parameter.Click += self._on_add_parameter
        if hasattr(self, 'button_remove_parameter') and self.button_remove_parameter:
            self.button_remove_parameter.Click += self._on_remove_parameter
        if hasattr(self, 'button_move_up') and self.button_move_up:
            self.button_move_up.Click += self._on_move_up
        if hasattr(self, 'button_move_down') and self.button_move_down:
            self.button_move_down.Click += self._on_move_down
        if hasattr(self, 'button_move_top') and self.button_move_top:
            self.button_move_top.Click += self._on_move_top
        if hasattr(self, 'button_move_bottom') and self.button_move_bottom:
            self.button_move_bottom.Click += self._on_move_bottom
        if hasattr(self, 'button_refresh') and self.button_refresh:
            self.button_refresh.Click += self._on_refresh
        if hasattr(self, 'button_add_custom_field') and self.button_add_custom_field:
            self.button_add_custom_field.Click += self._on_add_custom_field
        if hasattr(self, 'button_add_custom_separator') and self.button_add_custom_separator:
            self.button_add_custom_separator.Click += self._on_add_custom_separator
        if hasattr(self, 'button_ok') and self.button_ok:
            self.button_ok.Click += self._on_ok

        # Set up checkbox event handlers
        self.chk_include_project_params.Checked += self.toggle_project_params
        self.chk_include_project_params.Unchecked += self.toggle_project_params
        self.chk_field_separator.Checked += self.update_preview
        self.chk_field_separator.Unchecked += self.update_preview
        self.txt_field_separator.TextChanged += self.update_preview

        # Set up drag handler if window chrome allows or mouse drag
        self.MouseDown += self.header_drag

        # Initialize parameter collections
        # Use ObservableCollection[Object] instead of ObservableCollection[ParameterItem]
        # because ParameterItem is a Python class, not a .NET type
        self.available_params = ObservableCollection[Object]()
        self.selected_params = ObservableCollection[Object]()

        self.list_available.ItemsSource = self.available_params
        self.list_selected.ItemsSource = self.selected_params
        self.available_params.CollectionChanged += self._update_parameter_empty_states
        self.selected_params.CollectionChanged += self._update_parameter_empty_states
        self._update_parameter_empty_states()

        # Load parameters
        self.load_parameters()

        # Load cached configuration (restore previous user selections)
        self.load_cached_config()

        # Update preview
        self.update_preview(None, None)

    def load_parameters(self):
        """Load all available parameters from sheets or views."""
        self.available_params.Clear()

        # Get a sample element to extract parameters from
        sample_element = self._get_sample_element()
        if not sample_element:
            forms.alert("No {} found in the project.".format(self.element_type))
            return

        # Collect all parameters
        all_params = OrderedDict()

        # Add built-in parameters specific to sheets/views
        if self.element_type == 'sheet':
            self._add_sheet_builtin_params(all_params)
        else:
            self._add_view_builtin_params(all_params)

        # Add project information parameters if checkbox is checked
        if self.chk_include_project_params.IsChecked:
            self._add_project_info_params(all_params)

        # Extract all parameters from the sample element
        for param in sample_element.Parameters:
            try:
                param_name = param.Definition.Name
                if param_name not in all_params:
                    # Check if it's a built-in parameter
                    is_builtin = False
                    builtin_param = None
                    try:
                        builtin_param = param.Definition.BuiltInParameter
                        if builtin_param != BuiltInParameter.INVALID:
                            is_builtin = True
                    except:
                        pass

                    all_params[param_name] = ParameterItem(
                        param_name,
                        param_name,
                        is_builtin,
                        builtin_param
                    )
            except:
                continue

        # Add standard date/time placeholders
        all_params['Date'] = ParameterItem('Date', 'Date', False)
        all_params['Time'] = ParameterItem('Time', 'Time', False)

        # Add to available list
        for param in all_params.values():
            self.available_params.Add(param)

    def _get_sample_element(self):
        """Get a sample sheet or view to extract parameters from."""
        if self.element_type == 'sheet':
            sheets = DB.FilteredElementCollector(self.doc)\
                      .OfClass(DB.ViewSheet)\
                      .WhereElementIsNotElementType()\
                      .ToElements()
            return sheets[0] if sheets else None
        else:
            views = DB.FilteredElementCollector(self.doc)\
                     .OfClass(DB.View)\
                     .WhereElementIsNotElementType()\
                     .ToElements()
            for view in views:
                if not view.IsTemplate:
                    return view
            return None

    def _add_sheet_builtin_params(self, all_params):
        """Add sheet-specific built-in parameters."""
        sheet_params = [
            (BuiltInParameter.SHEET_NUMBER, 'SheetNumber', 'Sheet Number'),
            (BuiltInParameter.SHEET_NAME, 'SheetName', 'Sheet Name'),
            (BuiltInParameter.SHEET_CURRENT_REVISION, 'Revision', 'Revision'),
            (BuiltInParameter.SHEET_CURRENT_REVISION_DATE, 'RevisionDate', 'Revision Date'),
            (BuiltInParameter.SHEET_CURRENT_REVISION_DESCRIPTION, 'RevisionDescription', 'Revision Description'),
            (BuiltInParameter.SHEET_DRAWN_BY, 'DrawnBy', 'Drawn By'),
            (BuiltInParameter.SHEET_CHECKED_BY, 'CheckedBy', 'Checked By'),
            (BuiltInParameter.SHEET_ISSUE_DATE, 'IssueDate', 'Issue Date'),
            (BuiltInParameter.SHEET_APPROVED_BY, 'ApprovedBy', 'Approved By'),
        ]

        for builtin_param, name, display_name in sheet_params:
            all_params[name] = ParameterItem(name, display_name, True, builtin_param)

    def _add_view_builtin_params(self, all_params):
        """Add view-specific built-in parameters."""
        view_params = [
            (BuiltInParameter.VIEW_NAME, 'ViewName', 'View Name'),
            (BuiltInParameter.VIEW_TYPE, 'ViewType', 'View Type'),
            (BuiltInParameter.VIEW_SCALE, 'ViewScale', 'View Scale'),
            (BuiltInParameter.VIEW_PHASE, 'Phase', 'Phase'),
            (BuiltInParameter.VIEW_LEVEL, 'Level', 'Associated Level'),
            (BuiltInParameter.VIEW_DISCIPLINE, 'Discipline', 'Discipline'),
        ]

        for builtin_param, name, display_name in view_params:
            all_params[name] = ParameterItem(name, display_name, True, builtin_param)

    def _add_project_info_params(self, all_params):
        """Add project information parameters."""
        project_params = [
            ('ProjectNumber', 'Project Number', False),
            ('ProjectName', 'Project Name', False),
            ('ProjectAddress', 'Project Address', False),
            ('ClientName', 'Client Name', False),
            ('ProjectStatus', 'Project Status', False),
        ]

        for name, display_name, _ in project_params:
            all_params[name] = ParameterItem(name, display_name, False)

    def toggle_project_params(self, sender, e):
        """Toggle inclusion of project information parameters."""
        self.load_parameters()

    def _on_add_parameter(self, sender, e):
        """Add selected parameters from available to selected list."""
        selected_items = list(self.list_available.SelectedItems)
        for item in selected_items:
            self.selected_params.Add(item)
            self.available_params.Remove(item)
        self.update_preview(sender, e)

    def _on_remove_parameter(self, sender, e):
        """Remove selected parameters from selected list back to available."""
        selected_items = list(self.list_selected.SelectedItems)
        for item in selected_items:
            self.available_params.Add(item)
            self.selected_params.Remove(item)
        self.update_preview(sender, e)

    def _on_move_up(self, sender, e):
        """Move selected parameter up in the list."""
        if self.list_selected.SelectedIndex > 0:
            index = self.list_selected.SelectedIndex
            item = self.selected_params[index]
            self.selected_params.RemoveAt(index)
            self.selected_params.Insert(index - 1, item)
            self.list_selected.SelectedIndex = index - 1
        self.update_preview(sender, e)

    def _on_move_down(self, sender, e):
        """Move selected parameter down in the list."""
        if 0 <= self.list_selected.SelectedIndex < len(self.selected_params) - 1:
            index = self.list_selected.SelectedIndex
            item = self.selected_params[index]
            self.selected_params.RemoveAt(index)
            self.selected_params.Insert(index + 1, item)
            self.list_selected.SelectedIndex = index + 1
        self.update_preview(sender, e)

    def _on_move_top(self, sender, e):
        """Move selected parameter to the top of the list."""
        if self.list_selected.SelectedIndex > 0:
            index = self.list_selected.SelectedIndex
            item = self.selected_params[index]
            self.selected_params.RemoveAt(index)
            self.selected_params.Insert(0, item)
            self.list_selected.SelectedIndex = 0
        self.update_preview(sender, e)

    def _on_move_bottom(self, sender, e):
        """Move selected parameter to the bottom of the list."""
        if 0 <= self.list_selected.SelectedIndex < len(self.selected_params) - 1:
            index = self.list_selected.SelectedIndex
            item = self.selected_params[index]
            self.selected_params.RemoveAt(index)
            self.selected_params.Add(item)
            self.list_selected.SelectedIndex = len(self.selected_params) - 1
        self.update_preview(sender, e)

    def _default_param_names(self):
        """Parameter names making up the default filename pattern."""
        if self.element_type == 'sheet':
            return ['SheetNumber', 'SheetName']
        return ['ViewName']

    def _select_default_params(self):
        """Move the default pattern's parameters from available to selected, in order."""
        for name in self._default_param_names():
            for available_param in list(self.available_params):
                if available_param.Name == name:
                    self.available_params.Remove(available_param)
                    self.selected_params.Add(available_param)
                    break

    def _on_refresh(self, sender, e):
        """Refresh: reload parameters from the model and reset the selection
        back to the default filename pattern."""
        self.selected_params.Clear()
        self.load_parameters()
        self._select_default_params()
        self.update_preview(sender, e)

    def _on_add_custom_field(self, sender, e):
        """Add a custom field to the selected parameters."""
        custom_field = self.txt_custom_field.Text.strip()
        if custom_field:
            custom_param = ParameterItem(
                'Custom_{}'.format(custom_field),
                custom_field,
                False
            )
            self.selected_params.Add(custom_param)
            self.txt_custom_field.Text = ''
        self.update_preview(sender, e)

    def _on_add_custom_separator(self, sender, e):
        """Add a custom separator to the selected parameters."""
        custom_sep = self.txt_custom_separator.Text.strip()
        if custom_sep:
            sep_param = ParameterItem(
                'Separator_{}'.format(custom_sep),
                custom_sep,
                False
            )
            self.selected_params.Add(sep_param)
            self.txt_custom_separator.Text = ''
        self.update_preview(sender, e)

    def _update_parameter_empty_states(self, sender=None, e=None):
        """Keep both overlays current through load, cache restore, and transfers."""
        self.empty_available.Visibility = (
            Visibility.Collapsed if len(self.available_params) else Visibility.Visible
        )
        self.empty_available.Text = (
            "No parameters are available.\n"
            "Remove a selected parameter or click Reset to reload."
        )
        self.empty_selected.Visibility = (
            Visibility.Collapsed if len(self.selected_params) else Visibility.Visible
        )
        self.empty_selected.Text = (
            "No parameters are selected.\n"
            "Select parameters on the left, then use the right arrow to add them."
        )

    def update_preview(self, sender, e):
        """Update the preview text based on current selection."""
        pattern = self.build_pattern()
        if pattern:
            self.txt_preview.Text = "Preview: " + pattern
        else:
            self.txt_preview.Text = "Preview: (select parameters)"

    def _on_ok(self, sender, e):
        """OK button - build pattern and close dialog."""
        self.field_separator = self.txt_field_separator.Text if self.chk_field_separator.IsChecked else ''
        self.selected_result = self.build_pattern()
        # Save configuration to cache for next time
        self.save_cached_config()
        self.DialogResult = True
        self.Close()

    def _on_close(self, sender, e):
        """Close button - same as cancel."""
        self.selected_result = None
        self.DialogResult = False
        self.Close()

    def header_drag(self, sender, e):
        """Allow dragging the window by its header."""
        try:
            if e.ChangedButton == System.Windows.Input.MouseButton.Left:
                self.DragMove()
        except:
            pass

    def build_pattern(self):
        """Build filename pattern from selected parameters.

        Returns:
            String pattern with placeholders like {SheetNumber}-{SheetName}
        """
        if len(self.selected_params) == 0:
            return ''

        pattern_parts = []
        separator = self.txt_field_separator.Text if self.chk_field_separator.IsChecked else ''

        for param in self.selected_params:
            # If it's a separator item, add it directly
            if param.Name.startswith('Separator_'):
                pattern_parts.append(param.DisplayName)
            # If it's a custom field, add it as-is
            elif param.Name.startswith('Custom_'):
                pattern_parts.append(param.DisplayName)
            # For standard parameters, create placeholder
            else:
                pattern_parts.append('{{{}}}'.format(param.Name))

        # Join with separator
        if separator and self.chk_field_separator.IsChecked:
            return separator.join(pattern_parts)
        else:
            return ''.join(pattern_parts)

    def save_cached_config(self):
        """Save current configuration to cache file."""
        try:
            # Build list of selected parameter data
            selected_data = []
            for param in self.selected_params:
                selected_data.append(param.to_dict())

            config = {
                'element_type': self.element_type,
                'selected_params': selected_data,
                'field_separator': self.txt_field_separator.Text,
                'use_separator': self.chk_field_separator.IsChecked,
                'include_project_params': self.chk_include_project_params.IsChecked
            }

            with open(CACHE_FILE_PATH, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            # Silently ignore cache save errors
            pass

    def load_cached_config(self):
        """Load cached configuration and restore selected parameters."""
        try:
            if not os.path.exists(CACHE_FILE_PATH):
                return

            with open(CACHE_FILE_PATH, 'r') as f:
                config = json.load(f)

            # Only restore if element type matches
            if config.get('element_type') != self.element_type:
                return

            # Restore separator settings
            if 'field_separator' in config:
                self.txt_field_separator.Text = config['field_separator']
            if 'use_separator' in config:
                self.chk_field_separator.IsChecked = config['use_separator']
            if 'include_project_params' in config:
                self.chk_include_project_params.IsChecked = config['include_project_params']
                # Reload parameters if project params setting changed
                self.load_parameters()

            # Restore selected parameters in order
            selected_data = config.get('selected_params', [])
            for param_data in selected_data:
                param_name = param_data.get('Name', '')
                param_display = param_data.get('DisplayName', '')

                # Find in available params and move to selected
                found = False
                for available_param in list(self.available_params):
                    if available_param.Name == param_name:
                        self.available_params.Remove(available_param)
                        self.selected_params.Add(available_param)
                        found = True
                        break

                # If not found in available (custom field/separator), recreate it
                if not found and (param_name.startswith('Custom_') or param_name.startswith('Separator_')):
                    custom_param = ParameterItem(param_name, param_display, False)
                    self.selected_params.Add(custom_param)

        except Exception as e:
            # Silently ignore cache load errors
            pass

    @staticmethod
    def show_dialog(doc, element_type='sheet'):
        """Show the parameter selector dialog.

        Args:
            doc: Revit document
            element_type: 'sheet' or 'view'

        Returns:
            String pattern if OK was clicked, None if cancelled
        """
        try:
            dialog = ParameterSelectorDialog(doc, element_type)
            result = dialog.ShowDialog()
            if result:
                return dialog.selected_result
            return None
        except Exception as e:
            forms.alert("Error showing dialog: {}".format(str(e)))
            return None
