#! python3
# -*- coding: utf-8 -*-
"""Group Manager — rename groups, set their workset, clean up unused types, and plot where every instance sits."""

__title__   = "Group\nManager"
__author__  = "Tran Tien Thanh"
__version__ = "1.1.0"

# ── IMPORTS & BOOTSTRAP ──────────────────────────────────────────────────────
import os
import sys

# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
for _env in ('APPDATA', 'PROGRAMDATA'):
    _base = os.environ.get(_env, '')
    if _base:
        for _clone in ('pyRevit-Master', 'pyRevit'):
            _ceng = os.path.join(_base, _clone, 'bin', 'cengines', 'CPY3123')
            if os.path.isdir(_ceng):
                for _d in (_ceng, os.path.join(_ceng, 'Lib')):
                    if hasattr(os, 'add_dll_directory'):
                        try:
                            os.add_dll_directory(_d)
                        except Exception:
                            pass
                for _p in (_ceng, os.path.join(_ceng, 'Lib'), os.path.join(_ceng, 'python312.zip')):
                    if os.path.exists(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)

_cur = os.path.dirname(os.path.abspath(__file__))
while _cur and not os.path.exists(os.path.join(_cur, 'lib')):
    _parent = os.path.dirname(_cur)
    if _parent == _cur:
        break
    _cur = _parent
_lib_dir = os.path.join(_cur, 'lib')
if os.path.exists(_lib_dir) and _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
LIB_DIR = os.path.join(EXT_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from pyrevit import revit

# ── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    try:
        doc = revit.doc
    except Exception:
        doc = None

    if not doc:
        from GUI.T3Dialog import show_warning
        show_warning(
            "Open a Revit project before running Group Manager.",
            title="Group Manager",
            details="Group Manager needs an active document to read its groups from.")
    elif doc.IsFamilyDocument:
        from GUI.T3Dialog import show_warning
        show_warning(
            "Group Manager works on projects, not on family documents.",
            title="Group Manager",
            details="Open the project that contains the model or detail groups, then run it again.")
    else:
        from GUI.ManaGroupDialog import show_group_manager
        show_group_manager(doc)
