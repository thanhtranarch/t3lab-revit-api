# -*- coding: utf-8 -*-
"""The extension's ribbon tab folders, for dev gates and tests.

The folder name is the ribbon tab title and changes (``T3Lab.tab`` ->
``T3Lab_Dev.tab``, 2026-09-26), and the ribbon has several tabs since
2026-10-03. A hardcoded name made audit_tools, audit_wiring and audit_icons
walk a folder that no longer existed and report GREEN over zero pushbutton
scripts. Resolved by the same ``lib/core/extension_paths.py`` the extension
uses at runtime; loaded by file path so importing this does not put ``lib``
on ``sys.path``.

    TABS              every *.tab folder, in ribbon order — gates walk ALL of them
    TAB               the first one (old callers; never join a panel path onto it)
    bundle_path(n, …) a bundle found by folder name in any tab, e.g.
                      bundle_path('BatchOut.pushbutton', 'script.py')
"""
import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(REPO, 'T3Lab.extension')

_spec = importlib.util.spec_from_file_location(
    '_t3_extension_paths', os.path.join(EXT, 'lib', 'core', 'extension_paths.py'))
_paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_paths)

TABS = _paths.tab_dirs(EXT)
if not TABS:
    raise RuntimeError('No *.tab folder under %s' % EXT)
TAB = TABS[0]


def find_bundle(name):
    return _paths.find_bundle(name, EXT)


def bundle_path(name, *parts):
    folder = find_bundle(name)
    if not folder:
        raise RuntimeError('No ribbon bundle named %r under %s' % (name, EXT))
    return os.path.join(folder, *parts)


def tab_of(path):
    """The tab folder ``path`` sits in, or None."""
    path = os.path.abspath(path)
    return next((t for t in TABS if path == t or path.startswith(t + os.sep)), None)
