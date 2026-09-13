#! python3
# -*- coding: utf-8 -*-
"""Create Room Plan (SheetGen)
Create Plan Views and Sheets from Room List with WPF UI.

Author: Tran Tien Thanh & T3Lab
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "Create Plan Views"
__version__ = "1.0.0"

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

_lib = os.path.normpath(os.path.join(os.path.dirname(__file__), '../../../lib'))
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
    if 'GUI.SheetGenDialog' in sys.modules:
        reload(sys.modules['GUI.SheetGenDialog'])
    elif 'SheetGenDialog' in sys.modules:
        reload(sys.modules['SheetGenDialog'])

from pyrevit import revit
from GUI.SheetGenDialog import show_sheet_gen_dialog

if __name__ == '__main__':
    doc = None
    uidoc = None
    try:
        doc = revit.doc
        uidoc = revit.uidoc
    except Exception:
        pass
    show_sheet_gen_dialog(doc, uidoc)
