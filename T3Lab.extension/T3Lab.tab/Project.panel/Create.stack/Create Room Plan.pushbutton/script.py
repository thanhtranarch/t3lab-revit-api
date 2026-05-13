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

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝ IMPORTS
# ==================================================
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

# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝ VARIABLES
# ==================================================
logger        = script.get_logger()
output        = script.get_output()
uidoc         = revit.uidoc
doc           = revit.doc
REVIT_VERSION = int(revit.doc.Application.VersionNumber)

SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))
lib_dir    = os.path.join(EXT_DIR, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

XAML_FILE  = os.path.join(EXT_DIR, 'lib', 'GUI', 'Tools', 'CreateRoomPlan.xaml')


# ╔═╗╦  ╔═╗╔═╗╔═╗╔═╗╔═╗
# ║  ║  ╠═╣╚═╗╚═╗║╣ ╚═╗
# ╚═╝╩═╝╩ ╩╚═╝╚═╝╚═╝╚═╝ CLASSES
# ==================================================

class RoomItem(object):
    """Represents a room item in the DataGrid."""
    def __init__(self, room_element):
        self.Element = room_element
        self.IsSelected = False
        p_num = room_element.LookupParameter("Number")
        self.Number = p_num.AsString() if p_num else ""
        p_name = room_element.LookupParameter("Name")
        self.Name = p_name.AsString() if p_name else ""

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

    # ── Data loading ──────────────────────────────────
    def _load_rooms(self):
        """Collect all placed rooms from the document."""
        room_elements = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_Rooms) \
            .ToElements()

        self._all_rooms = []
        for r in room_elements:
            # Skip unplaced rooms (area == 0 or no location)
            try:
                if r.Location is None:
                    continue
            except Exception:
                continue
            self._all_rooms.append(RoomItem(r))

        # Sort by number
        self._all_rooms.sort(key=lambda x: x.Number)
        self.room_datagrid.ItemsSource = self._all_rooms

    def _load_view_templates(self):
        """Collect view templates for Floor Plan and Ceiling Plan."""
        view_elements = FilteredElementCollector(doc) \
            .OfClass(View) \
            .WhereElementIsNotElementType() \
            .ToElements()

        plan_templates = sorted([
            v.Name for v in view_elements
            if v.IsTemplate and v.ViewType == ViewType.FloorPlan
        ])
        rcp_templates = sorted([
            v.Name for v in view_elements
            if v.IsTemplate and v.ViewType == ViewType.CeilingPlan
        ])

        # Store template elements for later lookup
        self._plan_template_map = {
            v.Name: v.Id for v in view_elements
            if v.IsTemplate and v.ViewType == ViewType.FloorPlan
        }
        self._rcp_template_map = {
            v.Name: v.Id for v in view_elements
            if v.IsTemplate and v.ViewType == ViewType.CeilingPlan
        }

        # Populate combo boxes with "None" option
        self.cmb_plan_template.Items.Add("<None>")
        for name in plan_templates:
            self.cmb_plan_template.Items.Add(name)
        self.cmb_plan_template.SelectedIndex = 0

        self.cmb_rcp_template.Items.Add("<None>")
        for name in rcp_templates:
            self.cmb_rcp_template.Items.Add(name)
        self.cmb_rcp_template.SelectedIndex = 0

        # Elevation templates
        elev_templates = sorted([
            v.Name for v in view_elements
            if v.IsTemplate and v.ViewType == ViewType.Elevation
        ])
        self._elev_template_map = {
            v.Name: v.Id for v in view_elements
            if v.IsTemplate and v.ViewType == ViewType.Elevation
        }
        self.cmb_elev_template.Items.Add("<None>")
        for name in elev_templates:
            self.cmb_elev_template.Items.Add(name)
        self.cmb_elev_template.SelectedIndex = 0

    def _load_plan_type_options(self):
        """Load available view family types for Floor Plan / Ceiling Plan."""
        view_types = FilteredElementCollector(doc) \
            .OfClass(ViewFamilyType) \
            .WhereElementIsElementType() \
            .ToElements()

        self._floor_plan_type_id = None
        self._ceiling_plan_type_id = None
        self._elevation_type_id = None

        for vt in view_types:
            if vt.FamilyName == 'Floor Plan' and self._floor_plan_type_id is None:
                self._floor_plan_type_id = vt.Id
            elif vt.FamilyName == 'Ceiling Plan' and self._ceiling_plan_type_id is None:
                self._ceiling_plan_type_id = vt.Id
            elif vt.ViewFamily == ViewFamily.Elevation and self._elevation_type_id is None:
                self._elevation_type_id = vt.Id

    # ── Helpers ───────────────────────────────────────
    def _get_selected_rooms(self):
        """Return list of RoomItems that are checked."""
        return [r for r in self._all_rooms if r.IsSelected]

    def _update_status(self):
        """Update status bar text."""
        selected = len(self._get_selected_rooms())
        total = len(self._all_rooms)
        self.status_count.Text = "{} rooms".format(total)
        if selected > 0:
            self.status_text.Text = "{} room(s) selected".format(selected)
        else:
            self.status_text.Text = "Ready"

    def _get_offset(self):
        """Parse offset value from text box."""
        try:
            return float(self.txt_offset.Text)
        except (ValueError, TypeError):
            return 1.0

    @staticmethod
    def _offset_bbox(bbox, offset=1):
        """Expand bounding box by offset in all directions."""
        new_bbox = DB.BoundingBoxXYZ()
        new_bbox.Min = DB.XYZ(bbox.Min.X - offset, bbox.Min.Y - offset, bbox.Min.Z - offset)
        new_bbox.Max = DB.XYZ(bbox.Max.X + offset, bbox.Max.Y + offset, bbox.Max.Z + offset)
        return new_bbox

    def _build_view_name(self, room_item):
        """Build the plan view name from room info."""
        if "UNIT" in room_item.Name.upper() and room_item.RoomType:
            return "ENLARGED PLAN - TYPE {} ({})".format(room_item.RoomType, room_item.Name)
        else:
            return "ENLARGED PLAN - {} - (#{})".format(room_item.Name, room_item.Number)

    def _find_plan_view_for_level(self, level_id):
        """Find an existing floor plan view for the given level."""
        views = FilteredElementCollector(doc) \
            .OfClass(ViewPlan) \
            .WhereElementIsNotElementType() \
            .ToElements()
        for v in views:
            if (not v.IsTemplate
                    and v.ViewType == ViewType.FloorPlan
                    and v.GenLevel is not None
                    and v.GenLevel.Id == level_id):
                return v
        return None

    def _get_boundary_wall_ids(self, room):
        """Return set of wall element ids forming the room boundary."""
        wall_ids = set()
        try:
            opt = SpatialElementBoundaryOptions()
            opt.SpatialElementBoundaryLocation = \
                SpatialElementBoundaryLocation.Finish
            segments_list = room.GetBoundarySegments(opt)
            if segments_list:
                for seg_loop in segments_list:
                    for seg in seg_loop:
                        elem = doc.GetElement(seg.ElementId)
                        if elem and isinstance(elem, DB.Wall):
                            wall_ids.add(seg.ElementId)
        except Exception:
            pass
        return wall_ids

    # ── Window chrome handlers ────────────────────────
    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self.Close()

    # ── Toolbar handlers ──────────────────────────────
    def select_all_clicked(self, sender, e):
        for r in self._all_rooms:
            r.IsSelected = True
        self.room_datagrid.Items.Refresh()
        self._update_status()

    def select_none_clicked(self, sender, e):
        for r in self._all_rooms:
            r.IsSelected = False
        self.room_datagrid.Items.Refresh()
        self._update_status()

    def search_changed(self, sender, e):
        """Filter room list by search text."""
        query = self.txt_search.Text.strip().upper()
        if not query:
            self.room_datagrid.ItemsSource = self._all_rooms
        else:
            filtered = [
                r for r in self._all_rooms
                if query in r.Name.upper()
                or query in r.Number.upper()
                or query in (r.RoomType or "").upper()
                or query in (r.Level or "").upper()
            ]
            self.room_datagrid.ItemsSource = filtered
        self._update_status()

    # ── Main action ───────────────────────────────────
    def create_plans_clicked(self, sender, e):
        """Create plan views for all selected rooms."""
        selected_rooms = self._get_selected_rooms()
        if not selected_rooms:
            TaskDialog.Show("Create Room Plan", "Please select at least one room.")
            return

        do_floor = self.chk_floor_plan.IsChecked
        do_ceiling = self.chk_ceiling_plan.IsChecked
        do_elevations = self.chk_elevations.IsChecked

        if not do_floor and not do_ceiling and not do_elevations:
            TaskDialog.Show("Create Room Plan", "Please select at least one view type.")
            return

        # Get template selections
        plan_template_name = self.cmb_plan_template.SelectedItem
        rcp_template_name = self.cmb_rcp_template.SelectedItem
        elev_template_name = self.cmb_elev_template.SelectedItem
        plan_template_id = self._plan_template_map.get(plan_template_name) if plan_template_name != "<None>" else None
        rcp_template_id = self._rcp_template_map.get(rcp_template_name) if rcp_template_name != "<None>" else None
        elev_template_id = self._elev_template_map.get(elev_template_name) if elev_template_name != "<None>" else None

        offset = self._get_offset()
        cropbox_visible = self.chk_cropbox_visible.IsChecked
        created_count = 0
        error_count = 0

        active_view = doc.ActiveView

        for room_item in selected_rooms:
            room = room_item.Element
            room_level_id = room.LevelId
            room_bbox = room.get_BoundingBox(active_view)
            if room_bbox is None:
                error_count += 1
                continue

            new_bbox = self._offset_bbox(room_bbox, offset)
            view_name = self._build_view_name(room_item)

            # Create Floor Plan
            if do_floor and self._floor_plan_type_id:
                try:
                    with Transaction(doc, "Create Floor Plan") as t:
                        t.Start()
                        viewplan = DB.ViewPlan.Create(doc, self._floor_plan_type_id, room_level_id)
                        viewplan.CropBoxActive = True
                        viewplan.CropBoxVisible = cropbox_visible
                        viewplan.CropBox = new_bbox
                        viewplan.Name = view_name
                        t.Commit()
                    created_count += 1

                    # Apply template
                    if plan_template_id:
                        with Transaction(doc, "Assign Floor Plan Template") as t2:
                            t2.Start()
                            vp = doc.GetElement(viewplan.Id)
                            vp.ViewTemplateId = plan_template_id
                            t2.Commit()
                except Exception as ex:
                    error_count += 1
                    logger.error("Floor plan error for {}: {}".format(view_name, ex))

            # Create Ceiling Plan
            if do_ceiling and self._ceiling_plan_type_id:
                try:
                    with Transaction(doc, "Create Ceiling Plan") as t:
                        t.Start()
                        viewplan = DB.ViewPlan.Create(doc, self._ceiling_plan_type_id, room_level_id)
                        viewplan.CropBoxActive = True
                        viewplan.CropBoxVisible = cropbox_visible
                        viewplan.CropBox = new_bbox
                        viewplan.Name = view_name
                        t.Commit()
                    created_count += 1

                    # Apply template
                    if rcp_template_id:
                        with Transaction(doc, "Assign RCP Template") as t2:
                            t2.Start()
                            vp = doc.GetElement(viewplan.Id)
                            vp.ViewTemplateId = rcp_template_id
                            t2.Commit()
                except Exception as ex:
                    error_count += 1
                    logger.error("Ceiling plan error for {}: {}".format(view_name, ex))

            # Create Interior Elevations
            if do_elevations and self._elevation_type_id:
                try:
                    # Find a floor plan view on this level for the marker
                    host_plan = self._find_plan_view_for_level(room_level_id)
                    if host_plan is None:
                        error_count += 1
                        logger.error("No floor plan found for level to host elevation marker")
                        continue

                    # Get room center point
                    center = room.Location.Point

                    # Get level height range for crop
                    level = doc.GetElement(room_level_id)

                    with Transaction(doc, "Create Interior Elevations") as t:
                        t.Start()

                        # Create elevation marker at room center
                        scale = host_plan.Scale
                        marker = ElevationMarker.CreateElevationMarker(
                            doc, self._elevation_type_id, center, scale
                        )

                        # Create 4 elevation views (one for each cardinal direction)
                        directions = ["South", "West", "North", "East"]
                        for idx in range(4):
                            try:
                                elev_view = marker.CreateElevation(
                                    doc, host_plan.Id, idx
                                )
                                elev_name = "INTERIOR ELEV - {} - {} ({})".format(
                                    room_item.Name, directions[idx], room_item.Number
                                )
                                try:
                                    elev_view.Name = elev_name
                                except Exception:
                                    pass

                                elev_view.CropBoxActive = True
                                elev_view.CropBoxVisible = cropbox_visible

                                room_width = room_bbox.Max.X - room_bbox.Min.X
                                room_depth = room_bbox.Max.Y - room_bbox.Min.Y
                                max_dim = max(room_width, room_depth) + offset * 2

                                far_clip_param = elev_view.get_Parameter(
                                    DB.BuiltInParameter.VIEWER_BOUND_FAR_CLIPPING
                                )
                                if far_clip_param:
                                    far_clip_param.Set(1)

                                far_offset_param = elev_view.get_Parameter(
                                    DB.BuiltInParameter.VIEWER_BOUND_OFFSET_FAR
                                )
                                if far_offset_param:
                                    far_offset_param.Set(max_dim / 2 + offset)

                                if elev_template_id:
                                    elev_view.ViewTemplateId = elev_template_id

                                created_count += 1
                            except Exception as ex:
                                error_count += 1
                                logger.error("Elevation {} error: {}".format(
                                    directions[idx], ex))

                        t.Commit()
                except Exception as ex:
                    error_count += 1
                    logger.error("Elevation error for {}: {}".format(view_name, ex))

        # Show result
        msg = "{} view(s) created successfully.".format(created_count)
        if error_count > 0:
            msg += "\n{} room(s) had errors.".format(error_count)

        self.status_text.Text = msg
        TaskDialog.Show("Create Room Plan", msg)
        self.Close()


# ╔╦╗╔═╗╦╔╗╔
# ║║║╠═╣║║║║
# ╩ ╩╩ ╩╩╝╚╝ MAIN
# ==================================================
if __name__ == '__main__':
    try:
        window = CreateRoomPlanWindow()
        window.ShowDialog()
    except Exception as ex:
        logger.error("Create Room Plan error: {}".format(ex))
        import traceback
        logger.error(traceback.format_exc())
