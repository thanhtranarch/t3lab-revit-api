"""Selection tally must describe the entire batch, including filtered rows."""
import ast
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


if __name__ == '__main__':
    unittest.main()
