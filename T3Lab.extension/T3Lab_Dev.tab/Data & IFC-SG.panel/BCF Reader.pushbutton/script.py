#! python3
# -*- coding: utf-8 -*-
"""
DQT BCF Reader (v2 - pyRevit WPFWindow modeless)
Read BCF/BCFzip files exported from IFC Delta Viewer and navigate issues in Revit.

Dang Quoc Truong - DQT (c) 2025-2026
"""
__title__ = "BCF\nReader"
__author__ = "Dang Quoc Truong"
__doc__ = "Read BCF/BCFzip files and navigate issues in Revit."
__persistentengine__ = True

# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
import os, sys

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

from GUI.BCFReaderDialog import show_bcf_reader_dialog

if __name__ == "__main__":
    show_bcf_reader_dialog()