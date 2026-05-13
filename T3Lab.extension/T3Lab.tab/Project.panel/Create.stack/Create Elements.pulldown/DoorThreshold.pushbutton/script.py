# -*- coding: utf-8 -*-
"""
Door Threshold
-------------
Create threshold floors under selected doors.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__title__   = "Door Threshold"
__author__  = "Tran Tien Thanh"
__version__ = "1.0.0"

import os
import sys
import clr

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Windows import WindowState
from System.Collections.Generic import List

from rpw import revit, DB
from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    BuiltInCategory,
    FloorType,
    CurveLoop,
    IFailuresPreprocessor,
    FailureProcessingResult,
    BuiltInParameter,
    Floor,
    ElementId,
    XYZ,
    Line
)
from Autodesk.Revit.UI import TaskDialog
from pyrevit import forms, script

# Path setup
SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))))
lib_dir    = os.path.join(EXT_DIR, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

XAML_FILE  = os.path.join(EXT_DIR, 'lib', 'GUI', 'Tools', 'DoorThreshold.xaml')

logger        = script.get_logger()
doc           = revit.doc
uidoc         = revit.uidoc
REVIT_VERSION = int(doc.Application.VersionNumber)

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

class ThresholdCreationWarningSwallower(IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        failList = failuresAccessor.GetFailureMessages()
        for failure in failList:
            failuresAccessor.DeleteWarning(failure)
        return FailureProcessingResult.Continue

class DoorItem(object):
    def __init__(self, door_element):
        self.Element = door_element
        self.IsSelected = False
        
        mark_param = door_element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if not mark_param:
            mark_param = door_element.LookupParameter("Mark")
        
        self.Mark = mark_param.AsString() if (mark_param and mark_param.AsString()) else ""
        
        try:
            self.Name = door_element.Name or door_element.Symbol.Name or ""
        except:
            self.Name = ""
            
        try:
            level = doc.GetElement(door_element.LevelId)
            self.Level = level.Name if (level and level.Name) else ""
        except Exception:
            self.Level = ""

class ThresholdGenerator:
    def __init__(self, doc):
        self.doc = doc

    def generate_thresholds(self, doors, floor_type, offset_mm, ext_w_mm, ext_d_mm):
        offset_ft = offset_mm * MM_TO_FT
        ext_w_ft = ext_w_mm * MM_TO_FT
        ext_d_ft = ext_d_mm * MM_TO_FT
        
        created_count = 0
        error_count = 0
        new_floors = []

        with Transaction(self.doc, "T3Lab: Door Threshold") as t:
            t.Start()
            failOpt = t.GetFailureHandlingOptions()
            failOpt.SetFailuresPreprocessor(ThresholdCreationWarningSwallower())
            t.SetFailureHandlingOptions(failOpt)

            for door in doors:
                try:
                    level_id = door.LevelId
                    level = self.doc.GetElement(level_id)
                    if not level:
                        error_count += 1
                        continue
                    z = level.Elevation

                    # Get width
                    w_param = door.get_Parameter(BuiltInParameter.DOOR_WIDTH)
                    if not w_param:
                        w_param = door.Symbol.get_Parameter(BuiltInParameter.FAMILY_WIDTH_PARAM)
                    
                    if w_param:
                        w = w_param.AsDouble()
                    else:
                        w = 900 * MM_TO_FT # fallback

                    # Get host wall thickness
                    wall = door.Host
                    if wall:
                        thickness = wall.Width
                    else:
                        thickness = 150 * MM_TO_FT # fallback
                    
                    # Geometry
                    loc = door.Location
                    if not loc:
                        error_count += 1
                        continue
                    
                    p = XYZ(loc.Point.X, loc.Point.Y, z)
                    v = door.HandOrientation
                    u = door.FacingOrientation
                    
                    half_w = w / 2.0 + ext_w_ft
                    half_d = thickness / 2.0 + ext_d_ft

                    if half_w < 0.001 or half_d < 0.001:
                        error_count += 1
                        continue

                    # XYZ * float (not float * XYZ) — avoids IronPython __rmul__ issues
                    p1 = p - v * half_w - u * half_d
                    p2 = p + v * half_w - u * half_d
                    p3 = p + v * half_w + u * half_d
                    p4 = p - v * half_w + u * half_d

                    c1 = Line.CreateBound(p1, p2)
                    c2 = Line.CreateBound(p2, p3)
                    c3 = Line.CreateBound(p3, p4)
                    c4 = Line.CreateBound(p4, p1)

                    loop = CurveLoop()
                    loop.Append(c1)
                    loop.Append(c2)
                    loop.Append(c3)
                    loop.Append(c4)

                    new_floor = None

                    if REVIT_VERSION >= 2022:
                        profile = List[CurveLoop]()
                        profile.Add(loop)
                        new_floor = Floor.Create(self.doc, profile, floor_type.Id, level_id)
                    else:
                        from Autodesk.Revit.DB import CurveArray
                        curve_array = CurveArray()
                        curve_array.Append(c1)
                        curve_array.Append(c2)
                        curve_array.Append(c3)
                        curve_array.Append(c4)
                        new_floor = self.doc.Create.NewFloor(curve_array, floor_type, level, False)

                    if new_floor:
                        if abs(offset_ft) > 0.0001:
                            param = new_floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
                            if param: param.Set(offset_ft)

                        new_floors.append(new_floor)
                        created_count += 1

                except Exception as ex:
                    logger.debug("Error creating threshold for door {}: {}".format(door.Id, ex))
                    error_count += 1

            t.Commit()
            
        return new_floors, created_count, error_count

class DoorThresholdWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self.generator = ThresholdGenerator(doc)
        self._all_doors = []
        self._load_doors()
        self._load_floor_types()
        self._update_status()

    def _load_doors(self):
        # Allow checking pre-selected doors
        selected_ids = uidoc.Selection.GetElementIds()
        pre_selected = set()
        for eid in selected_ids:
            val = eid.Value if hasattr(eid, "Value") else eid.IntegerValue
            pre_selected.add(val)

        try:
            door_elements = FilteredElementCollector(doc, doc.ActiveView.Id) \
                .OfCategory(BuiltInCategory.OST_Doors) \
                .WhereElementIsNotElementType() \
                .ToElements()
        except:
            door_elements = []
        
        # If no doors in active view, maybe get all doors in project
        if not door_elements:
             door_elements = FilteredElementCollector(doc) \
                .OfCategory(BuiltInCategory.OST_Doors) \
                .WhereElementIsNotElementType() \
                .ToElements()

        self._all_doors = []
        for d in door_elements:
            item = DoorItem(d)
            val = d.Id.Value if hasattr(d.Id, "Value") else d.Id.IntegerValue
            if val in pre_selected:
                item.IsSelected = True
            self._all_doors.append(item)

        self._all_doors.sort(key=lambda x: (x.Level, x.Mark))
        self.door_datagrid.ItemsSource = self._all_doors

    def _load_floor_types(self):
        floor_types = FilteredElementCollector(doc) \
            .OfClass(FloorType) \
            .ToElements()
        
        self._floor_type_map = {}
        for t in floor_types:
            try:
                type_name = t.LookupParameter("Type Name").AsString() if t.LookupParameter("Type Name") else t.Name
            except:
                type_name = t.Name
            
            if not type_name:
                type_name = ""
            
            fam_name = t.FamilyName if t.FamilyName else ""
            key = "{}: {}".format(fam_name, type_name)
            self._floor_type_map[key] = t
        
        sorted_names = sorted(self._floor_type_map.keys())
        for name in sorted_names:
            self.cmb_floor_type.Items.Add(name)
        
        if self.cmb_floor_type.Items.Count > 0:
            self.cmb_floor_type.SelectedIndex = 0

    def _get_selected_doors(self):
        return [d for d in self._all_doors if d.IsSelected]

    def _update_status(self):
        selected = len(self._get_selected_doors())
        total = len(self._all_doors)
        self.status_count.Text = "{} doors".format(total)
        self.status_text.Text = "{} door(s) selected".format(selected) if selected > 0 else "Ready"

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Normal if self.WindowState == WindowState.Maximized else WindowState.Maximized

    def close_button_clicked(self, sender, e):
        self.Close()

    def select_all_clicked(self, sender, e):
        for d in self._all_doors: d.IsSelected = True
        self.door_datagrid.Items.Refresh()
        self._update_status()

    def select_none_clicked(self, sender, e):
        for d in self._all_doors: d.IsSelected = False
        self.door_datagrid.Items.Refresh()
        self._update_status()

    def search_changed(self, sender, e):
        query = self.txt_search.Text.strip().upper()
        if not query:
            self.door_datagrid.ItemsSource = self._all_doors
        else:
            self.door_datagrid.ItemsSource = [
                d for d in self._all_doors 
                if query in d.Name.upper() or query in d.Mark.upper() or query in d.Level.upper()
            ]
        self._update_status()

    def create_thresholds_clicked(self, sender, e):
        selected_doors = self._get_selected_doors()
        if not selected_doors:
            TaskDialog.Show("Door Threshold", "Please select at least one door.")
            return

        floor_type_name = self.cmb_floor_type.SelectedItem
        if not floor_type_name:
            TaskDialog.Show("Door Threshold", "Please select a threshold type (floor).")
            return
        
        floor_type = self._floor_type_map[floor_type_name]
        
        try: offset_mm = float(self.txt_offset.Text)
        except: offset_mm = 0
            
        try: ext_w_mm = float(self.txt_width_ext.Text)
        except: ext_w_mm = 0
            
        try: ext_d_mm = float(self.txt_depth_ext.Text)
        except: ext_d_mm = 0

        new_floors, created, errors = self.generator.generate_thresholds(
            [d.Element for d in selected_doors],
            floor_type,
            offset_mm, ext_w_mm, ext_d_mm
        )
        
        if new_floors:
            try:
                uidoc.Selection.SetElementIds(List[ElementId]([f.Id for f in new_floors if f.IsValidObject]))
            except Exception as ex:
                logger.debug("Failed to select created floors: {}".format(ex))

        msg = "Successfully created {} thresholds.".format(created)
        if errors > 0:
            msg += "\n{} errors occurred.".format(errors)
        
        TaskDialog.Show("Door Threshold", msg)
        self.Close()

if __name__ == '__main__':
    try:
        window = DoorThresholdWindow()
        window.ShowDialog()
    except Exception as ex:
        logger.error("Door Threshold error: {}".format(ex))
        import traceback
        logger.error(traceback.format_exc())
