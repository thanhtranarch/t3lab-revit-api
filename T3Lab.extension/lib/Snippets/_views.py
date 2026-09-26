# -*- coding: utf-8 -*-
"""
Views Snippets

Code snippets for working with Revit views.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Views Snippets"

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝ IMPORTS
# ==================================================
from pyrevit import forms
from Autodesk.Revit.DB import ( Transaction,
                                View,
                                ViewPlan,
                                ViewSection,
                                View3D,
                                ViewSchedule,
                                ViewDrafting,
                                ParameterValueProvider,
                                FilterStringRule,
                                FilterStringEquals,
                                ElementParameterFilter,
                                FilteredElementCollector,
                                BuiltInParameter,
                                BuiltInCategory,
                                ElementId,
                                ViewFamily,
                                ViewFamilyType)

# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝ VARIABLES
# ==================================================
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

# ╔═╗╦ ╦╔╗╔╔═╗╔╦╗╦╔═╗╔╗╔╔═╗
# ╠╣ ║ ║║║║║   ║ ║║ ║║║║╚═╗
# ╚  ╚═╝╝╚╝╚═╝ ╩ ╩╚═╝╝╚╝╚═╝ FUNCTIONS
# ==================================================
def create_string_equals_filter(key_parameter, element_value, caseSensitive = True):
    """Function to create ElementParameterFilter based on FilterStringRule."""
    f_parameter         = ParameterValueProvider(ElementId(key_parameter))
    f_parameter_value   = element_value

    if rvt_year < 2022:
        f_rule = FilterStringRule(f_parameter, FilterStringEquals(), f_parameter_value, caseSensitive)
    else:
        f_rule = FilterStringRule(f_parameter, FilterStringEquals(), f_parameter_value)

    return ElementParameterFilter(f_rule)

def get_sheet_from_view(view):
    #type:(View) -> ViewPlan
    """Function to get ViewSheet associated with the given ViewPlan"""

    #>>>>>>>>>> CREATE FILTER
    my_filter = create_string_equals_filter(key_parameter=BuiltInParameter.SHEET_NUMBER,
                                            element_value=view.get_Parameter(BuiltInParameter.VIEWER_SHEET_NUMBER).AsString() )
    #>>>>>>>>>> GET SHEET
    target_doc = view.Document if (view and hasattr(view, 'Document')) else doc
    return FilteredElementCollector(target_doc).OfCategory(BuiltInCategory.OST_Sheets).WhereElementIsNotElementType().WherePasses(my_filter).FirstElement()

# CREATE VIEW
