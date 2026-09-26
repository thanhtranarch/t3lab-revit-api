# -*- coding: utf-8 -*-
"""
Sheets Snippets

Code snippets for working with Revit sheets.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Sheets Snippets"

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import UIDocument
try:
    from Snippets._host import resolve_doc, host_uiapp
    default_doc, _ = resolve_doc()
    _uiapp = host_uiapp()
    default_uidoc = _uiapp.ActiveUIDocument if _uiapp else None
except Exception:
    default_doc = None
    default_uidoc = None


