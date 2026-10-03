#! python3
# -*- coding: utf-8 -*-
__title__  = "FamiGen"
__author__ = "Tran Tien Thanh"
__doc__    = "FamiGen — Create Revit families from CAD blocks, JSON schema, or presets."

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

_ext_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_lib = os.path.join(_ext_dir, 'lib')
if _lib not in sys.path:
    sys.path.insert(0, _lib)

# Force reload GUI modules to prevent caching issues in pyRevit
try:
    from importlib import reload as _reload
    for _mod in ('GUI.WPF_Base', 'GUI.FamiGenDialog', 'FamiGenDialog'):
        if _mod in sys.modules:
            try:
                _reload(sys.modules[_mod])
            except Exception:
                pass
except Exception:
    pass

from GUI.FamiGenDialog import show_family_creator
from Snippets._host import resolve_doc

if __name__ == '__main__':
    doc, err = resolve_doc()
    if not doc:
        from pyrevit import forms
        forms.alert(err or "No active document found in Revit.", title="FamiGen")
    else:
        show_family_creator(doc, doc.Application)

