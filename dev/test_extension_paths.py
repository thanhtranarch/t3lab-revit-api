# -*- coding: utf-8 -*-
"""Ribbon folders are found, never named (2026-09-26, 2026-10-03).

Renaming T3Lab.tab -> T3Lab_Dev.tab broke BG Theme settings, ImageToDrafting's
potrace, FamiGen prompts, ManaLoca sessions and the Assistant's tool list, and
left audit_tools / audit_wiring / audit_icons walking a missing folder while
still printing GREEN. Splitting the ribbon into several tabs breaks every path
that joins a panel name onto "the" tab folder the same way, so a bundle is
looked up by its folder name (find_bundle / bundle_path) and these tests keep
it that way.

Run: python dev/test_extension_paths.py
"""
import ast
import os
import re
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
# Legacy mascot generator (see CLAUDE.md): its config still lists the old
# Support-relative paths, but main() resolves each target by its LAST folder
# name through find_bundle() and skips a bundle that no longer exists.
PANEL_PATH_ALLOWED = {os.path.join(HERE, 'generate_all_icons.py')}
LOOKUPS = {'find_bundle', 'bundle_path'}


def _real_folders(suffixes):
    names = set()
    for tab in tabdir.TABS:
        names.add(os.path.basename(tab))
        for _dirpath, dirs, _files in os.walk(tab):
            names.update(d for d in dirs if d.endswith(suffixes))
    return names


def _literals(path):
    """(lineno, text) of string literals that are not docstrings and not the
    bundle name handed to find_bundle()/bundle_path()."""
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read(), path)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, 'body', [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        if isinstance(node, ast.Call) and node.args:
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
            if name in LOOKUPS:
                skip.add(id(node.args[0]))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]


def _py_files(roots, keep=lambda path: True):
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for f in files:
                path = os.path.join(dirpath, f)
                if (f.endswith('.py') and path not in ALLOWED
                        and path != os.path.abspath(__file__) and keep(path)):
                    yield path


def _mk(root, *parts):
    path = os.path.join(root, *parts)
    os.makedirs(path)
    return path


class TabDirs(unittest.TestCase):
    def test_finds_every_real_tab_folder(self):
        tabs = extension_paths.tab_dirs()
        self.assertTrue(tabs)
        for tab in tabs:
            self.assertTrue(os.path.isdir(tab), tab)
            self.assertTrue(tab.endswith('.tab'))
            self.assertEqual(os.path.dirname(tab), EXT)
        self.assertEqual(tabdir.TABS, tabs)
        self.assertEqual(tabdir.TAB, tabs[0])
        self.assertEqual(extension_paths.tab_dir(), tabs[0])

    def test_follows_a_rename(self):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, 'lib'))
            os.mkdir(os.path.join(root, 'Whatever.tab'))
            self.assertEqual(extension_paths.tab_dir(root), os.path.join(root, 'Whatever.tab'))
            os.mkdir(os.path.join(root, 'T3Lab_Next.tab'))
            self.assertEqual(extension_paths.tab_dir(root), os.path.join(root, 'T3Lab_Next.tab'))

    def test_ribbon_order_is_the_extension_layout(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ('A.tab', 'B.tab', 'C.tab'):
                os.mkdir(os.path.join(root, name))
            with open(os.path.join(root, 'bundle.yaml'), 'w', encoding='utf-8') as fh:
                fh.write('# order of the tabs\nlayout:\n  - B\n  - "A"\n\nauthor: T3Lab\n')
            self.assertEqual([os.path.basename(t) for t in extension_paths.tab_dirs(root)],
                             ['B.tab', 'A.tab', 'C.tab'])

    def test_real_layout_lists_every_tab(self):
        # pyRevit builds only the tabs named in the layout: a tab left out of it
        # simply never appears on the ribbon.
        layout = extension_paths._layout(EXT)
        if layout:
            self.assertEqual(sorted(n + '.tab' for n in layout),
                             sorted(os.path.basename(t) for t in tabdir.TABS))


class FindBundle(unittest.TestCase):
    def test_finds_a_bundle_in_any_tab(self):
        with tempfile.TemporaryDirectory() as root:
            _mk(root, 'One.tab', 'P.panel', 'A.pushbutton')
            b = _mk(root, 'Two.tab', 'Q.panel', 'S.stack', 'B.pushbutton')
            self.assertEqual(extension_paths.find_bundle('B.pushbutton', root), b)
            self.assertEqual(extension_paths.find_bundle('Q.panel', root),
                             os.path.dirname(os.path.dirname(b)))
            self.assertIsNone(extension_paths.find_bundle('Missing.pushbutton', root))

    def test_follows_a_bundle_that_moved(self):
        with tempfile.TemporaryDirectory() as root:
            old = _mk(root, 'One.tab', 'P.panel', 'A.pushbutton')
            self.assertEqual(extension_paths.find_bundle('A.pushbutton', root), old)
            new = os.path.join(root, 'Two.tab', 'Q.panel', 'A.pushbutton')
            os.makedirs(os.path.dirname(new))
            os.rename(old, new)
            self.assertEqual(extension_paths.find_bundle('A.pushbutton', root), new)

    def test_every_path_the_runtime_needs_exists(self):
        for name, parts in (('BG Theme.pushbutton', ()),
                            ('FamiGen.pushbutton', ('prompts',)),
                            ('ManaLoca.pushbutton', ()),
                            ('ImageToDrafting.pushbutton', ()),
                            ('T3LabAssistant.pushbutton', ('script.py',)),
                            ('BatchOut.pushbutton', ('script.py',))):
            path = extension_paths.bundle_path(name, *parts)
            self.assertTrue(os.path.exists(path), path)

    def test_famigen_prompts_dir_exists(self):
        from FamilyGen import guidance
        self.assertTrue(os.path.isdir(guidance.prompts_dir()), guidance.prompts_dir())

    def test_bundle_names_are_unique(self):
        # find_bundle() looks a bundle up by folder name: two folders with the
        # same name in different tabs or panels would make one of them unreachable.
        seen, dupes = {}, []
        for tab in tabdir.TABS:
            for dirpath, dirs, _files in os.walk(tab):
                for d in dirs:
                    if d.endswith(extension_paths.BUNDLE_SUFFIXES):
                        path = os.path.relpath(os.path.join(dirpath, d), EXT)
                        if d in seen:
                            dupes.append((seen[d], path))
                        seen.setdefault(d, path)
        self.assertEqual(dupes, [])


class NoHardcodedRibbonPath(unittest.TestCase):
    def test_no_tab_folder_name_in_lib_or_dev(self):
        names = {os.path.basename(t) for t in tabdir.TABS} | {'T3Lab.tab', 'T3Lab_Dev.tab'}
        bad = ['%s:%d' % (os.path.relpath(p, REPO), ln)
               for p in _py_files([os.path.join(EXT, 'lib'), HERE])
               for ln, text in _literals(p) if any(n in text for n in names)]
        self.assertEqual(bad, [])

    def test_no_panel_path_in_lib_or_dev_gates(self):
        # A panel / stack / pulldown name in a path breaks when the panel moves
        # to another tab or the button leaves its stack. Name the BUTTON and
        # let find_bundle() find it. Test fixtures that build a temp ribbon are
        # free to name what they like.
        names = _real_folders(('.panel', '.stack', '.pulldown'))
        pattern = re.compile('|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True)))
        roots = [os.path.join(EXT, 'lib'), HERE]
        keep = lambda p: not os.path.basename(p).startswith('test_') and p not in PANEL_PATH_ALLOWED  # noqa: E731
        bad = ['%s:%d %s' % (os.path.relpath(p, REPO), ln, text[:60])
               for p in _py_files(roots, keep)
               for ln, text in _literals(p) if pattern.search(text)]
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
