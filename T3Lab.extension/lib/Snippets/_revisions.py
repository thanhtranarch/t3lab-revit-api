# -*- coding: utf-8 -*-
"""
Revisions Snippets

Code snippets for managing Revit revisions and revision clouds.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Revisions Snippets"

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝ IMPORTS
#====================================================================================================
import traceback

from Autodesk.Revit.DB import (RevisionNumberType,
                               Revision,
                               ViewSheet,
                               ElementId)
from Snippets._context_manager import try_except

# `__revit__` members are unavailable when no UIDocument is active, and at
# module scope that kills the import outright. Resolve defensively; the entry
# point reports the real problem (see Snippets._host.resolve_doc()).
try:
    from Snippets._host import resolve_doc, get_revit_version
    doc, _doc_err = resolve_doc()
    rvt_year = get_revit_version()
except Exception:
    doc = None
    rvt_year = 2024


# ╔╦╗╔═╗╔═╗╔╦╗╦╔╗╔╔═╗
#  ║ ║╣ ╚═╗ ║ ║║║║║ ╦
#  ╩ ╚═╝╚═╝ ╩ ╩╝╚╝╚═╝
#==================================================
