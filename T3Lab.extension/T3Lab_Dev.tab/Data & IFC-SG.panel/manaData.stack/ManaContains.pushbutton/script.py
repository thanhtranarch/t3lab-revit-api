#! python3
# -*- coding: utf-8 -*-
"""
Contains Manager - Find elements in spatial containers or collect element data into spatial elements.
Copyright (c) 2026 Dang Quoc Truong (DQT)
"""

__title__ = "Contains\nManager"
__author__ = "Dang Quoc Truong (DQT)"
__doc__ = "Find elements in spatial containers or collect element data into spatial elements."

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

# Define extension directory and add lib to sys.path
SCRIPT_DIR = os.path.dirname(__file__)
# Stacked pushbutton (tab/panel/stack/pushbutton) -> 4 levels up to extension root
EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))
LIB_DIR = os.path.join(EXT_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from GUI.ManaContainsDialog import main

if __name__ == "__main__":
    main()
