# -*- coding: utf-8 -*-
"""
Select Similar Family

Select all elements of the same family type as the selection.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Select Similar Family"
__doc__ = """Version = 1.0
Date    = 22.08.2022
_____________________________________________________________________
Description:
Select all instances in the project of the same Family.
_____________________________________________________________________
How-to:
- Select a single element
- Get All instances of the same family in Model
_____________________________________________________________________
Last update:
- [22.08.2022] - 1.0 RELEASE
_____________________________________________________________________
"""
# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝ IMPORTS
# ==================================================
import clr
clr.AddReference("System")
from System.Collections.Generic import List
from Autodesk.Revit.DB import *

# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝
try:
    from Snippets._host import resolve_doc, resolve_uidoc, get_revit_version
except ImportError:
    try:
        import importlib
        import Snippets._host
        importlib.reload(Snippets._host)
        from Snippets._host import resolve_doc, resolve_uidoc, get_revit_version
    except Exception:
        from Snippets._host import resolve_doc, get_revit_version
        def resolve_uidoc(candidate=None):
            if candidate is not None and hasattr(candidate, 'Document') and candidate.Document is not None:
                return candidate
            try:
                from pyrevit import revit
                return revit.uidoc
            except Exception:
                return None
from Snippets._compat import make_eid

# ╔═╗╦ ╦╔╗╔╔═╗╔╦╗╦╔═╗╔╗╔
# ╠╣ ║ ║║║║║   ║ ║║ ║║║║
# ╚  ╚═╝╝╚╝╚═╝ ╩ ╩╚═╝╝╚╝ FUNCTION
# ==================================================