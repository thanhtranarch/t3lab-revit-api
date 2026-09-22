#! python3
# -*- coding: utf-8 -*-
"""Point Cloud to Model
Scan-to-BIM: Extract point cloud slices, detect planes, and generate walls, columns, floors, roofs, openings.

Author: Tran Tien Thanh & T3Lab
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "Point Cloud\nto Model"
__version__ = "2.0.0"

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
    if 'GUI.PointCloudDialog' in sys.modules:
        reload(sys.modules['GUI.PointCloudDialog'])
    elif 'PointCloudDialog' in sys.modules:
        reload(sys.modules['PointCloudDialog'])

from pyrevit import revit
from GUI.PointCloudDialog import show_point_cloud_dialog

if __name__ == '__main__':
    doc = None
    uidoc = None
    try:
        doc = revit.doc
        uidoc = revit.uidoc
    except Exception:
        pass
    show_point_cloud_dialog(doc, uidoc)
