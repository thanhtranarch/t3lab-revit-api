# -*- coding: utf-8 -*-
"""
Room to Floor
-------------
Create architectural or structural floors from selected room boundaries.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__title__   = "Room To Floor"
__author__  = "Tran Tien Thanh"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==============================================================================
import os
import sys
import clr
import json

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Windows import WindowState
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind
from System.Collections.Generic import List

from rpw import revit, DB
from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    BuiltInCategory,
    FloorType,
    SpatialElementBoundaryOptions,
    SpatialElementBoundaryLocation,
    CurveLoop,
    CurveArray,
    IFailuresPreprocessor,
    FailureProcessingResult,
    BuiltInParameter,
    Floor,
    ElementId
)
from Autodesk.Revit.UI import TaskDialog
from pyrevit import forms, script

# Path setup
SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))))
lib_dir    = os.path.join(EXT_DIR, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

XAML_FILE  = os.path.join(EXT_DIR, 'lib', 'GUI', 'Tools', 'RoomToFloor.xaml')

# DEFINE VARIABLES
# ==============================================================================
logger        = script.get_logger()
doc           = revit.doc
uidoc         = revit.uidoc
REVIT_VERSION = int(doc.Application.VersionNumber)

# CLASS/FUNCTIONS
# ==============================================================================

class FloorsCreationWarningSwallower(IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        failList = failuresAccessor.GetFailureMessages()
        for failure in failList:
            failuresAccessor.DeleteWarning(failure)
        return FailureProcessingResult.Continue

class FloorGenerator:
    def __init__(self, doc):
        self.doc = doc

    def generate_floors(self, room_elements, floor_type, offset_mm=0.0, is_structural=False, use_finish=True):
        offset_ft = offset_mm / 304.8
        created_count = 0
        error_count = 0
        new_floors = []

        with Transaction(self.doc, "T3Lab: Room to Floor") as t:
            t.Start()
            failOpt = t.GetFailureHandlingOptions()
            failOpt.SetFailuresPreprocessor(FloorsCreationWarningSwallower())
            t.SetFailureHandlingOptions(failOpt)

            for room in room_elements:
                try:
                    level_id = room.LevelId
                    level = self.doc.GetElement(level_id)
                    
                    # Boundary options
                    opt = SpatialElementBoundaryOptions()
                    opt.SpatialElementBoundaryLocation = (
                        SpatialElementBoundaryLocation.Finish if use_finish 
                        else SpatialElementBoundaryLocation.Center
                    )
                    
                    room_boundaries = room.GetBoundarySegments(opt)
                    if not room_boundaries:
                        error_count += 1
                        continue

                    new_floor = None

                    if REVIT_VERSION >= 2022:
                        profile = List[CurveLoop]()
                        for loop in room_boundaries:
                            curve_loop = CurveLoop()
                            for seg in loop:
                                curve_loop.Append(seg.GetCurve())
                            profile.Add(curve_loop)

                        new_floor = Floor.Create(self.doc, profile, floor_type.Id, level_id)
                    else:
                        floor_shape = room_boundaries[0]
                        openings = list(room_boundaries)[1:] if len(room_boundaries) > 1 else []

                        curve_array = CurveArray()
                        for seg in floor_shape:
                            curve_array.Append(seg.GetCurve())
                        
                        new_floor = self.doc.Create.NewFloor(curve_array, floor_type, level, is_structural)
                        
                        # Create Floor Openings separately for older versions
                        if new_floor and openings:
                            for opening in openings:
                                opening_curve = CurveArray()
                                for seg in opening:
                                    opening_curve.Append(seg.GetCurve())
                                self.doc.Create.NewOpening(new_floor, opening_curve, True)

                    if new_floor:
                        if abs(offset_ft) > 0.0001:
                            param = new_floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
                            if param: param.Set(offset_ft)
                        
                        # Set structural parameter if API is >= 2022 (older uses parameter in NewFloor)
                        if is_structural and REVIT_VERSION >= 2022:
                            struct_param = new_floor.get_Parameter(BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
                            if struct_param: struct_param.Set(1)

                        new_floors.append(new_floor)
                        created_count += 1
                except Exception as ex:
                    logger.debug("Error creating floor for room: {}".format(ex))
                    error_count += 1
            
            t.Commit()
            
        return new_floors, created_count, error_count


class RoomItem(object):
    """Represents a room item in the DataGrid."""
    def __init__(self, room_element):
        self.Element = room_element
        self.IsSelected = False
        self.Number = room_element.LookupParameter("Number").AsString() or ""
        self.Name = room_element.LookupParameter("Name").AsString() or ""
        try:
            level = doc.GetElement(room_element.LevelId)
            self.Level = level.Name if level else ""
        except Exception:
            self.Level = ""


class RoomToFloorWindow(forms.WPFWindow):
    """WPF window for creating floors from rooms."""

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self.generator = FloorGenerator(doc)
        self._all_rooms = []
        self._load_rooms()
        self._load_floor_types()
        self._update_status()

    def _load_rooms(self):
        room_elements = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_Rooms) \
            .ToElements()

        self._all_rooms = []
        for r in room_elements:
            if r.Location is None:
                continue
            self._all_rooms.append(RoomItem(r))

        self._all_rooms.sort(key=lambda x: (x.Level, x.Number))
        self.room_datagrid.ItemsSource = self._all_rooms

    def _load_floor_types(self):
        floor_types = FilteredElementCollector(doc) \
            .OfClass(FloorType) \
            .ToElements()
        
        self._floor_type_map = { "{}: {}".format(t.FamilyName, t.LookupParameter("Type Name").AsString()): t for t in floor_types }
        
        sorted_names = sorted(self._floor_type_map.keys())
        for name in sorted_names:
            self.cmb_floor_type.Items.Add(name)
        
        if self.cmb_floor_type.Items.Count > 0:
            self.cmb_floor_type.SelectedIndex = 0

    def _get_selected_rooms(self):
        return [r for r in self._all_rooms if r.IsSelected]

    def _update_status(self):
        selected = len(self._get_selected_rooms())
        total = len(self._all_rooms)
        self.status_count.Text = "{} rooms".format(total)
        self.status_text.Text = "{} room(s) selected".format(selected) if selected > 0 else "Ready"

    # ── UI Handlers ──────────────────────────────────
    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Normal if self.WindowState == WindowState.Maximized else WindowState.Maximized

    def close_button_clicked(self, sender, e):
        self.Close()

    def select_all_clicked(self, sender, e):
        for r in self._all_rooms: r.IsSelected = True
        self.room_datagrid.Items.Refresh()
        self._update_status()

    def select_none_clicked(self, sender, e):
        for r in self._all_rooms: r.IsSelected = False
        self.room_datagrid.Items.Refresh()
        self._update_status()

    def search_changed(self, sender, e):
        query = self.txt_search.Text.strip().upper()
        if not query:
            self.room_datagrid.ItemsSource = self._all_rooms
        else:
            self.room_datagrid.ItemsSource = [
                r for r in self._all_rooms 
                if query in r.Name.upper() or query in r.Number.upper() or query in r.Level.upper()
            ]
        self._update_status()

    def create_floors_clicked(self, sender, e):
        selected_rooms = self._get_selected_rooms()
        if not selected_rooms:
            TaskDialog.Show("Room to Floor", "Please select at least one room.")
            return

        floor_type_name = self.cmb_floor_type.SelectedItem
        if not floor_type_name:
            TaskDialog.Show("Room to Floor", "Please select a floor type.")
            return
        
        floor_type = self._floor_type_map[floor_type_name]
        
        try:
            offset_mm = float(self.txt_offset.Text)
        except:
            offset_mm = 0

        is_structural = self.chk_structural.IsChecked
        use_finish = self.chk_room_finish.IsChecked

        new_floors, created, errors = self.generator.generate_floors(
            [r.Element for r in selected_rooms],
            floor_type,
            offset_mm,
            is_structural,
            use_finish
        )
        
        if new_floors:
            try:
                uidoc.Selection.SetElementIds(List[ElementId]([f.Id for f in new_floors if f.IsValidObject]))
            except Exception as e:
                logger.debug("Failed to select created floors: {}".format(e))

        msg = "Successfully created {} floors.".format(created)
        if errors > 0:
            msg += "\n{} errors occurred.".format(errors)
        
        TaskDialog.Show("Room to Floor", msg)
        self.Close()


def run_headless(args_json):
    try:
        data = json.loads(args_json)
        gen = FloorGenerator(doc)
        
        room_ids = data.get("room_ids", [])
        type_id = data.get("type_id")
        offset = data.get("offset_mm", 0)
        structural = data.get("structural", False)
        use_finish = data.get("use_finish", True)
        
        rooms = [doc.GetElement(ElementId(int(rid))) for rid in room_ids]
        floor_type = doc.GetElement(ElementId(int(type_id)))
        
        new_floors, created, errors = gen.generate_floors(rooms, floor_type, offset, structural, use_finish)
        print(json.dumps({"status": "success", "created": created, "errors": errors}))
    except Exception as ex:
        print(json.dumps({"status": "error", "message": str(ex)}))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        run_headless(sys.argv[1])
    else:
        try:
            window = RoomToFloorWindow()
            window.ShowDialog()
        except Exception as ex:
            logger.error("Room to Floor error: {}".format(ex))
            import traceback
            logger.error(traceback.format_exc())
