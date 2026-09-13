#! python3
# -*- coding: utf-8 -*-
"""
Foundation Volume Writer v1.0 - DQT
Writes Revit built-in volume value into a selected instance parameter
on all Structural Foundation elements in the active document.

Workflow:
  1. Tool collects all writable instance parameters from foundations
  2. User searches and selects target parameter
  3. Tool writes HOST_VOLUME_COMPUTED into selected parameter

Copyright (c) 2026 Dang Quoc Truong (DQT)
All rights reserved.
"""

__title__ = "Foundation\nVolume"
__author__ = "Dang Quoc Truong (DQT)"
__doc__ = "Write Revit computed volume into a selected shared parameter on Structural Foundation elements."

# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
import os, sys

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

from pyrevit import revit
from GUI.FoundationVolumeDialog import show_foundation_volume_dialog

if __name__ == "__main__":
    show_foundation_volume_dialog(revit.doc, getattr(revit, 'uidoc', None))