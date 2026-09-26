#! python3
# -*- coding: utf-8 -*-
"""ManaAnno — Unified annotation and text note manager.

Consolidates:
  - Dimensions (Audit & manage dimension types/instances)
  - Text Notes (Audit & search text note contents)
  - Tag Checker (Search & delete orphan tags)
  - DimText (Manage dimension text overrides)
  - Utilities (Renumber along spline, Copy annotations, Upper all)

Author: T3Lab
"""
__title__ = "Mana\nAnno"
__author__ = "T3Lab"

import os
import sys
# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
# CPython engine paths come from lib/_cpython_bootstrap.py below - it finds
# the engine whatever the pyRevit clone is named or wherever it is installed.

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
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
    # Import once per CPython runtime. Deleting and re-importing GUI modules can
    # duplicate PythonNet wrapper types (TagChecker implements a CLR interface).
    from GUI.ManaAnnoDialog import show_dialog
    show_dialog()

if __name__ == '__main__':
    try:
        from GUI.ErrorGuard import run_tool
    except Exception:
        _main()
    else:
        run_tool('ManaAnno', _main)
