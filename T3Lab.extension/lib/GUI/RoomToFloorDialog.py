# -*- coding: utf-8 -*-
"""RoomToFloor — Dialog and logic for generating floors from room boundaries."""

import os
import sys
import json

from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Snippets._host import get_revit_version
from Snippets._compat import make_eid, eid_value

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
    TransactionGroup,
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
    ElementId,
)
from Autodesk.Revit.UI import TaskDialog

XAML_FILE = os.path.join(os.path.dirname(__file__), 'Tools', 'RoomToFloor.xaml')
logger = script.get_logger()
REVIT_VERSION = get_revit_version()


class FloorsCreationWarningSwallower(IFailuresPreprocessor):
    __namespace__ = "T3Lab.RoomToFloor"

    def PreprocessFailures(self, failuresAccessor):
        failList = failuresAccessor.GetFailureMessages()
        for failure in failList:
            failuresAccessor.DeleteWarning(failure)
        return FailureProcessingResult.Continue


class FloorGenerator(object):
    def __init__(self, doc):
        self.doc = doc

    def generate_floors(self, room_elements, floor_type, offset_mm=0.0, is_structural=False, use_finish=True,
                        progress_callback=None, cancel_check=None):
        offset_ft = offset_mm / 304.8
        is_structural_bool = bool(is_structural) if is_structural is not None else False
        created_count = 0
        error_count = 0
        new_floors = []

        with TransactionGroup(self.doc, "T3Lab: Room to Floor") as tg:
            tg.Start()
            total = len(room_elements)
            for idx, room in enumerate(room_elements):
                if cancel_check and cancel_check():
                    break
                if progress_callback:
                    progress_callback(idx, total)
                floor = self._create_one_floor(room, floor_type, offset_ft, is_structural_bool, use_finish)
                if floor:
                    new_floors.append(floor)
                    created_count += 1
                else:
                    error_count += 1
            tg.Assimilate()

        return new_floors, created_count, error_count

    def _create_one_floor(self, room, floor_type, offset_ft, is_structural_bool, use_finish):
        """Create a floor for a single room in its own Transaction. Returns floor or None."""
        try:
            area_param = room.get_Parameter(BuiltInParameter.ROOM_AREA)
            if not area_param or not area_param.AsDouble():
                return None

            level_id = room.LevelId
            level = self.doc.GetElement(level_id)
            if not level:
                return None

            opt = SpatialElementBoundaryOptions()
            opt.SpatialElementBoundaryLocation = (
                SpatialElementBoundaryLocation.Finish if use_finish
                else SpatialElementBoundaryLocation.Center
            )

            room_boundaries = room.GetBoundarySegments(opt)
            if not room_boundaries:
                return None

            new_floor = None

            if REVIT_VERSION >= 2022:
                with Transaction(self.doc, "T3Lab: Create Floor") as t:
                    t.Start()
                    profile = List[CurveLoop]()
                    for loop in room_boundaries:
                        curve_loop = CurveLoop()
                        for seg in loop:
                            curve = seg.GetCurve()
                            if curve:
                                curve_loop.Append(curve)
                        if not curve_loop.IsOpen():
                            profile.Add(curve_loop)

                    if not profile.Count:
                        return None

                    new_floor = Floor.Create(self.doc, profile, floor_type.Id, level_id)

                    if new_floor:
                        if abs(offset_ft) > 0.0001:
                            param = new_floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
                            if param:
                                param.Set(offset_ft)
                        if is_structural_bool:
                            struct_param = new_floor.get_Parameter(BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
                            if struct_param:
                                struct_param.Set(1)

                    failOpt = t.GetFailureHandlingOptions()
                    failOpt.SetFailuresPreprocessor(FloorsCreationWarningSwallower())
                    t.SetFailureHandlingOptions(failOpt)
                    t.Commit()

            else:
                floor_shape = room_boundaries[0]
                openings = list(room_boundaries)[1:] if len(room_boundaries) > 1 else []

                with Transaction(self.doc, "T3Lab: Create Floor") as t:
                    t.Start()
                    curve_array = CurveArray()
                    for seg in floor_shape:
                        curve = seg.GetCurve()
                        if curve:
                            curve_array.Append(curve)

                    new_floor = self.doc.Create.NewFloor(curve_array, floor_type, level, is_structural_bool)

                    if new_floor and abs(offset_ft) > 0.0001:
                        param = new_floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
                        if param:
                            param.Set(offset_ft)

                    failOpt = t.GetFailureHandlingOptions()
                    failOpt.SetFailuresPreprocessor(FloorsCreationWarningSwallower())
                    t.SetFailureHandlingOptions(failOpt)
                    t.Commit()

                if new_floor and openings:
                    with Transaction(self.doc, "T3Lab: Create Floor Openings") as t2:
                        t2.Start()
                        for opening in openings:
                            try:
                                opening_curve = CurveArray()
                                for seg in opening:
                                    curve = seg.GetCurve()
                                    if curve:
                                        opening_curve.Append(curve)
                                self.doc.Create.NewOpening(new_floor, opening_curve, True)
                            except Exception:
                                pass
                        t2.Commit()

            return new_floor

        except Exception as ex:
            logger.debug("Error creating floor for room: {}".format(ex))
            return None


class RoomItem(object):
    """Represents a room item in the DataGrid."""
    def __init__(self, room_element, document=None):
        self.Element = room_element
        self.IsSelected = False
        p_num = room_element.LookupParameter("Number")
        self.Number = p_num.AsString() if p_num else ""
        p_name = room_element.LookupParameter("Name")
        self.Name = p_name.AsString() if p_name else ""
        try:
            doc = document or room_element.Document
            level = doc.GetElement(room_element.LevelId)
            self.Level = level.Name if level else ""
        except Exception:
            self.Level = ""


class RoomToFloorWindow(T3WPFWindow):
    """WPF window for creating floors from rooms."""

    PP_PANEL      = "r2f_progress_panel"
    PP_BAR        = "r2f_pb"
    PP_PAUSE      = "r2f_btn_pause"
    PP_STOP       = "r2f_btn_stop"
    PP_PAUSE_ICON = "r2f_btn_pause_icon"
    PP_PAUSE_TEXT = "r2f_btn_pause_label"
    PP_STATUS     = "status_text"
    PP_STOP_MSG   = u"Stopping… finishing current room"

    def __init__(self, doc=None, uidoc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self._doc = doc or revit.doc
        self._uidoc = uidoc
        if self._uidoc is None:
            try:
                self._uidoc = revit.uidoc
            except Exception:
                self._uidoc = None

        self.generator = FloorGenerator(self._doc)
        self._all_rooms = []
        self._floor_type_map = {}

        self._adopt_host_font()
        self._apply_theme()
        self._load_rooms()
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

    def _load_rooms(self):
        try:
            room_elements = FilteredElementCollector(self._doc) \
                .OfCategory(BuiltInCategory.OST_Rooms) \
                .ToElements()
        except Exception:
            room_elements = []

        self._all_rooms = []
        for r in room_elements:
            if r.Location is None:
                continue
            self._all_rooms.append(RoomItem(r, self._doc))

        self._all_rooms.sort(key=lambda x: (x.Level, x.Number))
        self.room_datagrid.ItemsSource = to_items_source(self._all_rooms)

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
            self._floor_type_map["{}: {}".format(fam_name, type_name)] = t

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

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Normal if self.WindowState == WindowState.Maximized else WindowState.Maximized

    def close_button_clicked(self, sender, e):
        self.Close()

    def select_all_clicked(self, sender, e):
        for r in self._all_rooms:
            r.IsSelected = True
        self.room_datagrid.Items.Refresh()
        self._update_status()
        self.sync_header_checkbox(
            self.FindName("chk_all_room_datagrid"), self.room_datagrid, "IsSelected")

    def select_none_clicked(self, sender, e):
        for r in self._all_rooms:
            r.IsSelected = False
        self.room_datagrid.Items.Refresh()
        self._update_status()
        self.sync_header_checkbox(
            self.FindName("chk_all_room_datagrid"), self.room_datagrid, "IsSelected")

    def search_changed(self, sender, e):
        query = self.txt_search.Text.strip().upper()
        if not query:
            self.room_datagrid.ItemsSource = to_items_source(self._all_rooms)
        else:
            self.room_datagrid.ItemsSource = to_items_source([
                r for r in self._all_rooms
                if query in r.Name.upper() or query in r.Number.upper() or query in r.Level.upper()
            ])
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
        except (ValueError, TypeError):
            offset_mm = 0

        is_structural = self.chk_structural.IsChecked
        use_finish = self.chk_room_finish.IsChecked

        self.begin_progress(len(selected_rooms), disable=[sender])

        def progress_cb(current, total):
            self.step_progress(current, "Creating floor {}/{}...".format(current + 1, total))

        new_floors, created, errors = self.generator.generate_floors(
            [r.Element for r in selected_rooms],
            floor_type,
            offset_mm,
            is_structural,
            use_finish,
            progress_callback=progress_cb,
            cancel_check=lambda: self.is_cancelled
        )

        cancelled = self.is_cancelled
        self.end_progress()

        if new_floors and self._uidoc:
            try:
                self._uidoc.Selection.SetElementIds(List[ElementId]([f.Id for f in new_floors if f.IsValidObject]))
            except Exception as e:
                logger.debug("Failed to select created floors: {}".format(e))

        if cancelled:
            msg = "Cancelled — created {} floor(s).".format(created)
        else:
            msg = "Successfully created {} floors.".format(created)
        if errors > 0:
            msg += "\n{} errors occurred.".format(errors)

        TaskDialog.Show("Room to Floor", msg)
        self.Close()

    def select_all_room_datagrid_clicked(self, sender, e):
        """Header checkbox: select/deselect all rows currently shown in room_datagrid."""
        self.toggle_all_rows(self.room_datagrid, "IsSelected", sender.IsChecked)


def run_headless(args_json, doc=None):
    d = doc or revit.doc
    try:
        data = json.loads(args_json)
        gen = FloorGenerator(d)

        room_ids = data.get("room_ids", [])
        type_id = data.get("type_id")
        offset = data.get("offset_mm", 0)
        structural = data.get("structural", False)
        use_finish = data.get("use_finish", True)

        rooms = [d.GetElement(make_eid(int(rid))) for rid in room_ids if rid]
        floor_type = d.GetElement(make_eid(int(type_id))) if type_id else None

        new_floors, created, errors = gen.generate_floors(rooms, floor_type, offset, structural, use_finish)
        print(json.dumps({"status": "success", "created": created, "errors": errors}))
    except Exception as ex:
        print(json.dumps({"status": "error", "message": str(ex)}))


def show_room_to_floor_dialog(doc=None, uidoc=None):
    d = doc or revit.doc
    if not d:
        forms.alert("No active Revit document found.", title="Room to Floor")
        return
    u = uidoc
    if u is None:
        try:
            u = revit.uidoc
        except Exception:
            u = None
    win = RoomToFloorWindow(d, u)
    win.ShowDialog()
