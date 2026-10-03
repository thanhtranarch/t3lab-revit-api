#! python3
# -*- coding: utf-8 -*-
"""Text to Element — transfer text note content to element parameters via
bounding-box intersection in the active view.
"""

__title__ = "Text to\nElement"
__author__ = "Tran Tien Thanh"
__doc__ = (
    "Transfer text note content to element parameters via bounding-box "
    "intersection. Select a target category and parameter, then run "
    "'Find Intersections' to preview matches before writing."
)

import os, sys
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
import os

_lib = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'lib')
)
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from GUI.TextToElementDialog import show_text_to_element

show_text_to_element(__revit__)
