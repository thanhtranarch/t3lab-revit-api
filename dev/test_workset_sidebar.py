"""Workset sidebar regression checks without Revit/WPF runtime."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[1]
GUI = REPO / 'T3Lab.extension/lib/GUI'


class ControlWrapper:
    """Distinct Python wrappers can expose the same CLR control identity."""
    def __init__(self, name):
        self.Name = name

    def __eq__(self, other):
        return isinstance(other, ControlWrapper) and self.Name == other.Name


class SidebarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse((GUI / 'ManaWorksetDialog.py').read_text(encoding='utf-8'))
        window = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'WorksetManagerWindow')
        handler = next(n for n in window.body if isinstance(n, ast.FunctionDef) and n.name == 'nav_toggle_clicked')
        env = {}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), 'ManaWorksetDialog.py', 'exec'), env)
        cls.handler = staticmethod(env['nav_toggle_clicked'])

    def setUp(self):
        self.window = SimpleNamespace(tab_control=SimpleNamespace(SelectedIndex=0))
        for name in ('nav_worksets', 'nav_bulk', 'nav_views'):
            setattr(self.window, name, ControlWrapper(name))

    def test_all_tabs_work_with_distinct_wrappers(self):
        for name, index in (('nav_views', 2), ('nav_bulk', 1), ('nav_worksets', 0)):
            sender = ControlWrapper(name)
            self.assertIsNot(sender, getattr(self.window, name))
            self.assertEqual(sender, getattr(self.window, name))
            self.handler(self.window, sender, None)
            self.assertEqual(self.window.tab_control.SelectedIndex, index)

    def test_unrelated_and_missing_senders_do_not_navigate(self):
        self.window.tab_control.SelectedIndex = 2
        for sender in (ControlWrapper('btn_refresh'), object(), None):
            self.handler(self.window, sender, None)
            self.assertEqual(self.window.tab_control.SelectedIndex, 2)

    def test_xaml_names_and_checked_events_match_handler(self):
        root = ET.parse(GUI / 'Tools/ManaWorkset.xaml').getroot()
        names = '{http://schemas.microsoft.com/winfx/2006/xaml}Name'
        controls = {node.get(names): node for node in root.iter() if node.get(names)}
        for name in ('nav_worksets', 'nav_bulk', 'nav_views'):
            self.assertEqual(controls[name].get('Checked'), 'nav_toggle_clicked')
        self.assertEqual(controls['nav_worksets'].get('IsChecked'), 'True')
        self.assertIn('tab_control', controls)


if __name__ == '__main__':
    unittest.main()
