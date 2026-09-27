#! python3
# -*- coding: utf-8 -*-
"""MakePattern — Interactive Vector Hatch Studio & Large Surface Preview.

Author: T3Lab
"""
__title__ = "Make\nPattern"
__author__ = "T3Lab"

import os
import sys

# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 3 levels for non-stacked (Annotation & Select.panel/MakePattern.pushbutton)
EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
LIB_DIR = os.path.join(EXT_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────

def _main():
    from GUI.MakePatternDialog import show_dialog
    show_dialog()

if __name__ == '__main__':
    try:
        from GUI.ErrorGuard import run_tool
    except Exception:
        _main()
    else:
        run_tool('MakePattern', _main)
