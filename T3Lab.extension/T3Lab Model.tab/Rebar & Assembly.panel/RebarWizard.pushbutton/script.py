#! python3
# -*- coding: utf-8 -*-
"""Rebar Wizard — reinforce rectangular beams, columns and pad footings from a preset."""

__title__   = "Rebar\nWizard"
__author__  = "Tran Tien Thanh"
__version__ = "1.0.0"

# ── IMPORTS & BOOTSTRAP ──────────────────────────────────────────────────────
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

SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
LIB_DIR = os.path.join(EXT_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from pyrevit import revit  # noqa: F401  (keeps pyRevit happy; not used at module level)

# ── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # resolve_doc walks the real fallback chain (revit.doc -> ActiveUIDocument ->
    # the single open model); `revit.doc` alone can be None with a model open.
    from Snippets._host import resolve_doc
    doc, doc_error = resolve_doc()

    if not doc:
        from GUI.T3Dialog import show_warning
        show_warning(
            doc_error or "Open a Revit project before running Rebar Wizard.",
            title="Rebar Wizard",
            details="Rebar Wizard needs an active document with beams, columns or pad footings to reinforce.")
    elif doc.IsFamilyDocument:
        from GUI.T3Dialog import show_warning
        show_warning(
            "Rebar Wizard works on projects, not on family documents.",
            title="Rebar Wizard",
            details="Open the project that contains the concrete hosts, then run it again.")
    else:
        from GUI.RebarWizardDialog import show_rebar_wizard
        show_rebar_wizard(doc)
