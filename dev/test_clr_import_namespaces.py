# -*- coding: utf-8 -*-
"""
Catches .NET types imported from the wrong namespace.

`from System.Windows import Orientation` parses fine, passes every static
gate and only fails inside Revit, the moment the dialog module is imported:

    cannot import name 'Orientation' from 'System.Windows' (unknown location)

Clone Drawing died on open exactly like this (2026-10-03, Revit 2025).
`Orientation` lives in System.Windows.Controls. No gate runs .NET, so this
test keeps a table of WPF types that are commonly imported from the wrong
parent namespace and scans every .py under the extension for them.

Run: python dev/test_clr_import_namespaces.py
"""
import ast
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(REPO, 'T3Lab.extension')
SKIP_DIRS = {'__pycache__', '_archive', 'scratch'}

# type -> the namespace it really lives in (WPF reference assemblies).
REAL_NAMESPACE = {
    # System.Windows.Controls
    'Orientation': 'System.Windows.Controls',
    'StackPanel': 'System.Windows.Controls',
    'TextBlock': 'System.Windows.Controls',
    'TextBox': 'System.Windows.Controls',
    'Button': 'System.Windows.Controls',
    'CheckBox': 'System.Windows.Controls',
    'ComboBox': 'System.Windows.Controls',
    'ComboBoxItem': 'System.Windows.Controls',
    'ListBox': 'System.Windows.Controls',
    'ListBoxItem': 'System.Windows.Controls',
    'DataGrid': 'System.Windows.Controls',
    'Grid': 'System.Windows.Controls',
    'Border': 'System.Windows.Controls',
    'Canvas': 'System.Windows.Controls',
    'Image': 'System.Windows.Controls',
    'ScrollViewer': 'System.Windows.Controls',
    'TabControl': 'System.Windows.Controls',
    'TabItem': 'System.Windows.Controls',
    'RadioButton': 'System.Windows.Controls',
    'ToolTip': 'System.Windows.Controls',
    'Dock': 'System.Windows.Controls',
    'SelectionMode': 'System.Windows.Controls',
    'ScrollBarVisibility': 'System.Windows.Controls',
    'DataGridLength': 'System.Windows.Controls',
    # System.Windows.Media
    'Brushes': 'System.Windows.Media',
    'SolidColorBrush': 'System.Windows.Media',
    'Color': 'System.Windows.Media',
    'Colors': 'System.Windows.Media',
    'VisualTreeHelper': 'System.Windows.Media',
    # System.Windows.Input
    'Key': 'System.Windows.Input',
    'Keyboard': 'System.Windows.Input',
    'Cursors': 'System.Windows.Input',
    'ModifierKeys': 'System.Windows.Input',
    # System.Windows.Threading
    'DispatcherPriority': 'System.Windows.Threading',
    'DispatcherFrame': 'System.Windows.Threading',
    # System.Windows.Shapes
    'Ellipse': 'System.Windows.Shapes',
    'Rectangle': 'System.Windows.Shapes',
    'Path': 'System.Windows.Shapes',
}

WATCHED_PARENTS = {'System.Windows', 'System.Windows.Controls',
                   'System.Windows.Media', 'System.Windows.Input'}


def py_files():
    for dirpath, dirnames, filenames in os.walk(EXT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith('.py'):
                yield os.path.join(dirpath, name)


def wrong_imports(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        src = fh.read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module not in WATCHED_PARENTS:
            continue
        for alias in node.names:
            real = REAL_NAMESPACE.get(alias.name)
            if real and real != node.module:
                out.append('%s:%d  from %s import %s  -> lives in %s' % (
                    os.path.relpath(path, REPO), node.lineno, node.module,
                    alias.name, real))
    return out


class ClrImportNamespaceTests(unittest.TestCase):

    def test_no_wpf_type_imported_from_wrong_namespace(self):
        problems = []
        for path in py_files():
            problems.extend(wrong_imports(path))
        self.assertEqual(problems, [], '\n' + '\n'.join(problems))

    def test_detector_flags_the_clone_drawing_bug(self):
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False,
                                         encoding='utf-8') as fh:
            fh.write('from System.Windows import Clipboard, Orientation\n')
            name = fh.name
        try:
            found = wrong_imports(name)
        finally:
            os.remove(name)
        self.assertEqual(len(found), 1)
        self.assertIn('System.Windows.Controls', found[0])


if __name__ == '__main__':
    unittest.main()
