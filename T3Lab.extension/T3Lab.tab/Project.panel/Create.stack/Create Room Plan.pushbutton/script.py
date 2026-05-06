# -*- coding: utf-8 -*-
"""
Create Room Plan

Create Plan Views from Room List with WPF UI.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Create Plan Views"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==============================================================================
import os
import sys
import clr
import re

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Windows import WindowState, Visibility
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind

from rpw import revit, DB
from Autodesk.Revit.DB import (
    Transaction,
    View,
    FilteredElementCollector,
    BuiltInCategory,
    ViewType,
    ViewFamilyType,
    ViewFamily,
    ViewPlan,
    ElevationMarker,
    SpatialElementBoundaryOptions,
    SpatialElementBoundaryLocation,
)
from Autodesk.Revit.UI import TaskDialog
from pyrevit import forms, script

SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))
lib_dir    = os.path.join(EXT_DIR, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

XAML_FILE  = os.path.join(EXT_DIR, 'lib', 'GUI', 'Tools', 'CreateRoomPlan.xaml')

# DEFINE VARIABLES
# ==============================================================================
logger        = script.get_logger()
output        = script.get_output()
uidoc         = revit.uidoc
doc           = revit.doc
REVIT_VERSION = int(revit.doc.Application.VersionNumber)


# CLASS/FUNCTIONS
# ==============================================================================

class RoomItem(object):
    """Represents a room item in the DataGrid."""
    def __init__(self, room_element):
        self.Element = room_element
        self.IsSelected = False
        self.Number = room_element.LookupParameter("Number").AsString() or ""
        self.Name = room_element.LookupParameter("Name").AsString() or ""

        type_param = room_element.LookupParameter("Room Type")
        self.RoomType = type_param.AsString() if type_param else ""

        try:
            level = doc.GetElement(room_element.LevelId)
            self.Level = level.Name if level else ""
        except Exception:
            self.Level = ""


class CreateRoomPlanWindow(forms.WPFWindow):
    """WPF window for creating plan views from rooms."""

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self._all_rooms = []
        self._load_rooms()
        self._load_view_templates()
        self._load_plan_type_options()
        self._update_status()

    # ── Logo ──────────────────────────────────────────
# MAIN SCRIPT
# ==============================================================================
if __name__ == '__main__':
    try:
        window = CreateRoomPlanWindow()
        window.ShowDialog()
    except Exception as ex:
        logger.error("Create Room Plan error: {}".format(ex))
        import traceback
        logger.error(traceback.format_exc())
