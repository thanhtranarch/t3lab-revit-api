#! python3
# -*- coding: utf-8 -*-
"""Tekla Bridge — find a Revit command or T3Lab tool by its Tekla name."""

__title__   = "Tekla\nBridge"
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

from pyrevit import revit  # noqa: F401  (keeps pyRevit's CPython loader happy; not used here)

DIALOG_TITLE = "Tekla Bridge"


def _run_action(action, doc):
    """Carry out what the user picked in the Bridge window.

    Runs AFTER the window has closed: UIApplication.PostCommand only executes
    once this pyRevit command returns, and a posted command must not fight a
    modal window for focus.
    """
    from GUI.T3Dialog import show_info, show_warning
    from Snippets import _tekla_bridge
    from Snippets._host import host_uiapp

    kind, row = action
    uiapp = host_uiapp()
    year = _tekla_bridge.revit_year(uiapp)

    if kind == _tekla_bridge.ACTION_POST:
        status, payload = _tekla_bridge.post_revit_command(uiapp, row, year)
        if status == "no_command":
            show_info(_tekla_bridge.post_failure_message(row, status, payload, year),
                      title=DIALOG_TITLE, details=row["tip_en"])
        elif status == "cannot_post":
            show_warning(_tekla_bridge.post_failure_message(row, status, payload, year),
                         title=DIALOG_TITLE, details=row["tip_en"])
    elif kind == _tekla_bridge.ACTION_TOOL:
        error = _tekla_bridge.run_tool(row, doc)
        if error:
            show_warning(error, title=DIALOG_TITLE, details=row["tip_en"])
    elif kind == _tekla_bridge.ACTION_RIBBON:
        show_info(_tekla_bridge.ribbon_message(row, year),
                  title=DIALOG_TITLE, details=row["tip_en"])


# ── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # The Bridge opens WITHOUT a model: it only reads a JSON file and posts
    # commands, so there is no "Open a Revit project" gate here. `doc` may be
    # None; the T3Lab tools it opens check for a document themselves.
    from Snippets._host import resolve_doc
    doc, _doc_error = resolve_doc()

    from GUI.TeklaBridgeDialog import show_tekla_bridge
    chosen = show_tekla_bridge(doc)
    if chosen:
        _run_action(chosen, doc)
