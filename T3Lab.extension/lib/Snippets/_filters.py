# -*- coding: utf-8 -*-
"""
Filters Snippets

Code snippets for Revit element filter classes.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Filters Snippets"

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝ IMPORTS
# ==================================================

from Autodesk.Revit.DB import *
from pyrevit.forms import alert
# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
try:
    from Snippets._host import resolve_doc, host_uiapp, get_revit_version
    doc, _doc_err = resolve_doc()
    uiapp = host_uiapp()
    uidoc = uiapp.ActiveUIDocument if uiapp else None
    app   = uiapp.Application if uiapp else None
except Exception:
    doc = None
    uidoc = None
    app = None

# ╔═╗╦ ╦╔╗╔╔═╗╔╦╗╦╔═╗╔╗╔╔═╗
# ╠╣ ║ ║║║║║   ║ ║║ ║║║║╚═╗
# ╚  ╚═╝╝╚╝╚═╝ ╩ ╩╚═╝╝╚╝╚═╝ FUNCTIONS
# ==================================================
def create_filter(key_parameter, element_value):
    """Function to create a RevitAPI filter."""
    f_parameter = ParameterValueProvider(ElementId(key_parameter))
    f_parameter_value = element_value  # e.g. element.Category.Id
    f_rule = FilterElementIdRule(f_parameter, FilterNumericEquals(), f_parameter_value)
    filter = ElementParameterFilter(f_rule)
    return filter

# EXAMPLE GET GROUP INSTANCE
# filter = create_filter(BuiltInParameter.ELEM_TYPE_PARAM, group_type_id)
# group = FilteredElementCollector(doc).WherePasses(filter).FirstElement()

