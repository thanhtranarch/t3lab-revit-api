# -*- coding: utf-8 -*-
"""The extension's ribbon tab folder, for dev gates and tests.

The folder name is the ribbon tab title and changes (``T3Lab.tab`` ->
``T3Lab_Dev.tab``, 2026-09-26). A hardcoded name made audit_tools,
audit_wiring and audit_icons walk a folder that no longer existed and report
GREEN over zero pushbutton scripts. Resolved by the same
``lib/core/extension_paths.py`` the extension uses at runtime; loaded by file
path so importing this does not put ``lib`` on ``sys.path``.
"""
import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(REPO, 'T3Lab.extension')

_spec = importlib.util.spec_from_file_location(
    '_t3_extension_paths', os.path.join(EXT, 'lib', 'core', 'extension_paths.py'))
_paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_paths)

TAB = _paths.tab_dir(EXT)
if not os.path.isdir(TAB):
    raise RuntimeError('No *.tab folder under %s' % EXT)


def tab_path(*parts):
    return os.path.join(TAB, *parts)
