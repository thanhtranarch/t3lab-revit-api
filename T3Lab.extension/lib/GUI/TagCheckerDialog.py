# -*- coding: utf-8 -*-
"""
Tag Checker v3.1 - DQT
Check if elements in current view are fully tagged.
Modal dialog (ShowDialog) for maximum stability.
Double-click zoom: closes dialog, zooms, then re-opens.

v3.1 Changes:
- REVERT: Back to ShowDialog (modal) - ExternalEvent caused native crash
- SAFE: No get_BoundingBox on linked elements (avoids native crash)
- SAFE: All linked element operations wrapped in individual try/except
- NEW: Double-click zoom closes dialog, zooms, re-opens with state
- NEW: Reset Colors button (clears overrides, keeps auto-tags)
- NEW: Clear All button (clears overrides + deletes auto-tags)

Copyright (c) 2026 by Dang Quoc Truong (DQT)
All rights reserved.
"""

__title__ = "Tag\nChecker"
__author__ = "Dang Quoc Truong (DQT)"
__doc__ = "Check if elements are tagged in current view. Highlight untagged elements and far-away tags."

# ===========================================================================
# IMPORTS
# ===========================================================================
import clr
import sys
import math

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Xml")

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB import IFailuresPreprocessor, FailureProcessingResult
from Autodesk.Revit.UI import *
from System.Collections.Generic import List

import System
from System.IO import MemoryStream
from System.Text import Encoding
from System.Windows import Window, WindowStartupLocation, WindowState
from System.Windows import Thickness, Visibility, HorizontalAlignment
from System.Windows import MessageBox as WPFMessageBox
from System.Windows.Markup import XamlReader
WPFGrid = System.Windows.Controls.Grid
from GUI.WPF_Base import setup_window_logo

# pyRevit
from pyrevit import revit, script, forms

# `__revit__` members are unavailable when no UIDocument is active, and at
# module scope that kills the import outright. Resolve defensively; the entry
# point reports the real problem (see Snippets._host.resolve_doc()).
try:
    doc = __revit__.ActiveUIDocument.Document
except Exception:
    doc = None
try:
    uidoc = __revit__.ActiveUIDocument
except Exception:
    uidoc = None
try:
    app = __revit__.Application
except Exception:
    app = None

from Snippets._compat import make_eid, eid_value
try:
    from Snippets._host import resolve_doc, resolve_uidoc
except ImportError:
    try:
        import importlib
        import Snippets._host
        importlib.reload(Snippets._host)
        from Snippets._host import resolve_doc, resolve_uidoc
    except Exception:
        from Snippets._host import resolve_doc
        def resolve_uidoc(candidate=None):
            if candidate is not None and hasattr(candidate, 'Document') and candidate.Document is not None:
                return candidate
            try:
                from pyrevit import revit
                return revit.uidoc
            except Exception:
                return None

# ===========================================================================
# HELPERS
# ===========================================================================
def _eid_int(eid):
    return eid_value(eid)


def _make_eid(int_val):
    return make_eid(int_val)


class WarningSwallower(IFailuresPreprocessor):
    __namespace__ = "T3Lab.TagChecker"

    def PreprocessFailures(self, failuresAccessor):
        try:
            for f in failuresAccessor.GetFailureMessages():
                try:
                    failuresAccessor.DeleteWarning(f)
                except:
                    pass
        except:
            pass
        return FailureProcessingResult.Continue


def _start_transaction(name):
    t = Transaction(doc, name)
    t.Start()
    opts = t.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(WarningSwallower())
    t.SetFailureHandlingOptions(opts)
    return t


def get_solid_fill_pattern():
    try:
        for fpe in FilteredElementCollector(doc).OfClass(FillPatternElement):
            try:
                fp = fpe.GetFillPattern()
                if fp and fp.IsSolidFill:
                    return fpe
            except:
                continue
    except:
        pass
    return None


def make_override(r, g, b, solid_fill=None):
    color = Color(r, g, b)
    ogs = OverrideGraphicSettings()
    try:
        ogs.SetSurfaceForegroundPatternColor(color)
        ogs.SetSurfaceForegroundPatternVisible(True)
        if solid_fill:
            ogs.SetSurfaceForegroundPatternId(solid_fill.Id)
    except:
        try:
            ogs.SetProjectionFillColor(color)
            ogs.SetProjectionFillPatternVisible(True)
            if solid_fill:
                ogs.SetProjectionFillPatternId(solid_fill.Id)
        except:
            pass
    try:
        ogs.SetCutForegroundPatternColor(color)
        ogs.SetCutForegroundPatternVisible(True)
        if solid_fill:
            ogs.SetCutForegroundPatternId(solid_fill.Id)
    except:
        try:
            ogs.SetCutFillColor(color)
            ogs.SetCutFillPatternVisible(True)
        except:
            pass
    try:
        ogs.SetProjectionLineColor(color)
    except:
        pass
    return ogs


def get_element_location_point(elem):
    """Get center point - safe, no native crash risk."""
    try:
        loc = elem.Location
        if loc:
            if hasattr(loc, "Point"):
                return loc.Point
            if hasattr(loc, "Curve"):
                return loc.Curve.Evaluate(0.5, True)
    except:
        pass
    return None


def get_element_location_safe(elem):
    """Get center point with bounding box fallback. Only for HOST doc elements."""
    pt = get_element_location_point(elem)
    if pt:
        return pt
    try:
        bb = elem.get_BoundingBox(None)
        if bb:
            return XYZ(
                (bb.Min.X + bb.Max.X) / 2.0,
                (bb.Min.Y + bb.Max.Y) / 2.0,
                (bb.Min.Z + bb.Max.Z) / 2.0,
            )
    except:
        pass
    return None


def distance_2d(p1, p2):
    dx = p1.X - p2.X
    dy = p1.Y - p2.Y
    return math.sqrt(dx * dx + dy * dy)


# ---------------------------------------------------------------------------
# VIEW VOLUME (to filter LINKED elements to what is visible in the view)
# Fixes false "untagged" for linked Structural Framing / MEP that live in
# other levels / areas of the link but are NOT visible in the current view.
# Point-based only (no get_BoundingBox on linked elements -> no native crash).
# ---------------------------------------------------------------------------
def _bbox_world_bounds(bbox):
    """Transform an (optionally rotated) BoundingBoxXYZ to world min/max."""
    try:
        tf = bbox.Transform
        mn = bbox.Min
        mx = bbox.Max
        corners = [
            XYZ(mn.X, mn.Y, mn.Z), XYZ(mx.X, mn.Y, mn.Z),
            XYZ(mn.X, mx.Y, mn.Z), XYZ(mx.X, mx.Y, mn.Z),
            XYZ(mn.X, mn.Y, mx.Z), XYZ(mx.X, mn.Y, mx.Z),
            XYZ(mn.X, mx.Y, mx.Z), XYZ(mx.X, mx.Y, mx.Z),
        ]
        wp = []
        for c in corners:
            try:
                wp.append(tf.OfPoint(c))
            except:
                wp.append(c)
        xs = [p.X for p in wp]
        ys = [p.Y for p in wp]
        zs = [p.Z for p in wp]
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
    except:
        return None


def _crop_world_bounds(view):
    try:
        if not view.CropBoxActive:
            return None
        return _bbox_world_bounds(view.CropBox)
    except:
        return None


def _section_box_world_bounds(view):
    try:
        if isinstance(view, View3D) and view.IsSectionBoxActive:
            return _bbox_world_bounds(view.GetSectionBox())
    except:
        pass
    return None


def _nearest_level_below(z):
    """Elevation of the level immediately below z, or None."""
    try:
        below = []
        for lv in FilteredElementCollector(doc).OfClass(Level):
            try:
                e = lv.Elevation
                if e < z - 0.01:
                    below.append(e)
            except:
                continue
        if below:
            return max(below)
    except:
        pass
    return None


def _plan_z_range(view):
    """Visible Z band for a plan view.

    Beams that support a floor usually sit on the level BELOW and are shown
    via View Depth. So the band must reach down to (level-below - beam depth),
    while still excluding framing that is 2+ levels away (the old flood).
    Upper bound = Top Clip Plane (view range); lower bound = nearest level
    below the view's level, minus a margin for beam depth.
    """
    cur_z = None
    try:
        gl = view.GenLevel
        if gl:
            cur_z = gl.Elevation
    except:
        pass

    # --- Upper bound from Top Clip Plane ---
    top_z = None
    try:
        vr = view.GetViewRange()
        lid = vr.GetLevelId(PlanViewRangePlane.TopClipPlane)
        off = vr.GetOffset(PlanViewRangePlane.TopClipPlane)
        base = None
        if lid and _eid_int(lid) > 0:
            lvl = doc.GetElement(lid)
            if lvl is not None and hasattr(lvl, "Elevation"):
                base = lvl.Elevation
        if base is None and cur_z is not None:
            base = cur_z
        if base is not None:
            top_z = base + off
    except:
        pass
    if top_z is None and cur_z is not None:
        top_z = cur_z + 16.0  # ~ +5m fallback
    if top_z is None:
        return None

    # --- Lower bound: nearest level below - beam-depth margin ---
    BEAM_MARGIN = 4.0  # ft (~1.2m) below the lower level to catch beam depth
    low_z = None
    if cur_z is not None:
        nb = _nearest_level_below(cur_z)
        if nb is not None:
            low_z = nb - BEAM_MARGIN
    if low_z is None and cur_z is not None:
        low_z = cur_z - 16.0  # ~ -5m one-storey fallback
    if low_z is None:
        return None

    if low_z > top_z:
        low_z, top_z = top_z, low_z
    return (low_z, top_z)


def _compute_view_volume(view):
    """Return dict with optional 'x'/'y'/'z' = (lo, hi) in WORLD ft.
    Empty dict -> no filtering (safe fallback)."""
    vol = {}
    try:
        vtype = str(view.ViewType)
    except:
        return vol

    if vtype == "ThreeD":
        b = _section_box_world_bounds(view) or _crop_world_bounds(view)
        if b:
            vol['x'] = (b[0], b[1]); vol['y'] = (b[2], b[3]); vol['z'] = (b[4], b[5])
        return vol

    if vtype in ["FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan"]:
        zr = _plan_z_range(view)
        if zr:
            vol['z'] = zr
        cb = _crop_world_bounds(view)
        if cb:
            vol['x'] = (cb[0], cb[1]); vol['y'] = (cb[2], cb[3])
        return vol

    # Section / Elevation / Detail -> crop defines the visible rectangle
    cb = _crop_world_bounds(view)
    if cb:
        vol['x'] = (cb[0], cb[1]); vol['y'] = (cb[2], cb[3]); vol['z'] = (cb[4], cb[5])
    return vol


def _point_in_volume(pt, vol, tol=5.0):
    """True if pt is inside the view volume (with tolerance in ft).
    Missing volume or missing point -> True (don't filter)."""
    if not vol or pt is None:
        return True
    try:
        coords = {'x': pt.X, 'y': pt.Y, 'z': pt.Z}
        for axis in ('x', 'y', 'z'):
            if axis in vol:
                lo, hi = vol[axis]
                v = coords[axis]
                if v < lo - tol or v > hi + tol:
                    return False
    except:
        return True
    return True


def get_element_display_name(elem, source_doc=None):
    name = ""
    try:
        name = elem.Name or ""
    except:
        pass
    if not name:
        try:
            d = source_doc or doc
            etype = d.GetElement(elem.GetTypeId())
            if etype:
                p = etype.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                if p:
                    name = p.AsString() or ""
        except:
            pass
    cat_name = ""
    try:
        cat_name = elem.Category.Name
    except:
        pass
    return cat_name, name


def create_tag_for_linked_element(link_inst, linked_elem, host_point, view):
    """Create tag for linked element. Returns new tag or None."""
    try:
        link_ref = Reference(linked_elem).CreateLinkReference(link_inst)
    except:
        return None
    if not link_ref:
        return None

    view_z = 0
    try:
        view_z = view.Origin.Z
    except:
        try:
            level = view.GenLevel
            if level:
                view_z = level.Elevation
        except:
            pass

    vtype = str(view.ViewType)
    if vtype in ["FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan"]:
        tag_pt = XYZ(host_point.X, host_point.Y, view_z)
    else:
        tag_pt = host_point

    # Try Revit 2025+ overload first
    try:
        return IndependentTag.Create(
            doc, ElementId.InvalidElementId, view.Id,
            link_ref, False, TagOrientation.Horizontal, tag_pt
        )
    except:
        pass
    # Fallback Revit 2022-2024
    try:
        return IndependentTag.Create(
            doc, view.Id, link_ref, False,
            TagMode.TM_ADDBY_CATEGORY, TagOrientation.Horizontal, tag_pt
        )
    except:
        pass
    return None


def create_tag_for_host_element(elem, view):
    """Create tag for host element. Returns new tag or None."""
    try:
        host_ref = Reference(elem)
    except:
        return None

    loc_pt = get_element_location_safe(elem)
    if not loc_pt:
        return None

    view_z = 0
    try:
        view_z = view.Origin.Z
    except:
        try:
            level = view.GenLevel
            if level:
                view_z = level.Elevation
        except:
            pass

    vtype = str(view.ViewType)
    if vtype in ["FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan"]:
        tag_pt = XYZ(loc_pt.X, loc_pt.Y, view_z)
    else:
        tag_pt = loc_pt

    try:
        return IndependentTag.Create(
            doc, ElementId.InvalidElementId, view.Id,
            host_ref, False, TagOrientation.Horizontal, tag_pt
        )
    except:
        pass
    try:
        return IndependentTag.Create(
            doc, view.Id, host_ref, False,
            TagMode.TM_ADDBY_CATEGORY, TagOrientation.Horizontal, tag_pt
        )
    except:
        pass
    return None


# ===========================================================================
# CATEGORY MAP
# ===========================================================================
SUPPORTED_CATEGORIES = {
    "Walls": BuiltInCategory.OST_Walls,
    "Doors": BuiltInCategory.OST_Doors,
    "Windows": BuiltInCategory.OST_Windows,
    "Floors": BuiltInCategory.OST_Floors,
    "Ceilings": BuiltInCategory.OST_Ceilings,
    "Columns": BuiltInCategory.OST_Columns,
    "Structural Columns": BuiltInCategory.OST_StructuralColumns,
    "Structural Framing": BuiltInCategory.OST_StructuralFraming,
    "Structural Foundations": BuiltInCategory.OST_StructuralFoundation,
    "Rooms": BuiltInCategory.OST_Rooms,
    "Areas": BuiltInCategory.OST_Areas,
    "Furniture": BuiltInCategory.OST_Furniture,
    "Mechanical Equipment": BuiltInCategory.OST_MechanicalEquipment,
    "Plumbing Fixtures": BuiltInCategory.OST_PlumbingFixtures,
    "Electrical Equipment": BuiltInCategory.OST_ElectricalEquipment,
    "Electrical Fixtures": BuiltInCategory.OST_ElectricalFixtures,
    "Lighting Fixtures": BuiltInCategory.OST_LightingFixtures,
    "Air Terminals": BuiltInCategory.OST_DuctTerminal,
    "Sprinklers": BuiltInCategory.OST_Sprinklers,
    "Fire Alarm Devices": BuiltInCategory.OST_FireAlarmDevices,
    "Generic Models": BuiltInCategory.OST_GenericModel,
    "Parking": BuiltInCategory.OST_Parking,
    "Pipes": BuiltInCategory.OST_PipeCurves,
    "Ducts": BuiltInCategory.OST_DuctCurves,
    "Cable Trays": BuiltInCategory.OST_CableTray,
    "Conduits": BuiltInCategory.OST_Conduit,
}


# ===========================================================================
# RESULT DATA CLASS
# ===========================================================================
class TagCheckerResult(object):
    def __init__(self):
        self.total_elements = 0
        self.tagged_count = 0
        self.untagged_host_ids = []         # ElementId (host)
        self.untagged_host_elems = {}       # eid_int -> element
        self.untagged_link_data = []        # (link_inst_id_int, linked_elem_id_int, host_point, link_name)
        self.far_tag_ids = []               # ElementId (tags)
        self.far_tag_distances = {}         # tag_id_int -> dist_mm
        self.untagged_names = []            # (display, id_int, is_link)
        self.far_tag_names = []             # (text, id_int, dist_mm)
        self.new_tag_ids = []               # ElementId of auto-created tags
        self.highlighted_ids = []           # All overridden ElementIds
        self.question_tag_ids = []          # ElementId of tags showing "?"
        self.question_tag_names = []        # (display_text, tag_id_int)
        # Zoom data - parallel to untagged_names / far_tag_names / question_tag_names
        self.untagged_zoom = []             # (ElementId_or_None, XYZ_or_None, is_link)
        self.far_tag_zoom = []              # (ElementId,)
        self.question_tag_zoom = []         # (ElementId,)


# ===========================================================================
# CHECK TAGS
# ===========================================================================
def check_tags_in_view(category_names, include_links, max_distance_mm):
    result = TagCheckerResult()
    active_view = doc.ActiveView

    bics = []
    for cname in category_names:
        if cname in SUPPORTED_CATEGORIES:
            bics.append(SUPPORTED_CATEGORIES[cname])
    if not bics:
        return result

    # --- Collect all tags in current view ---
    all_tags = []
    try:
        for t in FilteredElementCollector(doc, active_view.Id) \
                .OfClass(IndependentTag).WhereElementIsNotElementType():
            all_tags.append(t)
    except:
        pass
    try:
        for t in FilteredElementCollector(doc, active_view.Id) \
                .OfClass(SpatialElementTag).WhereElementIsNotElementType():
            all_tags.append(t)
    except:
        pass

    # --- Build tagged set: (link_inst_id_int, elem_id_int) ---
    # Host elements use link_inst_id_int = 0
    tagged_set = set()
    tag_to_elem = {}  # tag_id_int -> (link_inst_id_int, elem_id_int)

    for tag in all_tags:
        tid = _eid_int(tag.Id)

        # Host tags
        try:
            if hasattr(tag, "GetTaggedLocalElementIds"):
                for eid in tag.GetTaggedLocalElementIds():
                    e = _eid_int(eid)
                    if e > 0:
                        tagged_set.add((0, e))
                        tag_to_elem[tid] = (0, e)
            else:
                try:
                    e = _eid_int(tag.TaggedLocalElementId)
                    if e > 0:
                        tagged_set.add((0, e))
                        tag_to_elem[tid] = (0, e)
                except:
                    pass
        except:
            pass

        # Robust source: GetTaggedElementIds() returns LinkElementId objects
        # that carry BOTH host and linked info in a single call. Used in
        # addition to the above so linked Structural Framing tags are not missed.
        try:
            if hasattr(tag, "GetTaggedElementIds"):
                for leid in tag.GetTaggedElementIds():
                    try:
                        host_eid = _eid_int(leid.HostElementId)
                        link_inst = _eid_int(leid.LinkInstanceId)
                        linked_eid = _eid_int(leid.LinkedElementId)
                        if linked_eid > 0 and link_inst > 0:
                            tagged_set.add((link_inst, linked_eid))
                            tag_to_elem[tid] = (link_inst, linked_eid)
                        elif host_eid > 0:
                            tagged_set.add((0, host_eid))
                            tag_to_elem[tid] = (0, host_eid)
                    except:
                        continue
        except:
            pass

        # Linked tags
        try:
            try:
                ref = tag.GetTaggedReference()
                if ref:
                    le = _eid_int(ref.LinkedElementId)
                    if le > 0:
                        li = _eid_int(ref.ElementId)
                        tagged_set.add((li, le))
                        tag_to_elem[tid] = (li, le)
            except:
                pass
            try:
                if hasattr(tag, "GetTaggedReferences"):
                    for ref in tag.GetTaggedReferences():
                        le = _eid_int(ref.LinkedElementId)
                        if le > 0:
                            li = _eid_int(ref.ElementId)
                            tagged_set.add((li, le))
                            tag_to_elem[tid] = (li, le)
            except:
                pass
        except:
            pass

    # --- Collect host elements in view ---
    host_elements = {}  # eid_int -> element
    for bic in bics:
        try:
            for elem in FilteredElementCollector(doc, active_view.Id) \
                    .OfCategory(bic).WhereElementIsNotElementType():
                e = _eid_int(elem.Id)
                if e > 0:
                    host_elements[e] = elem
        except:
            continue

    # --- Collect linked elements ---
    # SAFE: only use Location (no BoundingBox on linked elements)
    link_instances = {}  # link_inst_id_int -> (link_inst, link_doc, link_tf)
    link_elements = {}   # (link_inst_id_int, elem_id_int) -> (linked_elem, host_point)

    if include_links:
        # Visible region of the active view (used to skip link elements that
        # exist in the link file but are NOT shown in this view).
        view_volume = _compute_view_volume(active_view)
        try:
            for li in FilteredElementCollector(doc, active_view.Id) \
                    .OfClass(RevitLinkInstance).WhereElementIsNotElementType():
                li_int = _eid_int(li.Id)
                try:
                    ldoc = li.GetLinkDocument()
                    if not ldoc:
                        continue
                    ltf = li.GetTotalTransform()
                    link_instances[li_int] = (li, ldoc, ltf)

                    for bic in bics:
                        try:
                            for lelem in FilteredElementCollector(ldoc) \
                                    .OfCategory(bic).WhereElementIsNotElementType():
                                le_int = _eid_int(lelem.Id)
                                if le_int <= 0:
                                    continue
                                # Get location point ONLY (safe - no BoundingBox)
                                raw_pt = get_element_location_point(lelem)
                                host_pt = None
                                if raw_pt:
                                    try:
                                        host_pt = ltf.OfPoint(raw_pt)
                                    except:
                                        pass
                                # Skip link elements not visible in this view
                                if host_pt is not None and \
                                        not _point_in_volume(host_pt, view_volume):
                                    continue
                                link_elements[(li_int, le_int)] = (lelem, host_pt)
                        except:
                            continue
                except:
                    continue
        except:
            pass

    # --- Check host untagged ---
    for eid_int, elem in host_elements.items():
        result.total_elements += 1
        if (0, eid_int) in tagged_set:
            result.tagged_count += 1
        else:
            result.untagged_host_ids.append(elem.Id)
            result.untagged_host_elems[eid_int] = elem
            cat_name, name = get_element_display_name(elem)
            display = "{}: {} [ID:{}]".format(cat_name, name, eid_int)
            result.untagged_names.append((display, eid_int, False))
            result.untagged_zoom.append((elem.Id, None, False))

    # --- Check linked untagged ---
    if include_links:
        for (li_int, le_int), (lelem, host_pt) in link_elements.items():
            result.total_elements += 1
            if (li_int, le_int) in tagged_set:
                result.tagged_count += 1
            else:
                link_name = ""
                try:
                    link_el = doc.GetElement(_make_eid(li_int))
                    if link_el:
                        link_name = link_el.Name or ""
                        if ".rvt" in link_name:
                            link_name = link_name.split(".rvt")[0]
                except:
                    pass

                result.untagged_link_data.append((li_int, le_int, host_pt, link_name))

                cat_name, name = get_element_display_name(lelem,
                    link_instances.get(li_int, (None, None, None))[1])
                display = "[LINK:{}] {}: {} [ID:{}]".format(
                    link_name, cat_name, name, le_int)
                result.untagged_names.append((display, le_int, True))
                result.untagged_zoom.append((_make_eid(li_int), host_pt, True))

    # --- Check tag distance ---
    for tag in all_tags:
        try:
            tid = _eid_int(tag.Id)
            tag_head = None
            try:
                tag_head = tag.TagHeadPosition
            except:
                pass
            if not tag_head:
                continue

            key = tag_to_elem.get(tid)
            if not key:
                continue

            li_int, target_int = key
            elem_pt = None
            if li_int == 0:
                e = host_elements.get(target_int)
                if e:
                    elem_pt = get_element_location_safe(e)
            else:
                ld = link_elements.get((li_int, target_int))
                if ld:
                    elem_pt = ld[1]  # host_point

            if not elem_pt:
                continue

            dist_mm = distance_2d(tag_head, elem_pt) * 304.8
            if dist_mm > max_distance_mm:
                result.far_tag_ids.append(tag.Id)
                result.far_tag_distances[tid] = dist_mm
                tag_text = ""
                try:
                    tag_text = tag.TagText or ""
                except:
                    pass
                result.far_tag_names.append(
                    (tag_text or "Tag", tid, int(dist_mm)))
                result.far_tag_zoom.append((tag.Id,))
        except:
            continue

    # --- Check tags showing "?" (invalid/missing parameter) ---
    far_tag_id_set = set(_eid_int(x) for x in result.far_tag_ids)
    for tag in all_tags:
        try:
            tag_text = ""
            try:
                tag_text = tag.TagText or ""
            except:
                continue
            if "?" in tag_text:
                tid = _eid_int(tag.Id)
                # Avoid duplicating with far_tag list
                if tid not in far_tag_id_set:
                    result.question_tag_ids.append(tag.Id)

                    # Build display name
                    cat_name = ""
                    try:
                        if hasattr(tag, "Category") and tag.Category:
                            cat_name = tag.Category.Name
                    except:
                        pass
                    display = '{}: "{}" [ID:{}]'.format(
                        cat_name or "Tag", tag_text, tid)
                    result.question_tag_names.append((display, tid))
                    result.question_tag_zoom.append((tag.Id,))
        except:
            continue

    return result


# ===========================================================================
# REVIT OPERATIONS
# ===========================================================================
def apply_highlight(result):
    active_view = doc.ActiveView
    solid_fill = get_solid_fill_pattern()
    ogs_red = make_override(220, 60, 60, solid_fill)
    ogs_orange = make_override(255, 165, 0, solid_fill)
    ogs_purple = make_override(156, 39, 176, solid_fill)    # Purple for "?" tags

    t = _start_transaction("DQT - Tag Checker Highlight")
    try:
        for eid in result.untagged_host_ids:
            try:
                active_view.SetElementOverrides(eid, ogs_red)
                result.highlighted_ids.append(eid)
            except:
                continue
        for eid in result.far_tag_ids:
            try:
                active_view.SetElementOverrides(eid, ogs_orange)
                result.highlighted_ids.append(eid)
            except:
                continue
        for eid in result.question_tag_ids:
            try:
                active_view.SetElementOverrides(eid, ogs_purple)
                result.highlighted_ids.append(eid)
            except:
                continue
        t.Commit()
    except:
        if t.HasStarted():
            t.RollBack()


def auto_tag_untagged(result):
    """Auto-tag and highlight green. Returns (success, fail)."""
    active_view = doc.ActiveView
    solid_fill = get_solid_fill_pattern()
    ogs_green = make_override(60, 180, 80, solid_fill)

    t = _start_transaction("DQT - Auto Tag Untagged")
    success = 0
    fail = 0
    try:
        # Host elements
        for eid_int, elem in result.untagged_host_elems.items():
            try:
                new_tag = create_tag_for_host_element(elem, active_view)
                if new_tag:
                    result.new_tag_ids.append(new_tag.Id)
                    active_view.SetElementOverrides(new_tag.Id, ogs_green)
                    result.highlighted_ids.append(new_tag.Id)
                    success += 1
                else:
                    fail += 1
            except:
                fail += 1

        # Linked elements - need link_inst object
        for (li_int, le_int, host_pt, link_name) in result.untagged_link_data:
            if host_pt is None:
                fail += 1
                continue
            li_data = link_instances_cache.get(li_int)
            if not li_data:
                fail += 1
                continue
            link_inst = li_data[0]
            link_doc = li_data[1]
            try:
                linked_elem = link_doc.GetElement(_make_eid(le_int))
                if not linked_elem:
                    fail += 1
                    continue
                new_tag = create_tag_for_linked_element(
                    link_inst, linked_elem, host_pt, active_view)
                if new_tag:
                    result.new_tag_ids.append(new_tag.Id)
                    active_view.SetElementOverrides(new_tag.Id, ogs_green)
                    result.highlighted_ids.append(new_tag.Id)
                    success += 1
                else:
                    fail += 1
            except:
                fail += 1

        t.Commit()
    except:
        if t.HasStarted():
            t.RollBack()
        return (0, success + fail)
    return (success, fail)


def reset_colors(result):
    """Clear overrides only. Keep auto-tags."""
    active_view = doc.ActiveView
    t = _start_transaction("DQT - Reset Colors")
    try:
        blank = OverrideGraphicSettings()
        if result and result.highlighted_ids:
            for eid in result.highlighted_ids:
                try:
                    active_view.SetElementOverrides(eid, blank)
                except:
                    continue
            result.highlighted_ids = []
        t.Commit()
    except:
        if t.HasStarted():
            t.RollBack()


def clear_all(result):
    """Clear overrides AND delete auto-created tags."""
    active_view = doc.ActiveView
    t = _start_transaction("DQT - Clear All")
    try:
        blank = OverrideGraphicSettings()
        if result:
            for eid in result.highlighted_ids:
                try:
                    active_view.SetElementOverrides(eid, blank)
                except:
                    continue
            for tid in result.new_tag_ids:
                try:
                    doc.Delete(tid)
                except:
                    continue
        t.Commit()
    except:
        if t.HasStarted():
            t.RollBack()


def zoom_to_host_element(eid):
    """Select and zoom to a host element."""
    try:
        ids = List[ElementId]()
        ids.Add(eid)
        uidoc.Selection.SetElementIds(ids)
        uidoc.ShowElements(eid)
    except:
        pass


def zoom_to_point(point):
    """Zoom view to a point (for linked elements)."""
    if not point:
        return
    try:
        active_view = doc.ActiveView
        zoom_half = 5.0  # ~1.5m
        bb_min = XYZ(point.X - zoom_half, point.Y - zoom_half, point.Z - zoom_half)
        bb_max = XYZ(point.X + zoom_half, point.Y + zoom_half, point.Z + zoom_half)
        for uiv in uidoc.GetOpenUIViews():
            if _eid_int(uiv.ViewId) == _eid_int(active_view.Id):
                uiv.ZoomAndCenterRectangle(bb_min, bb_max)
                break
    except:
        pass


# Global cache for link instances (populated during check, used during auto-tag)
link_instances_cache = {}  # li_int -> (link_inst, link_doc, link_tf)


# ===========================================================================
# XAML PATH DEFINITION
# ===========================================================================
import os
import io

xaml_path = os.path.join(os.path.dirname(__file__), "Tools", "TagChecker.xaml")


# ===========================================================================
# WINDOW CLASS
# ===========================================================================
class TagCheckerWindow(object):
    """Modal WPF window. Zoom = close -> zoom -> re-show loop."""

    # Class-level state that persists across re-opens
    _shared_result = None
    _shared_checked = {}
    _shared_close_action = None  # "zoom_host", "zoom_link", "zoom_tag", "select"
    _shared_close_data = None    # data for post-close action

    def __init__(self):
        self._checkboxes = {}

        with io.open(xaml_path, 'r', encoding='utf-8') as f:
            xaml_content = f.read()
        xbytes = Encoding.UTF8.GetBytes(xaml_content)
        stream = MemoryStream(xbytes)
        self.window = XamlReader.Load(stream)
        setup_window_logo(self.window)

        # Controls
        self.tbSearch = self.window.FindName("tbSearch")
        self.spCategories = self.window.FindName("spCategories")
        self.btnSelectAll = self.window.FindName("btnSelectAll")
        self.btnSelectNone = self.window.FindName("btnSelectNone")
        self.cbIncludeLinks = self.window.FindName("cbIncludeLinks")
        self.tbMaxDist = self.window.FindName("tbMaxDist")
        self.btnCheck = self.window.FindName("btnCheck")
        self.btnAutoTag = self.window.FindName("btnAutoTag")
        self.btnSelect = self.window.FindName("btnSelect")
        self.btnResetColors = self.window.FindName("btnResetColors")
        self.btnClearAll = self.window.FindName("btnClearAll")
        self.borderResults = self.window.FindName("borderResults")
        self.tbSummary = self.window.FindName("tbSummary")
        self.borderProgress = self.window.FindName("borderProgress")
        self.tbPercent = self.window.FindName("tbPercent")
        self.tbTagResult = self.window.FindName("tbTagResult")
        self.tbUntaggedHeader = self.window.FindName("tbUntaggedHeader")
        self.lbUntagged = self.window.FindName("lbUntagged")
        self.tbFarHeader = self.window.FindName("tbFarHeader")
        self.lbFarTags = self.window.FindName("lbFarTags")
        self.tbQuestionHeader = self.window.FindName("tbQuestionHeader")
        self.lbQuestionTags = self.window.FindName("lbQuestionTags")

        # Categories
        self.all_cat_names = sorted(SUPPORTED_CATEGORIES.keys())
        if not TagCheckerWindow._shared_checked:
            for n in self.all_cat_names:
                TagCheckerWindow._shared_checked[n] = False
        self._build_checkboxes(self.all_cat_names)

        # Events
        self.btnSelectAll.Click += self._on_select_all
        self.btnSelectNone.Click += self._on_select_none
        self.tbSearch.TextChanged += self._on_search
        self.btnCheck.Click += self._on_check
        self.btnAutoTag.Click += self._on_auto_tag
        self.btnSelect.Click += self._on_select_untagged
        self.btnResetColors.Click += self._on_reset_colors
        self.btnClearAll.Click += self._on_clear_all
        self.lbUntagged.MouseDoubleClick += self._on_untagged_dblclick
        self.lbFarTags.MouseDoubleClick += self._on_far_dblclick
        self.lbQuestionTags.MouseDoubleClick += self._on_question_dblclick

        # Window chrome & footer buttons
        btn_close = self.window.FindName("btn_close")
        if btn_close:
            btn_close.Click += lambda s, e: self.window.Close()
        btn_min = self.window.FindName("btn_minimize")
        if btn_min:
            btn_min.Click += lambda s, e: setattr(self.window, "WindowState", WindowState.Minimized)
        btn_max = self.window.FindName("btn_maximize")
        if btn_max:
            btn_max.Click += self._on_toggle_maximize
        btn_footer_close = self.window.FindName("btn_footer_close")
        if btn_footer_close:
            btn_footer_close.Click += lambda s, e: self.window.Close()

        # Restore results if re-opening
        if TagCheckerWindow._shared_result:
            self._show_results()

    def _on_toggle_maximize(self, sender, args):
        if self.window.WindowState == WindowState.Maximized:
            self.window.WindowState = WindowState.Normal
        else:
            self.window.WindowState = WindowState.Maximized

    # --- Checkbox management ---
    def _build_checkboxes(self, names):
        self._sync_checked()
        self.spCategories.Children.Clear()
        self._checkboxes.clear()
        for name in names:
            cb = System.Windows.Controls.CheckBox()
            cb.Content = name
            cb.Style = self.window.FindResource("T3.CheckBox")
            cb.Margin = Thickness(0, 0, 0, 4)
            cb.IsChecked = TagCheckerWindow._shared_checked.get(name, False)
            self.spCategories.Children.Add(cb)
            self._checkboxes[name] = cb

    def _sync_checked(self):
        for n, cb in self._checkboxes.items():
            try:
                TagCheckerWindow._shared_checked[n] = bool(cb.IsChecked)
            except:
                pass

    def _get_checked_names(self):
        self._sync_checked()
        return [n for n, v in TagCheckerWindow._shared_checked.items() if v]

    def _on_search(self, sender, args):
        txt = (self.tbSearch.Text or "").strip().lower()
        if not txt:
            self._build_checkboxes(self.all_cat_names)
        else:
            self._build_checkboxes([n for n in self.all_cat_names if txt in n.lower()])

    def _on_select_all(self, sender, args):
        for n in self.all_cat_names:
            TagCheckerWindow._shared_checked[n] = True
        for cb in self._checkboxes.values():
            cb.IsChecked = True

    def _on_select_none(self, sender, args):
        for n in self.all_cat_names:
            TagCheckerWindow._shared_checked[n] = False
        for cb in self._checkboxes.values():
            cb.IsChecked = False

    # --- Check ---
    def _on_check(self, sender, args):
        self._sync_checked()
        selected = self._get_checked_names()
        if not selected:
            WPFMessageBox.Show("Please select at least one category.", "Tag Checker")
            return

        try:
            max_dist = float(self.tbMaxDist.Text or "3000")
        except:
            max_dist = 3000.0

        include_links = bool(self.cbIncludeLinks.IsChecked)

        # Clear previous
        if TagCheckerWindow._shared_result:
            clear_all(TagCheckerWindow._shared_result)
            TagCheckerWindow._shared_result = None

        # Populate link cache
        global link_instances_cache
        link_instances_cache = {}
        if include_links:
            try:
                active_view = doc.ActiveView
                for li in FilteredElementCollector(doc, active_view.Id) \
                        .OfClass(RevitLinkInstance).WhereElementIsNotElementType():
                    li_int = _eid_int(li.Id)
                    try:
                        ldoc = li.GetLinkDocument()
                        if ldoc:
                            ltf = li.GetTotalTransform()
                            link_instances_cache[li_int] = (li, ldoc, ltf)
                    except:
                        continue
            except:
                pass

        # Run check
        TagCheckerWindow._shared_result = check_tags_in_view(
            selected, include_links, max_dist)

        # Highlight
        apply_highlight(TagCheckerWindow._shared_result)

        # Show
        self._show_results()

    # --- Auto tag ---
    def _on_auto_tag(self, sender, args):
        r = TagCheckerWindow._shared_result
        if not r:
            return
        n = len(r.untagged_host_ids) + len(r.untagged_link_data)
        if n == 0:
            WPFMessageBox.Show("No untagged elements.", "Tag Checker")
            return

        success, fail = auto_tag_untagged(r)

        msg = "Auto-tagged {} elements.".format(success)
        if fail > 0:
            msg += "\n{} failed (no tag family loaded).".format(fail)
        self.tbTagResult.Text = msg
        self.tbTagResult.Visibility = Visibility.Visible
        if success > 0:
            self.btnAutoTag.IsEnabled = False

    # --- Select ---
    def _on_select_untagged(self, sender, args):
        r = TagCheckerWindow._shared_result
        if not r or not r.untagged_host_ids:
            WPFMessageBox.Show("No host untagged elements.", "Tag Checker")
            return
        TagCheckerWindow._shared_close_action = "select"
        self.window.Close()

    # --- Reset / Clear ---
    def _on_reset_colors(self, sender, args):
        r = TagCheckerWindow._shared_result
        if r:
            reset_colors(r)

    def _on_clear_all(self, sender, args):
        r = TagCheckerWindow._shared_result
        if r:
            clear_all(r)
        TagCheckerWindow._shared_result = None
        self.borderResults.Visibility = Visibility.Collapsed
        self.tbTagResult.Visibility = Visibility.Collapsed
        self.btnAutoTag.IsEnabled = False
        self.btnSelect.IsEnabled = False

    # --- Double-click zoom ---
    def _on_untagged_dblclick(self, sender, args):
        r = TagCheckerWindow._shared_result
        idx = self.lbUntagged.SelectedIndex
        if idx < 0 or not r or idx >= len(r.untagged_zoom):
            return

        zoom = r.untagged_zoom[idx]
        eid = zoom[0]
        point = zoom[1]
        is_link = zoom[2]

        if is_link:
            TagCheckerWindow._shared_close_action = "zoom_link"
            TagCheckerWindow._shared_close_data = point
        else:
            TagCheckerWindow._shared_close_action = "zoom_host"
            TagCheckerWindow._shared_close_data = eid
        self.window.Close()

    def _on_far_dblclick(self, sender, args):
        r = TagCheckerWindow._shared_result
        idx = self.lbFarTags.SelectedIndex
        if idx < 0 or not r or idx >= len(r.far_tag_zoom):
            return

        tag_eid = r.far_tag_zoom[idx][0]
        TagCheckerWindow._shared_close_action = "zoom_tag"
        TagCheckerWindow._shared_close_data = tag_eid
        self.window.Close()

    def _on_question_dblclick(self, sender, args):
        r = TagCheckerWindow._shared_result
        idx = self.lbQuestionTags.SelectedIndex
        if idx < 0 or not r or idx >= len(r.question_tag_zoom):
            return

        tag_eid = r.question_tag_zoom[idx][0]
        TagCheckerWindow._shared_close_action = "zoom_tag"
        TagCheckerWindow._shared_close_data = tag_eid
        self.window.Close()

    # --- Show results ---
    def _show_results(self):
        r = TagCheckerWindow._shared_result
        if not r:
            return

        self.borderResults.Visibility = Visibility.Visible

        total = r.total_elements
        tagged = r.tagged_count
        uh = len(r.untagged_host_ids)
        ul = len(r.untagged_link_data)
        untagged = uh + ul
        far = len(r.far_tag_ids)
        qmark = len(r.question_tag_ids)
        pct = (tagged * 100.0 / total) if total > 0 else 0

        self.tbSummary.Text = \
            "Total: {}  |  Tagged: {}  |  Untagged: {} (Host:{}, Link:{})  |  Far: {}  |  ?: {}".format(
                total, tagged, untagged, uh, ul, far, qmark)

        self.tbPercent.Text = str(int(round(pct))) + "% Tagged"
        try:
            max_w = self.borderResults.ActualWidth - 26
            if max_w <= 0:
                max_w = 540
            self.borderProgress.Width = max(0, min(max_w, max_w * pct / 100.0))
        except:
            self.borderProgress.Width = 200

        if pct >= 90:
            self.borderProgress.Background = self.window.FindResource("T3.Success.Accent")
        elif pct >= 60:
            self.borderProgress.Background = self.window.FindResource("T3.Ink")
        else:
            self.borderProgress.Background = self.window.FindResource("T3.Danger.Text")

        self.tbTagResult.Visibility = Visibility.Collapsed

        self.lbUntagged.Items.Clear()
        if r.untagged_names:
            self.tbUntaggedHeader.Visibility = Visibility.Visible
            self.tbUntaggedHeader.Text = "Untagged Elements ({}):".format(len(r.untagged_names))
            self.lbUntagged.Visibility = Visibility.Visible
            for name, eid, is_link in r.untagged_names:
                self.lbUntagged.Items.Add(name)
        else:
            self.tbUntaggedHeader.Visibility = Visibility.Collapsed
            self.lbUntagged.Visibility = Visibility.Collapsed

        self.lbFarTags.Items.Clear()
        if r.far_tag_names:
            self.tbFarHeader.Visibility = Visibility.Visible
            self.tbFarHeader.Text = "Tags Too Far ({}):".format(len(r.far_tag_names))
            self.lbFarTags.Visibility = Visibility.Visible
            for txt, tid, dist in r.far_tag_names:
                self.lbFarTags.Items.Add("{} [ID:{}] - {}mm".format(txt, tid, dist))
        else:
            self.tbFarHeader.Visibility = Visibility.Collapsed
            self.lbFarTags.Visibility = Visibility.Collapsed

        self.lbQuestionTags.Items.Clear()
        if r.question_tag_names:
            self.tbQuestionHeader.Visibility = Visibility.Visible
            self.tbQuestionHeader.Text = 'Tags Showing "?" ({}):'.format(len(r.question_tag_names))
            self.lbQuestionTags.Visibility = Visibility.Visible
            for display, tid in r.question_tag_names:
                self.lbQuestionTags.Items.Add(display)
        else:
            self.tbQuestionHeader.Visibility = Visibility.Collapsed
            self.lbQuestionTags.Visibility = Visibility.Collapsed

        self.btnAutoTag.IsEnabled = (untagged > 0)
        self.btnSelect.IsEnabled = (uh > 0)

    def show(self):
        self.window.ShowDialog()


# ===========================================================================
# MAIN LOOP: show -> close -> action -> re-show
# ===========================================================================
def show_dialog():
    global doc, uidoc
    if not doc:
        _d = resolve_doc(doc)
        doc = _d[0] if isinstance(_d, tuple) else _d
    if not uidoc:
        uidoc = resolve_uidoc(uidoc)
    if not doc or not doc.ActiveView:
        forms.alert(
            "No active document or view found.",
            title="Tag Checker", exitscript=True)
        return
    vtype = str(doc.ActiveView.ViewType)
    if vtype not in [
        "FloorPlan", "CeilingPlan", "EngineeringPlan",
        "AreaPlan", "Section", "Elevation", "Detail", "ThreeD",
    ]:
        forms.alert(
            "Please run from a Plan, Section, Elevation, or 3D view.\n"
            "Current view type: {}".format(vtype),
            title="Tag Checker", exitscript=True)

    try:
        keep_running = True
        while keep_running:
            TagCheckerWindow._shared_close_action = None
            TagCheckerWindow._shared_close_data = None

            win = TagCheckerWindow()
            win.show()

            # After dialog closes, execute deferred action
            action = TagCheckerWindow._shared_close_action
            data = TagCheckerWindow._shared_close_data

            if action == "zoom_host":
                zoom_to_host_element(data)
                # Re-open
                continue

            elif action == "zoom_link":
                zoom_to_point(data)
                continue

            elif action == "zoom_tag":
                try:
                    ids = List[ElementId]()
                    ids.Add(data)
                    uidoc.Selection.SetElementIds(ids)
                    uidoc.ShowElements(data)
                except:
                    pass
                continue

            elif action == "select":
                r = TagCheckerWindow._shared_result
                if r and r.untagged_host_ids:
                    ids = List[ElementId]()
                    for eid in r.untagged_host_ids:
                        ids.Add(eid)
                    try:
                        uidoc.Selection.SetElementIds(ids)
                    except:
                        pass
                keep_running = False

            else:
                # Normal close (X button or no action)
                keep_running = False

    except Exception as ex:
        forms.alert(
            "Error: {}\n\n{}".format(str(ex), str(sys.exc_info())),
            title="Tag Checker Error")

if __name__ == '__main__':
    show_dialog()
