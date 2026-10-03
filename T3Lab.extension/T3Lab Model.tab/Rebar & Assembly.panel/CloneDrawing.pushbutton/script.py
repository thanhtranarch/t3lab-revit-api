#! python3
# -*- coding: utf-8 -*-
"""Clone Drawing — copy the finished drawing of one assembly to similar assemblies (Tekla: clone drawing).

Design: dev/plan/clone-drawing-v2-design.md. UI in lib/GUI/CloneDrawingDialog.py,
Revit work in lib/Snippets/_drawing_clone.py; this file only resolves the
document, shows the modal dialog and opens the sheet it asked for afterwards.
"""

__title__   = "Clone\nDrawing"
__author__  = "Tran Tien Thanh"
__version__ = "2.0.0"

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

TOOL = "Clone Drawing"


def _open_sheet(doc, sheet_id):
    """Activate the sheet the user asked for, after the modal window closed (spec R12)."""
    from GUI.T3Dialog import show_warning
    from Snippets._compat import make_eid, short_error
    from Snippets._host import resolve_uidoc
    sheet = doc.GetElement(make_eid(sheet_id))
    uidoc = resolve_uidoc()
    if sheet is None or uidoc is None:
        show_warning("The sheet could not be opened: it no longer exists or Revit has no active window.",
                     title=TOOL,
                     details="Find it in the Project Browser under Sheets (sheet id %s)." % sheet_id)
        return
    try:
        uidoc.ActiveView = sheet
    except Exception as first:
        try:
            uidoc.RequestViewChange(sheet)       # [NV] fallback when ActiveView is refused
        except Exception:
            show_warning("Revit refused to open sheet %s." % getattr(sheet, "SheetNumber", sheet_id),
                         title=TOOL,
                         details="%s\nOpen it from the Project Browser." % short_error(first))


# ── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    from Snippets._host import resolve_doc
    doc, doc_error = resolve_doc()

    if not doc:
        from GUI.T3Dialog import show_warning
        show_warning(
            doc_error or "Open a Revit project before running Clone Drawing.",
            title=TOOL,
            details="Clone Drawing needs an active document to read its assemblies from.")
    elif doc.IsFamilyDocument:
        from GUI.T3Dialog import show_warning
        show_warning(
            "Clone Drawing works on projects, not on family documents.",
            title=TOOL,
            details="Open the project that contains the assemblies, then run it again.")
    else:
        from GUI.CloneDrawingDialog import show_clone_drawing
        from Snippets._host import resolve_uidoc
        try:
            uidoc = resolve_uidoc()            # None = From selection / Pick in model say so
        except Exception:
            uidoc = None
        sheet_to_open = show_clone_drawing(doc, uidoc)
        if sheet_to_open:
            _open_sheet(doc, sheet_to_open)
