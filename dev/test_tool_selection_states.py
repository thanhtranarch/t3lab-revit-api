"""Run shipped dialog state handlers with observable collection/control doubles."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


GUI = Path(__file__).resolve().parents[1] / 'T3Lab.extension' / 'lib' / 'GUI'
VISIBILITY = SimpleNamespace(Collapsed='collapsed', Visible='visible')


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self

    def fire(self):
        for handler in list(self.handlers):
            handler(None, None)


class Collection(list):
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, items=()):
        super().__init__(items)
        self.CollectionChanged = Event()

    def Add(self, item):
        self.append(item)
        self.CollectionChanged.fire()

    def Remove(self, item):
        self.remove(item)
        self.CollectionChanged.fire()

    def Clear(self):
        self.clear()
        self.CollectionChanged.fire()


class Window:
    def __init__(self, path):
        for name in ('empty_available', 'empty_selected', 'txt_preview',
                     'list_available', 'list_selected'):
            setattr(self, name, SimpleNamespace())
        self.MouseDown = Event()
        self.chk_include_project_params = SimpleNamespace(Checked=Event(), Unchecked=Event())
        self.chk_field_separator = SimpleNamespace(Checked=Event(), Unchecked=Event(), IsChecked=True)
        self.txt_field_separator = SimpleNamespace(TextChanged=Event(), Text='-')


def load_class(filename, class_name, extra_classes=()):
    path = GUI / filename
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name in (class_name,) + extra_classes]
    scope = {'T3WPFWindow': Window, 'Visibility': VISIBILITY,
             'ObservableCollection': Collection, 'Object': object,
             'os': os, '__file__': str(path), 'to_items_source': list, 'logger': Mock()}
    exec(compile(tree, str(path), 'exec'), scope)
    return scope[class_name]


ManaFami = load_class('ManaFamiDialog.py', 'ManaFamiWindow')
ParameterSelector = load_class('ParameterSelectorDialog.py', 'ParameterSelectorDialog', ('ParameterItem',))
AutoJoin = load_class('AutoJoinDialog.py', 'AutoJoinWindow', ('RuleItem',))


class FamilySelectionTests(unittest.TestCase):
    def dialog(self):
        dialog = ManaFami.__new__(ManaFami)
        dialog._is_updating = False
        dialog.all_families = [SimpleNamespace(Name=name, IsChecked=checked, PropertyChanged=Event())
                               for name, checked in (('Door', True), ('Window', False))]
        dialog.filtered_families = Collection(dialog.all_families)
        dialog.txt_result_count = SimpleNamespace()
        dialog.txt_selected_count = SimpleNamespace()
        dialog.btn_load = SimpleNamespace()
        return dialog

    def test_filter_preserves_selection_and_enables_load_for_hidden_choice(self):
        dialog = self.dialog()
        dialog.update_family_display([dialog.all_families[1]])
        self.assertTrue(dialog.all_families[0].IsChecked)
        self.assertTrue(dialog.btn_load.IsEnabled)
        self.assertEqual(dialog.txt_selected_count.Text, '1 families selected (1 hidden)')
        dialog.update_family_display()
        self.assertEqual(dialog.txt_selected_count.Text, '1 families selected')

    def test_clear_visible_does_not_clear_hidden_selection(self):
        dialog = self.dialog()
        dialog.all_families[1].IsChecked = True
        dialog.update_family_display([dialog.all_families[1]])
        dialog.select_none_loader_clicked(None, None)
        self.assertTrue(dialog.all_families[0].IsChecked)
        self.assertFalse(dialog.all_families[1].IsChecked)
        self.assertTrue(dialog.btn_load.IsEnabled)
        self.assertIn('(1 hidden)', dialog.txt_selected_count.Text)

    def test_zero_results_keeps_hidden_load_scope_but_empty_inventory_disables(self):
        dialog = self.dialog()
        dialog.update_family_display([])
        self.assertTrue(dialog.btn_load.IsEnabled)
        self.assertIn('1 families selected', dialog.txt_selected_count.Text)
        dialog.all_families = []
        dialog.update_family_display()
        self.assertFalse(dialog.btn_load.IsEnabled)


class ParameterEmptyTests(unittest.TestCase):
    def dialog(self, restore=False):
        dialog = ParameterSelector.__new__(ParameterSelector)
        item = SimpleNamespace(Name='SheetNumber')
        dialog.load_parameters = lambda: dialog.available_params.Add(item)

        def restore_cache():
            dialog.available_params.Remove(item)
            dialog.selected_params.Add(item)

        dialog.load_cached_config = restore_cache if restore else lambda: None
        dialog.__init__(None)
        return dialog

    def test_constructor_load_and_cache_restore_update_overlays(self):
        dialog = self.dialog(restore=True)
        self.assertEqual(dialog.empty_available.Visibility, 'visible')
        self.assertEqual(dialog.empty_selected.Visibility, 'collapsed')
        self.assertEqual(dialog.txt_preview.Text, 'Preview: {SheetNumber}')

    def test_transfer_and_remove_toggle_both_overlays(self):
        dialog = self.dialog()
        self.assertEqual(dialog.empty_available.Visibility, 'collapsed')
        self.assertEqual(dialog.empty_selected.Visibility, 'visible')
        item = dialog.available_params[0]
        dialog.list_available.SelectedItems = [item]
        dialog._on_add_parameter(None, None)
        self.assertEqual(dialog.empty_available.Visibility, 'visible')
        self.assertEqual(dialog.empty_selected.Visibility, 'collapsed')
        dialog.list_selected.SelectedItems = [item]
        dialog._on_remove_parameter(None, None)
        self.assertEqual(dialog.empty_available.Visibility, 'collapsed')
        self.assertEqual(dialog.empty_selected.Visibility, 'visible')

    def test_clear_and_reload_are_observed_without_preview_call(self):
        dialog = self.dialog()
        dialog.available_params.Clear()
        self.assertEqual(dialog.empty_available.Visibility, 'visible')
        dialog.load_parameters()
        self.assertEqual(dialog.empty_available.Visibility, 'collapsed')


class JoinEmptyTests(unittest.TestCase):
    def test_add_and_remove_last_rule_toggle_empty_overlay(self):
        dialog = AutoJoin.__new__(AutoJoin)
        dialog.rules_grid = SimpleNamespace()
        dialog.rules_grid_empty = SimpleNamespace()
        dialog.rule_count_text = SimpleNamespace()
        dialog._rules = []
        dialog._refresh_rules()
        self.assertEqual(dialog.rules_grid_empty.Visibility, 'visible')
        dialog._rules = [{'priority': 'Walls', 'join_with': 'Floors'}]
        dialog._refresh_rules()
        self.assertEqual(dialog.rules_grid_empty.Visibility, 'collapsed')
        self.assertEqual(len(dialog.rules_grid.ItemsSource), 1)
        dialog.rules_grid.SelectedItem = dialog.rules_grid.ItemsSource[0]
        dialog.btn_remove_rule_click(None, None)
        self.assertEqual(dialog.rules_grid_empty.Visibility, 'visible')
        self.assertEqual(dialog.rule_count_text.Text, '0 rule(s) defined')


if __name__ == '__main__':
    unittest.main()
