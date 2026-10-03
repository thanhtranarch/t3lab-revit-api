#! python3
# -*- coding: utf-8 -*-
"""
Crop Sync
Synchronize View Crop Region shape, dimensions, and annotation crop.

Copyright (c) 2026 T3Lab
"""
__title__ = "Crop\nSync"
__author__ = "T3Lab"

import os
import sys

# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
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

try:
    reload
except NameError:
    try:
        from importlib import reload
    except Exception:
        reload = None

if reload:
    if 'Snippets.crop_sync' in sys.modules:
        reload(sys.modules['Snippets.crop_sync'])
    if 'GUI.CropSyncDialog' in sys.modules:
        reload(sys.modules['GUI.CropSyncDialog'])
    elif 'CropSyncDialog' in sys.modules:
        reload(sys.modules['CropSyncDialog'])

from GUI.CropSyncDialog import show_crop_sync

if __name__ == '__main__':
    show_crop_sync()
