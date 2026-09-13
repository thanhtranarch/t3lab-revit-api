# -*- coding: utf-8 -*-
"""Parameter Manager — event handling for the Parameter Manager launcher window."""

import os
import codecs
import builtins as __builtin__

import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

import System
from System.Windows import (Window, Thickness, GridLength, GridUnitType,
                            HorizontalAlignment, VerticalAlignment, FontWeights, Visibility,
                            MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult)
from System.Windows.Controls import (StackPanel, TextBlock, TextBox, Button,
                                      ComboBox, ComboBoxItem, DataGrid, Orientation,
                                      DataGridTextColumn, ScrollViewer, ListBox,
                                      ListBoxItem, SelectionMode)
import System.Windows.Controls as WPFControls
WPFGrid = WPFControls.Grid
WPFRowDefinition = WPFControls.RowDefinition
WPFColumnDefinition = WPFControls.ColumnDefinition
WPFBorder = WPFControls.Border

from System.Windows.Media import BrushConverter, SolidColorBrush
from System.Windows.Data import Binding as WPFBinding
from System.Windows.Controls import DataGridLength
from System.Collections.ObjectModel import ObservableCollection
from System import Object
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Reflection import BindingFlags

from pyrevit import revit, DB, forms, script
from GUI.WPF_Base import T3WPFWindow, to_items_source


_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'ManaPara.xaml')

_brush_converter = BrushConverter()

def _hex_brush(hex_color):
    try:
        return _brush_converter.ConvertFromString(hex_color)
    except:
        return SolidColorBrush()


# ============================================================================
# VERSION HELPERS
# ============================================================================
from Snippets._compat import make_eid, eid_value

def _eid_int(element_id):
    """ElementId integer value — compatible across Revit 2020-2027+."""
    return eid_value(element_id)


def _get_revit_version():
    try:
        return int(revit.doc.Application.VersionNumber)
    except:
        return 2024


# ============================================================================
# MODULE-LEVEL HELPER FUNCTIONS (used by dialog classes and window methods)
# ============================================================================

def _get_group_name_from_definition(definition):
    """Return (group_display_name, group_id) — compatible across versions."""
    try:
        if hasattr(definition, 'GetGroupTypeId'):
            group_type_id = definition.GetGroupTypeId()
            if group_type_id and hasattr(group_type_id, 'TypeId'):
                try:
                    label = DB.LabelUtils.GetLabelForGroup(group_type_id)
                    if label:
                        return label, group_type_id
                except:
                    pass
                type_id_str = group_type_id.TypeId
                if type_id_str:
                    parts = type_id_str.split(':')
                    name = parts[-1] if len(parts) > 1 else parts[0]
                    name = name.split('-')[0]
                    name = name.replace('autodesk.parameter.group.', '')
                    name = name.replace('.', ' ').title()
                    return name, group_type_id
    except:
        pass

    try:
        param_group_enum = definition.ParameterGroup
        group_name = str(param_group_enum).replace('PG_', '').replace('_', ' ').title()
        return group_name, param_group_enum
    except:
        pass

    return "Unknown", None


def _get_parameter_data_type(definition):
    """Return readable data type string — compatible across versions."""
    try:
        if hasattr(definition, 'GetDataType'):
            spec_type_id = definition.GetDataType()
            try:
                label = DB.LabelUtils.GetLabelForSpec(spec_type_id)
                if label:
                    return label
            except:
                pass

            type_id_str = spec_type_id.TypeId if hasattr(spec_type_id, 'TypeId') else str(spec_type_id)
            if type_id_str and 'autodesk.spec' in type_id_str.lower():
                parts = type_id_str.split(':')
                if len(parts) > 1:
                    type_part = parts[-1].split('-')[0].replace('spec.', '')
                    type_map = {
                        'string': 'Text', 'int': 'Integer', 'integer': 'Integer',
                        'double': 'Number', 'number': 'Number', 'length': 'Length',
                        'area': 'Area', 'volume': 'Volume', 'angle': 'Angle',
                        'url': 'URL', 'material': 'Material', 'yesno': 'Yes/No',
                        'bool': 'Yes/No', 'boolean': 'Yes/No',
                        'multilinetext': 'Multiline Text', 'familytype': 'Family Type',
                        'image': 'Image',
                    }
                    for key, val in type_map.items():
                        if key in type_part.lower():
                            return val
                    return type_part.replace('.', ' ').title()

        rev_version = _get_revit_version()
        if rev_version < 2026:
            try:
                if hasattr(definition, 'ParameterType'):
                    return str(definition.ParameterType)
            except:
                pass
    except:
        pass
    return "Unknown"


def _get_shared_parameter_guids():
    """Return set of shared parameter names found in the document."""
    shared_names = set()
    try:
        doc = revit.doc
        collector = DB.FilteredElementCollector(doc).OfClass(DB.SharedParameterElement)
        for elem in collector:
            try:
                shared_names.add(elem.GetDefinition().Name)
            except:
                pass
        try:
            def_file = doc.Application.OpenSharedParameterFile()
            if def_file:
                for grp in def_file.Groups:
                    for defn in grp.Definitions:
                        shared_names.add(defn.Name)
        except:
            pass
    except:
        pass
    return shared_names


def _get_all_parameter_groups():
    """Return sorted list of (group_id, display_name) tuples."""
    groups = []
    rev_version = _get_revit_version()

    if rev_version >= 2026:
        try:
            group_type_id_type = clr.GetClrType(DB.GroupTypeId) if hasattr(DB, 'GroupTypeId') else None
            if group_type_id_type:
                props = group_type_id_type.GetProperties(BindingFlags.Public | BindingFlags.Static)
                for prop in props:
                    try:
                        forge_id = prop.GetValue(None)
                        if forge_id:
                            try:
                                label = DB.LabelUtils.GetLabelForGroup(forge_id)
                            except:
                                label = prop.Name.replace('_', ' ').title()
                            groups.append((forge_id, label))
                    except:
                        continue
                if groups:
                    return sorted(groups, key=lambda x: x[1])
        except:
            pass

    try:
        bipg_type = getattr(DB, 'BuiltInParameterGroup', None)
        if bipg_type:
            for group in DB.BuiltInParameterGroup.GetValues(bipg_type):
                if group != DB.BuiltInParameterGroup.INVALID:
                    name = str(group).replace('PG_', '').replace('_', ' ').title()
                    groups.append((group, name))
    except:
        pass

    return sorted(groups, key=lambda x: x[1])


def _get_all_categories():
    """Return sorted list of (category_object, category_name) tuples."""
    cats = []
    try:
        for cat in revit.doc.Settings.Categories:
            if cat.AllowsBoundParameters and cat.CategoryType == DB.CategoryType.Model:
                cats.append((cat, cat.Name))
    except:
        pass
    return sorted(cats, key=lambda x: x[1])


def _delete_parameter(param_name):
    doc = revit.doc
    try:
        t = DB.Transaction(doc, "T3Lab: Delete Parameter")
        t.Start()
        try:
            param_bindings = doc.ParameterBindings
            iterator = param_bindings.ForwardIterator()
            found = False
            while iterator.MoveNext():
                definition = iterator.Key
                if definition.Name == param_name:
                    param_bindings.Remove(definition)
                    found = True
                    break
            if found:
                t.Commit()
                return True, "Deleted"
            else:
                t.RollBack()
                return False, "Not found"
        except Exception as ex:
            t.RollBack()
            return False, str(ex)
    except Exception as ex:
        return False, str(ex)


def _update_parameter_group(param_name, new_group_id):
    doc = revit.doc
    try:
        t = DB.Transaction(doc, "T3Lab: Update Parameter Group")
        t.Start()
        try:
            param_bindings = doc.ParameterBindings
            iterator = param_bindings.ForwardIterator()
            definition = None
            while iterator.MoveNext():
                defn = iterator.Key
                if defn.Name == param_name:
                    definition = defn
                    break
            if not definition:
                t.RollBack()
                return False, "Parameter not found"

            if hasattr(definition, 'SetGroupTypeId'):
                try:
                    if hasattr(new_group_id, 'TypeId'):
                        definition.SetGroupTypeId(new_group_id)
                        t.Commit()
                        return True, "Updated"
                    else:
                        if hasattr(DB, 'ParameterUtils') and hasattr(DB.ParameterUtils, 'GetParameterGroupTypeId'):
                            forge_id = DB.ParameterUtils.GetParameterGroupTypeId(new_group_id)
                            definition.SetGroupTypeId(forge_id)
                            t.Commit()
                            return True, "Updated"
                except:
                    pass

            definition_type = definition.GetType()
            field_names = [
                'm_builtInParameterGroup', 'm_parameterGroup', 'parameterGroup',
                'm_group', 'group', '_builtInParameterGroup', '_parameterGroup'
            ]
            for field_name in field_names:
                try:
                    field = definition_type.GetField(
                        field_name,
                        BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public
                    )
                    if field:
                        field.SetValue(definition, new_group_id)
                        t.Commit()
                        return True, "Updated"
                except:
                    continue

            try:
                prop = definition_type.GetProperty(
                    "ParameterGroup",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic
                )
                if prop and prop.CanWrite:
                    prop.SetValue(definition, new_group_id, None)
                    t.Commit()
                    return True, "Updated"
            except:
                pass

            t.RollBack()
            return False, "Cannot modify group (API limitation)"
        except Exception as ex:
            t.RollBack()
            return False, str(ex)
    except Exception as ex:
        return False, str(ex)


def _update_parameter_categories(param_name, new_categories):
    doc = revit.doc
    app = doc.Application
    if doc.IsModifiable:
        return False, "Document already in transaction"
    try:
        t = DB.Transaction(doc, "T3Lab: Update Parameter Categories")
        t.Start()
        try:
            param_bindings = doc.ParameterBindings
            iterator = param_bindings.ForwardIterator()
            old_definition = None
            old_binding = None
            while iterator.MoveNext():
                defn = iterator.Key
                binding = iterator.Current
                if defn.Name == param_name:
                    old_definition = defn
                    old_binding = binding
                    break
            if not old_definition:
                t.RollBack()
                return False, "Parameter not found"
            new_cat_set = app.Create.NewCategorySet()
            for cat in new_categories:
                new_cat_set.Insert(cat)
            is_instance = isinstance(old_binding, DB.InstanceBinding)
            if is_instance:
                new_binding = app.Create.NewInstanceBinding(new_cat_set)
            else:
                new_binding = app.Create.NewTypeBinding(new_cat_set)
            success = param_bindings.ReInsert(old_definition, new_binding)
            if success:
                t.Commit()
                return True, "Categories updated"
            else:
                t.RollBack()
                return False, "Failed to update categories"
        except Exception as ex:
            t.RollBack()
            return False, str(ex)
    except Exception as ex:
        return False, str(ex)


def _update_parameter_binding(param_name, new_binding_type):
    doc = revit.doc
    app = doc.Application
    if doc.IsModifiable:
        return False, "Document already in transaction"
    try:
        t = DB.Transaction(doc, "T3Lab: Update Parameter Binding")
        t.Start()
        try:
            param_bindings = doc.ParameterBindings
            iterator = param_bindings.ForwardIterator()
            old_definition = None
            old_binding = None
            while iterator.MoveNext():
                defn = iterator.Key
                binding = iterator.Current
                if defn.Name == param_name:
                    old_definition = defn
                    old_binding = binding
                    break
            if not old_definition:
                t.RollBack()
                return False, "Parameter not found"
            categories = old_binding.Categories
            is_instance = isinstance(old_binding, DB.InstanceBinding)
            wants_instance = (new_binding_type == "Instance")
            if is_instance == wants_instance:
                t.RollBack()
                return True, "Already {}".format(new_binding_type)
            if wants_instance:
                new_binding = app.Create.NewInstanceBinding(categories)
            else:
                new_binding = app.Create.NewTypeBinding(categories)
            success = param_bindings.ReInsert(old_definition, new_binding)
            if success:
                t.Commit()
                return True, "Updated"
            else:
                t.RollBack()
                return False, "ReInsert failed"
        except Exception as ex:
            t.RollBack()
            return False, str(ex)
    except Exception as ex:
        return False, str(ex)


# ============================================================================
# PARAMETER DATA MODEL
# ============================================================================

try:
    _Reactive = getattr(forms, 'Reactive', object)
except Exception:
    _Reactive = object


class ParameterItem(_Reactive):
    """WPF-bindable parameter data model."""

    def __init__(self, name, param_type, data_type, group, group_id,
                 binding, categories, category_objects=None):
        self._handlers = []
        self._name = name
        self._param_type = param_type
        self._data_type = data_type
        self._group = group
        self._group_id = group_id
        self._binding = binding
        self._categories = categories
        self._category_objects = category_objects or []
        self._is_shared = (param_type == "Shared")

    # INotifyPropertyChanged
    def add_PropertyChanged(self, handler):
        self._handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)

    def OnPropertyChanged(self, prop_name):
        for h in self._handlers:
            try:
                h(self, PropertyChangedEventArgs(prop_name))
            except:
                pass

    @property
    def name(self):
        return self._name

    @property
    def param_type(self):
        return self._param_type

    @property
    def data_type(self):
        return self._data_type

    @property
    def group(self):
        return self._group

    @group.setter
    def group(self, value):
        self._group = value
        self.OnPropertyChanged("group")

    @property
    def group_id(self):
        return self._group_id

    @group_id.setter
    def group_id(self, value):
        self._group_id = value

    @property
    def binding(self):
        return self._binding

    @binding.setter
    def binding(self, value):
        self._binding = value
        self.OnPropertyChanged("binding")

    @property
    def categories(self):
        return self._categories

    @categories.setter
    def categories(self, value):
        self._categories = value
        self.OnPropertyChanged("categories")

    @property
    def category_objects(self):
        return self._category_objects

    @category_objects.setter
    def category_objects(self, value):
        self._category_objects = value

    @property
    def is_shared(self):
        return self._is_shared


# ============================================================================
# EDIT DIALOGS (pure Python WPF windows — no XAML needed)
# ============================================================================

class EditGroupDialog(Window):
    def __init__(self, item, all_groups):
        self.item = item
        self.all_groups = all_groups
        self.result = False
        self._build_ui()

    def _build_ui(self):
        self.Title = "Edit Parameter Group"
        self.Width = 450
        self.Height = 500
        self.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        self.Background = _hex_brush("#F8FAFC")

        main_grid = WPFGrid()
        main_grid.Margin = Thickness(15)
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Star)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))

        hdr = WPFBorder()
        hdr.Background = _hex_brush("#0F172A")
        hdr.Padding = Thickness(10, 8, 10, 8)
        hdr.Margin = Thickness(0, 0, 0, 10)
        hdr_text = TextBlock()
        hdr_text.Text = "Edit Parameter Group: {}".format(self.item.name)
        hdr_text.FontSize = 14
        hdr_text.FontWeight = FontWeights.SemiBold
        hdr_text.Foreground = _hex_brush("#FFFFFF")
        hdr.Child = hdr_text
        WPFGrid.SetRow(hdr, 0)
        main_grid.Children.Add(hdr)

        info = TextBlock()
        info.Text = "Current Group: {}".format(self.item.group)
        info.Foreground = _hex_brush("#64748B")
        info.Margin = Thickness(0, 0, 0, 8)
        WPFGrid.SetRow(info, 1)
        main_grid.Children.Add(info)

        list_panel = StackPanel()
        lbl = TextBlock()
        lbl.Text = "Select New Group:"
        lbl.FontWeight = FontWeights.SemiBold
        lbl.Margin = Thickness(0, 0, 0, 4)
        list_panel.Children.Add(lbl)
        self.group_listbox = ListBox()
        self.group_listbox.Height = 300
        for group_id, group_name in self.all_groups:
            lb = ListBoxItem()
            lb.Content = group_name
            lb.Tag = group_id
            if group_name == self.item.group:
                lb.IsSelected = True
            self.group_listbox.Items.Add(lb)
        list_panel.Children.Add(self.group_listbox)
        WPFGrid.SetRow(list_panel, 2)
        main_grid.Children.Add(list_panel)

        btn_panel = StackPanel()
        btn_panel.Orientation = Orientation.Horizontal
        btn_panel.HorizontalAlignment = HorizontalAlignment.Right
        btn_panel.Margin = Thickness(0, 10, 0, 0)
        btn_apply = Button()
        btn_apply.Content = "Apply"
        btn_apply.Width = 90
        btn_apply.Height = 32
        btn_apply.Margin = Thickness(0, 0, 8, 0)
        btn_apply.Background = _hex_brush("#10B981")
        btn_apply.Foreground = _hex_brush("#FFFFFF")
        btn_apply.Click += self._on_apply
        btn_panel.Children.Add(btn_apply)
        btn_cancel = Button()
        btn_cancel.Content = "Cancel"
        btn_cancel.Width = 90
        btn_cancel.Height = 32
        btn_cancel.Click += lambda s, e: self.Close()
        btn_panel.Children.Add(btn_cancel)
        WPFGrid.SetRow(btn_panel, 3)
        main_grid.Children.Add(btn_panel)
        self.Content = main_grid

    def _on_apply(self, sender, args):
        if not self.group_listbox.SelectedItem:
            MessageBox.Show("Please select a group.", "Warning",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        sel = self.group_listbox.SelectedItem
        new_name = sel.Content
        new_id = sel.Tag
        if new_name == self.item.group:
            MessageBox.Show("No change — same group selected.", "Info",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            return
        success, msg = _update_parameter_group(self.item.name, new_id)
        if success:
            self.item.group = new_name
            self.item.group_id = new_id
            self.result = True
            MessageBox.Show("Group updated successfully.", "Success",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            self.Close()
        else:
            MessageBox.Show("Failed to update group:\n\n{}".format(msg), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)


class EditCategoriesDialog(Window):
    def __init__(self, item, all_categories):
        self.item = item
        self.all_categories = all_categories
        self.result = False
        self._build_ui()

    def _build_ui(self):
        self.Title = "Edit Parameter Categories"
        self.Width = 450
        self.Height = 560
        self.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        self.Background = _hex_brush("#F8FAFC")

        main_grid = WPFGrid()
        main_grid.Margin = Thickness(15)
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Star)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))

        hdr = WPFBorder()
        hdr.Background = _hex_brush("#3B82F6")
        hdr.Padding = Thickness(10, 8, 10, 8)
        hdr.Margin = Thickness(0, 0, 0, 10)
        hdr_text = TextBlock()
        hdr_text.Text = "Edit Categories: {}".format(self.item.name)
        hdr_text.FontSize = 14
        hdr_text.FontWeight = FontWeights.SemiBold
        hdr_text.Foreground = _hex_brush("#FFFFFF")
        hdr.Child = hdr_text
        WPFGrid.SetRow(hdr, 0)
        main_grid.Children.Add(hdr)

        info = TextBlock()
        info.Text = "Type: {} | Binding: {}".format(self.item.param_type, self.item.binding)
        info.Foreground = _hex_brush("#64748B")
        info.Margin = Thickness(0, 0, 0, 6)
        WPFGrid.SetRow(info, 1)
        main_grid.Children.Add(info)

        quick_panel = StackPanel()
        quick_panel.Orientation = Orientation.Horizontal
        quick_panel.Margin = Thickness(0, 0, 0, 8)
        btn_all = Button()
        btn_all.Content = "Select All"
        btn_all.Padding = Thickness(8, 4, 8, 4)
        btn_all.Margin = Thickness(0, 0, 6, 0)
        btn_all.Click += self._on_select_all
        quick_panel.Children.Add(btn_all)
        btn_none = Button()
        btn_none.Content = "Select None"
        btn_none.Padding = Thickness(8, 4, 8, 4)
        btn_none.Click += self._on_select_none
        quick_panel.Children.Add(btn_none)
        WPFGrid.SetRow(quick_panel, 2)
        main_grid.Children.Add(quick_panel)

        list_panel = StackPanel()
        lbl = TextBlock()
        lbl.Text = "Select Categories:"
        lbl.FontWeight = FontWeights.SemiBold
        lbl.Margin = Thickness(0, 0, 0, 4)
        list_panel.Children.Add(lbl)
        current_names = []
        if self.item.categories and self.item.categories != "N/A":
            current_names = [c.strip() for c in self.item.categories.split(',')]
        self.cat_listbox = ListBox()
        self.cat_listbox.Height = 300
        self.cat_listbox.SelectionMode = SelectionMode.Multiple
        for cat_obj, cat_name in self.all_categories:
            lb = ListBoxItem()
            lb.Content = cat_name
            lb.Tag = cat_obj
            if cat_name in current_names:
                lb.IsSelected = True
            self.cat_listbox.Items.Add(lb)
        list_panel.Children.Add(self.cat_listbox)
        WPFGrid.SetRow(list_panel, 3)
        main_grid.Children.Add(list_panel)

        btn_panel = StackPanel()
        btn_panel.Orientation = Orientation.Horizontal
        btn_panel.HorizontalAlignment = HorizontalAlignment.Right
        btn_panel.Margin = Thickness(0, 10, 0, 0)
        btn_apply = Button()
        btn_apply.Content = "Apply"
        btn_apply.Width = 90
        btn_apply.Height = 32
        btn_apply.Margin = Thickness(0, 0, 8, 0)
        btn_apply.Background = _hex_brush("#10B981")
        btn_apply.Foreground = _hex_brush("#FFFFFF")
        btn_apply.Click += self._on_apply
        btn_panel.Children.Add(btn_apply)
        btn_cancel = Button()
        btn_cancel.Content = "Cancel"
        btn_cancel.Width = 90
        btn_cancel.Height = 32
        btn_cancel.Click += lambda s, e: self.Close()
        btn_panel.Children.Add(btn_cancel)
        WPFGrid.SetRow(btn_panel, 4)
        main_grid.Children.Add(btn_panel)
        self.Content = main_grid

    def _on_select_all(self, sender, args):
        for lb in self.cat_listbox.Items:
            lb.IsSelected = True

    def _on_select_none(self, sender, args):
        for lb in self.cat_listbox.Items:
            lb.IsSelected = False

    def _on_apply(self, sender, args):
        selected = [lb for lb in self.cat_listbox.Items if lb.IsSelected]
        if not selected:
            MessageBox.Show("Please select at least one category.", "Warning",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        cats = [lb.Tag for lb in selected]
        names = [lb.Content for lb in selected]
        success, msg = _update_parameter_categories(self.item.name, cats)
        if success:
            self.item.categories = ', '.join(names)
            self.item.category_objects = cats
            self.result = True
            MessageBox.Show("Categories updated successfully.", "Success",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            self.Close()
        else:
            MessageBox.Show("Failed:\n\n{}".format(msg), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)


class EditBindingDialog(Window):
    def __init__(self, item):
        self.item = item
        self.result = False
        self._build_ui()

    def _build_ui(self):
        self.Title = "Edit Parameter Binding"
        self.Width = 400
        self.Height = 280
        self.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        self.Background = _hex_brush("#F8FAFC")

        main_grid = WPFGrid()
        main_grid.Margin = Thickness(15)
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        main_grid.RowDefinitions.Add(WPFRowDefinition(Height=GridLength(1, GridUnitType.Auto)))

        hdr = WPFBorder()
        hdr.Background = _hex_brush("#F59E0B")
        hdr.Padding = Thickness(10, 8, 10, 8)
        hdr.Margin = Thickness(0, 0, 0, 10)
        hdr_text = TextBlock()
        hdr_text.Text = "Edit Binding: {}".format(self.item.name)
        hdr_text.FontSize = 14
        hdr_text.FontWeight = FontWeights.SemiBold
        hdr_text.Foreground = _hex_brush("#FFFFFF")
        hdr.Child = hdr_text
        WPFGrid.SetRow(hdr, 0)
        main_grid.Children.Add(hdr)

        info_panel = StackPanel()
        info_panel.Margin = Thickness(0, 0, 0, 10)
        info1 = TextBlock()
        info1.Text = "Type: {} | Current Binding: {}".format(self.item.param_type, self.item.binding)
        info1.Foreground = _hex_brush("#64748B")
        info_panel.Children.Add(info1)
        if not self.item.is_shared:
            warn = TextBlock()
            warn.Text = u"⚠ Project parameters cannot change binding via API"
            warn.Foreground = _hex_brush("#EF4444")
            warn.Margin = Thickness(0, 6, 0, 0)
            warn.TextWrapping = System.Windows.TextWrapping.Wrap
            info_panel.Children.Add(warn)
        WPFGrid.SetRow(info_panel, 1)
        main_grid.Children.Add(info_panel)

        select_panel = StackPanel()
        select_panel.Margin = Thickness(0, 0, 0, 10)
        lbl = TextBlock()
        lbl.Text = "Select Binding:"
        lbl.FontWeight = FontWeights.SemiBold
        lbl.Margin = Thickness(0, 0, 0, 4)
        select_panel.Children.Add(lbl)
        self.binding_combo = ComboBox()
        self.binding_combo.Padding = Thickness(8, 4, 8, 4)
        self.binding_combo.IsEnabled = self.item.is_shared
        for opt in ["Instance", "Type"]:
            cb = ComboBoxItem()
            cb.Content = opt
            self.binding_combo.Items.Add(cb)
        self.binding_combo.SelectedIndex = 0 if self.item.binding == "Instance" else 1
        select_panel.Children.Add(self.binding_combo)
        WPFGrid.SetRow(select_panel, 2)
        main_grid.Children.Add(select_panel)

        btn_panel = StackPanel()
        btn_panel.Orientation = Orientation.Horizontal
        btn_panel.HorizontalAlignment = HorizontalAlignment.Right
        btn_apply = Button()
        btn_apply.Content = "Apply"
        btn_apply.Width = 90
        btn_apply.Height = 32
        btn_apply.Margin = Thickness(0, 0, 8, 0)
        btn_apply.Background = _hex_brush("#10B981")
        btn_apply.Foreground = _hex_brush("#FFFFFF")
        btn_apply.IsEnabled = self.item.is_shared
        btn_apply.Click += self._on_apply
        btn_panel.Children.Add(btn_apply)
        btn_cancel = Button()
        btn_cancel.Content = "Cancel"
        btn_cancel.Width = 90
        btn_cancel.Height = 32
        btn_cancel.Click += lambda s, e: self.Close()
        btn_panel.Children.Add(btn_cancel)
        WPFGrid.SetRow(btn_panel, 3)
        main_grid.Children.Add(btn_panel)
        self.Content = main_grid

    def _on_apply(self, sender, args):
        new_binding = self.binding_combo.SelectedItem.Content
        if new_binding == self.item.binding:
            MessageBox.Show("No change — same binding selected.", "Info",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            return
        success, msg = _update_parameter_binding(self.item.name, new_binding)
        if success:
            self.item.binding = new_binding
            self.result = True
            MessageBox.Show("Binding updated successfully.", "Success",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            self.Close()
        else:
            MessageBox.Show("Failed:\n\n{}".format(msg), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)


# ============================================================================
# TRANSFER PARAMETERS SUPPORT
# ============================================================================

# Category map for Transfer tab — display name -> BuiltInCategory enum
CATEGORY_MAP = {
    "Walls": DB.BuiltInCategory.OST_Walls,
    "Floors": DB.BuiltInCategory.OST_Floors,
    "Ceilings": DB.BuiltInCategory.OST_Ceilings,
    "Roofs": DB.BuiltInCategory.OST_Roofs,
    "Doors": DB.BuiltInCategory.OST_Doors,
    "Windows": DB.BuiltInCategory.OST_Windows,
    "Rooms": DB.BuiltInCategory.OST_Rooms,
    "Areas": DB.BuiltInCategory.OST_Areas,
    "Spaces": DB.BuiltInCategory.OST_MEPSpaces,
    "Columns": DB.BuiltInCategory.OST_Columns,
    "Structural Columns": DB.BuiltInCategory.OST_StructuralColumns,
    "Structural Framing": DB.BuiltInCategory.OST_StructuralFraming,
    "Structural Foundations": DB.BuiltInCategory.OST_StructuralFoundation,
    "Furniture": DB.BuiltInCategory.OST_Furniture,
    "Casework": DB.BuiltInCategory.OST_Casework,
    "Generic Models": DB.BuiltInCategory.OST_GenericModel,
    "Mechanical Equipment": DB.BuiltInCategory.OST_MechanicalEquipment,
    "Plumbing Fixtures": DB.BuiltInCategory.OST_PlumbingFixtures,
    "Electrical Equipment": DB.BuiltInCategory.OST_ElectricalEquipment,
    "Electrical Fixtures": DB.BuiltInCategory.OST_ElectricalFixtures,
    "Lighting Fixtures": DB.BuiltInCategory.OST_LightingFixtures,
    "Pipe Fittings": DB.BuiltInCategory.OST_PipeFitting,
    "Pipe Accessories": DB.BuiltInCategory.OST_PipeAccessory,
    "Duct Fittings": DB.BuiltInCategory.OST_DuctFitting,
    "Duct Accessories": DB.BuiltInCategory.OST_DuctAccessory,
    "Conduit Fittings": DB.BuiltInCategory.OST_ConduitFitting,
    "Cable Trays": DB.BuiltInCategory.OST_CableTray,
    "Curtain Panels": DB.BuiltInCategory.OST_CurtainWallPanels,
    "Curtain Wall Mullions": DB.BuiltInCategory.OST_CurtainWallMullions,
    "Parking": DB.BuiltInCategory.OST_Parking,
    "Planting": DB.BuiltInCategory.OST_Planting,
    "Site": DB.BuiltInCategory.OST_Site,
    "Topography": DB.BuiltInCategory.OST_Topography,
    "Sheets": DB.BuiltInCategory.OST_Sheets,
}


class TransferPreviewRow(object):
    """Data class for dg_transfer_preview binding."""
    def __init__(self, element_name, element_id, source_val, target_param, status=""):
        self.ElementName = element_name
        self.ElementId = str(element_id)
        self.SourceValue = source_val
        self.TargetParam = target_param
        self.Status = status


# ============================================================================
# PARAMETER LOADER SUPPORT
# ============================================================================

class ParamRequirement(object):
    """A single parameter requirement: param name -> list of categories."""

    GROUP_MAP_RULES = [
        (["Ifc", "IfcObject", "IfcExport"], "IFC Parameters", "PG_IFC"),
        (["AGF_", "AST_", "ACN_", "ALS_", "AVF_"], "IFC Parameters", "PG_IFC"),
        (["AI_", "TED_"], "IFC Parameters", "PG_IFC"),
        (["Fire", "Smoke", "Shelter", "Sprinkler"], "Fire Protection", "PG_FIRE_PROTECTION"),
        (["Width", "Height", "Length", "Depth", "Thickness", "Diameter",
          "Area", "Volume", "Gradient", "Girth", "InnerDiameter", "OuterDiameter",
          "InternalLength", "InternalWidth", "ClearWidth", "ClearHeight", "ClearDepth",
          "OverallWidth", "Breadth", "InnerLength", "InnerWidth", "OuterLength", "OuterWidth",
          "RiserHeight", "TreadLength", "BasePlateThickness", "NominalDiameter",
          "StructuralWidth", "StructuralHeight", "MountingHeight", "SafetyBarrierHeight",
          "Hose_NominalDiameter"], "Dimensions", "PG_GEOMETRY"),
        (["Material", "MaterialGrade", "ReinforcementSteelGrade", "SectionFabricationMethod"],
         "Materials and Finishes", "PG_MATERIALS"),
        (["Phase", "Status", "Retrofit"], "Phasing", "PG_PHASING"),
        (["Mark", "SpaceName", "UnitNumber", "LotNumber", "FamilyLot",
          "BoreholeRef", "TreeNumber", "HedgeNumber"], "Identity Data", "PG_IDENTITY_DATA"),
        (["System", "SystemType", "SystemName"], "Mechanical", "PG_MECHANICAL"),
        (["Ventilation", "VentilationType", "VentilationMode", "CValue",
          "SoundPower", "SoundPressure"], "Mechanical", "PG_MECHANICAL"),
        (["Capacity", "NominalCapacity", "EffectiveCapacity", "LoadingCapacity",
          "OccupancyLoad", "WorkingLoad", "PumpHead", "Duty", "Standby",
          "CompactionRatio"], "Structural", "PG_STRUCTURAL"),
        (["Rebar", "Stirrups", "StirrupsType", "MainRebar", "TopMain", "BottomMain",
          "TopDistribution", "BottomDistribution", "SideBar", "WeldedMesh",
          "TopLeft", "TopMiddle", "TopRight", "BottomLeft", "BottomMiddle", "BottomRight",
          "LatticeGirder", "ColumnCage", "SpliceConnection", "SpliceDetail",
          "PrefabricationReinforcement", "ReinforcementLength"], "Structural", "PG_STRUCTURAL"),
        (["Connection", "ConnectionType", "ConnectionDetail", "MechanicalConnectionType"],
         "Structural", "PG_STRUCTURAL"),
        (["Construction", "ConstructionMethod"], "Construction", "PG_CONSTRUCTION"),
        (["Plumbing", "WELS", "TradeEffluent", "IsPotable", "WaterSupply",
          "Perforated", "PreInsulated", "DemountableStructure"], "Plumbing", "PG_PLUMBING"),
        (["Electrical", "PWCS_Flushing", "Purpose"], "Electrical", "PG_ELECTRICAL"),
    ]

    def __init__(self, name):
        self.name = name
        self.categories = []
        self.disciplines = set()
        self.selected = True
        self.already_exists = False
        self.status = ""
        self.group_under = ""
        self.group_pg = ""
        self._auto_map_group()

    def _auto_map_group(self):
        """Auto-detect parameter group based on name patterns."""
        for keywords, group_name, pg_key in self.GROUP_MAP_RULES:
            for kw in keywords:
                if self.name.startswith(kw) or self.name == kw:
                    self.group_under = group_name
                    self.group_pg = pg_key
                    return
        self.group_under = "IFC Parameters"
        self.group_pg = "PG_IFC"

    @property
    def group_display(self):
        return self.group_under or "IFC Parameters"


class RequirementParser(object):
    """Parse XML or Excel into list of ParamRequirements."""

    @staticmethod
    def from_xml(filepath):
        """Parse Autodesk Model Checker XML."""
        import xml.etree.ElementTree as ET
        tree = ET.parse(filepath)
        root = tree.getroot()

        param_map = {}
        for h in root.findall("Heading"):
            disc = h.get("HeadingText", "")
            for s in h.findall("Section"):
                cat = s.get("SectionName", "")
                for c in s.findall("Check"):
                    param = c.get("CheckName", "")
                    if not param:
                        continue
                    if param not in param_map:
                        param_map[param] = {"cats": set(), "discs": set()}
                    param_map[param]["cats"].add(cat)
                    param_map[param]["discs"].add(disc)

        reqs = []
        for name, data in sorted(param_map.items()):
            req = ParamRequirement(name)
            req.categories = sorted(data["cats"])
            req.disciplines = data["discs"]
            reqs.append(req)
        return reqs

    @staticmethod
    def from_excel(filepath, column_map):
        """Parse Excel with user-specified column mapping dict."""
        try:
            clr.AddReference('Microsoft.Office.Interop.Excel')
            from Microsoft.Office.Interop import Excel as ExcelInterop

            sheet_name = column_map.get("sheet_name")
            col_param = column_map.get("col_param", 1)
            col_category = column_map.get("col_category")
            col_discipline = column_map.get("col_discipline")
            header_row = column_map.get("header_row", 1)

            excel_app = ExcelInterop.ApplicationClass()
            excel_app.Visible = False
            excel_app.DisplayAlerts = False
            wb = excel_app.Workbooks.Open(filepath)

            if sheet_name:
                ws = wb.Sheets[sheet_name]
            else:
                ws = wb.Sheets[1]

            rows = ws.UsedRange.Rows.Count
            param_map = {}
            data_start = header_row + 1

            for r in range(data_start, rows + 1):
                raw_param = ws.Cells[r, col_param].Value2
                if raw_param is None:
                    continue
                if isinstance(raw_param, float) and raw_param == int(raw_param):
                    param = str(int(raw_param))
                else:
                    param = str(raw_param).strip()

                cat = ""
                if col_category:
                    raw_cat = ws.Cells[r, col_category].Value2
                    if raw_cat is not None:
                        cat = str(raw_cat).strip()

                disc = ""
                if col_discipline:
                    raw_disc = ws.Cells[r, col_discipline].Value2
                    if raw_disc is not None:
                        disc = str(raw_disc).strip()

                if not param:
                    continue

                if param not in param_map:
                    param_map[param] = {"cats": set(), "discs": set()}
                if cat:
                    param_map[param]["cats"].add(cat)
                if disc:
                    param_map[param]["discs"].add(disc)

            wb.Close(False)
            excel_app.Quit()
            System.Runtime.InteropServices.Marshal.ReleaseComObject(excel_app)

            reqs = []
            for name, data in sorted(param_map.items()):
                req = ParamRequirement(name)
                req.categories = sorted(data["cats"])
                req.disciplines = data["discs"]
                reqs.append(req)
            return reqs

        except Exception as ex:
            raise Exception("Excel parse error: {}".format(str(ex)))


def _get_group_type_id(pg_key):
    """Get ForgeTypeId for parameter group — compatible with Revit 2024-2026+."""
    group_map = {
        "PG_IFC": "Ifc",
        "PG_GEOMETRY": "Geometry",
        "PG_FIRE_PROTECTION": "FireProtection",
        "PG_MATERIALS": "Materials",
        "PG_IDENTITY_DATA": "IdentityData",
        "PG_STRUCTURAL": "Structural",
        "PG_MECHANICAL": "Mechanical",
        "PG_CONSTRUCTION": "Construction",
        "PG_PLUMBING": "Plumbing",
        "PG_ELECTRICAL": "Electrical",
        "PG_PHASING": "Phasing",
        "PG_GENERAL": "General",
        "PG_DATA": "Data",
    }
    try:
        from Autodesk.Revit.DB import GroupTypeId
        attr_name = group_map.get(pg_key, "Ifc")
        return getattr(GroupTypeId, attr_name)
    except:
        pass
    try:
        return getattr(DB.BuiltInParameterGroup, pg_key,
                       DB.BuiltInParameterGroup.PG_IFC)
    except:
        pass
    return None


def _create_ext_def_options(param_name):
    """Create ExternalDefinitionCreationOptions — compatible with Revit 2024-2026+."""
    try:
        from Autodesk.Revit.DB import SpecTypeId, ExternalDefinitionCreationOptions
        opt = ExternalDefinitionCreationOptions(param_name, SpecTypeId.String.Text)
        opt.Visible = True
        return opt
    except:
        pass
    try:
        from Autodesk.Revit.DB import ExternalDefinitionCreationOptions, ParameterType
        opt = ExternalDefinitionCreationOptions(param_name, ParameterType.Text)
        opt.Visible = True
        return opt
    except:
        pass
    return None


def _bind_param_insert(document, defn, binding, pg_key="PG_IFC"):
    """Insert parameter binding — compatible with Revit 2024-2026+."""
    group_id = _get_group_type_id(pg_key)
    if group_id is not None:
        try:
            return document.ParameterBindings.Insert(defn, binding, group_id)
        except:
            pass
    try:
        return document.ParameterBindings.Insert(defn, binding)
    except:
        pass
    return False


class ParameterAdder(object):
    """Add shared parameters to Revit model."""

    def __init__(self, doc, application):
        self.doc = doc
        self.app = application
        self.log = []
        self._original_sp_path = None
        self._temp_sp_path = None

    def _get_existing_params(self):
        existing = set()
        bm = self.doc.ParameterBindings
        it = bm.ForwardIterator()
        it.Reset()
        while it.MoveNext():
            try:
                existing.add(it.Key.Name)
            except:
                pass
        return existing

    def _setup_temp_shared_param_file(self):
        """Create temp shared param file using TEMP env var (not script dir)."""
        self._original_sp_path = self.app.SharedParametersFilename
        temp_dir = os.environ.get('TEMP', os.path.dirname(__file__))
        self._temp_sp_path = os.path.join(temp_dir, 'DQT_IFC_SG_SharedParams.txt')

        if not os.path.exists(self._temp_sp_path):
            with open(self._temp_sp_path, 'w') as f:
                f.write("# IFC+SG Shared Parameters - Auto-generated by T3Lab\n")
                f.write("*META\tVERSION\tMINVERSION\n")
                f.write("META\t2\t1\n")

        self.app.SharedParametersFilename = self._temp_sp_path
        sp_file = self.app.OpenSharedParameterFile()
        return sp_file

    def _restore_shared_param_file(self):
        if self._original_sp_path:
            try:
                self.app.SharedParametersFilename = self._original_sp_path
            except:
                pass

    def _find_definition_in_file(self, sp_file, param_name):
        for group in sp_file.Groups:
            for defn in group.Definitions:
                if defn.Name == param_name:
                    return defn
        return None

    def _create_definition(self, sp_file, group_name, param_name):
        group = None
        for g in sp_file.Groups:
            if g.Name == group_name:
                group = g
                break
        if not group:
            group = sp_file.Groups.Create(group_name)
        for d in group.Definitions:
            if d.Name == param_name:
                return d
        try:
            opt = _create_ext_def_options(param_name)
            if opt:
                return group.Definitions.Create(opt)
        except:
            pass
        return None

    def add_parameters(self, requirements, progress_callback=None):
        """Add selected parameters to model. Returns list of result dicts."""
        self.log = []
        results = []

        sp_file = self._setup_temp_shared_param_file()
        if not sp_file:
            self._restore_shared_param_file()
            return [{'name': 'setup', 'status': 'ERROR',
                     'message': 'Could not create shared parameter file'}]

        selected = [r for r in requirements if r.selected]
        total = len(selected)

        for idx, req in enumerate(selected):
            result = {'name': req.name, 'status': 'OK', 'message': ''}
            try:
                group_name = "IFC+SG Parameters"
                if req.disciplines:
                    disc_list = sorted(req.disciplines)
                    if len(disc_list) == 1:
                        group_name = "IFC+SG_{}".format(disc_list[0])

                defn = self._find_definition_in_file(sp_file, req.name)
                if not defn:
                    defn = self._create_definition(sp_file, group_name, req.name)

                if not defn:
                    req.status = "failed"
                    result['status'] = 'ERROR'
                    result['message'] = 'Could not create shared param definition'
                    results.append(result)
                    continue

                from Autodesk.Revit.DB import CategorySet, Category, InstanceBinding
                cat_set = CategorySet()
                for cat_name in req.categories:
                    bic = CATEGORY_MAP.get(cat_name)
                    if bic is not None:
                        try:
                            cat = Category.GetCategory(self.doc, bic)
                            if cat and cat.AllowsBoundParameters:
                                cat_set.Insert(cat)
                        except:
                            pass

                if cat_set.Size == 0:
                    req.status = "skipped"
                    result['status'] = 'SKIP'
                    result['message'] = 'No valid categories found'
                    results.append(result)
                    continue

                # Check for existing binding
                existing_binding = None
                bm = self.doc.ParameterBindings
                it = bm.ForwardIterator()
                it.Reset()
                while it.MoveNext():
                    try:
                        if it.Key.Name == req.name:
                            existing_binding = it.Current
                            defn = it.Key
                            break
                    except:
                        pass

                if existing_binding and hasattr(existing_binding, 'Categories'):
                    changed = False
                    for cat in cat_set:
                        try:
                            if not existing_binding.Categories.Contains(cat):
                                existing_binding.Categories.Insert(cat)
                                changed = True
                        except:
                            pass
                    if changed:
                        self.doc.ParameterBindings.ReInsert(defn, existing_binding)
                        req.status = "updated"
                    else:
                        req.status = "exists"
                        result['status'] = 'SKIP'
                else:
                    binding = InstanceBinding(cat_set)
                    pg_key = req.group_pg if req.group_pg else "PG_IFC"
                    success = False
                    try:
                        success = _bind_param_insert(self.doc, defn, binding, pg_key)
                    except:
                        pass
                    if not success:
                        try:
                            success = _bind_param_insert(self.doc, defn, binding, "PG_IFC")
                        except:
                            pass
                    if not success:
                        try:
                            success = self.doc.ParameterBindings.Insert(defn, binding)
                        except:
                            pass
                    if success:
                        req.status = "added"
                    else:
                        req.status = "failed"
                        result['status'] = 'ERROR'
                        result['message'] = 'ParameterBindings.Insert returned false'

            except Exception as ex:
                req.status = "failed"
                result['status'] = 'ERROR'
                result['message'] = str(ex)

            results.append(result)
            if progress_callback:
                progress_callback(idx + 1, total)

        self._restore_shared_param_file()
        return results


class ExcelColumnMapper(object):
    """Dialog to let user map Excel columns to Parameter/Category/Discipline."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.column_map = None
        self._sheets_data = {}
        self._window = None

        try:
            self._sheets_data = self._read_excel_headers(filepath)
        except Exception as ex:
            MessageBox.Show("Error reading Excel: {}".format(str(ex)), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)
            return

        self._build_ui()

    def _read_excel_headers(self, filepath):
        clr.AddReference('Microsoft.Office.Interop.Excel')
        from Microsoft.Office.Interop import Excel as ExcelInterop

        excel_app = ExcelInterop.ApplicationClass()
        excel_app.Visible = False
        excel_app.DisplayAlerts = False
        wb = excel_app.Workbooks.Open(filepath)

        sheets_data = {}
        for si in range(1, wb.Sheets.Count + 1):
            ws = wb.Sheets[si]
            sheet_name = ws.Name
            headers = []
            cols = ws.UsedRange.Columns.Count
            total_rows = ws.UsedRange.Rows.Count
            for ci in range(1, min(cols + 1, 30)):
                val = ws.Cells[1, ci].Value2
                headers.append(str(val) if val else "Column {}".format(ci))
            preview = []
            for ri in range(2, min(total_rows + 1, 7)):
                row_data = []
                for ci in range(1, min(cols + 1, 30)):
                    val = ws.Cells[ri, ci].Value2
                    row_data.append(str(val) if val else "")
                preview.append(row_data)
            sheets_data[sheet_name] = {
                "headers": headers,
                "preview": preview,
                "total_rows": total_rows,
                "total_cols": cols
            }

        wb.Close(False)
        excel_app.Quit()
        System.Runtime.InteropServices.Marshal.ReleaseComObject(excel_app)
        return sheets_data

    def _build_ui(self):
        self._window = Window()
        self._window.Title = "Map Excel Columns"
        self._window.Width = 600
        self._window.Height = 500
        self._window.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
        self._window.Background = _hex_brush("#F8FAFC")

        main = StackPanel()
        main.Margin = Thickness(16)

        hdr = TextBlock()
        hdr.Text = "Map Excel columns to parameter fields"
        hdr.FontSize = 14
        hdr.FontWeight = FontWeights.SemiBold
        hdr.Margin = Thickness(0, 0, 0, 12)
        main.Children.Add(hdr)

        # Sheet selector
        row_sheet = StackPanel()
        row_sheet.Orientation = Orientation.Horizontal
        row_sheet.Margin = Thickness(0, 0, 0, 8)
        lbl_sheet = TextBlock()
        lbl_sheet.Text = "Sheet:"
        lbl_sheet.Width = 120
        lbl_sheet.VerticalAlignment = VerticalAlignment.Center
        row_sheet.Children.Add(lbl_sheet)
        self._cmb_sheet = ComboBox()
        self._cmb_sheet.Width = 200
        for name in self._sheets_data.keys():
            self._cmb_sheet.Items.Add(name)
        if self._cmb_sheet.Items.Count > 0:
            self._cmb_sheet.SelectedIndex = 0
        self._cmb_sheet.SelectionChanged += self._on_sheet_changed
        row_sheet.Children.Add(self._cmb_sheet)
        main.Children.Add(row_sheet)

        # Header row
        row_hdr = StackPanel()
        row_hdr.Orientation = Orientation.Horizontal
        row_hdr.Margin = Thickness(0, 0, 0, 8)
        lbl_hdr = TextBlock()
        lbl_hdr.Text = "Header Row:"
        lbl_hdr.Width = 120
        lbl_hdr.VerticalAlignment = VerticalAlignment.Center
        row_hdr.Children.Add(lbl_hdr)
        self._txt_header_row = TextBox()
        self._txt_header_row.Text = "1"
        self._txt_header_row.Width = 60
        row_hdr.Children.Add(self._txt_header_row)
        main.Children.Add(row_hdr)

        # Column pickers
        def _make_col_row(label):
            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            row.Margin = Thickness(0, 0, 0, 8)
            lbl = TextBlock()
            lbl.Text = label
            lbl.Width = 120
            lbl.VerticalAlignment = VerticalAlignment.Center
            row.Children.Add(lbl)
            cmb = ComboBox()
            cmb.Width = 280
            row.Children.Add(cmb)
            main.Children.Add(row)
            return cmb

        self._cmb_param = _make_col_row("Parameter Column:")
        self._cmb_category = _make_col_row("Category Column:")
        self._cmb_discipline = _make_col_row("Discipline Column:")

        # Preview
        lbl_prev = TextBlock()
        lbl_prev.Text = "Preview:"
        lbl_prev.FontWeight = FontWeights.SemiBold
        lbl_prev.Margin = Thickness(0, 8, 0, 4)
        main.Children.Add(lbl_prev)
        self._txt_preview = TextBox()
        self._txt_preview.IsReadOnly = True
        self._txt_preview.Height = 100
        self._txt_preview.FontFamily = System.Windows.Media.FontFamily("Courier New")
        self._txt_preview.FontSize = 10
        self._txt_preview.VerticalScrollBarVisibility = System.Windows.Controls.ScrollBarVisibility.Auto
        main.Children.Add(self._txt_preview)

        # Buttons
        btn_row = StackPanel()
        btn_row.Orientation = Orientation.Horizontal
        btn_row.HorizontalAlignment = HorizontalAlignment.Right
        btn_row.Margin = Thickness(0, 12, 0, 0)
        btn_ok = Button()
        btn_ok.Content = "OK"
        btn_ok.Width = 90
        btn_ok.Height = 32
        btn_ok.Margin = Thickness(0, 0, 8, 0)
        btn_ok.Background = _hex_brush("#10B981")
        btn_ok.Foreground = _hex_brush("#FFFFFF")
        btn_ok.Click += self._on_ok
        btn_row.Children.Add(btn_ok)
        btn_cancel = Button()
        btn_cancel.Content = "Cancel"
        btn_cancel.Width = 90
        btn_cancel.Height = 32
        btn_cancel.Click += lambda s, e: self._window.Close()
        btn_row.Children.Add(btn_cancel)
        main.Children.Add(btn_row)

        self._window.Content = main
        self._populate_columns()

    def _populate_columns(self):
        sheet_name = str(self._cmb_sheet.SelectedItem) if self._cmb_sheet.SelectedItem else ""
        if not sheet_name or sheet_name not in self._sheets_data:
            return
        data = self._sheets_data[sheet_name]
        headers = data["headers"]
        preview = data["preview"]

        none_option = "(None)"
        for cmb in [self._cmb_param, self._cmb_category, self._cmb_discipline]:
            cmb.Items.Clear()

        self._cmb_category.Items.Add(none_option)
        self._cmb_discipline.Items.Add(none_option)

        for i, h in enumerate(headers):
            display = "Col {} - {}".format(i + 1, h)
            self._cmb_param.Items.Add(display)
            self._cmb_category.Items.Add(display)
            self._cmb_discipline.Items.Add(display)

        param_idx = 0
        cat_idx = 0
        disc_idx = 0
        for i, h in enumerate(headers):
            hl = h.lower()
            if any(k in hl for k in ["parameter", "param", "property", "field"]):
                param_idx = i
            if any(k in hl for k in ["category", "revit category", "element"]):
                cat_idx = i + 1
            if any(k in hl for k in ["discipline", "disc", "group", "heading"]):
                disc_idx = i + 1

        if self._cmb_param.Items.Count > param_idx:
            self._cmb_param.SelectedIndex = param_idx
        if self._cmb_category.Items.Count > cat_idx:
            self._cmb_category.SelectedIndex = cat_idx
        if self._cmb_discipline.Items.Count > disc_idx:
            self._cmb_discipline.SelectedIndex = disc_idx

        lines = []
        header_line = " | ".join("{:15s}".format(h[:15]) for h in headers[:8])
        lines.append(header_line)
        lines.append("-" * len(header_line))
        for row in preview:
            line = " | ".join("{:15s}".format(str(v)[:15]) for v in row[:8])
            lines.append(line)
        self._txt_preview.Text = "\n".join(lines)

    def _on_sheet_changed(self, s, e):
        self._populate_columns()

    def _on_ok(self, s, e):
        if self._cmb_param.SelectedIndex < 0:
            MessageBox.Show("Please select the Parameter Name column.", "Required",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        sheet_name = str(self._cmb_sheet.SelectedItem)
        col_param = self._cmb_param.SelectedIndex + 1
        col_category = None
        if self._cmb_category.SelectedIndex > 0:
            col_category = self._cmb_category.SelectedIndex
        col_discipline = None
        if self._cmb_discipline.SelectedIndex > 0:
            col_discipline = self._cmb_discipline.SelectedIndex

        try:
            header_row = int(self._txt_header_row.Text.strip())
        except:
            header_row = 1

        self.column_map = {
            "sheet_name": sheet_name,
            "col_param": col_param,
            "col_category": col_category,
            "col_discipline": col_discipline,
            "header_row": header_row
        }
        self._window.Close()

    def ShowDialog(self):
        if self._window:
            self._window.ShowDialog()


# ============================================================================
# LOADER GRID DATA ROW
# ============================================================================

class LoaderGridRow(_Reactive):
    """WPF-bindable row for dg_loader_params."""

    def __init__(self, req):
        self._handlers = []
        self._req = req
        self._is_selected = req.selected

    def add_PropertyChanged(self, handler):
        self._handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)

    def OnPropertyChanged(self, prop_name):
        for h in self._handlers:
            try:
                h(self, PropertyChangedEventArgs(prop_name))
            except:
                pass

    @property
    def is_selected(self):
        return self._is_selected

    @is_selected.setter
    def is_selected(self, value):
        self._is_selected = value
        self._req.selected = value
        self.OnPropertyChanged("is_selected")

    @property
    def status(self):
        st = self._req.status
        if not st:
            return "New" if not self._req.already_exists else "Exists"
        return st.capitalize()

    @property
    def name(self):
        return self._req.name

    @property
    def group(self):
        return self._req.group_display

    @property
    def categories(self):
        cats = self._req.categories
        if not cats:
            return "(none)"
        if len(cats) <= 3:
            return ", ".join(cats)
        return ", ".join(cats[:3]) + " +{}".format(len(cats) - 3)


# ============================================================================
# MAIN WINDOW
# ============================================================================

class ManaParaWindow(T3WPFWindow):
    def __init__(self, script_dir, revit_obj):
        T3WPFWindow.__init__(self, _XAML)
        self._script_dir = script_dir
        self._revit = revit_obj
        self._doc = revit.doc
        self._app = revit_obj

        # Window chrome buttons
        self.btn_minimize.Click += self._minimize
        self.btn_maximize.Click += self._maximize
        self.btn_close_chrome.Click += self._close_chrome
        self.PreviewKeyDown += self._on_key_down

        # Sidebar navigation
        self.btn_nav_browse.Checked += self._on_nav_browse
        self.btn_nav_transfer.Checked += self._on_nav_transfer
        self.btn_nav_loader.Checked += self._on_nav_loader

        # Browse Parameters tab — populate ComboBox items from Python
        self._populate_filter_combos()

        # Browse Parameters tab event handlers
        self.txt_param_search.TextChanged += self._on_param_filter_changed
        self.cmb_param_type.SelectionChanged += self._on_param_filter_changed
        self.cmb_param_binding.SelectionChanged += self._on_param_filter_changed
        self.btn_param_refresh.Click += self._on_param_refresh
        self.btn_param_edit_group.Click += self._on_param_edit_group
        self.btn_param_edit_cats.Click += self._on_param_edit_cats
        self.btn_param_edit_binding.Click += self._on_param_edit_binding
        self.btn_param_export.Click += self._on_param_export
        self.btn_param_delete.Click += self._on_param_delete

        # State
        self._all_param_items = []
        self._all_groups = []
        self._all_categories = []

        # Initialize new tabs
        self._init_transfer_tab()
        self._init_loader_tab()

        # Load parameter data
        self._load_param_data()

        # AI Mode initialization
        self._init_ai_mode()

        # Force initial tab content to render: btn_nav_browse.IsChecked was already
        # True when the XAML was parsed, so its Checked event fired before the
        # += wiring above and tab_main.SelectedIndex was never explicitly set
        # (same fix as ManaSheets/ManaViews/ManaAnno).
        self.tab_main.SelectedIndex = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _vis(self, state):
        from System.Windows import Visibility
        return Visibility.Visible if state == 'Visible' else Visibility.Collapsed

    def _minimize(self, sender, e):
        self.WindowState = System.Windows.WindowState.Minimized

    def _maximize(self, sender, e):
        if self.WindowState == System.Windows.WindowState.Maximized:
            self.WindowState = System.Windows.WindowState.Normal
        else:
            self.WindowState = System.Windows.WindowState.Maximized

    def _close_chrome(self, sender, e):
        self.Close()

    def _on_key_down(self, sender, e):
        import System.Windows.Input as WI
        if e.Key == WI.Key.Escape:
            self.Close()
        elif e.Key == WI.Key.F5:
            try:
                self._on_param_refresh(None, None)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Sidebar navigation handlers
    # ------------------------------------------------------------------

    def _on_nav_browse(self, s, e):
        self.tab_main.SelectedIndex = 0

    def _on_nav_transfer(self, s, e):
        self.tab_main.SelectedIndex = 1

    def _on_nav_loader(self, s, e):
        self.tab_main.SelectedIndex = 2

    # ------------------------------------------------------------------
    # Browse Parameters helpers
    # ------------------------------------------------------------------

    def _populate_filter_combos(self):
        for text in ["All", "Shared Only", "Project Only"]:
            cb = ComboBoxItem()
            cb.Content = text
            self.cmb_param_type.Items.Add(cb)
        self.cmb_param_type.SelectedIndex = 0

        for text in ["All", "Instance", "Type"]:
            cb = ComboBoxItem()
            cb.Content = text
            self.cmb_param_binding.Items.Add(cb)
        self.cmb_param_binding.SelectedIndex = 0

    def _set_status(self, text):
        try:
            self.txt_param_status_bar.Text = text
        except:
            pass

    def _load_param_data(self):
        self._set_status("Loading parameters...")
        self._all_param_items = []
        try:
            doc = revit.doc
            shared_names = _get_shared_parameter_guids()
            param_bindings = doc.ParameterBindings
            iterator = param_bindings.ForwardIterator()
            while iterator.MoveNext():
                definition = iterator.Key
                binding = iterator.Current
                param_name = definition.Name
                is_shared = param_name in shared_names
                param_type = "Shared" if is_shared else "Project"
                data_type = _get_parameter_data_type(definition)
                group_name, group_id = _get_group_name_from_definition(definition)
                if isinstance(binding, DB.InstanceBinding):
                    binding_type = "Instance"
                elif isinstance(binding, DB.TypeBinding):
                    binding_type = "Type"
                else:
                    binding_type = "Unknown"
                categories = []
                cat_objects = []
                if hasattr(binding, 'Categories'):
                    for cat in binding.Categories:
                        categories.append(cat.Name)
                        cat_objects.append(cat)
                item = ParameterItem(
                    param_name, param_type, data_type, group_name, group_id,
                    binding_type,
                    ', '.join(categories) if categories else 'N/A',
                    cat_objects
                )
                self._all_param_items.append(item)
            self._all_param_items.sort(key=lambda x: x.name)
            self._all_groups = _get_all_parameter_groups()
            self._all_categories = _get_all_categories()
            self._apply_param_filters()
            self._set_status("Loaded {} parameters.".format(len(self._all_param_items)))
        except Exception as ex:
            self._set_status("Error loading parameters: {}".format(str(ex)))

    def _apply_param_filters(self):
        try:
            search_text = ""
            if self.txt_param_search and self.txt_param_search.Text:
                search_text = self.txt_param_search.Text.lower()

            type_idx = 0
            if self.cmb_param_type:
                type_idx = self.cmb_param_type.SelectedIndex

            binding_idx = 0
            if self.cmb_param_binding:
                binding_idx = self.cmb_param_binding.SelectedIndex

            filtered = []
            for item in self._all_param_items:
                if search_text and search_text not in item.name.lower():
                    continue
                if type_idx == 1 and not item.is_shared:
                    continue
                if type_idx == 2 and item.is_shared:
                    continue
                if binding_idx == 1 and item.binding != "Instance":
                    continue
                if binding_idx == 2 and item.binding != "Type":
                    continue
                filtered.append(item)

            self.dg_parameters.ItemsSource = to_items_source(filtered)
            self._set_status("Showing {} of {} parameters.".format(
                len(filtered), len(self._all_param_items)))
        except Exception as ex:
            self._set_status("Filter error: {}".format(str(ex)))

    def _get_selected_param(self):
        """Return the single selected ParameterItem or None."""
        try:
            sel = self.dg_parameters.SelectedItem
            return sel
        except:
            return None

    # ------------------------------------------------------------------
    # Browse Parameters event handlers
    # ------------------------------------------------------------------

    def _on_param_filter_changed(self, sender, e):
        self._apply_param_filters()

    def _on_param_refresh(self, sender, e):
        self._load_param_data()

    def _on_param_edit_group(self, sender, e):
        item = self._get_selected_param()
        if not item:
            MessageBox.Show("Please select a parameter first.", "Warning",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        dlg = EditGroupDialog(item, self._all_groups)
        dlg.ShowDialog()
        if dlg.result:
            self.dg_parameters.Items.Refresh()
            self._set_status("Group updated for '{}'.".format(item.name))

    def _on_param_edit_cats(self, sender, e):
        item = self._get_selected_param()
        if not item:
            MessageBox.Show("Please select a parameter first.", "Warning",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        dlg = EditCategoriesDialog(item, self._all_categories)
        dlg.ShowDialog()
        if dlg.result:
            self.dg_parameters.Items.Refresh()
            self._set_status("Categories updated for '{}'.".format(item.name))

    def _on_param_edit_binding(self, sender, e):
        item = self._get_selected_param()
        if not item:
            MessageBox.Show("Please select a parameter first.", "Warning",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        dlg = EditBindingDialog(item)
        dlg.ShowDialog()
        if dlg.result:
            self.dg_parameters.Items.Refresh()
            self._set_status("Binding updated for '{}'.".format(item.name))

    def _on_param_export(self, sender, e):
        save_path = forms.save_file(file_ext='csv', default_name='Revit_Parameters.csv')
        if not save_path:
            return
        try:
            with codecs.open(save_path, 'w', encoding='utf-8-sig') as f:
                f.write('Parameter Name,Type,Data Type,Group,Binding,Categories\n')
                for item in self._all_param_items:
                    name = '"{}"'.format(item.name) if ',' in item.name else item.name
                    cats = '"{}"'.format(item.categories) if ',' in item.categories else item.categories
                    f.write('{},{},{},{},{},{}\n'.format(
                        name, item.param_type, item.data_type,
                        item.group, item.binding, cats
                    ))
            self._set_status("Exported {} parameters to {}".format(
                len(self._all_param_items), save_path))
            MessageBox.Show("Exported {} parameters.".format(len(self._all_param_items)),
                            "Export Complete", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            MessageBox.Show("Export error:\n{}".format(str(ex)), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_param_delete(self, sender, e):
        item = self._get_selected_param()
        if not item:
            MessageBox.Show("Please select a parameter to delete.", "Warning",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        result = MessageBox.Show(
            "Delete parameter '{}'?\n\nAll parameter values will be lost!".format(item.name),
            "Confirm Delete",
            MessageBoxButton.YesNo,
            MessageBoxImage.Warning
        )
        if result != MessageBoxResult.Yes:
            return
        success, msg = _delete_parameter(item.name)
        if success:
            self._set_status("Deleted '{}'.".format(item.name))
            self._load_param_data()
        else:
            MessageBox.Show("Delete failed:\n\n{}".format(msg), "Error",
                            MessageBoxButton.OK, MessageBoxImage.Error)

    # ------------------------------------------------------------------
    # Transfer Parameters tab
    # ------------------------------------------------------------------

    def _init_transfer_tab(self):
        """Wire events and initialise state for the Transfer Parameters tab."""
        # Populate category dropdown
        self.cmb_transfer_category.Items.Clear()
        for name in sorted(CATEGORY_MAP.keys()):
            self.cmb_transfer_category.Items.Add(name)

        # Wire events
        self.cmb_transfer_category.SelectionChanged += self._on_transfer_category_changed
        self.txt_transfer_src_search.TextChanged += self._on_transfer_src_search
        self.txt_transfer_tgt_search.TextChanged += self._on_transfer_tgt_search
        self.btn_transfer_preview.Click += self._on_transfer_preview
        self.btn_transfer_run.Click += self._on_transfer_run
        if hasattr(self, "btn_transfer_ai_match"):
            self.btn_transfer_ai_match.Click += self._on_transfer_ai_match

        # Set up DataGrid columns for preview
        self._setup_transfer_preview_columns()

        # State
        self._transfer_elements = []
        self._transfer_source_items = []
        self._transfer_target_items = []
        self._selected_source = None
        self._selected_target = None

    def _setup_transfer_preview_columns(self):
        """Add columns to dg_transfer_preview via code-behind."""
        try:
            self.dg_transfer_preview.Columns.Clear()
            col_defs = [
                ("Element", "ElementName", 200),
                ("ID", "ElementId", 80),
                ("Source Value", "SourceValue", 180),
                ("Target Param", "TargetParam", 150),
                ("Status", "Status", 90),
            ]
            for header, binding_path, width in col_defs:
                col = DataGridTextColumn()
                col.Header = header
                col.Width = DataGridLength(width)
                col.IsReadOnly = True
                col.Binding = WPFBinding(binding_path)
                self.dg_transfer_preview.Columns.Add(col)
        except Exception as ex:
            pass

    def _on_transfer_category_changed(self, s, e):
        selected_name = self.cmb_transfer_category.SelectedItem
        if not selected_name:
            return
        bic = CATEGORY_MAP.get(str(selected_name))
        if bic is None:
            return
        self._transfer_elements = self._get_elements_by_category(bic)

        # Update element count label if present
        try:
            self.txt_transfer_count.Text = "{} elements".format(len(self._transfer_elements))
        except:
            pass

        self._refresh_transfer_params()
        self._selected_source = None
        self._selected_target = None
        self.btn_transfer_run.IsEnabled = False
        self.dg_transfer_preview.ItemsSource = None

    def _get_elements_by_category(self, bic):
        """Collect elements of given BuiltInCategory from the active view."""
        try:
            view = self._doc.ActiveView
            if view is None:
                return list(DB.FilteredElementCollector(self._doc)
                            .OfCategory(bic)
                            .WhereElementIsNotElementType()
                            .ToElements())
            return list(DB.FilteredElementCollector(self._doc, view.Id)
                        .OfCategory(bic)
                        .WhereElementIsNotElementType()
                        .ToElements())
        except Exception:
            return []

    def _refresh_transfer_params(self):
        """Rebuild both param panels from current element list."""
        if not self._transfer_elements:
            self.pnl_transfer_source.Children.Clear()
            self.pnl_transfer_target.Children.Clear()
            self._transfer_source_items = []
            self._transfer_target_items = []
            return
        param_names = self._get_instance_parameters(self._transfer_elements)
        src_filter = self.txt_transfer_src_search.Text if self.txt_transfer_src_search.Text else ""
        tgt_filter = self.txt_transfer_tgt_search.Text if self.txt_transfer_tgt_search.Text else ""
        self._transfer_source_items = self._populate_param_panel(
            self.pnl_transfer_source, param_names, src_filter,
            self._on_source_param_selected, 'source')
        self._transfer_target_items = self._populate_param_panel(
            self.pnl_transfer_target, param_names, tgt_filter,
            self._on_target_param_selected, 'target')

    def _get_instance_parameters(self, elements):
        """Scan up to 20 elements, return sorted unique writable param names."""
        names = set()
        for elem in elements[:20]:
            try:
                for p in elem.Parameters:
                    try:
                        if not p.IsReadOnly and p.Definition:
                            names.add(p.Definition.Name)
                    except Exception:
                        continue
            except Exception:
                continue
        return sorted(names)

    def _populate_param_panel(self, panel, param_names, filter_text, click_handler, role):
        """Create clickable param rows in panel. Returns list of (name, border) tuples."""
        panel.Children.Clear()
        ft = filter_text.lower() if filter_text else ""
        items = []
        for name in param_names:
            if ft and ft not in name.lower():
                continue
            border, txt = self._make_param_item(name, click_handler, role)
            panel.Children.Add(border)
            items.append((name, border, txt))
        return items

    def _make_param_item(self, name, click_handler, role):
        """Create a clickable border+textblock parameter row."""
        border = WPFBorder()
        border.Padding = Thickness(8, 5, 8, 5)
        border.Margin = Thickness(1)
        border.Background = _hex_brush("#FFFFFF")
        border.Cursor = System.Windows.Input.Cursors.Hand
        border.BorderThickness = Thickness(1)
        border.BorderBrush = _hex_brush("Transparent")

        txt = TextBlock()
        txt.Text = name
        txt.FontSize = 11.5
        txt.VerticalAlignment = VerticalAlignment.Center
        txt.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        txt.Foreground = _hex_brush("#18181B")
        border.Child = txt

        # Capture variables for closure
        item_data = [name, border, txt, False]  # [name, border, txt, selected]

        def on_click(s, e, _name=name, _border=border, _txt=txt, _data=item_data, _role=role):
            click_handler(_name, _border, _txt, _data, _role)

        border.MouseLeftButtonUp += on_click
        return border, txt

    def _on_source_param_selected(self, name, border, txt, data, role):
        """Deselect previous source, select this one."""
        # Deselect old
        for n, b, t in self._transfer_source_items:
            b.Background = _hex_brush("#FFFFFF")
            b.BorderBrush = _hex_brush("Transparent")
        self._selected_source = name
        border.Background = _hex_brush("#18181B")
        border.BorderBrush = _hex_brush("#3B82F6")
        txt.Foreground = _hex_brush("#FFFFFF")
        try:
            self.txt_transfer_src_info.Text = "Source: {}".format(name)
        except:
            pass
        self._check_enable_run()

    def _on_target_param_selected(self, name, border, txt, data, role):
        """Deselect previous target, select this one."""
        for n, b, t in self._transfer_target_items:
            b.Background = _hex_brush("#FFFFFF")
            b.BorderBrush = _hex_brush("Transparent")
            t.Foreground = _hex_brush("#18181B")
        self._selected_target = name
        border.Background = _hex_brush("#18181B")
        border.BorderBrush = _hex_brush("#3B82F6")
        txt.Foreground = _hex_brush("#FFFFFF")
        try:
            self.txt_transfer_tgt_info.Text = "Target: {}".format(name)
        except:
            pass
        self._check_enable_run()

    def _check_enable_run(self):
        if self._selected_source and self._selected_target:
            self.btn_transfer_run.IsEnabled = True
        else:
            self.btn_transfer_run.IsEnabled = False

    def _on_transfer_src_search(self, s, e):
        if not self._transfer_elements:
            return
        param_names = self._get_instance_parameters(self._transfer_elements)
        filter_text = self.txt_transfer_src_search.Text if self.txt_transfer_src_search.Text else ""
        self._transfer_source_items = self._populate_param_panel(
            self.pnl_transfer_source, param_names, filter_text,
            self._on_source_param_selected, 'source')
        self._selected_source = None
        self._check_enable_run()

    def _on_transfer_tgt_search(self, s, e):
        if not self._transfer_elements:
            return
        param_names = self._get_instance_parameters(self._transfer_elements)
        filter_text = self.txt_transfer_tgt_search.Text if self.txt_transfer_tgt_search.Text else ""
        self._transfer_target_items = self._populate_param_panel(
            self.pnl_transfer_target, param_names, filter_text,
            self._on_target_param_selected, 'target')
        self._selected_target = None
        self._check_enable_run()

    def _init_ai_mode(self):
        """Initialize AI Mode status pill and tool capabilities if AI is active."""
        try:
            if hasattr(self, "ai_mode_badge"):
                if self.is_ai_mode_active():
                    self.ai_mode_badge.Visibility = Visibility.Visible
                    info = self.get_ai_status_info()
                    provider = info.get("provider", "Ready")
                    model = info.get("model", "")
                    label = "AI: {}".format(provider)
                    if model:
                        label = "AI: {} ({})".format(provider, model)
                    if hasattr(self, "txt_ai_status"):
                        self.txt_ai_status.Text = label
                    if hasattr(self, "btn_transfer_ai_match"):
                        self.btn_transfer_ai_match.Visibility = Visibility.Visible
                else:
                    self.ai_mode_badge.Visibility = Visibility.Collapsed
                    if hasattr(self, "btn_transfer_ai_match"):
                        self.btn_transfer_ai_match.ToolTip = "Enable AI Mode in LLMs Setting for semantic parameter matching"
        except Exception:
            pass

    def _on_transfer_ai_match(self, sender, e):
        """Use AI to semantically match the selected source parameter to candidate target parameters."""
        if not self._selected_source:
            MessageBox.Show(
                "Please select a Source Parameter first to match.",
                "AI Parameter Match",
                MessageBoxButton.OK,
                MessageBoxImage.Information
            )
            return

        if not self._transfer_target_items:
            MessageBox.Show(
                "No target parameters available in the current category.",
                "AI Parameter Match",
                MessageBoxButton.OK,
                MessageBoxImage.Warning
            )
            return

        if not self.is_ai_mode_active():
            MessageBox.Show(
                "AI Mode is currently disabled or no LLM provider is configured.\n"
                "Please enable AI Mode in LLMs Setting.",
                "AI Mode Inactive",
                MessageBoxButton.OK,
                MessageBoxImage.Information
            )
            return

        src_name = self._selected_source
        tgt_candidates = [item[0] for item in self._transfer_target_items if item[0] != src_name]
        if not tgt_candidates:
            return

        # Visual feedback
        btn = getattr(self, 'btn_transfer_ai_match', None)
        orig_content = btn.Content if btn else "✨ AI Match"

        def _restore_btn():
            if btn:
                btn.Content = orig_content
                btn.IsEnabled = True

        if btn:
            btn.Content = "⏳ Matching..."
            btn.IsEnabled = False

        self.txt_transfer_tgt_info.Text = "AI matching target parameter for '{}'...".format(src_name)
        if hasattr(self, "txt_param_status_bar"):
            self.txt_param_status_bar.Text = "AI reasoning: matching '{}' to candidates...".format(src_name)

        system_prompt = (
            "You are an expert BIM Manager and Autodesk Revit data specialist. "
            "Given a source Revit parameter name and a list of target parameter candidates, "
            "identify the single best semantic target match. "
            "Return JSON: {\"target_param\": \"<name>\", \"confidence\": 0.0-1.0, \"reasoning\": \"<brief 1-sentence reason>\"}."
        )
        prompt = (
            "Source parameter: \"{}\"\n"
            "Target candidates:\n{}\n\n"
            "Select the best target candidate that matches the semantic intent of the source parameter."
        ).format(src_name, "\n".join("- " + c for c in tgt_candidates[:60]))

        def _worker():
            return self.ai_bridge.ask_json(prompt, system_prompt=system_prompt)

        def _on_success(result):
            try:
                if not result or not isinstance(result, dict) or "target_param" not in result:
                    self.txt_transfer_tgt_info.Text = "AI could not find a confident match."
                    return

                best_param = result.get("target_param")
                confidence = result.get("confidence", 0.0)
                reasoning = result.get("reasoning", "")

                # Find matching item in target list
                found = False
                for name, border, txt in self._transfer_target_items:
                    if name.lower() == str(best_param).lower():
                        # Trigger selection handler
                        self._on_target_param_selected(name, border, txt, [name, border, txt, True], 'target')
                        found = True
                        break

                if found:
                    try:
                        conf_pct = float(confidence) * 100
                    except:
                        conf_pct = 90.0
                    self.txt_transfer_tgt_info.Text = "Target: {} (AI: {:.0f}% - {})".format(
                        best_param, conf_pct, reasoning
                    )
                    if hasattr(self, "txt_param_status_bar"):
                        self.txt_param_status_bar.Text = "AI matched '{}' -> '{}' ({:.0f}%)".format(
                            src_name, best_param, conf_pct
                        )
                else:
                    self.txt_transfer_tgt_info.Text = "AI suggested '{}', but it is not in the active list.".format(best_param)
            finally:
                _restore_btn()

        def _on_error(err):
            _restore_btn()
            self.txt_transfer_tgt_info.Text = "AI match error: {}".format(err)

        self.run_ai_async(_worker, on_success=_on_success, on_error=_on_error)

    def _on_transfer_preview(self, s, e):
        if not self._selected_source or not self._selected_target:
            MessageBox.Show("Please select both Source and Target parameters.",
                            "Missing Selection",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        if self._selected_source == self._selected_target:
            MessageBox.Show("Source and Target must be different parameters.",
                            "Same Parameter",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        src_name = self._selected_source
        tgt_name = self._selected_target
        rows = []
        for elem in self._transfer_elements:
            try:
                elem_name = self._get_element_display_name(elem)
                elem_id_str = str(_eid_int(elem.Id))
                src_val = self._get_param_value_as_string(elem, src_name)
                src_p = elem.LookupParameter(src_name)
                tgt_p = elem.LookupParameter(tgt_name)
                if src_p is None:
                    status = "No source param"
                elif tgt_p is None:
                    status = "No target param"
                elif tgt_p.IsReadOnly:
                    status = "Read-only"
                elif not src_p.HasValue:
                    status = "Empty source"
                else:
                    status = "Ready"
                rows.append(TransferPreviewRow(elem_name, elem_id_str, src_val, tgt_name, status))
            except Exception:
                continue

        self.dg_transfer_preview.ItemsSource = to_items_source(rows)
        try:
            self.txt_transfer_preview_status.Text = "{} element(s) previewed".format(len(rows))
        except:
            pass
        self.txt_transfer_status.Text = "{} element(s) ready for transfer.".format(
            sum(1 for r in rows if r.Status == "Ready"))

    def _get_element_display_name(self, elem):
        """Get a readable name for an element."""
        try:
            type_id = elem.GetTypeId()
            if type_id and _eid_int(type_id) != -1:
                elem_type = self._doc.GetElement(type_id)
                if elem_type:
                    fam_name = ""
                    try:
                        fam_name = elem_type.FamilyName
                    except Exception:
                        pass
                    type_name = getattr(elem_type, 'Name', '')
                    if fam_name and type_name:
                        return "{}: {}".format(fam_name, type_name)
                    elif type_name:
                        return type_name
            name_param = elem.LookupParameter("Name")
            if name_param and name_param.HasValue:
                return name_param.AsString()
            return "ID: {}".format(_eid_int(elem.Id))
        except Exception:
            return "ID: {}".format(_eid_int(elem.Id))

    def _get_param_value_as_string(self, elem, param_name):
        """Get parameter value as display string."""
        try:
            param = elem.LookupParameter(param_name)
            if param is None:
                return "<not found>"
            if not param.HasValue:
                return "<empty>"
            st = param.StorageType
            if st == DB.StorageType.String:
                v = param.AsString()
                return v if v else "<empty>"
            elif st == DB.StorageType.Integer:
                return str(param.AsInteger())
            elif st == DB.StorageType.Double:
                try:
                    return param.AsValueString() or str(param.AsDouble())
                except Exception:
                    return str(param.AsDouble())
            elif st == DB.StorageType.ElementId:
                eid = param.AsElementId()
                if eid and _eid_int(eid) != -1:
                    ref_elem = self._doc.GetElement(eid)
                    if ref_elem and hasattr(ref_elem, 'Name'):
                        return ref_elem.Name
                    return str(_eid_int(eid))
                return "<None>"
            return "<unknown>"
        except Exception:
            return "<error>"

    def _transfer_value(self, elem, src_name, tgt_name):
        """Transfer value from source to target on one element.
        Returns True on success, False on failure.
        """
        try:
            src_param = elem.LookupParameter(src_name)
            tgt_param = elem.LookupParameter(tgt_name)
            if src_param is None or tgt_param is None:
                return False
            if tgt_param.IsReadOnly:
                return False
            if not src_param.HasValue:
                return False

            src_st = src_param.StorageType
            tgt_st = tgt_param.StorageType

            if src_st == tgt_st:
                if src_st == DB.StorageType.String:
                    tgt_param.Set(src_param.AsString() or "")
                elif src_st == DB.StorageType.Integer:
                    tgt_param.Set(src_param.AsInteger())
                elif src_st == DB.StorageType.Double:
                    tgt_param.Set(src_param.AsDouble())
                elif src_st == DB.StorageType.ElementId:
                    tgt_param.Set(src_param.AsElementId())
                return True

            # Cross-type: convert to string first, then parse
            if tgt_st == DB.StorageType.String:
                val = self._get_param_value_as_string(elem, src_name)
                if val.startswith("<"):
                    val = ""
                tgt_param.Set(val)
                return True

            src_str = self._get_param_value_as_string(elem, src_name)
            if src_str.startswith("<"):
                return False

            if tgt_st == DB.StorageType.Integer:
                try:
                    tgt_param.Set(int(float(src_str)))
                    return True
                except Exception:
                    return False
            elif tgt_st == DB.StorageType.Double:
                try:
                    tgt_param.Set(float(src_str))
                    return True
                except Exception:
                    return False
            elif tgt_st == DB.StorageType.ElementId:
                try:
                    eid = make_eid(int(float(src_str)))
                    tgt_param.Set(eid)
                    return True
                except Exception:
                    return False

            return False
        except Exception:
            return False

    def _on_transfer_run(self, s, e):
        if not self._selected_source or not self._selected_target:
            MessageBox.Show("Please select both Source and Target parameters.",
                            "Missing Selection",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        if self._selected_source == self._selected_target:
            MessageBox.Show("Source and Target must be different parameters.",
                            "Same Parameter",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        confirm = MessageBox.Show(
            "Transfer values:\n  Source: {}\n  Target: {}\n  Elements: {}\n\nProceed?".format(
                self._selected_source, self._selected_target, len(self._transfer_elements)),
            "Confirm Transfer",
            MessageBoxButton.YesNo, MessageBoxImage.Question)
        if confirm != MessageBoxResult.Yes:
            return

        t = DB.Transaction(self._doc, "T3Lab: Transfer Parameter Values")
        t.Start()
        success_count = 0
        fail_count = 0
        try:
            for elem in self._transfer_elements:
                if self._transfer_value(elem, self._selected_source, self._selected_target):
                    success_count += 1
                else:
                    fail_count += 1
            t.Commit()
        except Exception as ex:
            try:
                t.RollBack()
            except:
                pass
            self.txt_transfer_status.Text = "Error: {}".format(str(ex))
            return
        self.txt_transfer_status.Text = "Transferred {} element(s). {} failed.".format(
            success_count, fail_count)
        # Refresh preview after run
        self._on_transfer_preview(None, None)

    # ------------------------------------------------------------------
    # Parameter Loader tab
    # ------------------------------------------------------------------

    def _init_loader_tab(self):
        """Wire events and initialise state for the Parameter Loader tab."""
        self.btn_loader_import_xml.Click += self._on_loader_import_xml
        self.btn_loader_import_excel.Click += self._on_loader_import_excel
        self.btn_loader_add.Click += self._on_loader_add_params

        # Wire selection buttons if present in XAML
        try:
            self.btn_loader_select_new.Click += self._on_loader_select_new
        except:
            pass
        try:
            self.btn_loader_select_all.Click += self._on_loader_select_all
        except:
            pass
        try:
            self.btn_loader_select_none.Click += self._on_loader_select_none
        except:
            pass

        # Set up DataGrid columns via code-behind
        self._setup_loader_grid_columns()

        # State
        self._loader_requirements = []

    def _setup_loader_grid_columns(self):
        """DataGrid columns are declared in XAML; no code-behind setup needed.
        This method is kept as a hook for future customisation."""
        pass

    def _on_loader_import_xml(self, s, e):
        from System.Windows.Forms import OpenFileDialog, DialogResult
        dlg = OpenFileDialog()
        dlg.Filter = "XML Files (*.xml)|*.xml|All Files (*.*)|*.*"
        dlg.Title = "Import Autodesk Model Checker XML"
        if dlg.ShowDialog() != DialogResult.OK:
            return
        try:
            self._loader_requirements = RequirementParser.from_xml(dlg.FileName)
            self.txt_loader_filepath.Text = dlg.FileName
            self._refresh_loader_grid()
            has_reqs = len(self._loader_requirements) > 0
            self.btn_loader_add.IsEnabled = has_reqs
            self.txt_loader_stats.Text = "{} parameter(s) loaded".format(
                len(self._loader_requirements))
            self.txt_loader_status.Text = "Loaded {} requirements from XML.".format(
                len(self._loader_requirements))
        except Exception as ex:
            self.txt_loader_status.Text = "Error loading XML: {}".format(str(ex))

    def _on_loader_import_excel(self, s, e):
        from System.Windows.Forms import OpenFileDialog, DialogResult
        dlg = OpenFileDialog()
        dlg.Filter = "Excel (*.xlsx)|*.xlsx|All Excel (*.xlsx;*.xls)|*.xlsx;*.xls"
        dlg.Title = "Import Excel Parameter Mapping"
        if dlg.ShowDialog() != DialogResult.OK:
            return
        try:
            mapper = ExcelColumnMapper(dlg.FileName)
            mapper.ShowDialog()
            if not mapper.column_map:
                return
            self._loader_requirements = RequirementParser.from_excel(
                dlg.FileName, mapper.column_map)
            self.txt_loader_filepath.Text = dlg.FileName
            self._refresh_loader_grid()
            has_reqs = len(self._loader_requirements) > 0
            self.btn_loader_add.IsEnabled = has_reqs
            self.txt_loader_stats.Text = "{} parameter(s) loaded".format(
                len(self._loader_requirements))
            self.txt_loader_status.Text = "Loaded {} requirements from Excel.".format(
                len(self._loader_requirements))
        except Exception as ex:
            self.txt_loader_status.Text = "Error: {}".format(str(ex))

    def _refresh_loader_grid(self):
        """Populate dg_loader_params with current requirements."""
        try:
            self.dg_loader_params.ItemsSource = to_items_source(
                [LoaderGridRow(r) for r in self._loader_requirements])
        except Exception as ex:
            self.txt_loader_status.Text = "Grid error: {}".format(str(ex))

    def _on_loader_select_new(self, s, e):
        for r in self._loader_requirements:
            r.selected = not r.already_exists
        self._refresh_loader_grid()

    def _on_loader_select_all(self, s, e):
        for r in self._loader_requirements:
            r.selected = True
        self._refresh_loader_grid()

    def _on_loader_select_none(self, s, e):
        for r in self._loader_requirements:
            r.selected = False
        self._refresh_loader_grid()

    def _on_loader_add_params(self, s, e):
        if not self._loader_requirements:
            return

        selected = [r for r in self._loader_requirements if r.selected]
        if not selected:
            MessageBox.Show("No parameters selected.", "Info",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            return

        confirm = MessageBox.Show(
            "Add {} parameter(s) to the model?\n\nThis operation can be undone (Ctrl+Z).".format(
                len(selected)),
            "Confirm Add Parameters",
            MessageBoxButton.YesNo, MessageBoxImage.Question)
        if confirm != MessageBoxResult.Yes:
            return

        self.btn_loader_add.IsEnabled = False
        self.prg_loader.Visibility = self._vis('Visible')
        self.prg_loader.Maximum = len(selected)
        self.prg_loader.Value = 0

        try:
            adder = ParameterAdder(self._doc, revit.doc.Application)
            results = [None]

            def on_progress(current, total):
                self.prg_loader.Value = current

            t = DB.Transaction(self._doc, "T3Lab: Add Parameters to Project")
            t.Start()
            try:
                results[0] = adder.add_parameters(self._loader_requirements, on_progress)
                t.Commit()
            except Exception as tx_ex:
                try:
                    t.RollBack()
                except:
                    pass
                self.txt_loader_status.Text = "Transaction error: {}".format(str(tx_ex))
                return

            if results[0]:
                added = sum(1 for r in results[0] if r.get('status') == 'OK')
                failed = len(results[0]) - added
                self.txt_loader_status.Text = "Done: {} added, {} failed.".format(
                    added, failed)
            else:
                self.txt_loader_status.Text = "Done."

            self._refresh_loader_grid()

        except Exception as ex:
            self.txt_loader_status.Text = "Error: {}".format(str(ex))
        finally:
            self.btn_loader_add.IsEnabled = True
            self.prg_loader.Visibility = self._vis('Collapsed')

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_dg_loader_params_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua dg_loader_params."""
        self.toggle_all_rows(self.dg_loader_params, "is_selected", sender.IsChecked)


# ============================================================================
# PUBLIC API
# ============================================================================

def show_parameter_manager(script_dir, revit_obj):
    ManaParaWindow(script_dir, revit_obj).ShowDialog()
