#! python3
# -*- coding: utf-8 -*-
"""Room to Floor
Create architectural or structural floors from selected room boundaries.

Author: Tran Tien Thanh & T3Lab
Mail: trantienthanh909@gmail.com
"""

__title__   = "Room\nTo Floor"
__author__  = "Tran Tien Thanh"
__version__ = "1.0.0"

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
    if 'GUI.RoomToFloorDialog' in sys.modules:
        reload(sys.modules['GUI.RoomToFloorDialog'])
    elif 'RoomToFloorDialog' in sys.modules:
        reload(sys.modules['RoomToFloorDialog'])

from pyrevit import revit
from GUI.RoomToFloorDialog import show_room_to_floor_dialog, run_headless

if __name__ == '__main__':
    doc = None
    uidoc = None
    try:
        doc = revit.doc
        uidoc = revit.uidoc
    except Exception:
        pass

    if len(sys.argv) > 1:
        run_headless(sys.argv[1], doc)
    else:
        show_room_to_floor_dialog(doc, uidoc)
