# -*- coding: utf-8 -*-
"""
Convert Snippets

Code snippets for unit conversion in Revit.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Convert Snippets"

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝ IMPORTS
# ==================================================
from Autodesk.Revit.DB import *

# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝ VARIABLES
# ==================================================
# `__revit__` members are unavailable when no UIDocument is active, and at
# module scope that kills the import outright. Resolve defensively; the entry
# point reports the real problem (see Snippets._host.resolve_doc()).
try:
    from Snippets._host import get_revit_version
    rvt_year = get_revit_version()
except Exception:
    try:
        app = __revit__.Application
        rvt_year = int(app.VersionNumber)
    except Exception:
        rvt_year = 2024


# ╔═╗╦ ╦╔╗╔╔═╗╔╦╗╦╔═╗╔╗╔╔═╗
# ╠╣ ║ ║║║║║   ║ ║║ ║║║║╚═╗
# ╚  ╚═╝╝╚╝╚═╝ ╩ ╩╚═╝╝╚╝╚═╝ FUNCTIONS
# ==================================================

# ╔═╗╔╗ ╔═╗╔═╗╦  ╔═╗╔╦╗╔═╗
# ║ ║╠╩╗╚═╗║ ║║  ║╣  ║ ║╣
# ╚═╝╚═╝╚═╝╚═╝╩═╝╚═╝ ╩ ╚═╝ OBSOLETE ( still need to refactor the code)
def convert_cm_to_feet(length):
    """Function to convert cm to feet."""

    # RVT >= 2022
    if rvt_year < 2022:
        from Autodesk.Revit.DB import DisplayUnitType
        return UnitUtils.Convert(length,
                                DisplayUnitType.DUT_CENTIMETERS,
                                DisplayUnitType.DUT_DECIMAL_FEET)
    # RVT >= 2022
    else:
        from Autodesk.Revit.DB import UnitTypeId
        return UnitUtils.ConvertToInternalUnits(length, UnitTypeId.Centimeters)

