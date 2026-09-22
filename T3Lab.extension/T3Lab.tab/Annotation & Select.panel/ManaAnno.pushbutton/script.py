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
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

# Evict stale cached modules so code changes take effect without a full pyRevit reload
_stale = [k for k in list(sys.modules.keys())
          if k in ('GUI.ManaAnnoDialog', 'GUI.DimTextDialog',
                   'GUI.TagCheckerDialog', 'GUI.CopyAnnotationDialog',
                   'ManaAnnoDialog', 'DimTextDialog',
                   'TagCheckerDialog', 'CopyAnnotationDialog',
                   'Snippets._host', '_host')]
for _k in _stale:
    del sys.modules[_k]

import GUI.ManaAnnoDialog as ManaAnnoDialog

if __name__ == '__main__':
    ManaAnnoDialog.show_dialog()
