#! python3
# -*- coding: utf-8 -*-
"""BVBS Export — write .abs bending-machine files (BVBS BF2D) from shape-driven rebar, with a verified checksum."""

__title__   = "BVBS\nExport"
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

from pyrevit import revit  # noqa: F401  (keeps pyRevit's CPython loader happy; not used at module level)

# ── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # Snippets._host.resolve_doc walks the real fallback chain (revit.doc →
    # ActiveUIDocument → the single open model) and returns a message worth
    # showing when there genuinely is nothing to act on.
    from Snippets._host import resolve_doc
    doc, doc_error = resolve_doc()

    if not doc:
        from GUI.T3Dialog import show_warning
        show_warning(
            doc_error or "Open a Revit project before running BVBS Export.",
            title="BVBS Export",
            details="BVBS Export needs an active document to read its rebar from.")
    elif doc.IsFamilyDocument:
        from GUI.T3Dialog import show_warning
        show_warning(
            "BVBS Export works on projects, not on family documents.",
            title="BVBS Export",
            details="Open the project that contains the reinforcement, then run it again.")
    else:
        from GUI.BVBSExportDialog import show_bvbs_export
        show_bvbs_export(doc)
