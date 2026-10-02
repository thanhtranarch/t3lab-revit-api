# -*- coding: utf-8 -*-
"""Tab Manager Dialog class."""

import os
from pyrevit import forms
from GUI.WPF_Base import T3WPFWindow
from System.Windows import WindowState, Visibility
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System import Object

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'ManaTabs.xaml')

class TabItemModel(object):
    def __init__(self, name, is_checked=False):
        self._name = str(name)
        self._is_checked = bool(is_checked)
        self._property_changed_handlers = []

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        val = str(value)
        if self._name != val:
            self._name = val
            self.OnPropertyChanged("Name")

    @property
    def IsChecked(self):
        return self._is_checked

    @IsChecked.setter
    def IsChecked(self, value):
        val = bool(value)
        if self._is_checked != val:
            self._is_checked = val
            self.OnPropertyChanged("IsChecked")

    @property
    def item(self):
        return self._name

    @property
    def state(self):
        return self._is_checked

    def add_PropertyChanged(self, handler):
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, property_name):
        if self._property_changed_handlers:
            args = PropertyChangedEventArgs(property_name)
            for handler in list(self._property_changed_handlers):
                try:
                    handler(self, args)
                except Exception:
                    pass

class TabManagerWindow(T3WPFWindow):
    def __init__(self, current_lst):
        # WPFWindow.__init__ loads XAML
        T3WPFWindow.__init__(self, _XAML)
        
        self.all_items = []
        for it in (current_lst or []):
            if isinstance(it, TabItemModel):
                self.all_items.append(it)
            elif isinstance(it, (tuple, list)):
                self.all_items.append(TabItemModel(it[0], it[1] if len(it) > 1 else False))
            elif hasattr(it, 'item') and hasattr(it, 'state'):
                self.all_items.append(TabItemModel(it.item, it.state))
            elif hasattr(it, 'Name') and hasattr(it, 'IsChecked'):
                self.all_items.append(TabItemModel(it.Name, it.IsChecked))
            else:
                self.all_items.append(TabItemModel(str(it), False))

        self.filtered_items = ObservableCollection[Object]()
        
        self.BtnApply.Click += self._on_apply
        
        self.applied = False
        self.selected_names = []

        # Load initial items
        self._filter_list("")

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

    def SearchBox_TextChanged(self, sender, e):
        search_text = self.SearchBox.Text.strip().lower()
        self._filter_list(search_text)

    def _filter_list(self, search_text):
        self.filtered_items.Clear()
        for item in self.all_items:
            if not search_text or search_text in item.Name.lower():
                self.filtered_items.Add(item)
        self.TabListBox.ItemsSource = self.filtered_items
        if hasattr(self, 'TabListBox_empty') and self.TabListBox_empty is not None:
            self.TabListBox_empty.Visibility = (
                Visibility.Collapsed if self.filtered_items.Count > 0 else Visibility.Visible
            )

    def _on_apply(self, sender, e):
        # Collect checked items
        self.selected_names = [item.Name for item in self.all_items if item.IsChecked]
        self.applied = True
        self.Close()

def show_tab_manager_dialog(current_lst):
    """Show the Tab Manager dialog."""
    dlg = TabManagerWindow(current_lst)
    dlg.ShowDialog()
    if dlg.applied:
        return dlg.selected_names
    return None
