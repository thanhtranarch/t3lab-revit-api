#! python3
# -*- coding: utf-8 -*-
"""
Property Line

Create and manage property lines from survey data.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Property Line"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==================================================
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
import clr

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')

from pyrevit import revit, script

# Path setup — 4 levels up: script.py → pushbutton → stack → panel → tab → extension
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

from GUI.PropertyLineDialog import show_property_line_dialog

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()
# CPython has no ScriptOutput.GetDefault; safe_output()
# returns a no-op window instead of killing the tool.
try:
    from _cpython_bootstrap import safe_output
    output = safe_output()
except Exception:
    output = script.get_output()
# Read from the Application, not the document: `revit.doc` is None when this
# tool is launched from the Assistant pane or with no project open, and the
# old `int(revit.doc.Application.VersionNumber)` raised at IMPORT time
# ('NoneType' object has no attribute 'Application') so the window never opened.
from Snippets._host import get_revit_version
REVIT_VERSION = get_revit_version()

# CLASS/FUNCTIONS
# ==================================================

# MAIN SCRIPT
# ==================================================

if __name__ == '__main__':
    if not revit.doc:
        from pyrevit import forms
        forms.alert("Please open a Revit document first.", exitscript=True)
    try:
        show_property_line_dialog()
    except Exception as ex:
        logger.error("Property Line Tool error: {}".format(ex))
        import traceback
        logger.error(traceback.format_exc())
