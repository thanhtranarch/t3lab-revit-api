# -*- coding: utf-8 -*-
"""
Import smoke test for Clone Drawing: a module-level .NET / Revit import error
(the V1 crash: `from System.Windows import Orientation`) must fail HERE, not
on the user's machine.

* ``Snippets._drawing_clone`` is imported for real (pure at module level).
* ``GUI.CloneDrawingDialog`` is imported with ``clr``, ``System.*``,
  ``GUI.WPF_Base`` and ``GUI.T3Dialog`` stubbed the way dev/test_group_manager.py
  stubs Revit, and every WPF type it imports is checked against the real
  namespace table of dev/test_clr_import_namespaces.py.
* Every ``self.<x:Name>`` the dialog touches must exist in CloneDrawing.xaml.

Run: python dev/test_clone_drawing_import.py
"""
import ast
import os
import re
import sys
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
DEV_DIR = os.path.join(REPO, 'dev')
for path in (LIB_DIR, DEV_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

DIALOG = os.path.join(LIB_DIR, 'GUI', 'CloneDrawingDialog.py')
XAML = os.path.join(LIB_DIR, 'GUI', 'Tools', 'CloneDrawing.xaml')

# What each WPF namespace really exports (subset the dialog may use).
NAMESPACES = {
    'System.Windows': {'Clipboard', 'RoutedEventHandler', 'Visibility', 'WindowState',
                       'MessageBox', 'Thickness', 'HorizontalAlignment', 'VerticalAlignment'},
    'System.Windows.Controls': {'CheckBox', 'Orientation', 'StackPanel', 'TextBlock', 'TextBox',
                                'Button', 'ComboBox', 'ComboBoxItem', 'ListBox', 'DataGrid',
                                'Grid', 'Border', 'RadioButton', 'TabControl', 'TabItem'},
    'System.Windows.Media': {'Brushes', 'SolidColorBrush', 'Color', 'Colors', 'VisualTreeHelper'},
    'System.Windows.Input': {'Key', 'Keyboard', 'Cursors', 'ModifierKeys'},
}


class _Stub(types.ModuleType):
    def __getattr__(self, item):
        if item.startswith('__'):
            raise AttributeError(item)
        return type(item, (), {})


def _stub_module(name, attrs=None):
    module = _Stub(name)
    for key, value in (attrs or {}).items():
        setattr(module, key, value)
    return module


class _FakeWindow(object):
    """Stands in for T3WPFWindow: never touches WPF."""

    def __init__(self, xaml):
        self.xaml = xaml


def _wpf_module(name):
    module = types.ModuleType(name)
    allowed = NAMESPACES.get(name)

    def getattr_(item):
        if item.startswith('__'):
            raise AttributeError(item)
        if allowed is not None and item not in allowed:
            raise ImportError("cannot import name %r from %r (lives elsewhere)" % (item, name))
        return type(item, (), {})

    module.__getattr__ = getattr_
    return module


def load_dialog():
    saved = {}
    stubs = {
        'clr': _stub_module('clr', {'AddReference': lambda *_: None}),
        'System': _wpf_module('System'),
        'System.Windows': _wpf_module('System.Windows'),
        'System.Windows.Controls': _wpf_module('System.Windows.Controls'),
        'System.Windows.Media': _wpf_module('System.Windows.Media'),
        'System.Windows.Input': _wpf_module('System.Windows.Input'),
        'GUI.WPF_Base': _stub_module('GUI.WPF_Base', {'T3WPFWindow': _FakeWindow}),
        'GUI.T3Dialog': _stub_module('GUI.T3Dialog', {
            'confirm': lambda *a, **k: True, 'show_error': lambda *a, **k: None,
            'show_warning': lambda *a, **k: None, 'show_info': lambda *a, **k: None}),
    }
    for name in list(sys.modules):
        if name == 'GUI.CloneDrawingDialog':
            saved[name] = sys.modules.pop(name)
    for name, module in stubs.items():
        saved.setdefault(name, sys.modules.get(name))
        sys.modules[name] = module
    try:
        import importlib
        gui_pkg = types.ModuleType('GUI')
        gui_pkg.__path__ = [os.path.join(LIB_DIR, 'GUI')]
        saved.setdefault('GUI', sys.modules.get('GUI'))
        sys.modules['GUI'] = gui_pkg
        return importlib.import_module('GUI.CloneDrawingDialog')
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class CloneDrawingImportTests(unittest.TestCase):

    def test_snippet_module_imports_without_revit(self):
        from Snippets import _drawing_clone
        self.assertTrue(callable(_drawing_clone.run_clone))
        self.assertTrue(callable(_drawing_clone.plan_for_target))

    def test_dialog_module_imports_with_wpf_stubbed(self):
        module = load_dialog()
        self.assertTrue(hasattr(module, 'CloneDrawingDialog'))
        self.assertTrue(callable(module.show_clone_drawing))

    def test_wpf_imports_use_the_right_namespace(self):
        import test_clr_import_namespaces as gate
        self.assertEqual(gate.wrong_imports(DIALOG), [])

    def test_every_xaml_name_the_dialog_uses_exists(self):
        with open(XAML, encoding='utf-8') as fh:
            names = set(re.findall(r'x:Name="(\w+)"', fh.read()))
        with open(DIALOG, encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        used = set()
        methods = set(node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and node.value.id == 'self' and node.attr not in methods:
                used.add(node.attr)
        # names that look like controls: prefixes used in the XAML
        prefixes = ('cb_', 'tb_', 'chk_', 'rb_', 'btn_', 'grid_', 'lst_', 'txt_', 'chip_',
                    'dot_', 'status_', 'tab_', 'pb_', 'progress_', 'logo_')
        missing = sorted(n for n in used if n.startswith(prefixes) and n not in names)
        self.assertEqual(missing, [], "dialog uses x:Names missing in XAML: %s" % missing)

    def test_every_xaml_handler_exists_in_dialog_or_base(self):
        with open(XAML, encoding='utf-8') as fh:
            handlers = set(re.findall(
                r'(?<![A-Za-z])(?:Click|Checked|SelectionChanged|TextChanged)="(\w+)"', fh.read()))
        with open(DIALOG, encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        defined = set(node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
        base = {'minimize_button_clicked', 'maximize_button_clicked', 'stop_clicked',
                'close_button_clicked'}
        missing = sorted(h for h in handlers if h not in defined and h not in base)
        self.assertEqual(missing, [])

    def test_settings_combo_names_match_the_rows(self):
        from Snippets import _drawing_clone as dc
        with open(XAML, encoding='utf-8') as fh:
            xaml = fh.read()
        for field, _, choices, default, _ in dc.SETTINGS_ROWS:
            self.assertIn('x:Name="cb_set_%s"' % field, xaml, field)
            for choice in choices:
                self.assertIn('Tag="%s"' % choice, xaml, (field, choice))


if __name__ == '__main__':
    unittest.main()
