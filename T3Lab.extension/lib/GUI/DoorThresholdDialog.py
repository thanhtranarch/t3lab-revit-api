# -*- coding: utf-8 -*-
"""DoorThreshold — Dialog and logic for generating threshold floors under doors."""

import os
import sys

from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Snippets._host import get_revit_version
from Snippets._compat import eid_value

try:
    from GUI import RevitTheme as _theme
except Exception:
    _theme = None

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

from System.Windows import WindowState
from System.Collections.Generic import List
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
    Line,
    HostObjectUtils,
    ShellLayerType,
    Wall,
)
from Autodesk.Revit.UI import TaskDialog

XAML_FILE = os.path.join(os.path.dirname(__file__), 'Tools', 'DoorThreshold.xaml')
logger = script.get_logger()

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8
REVIT_VERSION = get_revit_version()


class ThresholdCreationWarningSwallower(IFailuresPreprocessor):
    __namespace__ = "T3Lab.DoorThreshold"

    def PreprocessFailures(self, failuresAccessor):
        failList = failuresAccessor.GetFailureMessages()
        for failure in failList:
            failuresAccessor.DeleteWarning(failure)
        return FailureProcessingResult.Continue


class DoorItem(object):
    def __init__(self, door_element, document=None):
        self.Element = door_element
        self.IsSelected = False

        mark_param = door_element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if not mark_param:
            mark_param = door_element.LookupParameter("Mark")

        self.Mark = mark_param.AsString() if (mark_param and mark_param.AsString()) else ""

        try:
            self.Name = door_element.Name or door_element.Symbol.Name or ""
        except Exception:
            self.Name = ""

        try:
            doc = document or door_element.Document
            level = doc.GetElement(door_element.LevelId)
            self.Level = level.Name if (level and level.Name) else ""
        except Exception:
            self.Level = ""


def _get_wall_thickness_at_point(wall, point):
    """Measure the actual wall thickness at a given point by projecting it
    onto the wall's exterior and interior side faces."""
    try:
        if not isinstance(wall, Wall):
            return None

        ext_refs = HostObjectUtils.GetSideFaces(wall, ShellLayerType.Exterior)
        int_refs = HostObjectUtils.GetSideFaces(wall, ShellLayerType.Interior)
        if not ext_refs or not int_refs or ext_refs.Count == 0 or int_refs.Count == 0:
            return None

        ext_face = wall.GetGeometryObjectFromReference(ext_refs[0])
        int_face = wall.GetGeometryObjectFromReference(int_refs[0])
        if not ext_face or not int_face:
            return None

        ext_proj = ext_face.Project(point)
        int_proj = int_face.Project(point)
        if not ext_proj or not int_proj:
            return None

        thickness = ext_proj.XYZPoint.DistanceTo(int_proj.XYZPoint)
        if thickness > 0.001:
            return thickness
    except Exception:
        pass
    return None


def _get_door_width_ft(door):
    """Get the door's actual width in feet, trying parameters first and
    falling back to measuring the instance's bounding box along its HandOrientation axis."""
    for param in (
        lambda: door.get_Parameter(BuiltInParameter.DOOR_WIDTH),
        lambda: door.Symbol.get_Parameter(BuiltInParameter.FAMILY_WIDTH_PARAM),
        lambda: door.LookupParameter("Width"),
        lambda: door.Symbol.LookupParameter("Width"),
    ):
        try:
            p = param()
            if p and p.AsDouble() > 0.001:
                return p.AsDouble()
        except Exception:
            pass

    try:
        bbox = door.get_BoundingBox(None)
        if bbox:
            v = door.HandOrientation
            corners = [
                XYZ(bbox.Min.X, bbox.Min.Y, bbox.Min.Z),
                XYZ(bbox.Max.X, bbox.Min.Y, bbox.Min.Z),
                XYZ(bbox.Min.X, bbox.Max.Y, bbox.Min.Z),
                XYZ(bbox.Max.X, bbox.Max.Y, bbox.Min.Z),
            ]
            projections = [c.DotProduct(v) for c in corners]
            width = max(projections) - min(projections)
            if width > 0.001:
                return width
    except Exception:
        pass

    return 900 * MM_TO_FT


class ThresholdGenerator(object):
    def __init__(self, doc):
        self.doc = doc

    def generate_thresholds(self, doors, floor_type, offset_mm):
        offset_ft = offset_mm * MM_TO_FT

        created_count = 0
        error_count = 0
        new_floors = []
        error_messages = []

        with Transaction(self.doc, "T3Lab: Door Threshold") as t:
            t.Start()
            failOpt = t.GetFailureHandlingOptions()
            failOpt.SetFailuresPreprocessor(ThresholdCreationWarningSwallower())
            t.SetFailureHandlingOptions(failOpt)

            for door in doors:
                mark_param = door.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                door_label = "Door {}".format(mark_param.AsString() if (mark_param and mark_param.AsString()) else eid_value(door.Id))
                try:
                    level_id = door.LevelId
                    level = self.doc.GetElement(level_id)
                    if not level:
                        error_count += 1
                        error_messages.append("{}: no level found".format(door_label))
                        continue
                    z = level.Elevation

                    w = _get_door_width_ft(door)

                    loc = door.Location
                    if not loc:
                        error_count += 1
                        error_messages.append("{}: no location point".format(door_label))
                        continue

                    p = XYZ(loc.Point.X, loc.Point.Y, z)

                    wall = door.Host
                    if wall:
                        thickness = _get_wall_thickness_at_point(wall, p)
                        if thickness is None:
                            thickness = wall.Width
                    else:
                        thickness = 150 * MM_TO_FT

                    v = door.HandOrientation
                    u = door.FacingOrientation

                    half_w = w / 2.0
                    half_d = thickness / 2.0

                    if half_w < 0.001 or half_d < 0.001:
                        error_count += 1
                        error_messages.append(
                            "{}: invalid size (width={:.1f}mm, thickness={:.1f}mm)".format(
                                door_label, w * FT_TO_MM, thickness * FT_TO_MM))
                        continue

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
                            if param:
                                param.Set(offset_ft)

                        new_floors.append(new_floor)
                        created_count += 1

                except Exception as ex:
                    logger.error("Error creating threshold for {}: {}".format(door_label, ex))
                    error_messages.append("{}: {}".format(door_label, ex))
                    error_count += 1

            t.Commit()

        return new_floors, created_count, error_count, error_messages


class DoorThresholdWindow(T3WPFWindow):
    def __init__(self, doc=None, uidoc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self._doc = doc or revit.doc
        self._uidoc = uidoc
        if self._uidoc is None:
            try:
                self._uidoc = revit.uidoc
            except Exception:
                self._uidoc = None

        self.generator = ThresholdGenerator(self._doc)
        self._all_doors = []
        self._floor_type_map = {}

        self._adopt_host_font()
        self._apply_theme()
        self._load_doors()
        self._load_floor_types()
        self._update_status()

    def _adopt_host_font(self):
        if _theme is None:
            return
        family, size = _theme.host_font()
        if family:
            try:
                self.FontFamily = family
                if size and size > 0:
                    self.FontSize = size
            except Exception:
                pass

    def _apply_theme(self, theme=None):
        if _theme is None:
            return
        try:
            _theme.apply(self, theme)
        except Exception:
            pass

    def _load_doors(self):
        pre_selected = set()
        if self._uidoc:
            try:
                selected_ids = self._uidoc.Selection.GetElementIds()
                for eid in selected_ids:
                    pre_selected.add(eid_value(eid))
            except Exception:
                pass

        try:
            door_elements = FilteredElementCollector(self._doc, self._doc.ActiveView.Id) \
                .OfCategory(BuiltInCategory.OST_Doors) \
                .WhereElementIsNotElementType() \
                .ToElements()
        except Exception:
            door_elements = []

        if not door_elements:
            try:
                door_elements = FilteredElementCollector(self._doc) \
                    .OfCategory(BuiltInCategory.OST_Doors) \
                    .WhereElementIsNotElementType() \
                    .ToElements()
            except Exception:
                door_elements = []

        self._all_doors = []
        for d in door_elements:
            item = DoorItem(d, self._doc)
            if eid_value(d.Id) in pre_selected:
                item.IsSelected = True
            self._all_doors.append(item)

        self._all_doors.sort(key=lambda x: (x.Level, x.Mark))
        self.door_datagrid.ItemsSource = to_items_source(self._all_doors)

    def _load_floor_types(self):
        try:
            floor_types = FilteredElementCollector(self._doc) \
                .OfClass(FloorType) \
                .ToElements()
        except Exception:
            floor_types = []

        self._floor_type_map = {}
        for t in floor_types:
            try:
                type_name = t.LookupParameter("Type Name").AsString() if t.LookupParameter("Type Name") else t.Name
            except Exception:
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
        for d in self._all_doors:
            d.IsSelected = True
        self.door_datagrid.Items.Refresh()
        self._update_status()
        self.sync_header_checkbox(
            self.FindName("chk_all_door_datagrid"), self.door_datagrid, "IsSelected")

    def select_none_clicked(self, sender, e):
        for d in self._all_doors:
            d.IsSelected = False
        self.door_datagrid.Items.Refresh()
        self._update_status()
        self.sync_header_checkbox(
            self.FindName("chk_all_door_datagrid"), self.door_datagrid, "IsSelected")

    def search_changed(self, sender, e):
        query = self.txt_search.Text.strip().upper()
        if not query:
            self.door_datagrid.ItemsSource = to_items_source(self._all_doors)
        else:
            self.door_datagrid.ItemsSource = to_items_source([
                d for d in self._all_doors
                if query in d.Name.upper() or query in d.Mark.upper() or query in d.Level.upper()
            ])
        self._update_status()

    def create_thresholds_clicked(self, sender, e):
        selected_doors = self._get_selected_doors()
        if not selected_doors:
            TaskDialog.Show("Door Threshold", "Please select at least one door.")
            return

        floor_type_name = self.cmb_floor_type.SelectedItem
        if not floor_type_name:
            if not self._floor_type_map:
                TaskDialog.Show(
                    "Door Threshold",
                    "This model has no floor types to use as a threshold.\n\n"
                    "Load or create at least one floor type, then reopen this tool.")
            else:
                TaskDialog.Show("Door Threshold", "Please select a threshold type (floor).")
            return

        floor_type = self._floor_type_map.get(floor_type_name)
        if floor_type is None:
            TaskDialog.Show("Door Threshold",
                            "The selected threshold type is no longer available.")
            return

        try:
            offset_mm = float(self.txt_offset.Text)
        except Exception:
            offset_mm = 0

        new_floors, created, errors, error_messages = self.generator.generate_thresholds(
            [d.Element for d in selected_doors],
            floor_type,
            offset_mm
        )

        if new_floors and self._uidoc:
            try:
                self._uidoc.Selection.SetElementIds(List[ElementId]([f.Id for f in new_floors if f.IsValidObject]))
            except Exception as ex:
                logger.debug("Failed to select created floors: {}".format(ex))

        msg = "Successfully created {} thresholds.".format(created)
        if errors > 0:
            msg += "\n{} errors occurred:".format(errors)
            for err in error_messages[:10]:
                msg += "\n- {}".format(err)
            if len(error_messages) > 10:
                msg += "\n... and {} more".format(len(error_messages) - 10)

        TaskDialog.Show("Door Threshold", msg)
        self.Close()

    def select_all_door_datagrid_clicked(self, sender, e):
        """Header checkbox: select/deselect all rows currently shown in door_datagrid."""
        self.toggle_all_rows(self.door_datagrid, "IsSelected", sender.IsChecked)


def show_door_threshold_dialog(doc=None, uidoc=None):
    d = doc or revit.doc
    if not d:
        forms.alert("No active Revit document found.", title="Door Threshold")
        return
    u = uidoc
    if u is None:
        try:
            u = revit.uidoc
        except Exception:
            u = None
    win = DoorThresholdWindow(d, u)
    win.ShowDialog()
