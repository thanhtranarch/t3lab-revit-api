# -*- coding: utf-8 -*-
"""
Selection Snippets

Code snippets for element selection in Revit.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Selection Snippets"

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝
#==================================================
import sys, clr
import traceback

from Autodesk.Revit.UI.Selection    import ISelectionFilter, ObjectType, Selection
from Autodesk.Revit.DB.Architecture import Room
from Autodesk.Revit.DB import *
from Autodesk.Revit.Exceptions     import OperationCanceledException

# pyRevit IMPORTS
from pyrevit.forms import SelectFromList
from pyrevit import forms

#.NET
clr.AddReference('System')
from System.Collections.Generic import List
from Snippets._compat import net_list

# CUSTOM IMPORTS
from Snippets._variables import ALL_VIEW_TYPES
from GUI.forms           import select_from_dict

# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝
#==================================================
# `__revit__` members are unavailable when no UIDocument is active, and at
# module scope that kills the import outright. Resolve defensively; the entry
# point reports the real problem (see Snippets._host.resolve_doc()).
try:
    uidoc = __revit__.ActiveUIDocument
except Exception:
    uidoc = None
try:
    doc = __revit__.ActiveUIDocument.Document
except Exception:
    doc = None
selection = uidoc.Selection                          # type: Selection

# ╔═╗╔═╗╔╦╗  ╔═╗╔═╗╦  ╔═╗╔═╗╔╦╗╔═╗╔╦╗
# ║ ╦║╣  ║   ╚═╗║╣ ║  ║╣ ║   ║ ║╣  ║║
# ╚═╝╚═╝ ╩   ╚═╝╚═╝╩═╝╚═╝╚═╝ ╩ ╚═╝═╩╝
#==================================================
def get_selected_elements(uidoc = uidoc, exitscript=True):
    """Property that retrieves selected views or promt user to select some from the dialog box."""
    doc       = uidoc.Document
    selection = uidoc.Selection  # type: Selection

    selected_elements = [doc.GetElement(e_id) for e_id in selection.GetElementIds()]
    if not selected_elements:
        forms.alert("No elements were selected.\nPlease, try again.", exitscript=exitscript)

    return selected_elements

#>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> GET ROOMS


#>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> GET VIEWS

#>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> GET SHEETS

# ╔═╗╔═╗╦  ╔═╗╔═╗╔╦╗
# ╚═╗║╣ ║  ║╣ ║   ║
# ╚═╝╚═╝╩═╝╚═╝╚═╝ ╩
#==================================================
#>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> SELECT TITLEBLOCK

#>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> GET RegionType

#>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> GET FloorType

#>>>>>>>>> LIMIT SELECTION
# ╦  ╔═╗╔═╗╦  ╔═╗╔═╗╔╦╗╦╔═╗╔╗╔  ╔═╗╦╦ ╔╦╗╔═╗╦═╗
# ║  ╚═╗║╣ ║  ║╣ ║   ║ ║║ ║║║║  ╠╣ ║║  ║ ║╣ ╠╦╝
# ╩  ╚═╝╚═╝╩═╝╚═╝╚═╝ ╩ ╩╚═╝╝╚╝  ╚  ╩╩═╝╩ ╚═╝╩╚═
class CustomISelectionFilter(ISelectionFilter):
    __namespace__ = "T3Lab.Selection"
    """Filter user selection to certain element."""
    def __init__(self, cats):
        self.cats = cats
    def AllowElement(self, e):
        if str(e.Category.Id) == str(self.cats):
        #if e.Category.Name == "Walls"
            return True
        return False

class ISelectionFilter_Classes(ISelectionFilter):
    __namespace__ = "T3Lab.Selection"
    def __init__(self, allowed_types):
        """ ISelectionFilter made to filter with types
        :param allowed_types: list of allowed Types"""
        self.allowed_types = allowed_types

    def AllowElement(self, element):
        if type(element) in self.allowed_types:
            return True


# ╔═╗╦╔═╗╦╔═  ╔═╗╦  ╔═╗╔╦╗╔═╗╔╗╔╔╦╗╔═╗
# ╠═╝║║  ╠╩╗  ║╣ ║  ║╣ ║║║║╣ ║║║ ║ ╚═╗
# ╩  ╩╚═╝╩ ╩  ╚═╝╩═╝╚═╝╩ ╩╚═╝╝╚╝ ╩ ╚═╝
#==================================================
#>>>>>>>>>> PICK WALL
