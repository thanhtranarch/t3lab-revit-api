# -*- coding: utf-8 -*-
"""The ribbon tab folder is found, never named (2026-09-26).

Renaming T3Lab.tab -> T3Lab_Dev.tab broke BG Theme settings, ImageToDrafting's
potrace, FamiGen prompts, ManaLoca sessions and the Assistant's tool list, and
left audit_tools / audit_wiring / audit_icons walking a missing folder while
still printing GREEN.

Run: python dev/test_extension_paths.py
"""
import ast
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXT = os.path.join(REPO, 'T3Lab.extension')
sys.path.insert(0, os.path.join(EXT, 'lib'))

from core import extension_paths  # noqa: E402
import tabdir  # noqa: E402

# The resolver itself keeps 'T3Lab.tab' as its last-resort default.
ALLOWED = {os.path.join(EXT, 'lib', 'core', 'extension_paths.py')}


def _hardcoded(path):
    """String literals (not docstrings) that name the tab folder."""
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read(), path)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, 'body', [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings and '.tab' in n.value
            and ('T3Lab.tab' in n.value or n.value.endswith('_Dev.tab'))]


class TabDir(unittest.TestCase):
    def test_finds_the_real_tab_folder(self):
        tab = extension_paths.tab_dir()
        self.assertTrue(os.path.isdir(tab), tab)
        self.assertTrue(tab.endswith('.tab'))
        self.assertEqual(os.path.dirname(tab), EXT)
        self.assertEqual(tabdir.TAB, tab)

    def test_follows_a_rename(self):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, 'lib'))
            os.mkdir(os.path.join(root, 'Whatever.tab'))
            self.assertEqual(extension_paths.tab_dir(root), os.path.join(root, 'Whatever.tab'))
            os.mkdir(os.path.join(root, 'T3Lab_Next.tab'))
            self.assertEqual(extension_paths.tab_dir(root), os.path.join(root, 'T3Lab_Next.tab'))

    def test_every_path_the_runtime_needs_exists(self):
        for parts in (('Support.panel', 'UI.stack', 'BG Theme.pushbutton'),
                      ('Modeling & Datum.panel', 'FamiGen.pushbutton', 'prompts'),
                      ('Standards & Settings.panel', 'ManaLoca.pushbutton'),
                      ('Modeling & Datum.panel', 'Create.stack', 'Create Elements.pulldown',
                       'ImageToDrafting.pushbutton'),
                      ('Support.panel', 'T3LabAssistant.pushbutton', 'script.py')):
            self.assertTrue(os.path.exists(extension_paths.tab_path(*parts)), parts)


class NoHardcodedTabName(unittest.TestCase):
    def test_lib_and_dev_tooling(self):
        roots = [os.path.join(EXT, 'lib'), HERE]
        bad = []
        for root in roots:
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d != '__pycache__']
                for f in files:
                    path = os.path.join(dirpath, f)
                    if not f.endswith('.py') or path in ALLOWED or path == os.path.abspath(__file__):
                        continue
                    bad += ['%s:%d' % (os.path.relpath(path, REPO), ln) for ln in _hardcoded(path)]
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
