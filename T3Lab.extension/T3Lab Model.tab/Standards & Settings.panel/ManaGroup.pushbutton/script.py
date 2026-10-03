#! python3
# -*- coding: utf-8 -*-
"""Group Manager — rename groups, set their workset, clean up unused types, and plot where every instance sits."""

__title__   = "Group\nManager"
__author__  = "Tran Tien Thanh"
__version__ = "1.1.0"

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

from pyrevit import revit

# ── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # `revit.doc` on its own is a getattr chain that yields None whenever this
    # engine has no active document — which is NOT the same thing as "no model
    # is open", and it made the tool refuse to start with a project loaded.
    # Snippets._host.resolve_doc walks the real fallback chain (revit.doc →
    # ActiveUIDocument → the single open model) and returns a message worth
    # showing when there genuinely is nothing to act on.
    from Snippets._host import resolve_doc
    doc, doc_error = resolve_doc()

    if not doc:
        from GUI.T3Dialog import show_warning
        show_warning(
            doc_error or "Open a Revit project before running Group Manager.",
            title="Group Manager",
            details="Group Manager needs an active document to read its groups from.")
    elif doc.IsFamilyDocument:
        from GUI.T3Dialog import show_warning
        show_warning(
            "Group Manager works on projects, not on family documents.",
            title="Group Manager",
            details="Open the project that contains the model or detail groups, then run it again.")
    else:
        from GUI.ManaGroupDialog import show_group_manager
        show_group_manager(doc)
