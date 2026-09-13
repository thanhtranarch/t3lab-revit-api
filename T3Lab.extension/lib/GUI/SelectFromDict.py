# -*- coding: utf-8 -*-
"""
Select From Dict Dialog
GUI dialog for selecting items from a dictionary.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "Select From Dict Dialog"

import os

from pyrevit import forms

from GUI.WPF_Base import my_WPF, to_items_source

import clr
clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")

from System import Uri, UriKind
from System.Collections.Generic import List
from System.Windows import Visibility, WindowState, RoutedEventHandler
from System.Windows.Controls import CheckBox
from System.Windows.Media.Imaging import BitmapImage
try:
    import wpf
except Exception:
    wpf = None

PATH_SCRIPT = os.path.dirname(__file__)


class ListItem:
    def __init__(self, Name='Unnamed', element=None, checked=False):
        self.Name      = Name
        self.IsChecked = checked
        self.element   = element


class SelectFromDict(my_WPF):
    def __init__(self, items,
                 title='__title',
                 label="Select Elements:",
                 button_name='Select',
                 version='version= 1.0',
                 SelectMultiple=True):
        self.SelectMultiple = SelectMultiple
        self.given_dict_items = {k: v for k, v in items.items() if k}
        self.items = self.generate_list_items()
        self.selected_items = []
        self._button_name = button_name

        path_xaml_file = os.path.join(PATH_SCRIPT, 'Tools', 'SelectFromDict.xaml')
        my_WPF.__init__(self, path_xaml_file)

        self.main_title.Text     = title
        self.text_label.Text     = label
        self.button_main.Content = button_name
        self.footer_version.Text = version

        if not SelectMultiple:
            self.UI_Buttons_all_none.Visibility = Visibility.Collapsed

        # Template controls have their own namescope. Listen on the list so
        # checkbox clicks reach Python under CPython's XamlReader loader.
        self._item_click_handler = RoutedEventHandler(self.UIe_ItemChecked)
        self.main_ListBox.AddHandler(CheckBox.ClickEvent, self._item_click_handler, True)
        self.text_filter_updated(None, None)
        self.ShowDialog()

    def generate_list_items(self):
        return [ListItem(Name=name, element=element)
                for name, element in self.given_dict_items.items()]

    def button_select_all(self, sender, e):
        for item in self.main_ListBox.Items:
            item.IsChecked = True
        self.main_ListBox.Items.Refresh()
        self._update_selection_state()

    def button_select_none(self, sender, e):
        for item in self.main_ListBox.Items:
            item.IsChecked = False
        self.main_ListBox.Items.Refresh()
        self._update_selection_state()

    def UIe_ItemChecked(self, sender, e):
        checkbox = e.OriginalSource
        if not isinstance(checkbox, CheckBox):
            return
        checked_item = checkbox.DataContext
        if checked_item not in self.items:
            return
        checked_item.IsChecked = bool(checkbox.IsChecked)
        if not self.SelectMultiple and checked_item.IsChecked:
            for item in self.items:
                if item is not checked_item:
                    item.IsChecked = False
            self.main_ListBox.Items.Refresh()
        self._update_selection_state()

    def _update_selection_state(self):
        visible = list(self.main_ListBox.Items)
        selected = sum(1 for item in self.items if item.IsChecked)
        visible_selected = sum(1 for item in visible if item.IsChecked)
        hidden_selected = selected - visible_selected
        self.button_main.IsEnabled = selected > 0
        self.button_main.Content = "{} ({})".format(self._button_name, selected)
        self.selection_status.Text = "{} shown | {} selected".format(len(visible), selected)
        if hidden_selected:
            self.selection_status.Text += " ({} hidden by filter)".format(hidden_selected)
        self.main_ListBox_empty.Visibility = Visibility.Collapsed if visible else Visibility.Visible
        self.main_ListBox_empty.Text = (
            "No items match the filter.\nClear the search box to see all items."
            if self.items else
            "No items are available.\nClose this dialog and check the source selection."
        )

    def text_filter_updated(self, sender, e):
        filter_text = self.textbox_filter.Text.strip().lower() if self.textbox_filter.Text else ''
        if filter_text:
            self.main_ListBox.ItemsSource = to_items_source([
                item for item in self.items if filter_text in item.Name.lower()
            ])
        else:
            self.main_ListBox.ItemsSource = to_items_source(self.items)
        self._update_selection_state()

    def button_select(self, sender, e):
        self.selected_items = [item.element for item in self.items if item.IsChecked]
        if not self.selected_items:
            return
        self.Close()

def select_from_dict(elements_dict,
                     title='__title__',
                     label="Select Elements:",
                     button_name='Select',
                     version='Version: 1.0',
                     SelectMultiple=True):
    if isinstance(elements_dict, list):
        elements_dict = {i: i for i in elements_dict}

    GUI_select = SelectFromDict(
        items=elements_dict, title=title, label=label,
        button_name=button_name, version=version,
        SelectMultiple=SelectMultiple
    )
    return GUI_select.selected_items
