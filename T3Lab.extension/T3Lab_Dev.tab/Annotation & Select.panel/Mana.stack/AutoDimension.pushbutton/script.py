#! python3
# -*- coding: utf-8 -*-
"""Auto Dimension — automatically create dimension chains for selected elements.

Author: T3Lab
"""
__title__ = "Auto\nDimension"
__author__ = "T3Lab"
__persistentengine__ = True

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

# Add lib directory to system path
# __file__ is T3Lab.extension/<Tab>.tab/Annotation & Select.panel/Mana.stack/AutoDimension.pushbutton/script.py
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

# Import and show the dialog
import GUI.AutoDimensionDialog as AutoDimensionDialog

if __name__ == '__main__':
    AutoDimensionDialog.show_dialog()
