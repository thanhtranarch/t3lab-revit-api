# -*- coding: utf-8 -*-
"""
Groups Snippets

Code snippets for working with Revit groups.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Groups Snippets"

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗
# ║║║║╠═╝║ ║╠╦╝ ║
# ╩╩ ╩╩  ╚═╝╩╚═ ╩  IMPORT
#==================================================

from Autodesk.Revit.DB import *
from pyrevit import forms

# CUSTOM IMPORTS
from GUI.forms           import select_from_dict


try:
    from Snippets._host import resolve_doc, host_uiapp
    default_doc, _ = resolve_doc()
    _uiapp = host_uiapp()
    default_uidoc = _uiapp.ActiveUIDocument if _uiapp else None
    default_app = _uiapp.Application if _uiapp else None
except Exception:
    default_doc = None
    default_uidoc = None
    default_app = None


