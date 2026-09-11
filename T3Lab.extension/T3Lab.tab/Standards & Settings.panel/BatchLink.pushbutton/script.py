#! python3
# -*- coding: utf-8 -*-
"""Batch Link — Link Revit models, set their workset in this model, and their per-view display."""

__title__   = "Batch\nLink"
__author__  = "Tran Tien Thanh"
__version__ = "1.1.0"

# ── IMPORTS & BOOTSTRAP ──────────────────────────────────────────────────────
import os
import sys

# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
for _env in ('APPDATA', 'PROGRAMDATA'):
    _base = os.environ.get(_env, '')
    if _base:
        for _clone in ('pyRevit-Master', 'pyRevit'):
            _ceng = os.path.join(_base, _clone, 'bin', 'cengines', 'CPY3123')
            if os.path.isdir(_ceng):
                for _d in (_ceng, os.path.join(_ceng, 'Lib')):
                    if hasattr(os, 'add_dll_directory'):
                        try:
                            os.add_dll_directory(_d)
                        except Exception:
                            pass
                for _p in (_ceng, os.path.join(_ceng, 'Lib'), os.path.join(_ceng, 'python312.zip')):
                    if os.path.exists(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)

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
    # engine has no active document -- which is NOT the same thing as "no model
    # is open", and it made the tool refuse to start with a project loaded.
    # Snippets._host.resolve_doc walks the real fallback chain (revit.doc ->
    # ActiveUIDocument -> the single open model) and returns a message worth
    # showing when there genuinely is nothing to act on.
    from Snippets._host import resolve_doc
    doc, doc_error = resolve_doc()

    if not doc:
        from GUI.T3Dialog import show_warning
        show_warning(
            doc_error or "Open a Revit project before running Batch Link.",
            title="Batch Link",
            details="Batch Link needs an active document to read its links from.")
    elif doc.IsFamilyDocument:
        from GUI.T3Dialog import show_warning
        show_warning(
            "Batch Link works on projects, not on family documents.",
            title="Batch Link",
            details="Open the project the models should be linked into, then run it again.")
    else:
        from GUI.BatchLinkDialog import show_batch_link
        show_batch_link(doc)