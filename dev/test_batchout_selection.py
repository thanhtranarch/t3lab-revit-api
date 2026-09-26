"""Selection tally must describe the entire batch, including filtered rows,
and the row checkboxes must show that selection."""
import ast
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class SelectionTallyTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI/BatchOutDialog.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'ExportManagerWindow')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'update_selection_count')
        scope = {'logger': Mock()}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), scope)
        self.update = scope['update_selection_count']

    def test_hidden_selection_is_reported_for_sheets_and_views(self):
        for mode in ('sheets', 'views'):
            with self.subTest(mode=mode):
                rows = [SimpleNamespace(IsSelected=True), SimpleNamespace(IsSelected=True)]
                window = SimpleNamespace(selection_mode=mode,
                                         selection_count_text=SimpleNamespace(Text=''),
                                         header_checkbox=SimpleNamespace(IsChecked=False))
                setattr(window, 'all_' + mode, rows)
                setattr(window, 'filtered_' + mode, rows[:1])
                self.update(window)
                self.assertIn('2 {} selected'.format(mode), window.selection_count_text.Text)
                self.assertIn('1 selected hidden', window.selection_count_text.Text)
                self.assertTrue(window.header_checkbox.IsChecked)

    def test_empty_filter_does_not_hide_selected_batch_count(self):
        window = SimpleNamespace(selection_mode='sheets',
                                 all_sheets=[SimpleNamespace(IsSelected=True)], filtered_sheets=[],
                                 selection_count_text=SimpleNamespace(Text=''),
                                 header_checkbox=SimpleNamespace(IsChecked=True))
        self.update(window)
        self.assertIn('1 sheets selected | 0 shown', window.selection_count_text.Text)
        self.assertFalse(window.header_checkbox.IsChecked)


class RowCheckboxShowsModelTests(unittest.TestCase):
    """pythonnet hands WPF a PyObject for a Python attribute: WPF converts it
    to string but not to bool?, so IsChecked bound straight to IsSelected read
    unchecked and every tick vanished on Items.Refresh() (2026-09-26)."""

    X = '{http://schemas.microsoft.com/winfx/2006/xaml}'
    P = '{http://schemas.microsoft.com/winfx/2006/xaml/presentation}'

    def setUp(self):
        xaml = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI/Tools/ExportManager.xaml'
        root = ET.parse(xaml).getroot()
        listview = next(n for n in root.iter() if n.get(self.X + 'Name') == 'sheets_listview')
        self.row_boxes = [n for n in listview.iter(self.P + 'CheckBox')
                          if n.get('Click') == 'row_checkbox_clicked']
        self.bridges = {n.get(self.X + 'Name'): n for n in listview.iter(self.P + 'TextBlock')
                        if n.get(self.X + 'Name')}

    def test_row_checkbox_reads_selection_through_a_string(self):
        self.assertEqual(len(self.row_boxes), 1)
        binding = self.row_boxes[0].get('IsChecked')
        self.assertNotIn('Binding IsSelected', binding)
        self.assertIn('ElementName=row_selected_text', binding)
        self.assertIn('Mode=OneWay', binding)
        self.assertEqual(self.bridges['row_selected_text'].get('Text'), '{Binding IsSelected}')
        self.assertEqual(self.bridges['row_selected_text'].get('Visibility'), 'Collapsed')

    def test_click_handler_writes_the_model(self):
        source = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI/BatchOutDialog.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'ExportManagerWindow')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'row_checkbox_clicked')
        scope = {'logger': Mock()}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), scope)
        row = SimpleNamespace(IsSelected=False)
        window = SimpleNamespace(sheets_listview=SimpleNamespace(Items=Mock()),
                                 update_selection_count=Mock(),
                                 update_export_preview_if_needed=Mock())
        scope['row_checkbox_clicked'](window, SimpleNamespace(DataContext=row, IsChecked=True), None)
        self.assertTrue(row.IsSelected)
        window.sheets_listview.Items.Refresh.assert_called_once()
        window.update_selection_count.assert_called_once()


if __name__ == '__main__':
    unittest.main()
