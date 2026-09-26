#! python3
# -*- coding: utf-8 -*-
"""Auto Adjust Base Offset
Automatically adjusts Base/Top Offset when changing level constraints to maintain 3D elevation.
Supports: Walls, Floors, Columns, Structural Columns, Beams, Structural Framing
"""

__title__ = "Auto Adj\nBase Offset"
__author__ = "Dang Quoc Truong & T3Lab"
__doc__ = "Auto-adjusts Base or Top Offset when changing Level Constraint to preserve element 3D position."

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
    if 'GUI.WallAdjustBaseDialog' in sys.modules:
        reload(sys.modules['GUI.WallAdjustBaseDialog'])
    elif 'WallAdjustBaseDialog' in sys.modules:
        reload(sys.modules['WallAdjustBaseDialog'])

from pyrevit import revit
from GUI.WallAdjustBaseDialog import show_wall_adjust_base

if __name__ == '__main__':
    doc = None
    uidoc = None
    try:
        doc = revit.doc
        uidoc = revit.uidoc
    except Exception:
        pass
    show_wall_adjust_base(doc, uidoc)