#! python3
# -*- coding: utf-8 -*-
"""
Location Manager (Modeless)

List and adjust the location of elements in the current view or by level.
Stays open while you work — fully independent of Revit's modal UI.

Architecture: ALL Revit API calls go through ExternalEvent.Execute().
              The WPF thread only reads/writes UI controls and plain Python data.

--------------------------------------------------------
Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
--------------------------------------------------------
"""

__author__ = "Tran Tien Thanh"
__title__ = "Location\nManager"
__version__ = "1.4.0"
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

from GUI.ManaLocaDialog import show_location_manager_dialog, detect_persistent_engine

if __name__ == "__main__":
    is_persistent = detect_persistent_engine()
    show_location_manager_dialog(modal=not is_persistent)
