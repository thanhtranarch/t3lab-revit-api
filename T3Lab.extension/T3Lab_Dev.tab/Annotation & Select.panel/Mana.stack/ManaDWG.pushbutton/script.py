#! python3
# -*- coding: utf-8 -*-
"""DWG Management
Manage CAD imports and CAD links in the current Revit project.
List, rename, and delete DWG imports and links directly from a single interface.
"""

__author__  = "Tran Tien Thanh & Dang Quoc Truong"
__title__   = "DWG\nManager"
__version__ = "1.2.0"

import os
import sys

# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
# CPython engine paths come from lib/_cpython_bootstrap.py below - it finds
# the engine whatever the pyRevit clone is named or wherever it is installed.

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

_lib = os.path.normpath(os.path.join(os.path.dirname(__file__), '../../../../lib'))
if _lib not in sys.path:
    sys.path.insert(0, _lib)

try:
    reload
except NameError:
    try:
        from importlib import reload
    except Exception:
        reload = None

if reload:
    if 'GUI.ManaDWGDialog' in sys.modules:
        reload(sys.modules['GUI.ManaDWGDialog'])
    elif 'ManaDWGDialog' in sys.modules:
        reload(sys.modules['ManaDWGDialog'])

from pyrevit import revit
from GUI.ManaDWGDialog import show_dwg_manager

if __name__ == '__main__':
    doc = None
    try:
        doc = revit.doc
    except Exception:
        pass
    show_dwg_manager(doc)
