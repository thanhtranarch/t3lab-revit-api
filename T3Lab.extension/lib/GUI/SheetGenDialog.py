#! python3
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
# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
for _env in ('APPDATA', 'PROGRAMDATA'):
    _base = os.environ.get(_env, '')
    if _base:
        for _clone in ('pyRevit-Master', 'pyRevit'):
            _ceng = os.path.join(_base, _clone, 'bin', 'cengines', 'CPY3123')
            if os.path.isdir(_ceng):
                for _d in (_ceng, os.path.join(_ceng, 'Lib')):
                    if hasattr(os, 'add_dll_directory'):
                        try:
                            os.add_dll_directory(_d)
                        except Exception:
                            pass
                for _p in (_ceng, os.path.join(_ceng, 'Lib'), os.path.join(_ceng, 'python312.zip')):
                    if os.path.exists(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)

_cur = os.path.dirname(os.path.abspath(__file__))
while _cur and not os.path.exists(os.path.join(_cur, 'lib')):
    _parent = os.path.dirname(_cur)
    if _parent == _cur:
        break
    _cur = _parent
_lib_dir = os.path.join(_cur, 'lib')
if os.path.exists(_lib_dir) and _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────
import clr
import re
import math
import json

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Windows import WindowState, Visibility, Thickness, CornerRadius, HorizontalAlignment, VerticalAlignment, FontWeights
from System.Windows.Controls import Border, Grid, TextBlock, Canvas, DataGridRow, CheckBox, TextBox
from System.Windows.Media import SolidColorBrush, Color, VisualTreeHelper
from System.Windows.Shapes import Line

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.DB import (
    Transaction,
    TransactionGroup,
    View,
    FilteredElementCollector,
    BuiltInCategory,
    ViewType,
    ViewFamilyType,
    ViewFamily,
    ViewPlan,
    ViewSheet,
    Viewport,
    ElevationMarker,
    SpatialElementBoundaryOptions,
    SpatialElementBoundaryLocation,
    ElementTransformUtils,
    XYZ,
)
from Autodesk.Revit.UI import TaskDialog
from GUI.WPF_Base import T3WPFWindow, to_items_source

# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝ VARIABLES
# ==================================================
logger        = script.get_logger()
# CPython has no ScriptOutput.GetDefault; safe_output()
# returns a no-op window instead of killing the tool.
try:
    from _cpython_bootstrap import safe_output
    output = safe_output()
except Exception:
    output = script.get_output()
# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
# point report the real problem.
try:
    uidoc = revit.uidoc
except Exception:
    uidoc = None
try:
    doc = revit.doc
except Exception:
    doc = None
# Read from the Application, not the document: `revit.doc` is None when this
# tool is launched from the Assistant pane or with no project open, and the
# old `int(revit.doc.Application.VersionNumber)` raised at IMPORT time
# ('NoneType' object has no attribute 'Application') so the window never opened.
from Snippets._host import get_revit_version
REVIT_VERSION = get_revit_version()

SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
lib_dir    = os.path.join(EXT_DIR, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

XAML_FILE  = os.path.join(os.path.dirname(__file__), 'Tools', 'SheetGen.xaml')

from GUI.ProgressPauseMixin import ProgressPauseMixin
try:
    import GUI.RevitTheme as RevitTheme
except Exception:
    RevitTheme = None

# Printable margin inside the title block (~20 mm), shared by the layout
# preview and the real viewport placement so they can never drift apart.
SHEET_MARGIN_FT = 0.066


# ╔═╗╦  ╔═╗╔═╗╔═╗╔═╗╔═╗
# ║  ║  ╠═╣╚═╗╚═╗║╣ ╚═╗
# ╚═╝╩═╝╩ ╩╚═╝╚═╝╚═╝╚═╝ CLASSES
# ==================================================

class RoomItem(object):
    """Represents a room item in the DataGrid."""
    def __init__(self, room_element, existing_plan_names=None):
        self.Element = room_element
        self.IsSelected = False
        p_num = room_element.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER)
        self.Number = p_num.AsString() if p_num else ""
        p_name = room_element.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
        self.Name = p_name.AsString() if p_name else ""

        type_param = room_element.LookupParameter("Room Type")
        self.RoomType = type_param.AsString() if type_param else ""

        try:
            level = doc.GetElement(room_element.LevelId)
            self.Level = level.Name if level else ""
        except Exception:
            self.Level = ""

        # Count floor plans matching this room number suffix (#Number)
        search_suffix = "(#{})".format(self.Number)
        if existing_plan_names:
            self.FloorPlanCount = sum(1 for name in existing_plan_names if search_suffix in name)
        else:
            self.FloorPlanCount = 0

        # Quantity to generate
        self.GenQty = 1


class CreateRoomPlanWindow(T3WPFWindow):
    """WPF window for creating plan views from rooms."""

    # ProgressPauseMixin — SheetGen.xaml footer progress panel
    PP_PANEL      = "sg_progress_panel"
    PP_BAR        = "sg_pb"
    PP_PAUSE      = "sg_btn_pause"
    PP_STOP       = "sg_btn_stop"
    PP_PAUSE_ICON = "sg_btn_pause_icon"
    PP_PAUSE_TEXT = "sg_btn_pause_label"
    PP_STATUS     = "status_text"
    PP_STOP_MSG   = u"Stopping… finishing current room"

    def __init__(self, doc_param=None, uidoc_param=None):
        global doc, uidoc
        if doc_param:
            doc = doc_param
        if uidoc_param:
            uidoc = uidoc_param
        T3WPFWindow.__init__(self, XAML_FILE)
        self._adopt_host_font()
        self._apply_theme()
        self._all_rooms = []
        self._first_sheet = None
        self._generated_sheets = []
        self._tb_size_cache = {}  # titleblock type id -> (w, h) feet
        self._load_rooms()
        self._load_view_templates()
        self._load_plan_type_options()
        self._load_title_blocks()
        self.cmb_strip_side.SelectedIndex = 0  # default: right (vertical) strip
        self._update_status()
        self._update_mockup()
        self._init_ai_mode()

    # ── AI Mode Support ──────────────────────────────────────────────────

    def _init_ai_mode(self):
        try:
            if hasattr(self, 'is_ai_mode_active') and self.is_ai_mode_active():
                if hasattr(self, 'ai_mode_badge') and self.ai_mode_badge:
                    self.ai_mode_badge.Visibility = Visibility.Visible
                if hasattr(self, 'txt_ai_status') and self.txt_ai_status:
                    info = self.get_ai_status_info()
                    self.txt_ai_status.Text = "AI Mode: {}".format(info.get('model', 'Ready'))
        except Exception as ex:
            logger.warning("AI Mode init failed: {}".format(ex))

    def ai_layout_clicked(self, sender, e):
        """AI Recommendation: analyze selected rooms and suggest optimal layout mode and strip width."""
        btn = getattr(self, 'btn_ai_layout_naming', None)
        orig_content = "✨ AI Smart Layout"

        def _restore_btn():
            if btn:
                btn.Content = orig_content
                btn.IsEnabled = True

        try:
            sel_rooms = [r for r in self._all_rooms if r.IsSelected]
            target_rooms = sel_rooms if sel_rooms else self._all_rooms[:10]

            if not target_rooms:
                forms.alert("Please select rooms to analyze layout.", title="AI Layout")
                return

            def _apply_classic():
                elev_checked = getattr(self.chk_elevations, 'IsChecked', False)
                if elev_checked:
                    self.rdo_layout_separate.IsChecked = True
                else:
                    self.rdo_layout_combined.IsChecked = True
                if hasattr(self, 'txt_strip_mm'):
                    self.txt_strip_mm.Text = "75"
                self._update_status("Rule-based Layout: Applied default layout recommendations.")
                _restore_btn()

            if not hasattr(self, 'is_ai_mode_active') or not self.is_ai_mode_active():
                _apply_classic()
                return

            if btn:
                btn.Content = "⏳ Analyzing..."
                btn.IsEnabled = False

            self._update_status("AI analyzing optimal sheet layout for {} rooms...".format(len(target_rooms)))

            room_summaries = []
            for r in target_rooms:
                room_summaries.append({
                    "name": getattr(r, 'Name', ''),
                    "number": getattr(r, 'Number', ''),
                    "type": getattr(r, 'RoomType', '')
                })

            prompt = (
                "You are an expert BIM Architect and Sheet Layout Planner.\n"
                "Given {} selected room plans for sheet generation in Autodesk Revit:\n{}\n"
                "Recommend the optimal layout strategy:\n"
                "- layout_mode: 'combined' (Plan + Elevations on 1 sheet) or 'separate' (Plans on Sheet 1, Elevations on Sheet 2)\n"
                "- strip_side: 'Right (vertical)' or 'Bottom (horizontal)'\n"
                "- strip_mm: integer (suggested header margin in mm, e.g. 70, 75, 80)\n"
                "- rationale: 1-sentence reasoning\n"
                "Return JSON ONLY with keys: {{\"layout_mode\": string, \"strip_side\": string, \"strip_mm\": integer, \"rationale\": string}}"
            ).format(len(target_rooms), json.dumps(room_summaries[:15]))

            def _worker():
                return self.ai_bridge.ask_json(prompt, fast=True)

            def _callback(res, err):
                try:
                    if err or not res or not isinstance(res, dict) or 'layout_mode' not in res:
                        _apply_classic()
                        return

                    mode = res.get('layout_mode', 'combined').lower()
                    strip_mm = res.get('strip_mm', 75)
                    rationale = res.get('rationale', 'Optimal layout applied')

                    if 'separate' in mode:
                        self.rdo_layout_separate.IsChecked = True
                    else:
                        self.rdo_layout_combined.IsChecked = True

                    if hasattr(self, 'txt_strip_mm'):
                        self.txt_strip_mm.Text = str(int(strip_mm))

                    self._update_status("AI Layout: {} (Strip: {}mm - {})".format(
                        "Separate Sheets" if 'separate' in mode else "Combined Sheet",
                        int(strip_mm), rationale
                    ))
                finally:
                    _restore_btn()

            self.run_ai_async(_worker, _callback)

        except Exception as ex:
            _restore_btn()
            logger.error("Error in ai_layout_clicked: {}".format(ex))
            self._update_status("AI Layout error: {}".format(ex))

    def _adopt_host_font(self):
        """Adopt host font per T3 standard."""
        if RevitTheme:
            try:
                RevitTheme.adopt_font(self)
            except Exception:
                pass

    def _apply_theme(self):
        """Apply theme per T3 standard."""
        if RevitTheme:
            try:
                RevitTheme.apply(self)
            except Exception:
                pass

    # ── Data loading ──────────────────────────────────
    def _load_rooms(self):
        """Collect all placed rooms from the document."""
        room_elements = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_Rooms) \
            .ToElements()

        # Collect names of all existing floor plans in the document
        view_elements = FilteredElementCollector(doc) \
            .OfClass(ViewPlan) \
            .WhereElementIsNotElementType() \
            .ToElements()
        
        existing_plan_names = [
            v.Name for v in view_elements
            if not v.IsTemplate and v.ViewType == ViewType.FloorPlan
        ]

        self._all_rooms = []
        for r in room_elements:
            # Skip unplaced rooms (area == 0 or no location)
            try:
                if r.Location is None:
                    continue
            except Exception:
                continue
            self._all_rooms.append(RoomItem(r, existing_plan_names))

        # Sort by number
        self._all_rooms.sort(key=lambda x: x.Number)
        self.room_datagrid.ItemsSource = to_items_source(self._all_rooms)
        if self._all_rooms:
            self.room_datagrid.SelectedItem = self._all_rooms[0]

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

    def _load_title_blocks(self):
        """Load all title block types into cmb_titleblock."""
        tb_types = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_TitleBlocks) \
            .WhereElementIsElementType() \
            .ToElements()

        self._titleblock_map = {}  # display_name -> ElementId
        for tb in tb_types:
            try:
                fam_name = tb.Family.Name
                type_name = tb.get_Parameter(
                    DB.BuiltInParameter.SYMBOL_NAME_PARAM
                )
                type_name = type_name.AsString() if type_name else ""
                display = "{}: {}".format(fam_name, type_name) if type_name else fam_name
            except Exception:
                display = str(tb.Id)
            self._titleblock_map[display] = tb.Id

        for name in sorted(self._titleblock_map.keys()):
            self.cmb_titleblock.Items.Add(name)
        if self._titleblock_map:
            self.cmb_titleblock.SelectedIndex = 0
        else:
            self.chk_layout_on_sheet.IsEnabled = False
            self.chk_layout_on_sheet.ToolTip = "No title blocks found in project"

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
        """Parse offset value from text box (in meters) and convert to feet."""
        try:
            val_meters = float(self.txt_offset.Text)
            return val_meters * 3.28084
        except (ValueError, TypeError):
            return 3.28084  # Default 1 meter in feet

    @staticmethod
    def _offset_bbox(bbox, offset=1):
        """Expand bounding box by offset in all directions."""
        new_bbox = DB.BoundingBoxXYZ()
        new_bbox.Min = DB.XYZ(bbox.Min.X - offset, bbox.Min.Y - offset, bbox.Min.Z - offset)
        new_bbox.Max = DB.XYZ(bbox.Max.X + offset, bbox.Max.Y + offset, bbox.Max.Z + offset)
        return new_bbox

    def _build_view_name(self, room_item, copy_index=0):
        """Build the plan view name from room info."""
        if "UNIT" in room_item.Name.upper() and room_item.RoomType:
            base = "ENLARGED PLAN - TYPE {} ({})".format(room_item.RoomType, room_item.Name)
        else:
            base = "ENLARGED PLAN - {} - (#{})".format(room_item.Name, room_item.Number)
        
        if copy_index > 0:
            return "{} - Copy {}".format(base, copy_index)
        return base

    def _unique_view_name(self, base_name, view_type):
        """
        Return base_name, or base_name with an incrementing " (n)" suffix if
        a view of the same ViewType already has that name (Revit only
        requires view names to be unique within the same ViewType, so a
        Floor Plan and a Ceiling Plan may legitimately share a name).
        Tracks names it hands out in self._used_view_names so repeated
        calls within the same run don't collide with each other either.
        """
        used = self._used_view_names.setdefault(view_type, set())
        name = base_name
        i = 2
        while name in used:
            name = "{} ({})".format(base_name, i)
            i += 1
        used.add(name)
        return name

    def _find_plan_view_for_level(self, level_id):
        """Find an existing floor plan view for the given level.

        Excludes the "ENLARGED PLAN - ..." views this same tool generates
        (see _build_view_name) - those are tightly cropped per-room views and
        make a poor host for a building-wide interior elevation marker.
        Prefers a candidate whose crop box is not active, if one exists.
        """
        views = FilteredElementCollector(doc) \
            .OfClass(ViewPlan) \
            .WhereElementIsNotElementType() \
            .ToElements()
        fallback = None
        for v in views:
            if (v.IsTemplate
                    or v.ViewType != ViewType.FloorPlan
                    or v.GenLevel is None
                    or v.GenLevel.Id != level_id):
                continue
            if v.Name.upper().startswith("ENLARGED PLAN"):
                continue
            if not v.CropBoxActive:
                return v
            if fallback is None:
                fallback = v
        return fallback

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

    def _create_interior_elevation_view(self, marker, host_plan, idx,
                                         cropbox_visible, max_dim,
                                         offset, elev_template_id):
        """Create and configure one elevation view at the given marker index.

        Naming is deferred to _finalize_elevation_name so the multi-marker
        fallback can rotate its marker into position first - direction labels
        are read from the real ViewDirection, not the marker index.
        """
        ev = marker.CreateElevation(doc, host_plan.Id, idx)
        ev.CropBoxActive  = True
        ev.CropBoxVisible = cropbox_visible
        p_far = ev.get_Parameter(DB.BuiltInParameter.VIEWER_BOUND_FAR_CLIPPING)
        if p_far:
            p_far.Set(1)
        p_off = ev.get_Parameter(DB.BuiltInParameter.VIEWER_BOUND_OFFSET_FAR)
        if p_off:
            p_off.Set(max_dim / 2 + offset)
        if elev_template_id:
            ev.ViewTemplateId = elev_template_id
        return ev

    @staticmethod
    def _direction_label(view):
        """Direction label from the elevation's actual ViewDirection.

        Marker index order (0..3 = S/W/N/E) is not guaranteed by the API, and
        the multi-marker fallback always hosts at index 0 - so derive the
        label from where the view really looks. ViewDirection points from the
        model toward the viewer: a view of the north wall looks +Y and
        reports -Y.
        """
        try:
            d = view.ViewDirection
            if abs(d.Y) >= abs(d.X):
                return "North" if d.Y < 0 else "South"
            return "East" if d.X < 0 else "West"
        except Exception:
            return "View"

    def _finalize_elevation_name(self, ev, room_item):
        """Name an elevation from its ViewDirection.

        Call only after the hosting marker has its final orientation (i.e.
        after the fallback rotation) and after a Regenerate.
        """
        label = self._direction_label(ev)
        try:
            d = ev.ViewDirection
            logger.debug(
                "Room {}: elevation ViewDirection=({:.2f}, {:.2f}, {:.2f})"
                " -> '{}'".format(room_item.Number, d.X, d.Y, d.Z, label))
        except Exception:
            pass
        try:
            base_name = "INTERIOR ELEV - {} - {} ({})".format(
                room_item.Name, label, room_item.Number)
            ev.Name = self._unique_view_name(base_name, ev.ViewType)
        except Exception:
            pass
        return label

    @staticmethod
    def _view_type_name(vt):
        """Type name of a ViewFamilyType, defensively."""
        try:
            p = vt.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            name = p.AsString() if p else None
            if name:
                return name
        except Exception:
            pass
        try:
            return vt.Name
        except Exception:
            return str(vt.Id)

    def _resolve_elevation_type(self):
        """Pick the elevation ViewFamilyType whose marker hosts the most views.

        The first-found Elevation type may map to a single-slot marker body
        (Snowdon: 'Building Elevation' hosts only 1) - CreateElevation on
        index 1..3 then throws "index is occupied or out of range". Capacity
        is an instance property (ElevationMarker.MaximumViewCount), so probe
        each type with a temp marker inside a rolled-back transaction.
        Prefers capacity >= 4 with 'interior' in the name, then any capacity
        >= 4, then the largest capacity. Cached per Create run.

        Returns (type_id, type_name, capacity).
        """
        if getattr(self, '_resolved_elev_type', None):
            return self._resolved_elev_type

        candidates = []
        view_types = FilteredElementCollector(doc) \
            .OfClass(ViewFamilyType) \
            .WhereElementIsElementType() \
            .ToElements()
        elev_types = [vt for vt in view_types
                      if vt.ViewFamily == ViewFamily.Elevation]

        probe = Transaction(doc, "Probe Elevation Marker Capacity")
        try:
            probe.Start()
            for vt in elev_types:
                name = self._view_type_name(vt)
                try:
                    m = ElevationMarker.CreateElevationMarker(
                        doc, vt.Id, XYZ(0, 0, 0), 50)
                    candidates.append((vt.Id, name, m.MaximumViewCount))
                    logger.debug("Elevation type '{}' -> marker capacity {}"
                                 .format(name, m.MaximumViewCount))
                except Exception as ex:
                    logger.debug("Elevation type '{}' probe failed: {}"
                                 .format(name, ex))
        except Exception as ex:
            logger.debug("Elevation capacity probe failed: {}".format(ex))
        finally:
            try:
                if probe.HasStarted():
                    probe.RollBack()
            except Exception:
                pass

        if candidates:
            best = max(candidates, key=lambda c: (
                c[2] >= 4,
                "INTERIOR" in c[1].upper() if c[2] >= 4 else False,
                c[2]))
            self._resolved_elev_type = best
        else:
            # Probe found nothing usable - keep the first-found type and
            # assume a single-slot marker (the safe fallback path).
            self._resolved_elev_type = (
                self._elevation_type_id, "<first found>", 1)
        logger.debug("Resolved elevation type: '{}' (capacity {})".format(
            self._resolved_elev_type[1], self._resolved_elev_type[2]))
        return self._resolved_elev_type

    def _log_marker_debug(self, marker, type_name):
        """Debug-log marker capacity and slot availability."""
        try:
            cap = marker.MaximumViewCount
        except Exception:
            cap = "?"
        states = []
        for idx in range(4):
            try:
                states.append("{}={}".format(idx, marker.IsAvailableIndex(idx)))
            except Exception as ex:
                states.append("{}=err({})".format(idx, ex))
        logger.debug(
            "Elevation marker (type '{}'): MaximumViewCount={}, "
            "IsAvailableIndex[{}]".format(type_name, cap, ", ".join(states)))

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

    def nav_toggle_clicked(self, sender, e):
        """Handle sidebar toggle button clicks mutually exclusively, switch tabs, and update title subtitle."""
        try:
            # Set clicked button to checked, and all others to unchecked
            is_rooms = (sender.Name == "nav_rooms")
            self.nav_rooms.IsChecked = is_rooms
            self.nav_layout.IsChecked = not is_rooms

            # Switch TabControl index
            if is_rooms:
                self.main_tab_control.SelectedIndex = 0
                self.lbl_subtitle.Text = "Generate plan views from selected rooms"
            else:
                self.main_tab_control.SelectedIndex = 1
                self.lbl_subtitle.Text = "Configure sheet layout and preview viewport placement"
                self._update_mockup()
        except Exception as ex:
            logger.error("Navigation click error: {}".format(ex))

    # ── Layout / Preview handlers ─────────────────────
    def layout_toggle_changed(self, sender, e):
        """Enable/disable layout sub-options based on toggle."""
        enabled = bool(self.chk_layout_on_sheet.IsChecked)
        self.pnl_layout_options.IsEnabled = enabled
        self.pnl_layout_options.Opacity = 1.0 if enabled else 0.45

    def room_selection_changed(self, sender, e):
        """Handle selected room change to update the realtime preview."""
        self._update_status()
        self._update_mockup()

    def room_row_clicked(self, sender, e):
        """Handle click on a Room List row to toggle its checkbox.

        Lets users click anywhere on a row to toggle IsSelected, in addition
        to using the checkbox itself (same UX as BatchOut's sheet list).
        """
        try:
            row = None
            element = e.OriginalSource
            while element is not None:
                if isinstance(element, (TextBox, CheckBox)):
                    # Click was on the checkbox or an editable cell - let it handle itself
                    return
                if isinstance(element, DataGridRow):
                    row = element
                    break
                element = VisualTreeHelper.GetParent(element)

            if row is not None:
                data_item = row.Item
                if data_item is not None:
                    data_item.IsSelected = not data_item.IsSelected
                    self.room_datagrid.Items.Refresh()
                    self._update_status()
                    self._update_mockup()
        except Exception as ex:
            logger.debug("Error handling room row click: {}".format(ex))

    def mockup_setting_changed(self, sender, e):
        """Handle view checkbox or ComboBox changes to redraw mockup."""
        self._update_mockup()

    def offset_changed(self, sender, e):
        """Handle offset textbox text changes to redraw mockup."""
        self._update_mockup()

    def _get_preview_room(self):
        """Get the room item to use for the layout preview."""
        try:
            row = self.room_datagrid.SelectedItem
            if row:
                return row
        except Exception:
            pass
        
        checked_rooms = self._get_selected_rooms()
        if checked_rooms:
            return checked_rooms[0]
            
        if self._all_rooms:
            return self._all_rooms[0]
            
        return None

    def _get_room_dimensions(self, room_item):
        """Get room bounding box width and depth in feet, with a fallback."""
        if room_item is None or room_item.Element is None:
            return 15.0, 12.0
        try:
            active_view = doc.ActiveView
            bbox = room_item.Element.get_BoundingBox(active_view)
            if bbox:
                w = bbox.Max.X - bbox.Min.X
                h = bbox.Max.Y - bbox.Min.Y
                if w > 0 and h > 0:
                    return w, h
        except Exception:
            pass
        return 15.0, 12.0

    def _get_template_scale(self, template_name):
        """Get the scale factor of a view template by name, defaulting to active view scale."""
        if not template_name or template_name == "<None>":
            try:
                return doc.ActiveView.Scale
            except Exception:
                return 50
        
        t_id = None
        if hasattr(self, '_plan_template_map') and template_name in self._plan_template_map:
            t_id = self._plan_template_map[template_name]
        elif hasattr(self, '_rcp_template_map') and template_name in self._rcp_template_map:
            t_id = self._rcp_template_map[template_name]
        elif hasattr(self, '_elev_template_map') and template_name in self._elev_template_map:
            t_id = self._elev_template_map[template_name]
            
        if t_id:
            try:
                template_view = doc.GetElement(t_id)
                if template_view:
                    return template_view.Scale
            except Exception:
                pass
        
        try:
            return doc.ActiveView.Scale
        except Exception:
            return 50

    def _get_titleblock_size(self, tb_id):
        """Real paper size (w, h) in feet for a title block type.

        SHEET_WIDTH/SHEET_HEIGHT only exist on placed instances (verified:
        the FJX/WH custom families expose nothing on the type), so probe by
        creating a temp sheet inside a rolled-back transaction. Cached per
        type so each title block is probed at most once per window.
        """
        if tb_id in self._tb_size_cache:
            return self._tb_size_cache[tb_id]
        size = None
        t = Transaction(doc, "Probe Title Block Size")
        try:
            t.Start()
            sheet = ViewSheet.Create(doc, tb_id)
            doc.Regenerate()
            inst = FilteredElementCollector(doc, sheet.Id) \
                .OfCategory(BuiltInCategory.OST_TitleBlocks) \
                .FirstElement()
            if inst:
                pw = inst.get_Parameter(DB.BuiltInParameter.SHEET_WIDTH)
                ph = inst.get_Parameter(DB.BuiltInParameter.SHEET_HEIGHT)
                if pw and ph and pw.AsDouble() > 0 and ph.AsDouble() > 0:
                    size = (pw.AsDouble(), ph.AsDouble())
                else:
                    bb = inst.get_BoundingBox(sheet)
                    if bb:
                        w = bb.Max.X - bb.Min.X
                        h = bb.Max.Y - bb.Min.Y
                        if w > 0 and h > 0:
                            size = (w, h)
        except Exception as ex:
            logger.debug("Title block size probe failed: {}".format(ex))
        finally:
            try:
                if t.HasStarted():
                    t.RollBack()
            except Exception:
                pass
        if not size:
            size = (2.759, 1.949)  # A1 fallback
            logger.debug("Title block size probe empty - falling back to A1")
        self._tb_size_cache[tb_id] = size
        return size

    def _get_preview_sheet_size(self):
        """Sheet dimensions (w, h) in feet of the selected title block."""
        try:
            tb_name = self.cmb_titleblock.SelectedItem
            tb_id = self._titleblock_map.get(tb_name) if tb_name else None
        except Exception:
            tb_id = None
        if tb_id:
            return self._get_titleblock_size(tb_id)
        return 2.759, 1.949  # A1 default before title blocks are loaded

    def _get_strip_config(self):
        """Title-block header strip: ('right'|'bottom'|'none', size in feet)."""
        side = 'right'
        try:
            idx = self.cmb_strip_side.SelectedIndex
            if 0 <= idx <= 2:
                side = ('right', 'bottom', 'none')[idx]
        except Exception:
            pass
        try:
            mm = float(self.txt_strip_mm.Text)
        except (ValueError, TypeError):
            mm = 70.0
        if mm < 0:
            mm = 0.0
        return side, mm / 304.8

    @staticmethod
    def _usable_rect(x0, y0, w, h, margin, strip_side, strip):
        """Printable rect (x0, y0, w, h) inside a sheet after the margin and
        the title-block header strip (vertical right / horizontal bottom)."""
        ux0 = x0 + margin
        uy0 = y0 + margin
        uw = w - 2.0 * margin
        uh = h - 2.0 * margin
        if strip_side == 'right':
            uw -= strip
        elif strip_side == 'bottom':
            uy0 += strip
            uh -= strip
        if uw < 0.1:
            uw = 0.1
        if uh < 0.1:
            uh = 0.1
        return ux0, uy0, uw, uh

    @staticmethod
    def _clamp_center(cx, cy, w_paper, h_paper, usable):
        """Clamp a box center so the whole box stays inside the usable rect.
        Returns (cx, cy, fits) - fits is False when the box is larger than
        the rect (it then gets centered so the spill is symmetric)."""
        ux0, uy0, uw, uh = usable
        fits = True
        if w_paper >= uw:
            cx = ux0 + uw / 2.0
            fits = False
        else:
            cx = min(max(cx, ux0 + w_paper / 2.0), ux0 + uw - w_paper / 2.0)
        if h_paper >= uh:
            cy = uy0 + uh / 2.0
            fits = False
        else:
            cy = min(max(cy, uy0 + h_paper / 2.0), uy0 + uh - h_paper / 2.0)
        return cx, cy, fits

    @staticmethod
    def _slot_centers(usable, plan_count, combined):
        """Viewport slot centers inside a usable rect.

        Single source of geometry for BOTH the preview mockup and the real
        placement, so what the user sees is what gets created.
        Returns (plan_centers, elev_centers) as (x, y) sheet-feet tuples.
        """
        ux0, uy0, uw, uh = usable
        plans, elevs = [], []
        if combined:
            px = ux0 + uw * 0.225  # middle of the left 45% plan zone
            if plan_count == 1:
                plans.append((px, uy0 + uh * 0.5))
            elif plan_count >= 2:
                plans.append((px, uy0 + uh * 0.72))
                plans.append((px, uy0 + uh * 0.28))
            for fx, fy in ((0.625, 0.75), (0.875, 0.75),
                           (0.625, 0.25), (0.875, 0.25)):
                elevs.append((ux0 + uw * fx, uy0 + uh * fy))
        else:
            if plan_count == 1:
                plans.append((ux0 + uw * 0.5, uy0 + uh * 0.5))
            elif plan_count >= 2:
                plans.append((ux0 + uw * 0.5, uy0 + uh * 0.70))
                plans.append((ux0 + uw * 0.5, uy0 + uh * 0.28))
            for fx, fy in ((0.25, 0.75), (0.75, 0.75),
                           (0.25, 0.25), (0.75, 0.25)):
                elevs.append((ux0 + uw * fx, uy0 + uh * fy))
        return plans, elevs

    @staticmethod
    def _paper_label(w_ft, h_ft):
        """Human label for a paper size, e.g. 'A1 (841 x 594 mm)'."""
        w_mm = int(round(w_ft * 304.8))
        h_mm = int(round(h_ft * 304.8))
        iso = ((1189, 841, "A0"), (841, 594, "A1"), (594, 420, "A2"),
               (420, 297, "A3"), (297, 210, "A4"))
        for iw, ih, name in iso:
            if abs(w_mm - iw) <= 6 and abs(h_mm - ih) <= 6:
                return "{}  ({} x {} mm)".format(name, w_mm, h_mm)
        return "{} x {} mm".format(w_mm, h_mm)

    def _draw_viewport(self, canvas, title, detail_num, w_px, h_px, x_px, y_px, bg_color, border_color):
        """Draw a viewport rectangle and Revit-style title mark on the WPF canvas."""
        # 1. Viewport Box
        box = Border()
        box.Width = w_px
        box.Height = h_px
        box.Background = SolidColorBrush(bg_color)
        box.BorderBrush = SolidColorBrush(border_color)
        box.BorderThickness = Thickness(1.5)
        box.CornerRadius = CornerRadius(4)
        Canvas.SetLeft(box, x_px)
        Canvas.SetTop(box, y_px)
        canvas.Children.Add(box)

        # 2. Revit Title Line
        line_y = y_px + h_px + 6
        line = Line()
        line.X1 = x_px
        line.Y1 = line_y
        line.X2 = x_px + max(w_px * 0.7, 40.0)
        line.Y2 = line_y
        line.Stroke = SolidColorBrush(Color.FromRgb(0x71, 0x71, 0x7A))
        line.StrokeThickness = 1
        canvas.Children.Add(line)

        # 3. Detail Circle
        circle = Border()
        circle.Width = 14
        circle.Height = 14
        circle.CornerRadius = CornerRadius(7)
        circle.BorderBrush = SolidColorBrush(border_color)
        circle.BorderThickness = Thickness(1)
        circle.Background = SolidColorBrush(Color.FromRgb(0xFF, 0xFF, 0xFF))
        
        circle_text = TextBlock()
        circle_text.Text = str(detail_num)
        circle_text.FontSize = 8
        circle_text.FontWeight = FontWeights.Bold
        circle_text.Foreground = SolidColorBrush(border_color)
        circle_text.HorizontalAlignment = HorizontalAlignment.Center
        circle_text.VerticalAlignment = VerticalAlignment.Center
        circle.Child = circle_text
        
        Canvas.SetLeft(circle, x_px)
        Canvas.SetTop(circle, line_y + 3)
        canvas.Children.Add(circle)

        # 4. View Title Text
        title_text = TextBlock()
        title_text.Text = title
        title_text.FontSize = 8.5
        title_text.FontWeight = FontWeights.Bold
        title_text.Foreground = SolidColorBrush(Color.FromRgb(0x18, 0x18, 0x1B))
        Canvas.SetLeft(title_text, x_px + 18)
        Canvas.SetTop(title_text, line_y + 2)
        canvas.Children.Add(title_text)

    def _setup_sheet_canvas(self, border, canvas, w_sheet, h_sheet,
                            max_w, max_h, strip_side, strip_ft):
        """Size the mockup sheet to the true paper aspect ratio, draw the
        header strip band and the paper-size label. Returns px-per-foot."""
        scale_px = min(max_w / w_sheet, max_h / h_sheet)
        border.Width = w_sheet * scale_px
        border.Height = h_sheet * scale_px

        if strip_side != 'none' and strip_ft > 0:
            band = Border()
            band.Background = SolidColorBrush(Color.FromRgb(0xF8, 0xFA, 0xFC))
            band.BorderBrush = SolidColorBrush(Color.FromRgb(0xCB, 0xD5, 0xE1))
            if strip_side == 'right':
                band.Width = strip_ft * scale_px
                band.Height = h_sheet * scale_px
                band.BorderThickness = Thickness(1, 0, 0, 0)
                Canvas.SetLeft(band, (w_sheet - strip_ft) * scale_px)
                Canvas.SetTop(band, 0)
            else:  # bottom
                band.Width = w_sheet * scale_px
                band.Height = strip_ft * scale_px
                band.BorderThickness = Thickness(0, 1, 0, 0)
                Canvas.SetLeft(band, 0)
                Canvas.SetTop(band, (h_sheet - strip_ft) * scale_px)
            canvas.Children.Add(band)

        lbl = TextBlock()
        lbl.Text = self._paper_label(w_sheet, h_sheet)
        lbl.FontSize = 9
        lbl.FontWeight = FontWeights.Bold
        lbl.Foreground = SolidColorBrush(Color.FromRgb(0x94, 0xA3, 0xB8))
        Canvas.SetLeft(lbl, 6)
        Canvas.SetTop(lbl, 4)
        canvas.Children.Add(lbl)
        return scale_px

    def _draw_viewport_to_canvas(self, canvas, title, detail_num, w_paper, h_paper,
                                 cx, cy, h_sheet, scale_px, fits,
                                 bg_color, border_color):
        """Map sheet feet (origin bottom-left) to canvas px (origin top-left)
        and draw the viewport. Boxes that overflow the usable area are
        outlined in red."""
        if not fits:
            border_color = Color.FromRgb(0xEF, 0x44, 0x44)
        x_px = (cx - w_paper / 2.0) * scale_px
        y_px = (h_sheet - (cy + h_paper / 2.0)) * scale_px
        w_px = w_paper * scale_px
        h_px = h_paper * scale_px
        self._draw_viewport(canvas, title, detail_num, w_px, h_px, x_px, y_px, bg_color, border_color)

    def _update_mockup(self):
        """Update the real-time layout mockup based on current settings and selected room."""
        # _all_rooms is only set after LoadComponent returns - events fired
        # while the XAML is still parsing must not reach the drawing code.
        if not hasattr(self, "_all_rooms"):
            return
        if not hasattr(self, "combined_canvas") or self.combined_canvas is None:
            return
        
        # 1. Clear previous drawings
        self.combined_canvas.Children.Clear()
        self.plans_canvas.Children.Clear()
        self.elevations_canvas.Children.Clear()

        # 2. Get the room to preview
        room_item = self._get_preview_room()
        if not room_item:
            return

        w_room, h_room = self._get_room_dimensions(room_item)
        offset = self._get_offset()

        # 3. Check enabled view types and lookup templates
        do_floor = bool(self.chk_floor_plan.IsChecked)
        do_ceiling = bool(self.chk_ceiling_plan.IsChecked)
        do_elevations = bool(self.chk_elevations.IsChecked)

        plan_template = str(self.cmb_plan_template.SelectedItem) if self.cmb_plan_template.SelectedItem else "<None>"
        rcp_template = str(self.cmb_rcp_template.SelectedItem) if self.cmb_rcp_template.SelectedItem else "<None>"
        elev_template = str(self.cmb_elev_template.SelectedItem) if self.cmb_elev_template.SelectedItem else "<None>"

        plan_scale = self._get_template_scale(plan_template)
        rcp_scale = self._get_template_scale(rcp_template)
        elev_scale = self._get_template_scale(elev_template)

        # 4. Viewport sizes on paper (feet)
        w_floor_paper = (w_room + 2.0 * offset) / plan_scale
        h_floor_paper = (h_room + 2.0 * offset) / plan_scale

        w_rcp_paper = (w_room + 2.0 * offset) / rcp_scale
        h_rcp_paper = (h_room + 2.0 * offset) / rcp_scale

        h_elev_paper = 10.0 / elev_scale
        w_elev_s_n_paper = (w_room + 2.0 * offset) / elev_scale
        w_elev_w_e_paper = (h_room + 2.0 * offset) / elev_scale

        # 5. Real sheet size from the selected title block + usable area
        #    (same helpers the actual placement uses, so preview == result)
        w_sheet, h_sheet = self._get_preview_sheet_size()
        strip_side, strip_ft = self._get_strip_config()
        usable = self._usable_rect(0.0, 0.0, w_sheet, h_sheet,
                                   SHEET_MARGIN_FT, strip_side, strip_ft)

        # Colors
        color_plan_bg = Color.FromRgb(0xEF, 0xF6, 0xFF)
        color_plan_border = Color.FromRgb(0x25, 0x63, 0xEB)

        color_rcp_bg = Color.FromRgb(0xF5, 0xF3, 0xFF)
        color_rcp_border = Color.FromRgb(0x7C, 0x3A, 0xED)

        color_elev_bg = Color.FromRgb(0xFF, 0xFB, 0xEB)
        color_elev_border = Color.FromRgb(0xD9, 0x77, 0x06)

        plan_views = []
        if do_floor:
            plan_views.append(("Floor Plan", w_floor_paper, h_floor_paper,
                               color_plan_bg, color_plan_border))
        if do_ceiling:
            plan_views.append(("Ceiling Plan", w_rcp_paper, h_rcp_paper,
                               color_rcp_bg, color_rcp_border))
        elev_specs = [
            ("Elevation 1", w_elev_s_n_paper, h_elev_paper),
            ("Elevation 2", w_elev_w_e_paper, h_elev_paper),
            ("Elevation 3", w_elev_s_n_paper, h_elev_paper),
            ("Elevation 4", w_elev_w_e_paper, h_elev_paper),
        ]

        # 6. Draw Combined Canvas (one sheet per room)
        if bool(self.rdo_layout_combined.IsChecked):
            scale_px = self._setup_sheet_canvas(
                self.CombinedSheetMockup, self.combined_canvas,
                w_sheet, h_sheet, 580.0, 380.0, strip_side, strip_ft)
            plan_centers, elev_centers = self._slot_centers(
                usable, len(plan_views), True)
            num = 1
            for spec, center in zip(plan_views, plan_centers):
                title, wp, hp, bg, bcol = spec
                cx, cy, fits = self._clamp_center(center[0], center[1], wp, hp, usable)
                self._draw_viewport_to_canvas(
                    self.combined_canvas, title, str(num), wp, hp, cx, cy,
                    h_sheet, scale_px, fits, bg, bcol)
                num += 1
            if do_elevations:
                for spec, center in zip(elev_specs, elev_centers):
                    title, wp, hp = spec
                    cx, cy, fits = self._clamp_center(center[0], center[1], wp, hp, usable)
                    self._draw_viewport_to_canvas(
                        self.combined_canvas, title, str(num), wp, hp, cx, cy,
                        h_sheet, scale_px, fits, color_elev_bg, color_elev_border)
                    num += 1

        # 7. Draw Separate Canvases (plans sheet + elevations sheet)
        else:
            if plan_views:
                scale_px = self._setup_sheet_canvas(
                    self.PlansSheetMockup, self.plans_canvas,
                    w_sheet, h_sheet, 300.0, 200.0, strip_side, strip_ft)
                plan_centers, _unused = self._slot_centers(
                    usable, len(plan_views), False)
                num = 1
                for spec, center in zip(plan_views, plan_centers):
                    title, wp, hp, bg, bcol = spec
                    cx, cy, fits = self._clamp_center(center[0], center[1], wp, hp, usable)
                    self._draw_viewport_to_canvas(
                        self.plans_canvas, title, str(num), wp, hp, cx, cy,
                        h_sheet, scale_px, fits, bg, bcol)
                    num += 1
            if do_elevations:
                scale_px = self._setup_sheet_canvas(
                    self.ElevationsSheetMockup, self.elevations_canvas,
                    w_sheet, h_sheet, 300.0, 200.0, strip_side, strip_ft)
                _unused, elev_centers = self._slot_centers(usable, 0, False)
                num = 1
                for spec, center in zip(elev_specs, elev_centers):
                    title, wp, hp = spec
                    cx, cy, fits = self._clamp_center(center[0], center[1], wp, hp, usable)
                    self._draw_viewport_to_canvas(
                        self.elevations_canvas, title, str(num), wp, hp, cx, cy,
                        h_sheet, scale_px, fits, color_elev_bg, color_elev_border)
                    num += 1

    def open_sheet_clicked(self, sender, e):
        """Activate the selected generated sheet in Revit."""
        try:
            selected_idx = self.cmb_generated_sheets.SelectedIndex
            if selected_idx >= 0 and selected_idx < len(self._generated_sheets):
                selected_sheet = self._generated_sheets[selected_idx]
                uidoc.ActiveView = selected_sheet
            else:
                TaskDialog.Show("Create Room Plan", "Please select a valid generated sheet from the list.")
        except Exception as ex:
            logger.error("Open sheet error: {}".format(ex))

    # ── Toolbar handlers ──────────────────────────────
    def select_all_clicked(self, sender, e):
        for r in self._all_rooms:
            r.IsSelected = True
        self.room_datagrid.Items.Refresh()
        self._update_status()
        # Giu checkbox select-all o header khop voi nut nay.
        self.sync_header_checkbox(
            self.FindName("chk_all_room_datagrid"), self.room_datagrid, "IsSelected")

    def select_none_clicked(self, sender, e):
        for r in self._all_rooms:
            r.IsSelected = False
        self.room_datagrid.Items.Refresh()
        self._update_status()
        # Giu checkbox select-all o header khop voi nut nay.
        self.sync_header_checkbox(
            self.FindName("chk_all_room_datagrid"), self.room_datagrid, "IsSelected")

    def search_changed(self, sender, e):
        """Filter room list by search text."""
        query = self.txt_search.Text.strip().upper()
        if not query:
            self.room_datagrid.ItemsSource = to_items_source(self._all_rooms)
        else:
            filtered = [
                r for r in self._all_rooms
                if query in r.Name.upper()
                or query in r.Number.upper()
                or query in (r.RoomType or "").upper()
                or query in (r.Level or "").upper()
            ]
            self.room_datagrid.ItemsSource = to_items_source(filtered)
        self._update_status()

    # ── Main action ───────────────────────────────────
    def create_plans_clicked(self, sender, e):
        """Create plan views (and optionally lay them out on sheets) for selected rooms."""
        selected_rooms = self._get_selected_rooms()
        if not selected_rooms:
            TaskDialog.Show("Create Room Plan", "Please select at least one room.")
            return

        do_floor      = bool(self.chk_floor_plan.IsChecked)
        do_ceiling    = bool(self.chk_ceiling_plan.IsChecked)
        do_elevations = bool(self.chk_elevations.IsChecked)
        do_layout     = bool(self.chk_layout_on_sheet.IsChecked)

        if not do_floor and not do_ceiling and not do_elevations:
            TaskDialog.Show("Create Room Plan", "Please select at least one view type.")
            return

        # Template selections
        plan_template_name = self.cmb_plan_template.SelectedItem
        rcp_template_name  = self.cmb_rcp_template.SelectedItem
        elev_template_name = self.cmb_elev_template.SelectedItem
        plan_template_id = self._plan_template_map.get(plan_template_name) \
            if plan_template_name != "<None>" else None
        rcp_template_id  = self._rcp_template_map.get(rcp_template_name) \
            if rcp_template_name != "<None>" else None
        elev_template_id = self._elev_template_map.get(elev_template_name) \
            if elev_template_name != "<None>" else None

        offset         = self._get_offset()
        cropbox_visible = bool(self.chk_cropbox_visible.IsChecked)
        created_count  = 0
        error_count    = 0
        active_view    = doc.ActiveView

        # Seed name/number uniqueness trackers from what's already in the
        # document, so re-running this tool on a room that already has
        # views/sheets auto-suffixes instead of crashing with
        # "Name must be unique" / "Sheet Number is already in use".
        self._used_view_names = {}
        for v in FilteredElementCollector(doc).OfClass(View).WhereElementIsNotElementType().ToElements():
            self._used_view_names.setdefault(v.ViewType, set()).add(v.Name)
        self._used_sheet_numbers = set()
        for sh in FilteredElementCollector(doc).OfClass(ViewSheet).ToElements():
            p = sh.get_Parameter(DB.BuiltInParameter.SHEET_NUMBER)
            if p:
                self._used_sheet_numbers.add(p.AsString())
        # Re-probe elevation marker capacity each run (model may change)
        self._resolved_elev_type = None

        # Collect per-room results for sheet layout
        room_results = []  # list of dicts

        self.begin_progress(len(selected_rooms), disable=[sender])
        for _sg_idx, room_item in enumerate(selected_rooms):
            if not self.step_progress(_sg_idx, "Processing room {}/{}...".format(_sg_idx + 1, len(selected_rooms))):
                break
            room          = room_item.Element
            room_level_id = room.LevelId
            room_bbox     = room.get_BoundingBox(active_view)
            if room_bbox is None:
                error_count += 1
                continue

            new_bbox  = self._offset_bbox(room_bbox, offset)

            # Get quantity to generate for floor plans
            try:
                qty = int(room_item.GenQty)
                if qty < 0:
                    qty = 0
            except Exception:
                qty = 1

            result = {
                'room_item':    room_item,
                'floor_plan':   None,
                'ceiling_plan': None,
                'elevations':   [],
            }

            # ── Floor Plan ─────────────────────────────
            if do_floor and self._floor_plan_type_id and qty > 0:
                for idx in range(qty):
                    view_name = self._unique_view_name(
                        self._build_view_name(room_item, copy_index=idx),
                        ViewType.FloorPlan)
                    try:
                        with Transaction(doc, "Create Floor Plan") as t:
                            t.Start()
                            vp = DB.ViewPlan.Create(doc, self._floor_plan_type_id, room_level_id)
                            vp.CropBoxActive  = True
                            vp.CropBoxVisible = cropbox_visible
                            vp.CropBox        = new_bbox
                            vp.Name           = view_name
                            t.Commit()
                        # Use first created floor plan as primary for sheet layout
                        if idx == 0:
                            result['floor_plan'] = vp
                        created_count += 1
                        if plan_template_id:
                            with Transaction(doc, "Assign Floor Plan Template") as t2:
                                t2.Start()
                                doc.GetElement(vp.Id).ViewTemplateId = plan_template_id
                                t2.Commit()
                    except Exception as ex:
                        error_count += 1
                        logger.error("Floor plan error for {}: {}".format(view_name, ex))

            # ── Ceiling Plan ────────────────────────────
            if do_ceiling and self._ceiling_plan_type_id:
                ceiling_view_name = self._unique_view_name(
                    self._build_view_name(room_item), ViewType.CeilingPlan)
                try:
                    with Transaction(doc, "Create Ceiling Plan") as t:
                        t.Start()
                        vp = DB.ViewPlan.Create(doc, self._ceiling_plan_type_id, room_level_id)
                        vp.CropBoxActive  = True
                        vp.CropBoxVisible = cropbox_visible
                        vp.CropBox        = new_bbox
                        vp.Name           = ceiling_view_name
                        t.Commit()
                    result['ceiling_plan'] = vp
                    created_count += 1
                    if rcp_template_id:
                        with Transaction(doc, "Assign RCP Template") as t2:
                            t2.Start()
                            doc.GetElement(vp.Id).ViewTemplateId = rcp_template_id
                            t2.Commit()
                except Exception as ex:
                    error_count += 1
                    logger.error("Ceiling plan error for {}: {}".format(ceiling_view_name, ex))

            # ── Interior Elevations ─────────────────────
            if do_elevations and self._elevation_type_id:
                try:
                    host_plan = self._find_plan_view_for_level(room_level_id)
                    if host_plan is None:
                        error_count += 1
                        logger.error("No floor plan found for level to host elevation marker")
                    else:
                        center = room.Location.Point
                        room_width = room_bbox.Max.X - room_bbox.Min.X
                        room_depth = room_bbox.Max.Y - room_bbox.Min.Y
                        max_dim    = max(room_width, room_depth) + offset * 2

                        # A marker only hosts MaximumViewCount views - the
                        # first-found type may host just 1 ('Building
                        # Elevation' in Snowdon), making CreateElevation on
                        # index 1..3 throw "index is occupied or out of
                        # range". Resolve the most capable type once per run;
                        # if nothing hosts 4, fall back to 4 single markers
                        # rotated 90 degrees apart.
                        elev_type_id, elev_type_name, elev_capacity = \
                            self._resolve_elevation_type()
                        use_fallback = elev_capacity < 4
                        if use_fallback:
                            logger.warning(
                                "No elevation type with a 4-view marker in "
                                "this model (best: '{}' hosts {}). Using 4 "
                                "single markers rotated 90 degrees apart."
                                .format(elev_type_name, elev_capacity))

                        with Transaction(doc, "Create Interior Elevations") as t:
                            t.Start()
                            scale = host_plan.Scale
                            if not use_fallback:
                                marker = ElevationMarker.CreateElevationMarker(
                                    doc, elev_type_id, center, scale)
                                doc.Regenerate()
                                self._log_marker_debug(marker, elev_type_name)
                                # IsAvailableIndex() is not reliable as a
                                # pre-filter across Revit versions (2023/2025
                                # report differently) - attempt CreateElevation
                                # on each index and let the API reject a slot.
                                for idx in range(4):
                                    try:
                                        ev = self._create_interior_elevation_view(
                                            marker, host_plan, idx,
                                            cropbox_visible, max_dim,
                                            offset, elev_template_id)
                                        doc.Regenerate()
                                        self._finalize_elevation_name(ev, room_item)
                                        result['elevations'].append(ev)
                                        created_count += 1
                                    except Exception as ex:
                                        error_count += 1
                                        logger.error(
                                            "Elevation index {} failed for "
                                            "room {} [host plan: '{}', type: "
                                            "'{}', marker capacity: {}]: "
                                            "{}: {}".format(
                                                idx, room_item.Number,
                                                host_plan.Name,
                                                elev_type_name, elev_capacity,
                                                type(ex).__name__, ex))
                            else:
                                # One marker per direction, hosted at index 0,
                                # rotated into place about Z at the room center.
                                z_axis_top = XYZ(center.X, center.Y, center.Z + 1.0)
                                for i in range(4):
                                    marker_i = None
                                    try:
                                        marker_i = ElevationMarker.CreateElevationMarker(
                                            doc, elev_type_id, center, scale)
                                        ev = self._create_interior_elevation_view(
                                            marker_i, host_plan, 0,
                                            cropbox_visible, max_dim,
                                            offset, elev_template_id)
                                        if i > 0:
                                            axis = DB.Line.CreateBound(center, z_axis_top)
                                            ElementTransformUtils.RotateElement(
                                                doc, marker_i.Id, axis,
                                                i * math.pi / 2.0)
                                        doc.Regenerate()
                                        self._finalize_elevation_name(ev, room_item)
                                        result['elevations'].append(ev)
                                        created_count += 1
                                    except Exception as ex:
                                        error_count += 1
                                        logger.error(
                                            "Fallback elevation {} of 4 failed "
                                            "for room {} [host plan: '{}', "
                                            "type: '{}']: {}: {}".format(
                                                i + 1, room_item.Number,
                                                host_plan.Name, elev_type_name,
                                                type(ex).__name__, ex))
                                        # Don't leave an empty marker behind
                                        if marker_i is not None:
                                            try:
                                                doc.Delete(marker_i.Id)
                                            except Exception:
                                                pass
                            logger.info(
                                "Room {}: {}/4 interior elevations created "
                                "(type='{}', capacity={}, fallback={})".format(
                                    room_item.Number,
                                    len(result['elevations']),
                                    elev_type_name, elev_capacity,
                                    "yes" if use_fallback else "no"))
                            if result['elevations']:
                                t.Commit()
                            else:
                                # Nothing hosted - don't leave an empty
                                # elevation marker behind in the model.
                                t.RollBack()
                except Exception as ex:
                    error_count += 1
                    logger.error("Elevation error for room {}: {}".format(
                        room_item.Number, ex))

            room_results.append(result)

        # ── Sheet Layout ────────────────────────────────
        sheets_created = 0
        self._generated_sheets = []
        if do_layout and room_results:
            tb_name = self.cmb_titleblock.SelectedItem
            tb_id   = self._titleblock_map.get(tb_name) if tb_name else None
            if tb_id:
                combined = bool(self.rdo_layout_combined.IsChecked)
                for result in room_results:
                    try:
                        sheets = self._layout_views_on_sheets(
                            result, tb_id, combined
                        )
                        sheets_created += len(sheets)
                        for sh in sheets:
                            self._generated_sheets.append(sh)
                    except Exception as ex:
                        error_count += 1
                        logger.error("Sheet layout error for {}: {}".format(
                            result['room_item'].Name, ex))

                if self._generated_sheets:
                    # Clear and populate the combo box
                    self.cmb_generated_sheets.Items.Clear()
                    for sh in self._generated_sheets:
                        sheet_num = sh.get_Parameter(DB.BuiltInParameter.SHEET_NUMBER)
                        num_str = sheet_num.AsString() if sheet_num else "???"
                        self.cmb_generated_sheets.Items.Add("{} - {}".format(num_str, sh.Name))
                    
                    # Select first sheet and enable controls
                    self.cmb_generated_sheets.SelectedIndex = 0
                    self.cmb_generated_sheets.IsEnabled = True
                    self.btn_open_sheet.IsEnabled = True
            else:
                logger.warning("No title block selected — skipping sheet layout.")

        # ── Result ─────────────────────────────────────
        cancelled = self.is_cancelled
        self.end_progress()

        if cancelled:
            msg = "Cancelled — {} view(s) created.".format(created_count)
        else:
            msg = "{} view(s) created.".format(created_count)
        if sheets_created:
            msg += "\n{} sheet(s) created.".format(sheets_created)
        if error_count > 0:
            msg += "\n{} error(s) — see output for details.".format(error_count)

        self.status_text.Text = msg
        if not do_layout:
            TaskDialog.Show("Create Room Plan", msg)
            self.Close()
        else:
            # Keep dialog open so user can click Open Selected Sheet
            TaskDialog.Show("Create Room Plan", msg)

    # ── Sheet helpers ─────────────────────────────────
    def _get_sheet_rect(self, sheet):
        """Return (x0, y0, width, height) of the title block on a sheet, in
        feet. Uses the real bbox origin - title blocks are NOT guaranteed to
        start at (0,0) (verified: FJX cover page bbox min is (349,-56) mm).
        Falls back to A1 at the origin."""
        try:
            tb_elems = FilteredElementCollector(doc, sheet.Id) \
                .OfCategory(BuiltInCategory.OST_TitleBlocks) \
                .ToElements()
            if tb_elems:
                bb = tb_elems[0].get_BoundingBox(sheet)
                if bb:
                    w = bb.Max.X - bb.Min.X
                    h = bb.Max.Y - bb.Min.Y
                    if w > 0 and h > 0:
                        return (bb.Min.X, bb.Min.Y, w, h)
        except Exception:
            pass
        # Default A1: 841x594 mm = 2.759 x 1.949 ft
        return (0.0, 0.0, 2.759, 1.949)

    def _build_sheet_number(self, room_item, suffix=""):
        """Build a sheet number like EPL-101-P or EPL-101-E."""
        base = "EPL-{}".format(room_item.Number)
        return "{}-{}".format(base, suffix) if suffix else base

    def _unique_sheet_number(self, base_number):
        """
        Return base_number, or base_number with an incrementing "-n" suffix
        if that sheet number is already in use (e.g. from a previous run of
        this tool on the same room). Tracks numbers it hands out in
        self._used_sheet_numbers so repeated calls within the same run
        don't collide with each other either.
        """
        number = base_number
        i = 2
        while number in self._used_sheet_numbers:
            number = "{}-{}".format(base_number, i)
            i += 1
        self._used_sheet_numbers.add(number)
        return number

    def _create_sheet(self, room_item, titleblock_id, name_suffix="", num_suffix=""):
        """Create a ViewSheet. Returns the sheet element."""
        sheet_name = self._build_view_name(room_item)
        if name_suffix:
            sheet_name = "{} — {}".format(sheet_name, name_suffix)
        sheet_name = self._unique_view_name(sheet_name, ViewType.DrawingSheet)
        sheet_num = self._unique_sheet_number(self._build_sheet_number(room_item, num_suffix))

        with Transaction(doc, "Create Sheet") as t:
            t.Start()
            sheet = ViewSheet.Create(doc, titleblock_id)
            sheet.Name = sheet_name
            p_num = sheet.get_Parameter(DB.BuiltInParameter.SHEET_NUMBER)
            if p_num and not p_num.IsReadOnly:
                attempt = sheet_num
                for _ in range(25):
                    try:
                        p_num.Set(attempt)
                        break
                    except Exception:
                        attempt = self._unique_sheet_number(attempt)
                else:
                    logger.error(
                        "Could not assign a unique sheet number based on "
                        "{}".format(sheet_num))
            t.Commit()
        return sheet

    def _place_viewport_centered(self, sheet, view_id, cx, cy, usable=None):
        """
        Place a viewport at (cx, cy) in sheet coordinates, then nudge it back
        inside the usable rect (margins + title header strip) using its real
        paper footprint - so view content can never sit on the title block
        header, whether the strip is vertical or horizontal.
        Returns the Viewport element, or None on failure.
        """
        try:
            with Transaction(doc, "Place Viewport") as t:
                t.Start()
                vp = Viewport.Create(doc, sheet.Id, view_id, XYZ(cx, cy, 0))
                if vp is not None and usable is not None:
                    doc.Regenerate()
                    try:
                        box = vp.GetBoxOutline()
                        w_p = box.MaximumPoint.X - box.MinimumPoint.X
                        h_p = box.MaximumPoint.Y - box.MinimumPoint.Y
                        bcx = (box.MaximumPoint.X + box.MinimumPoint.X) / 2.0
                        bcy = (box.MaximumPoint.Y + box.MinimumPoint.Y) / 2.0
                        nx, ny, fits = self._clamp_center(bcx, bcy, w_p, h_p, usable)
                        if abs(nx - bcx) > 1e-6 or abs(ny - bcy) > 1e-6:
                            vp.SetBoxCenter(XYZ(nx, ny, 0))
                        if not fits:
                            # Debug-level only (a warning here spams the
                            # output window on every oversized view) - the
                            # preview already flags this case with a red
                            # outline before Create is clicked.
                            view = doc.GetElement(view_id)
                            logger.debug(
                                "Viewport '{}' ({:.0f} x {:.0f} mm on paper) "
                                "is larger than the usable area of sheet '{}' "
                                "- reduce the view scale or use a larger "
                                "title block.".format(
                                    view.Name if view else view_id,
                                    w_p * 304.8, h_p * 304.8,
                                    sheet.SheetNumber))
                    except Exception as ex:
                        logger.debug("Viewport clamp skipped: {}".format(ex))
                t.Commit()
            return vp
        except Exception as ex:
            logger.error("Viewport place error: {}".format(ex))
            return None

    def _layout_combined(self, result, sheet):
        """
        Combined layout — plans left, 4 elevations right (2x2 grid), all kept
        inside the usable rect (margins + title header strip). Slot geometry
        comes from _slot_centers, the same helper the preview draws with.
        """
        x0, y0, w, h = self._get_sheet_rect(sheet)
        strip_side, strip_ft = self._get_strip_config()
        usable = self._usable_rect(x0, y0, w, h,
                                   SHEET_MARGIN_FT, strip_side, strip_ft)

        plan_views = [v for v in [
            result.get('floor_plan'), result.get('ceiling_plan')
        ] if v is not None]
        elev_views = result.get('elevations', [])

        plan_centers, elev_centers = self._slot_centers(
            usable, len(plan_views), True)
        for v, center in zip(plan_views, plan_centers):
            self._place_viewport_centered(sheet, v.Id, center[0], center[1], usable)
        for ev, center in zip(elev_views[:4], elev_centers):
            self._place_viewport_centered(sheet, ev.Id, center[0], center[1], usable)

    def _layout_separate(self, result, plan_sheet, elev_sheet):
        """
        Separate layout — plans on plan_sheet, elevations on elev_sheet, all
        kept inside the usable rect (margins + title header strip).
        """
        strip_side, strip_ft = self._get_strip_config()

        # ── Plans sheet ──────────────────────────────
        if plan_sheet:
            x0, y0, w, h = self._get_sheet_rect(plan_sheet)
            usable = self._usable_rect(x0, y0, w, h,
                                       SHEET_MARGIN_FT, strip_side, strip_ft)
            plan_views = [v for v in [
                result.get('floor_plan'), result.get('ceiling_plan')
            ] if v is not None]
            plan_centers, _unused = self._slot_centers(
                usable, len(plan_views), False)
            for v, center in zip(plan_views, plan_centers):
                self._place_viewport_centered(
                    plan_sheet, v.Id, center[0], center[1], usable)

        # ── Elevations sheet ──────────────────────────
        if elev_sheet:
            x0, y0, w, h = self._get_sheet_rect(elev_sheet)
            usable = self._usable_rect(x0, y0, w, h,
                                       SHEET_MARGIN_FT, strip_side, strip_ft)
            _unused, elev_centers = self._slot_centers(usable, 0, False)
            for ev, center in zip(result.get('elevations', [])[:4], elev_centers):
                self._place_viewport_centered(
                    elev_sheet, ev.Id, center[0], center[1], usable)

    def _layout_views_on_sheets(self, result, titleblock_id, combined):
        """
        Create sheet(s) and place viewports for a single room result.
        Returns list of created ViewSheet objects.
        """
        room_item  = result['room_item']
        has_plans  = result['floor_plan'] or result['ceiling_plan']
        has_elevs  = bool(result['elevations'])
        sheets     = []

        if combined:
            # One sheet for everything
            if has_plans or has_elevs:
                sheet = self._create_sheet(room_item, titleblock_id)
                self._layout_combined(result, sheet)
                sheets.append(sheet)
        else:
            # Plans on their own sheet
            if has_plans:
                p_sheet = self._create_sheet(
                    room_item, titleblock_id,
                    name_suffix="Plans", num_suffix="P"
                )
                sheets.append(p_sheet)
            else:
                p_sheet = None

            # Elevations on their own sheet
            if has_elevs:
                e_sheet = self._create_sheet(
                    room_item, titleblock_id,
                    name_suffix="Elevations", num_suffix="E"
                )
                sheets.append(e_sheet)
            else:
                e_sheet = None

            self._layout_separate(result, p_sheet, e_sheet)

        return sheets

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_room_datagrid_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua room_datagrid."""
        self.toggle_all_rows(self.room_datagrid, "IsSelected", sender.IsChecked)


# ╔╦╗╔═╗╦╔╗╔
# ║║║╠═╣║║║║
# ╩ ╩╩ ╩╩╝╚╝ MAIN
# ==================================================
def show_sheet_gen_dialog(doc=None, uidoc=None):
    d = doc or revit.doc
    if not d:
        forms.alert("No active Revit document found.", title="SheetGen")
        return
    u = uidoc
    if u is None:
        try:
            u = revit.uidoc
        except Exception:
            u = None
    try:
        window = CreateRoomPlanWindow(d, u)
        window.ShowDialog()
    except Exception as ex:
        logger.error("SheetGen error: {}".format(ex))
        import traceback
        logger.error(traceback.format_exc())

if __name__ == '__main__':
    show_sheet_gen_dialog()
