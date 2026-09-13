"""Regression tests for selection scope and filename ordering without Revit.

Execute production dialog classes against lightweight control doubles. This
checks Python interaction semantics; actual WPF routed events need a host smoke
test after Reload pyRevit.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


GUI = Path(__file__).resolve().parents[1] / 'T3Lab.extension' / 'lib' / 'GUI'


class Items(list):
    def Refresh(self):
        pass

    def RemoveAt(self, index):
        if index < 0:
            raise IndexError('WPF collections reject negative indices')
        del self[index]

    def Insert(self, index, item):
        self.insert(index, item)

    def Add(self, item):
        self.append(item)


class ListControl:
    def __init__(self):
        self.Items = Items()
        self.handlers = []

    @property
    def ItemsSource(self):
        return self.Items

    @ItemsSource.setter
    def ItemsSource(self, items):
        self.Items = Items(items)

    def AddHandler(self, event, handler, handled_events_too):
        self.handlers.append((event, handler, handled_events_too))


class CheckBox:
    ClickEvent = object()

    def __init__(self, item, checked):
        self.DataContext = item
        self.IsChecked = checked


class Window:
    def __init__(self, path):
        self.closed = False
        for name in ('main_title', 'text_label', 'button_main', 'footer_version',
                     'UI_Buttons_all_none', 'selection_status', 'main_ListBox_empty'):
            setattr(self, name, SimpleNamespace())
        self.main_ListBox = ListControl()
        self.textbox_filter = SimpleNamespace(Text='')

    def ShowDialog(self):
        pass

    def Close(self):
        self.closed = True


def load_classes(filename, base_name):
    """Load actual class bodies without importing Revit/CLR dependencies."""
    source = ast.parse((GUI / filename).read_text(encoding='utf-8-sig'))
    module = ast.Module(body=[node for node in source.body if isinstance(node, ast.ClassDef)], type_ignores=[])
    namespace = {
        base_name: Window, 'os': __import__('os'), 'PATH_SCRIPT': str(GUI),
        'to_items_source': list, 'CheckBox': CheckBox,
        'RoutedEventHandler': lambda callback: callback,
        'Visibility': SimpleNamespace(Collapsed='collapsed', Visible='visible'),
    }
    exec(compile(module, str(GUI / filename), 'exec'), namespace)
    return namespace


SelectFromDict = load_classes('SelectFromDict.py', 'my_WPF')['SelectFromDict']
ParameterSelector = load_classes('ParameterSelectorDialog.py', 'T3WPFWindow')['ParameterSelectorDialog']


class SelectionTests(unittest.TestCase):
    def dialog(self, multiple=True, items=None):
        return SelectFromDict({'Alpha': 1, 'Beta': 2} if items is None else items,
                              SelectMultiple=multiple)

    def filter(self, dialog, text):
        dialog.textbox_filter.Text = text
        dialog.text_filter_updated(None, None)

    def click(self, dialog, item, checked=True):
        dialog.UIe_ItemChecked(dialog.main_ListBox,
                              SimpleNamespace(OriginalSource=CheckBox(item, checked)))

    def test_bulk_selection_only_changes_visible_items(self):
        dialog = self.dialog()
        self.filter(dialog, 'alpha')
        dialog.button_select_all(None, None)
        self.assertEqual([item.IsChecked for item in dialog.items], [True, False])
        self.filter(dialog, 'beta')
        dialog.button_select_none(None, None)
        self.assertTrue(dialog.items[0].IsChecked)
        self.assertIn('1 hidden by filter', dialog.selection_status.Text)

    def test_single_choice_clears_previous_hidden_choice(self):
        dialog = self.dialog(multiple=False)
        self.click(dialog, dialog.items[0])
        self.filter(dialog, 'beta')
        self.click(dialog, dialog.items[1])
        dialog.button_select(None, None)
        self.assertEqual(dialog.selected_items, [2])
        self.assertTrue(dialog.closed)

    def test_unchecking_disables_action_and_cannot_submit_empty(self):
        dialog = self.dialog()
        self.click(dialog, dialog.items[0])
        self.assertTrue(dialog.button_main.IsEnabled)
        self.click(dialog, dialog.items[0], False)
        self.assertFalse(dialog.button_main.IsEnabled)
        dialog.button_select(None, None)
        self.assertFalse(dialog.closed)

    def test_filtered_empty_state_offers_recovery(self):
        dialog = self.dialog()
        self.filter(dialog, 'missing')
        self.assertEqual(dialog.main_ListBox_empty.Visibility, 'visible')
        self.assertIn('Clear the search box', dialog.main_ListBox_empty.Text)
        self.filter(dialog, '')
        self.assertEqual(dialog.main_ListBox_empty.Visibility, 'collapsed')

    def test_empty_source_explains_missing_input(self):
        dialog = self.dialog(items={})
        self.assertFalse(dialog.button_main.IsEnabled)
        self.assertEqual(dialog.main_ListBox_empty.Visibility, 'visible')
        self.assertIn('check the source selection', dialog.main_ListBox_empty.Text)

    def test_routed_checkbox_handler_is_registered_on_list(self):
        dialog = self.dialog()
        event, callback, handled = dialog.main_ListBox.handlers[0]
        self.assertIs(event, CheckBox.ClickEvent)
        self.assertTrue(handled)
        callback(dialog.main_ListBox, SimpleNamespace(OriginalSource=CheckBox(dialog.items[0], True)))
        self.assertTrue(dialog.items[0].IsChecked)


class ParameterOrderingTests(unittest.TestCase):
    def dialog(self, index, items=('Number', 'Name', 'Revision')):
        dialog = ParameterSelector.__new__(ParameterSelector)
        dialog.selected_params = Items(items)
        dialog.list_selected = SimpleNamespace(SelectedIndex=index)
        dialog.update_preview = lambda *_: None
        return dialog

    def test_no_selection_does_not_reorder_or_raise(self):
        for handler in ('_on_move_down', '_on_move_bottom'):
            for items in ((), ('Name',), ('Number', 'Name')):
                with self.subTest(handler=handler, items=items):
                    dialog = self.dialog(-1, items)
                    getattr(dialog, handler)(None, None)
                    self.assertEqual(dialog.selected_params, list(items))

    def test_move_down_preserves_selection_and_order(self):
        dialog = self.dialog(0)
        dialog._on_move_down(None, None)
        self.assertEqual(dialog.selected_params, ['Name', 'Number', 'Revision'])
        self.assertEqual(dialog.list_selected.SelectedIndex, 1)

    def test_move_bottom_preserves_selection_and_order(self):
        dialog = self.dialog(0)
        dialog._on_move_bottom(None, None)
        self.assertEqual(dialog.selected_params, ['Name', 'Revision', 'Number'])
        self.assertEqual(dialog.list_selected.SelectedIndex, 2)

    def test_last_item_stays_in_place(self):
        dialog = self.dialog(2)
        dialog._on_move_down(None, None)
        dialog._on_move_bottom(None, None)
        self.assertEqual(dialog.selected_params, ['Number', 'Name', 'Revision'])


if __name__ == '__main__':
    unittest.main()
