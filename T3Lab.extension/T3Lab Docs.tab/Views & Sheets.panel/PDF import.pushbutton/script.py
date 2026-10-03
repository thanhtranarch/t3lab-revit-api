#! python3
# -*- coding: utf-8 -*-
"""
PDF Import

Import PDF pages into Revit views sequentially.
Opens a dialog to pick a PDF and map each page to a target view.
Page 1 → View 1, Page 2 → View 2, etc.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__title__   = "PDF\nImport"
__author__  = "Tran Tien Thanh"
__version__ = "2.1.0"

# IMPORTS
# ==============================================================================
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

extension_dir = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
)
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

# MAIN
# ==============================================================================
if __name__ == '__main__':
    from GUI.PDFImportDialog import show_pdf_import
    show_pdf_import()
