#! python3
# -*- coding: utf-8 -*-
"""Image To Drafting View
Accepts images (JPG/PNG/BMP) or PDF files and converts them to Drafting Detail Lines.

Author: Tran Tien Thanh & T3Lab
Mail: trantienthanh909@gmail.com
"""

__title__   = "Image to\nDrafting"
__author__  = "Tran Tien Thanh"
__version__ = "4.0.0"

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
    if 'GUI.ImageToDraftingDialog' in sys.modules:
        reload(sys.modules['GUI.ImageToDraftingDialog'])
    elif 'ImageToDraftingDialog' in sys.modules:
        reload(sys.modules['ImageToDraftingDialog'])

from pyrevit import revit
from GUI.ImageToDraftingDialog import show_image_to_drafting_dialog

if __name__ == '__main__':
    doc = None
    try:
        doc = revit.doc
    except Exception:
        pass
    show_image_to_drafting_dialog(doc)
