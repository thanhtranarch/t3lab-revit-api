# -*- coding: utf-8 -*-
"""
CAD to Elements — Unified launcher and creation logic.

Combines Wall, Floor, Beam and MEP routing (Duct / Pipe / Cable Tray /
Conduit) creation from CAD into one integrated module. CADToElementsWindow
loads the hub XAML that contains every panel inline; sidebar buttons switch
the active panel and all Revit API logic runs in-place — no child windows
are spawned.

Copyright (c) 2026 T3Lab
All rights reserved.
"""

from __future__ import print_function

import os
import sys
import math
import codecs

import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System")
clr.AddReference("System.Xml")

import System
from System.Collections.Generic import List
from System.IO import MemoryStream, StringReader
from System.Text import Encoding
from System.Xml import XmlReader
from System.Windows import (
    Window, WindowState, Visibility, Thickness,
    HorizontalAlignment, VerticalAlignment, MessageBox,
    MessageBoxButton, MessageBoxResult, MessageBoxImage,
    FontWeights
)
from System.Windows.Controls import (
    StackPanel, TextBlock, Border, CheckBox, ComboBoxItem,
    Orientation, ScrollBarVisibility
)
from System.Windows.Markup import XamlReader
from System.Windows.Media import SolidColorBrush, Color, BrushConverter

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    Transaction, FilteredElementCollector,
    ElementId, XYZ, Line, Wall, WallType, Level,
    ImportInstance,
    CompoundStructure, MaterialFunctionAssignment
)

from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow, to_items_source

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(__file__)  # …/lib/GUI/
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))  # …/lib on path for GUI.* imports
from GUI.ProgressPauseMixin import ProgressPauseMixin
_TOOLS_DIR = os.path.join(_HERE, "Tools")
_XAML_HUB = os.path.join(_TOOLS_DIR, "CADToElements.xaml")
_XAML_WALL = os.path.join(_TOOLS_DIR, "CadtoWall.xaml")
_XAML_FLOOR = os.path.join(_TOOLS_DIR, "CadtoFloor.xaml")
_XAML_FLOOR_ITEM = os.path.join(_TOOLS_DIR, "CadtoFloorLayerItem.xaml")
_XAML_BEAM = os.path.join(_TOOLS_DIR, "CADtoBeam.xaml")

# ---------------------------------------------------------------------------
# CONSTANTS (shared)
# ---------------------------------------------------------------------------
TOLERANCE = 0.01
MERGE_TOL = 0.15
PARALLEL_TOL = 0.998
MAX_WALL_THICKNESS = 2.0
THICKNESS_ROUND_MM = 1

FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

MODE_FLOOR = "floor"
MODE_PART = "part"


# ===========================================================================
# SHARED HELPERS
# ===========================================================================

def _eid_int(eid):
    """Get integer value from ElementId — Revit 2024 uses .IntegerValue, 2025+ uses .Value."""
    try:
        return eid.IntegerValue
    except Exception:
        try:
            return eid.Value
        except Exception:
            return int(str(eid))


def mm_to_ft(mm):
    return mm / FT_TO_MM


def ft_to_mm_str(feet):
    return str(int(round(feet * FT_TO_MM)))


def safe_bool(nullable_bool):
    """Safely convert Nullable[Boolean] to Python bool."""
    try:
        if nullable_bool is None:
            return False
        return bool(nullable_bool)
    except Exception:
        return False


def load_xaml_file(path):
    """Load XAML from a file path using XamlReader."""
    with codecs.open(path, "r", "utf-8") as f:
        content = f.read()
    byte_array = Encoding.UTF8.GetBytes(content)
    stream = MemoryStream(byte_array)
    return XamlReader.Load(stream)


def load_xaml_string(xaml_string):
    """Load XAML from a string."""
    string_reader = StringReader(xaml_string)
    xml_reader = XmlReader.Create(string_reader)
    return XamlReader.Load(xml_reader)


# ===========================================================================
# CAD INSTANCE COLLECTION (shared by all three tools)
# ===========================================================================

def _add_cad_to_list(doc, inst, cad_list):
    """Helper to safely add a CAD ImportInstance to the list."""
    name = "Unknown CAD"
    try:
        cad_type = doc.GetElement(inst.GetTypeId())
        if cad_type:
            try:
                name = DB.Element.Name.GetValue(cad_type)
            except Exception:
                try:
                    p = cad_type.LookupParameter("Name")
                    if p:
                        name = p.AsString()
                except Exception:
                    pass
            if not name or name == "Unknown CAD":
                try:
                    name = str(_eid_int(cad_type.Id))
                except Exception:
                    pass
    except Exception:
        pass

    is_linked = False
    try:
        is_linked = inst.IsLinked
    except Exception:
        try:
            cad_type = doc.GetElement(inst.GetTypeId())
            if cad_type:
                efr = cad_type.GetExternalFileReference()
                if efr:
                    is_linked = True
        except Exception:
            pass

    label = "{} [{}]".format(name, "Linked" if is_linked else "Imported")

    eid = 0
    try:
        eid = _eid_int(inst.Id)
    except Exception:
        try:
            eid = inst.Id.Value
        except Exception:
            pass

    cad_list.append({
        "element": inst,
        "name": label,
        "id": eid,
        "is_linked": is_linked,
        "revit_id": inst.Id,
    })


def get_cad_instances(doc):
    """Get all CAD imports/links — compatible with Revit 2024–2026."""
    cad_list = []

    try:
        collector = FilteredElementCollector(doc).OfClass(ImportInstance)
        for inst in collector:
            try:
                _add_cad_to_list(doc, inst, cad_list)
            except Exception:
                pass
    except Exception:
        pass

    if not cad_list:
        try:
            from Autodesk.Revit.DB import BuiltInCategory
            collector2 = FilteredElementCollector(doc).OfCategory(
                BuiltInCategory.OST_ImportObjectStyles).WhereElementIsNotElementType()
            for elem in collector2:
                if isinstance(elem, ImportInstance):
                    try:
                        _add_cad_to_list(doc, elem, cad_list)
                    except Exception:
                        pass
        except Exception:
            pass

    # Deduplicate by element id
    seen_ids = set()
    unique_list = []
    for cad in cad_list:
        if cad["id"] not in seen_ids:
            seen_ids.add(cad["id"])
            unique_list.append(cad)

    return unique_list


def get_levels(doc):
    """Get all levels sorted by elevation."""
    collector = FilteredElementCollector(doc).OfClass(Level)
    lvs = []
    for lv in collector:
        try:
            name = DB.Element.Name.GetValue(lv)
            lvs.append({"name": name, "id": lv.Id, "elevation": lv.Elevation})
        except Exception:
            pass
    lvs.sort(key=lambda x: x["elevation"])
    return lvs


# ===========================================================================
# WALL — GEOMETRY EXTRACTION
# ===========================================================================

def get_cad_layers_wall(doc, cad_instance):
    """Return sorted list of every layer defined on the CAD instance (not just ones with detected geometry)."""
    layers = []
    try:
        import_cat = cad_instance.Category
        for sc in import_cat.SubCategories:
            try:
                layers.append(sc.Name)
            except Exception:
                pass
    except Exception:
        pass
    return sorted(layers)


def extract_lines_from_cad(doc, cad_instance, selected_layers):
    """Extract line segments from a CAD instance on the given layers.

    Recurses to arbitrary depth through nested DB.GeometryInstance objects
    (CAD blocks nested inside other blocks). No manual Transform is applied
    at any depth: GeometryInstance.GetInstanceGeometry() already returns
    geometry pre-transformed into the coordinate system of its immediate
    container, so repeatedly calling it while descending already yields
    fully-composed document-space coordinates at any nesting level (matching
    the original single-level implementation's behavior, which never applied
    a transform either). A previous revision of this function re-applied
    each nested instance's own .Transform via CreateTransformed()/OfPoint(),
    which double-transforms geometry whenever the CAD import itself has a
    non-identity placement transform (rotated/offset to match site
    coordinates) — walls still got created, just silently shifted/rotated
    away from their real position. Do not reintroduce that.
    """
    lines = []
    selected_set = set(selected_layers)

    def scan_geo(geo_iterable):
        for sub_obj in geo_iterable:
            if isinstance(sub_obj, DB.GeometryInstance):
                sub_geo = sub_obj.GetInstanceGeometry()
                if sub_geo:
                    scan_geo(sub_geo)
                continue
            try:
                layer_name = ""
                gstyle = doc.GetElement(sub_obj.GraphicsStyleId)
                if gstyle:
                    cat = gstyle.GraphicsStyleCategory
                    if cat:
                        layer_name = cat.Name
                if layer_name not in selected_set:
                    continue
                if isinstance(sub_obj, DB.Line):
                    p0 = sub_obj.GetEndPoint(0)
                    p1 = sub_obj.GetEndPoint(1)
                    if p0.DistanceTo(p1) > TOLERANCE:
                        lines.append({"start": p0, "end": p1, "layer": layer_name})
                elif isinstance(sub_obj, DB.PolyLine):
                    coords = sub_obj.GetCoordinates()
                    for i in range(len(coords) - 1):
                        p0 = coords[i]
                        p1 = coords[i + 1]
                        if p0.DistanceTo(p1) > TOLERANCE:
                            lines.append({"start": p0, "end": p1, "layer": layer_name})
            except Exception:
                pass

    try:
        geo_elem = cad_instance.get_Geometry(DB.Options())
        if geo_elem is None:
            return lines
        scan_geo(geo_elem)
    except Exception as ex:
        print("Error extracting CAD lines: {}".format(str(ex)))
    return lines


# ===========================================================================
# WALL — MERGE COLLINEAR
# ===========================================================================

def merge_collinear_lines(lines):
    """Merge collinear line segments with small gaps."""
    if not lines:
        return lines
    merged = True
    result = list(lines)
    while merged:
        merged = False
        new_result = []
        used = [False] * len(result)
        for i in range(len(result)):
            if used[i]:
                continue
            cur = result[i]
            cs = cur["start"]
            ce = cur["end"]
            dx = ce.X - cs.X
            dy = ce.Y - cs.Y
            clen = math.sqrt(dx * dx + dy * dy)
            if clen < TOLERANCE:
                used[i] = True
                continue
            cdx = dx / clen
            cdy = dy / clen
            for j in range(i + 1, len(result)):
                if used[j]:
                    continue
                other = result[j]
                os_ = other["start"]
                oe = other["end"]
                odx = oe.X - os_.X
                ody = oe.Y - os_.Y
                olen = math.sqrt(odx * odx + ody * ody)
                if olen < TOLERANCE:
                    used[j] = True
                    continue
                dot = abs(cdx * (odx / olen) + cdy * (ody / olen))
                if dot < PARALLEL_TOL:
                    continue
                vx = os_.X - cs.X
                vy = os_.Y - cs.Y
                cross = abs(vx * cdy - vy * cdx)
                if cross > MERGE_TOL:
                    continue
                ns = ne = None
                if ce.DistanceTo(os_) < MERGE_TOL:
                    ns, ne = cs, oe
                elif ce.DistanceTo(oe) < MERGE_TOL:
                    ns, ne = cs, os_
                elif cs.DistanceTo(os_) < MERGE_TOL:
                    ns, ne = ce, oe
                elif cs.DistanceTo(oe) < MERGE_TOL:
                    ns, ne = ce, os_
                if ns and ne and ns.DistanceTo(ne) > TOLERANCE:
                    cur = {"start": ns, "end": ne, "layer": cur["layer"]}
                    cs, ce = ns, ne
                    dx = ce.X - cs.X
                    dy = ce.Y - cs.Y
                    clen = math.sqrt(dx * dx + dy * dy)
                    if clen > TOLERANCE:
                        cdx = dx / clen
                        cdy = dy / clen
                    used[j] = True
                    merged = True
            new_result.append(cur)
            used[i] = True
        result = new_result
    return result


# ===========================================================================
# WALL — PARALLEL PAIR DETECTION
# ===========================================================================

def _project_point_on_line_2d(px, py, ax, ay, dx, dy):
    vx = px - ax
    vy = py - ay
    t = vx * dx + vy * dy
    fx = ax + t * dx
    fy = ay + t * dy
    dist = math.sqrt((px - fx) ** 2 + (py - fy) ** 2)
    return t, dist


def find_parallel_pairs(lines, max_sep=MAX_WALL_THICKNESS):
    """Detect parallel line pairs and compute centerlines using UNION extent.

    max_sep caps the pair separation (feet). The default preserves the wall
    behavior; MEP double-line callers pass a wider cap for ducts/trays.
    """
    n = len(lines)
    paired = [False] * n
    centerlines = []

    dirs = []
    for line in lines:
        dx = line["end"].X - line["start"].X
        dy = line["end"].Y - line["start"].Y
        length = math.sqrt(dx * dx + dy * dy)
        if length > TOLERANCE:
            dirs.append({"dx": dx / length, "dy": dy / length, "len": length})
        else:
            dirs.append({"dx": 0, "dy": 0, "len": 0})

    for i in range(n):
        if paired[i] or dirs[i]["len"] == 0:
            continue
        di = dirs[i]
        si = lines[i]["start"]

        candidates = []
        for j in range(n):
            if j == i or paired[j] or dirs[j]["len"] == 0:
                continue
            dj = dirs[j]
            dot = abs(di["dx"] * dj["dx"] + di["dy"] * dj["dy"])
            if dot < PARALLEL_TOL:
                continue
            sj = lines[j]["start"]
            ej = lines[j]["end"]
            _, dist_s = _project_point_on_line_2d(sj.X, sj.Y, si.X, si.Y, di["dx"], di["dy"])
            _, dist_e = _project_point_on_line_2d(ej.X, ej.Y, si.X, si.Y, di["dx"], di["dy"])
            avg_dist = (dist_s + dist_e) / 2.0
            if avg_dist > max_sep or avg_dist < TOLERANCE:
                continue
            t_js, _ = _project_point_on_line_2d(sj.X, sj.Y, si.X, si.Y, di["dx"], di["dy"])
            t_je, _ = _project_point_on_line_2d(ej.X, ej.Y, si.X, si.Y, di["dx"], di["dy"])
            overlap = min(di["len"], max(t_js, t_je)) - max(0, min(t_js, t_je))
            shorter = min(di["len"], dj["len"])
            if overlap < shorter * 0.2:
                continue
            candidates.append({"idx": j, "dist": avg_dist, "t_s": t_js, "t_e": t_je})

        if not candidates:
            continue

        candidates.sort(key=lambda c: c["dist"])
        best_dist = candidates[0]["dist"]
        same_side = [c for c in candidates if abs(c["dist"] - best_dist) < mm_to_ft(20)]

        all_t_values = [0.0, di["len"]]
        for c in same_side:
            all_t_values.append(c["t_s"])
            all_t_values.append(c["t_e"])

        t_union_start = min(all_t_values)
        t_union_end = max(all_t_values)

        if t_union_end - t_union_start < TOLERANCE:
            continue

        pi_s = XYZ(si.X + di["dx"] * t_union_start, si.Y + di["dy"] * t_union_start, 0)
        pi_e = XYZ(si.X + di["dx"] * t_union_end, si.Y + di["dy"] * t_union_end, 0)

        perp_dx = -di["dy"]
        perp_dy = di["dx"]

        mid_c = same_side[0]
        sj_pt = lines[mid_c["idx"]]["start"]
        vx = sj_pt.X - si.X
        vy = sj_pt.Y - si.Y
        side = vx * perp_dx + vy * perp_dy
        half_t = best_dist / 2.0
        if side > 0:
            offset_x = perp_dx * half_t
            offset_y = perp_dy * half_t
        else:
            offset_x = -perp_dx * half_t
            offset_y = -perp_dy * half_t

        cs = XYZ(pi_s.X + offset_x, pi_s.Y + offset_y, 0)
        ce = XYZ(pi_e.X + offset_x, pi_e.Y + offset_y, 0)

        if cs.DistanceTo(ce) > TOLERANCE:
            centerlines.append({
                "start": cs, "end": ce,
                "thickness": best_dist,
                "layer": lines[i]["layer"]
            })

        paired[i] = True
        for c in same_side:
            paired[c["idx"]] = True

    unpaired = [lines[i] for i in range(n) if not paired[i]]
    return centerlines, unpaired


# ===========================================================================
# WALL — TYPE MANAGEMENT
# ===========================================================================

def _round_thickness_mm(thickness_ft):
    mm = thickness_ft * FT_TO_MM
    return int(round(mm / THICKNESS_ROUND_MM) * THICKNESS_ROUND_MM)


def group_by_thickness(centerlines):
    """Group centerlines by rounded thickness (mm). Returns {thickness_mm: [cl_list]}."""
    groups = {}
    for cl in centerlines:
        t_mm = _round_thickness_mm(cl["thickness"])
        if t_mm not in groups:
            groups[t_mm] = []
        groups[t_mm].append(cl)
    return groups


def find_base_wall_type(doc):
    """Find a basic wall type to use as template — prefers Generic types."""
    collector = FilteredElementCollector(doc).OfClass(WallType)
    generic_type = None
    any_basic = None
    for wt in collector:
        try:
            kind = wt.Kind
            if kind != DB.WallKind.Basic:
                continue
            name = DB.Element.Name.GetValue(wt)
            any_basic = wt
            if "generic" in name.lower():
                generic_type = wt
                break
        except Exception:
            pass
    return generic_type or any_basic


def get_or_create_wall_type(doc, thickness_mm, base_type):
    """Find existing or create new WallType named 'Generic - XXXmm'."""
    target_name = "Generic - {}mm".format(thickness_mm)
    thickness_ft = thickness_mm / FT_TO_MM

    collector = FilteredElementCollector(doc).OfClass(WallType)
    for wt in collector:
        try:
            name = DB.Element.Name.GetValue(wt)
            if name == target_name:
                return wt
        except Exception:
            pass

    try:
        new_type = base_type.Duplicate(target_name)
    except Exception as ex:
        print("Error duplicating wall type: {}".format(str(ex)))
        return base_type

    try:
        cs = new_type.GetCompoundStructure()
        if cs:
            layers = cs.GetLayers()
            if layers.Count == 1:
                cs.SetLayerWidth(0, thickness_ft)
            else:
                found = False
                for idx in range(layers.Count):
                    layer = layers[idx]
                    if layer.Function == MaterialFunctionAssignment.Structure:
                        cs.SetLayerWidth(idx, thickness_ft)
                        found = True
                        break
                if not found:
                    cs.SetLayerWidth(0, thickness_ft)
            new_type.SetCompoundStructure(cs)
        print("Created wall type: {} ({}mm)".format(target_name, thickness_mm))
    except Exception as ex:
        print("Error setting wall thickness: {}".format(str(ex)))

    return new_type


# ===========================================================================
# WALL — CREATION
# ===========================================================================

def create_walls_auto(doc, centerlines, unpaired, level_id, height, use_unpaired,
                      default_thickness_mm, structural=False,
                      progress_callback=None, cancel_check=None):
    """Create walls with auto-generated wall types based on detected thickness."""
    created = 0
    failed = 0
    skipped = 0
    types_created = []
    _done = [0]
    _total = len(centerlines) + (len(unpaired) if use_unpaired else 0)

    def _tick():
        if progress_callback:
            progress_callback(_done[0], _total)
        _done[0] += 1

    level = doc.GetElement(level_id)
    level_elev = level.Elevation

    base_type = find_base_wall_type(doc)
    if not base_type:
        print("ERROR: No basic wall type found in model!")
        return 0, 0, 0, []

    groups = group_by_thickness(centerlines)

    t = Transaction(doc, "T3Lab: CAD to Wall")
    t.Start()
    try:
        wall_type_cache = {}

        for t_mm, cl_list in groups.items():
            if cancel_check and cancel_check():
                break
            if t_mm not in wall_type_cache:
                wt = get_or_create_wall_type(doc, t_mm, base_type)
                wall_type_cache[t_mm] = wt
                types_created.append("Generic - {}mm".format(t_mm))
            wt = wall_type_cache[t_mm]

            for cl in cl_list:
                if cancel_check and cancel_check():
                    break
                _tick()
                try:
                    s = cl["start"]
                    e = cl["end"]
                    start = XYZ(s.X, s.Y, level_elev)
                    end = XYZ(e.X, e.Y, level_elev)
                    if start.DistanceTo(end) < TOLERANCE:
                        skipped += 1
                        continue
                    if abs(end.X - start.X) < TOLERANCE and abs(end.Y - start.Y) < TOLERANCE:
                        skipped += 1
                        continue
                    wall_line = Line.CreateBound(start, end)
                    new_wall = Wall.Create(doc, wall_line, wt.Id, level_id, height, 0.0, False, structural)
                    if new_wall:
                        created += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

        if use_unpaired and unpaired and not (cancel_check and cancel_check()):
            default_wt_key = default_thickness_mm
            if default_wt_key not in wall_type_cache:
                wt = get_or_create_wall_type(doc, default_wt_key, base_type)
                wall_type_cache[default_wt_key] = wt
                types_created.append("Generic - {}mm".format(default_wt_key))
            wt = wall_type_cache[default_wt_key]

            for ln in unpaired:
                if cancel_check and cancel_check():
                    break
                _tick()
                try:
                    s = ln["start"]
                    e = ln["end"]
                    start = XYZ(s.X, s.Y, level_elev)
                    end = XYZ(e.X, e.Y, level_elev)
                    if start.DistanceTo(end) < TOLERANCE:
                        skipped += 1
                        continue
                    if abs(end.X - start.X) < TOLERANCE and abs(end.Y - start.Y) < TOLERANCE:
                        skipped += 1
                        continue
                    wall_line = Line.CreateBound(start, end)
                    new_wall = Wall.Create(doc, wall_line, wt.Id, level_id, height, 0.0, False, structural)
                    if new_wall:
                        created += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

        status = t.Commit()
        if status != DB.TransactionStatus.Committed:
            print("Wall transaction did not commit, status: {}".format(status))
            return 0, created + failed, skipped, []
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        print("Wall transaction error: {}".format(str(ex)))
        return 0, created + failed, skipped, []

    return created, failed, skipped, types_created


# ===========================================================================
# FLOOR — GEOMETRY EXTRACTION
# ===========================================================================

def _process_geom_obj_floor(geom_obj, layers, layer_name):
    """Process a single geometry object and add to layer dict for floors."""
    if layer_name not in layers:
        layers[layer_name] = {"curves": [], "closed_loops": [], "all_curves_count": 0}

    layer = layers[layer_name]

    try:
        if isinstance(geom_obj, DB.PolyLine):
            coords = geom_obj.GetCoordinates()
            layer["all_curves_count"] += 1
            if coords.Count >= 4:
                first = coords[0]
                last = coords[coords.Count - 1]
                if first.DistanceTo(last) < 0.01:
                    try:
                        curve_loop = DB.CurveLoop()
                        for i in range(coords.Count - 1):
                            p1 = coords[i]
                            p2 = coords[i + 1]
                            dist = p1.DistanceTo(p2)
                            if dist > 0.001:
                                ln = DB.Line.CreateBound(p1, p2)
                                curve_loop.Append(ln)
                        if not curve_loop.IsOpen():
                            layer["closed_loops"].append(curve_loop)
                    except Exception:
                        pass
        elif isinstance(geom_obj, DB.Line):
            layer["all_curves_count"] += 1
            layer["curves"].append(geom_obj)
        elif isinstance(geom_obj, DB.Arc):
            layer["all_curves_count"] += 1
            layer["curves"].append(geom_obj)
        elif isinstance(geom_obj, DB.Curve):
            layer["all_curves_count"] += 1
            layer["curves"].append(geom_obj)
    except Exception:
        pass


def get_cad_layer_geometry_floor(doc, cad_instance):
    """Extract geometry from CAD instance, organised by layer, for floor creation."""
    layers = {}
    try:
        opts = DB.Options()
        geom_elem = cad_instance.get_Geometry(opts)
        if geom_elem is None:
            return layers
        for geom_obj in geom_elem:
            if isinstance(geom_obj, DB.GeometryInstance):
                sub_geom = geom_obj.GetInstanceGeometry()
                if sub_geom:
                    for sub_obj in sub_geom:
                        layer_name = "Default"
                        try:
                            style = doc.GetElement(sub_obj.GraphicsStyleId)
                            if style:
                                cat = style.GraphicsStyleCategory
                                if cat:
                                    layer_name = cat.Name
                        except Exception:
                            pass
                        _process_geom_obj_floor(sub_obj, layers, layer_name)
            else:
                _process_geom_obj_floor(geom_obj, layers, "Default")
    except Exception:
        pass

    try:
        import_cat = cad_instance.Category
        for sc in import_cat.SubCategories:
            try:
                if sc.Name not in layers:
                    layers[sc.Name] = {"curves": [], "closed_loops": [], "all_curves_count": 0}
            except Exception:
                pass
    except Exception:
        pass
    return layers


def try_build_loops_from_curves(curves):
    """Try to build closed CurveLoops from individual curves by endpoint matching."""
    if not curves:
        return []
    closed_loops = []
    used = set()
    tolerance = 0.01

    for start_idx in range(len(curves)):
        if start_idx in used:
            continue
        try:
            loop_curves = [curves[start_idx]]
            used_in_loop = {start_idx}
            current_end = curves[start_idx].GetEndPoint(1)
            start_point = curves[start_idx].GetEndPoint(0)
            max_iter = len(curves)
            it = 0

            while it < max_iter:
                it += 1
                found = False
                if current_end.DistanceTo(start_point) < tolerance and len(loop_curves) >= 3:
                    try:
                        cl = DB.CurveLoop()
                        for c in loop_curves:
                            cl.Append(c)
                        if not cl.IsOpen():
                            closed_loops.append(cl)
                            used.update(used_in_loop)
                    except Exception:
                        pass
                    break

                for j in range(len(curves)):
                    if j in used or j in used_in_loop:
                        continue
                    try:
                        c = curves[j]
                        p0 = c.GetEndPoint(0)
                        p1 = c.GetEndPoint(1)
                        if current_end.DistanceTo(p0) < tolerance:
                            loop_curves.append(c)
                            used_in_loop.add(j)
                            current_end = p1
                            found = True
                            break
                        elif current_end.DistanceTo(p1) < tolerance:
                            rev = c.CreateReversed()
                            loop_curves.append(rev)
                            used_in_loop.add(j)
                            current_end = rev.GetEndPoint(1)
                            found = True
                            break
                    except Exception:
                        pass
                if not found:
                    break
        except Exception:
            pass
    return closed_loops


def get_floor_types(doc):
    """Get all floor types sorted by name."""
    floor_types = []
    try:
        collector = FilteredElementCollector(doc).OfClass(DB.FloorType)
        for ft in collector:
            try:
                name_param = ft.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
                type_name = name_param.AsString() if name_param and name_param.AsString() else "Unknown"
                family_name = ""
                try:
                    family_name = ft.FamilyName
                except Exception:
                    pass
                display = "{}: {}".format(family_name, type_name) if family_name else type_name
                floor_types.append({"id": ft.Id, "name": display, "element": ft})
            except Exception:
                pass
    except Exception:
        pass
    floor_types.sort(key=lambda x: x["name"])
    return floor_types


def get_ds_categories():
    """Return common categories for DirectShape."""
    return [
        {"name": "Floors", "bic": DB.BuiltInCategory.OST_Floors},
        {"name": "Generic Models", "bic": DB.BuiltInCategory.OST_GenericModel},
        {"name": "Mass", "bic": DB.BuiltInCategory.OST_Mass},
        {"name": "Structural Foundations", "bic": DB.BuiltInCategory.OST_StructuralFoundation},
        {"name": "Walls", "bic": DB.BuiltInCategory.OST_Walls},
        {"name": "Roofs", "bic": DB.BuiltInCategory.OST_Roofs},
        {"name": "Ceilings", "bic": DB.BuiltInCategory.OST_Ceilings},
        {"name": "Site", "bic": DB.BuiltInCategory.OST_Site},
    ]


# ===========================================================================
# FLOOR — CREATION
# ===========================================================================

def create_floor_from_loop(doc, curve_loop, floor_type_id, level_id, offset_mm=0, is_structural=False):
    """Create a Floor element from a CurveLoop — tries Revit 2022+ API, falls back to legacy."""
    try:
        try:
            loop_list = List[DB.CurveLoop]()
            loop_list.Add(curve_loop)
            floor = DB.Floor.Create(doc, loop_list, floor_type_id, level_id)
            if floor and offset_mm != 0:
                param = floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
                if param and not param.IsReadOnly:
                    param.Set(mm_to_ft(offset_mm))
            return floor
        except Exception:
            pass

        try:
            curve_array = DB.CurveArray()
            for curve in curve_loop:
                curve_array.Append(curve)
            floor_type = doc.GetElement(floor_type_id)
            level = doc.GetElement(level_id)
            normal = DB.XYZ.BasisZ
            floor = doc.Create.NewFloor(curve_array, floor_type, level, is_structural, normal)
            if floor and offset_mm != 0:
                param = floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
                if param and not param.IsReadOnly:
                    param.Set(mm_to_ft(offset_mm))
            return floor
        except Exception:
            pass
    except Exception:
        pass
    return None


def create_part_from_loop(doc, curve_loop, category_bic, level_id, thickness_mm=200, offset_mm=0):
    """Create a DirectShape by extruding a CurveLoop vertically."""
    try:
        thickness_ft = mm_to_ft(thickness_mm)
        offset_ft = mm_to_ft(offset_mm)

        if curve_loop.IsOpen():
            return None

        profile = List[DB.CurveLoop]()
        profile.Add(curve_loop)

        solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            profile, DB.XYZ.BasisZ, thickness_ft)

        if solid is None:
            return None

        try:
            vol = solid.Volume
            if vol < 0.0001:
                return None
        except Exception:
            return None

        if abs(offset_ft) > 0.0001:
            try:
                move_tf = DB.Transform.CreateTranslation(DB.XYZ(0, 0, offset_ft))
                moved = DB.SolidUtils.CreateTransformed(solid, move_tf)
                if moved is not None:
                    solid = moved
            except Exception:
                pass

        cat_id = ElementId(category_bic)
        ds = DB.DirectShape.CreateElement(doc, cat_id)
        geom_list = List[DB.GeometryObject]()
        geom_list.Add(solid)
        ds.SetShape(geom_list)
        ds.Name = "T3Lab Part"
        return ds
    except Exception:
        pass
    return None


def build_rect_loop_from_centerline(start_xyz, end_xyz, half_width_ft):
    """Build a closed rectangular CurveLoop by offsetting a centerline segment
    perpendicular by half_width_ft on each side. start_xyz/end_xyz must already
    be at the same Z (the loop is flat)."""
    direction = (end_xyz - start_xyz).Normalize()
    perp = DB.XYZ(-direction.Y, direction.X, 0).Normalize()
    offset = perp.Multiply(half_width_ft)
    p1 = start_xyz + offset
    p2 = end_xyz + offset
    p3 = end_xyz - offset
    p4 = start_xyz - offset
    loop = DB.CurveLoop()
    loop.Append(DB.Line.CreateBound(p1, p2))
    loop.Append(DB.Line.CreateBound(p2, p3))
    loop.Append(DB.Line.CreateBound(p3, p4))
    loop.Append(DB.Line.CreateBound(p4, p1))
    return loop


# ===========================================================================
# BEAM — GEOMETRY AND TYPE HELPERS
# ===========================================================================

def get_or_create_beam_type(doc, family_name, width_mm, height_mm):
    """Find or create a beam FamilySymbol within the specified family."""
    type_name = "{}x{}mm".format(int(width_mm), int(height_mm))

    symbols = (FilteredElementCollector(doc)
               .OfClass(DB.FamilySymbol)
               .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
               .ToElements())
    target_symbols = [s for s in symbols if s.Family.Name == family_name]

    if not target_symbols:
        return None

    for s in target_symbols:
        try:
            if DB.Element.Name.GetValue(s) == type_name:
                return s
        except Exception:
            pass

    source_symbol = target_symbols[0]
    try:
        new_symbol = source_symbol.Duplicate(type_name)
        p_b = (new_symbol.LookupParameter("b") or
               new_symbol.LookupParameter("Width") or
               new_symbol.LookupParameter("B"))
        p_h = (new_symbol.LookupParameter("h") or
               new_symbol.LookupParameter("Height") or
               new_symbol.LookupParameter("H"))
        if p_b:
            p_b.Set(width_mm * MM_TO_FT)
        if p_h:
            p_h.Set(height_mm * MM_TO_FT)
        return new_symbol
    except Exception as ex:
        print("Failed to create beam type {}: {}".format(type_name, ex))
        return source_symbol


def _pair_lines_h(lines):
    """Pair horizontal CAD lines to find beam centerlines and widths."""
    pairs = []
    used = set()
    for i, l1 in enumerate(lines):
        if i in used:
            continue
        best_j, best_dist = None, None
        for j, l2 in enumerate(lines):
            if j <= i or j in used:
                continue
            min1, max1 = min(l1["x1"], l1["x2"]), max(l1["x1"], l1["x2"])
            min2, max2 = min(l2["x1"], l2["x2"]), max(l2["x1"], l2["x2"])
            overlap = min(max1, max2) - max(min1, min2)
            min_len = min(max1 - min1, max2 - min2)
            if min_len < 1 or overlap / min_len < 0.7:
                continue
            dist = abs(l1["y1"] - l2["y1"])
            if dist < 50 or dist > 1500:
                continue
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_j = j
        if best_j is not None:
            l2 = lines[best_j]
            x_start = (min(l1["x1"], l1["x2"]) + min(l2["x1"], l2["x2"])) / 2
            x_end = (max(l1["x1"], l1["x2"]) + max(l2["x1"], l2["x2"])) / 2
            cy = (l1["y1"] + l2["y1"]) / 2
            pairs.append({
                "dir": "H", "main_s": x_start, "main_e": x_end,
                "perp": cy, "z": l1["z"],
                "width": round(abs(l1["y1"] - l2["y1"]))
            })
            used.add(i)
            used.add(best_j)
    return pairs


def _pair_lines_v(lines):
    """Pair vertical CAD lines to find beam centerlines and widths."""
    pairs = []
    used = set()
    for i, l1 in enumerate(lines):
        if i in used:
            continue
        best_j, best_dist = None, None
        for j, l2 in enumerate(lines):
            if j <= i or j in used:
                continue
            min1, max1 = min(l1["y1"], l1["y2"]), max(l1["y1"], l1["y2"])
            min2, max2 = min(l2["y1"], l2["y2"]), max(l2["y1"], l2["y2"])
            overlap = min(max1, max2) - max(min1, min2)
            min_len = min(max1 - min1, max2 - min2)
            if min_len < 1 or overlap / min_len < 0.7:
                continue
            dist = abs(l1["x1"] - l2["x1"])
            if dist < 50 or dist > 1500:
                continue
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_j = j
        if best_j is not None:
            l2 = lines[best_j]
            y_start = (min(l1["y1"], l1["y2"]) + min(l2["y1"], l2["y2"])) / 2
            y_end = (max(l1["y1"], l1["y2"]) + max(l2["y1"], l2["y2"])) / 2
            cx = (l1["x1"] + l2["x1"]) / 2
            pairs.append({
                "dir": "V", "main_s": y_start, "main_e": y_end,
                "perp": cx, "z": l1["z"],
                "width": round(abs(l1["x1"] - l2["x1"]))
            })
            used.add(i)
            used.add(best_j)
    return pairs


def _get_height_for_width(w):
    """Map beam width (mm) to standard height."""
    if w <= 200:
        return 500
    if w <= 250:
        return 600
    if w <= 300:
        return 600
    if w <= 400:
        return 800
    if w <= 500:
        return 1000
    return w * 2


# ===========================================================================
# MEP — TYPE COLLECTION
# ===========================================================================

def _collect_types_by_class(doc, cls):
    """Collect element types of a class as [{id, name, element}] sorted by name."""
    results = []
    try:
        collector = FilteredElementCollector(doc).OfClass(cls)
        for el in collector:
            try:
                name = DB.Element.Name.GetValue(el)
                results.append({"id": el.Id, "name": name or "Unknown", "element": el})
            except Exception:
                pass
    except Exception:
        pass
    results.sort(key=lambda x: x["name"])
    return results


def _collect_system_types(doc, cls):
    """Collect MEP system types of a given subclass, sorted by name."""
    results = []
    try:
        collector = FilteredElementCollector(doc).OfClass(DB.MEPSystemType)
        for st in collector:
            try:
                if isinstance(st, cls):
                    name = DB.Element.Name.GetValue(st)
                    results.append({"id": st.Id, "name": name or "Unknown", "element": st})
            except Exception:
                pass
    except Exception:
        pass
    results.sort(key=lambda x: x["name"])
    return results


def get_duct_types(doc):
    """All duct types (rectangular / round / oval) sorted by name."""
    return _collect_types_by_class(doc, DB.Mechanical.DuctType)


def get_mech_system_types(doc):
    """Mechanical system types (Supply / Return / Exhaust Air)."""
    return _collect_system_types(doc, DB.Mechanical.MechanicalSystemType)


def get_pipe_types(doc):
    """All pipe types sorted by name."""
    return _collect_types_by_class(doc, DB.Plumbing.PipeType)


def get_piping_system_types(doc):
    """Piping system types (Domestic / Sanitary / Hydronic ...)."""
    return _collect_system_types(doc, DB.Plumbing.PipingSystemType)


def get_cabletray_types(doc):
    """All cable tray types sorted by name."""
    return _collect_types_by_class(doc, DB.Electrical.CableTrayType)


def get_conduit_types(doc):
    """All conduit types sorted by name."""
    return _collect_types_by_class(doc, DB.Electrical.ConduitType)


# ===========================================================================
# MEP — CREATION
# ===========================================================================

MEP_TX_NAMES = {
    "duct": "T3Lab: CAD to Duct",
    "pipe": "T3Lab: CAD to Pipe",
    "tray": "T3Lab: CAD to Cable Tray",
    "conduit": "T3Lab: CAD to Conduit",
}


def _set_param_ft(elem, bip, value_ft):
    """Set a BuiltInParameter (feet) if present and writable. True on success."""
    try:
        p = elem.get_Parameter(bip)
        if p and not p.IsReadOnly:
            p.Set(value_ft)
            return True
    except Exception:
        pass
    return False


def _set_mep_size(elem, category_key, width_ft, height_ft):
    """Best-effort sizing — sets whichever size parameters the instance exposes.

    Ducts: rectangular/oval types expose width+height params, round types a
    diameter param — the created instance tells us its shape, so no
    MEPCurveType.Shape lookup is needed. Pipe and conduit diameters snap to
    their segment/size catalogs and may reject off-catalog values.
    Returns True if the requested size was applied; callers count a False
    as 'size not applied' and the element keeps its type default size.
    """
    if not width_ft:
        return True
    if category_key == "duct":
        if _set_param_ft(elem, DB.BuiltInParameter.RBS_CURVE_WIDTH_PARAM, width_ft):
            if height_ft:
                _set_param_ft(elem, DB.BuiltInParameter.RBS_CURVE_HEIGHT_PARAM, height_ft)
            return True
        # Round duct — the width input drives the diameter
        return _set_param_ft(elem, DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM, width_ft)
    if category_key == "pipe":
        return _set_param_ft(elem, DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM, width_ft)
    if category_key == "tray":
        ok_w = _set_param_ft(elem, DB.BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM, width_ft)
        ok_h = True
        if height_ft:
            ok_h = _set_param_ft(elem, DB.BuiltInParameter.RBS_CABLETRAY_HEIGHT_PARAM, height_ft)
        return ok_w and ok_h
    if category_key == "conduit":
        return _set_param_ft(elem, DB.BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM, width_ft)
    return False


def _nearest_end_connector(elem, point, tol_ft):
    """Nearest unconnected end connector of an MEP curve within tol_ft of point."""
    best = None
    best_dist = None
    try:
        for c in elem.ConnectorManager.Connectors:
            try:
                if c.ConnectorType != DB.ConnectorType.End:
                    continue
                if c.IsConnected:
                    continue
                dist = c.Origin.DistanceTo(point)
                if dist <= tol_ft and (best_dist is None or dist < best_dist):
                    best = c
                    best_dist = dist
            except Exception:
                pass
    except Exception:
        pass
    return best


def connect_with_elbows(doc, created_items, tol_ft=0.03):
    """Best-effort elbow placement at shared endpoints of created MEP curves.

    created_items: [{"elem": MEPCurve, "p0": XYZ, "p1": XYZ}]. Skips T/X
    junctions (3+ segment ends at one point) and near-collinear joints.
    Every corner attempt is individually guarded so a failed fitting (no
    elbow in the type's routing preferences, unsupported angle) never aborts
    the enclosing transaction. Returns (placed, skipped).
    """
    placed = 0
    skipped = 0
    try:
        ends = []
        for idx, item in enumerate(created_items):
            p0 = item["p0"]
            p1 = item["p1"]
            if p0.DistanceTo(p1) < TOLERANCE:
                continue
            d = (p1 - p0).Normalize()
            ends.append({"idx": idx, "pt": p0, "away": d})
            ends.append({"idx": idx, "pt": p1, "away": d.Negate()})

        used = [False] * len(ends)
        for i in range(len(ends)):
            if used[i]:
                continue
            group = [i]
            for j in range(i + 1, len(ends)):
                if not used[j] and ends[i]["pt"].DistanceTo(ends[j]["pt"]) <= tol_ft:
                    group.append(j)
            if len(group) < 2:
                continue
            for g in group:
                used[g] = True
            if len(group) > 2:
                skipped += 1  # T/X junction — connect manually in Revit
                continue
            a = ends[group[0]]
            b = ends[group[1]]
            if a["idx"] == b["idx"]:
                continue
            dot = a["away"].DotProduct(b["away"])
            if abs(dot) > 0.985:
                skipped += 1  # straight continuation or fold-back — no elbow possible
                continue
            c1 = _nearest_end_connector(created_items[a["idx"]]["elem"], a["pt"], tol_ft)
            c2 = _nearest_end_connector(created_items[b["idx"]]["elem"], b["pt"], tol_ft)
            if not c1 or not c2:
                skipped += 1
                continue
            try:
                doc.Create.NewElbowFitting(c1, c2)
                placed += 1
            except Exception:
                skipped += 1
    except Exception:
        pass
    return placed, skipped


def create_mep_runs(doc, category_key, segments, level_id, offset_ft,
                    type_id, system_type_id=None,
                    width_ft=None, height_ft=None, auto_elbow=True):
    """Create one MEP category's runs inside a single transaction.

    segments: [{"start": XYZ, "end": XYZ, "width_ft": float or None}] —
    a per-segment width (from double-line detection) overrides width_ft.
    Only the CAD XY is used; Z = level elevation + offset_ft.
    Returns (created, failed, skipped, size_failed, elbow_placed, elbow_skipped).
    """
    created = 0
    failed = 0
    skipped = 0
    size_failed = 0
    elbow_placed = 0
    elbow_skipped = 0

    level = doc.GetElement(level_id)
    z_ft = level.Elevation + offset_ft

    t = Transaction(doc, MEP_TX_NAMES.get(category_key, "T3Lab: CAD to MEP"))
    t.Start()
    try:
        created_items = []
        for seg in segments:
            try:
                s = seg["start"]
                e = seg["end"]
                p0 = XYZ(s.X, s.Y, z_ft)
                p1 = XYZ(e.X, e.Y, z_ft)
                if p0.DistanceTo(p1) < TOLERANCE:
                    skipped += 1
                    continue
                if category_key == "duct":
                    elem = DB.Mechanical.Duct.Create(
                        doc, system_type_id, type_id, level_id, p0, p1)
                elif category_key == "pipe":
                    elem = DB.Plumbing.Pipe.Create(
                        doc, system_type_id, type_id, level_id, p0, p1)
                elif category_key == "tray":
                    elem = DB.Electrical.CableTray.Create(
                        doc, type_id, p0, p1, level_id)
                elif category_key == "conduit":
                    elem = DB.Electrical.Conduit.Create(
                        doc, type_id, p0, p1, level_id)
                else:
                    skipped += 1
                    continue
                if not elem:
                    failed += 1
                    continue
                w = seg.get("width_ft") or width_ft
                try:
                    if not _set_mep_size(elem, category_key, w, height_ft):
                        size_failed += 1
                except Exception:
                    size_failed += 1
                created_items.append({"elem": elem, "p0": p0, "p1": p1})
                created += 1
            except Exception:
                failed += 1

        if auto_elbow and len(created_items) >= 2:
            try:
                # Connector origins are only reliable after sizes are applied
                doc.Regenerate()
            except Exception:
                pass
            elbow_placed, elbow_skipped = connect_with_elbows(doc, created_items)

        if created > 0:
            status = t.Commit()
            if status != DB.TransactionStatus.Committed:
                print("MEP transaction did not commit, status: {}".format(status))
                return 0, created + failed, skipped, 0, 0, 0
        else:
            t.RollBack()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        print("MEP transaction error: {}".format(str(ex)))
        return 0, created + failed, skipped, size_failed, 0, 0

    return created, failed, skipped, size_failed, elbow_placed, elbow_skipped


# UI configuration per MEP category — drives the shared _run_mep flow.
MEP_UI_CONFIG = {
    "duct": {
        "title": "CAD to Duct", "noun": "ducts", "type_label": "duct type",
        "double": True, "system": True,
        "type_combo": "cmb_duct_type",
        "width_box": "txt_duct_width", "default_width": 300.0,
        "height_box": "txt_duct_height", "default_height": 250.0,
        "offset_box": "txt_duct_offset", "default_offset": 2800.0,
        "elbow_chk": "chk_duct_elbows", "merge_chk": "chk_duct_merge",
    },
    "pipe": {
        "title": "CAD to Pipe", "noun": "pipes", "type_label": "pipe type",
        "double": False, "system": True,
        "type_combo": "cmb_pipe_type",
        "width_box": "txt_pipe_diameter", "default_width": 100.0,
        "height_box": None, "default_height": None,
        "offset_box": "txt_pipe_offset", "default_offset": 2600.0,
        "elbow_chk": "chk_pipe_elbows", "merge_chk": "chk_pipe_merge",
    },
    "tray": {
        "title": "CAD to Cable Tray", "noun": "cable trays", "type_label": "cable tray type",
        "double": True, "system": False,
        "type_combo": "cmb_tray_type",
        "width_box": "txt_tray_width", "default_width": 300.0,
        "height_box": "txt_tray_height", "default_height": 100.0,
        "offset_box": "txt_tray_offset", "default_offset": 2700.0,
        "elbow_chk": "chk_tray_elbows", "merge_chk": "chk_tray_merge",
    },
    "conduit": {
        "title": "CAD to Conduit", "noun": "conduits", "type_label": "conduit type",
        "double": False, "system": False,
        "type_combo": "cmb_conduit_type",
        "width_box": "txt_conduit_diameter", "default_width": 25.0,
        "height_box": None, "default_height": None,
        "offset_box": "txt_conduit_offset", "default_offset": 2700.0,
        "elbow_chk": "chk_conduit_elbows", "merge_chk": "chk_conduit_merge",
    },
}

# MEP double-line detection: cap the parallel-pair separation at 2500 mm
MEP_MAX_PAIR_SEP_MM = 2500.0


# ===========================================================================
# LAYER DATA (Floor)
# ===========================================================================

class LayerData(object):
    def __init__(self, name, closed_loops, all_curves_count):
        self.name = name
        self.closed_loops = closed_loops
        self.all_curves_count = all_curves_count
        self.closed_count = len(closed_loops)
        self.is_selected = False


# ===========================================================================
# WALL WINDOW
# ===========================================================================

class _CADtoWallWindow(ProgressPauseMixin):
    """Full Wall creation window — loads CadtoWall.xaml and embeds all logic."""

    # ProgressPauseMixin — object-wrapper window; the mixin reaches the
    # dispatcher through self.window.Dispatcher (see _pp_dispatcher).
    PP_PANEL      = "wall_progress_panel"
    PP_BAR        = "wall_pb"
    PP_PAUSE      = "wall_btn_pause"
    PP_STOP       = "wall_btn_stop"
    PP_PAUSE_ICON = "wall_btn_pause_icon"
    PP_PAUSE_TEXT = "wall_btn_pause_label"
    PP_STATUS     = "txtStatus"
    PP_STOP_MSG   = u"Stopping… finishing current wall"

    def __init__(self, doc, uidoc):
        self.doc = doc
        self.uidoc = uidoc

        xr = load_xaml_file(_XAML_WALL)
        self.window = xr

        self.cmbCAD = xr.FindName("cmbCAD")
        self.cmbLevel = xr.FindName("cmbLevel")
        self.txtHeight = xr.FindName("txtHeight")
        self.txtDefaultThk = xr.FindName("txtDefaultThk")
        self.chkStructural = xr.FindName("chkStructural")
        self.chkMerge = xr.FindName("chkMerge")
        self.chkUnpaired = xr.FindName("chkUnpaired")
        self.chkSelectAll = xr.FindName("chkSelectAll")
        self.txtSummary = xr.FindName("txtSummary")
        self.txtSearch = xr.FindName("txtSearch")
        self.layerPanel = xr.FindName("layerPanel")
        self.btnRefresh = xr.FindName("btnRefresh")
        self.btnPreview = xr.FindName("btnPreview")
        self.btnCreate = xr.FindName("btnCreate")
        self.btnClose = xr.FindName("btnClose")
        self.txtStatus = xr.FindName("txtStatus")

        # Try optional window control buttons
        for btn_name, handler in [
            ("btn_minimize", self._on_minimize),
            ("btn_maximize", self._on_maximize),
            ("btn_close_chrome", self._on_close),
        ]:
            btn = xr.FindName(btn_name)
            if btn is not None:
                btn.Click += handler

        self.cad_list = []
        self.levels = []
        self.layer_checkboxes = {}

        if self.cmbCAD is not None:
            self.cmbCAD.SelectionChanged += self._on_cad_changed
        if self.chkSelectAll is not None:
            self.chkSelectAll.Checked += self._on_select_all_checked
            self.chkSelectAll.Unchecked += self._on_select_all_unchecked
        if self.txtSearch is not None:
            self.txtSearch.TextChanged += self._on_search_changed
        if self.btnRefresh is not None:
            self.btnRefresh.Click += self._on_refresh
        if self.btnPreview is not None:
            self.btnPreview.Click += self._on_preview
        if self.btnCreate is not None:
            self.btnCreate.Click += self._on_create
        if self.btnClose is not None:
            self.btnClose.Click += self._on_close

        # Progress + Pause/Stop (ProgressPauseMixin)
        self.wall_progress_panel  = xr.FindName("wall_progress_panel")
        self.wall_pb              = xr.FindName("wall_pb")
        self.wall_btn_pause       = xr.FindName("wall_btn_pause")
        self.wall_btn_pause_icon  = xr.FindName("wall_btn_pause_icon")
        self.wall_btn_pause_label = xr.FindName("wall_btn_pause_label")
        self.wall_btn_stop        = xr.FindName("wall_btn_stop")
        if self.wall_btn_pause is not None:
            self.wall_btn_pause.Click += self.pause_resume_clicked
        if self.wall_btn_stop is not None:
            self.wall_btn_stop.Click += self.stop_clicked

        self._load_data()

    def _status(self, msg):
        if self.txtStatus is not None:
            self.txtStatus.Text = str(msg)

    def _load_data(self):
        self.cad_list = get_cad_instances(self.doc)
        if self.cmbCAD is None:
            return
        self.cmbCAD.Items.Clear()
        if not self.cad_list:
            item = ComboBoxItem()
            item.Content = "No CAD found in model"
            item.IsEnabled = False
            self.cmbCAD.Items.Add(item)
        else:
            for cad in self.cad_list:
                item = ComboBoxItem()
                item.Content = cad["name"]
                self.cmbCAD.Items.Add(item)
            self.cmbCAD.SelectedIndex = 0

        self.levels = get_levels(self.doc)
        if self.cmbLevel is None:
            return
        self.cmbLevel.Items.Clear()
        for lv in self.levels:
            item = ComboBoxItem()
            item.Content = "{} (Elev: {} mm)".format(lv["name"], ft_to_mm_str(lv["elevation"]))
            self.cmbLevel.Items.Add(item)
        if self.levels:
            try:
                av = self.doc.ActiveView
                alid = av.GenLevel.Id if hasattr(av, "GenLevel") and av.GenLevel else None
                if alid:
                    for i, lv in enumerate(self.levels):
                        if _eid_int(lv["id"]) == _eid_int(alid):
                            self.cmbLevel.SelectedIndex = i
                            break
                    else:
                        self.cmbLevel.SelectedIndex = 0
                else:
                    self.cmbLevel.SelectedIndex = 0
            except Exception:
                self.cmbLevel.SelectedIndex = 0

    def _load_layers(self, cad_data):
        if self.layerPanel is None:
            return
        self.layerPanel.Children.Clear()
        self.layer_checkboxes = {}
        if not cad_data:
            return
        layers = get_cad_layers_wall(self.doc, cad_data["element"])
        if not layers:
            tb = TextBlock()
            tb.Text = "No layers found"
            tb.FontSize = 11
            tb.Margin = Thickness(8, 8, 8, 8)
            self.layerPanel.Children.Add(tb)
            return
        for layer_name in layers:
            border = Border()
            border.Padding = Thickness(8, 4, 8, 4)
            border.Margin = Thickness(0, 0, 0, 1)
            border.Tag = layer_name
            sp = StackPanel()
            sp.Orientation = Orientation.Horizontal
            cb = CheckBox()
            cb.VerticalContentAlignment = VerticalAlignment.Center
            cb.Margin = Thickness(0, 0, 8, 0)
            cb.IsChecked = System.Nullable[System.Boolean](False)
            cb.Tag = layer_name
            tb = TextBlock()
            tb.Text = layer_name
            tb.FontSize = 11
            tb.Foreground = SolidColorBrush(Color.FromRgb(51, 51, 51))
            tb.VerticalAlignment = VerticalAlignment.Center
            sp.Children.Add(cb)
            sp.Children.Add(tb)
            border.Child = sp
            self.layerPanel.Children.Add(border)
            self.layer_checkboxes[layer_name] = cb
        if self.txtSummary is not None:
            self.txtSummary.Text = "{} layers found. Select layers with wall lines.".format(len(layers))

    def _get_selected_layers(self):
        selected = []
        for name, cb in self.layer_checkboxes.items():
            try:
                if cb.IsChecked == True:
                    selected.append(name)
            except Exception:
                pass
        return selected

    def _get_cad(self):
        if self.cmbCAD is None:
            return None
        idx = self.cmbCAD.SelectedIndex
        if idx < 0 or idx >= len(self.cad_list):
            return None
        return self.cad_list[idx]

    def _get_lv(self):
        if self.cmbLevel is None:
            return None
        idx = self.cmbLevel.SelectedIndex
        if idx < 0 or idx >= len(self.levels):
            return None
        return self.levels[idx]

    def _get_height(self):
        try:
            return mm_to_ft(float(self.txtHeight.Text.strip()))
        except Exception:
            return mm_to_ft(3000)

    def _get_default_thk(self):
        try:
            return int(float(self.txtDefaultThk.Text.strip()))
        except Exception:
            return 200

    def _chk(self, c):
        try:
            return c.IsChecked == True
        except Exception:
            return False

    def _process(self):
        cad = self._get_cad()
        sel = self._get_selected_layers()
        lines = extract_lines_from_cad(self.doc, cad["element"], sel)
        raw = len(lines)
        if self.chkMerge is not None and self._chk(self.chkMerge):
            lines = merge_collinear_lines(lines)
        cl, up = find_parallel_pairs(lines)
        return cl, up, raw, len(lines)

    def _on_cad_changed(self, sender, args):
        cad = self._get_cad()
        if cad:
            self._load_layers(cad)

    def _on_select_all_checked(self, sender, args):
        for cb in self.layer_checkboxes.values():
            cb.IsChecked = System.Nullable[System.Boolean](True)

    def _on_select_all_unchecked(self, sender, args):
        for cb in self.layer_checkboxes.values():
            cb.IsChecked = System.Nullable[System.Boolean](False)

    def _on_search_changed(self, sender, args):
        if self.txtSearch is None or self.layerPanel is None:
            return
        txt = self.txtSearch.Text.strip().lower()
        for i in range(self.layerPanel.Children.Count):
            child = self.layerPanel.Children[i]
            if isinstance(child, Border) and child.Tag:
                name = str(child.Tag).lower()
                child.Visibility = (Visibility.Visible
                                    if (not txt or txt in name)
                                    else Visibility.Collapsed)

    def _on_refresh(self, sender, args):
        self._load_data()
        cad = self._get_cad()
        if cad:
            self._load_layers(cad)

    def _on_preview(self, sender, args):
        cad = self._get_cad()
        if not cad:
            MessageBox.Show("Select a CAD instance.", "CAD to Wall",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        sel = self._get_selected_layers()
        if not sel:
            MessageBox.Show("Select at least one layer.", "CAD to Wall",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        cl, up, raw, merged_count = self._process()
        groups = group_by_thickness(cl)

        msg = "Raw lines: {} | After merge: {}\n".format(raw, merged_count)
        msg += "Parallel pairs: {} centerline walls\n".format(len(cl))
        msg += "Unpaired lines: {}\n\n".format(len(up))

        if groups:
            msg += "Wall types to create:\n"
            for t_mm in sorted(groups.keys()):
                count = len(groups[t_mm])
                msg += "  Generic - {}mm : {} walls\n".format(t_mm, count)

        if self.txtSummary is not None:
            self.txtSummary.Text = msg
        self._status("Preview: {} wall types, {} total walls".format(len(groups), len(cl)))

    def _on_create(self, sender, args):
        cad = self._get_cad()
        if not cad:
            MessageBox.Show("Select a CAD instance.", "CAD to Wall",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        sel = self._get_selected_layers()
        if not sel:
            MessageBox.Show("Select at least one layer.", "CAD to Wall",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        lv = self._get_lv()
        if not lv:
            MessageBox.Show("Select a Level.", "CAD to Wall",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        height = self._get_height()
        if height <= 0:
            MessageBox.Show("Enter a valid wall height.", "CAD to Wall",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        cl, up, _raw, _merged = self._process()
        use_up = self.chkUnpaired is not None and self._chk(self.chkUnpaired)
        total = len(cl) + (len(up) if use_up else 0)
        if total == 0:
            MessageBox.Show("No lines found.", "CAD to Wall",
                            MessageBoxButton.OK, MessageBoxImage.Information)
            return

        groups = group_by_thickness(cl)
        msg = "Create walls?\n\n"
        for t_mm in sorted(groups.keys()):
            msg += "Generic - {}mm: {} walls\n".format(t_mm, len(groups[t_mm]))
        if use_up:
            thk = self._get_default_thk()
            msg += "\nUnpaired: {} walls (Generic - {}mm)\n".format(len(up), thk)
        msg += "\nTotal: {} walls\n".format(total)
        msg += "Level: {}\nHeight: {} mm".format(
            lv["name"],
            self.txtHeight.Text.strip() if self.txtHeight is not None else "?"
        )

        if (MessageBox.Show(msg, "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
                != MessageBoxResult.Yes):
            return

        structural = self.chkStructural is not None and self._chk(self.chkStructural)

        self.begin_progress(total, disable=[self.btnCreate])
        def progress_cb(current, total_):
            self.step_progress(current, "Creating wall {}/{}...".format(current + 1, total_))
        created, failed, skipped, types_created = create_walls_auto(
            self.doc, cl, up, lv["id"], height, use_up, self._get_default_thk(), structural,
            progress_callback=progress_cb, cancel_check=lambda: self.is_cancelled)
        cancelled = self.is_cancelled
        self.end_progress()

        result_msg = ("Cancelled — created: {} walls\n".format(created)
                      if cancelled else "Created: {} walls\n".format(created))
        if failed > 0:
            result_msg += "Failed: {}\n".format(failed)
        if skipped > 0:
            result_msg += "Skipped: {}\n".format(skipped)
        if types_created:
            result_msg += "\nWall types created/used:\n"
            for tn in types_created:
                result_msg += "  {}\n".format(tn)

        if self.txtSummary is not None:
            self.txtSummary.Text = "Done: {} walls, {} types".format(created, len(types_created))
        self._status("Done: {} created, {} failed".format(created, failed))
        MessageBox.Show(result_msg, "CAD to Wall",
                        MessageBoxButton.OK, MessageBoxImage.Information)

    def _on_minimize(self, sender, args):
        self.window.WindowState = WindowState.Minimized

    def _on_maximize(self, sender, args):
        if self.window.WindowState == WindowState.Maximized:
            self.window.WindowState = WindowState.Normal
        else:
            self.window.WindowState = WindowState.Maximized

    def _on_close(self, sender, args):
        self.window.Close()

    def show(self):
        self.window.ShowDialog()


# ===========================================================================
# FLOOR WINDOW
# ===========================================================================

class _CADtoFloorWindow(ProgressPauseMixin):
    """Full Floor/Part creation window — loads CadtoFloor.xaml and embeds all logic."""

    # ProgressPauseMixin — object-wrapper window (dispatcher via self.window)
    PP_PANEL      = "floor_progress_panel"
    PP_BAR        = "floor_pb"
    PP_PAUSE      = "floor_btn_pause"
    PP_STOP       = "floor_btn_stop"
    PP_PAUSE_ICON = "floor_btn_pause_icon"
    PP_PAUSE_TEXT = "floor_btn_pause_label"
    PP_STATUS     = "txt_status"
    PP_STOP_MSG   = u"Stopping… finishing current loop"

    def __init__(self, doc, uidoc):
        self.doc = doc
        self.uidoc = uidoc

        xr = load_xaml_file(_XAML_FLOOR)
        self.window = xr

        def _n(name):
            return xr.FindName(name)

        self.rb_floor = _n("rb_floor")
        self.rb_part = _n("rb_part")
        self.cmb_cad_files = _n("cmb_cad_files")
        self.btn_pick_cad = _n("btn_pick_cad")
        self.btn_scan = _n("btn_scan")
        self.cmb_levels = _n("cmb_levels")
        self.pnl_floor_type_row = _n("pnl_floor_type_row")
        self.cmb_floor_types = _n("cmb_floor_types")
        self.pnl_part_row1 = _n("pnl_part_row1")
        self.pnl_part_row2 = _n("pnl_part_row2")
        self.cmb_ds_category = _n("cmb_ds_category")
        self.txt_thickness = _n("txt_thickness")
        self.txt_offset = _n("txt_offset")
        self.chk_structural = _n("chk_structural")
        self.txt_total_layers = _n("txt_total_layers")
        self.txt_selected_layers = _n("txt_selected_layers")
        self.txt_total_curves = _n("txt_total_curves")
        self.txt_elements_created = _n("txt_elements_created")
        self.lbl_created = _n("lbl_created")
        self.txt_search = _n("txt_search")
        self.btn_select_all = _n("btn_select_all")
        self.btn_select_none = _n("btn_select_none")
        self.pnl_layers = _n("pnl_layers")
        self.txt_status = _n("txt_status")
        self.btn_refresh = _n("btn_refresh")
        self.btn_create = _n("btn_create")
        self.btn_close = _n("btn_close")

        # Window chrome controls
        for btn_name, handler in [
            ("btn_minimize", self._on_minimize),
            ("btn_maximize", self._on_maximize),
            ("btn_close_chrome", self._on_close),
        ]:
            btn = _n(btn_name)
            if btn is not None:
                btn.Click += handler

        self.cad_instances = []
        self.floor_types = []
        self.levels = []
        self.ds_categories = get_ds_categories()
        self.layer_data = []
        self.layer_checkboxes = []
        self.elements_created_count = 0
        self.current_mode = MODE_FLOOR
        self._pick_element_id = None

        if self.btn_pick_cad is not None:
            self.btn_pick_cad.Click += self._on_pick_cad
        if self.btn_scan is not None:
            self.btn_scan.Click += self._on_scan_layers
        if self.txt_search is not None:
            self.txt_search.TextChanged += self._on_search_changed
        if self.btn_select_all is not None:
            self.btn_select_all.Click += self._on_select_all
        if self.btn_select_none is not None:
            self.btn_select_none.Click += self._on_select_none
        if self.btn_refresh is not None:
            self.btn_refresh.Click += self._on_refresh
        if self.btn_create is not None:
            self.btn_create.Click += self._on_create_elements
        if self.btn_close is not None:
            self.btn_close.Click += self._on_close
        if self.rb_floor is not None:
            self.rb_floor.Checked += self._on_mode_changed
        if self.rb_part is not None:
            self.rb_part.Checked += self._on_mode_changed

        # Progress + Pause/Stop (ProgressPauseMixin)
        self.floor_progress_panel  = _n("floor_progress_panel")
        self.floor_pb              = _n("floor_pb")
        self.floor_btn_pause       = _n("floor_btn_pause")
        self.floor_btn_pause_icon  = _n("floor_btn_pause_icon")
        self.floor_btn_pause_label = _n("floor_btn_pause_label")
        self.floor_btn_stop        = _n("floor_btn_stop")
        if self.floor_btn_pause is not None:
            self.floor_btn_pause.Click += self.pause_resume_clicked
        if self.floor_btn_stop is not None:
            self.floor_btn_stop.Click += self.stop_clicked

        self._load_cad_files()
        self._load_floor_types()
        self._load_levels()
        self._load_ds_categories()
        self._update_mode_ui()

    def _update_status(self, msg):
        if self.txt_status is not None:
            self.txt_status.Text = str(msg)

    def _update_mode_ui(self):
        if self.rb_floor is not None and safe_bool(self.rb_floor.IsChecked):
            self.current_mode = MODE_FLOOR
            if self.pnl_floor_type_row is not None:
                self.pnl_floor_type_row.Visibility = Visibility.Visible
            if self.pnl_part_row1 is not None:
                self.pnl_part_row1.Visibility = Visibility.Collapsed
            if self.pnl_part_row2 is not None:
                self.pnl_part_row2.Visibility = Visibility.Collapsed
            if self.chk_structural is not None:
                self.chk_structural.Visibility = Visibility.Visible
            if self.btn_create is not None:
                self.btn_create.Content = "Create Floors"
            if self.lbl_created is not None:
                self.lbl_created.Text = "Floors Created"
        else:
            self.current_mode = MODE_PART
            if self.pnl_floor_type_row is not None:
                self.pnl_floor_type_row.Visibility = Visibility.Collapsed
            if self.pnl_part_row1 is not None:
                self.pnl_part_row1.Visibility = Visibility.Visible
            if self.pnl_part_row2 is not None:
                self.pnl_part_row2.Visibility = Visibility.Visible
            if self.chk_structural is not None:
                self.chk_structural.Visibility = Visibility.Collapsed
            if self.btn_create is not None:
                self.btn_create.Content = "Create Parts"
            if self.lbl_created is not None:
                self.lbl_created.Text = "Parts Created"

    def _update_summary(self):
        total_layers = len(self.layer_data)
        selected = 0
        for _, ld in self.layer_checkboxes:
            if ld.is_selected:
                selected += 1
        total_closed = sum(ld.closed_count for ld in self.layer_data)

        if self.txt_total_layers is not None:
            self.txt_total_layers.Text = str(total_layers)
        if self.txt_selected_layers is not None:
            self.txt_selected_layers.Text = str(selected)
        if self.txt_total_curves is not None:
            self.txt_total_curves.Text = str(total_closed)
        if self.txt_elements_created is not None:
            self.txt_elements_created.Text = str(self.elements_created_count)
        if self.btn_create is not None:
            self.btn_create.IsEnabled = selected > 0

    def _load_cad_files(self):
        self.cad_instances = get_cad_instances(self.doc)
        if self.cmb_cad_files is None:
            return
        self.cmb_cad_files.Items.Clear()

        if not self.cad_instances:
            self.cmb_cad_files.Items.Add("No CAD files found in model")
            self.cmb_cad_files.SelectedIndex = 0
            self.cmb_cad_files.IsEnabled = False
            if self.btn_scan is not None:
                self.btn_scan.IsEnabled = False
            self._update_status("No linked/imported CAD files found.")
            return

        for cad in self.cad_instances:
            prefix = "[Linked]" if cad["is_linked"] else "[Imported]"
            display = "{} {} (ID: {})".format(prefix, cad["name"], str(cad["id"]))
            self.cmb_cad_files.Items.Add(display)

        self.cmb_cad_files.IsEnabled = True
        if self.btn_scan is not None:
            self.btn_scan.IsEnabled = True
        self.cmb_cad_files.SelectedIndex = 0
        self._update_status("{} CAD file(s) found. Select one and click 'Scan Layers'.".format(
            len(self.cad_instances)))

    def _load_floor_types(self):
        self.floor_types = get_floor_types(self.doc)
        if self.cmb_floor_types is None:
            return
        self.cmb_floor_types.Items.Clear()
        for ft in self.floor_types:
            self.cmb_floor_types.Items.Add(ft["name"])
        if self.floor_types:
            self.cmb_floor_types.SelectedIndex = 0

    def _load_levels(self):
        self.levels = get_levels(self.doc)
        if self.cmb_levels is None:
            return
        self.cmb_levels.Items.Clear()
        for lvl in self.levels:
            elev_mm = str(int(round(lvl["elevation"] * FT_TO_MM)))
            display = "{} ({} mm)".format(lvl["name"], elev_mm)
            self.cmb_levels.Items.Add(display)
        if self.levels:
            self.cmb_levels.SelectedIndex = 0

    def _load_ds_categories(self):
        if self.cmb_ds_category is None:
            return
        self.cmb_ds_category.Items.Clear()
        for cat in self.ds_categories:
            self.cmb_ds_category.Items.Add(cat["name"])
        if self.ds_categories:
            self.cmb_ds_category.SelectedIndex = 0

    def _scan_layers(self, cad_instance):
        self.layer_data = []
        self._update_status("Scanning CAD layers...")

        try:
            layers_geom = get_cad_layer_geometry_floor(self.doc, cad_instance)
            for layer_name, data in sorted(layers_geom.items()):
                closed_loops = list(data.get("closed_loops", []))
                all_curves_count = data.get("all_curves_count", 0)
                individual_curves = data.get("curves", [])
                if individual_curves:
                    extra_loops = try_build_loops_from_curves(individual_curves)
                    closed_loops.extend(extra_loops)
                ld = LayerData(layer_name, closed_loops, all_curves_count)
                self.layer_data.append(ld)
        except Exception as ex:
            self._update_status("Error scanning: {}".format(str(ex)))

        self._render_layers()
        self._update_summary()

        total_closed = sum(ld.closed_count for ld in self.layer_data)
        self._update_status("Found {} layers with {} closed loops.".format(
            len(self.layer_data), total_closed))

    def _render_layers(self, filter_text=""):
        if self.pnl_layers is None:
            return
        self.pnl_layers.Children.Clear()
        self.layer_checkboxes = []
        filter_lower = filter_text.lower().strip()

        for ld in self.layer_data:
            if filter_lower and filter_lower not in ld.name.lower():
                continue
            try:
                with codecs.open(_XAML_FLOOR_ITEM, "r", "utf-8") as f:
                    item_content = f.read()
                border = load_xaml_string(item_content)
                chk = border.FindName("chk")
                txt_name = border.FindName("txt_name")
                txt_closed = border.FindName("txt_closed")
                txt_total = border.FindName("txt_total")

                if txt_name is not None:
                    txt_name.Text = ld.name
                if txt_closed is not None:
                    txt_closed.Text = str(ld.closed_count)
                if txt_total is not None:
                    txt_total.Text = str(ld.all_curves_count)
                if chk is not None:
                    chk.IsChecked = ld.is_selected

                try:
                    bc = BrushConverter()
                    if ld.closed_count > 0:
                        if txt_name is not None:
                            txt_name.FontWeight = FontWeights.SemiBold
                        if txt_closed is not None:
                            txt_closed.Foreground = bc.ConvertFromString("#10B981")
                    else:
                        if txt_closed is not None:
                            txt_closed.Foreground = bc.ConvertFromString("#CCC")
                except Exception:
                    pass

                def make_chk_handler(layer_data, checkbox):
                    def handler(sender, args):
                        layer_data.is_selected = safe_bool(checkbox.IsChecked)
                        self._update_summary()
                    return handler

                if chk is not None:
                    h = make_chk_handler(ld, chk)
                    chk.Checked += h
                    chk.Unchecked += h

                def make_border_handler(checkbox):
                    def handler(sender, args):
                        try:
                            src = args.OriginalSource
                            if src != checkbox:
                                checkbox.IsChecked = not safe_bool(checkbox.IsChecked)
                        except Exception:
                            pass
                    return handler

                if chk is not None:
                    border.MouseLeftButtonUp += make_border_handler(chk)

                def make_enter(brd):
                    def handler(s, e):
                        try:
                            bc2 = BrushConverter()
                            brd.Background = bc2.ConvertFromString("#FFF5E0")
                            brd.BorderBrush = bc2.ConvertFromString("#0F172A")
                        except Exception:
                            pass
                    return handler

                def make_leave(brd):
                    def handler(s, e):
                        try:
                            bc2 = BrushConverter()
                            brd.Background = bc2.ConvertFromString("Transparent")
                            brd.BorderBrush = bc2.ConvertFromString("Transparent")
                        except Exception:
                            pass
                    return handler

                border.MouseEnter += make_enter(border)
                border.MouseLeave += make_leave(border)

                self.pnl_layers.Children.Add(border)
                self.layer_checkboxes.append((chk, ld))
            except Exception:
                pass

        self._update_summary()

    # --- Event handlers ---

    def _on_mode_changed(self, sender, args):
        self._update_mode_ui()

    def _on_pick_cad(self, sender, args):
        self._pick_element_id = "PICK_REQUESTED"
        self.window.Close()

    def _on_scan_layers(self, sender, args):
        if self.cmb_cad_files is None:
            return
        idx = self.cmb_cad_files.SelectedIndex
        if idx < 0 or idx >= len(self.cad_instances):
            MessageBox.Show("Please select a CAD file first.", "CAD to Floor/Part",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        cad = self.cad_instances[idx]
        self._scan_layers(cad["element"])

    def _on_search_changed(self, sender, args):
        if self.txt_search is not None:
            self._render_layers(self.txt_search.Text)

    def _on_select_all(self, sender, args):
        for chk, ld in self.layer_checkboxes:
            if chk is not None:
                chk.IsChecked = True
            ld.is_selected = True
        self._update_summary()

    def _on_select_none(self, sender, args):
        for chk, ld in self.layer_checkboxes:
            if chk is not None:
                chk.IsChecked = False
            ld.is_selected = False
        self._update_summary()

    def _on_refresh(self, sender, args):
        self.elements_created_count = 0
        self._load_cad_files()
        self._load_floor_types()
        self._load_levels()
        self._load_ds_categories()
        self.layer_data = []
        self.layer_checkboxes = []
        if self.pnl_layers is not None:
            self.pnl_layers.Children.Clear()
        self._update_summary()
        self._update_status("Refreshed.")

    def _on_create_elements(self, sender, args):
        selected_layers = [ld for _, ld in self.layer_checkboxes if ld.is_selected]

        if not selected_layers:
            MessageBox.Show("No layers selected.", "CAD to Floor/Part",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        total_loops = sum(ld.closed_count for ld in selected_layers)

        if total_loops == 0:
            MessageBox.Show(
                "Selected layers have no closed loops.\nOnly closed polylines can be converted.",
                "CAD to Floor/Part", MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        if self.cmb_levels is None:
            return
        lvl_idx = self.cmb_levels.SelectedIndex
        if lvl_idx < 0 or lvl_idx >= len(self.levels):
            MessageBox.Show("Please select a Level.", "CAD to Floor/Part",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        level_id = self.levels[lvl_idx]["id"]

        try:
            offset_mm = float(self.txt_offset.Text) if self.txt_offset is not None else 0.0
        except (ValueError, TypeError):
            offset_mm = 0.0

        if self.current_mode == MODE_FLOOR:
            self._create_floors(selected_layers, total_loops, level_id, offset_mm)
        else:
            self._create_parts(selected_layers, total_loops, level_id, offset_mm)

    def _create_floors(self, selected_layers, total_loops, level_id, offset_mm):
        if self.cmb_floor_types is None:
            return
        ft_idx = self.cmb_floor_types.SelectedIndex
        if ft_idx < 0 or ft_idx >= len(self.floor_types):
            MessageBox.Show("Please select a Floor Type.", "CAD to Floor",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        floor_type_id = self.floor_types[ft_idx]["id"]
        is_structural = self.chk_structural is not None and safe_bool(self.chk_structural.IsChecked)

        msg = "Create FLOORS from {} layer(s) with {} loop(s)?\n\n".format(
            len(selected_layers), total_loops)
        if self.cmb_levels is not None:
            lvl_idx = self.cmb_levels.SelectedIndex
            msg += "Floor Type: {}\n".format(self.floor_types[ft_idx]["name"])
            if lvl_idx >= 0 and lvl_idx < len(self.levels):
                msg += "Level: {}\n".format(self.levels[lvl_idx]["name"])
        msg += "Offset: {} mm".format(offset_mm)

        result = MessageBox.Show(msg, "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result != MessageBoxResult.Yes:
            return

        self._update_status("Creating floors...")
        created = 0
        failed = 0

        self.begin_progress(total_loops, disable=[self.btn_create])
        t = Transaction(self.doc, "T3Lab: CAD to Floor")
        t.Start()
        try:
            _done = 0
            for ld in selected_layers:
                if self.is_cancelled:
                    break
                for loop in ld.closed_loops:
                    if self.is_cancelled:
                        break
                    self.step_progress(_done, "Creating floor {}/{}...".format(_done + 1, total_loops))
                    _done += 1
                    try:
                        floor = create_floor_from_loop(
                            self.doc, loop, floor_type_id, level_id, offset_mm, is_structural)
                        if floor:
                            created += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1

            cancelled = self.is_cancelled
            if created > 0:
                t.Commit()
                self.elements_created_count += created
                self._update_summary()
                _verb = "Cancelled — created" if cancelled else "Created"
                self._update_status("{} {} floor(s). {} failed.".format(_verb, created, failed))
                MessageBox.Show("{}: {} floor(s)\nFailed: {}".format(_verb, created, failed),
                                "Result", MessageBoxButton.OK, MessageBoxImage.Information)
            else:
                t.RollBack()
                self._update_status("No floors created.")
                MessageBox.Show("Failed to create any floors.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)
        except Exception as ex:
            try:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
            except Exception:
                pass
            self._update_status("Error: {}".format(str(ex)))
        finally:
            self.end_progress()

    def _create_parts(self, selected_layers, total_loops, level_id, offset_mm):
        if self.cmb_ds_category is None:
            return
        cat_idx = self.cmb_ds_category.SelectedIndex
        if cat_idx < 0 or cat_idx >= len(self.ds_categories):
            MessageBox.Show("Please select a Category.", "CAD to Part",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        category_bic = self.ds_categories[cat_idx]["bic"]

        try:
            thickness_mm = float(self.txt_thickness.Text) if self.txt_thickness is not None else 200.0
            if thickness_mm <= 0:
                MessageBox.Show("Thickness must be > 0.", "CAD to Part",
                                MessageBoxButton.OK, MessageBoxImage.Warning)
                return
        except (ValueError, TypeError):
            MessageBox.Show("Invalid thickness.", "CAD to Part",
                            MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        msg = "Create PARTS from {} layer(s) with {} loop(s)?\n\n".format(
            len(selected_layers), total_loops)
        msg += "Category: {}\n".format(self.ds_categories[cat_idx]["name"])
        msg += "Thickness: {} mm\n".format(thickness_mm)
        msg += "Offset: {} mm".format(offset_mm)

        result = MessageBox.Show(msg, "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result != MessageBoxResult.Yes:
            return

        self._update_status("Creating parts...")
        created = 0
        failed = 0

        self.begin_progress(total_loops, disable=[self.btn_create])
        t = Transaction(self.doc, "T3Lab: CAD to Part")
        t.Start()
        try:
            _done = 0
            for ld in selected_layers:
                if self.is_cancelled:
                    break
                for loop in ld.closed_loops:
                    if self.is_cancelled:
                        break
                    self.step_progress(_done, "Creating part {}/{}...".format(_done + 1, total_loops))
                    _done += 1
                    try:
                        ds = create_part_from_loop(
                            self.doc, loop, category_bic, level_id, thickness_mm, offset_mm)
                        if ds:
                            created += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1

            cancelled = self.is_cancelled
            if created > 0:
                t.Commit()
                self.elements_created_count += created
                self._update_summary()
                _verb = "Cancelled — created" if cancelled else "Created"
                self._update_status("{} {} part(s). {} failed.".format(_verb, created, failed))
                MessageBox.Show("{}: {} part(s)\nFailed: {}".format(_verb, created, failed),
                                "Result", MessageBoxButton.OK, MessageBoxImage.Information)
            else:
                t.RollBack()
                self._update_status("No parts created.")
                MessageBox.Show("Failed to create any parts.", "Error",
                                MessageBoxButton.OK, MessageBoxImage.Error)
        except Exception as ex:
            try:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
            except Exception:
                pass
            self._update_status("Error: {}".format(str(ex)))
        finally:
            self.end_progress()

    def _on_minimize(self, sender, args):
        self.window.WindowState = WindowState.Minimized

    def _on_maximize(self, sender, args):
        if self.window.WindowState == WindowState.Maximized:
            self.window.WindowState = WindowState.Normal
        else:
            self.window.WindowState = WindowState.Maximized

    def _on_close(self, sender, args):
        self._pick_element_id = None
        self.window.Close()

    def show(self):
        self.window.ShowDialog()


# ===========================================================================
# BEAM WINDOW
# ===========================================================================

class _CADtoBeamWindow(T3WPFWindow):
    """Full Beam creation window — loads CADtoBeam.xaml via forms.WPFWindow."""

    # ProgressPauseMixin — CADtoBeam.xaml status-bar progress panel
    PP_PANEL      = "beam_progress_panel"
    PP_BAR        = "beam_pb"
    PP_PAUSE      = "beam_btn_pause"
    PP_STOP       = "beam_btn_stop"
    PP_PAUSE_ICON = "beam_btn_pause_icon"
    PP_PAUSE_TEXT = "beam_btn_pause_label"
    PP_STATUS     = "lbl_status"
    PP_STOP_MSG   = u"Stopping… finishing current beam"

    def __init__(self, doc, uidoc):
        self.doc = doc
        self.uidoc = uidoc
        T3WPFWindow.__init__(self, _XAML_BEAM)
        self._populate_initial_data()

    def _populate_initial_data(self):
        # CAD links
        cad_list = get_cad_instances(self.doc)
        self.cad_map = {}
        for cad in cad_list:
            key = "{} (Id:{})".format(cad["name"], cad["id"])
            self.cad_map[key] = cad["element"]

        self.cb_cad_links.ItemsSource = to_items_source(sorted(self.cad_map.keys()))

        # Beam families
        beam_symbols = (FilteredElementCollector(self.doc)
                        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
                        .OfClass(DB.FamilySymbol)
                        .ToElements())
        self.family_names = sorted(list(set(s.Family.Name for s in beam_symbols)))
        self.cb_beam_types.ItemsSource = to_items_source(self.family_names)

        # Levels
        levels = FilteredElementCollector(self.doc).OfClass(Level).ToElements()
        self.level_map = {l.Name: l for l in levels}
        self.cb_levels.ItemsSource = to_items_source(sorted(self.level_map.keys()))

    def cad_link_changed(self, sender, e):
        selected_key = self.cb_cad_links.SelectedItem
        if not selected_key:
            return
        instance = self.cad_map[selected_key]
        layers = set()
        opt = DB.Options()
        geom = instance.get_Geometry(opt)
        for obj in geom:
            if isinstance(obj, DB.GeometryInstance):
                for sym_obj in obj.GetSymbolGeometry():
                    g_style = self.doc.GetElement(sym_obj.GraphicsStyleId)
                    if g_style:
                        try:
                            layers.add(g_style.GraphicsStyleCategory.Name)
                        except Exception:
                            pass
        self.cb_layers.ItemsSource = to_items_source(sorted(list(layers)))

    def generate_clicked(self, sender, e):
        cad_key = self.cb_cad_links.SelectedItem
        layer_name = self.cb_layers.SelectedItem
        family_name = self.cb_beam_types.SelectedItem
        level_name = self.cb_levels.SelectedItem

        if not all([cad_key, layer_name, family_name, level_name]):
            forms.alert("Please select all required fields.")
            return

        instance = self.cad_map[cad_key]
        level = self.level_map[level_name]

        try:
            default_z_offset = float(self.txt_offset.Text)
        except (ValueError, TypeError):
            default_z_offset = -50.0

        # Get GraphicsStyle ID for the layer
        beam_gs_id = None
        import_cat = instance.Category
        for sc in import_cat.SubCategories:
            if sc.Name == layer_name:
                beam_gs_id = sc.GetGraphicsStyle(DB.GraphicsStyleType.Projection).Id
                break

        if not beam_gs_id:
            forms.alert("Could not find GraphicsStyle for the selected layer.")
            return

        # Extract geometry
        raw_curves = []
        opt = DB.Options()
        geom = instance.get_Geometry(opt)

        def scan_geo(geo_iterable, transform=None):
            for obj in geo_iterable:
                if isinstance(obj, DB.GeometryInstance):
                    scan_geo(obj.GetInstanceGeometry(), obj.Transform)
                elif isinstance(obj, (DB.Line, DB.Curve)):
                    if obj.GraphicsStyleId == beam_gs_id:
                        if transform:
                            raw_curves.append(obj.CreateTransformed(transform))
                        else:
                            raw_curves.append(obj)

        scan_geo(geom)

        # Pair lines
        lines_h = []
        lines_v = []
        for c in raw_curves:
            sp = c.GetEndPoint(0)
            ep = c.GetEndPoint(1)
            dx = ep.X - sp.X
            dy = ep.Y - sp.Y
            length_2d = math.sqrt(dx * dx + dy * dy) * FT_TO_MM
            if length_2d < 10:
                continue
            angle = abs(math.degrees(math.atan2(dy, dx))) % 180
            entry = {
                "x1": sp.X * FT_TO_MM, "y1": sp.Y * FT_TO_MM,
                "x2": ep.X * FT_TO_MM, "y2": ep.Y * FT_TO_MM,
                "z": sp.Z * FT_TO_MM, "length": length_2d
            }
            if angle < 10 or angle > 170:
                lines_h.append(entry)
            elif 80 < angle < 100:
                lines_v.append(entry)

        all_pairs = _pair_lines_h(lines_h) + _pair_lines_v(lines_v)

        if not all_pairs:
            forms.alert("No parallel pairs found in the selected layer.")
            return

        # Create beams in a transaction
        self.begin_progress(len(all_pairs), disable=[self.btn_generate])
        t = Transaction(self.doc, "T3Lab: CAD to Beam")
        t.Start()
        try:
            created = 0
            for _beam_idx, p in enumerate(all_pairs):
                if self.is_cancelled:
                    break
                self.step_progress(_beam_idx, "Creating beam {}/{}...".format(_beam_idx + 1, len(all_pairs)))
                width_rounded = round(p["width"] / 50) * 50
                height = _get_height_for_width(width_rounded)

                fam_sym = get_or_create_beam_type(self.doc, family_name, width_rounded, height)
                if not fam_sym:
                    continue
                if not fam_sym.IsActive:
                    fam_sym.Activate()

                z_ft = level.Elevation + (default_z_offset * MM_TO_FT)

                if p["dir"] == "H":
                    sp_pt = DB.XYZ(p["main_s"] * MM_TO_FT, p["perp"] * MM_TO_FT, z_ft)
                    ep_pt = DB.XYZ(p["main_e"] * MM_TO_FT, p["perp"] * MM_TO_FT, z_ft)
                else:
                    sp_pt = DB.XYZ(p["perp"] * MM_TO_FT, p["main_s"] * MM_TO_FT, z_ft)
                    ep_pt = DB.XYZ(p["perp"] * MM_TO_FT, p["main_e"] * MM_TO_FT, z_ft)

                if sp_pt.DistanceTo(ep_pt) < 0.1:
                    continue

                beam_line = DB.Line.CreateBound(sp_pt, ep_pt)
                beam = self.doc.Create.NewFamilyInstance(
                    beam_line, fam_sym, level, DB.Structure.StructuralType.Beam)

                p_offset = beam.get_Parameter(DB.BuiltInParameter.Z_OFFSET_VALUE)
                if p_offset:
                    p_offset.Set(default_z_offset * MM_TO_FT)

                created += 1

            t.Commit()
        except Exception as ex:
            self.end_progress()
            try:
                t.RollBack()
            except Exception:
                pass
            forms.alert("Error creating beams: {}".format(str(ex)))
            return

        cancelled = self.is_cancelled
        self.end_progress()
        if cancelled:
            forms.alert("Cancelled — created {} beam(s).".format(created))
        else:
            forms.alert("Created {} beams successfully!".format(created))
        self.Close()

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Restore"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self.Close()


# ===========================================================================
# HUB WINDOW
# ===========================================================================

class CADToElementsWindow(T3WPFWindow):
    """
    Unified CAD to Elements window with sidebar navigation.

    Loads CADToElements.xaml which contains all panels (Wall, Floor, Beam,
    Duct, Pipe, Cable Tray, Conduit) inline. Sidebar buttons switch the
    active panel; all Revit logic runs in-place — no child windows are
    spawned.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self):
        T3WPFWindow.__init__(self, _XAML_HUB)
        self._doc = revit.doc
        self._uidoc = revit.uidoc

        # State
        self._active_type = "wall"
        self._cad_list = []       # list of dicts from get_cad_instances()
        self._levels = []         # list of dicts from get_levels()
        self._floor_types = []    # list of dicts from get_floor_types()
        self._ds_categories = []  # list of dicts from get_ds_categories()
        self._family_names = []   # beam family names (strings)
        self._beam_cad_layers = []  # beam layer names from current CAD
        self._beam_layer_checkboxes = {}   # name -> CheckBox

        # Wall layer state
        self._wall_layer_checkboxes = {}   # name -> CheckBox

        # Floor layer state
        self._floor_layer_data = []          # list of LayerData
        self._floor_layer_checkboxes = []    # list of (CheckBox, LayerData)

        # MEP state (duct / pipe / tray / conduit)
        self._duct_types = []
        self._mech_systems = []
        self._pipe_types = []
        self._piping_systems = []
        self._tray_types = []
        self._conduit_types = []
        self._mep_layer_checkboxes = {"duct": {}, "pipe": {}, "tray": {}, "conduit": {}}

        # Sidebar nav map: (key, button, panel) — drives _switch_type
        self._nav_items = [
            ("wall", self.btn_type_wall, self.pnl_wall),
            ("floor", self.btn_type_floor, self.pnl_floor),
            ("beam", self.btn_type_beam, self.pnl_beam),
            ("duct", self.btn_type_duct, self.pnl_duct),
            ("pipe", self.btn_type_pipe, self.pnl_pipe),
            ("tray", self.btn_type_tray, self.pnl_tray),
            ("conduit", self.btn_type_conduit, self.pnl_conduit),
        ]

        # Wire sidebar
        self.btn_type_wall.Click += self._on_nav_wall
        self.btn_type_floor.Click += self._on_nav_floor
        self.btn_type_beam.Click += self._on_nav_beam
        self.btn_type_duct.Click += self._on_nav_duct
        self.btn_type_pipe.Click += self._on_nav_pipe
        self.btn_type_tray.Click += self._on_nav_tray
        self.btn_type_conduit.Click += self._on_nav_conduit

        # Wire status-bar buttons
        self.btn_refresh.Click += self._on_refresh
        self.btn_run.Click += self._on_run
        self.btn_close_bar.Click += self._on_close

        # Wire wall helpers
        self.btn_wall_select_all.Click += self._on_wall_select_all
        self.btn_wall_clear.Click += self._on_wall_clear
        self.txt_layer_search.TextChanged += self._on_wall_search_changed
        self.rb_wall_mode.Checked += self._on_wall_mode_changed
        self.rb_wall_part_mode.Checked += self._on_wall_mode_changed

        # Wire floor helpers
        self.btn_floor_select_all.Click += self._on_floor_select_all
        self.btn_floor_clear.Click += self._on_floor_clear
        self.txt_floor_layer_search.TextChanged += self._on_floor_search_changed
        self.rb_floor_mode.Checked += self._on_floor_mode_changed
        self.rb_part_mode.Checked += self._on_floor_mode_changed

        # Wire beam helpers
        self.btn_beam_select_all.Click += self._on_beam_select_all
        self.btn_beam_clear.Click += self._on_beam_clear
        self.txt_beam_layer_search.TextChanged += self._on_beam_search_changed
        self.rb_beam_mode.Checked += self._on_beam_mode_changed
        self.rb_beam_part_mode.Checked += self._on_beam_mode_changed

        # Wire MEP helpers (duct / pipe / tray / conduit share generic handlers)
        for key in ("duct", "pipe", "tray", "conduit"):
            getattr(self, "btn_{}_select_all".format(key)).Click += \
                self._make_mep_setall_handler(key, True)
            getattr(self, "btn_{}_clear".format(key)).Click += \
                self._make_mep_setall_handler(key, False)
            getattr(self, "txt_{}_layer_search".format(key)).TextChanged += \
                self._make_mep_search_handler(key)
        self.rb_duct_single.Checked += self._on_duct_mode_changed
        self.rb_duct_double.Checked += self._on_duct_mode_changed
        self.rb_tray_single.Checked += self._on_tray_mode_changed
        self.rb_tray_double.Checked += self._on_tray_mode_changed

        # Wire window chrome
        self.btn_minimize.Click += self._on_minimize
        self.btn_maximize.Click += self._on_maximize
        self.btn_close.Click += self._on_close
        self.PreviewKeyDown += self._on_key_down

        # Wire AI layer matching helpers
        if hasattr(self, "btn_wall_ai"):
            self.btn_wall_ai.Click += self._on_wall_ai
        if hasattr(self, "btn_floor_ai"):
            self.btn_floor_ai.Click += self._on_floor_ai
        if hasattr(self, "btn_beam_ai"):
            self.btn_beam_ai.Click += self._on_beam_ai
        mep_labels = {
            "duct": "HVAC Duct Lines",
            "pipe": "Plumbing / Mechanical Pipe Lines",
            "tray": "Cable Tray Lines",
            "conduit": "Electrical Conduit Lines"
        }
        for key, lbl in mep_labels.items():
            btn = getattr(self, "btn_{}_ai".format(key), None)
            if btn is not None:
                btn.Click += self._make_mep_ai_handler(key, lbl)

        # Populate shared combos and type-specific combos
        self._populate_cad_files()
        self._populate_levels()
        self._populate_floor_types()
        self._populate_ds_categories()
        self._populate_beam_families()
        self._populate_duct_types()
        self._populate_duct_systems()
        self._populate_pipe_types()
        self._populate_pipe_systems()
        self._populate_tray_types()
        self._populate_conduit_types()

        # Show the default panel
        self._switch_type("wall")
        self._set_status("Ready. Select a CAD file and click Refresh to scan layers.")

        # AI Mode initialization
        self._init_ai_mode()

    # ------------------------------------------------------------------
    # Status helper
    # ------------------------------------------------------------------

    def _set_status(self, msg):
        try:
            self.txt_status.Text = str(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # AI Mode Helpers
    # ------------------------------------------------------------------

    def _init_ai_mode(self):
        """Initialize AI Mode status pill and tool capabilities if AI is active."""
        try:
            if hasattr(self, "ai_mode_badge"):
                if self.is_ai_mode_active():
                    self.ai_mode_badge.Visibility = Visibility.Visible
                    info = self.get_ai_status_info()
                    provider = info.get("provider", "Ready")
                    model = info.get("model", "")
                    label = "AI: {}".format(provider)
                    if model:
                        label = "AI: {} ({})".format(provider, model)
                    if hasattr(self, "txt_ai_status"):
                        self.txt_ai_status.Text = label
                else:
                    self.ai_mode_badge.Visibility = Visibility.Collapsed
        except Exception:
            pass

    def _on_wall_ai(self, sender, e):
        def _apply(matched_names):
            count = 0
            matched_lower = {m.lower().strip() for m in matched_names}
            for name, cb in self._wall_layer_checkboxes.items():
                if name.lower().strip() in matched_lower:
                    cb.IsChecked = System.Nullable[System.Boolean](True)
                    count += 1
            return count
        self._ai_match_category_layers("wall", "Walls / Partitions",
                                       lambda: list(self._wall_layer_checkboxes.keys()),
                                       _apply)

    def _on_floor_ai(self, sender, e):
        def _apply(matched_names):
            count = 0
            matched_lower = {m.lower().strip() for m in matched_names}
            for cb, ld in self._floor_layer_checkboxes:
                if ld.name.lower().strip() in matched_lower:
                    try:
                        cb.IsChecked = True
                    except Exception:
                        pass
                    ld.is_selected = True
                    count += 1
            return count
        self._ai_match_category_layers("floor", "Floors / Slabs",
                                       lambda: [ld.name for _, ld in self._floor_layer_checkboxes],
                                       _apply)

    def _on_beam_ai(self, sender, e):
        def _apply(matched_names):
            count = 0
            matched_lower = {m.lower().strip() for m in matched_names}
            for name, cb in self._beam_layer_checkboxes.items():
                if name.lower().strip() in matched_lower:
                    cb.IsChecked = System.Nullable[System.Boolean](True)
                    count += 1
            return count
        self._ai_match_category_layers("beam", "Structural Beams / Framing",
                                       lambda: list(self._beam_layer_checkboxes.keys()),
                                       _apply)

    def _make_mep_ai_handler(self, key, label):
        def handler(sender, e):
            mep_dict = self._mep_layer_checkboxes.get(key, {})
            def _apply(matched_names):
                count = 0
                matched_lower = {m.lower().strip() for m in matched_names}
                for name, cb in mep_dict.items():
                    if name.lower().strip() in matched_lower:
                        cb.IsChecked = System.Nullable[System.Boolean](True)
                        count += 1
                return count
            self._ai_match_category_layers(key, label, lambda: list(mep_dict.keys()), _apply)
        return handler

    def _ai_match_category_layers(self, category_key, category_label, get_names_fn, apply_fn):
        """Asynchronously call AI to match CAD layers for a specific element category."""
        if not self.is_ai_mode_active():
            MessageBox.Show(
                "AI Mode is currently disabled or no LLM provider is configured.\n"
                "Please enable AI Mode in LLMs Setting to use smart layer auto-matching.",
                "AI Mode Inactive",
                MessageBoxButton.OK,
                MessageBoxImage.Information
            )
            return

        layer_names = get_names_fn()
        if not layer_names:
            MessageBox.Show(
                "No CAD layers found. Please select a CAD file and click Refresh first.",
                "No Layers",
                MessageBoxButton.OK,
                MessageBoxImage.Warning
            )
            return

        btn = getattr(self, "btn_{}_ai".format(category_key), None)
        orig_content = btn.Content if btn else "✨ AI Select"

        def _restore_btn():
            if btn:
                btn.Content = orig_content
                btn.IsEnabled = True

        if btn:
            btn.Content = "⏳ Analyzing..."
            btn.IsEnabled = False

        self._set_status("AI analyzing {} CAD layers for {}...".format(len(layer_names), category_label))

        system_prompt = (
            "You are an expert BIM Manager and CAD/Revit specialist. "
            "You analyze AutoCAD/DWG layer names and identify which layers correspond to a specific building element category. "
            "Return JSON: {\"matched_layers\": [\"LAYER1\", \"LAYER2\"], \"confidence\": 0.0-1.0, \"reasoning\": \"<1-sentence explanation>\"}."
        )
        prompt = (
            "Element Category: {}\n"
            "CAD Layer Names in Drawing:\n{}\n\n"
            "Identify all layer names that represent {}. Return only valid matching names from the list."
        ).format(category_label, "\n".join("- " + l for l in layer_names[:120]), category_label)

        def _worker():
            return self.ai_bridge.ask_json(prompt, system_prompt=system_prompt)

        def _on_success(result):
            try:
                if not result or not isinstance(result, dict) or "matched_layers" not in result:
                    self._set_status("AI layer analysis completed: No confident matches found.")
                    return
                matched = result.get("matched_layers", [])
                confidence = result.get("confidence", 0.0)
                reasoning = result.get("reasoning", "")
                count = apply_fn(matched)
                try:
                    conf_pct = float(confidence) * 100
                except:
                    conf_pct = 90.0
                self._set_status("AI selected {} layer(s) for {} ({:.0f}% - {})".format(
                    count, category_label, conf_pct, reasoning
                ))
            finally:
                _restore_btn()

        def _on_error(err):
            _restore_btn()
            self._set_status("AI layer selection error: {}".format(err))

        self.run_ai_async(_worker, on_success=_on_success, on_error=_on_error)

    # ------------------------------------------------------------------
    # Initial population helpers
    # ------------------------------------------------------------------

    def _populate_cad_files(self):
        self._cad_list = get_cad_instances(self._doc)
        self.cmb_cad_files.Items.Clear()
        if not self._cad_list:
            item = ComboBoxItem()
            item.Content = "No CAD found in model"
            item.IsEnabled = False
            self.cmb_cad_files.Items.Add(item)
        else:
            for cad in self._cad_list:
                item = ComboBoxItem()
                item.Content = cad["name"]
                self.cmb_cad_files.Items.Add(item)
            self.cmb_cad_files.SelectedIndex = 0

    def _populate_levels(self):
        self._levels = get_levels(self._doc)
        self.cmb_levels.Items.Clear()
        for lv in self._levels:
            item = ComboBoxItem()
            item.Content = u"{} ({} mm)".format(
                lv["name"], ft_to_mm_str(lv["elevation"]))
            self.cmb_levels.Items.Add(item)
        if self._levels:
            # Try to pre-select the active view's level
            try:
                av = self._doc.ActiveView
                alid = av.GenLevel.Id if hasattr(av, "GenLevel") and av.GenLevel else None
                if alid:
                    for i, lv in enumerate(self._levels):
                        if _eid_int(lv["id"]) == _eid_int(alid):
                            self.cmb_levels.SelectedIndex = i
                            break
                    else:
                        self.cmb_levels.SelectedIndex = 0
                else:
                    self.cmb_levels.SelectedIndex = 0
            except Exception:
                self.cmb_levels.SelectedIndex = 0

    def _populate_floor_types(self):
        self._floor_types = get_floor_types(self._doc)
        self.cmb_floor_type.Items.Clear()
        for ft in self._floor_types:
            item = ComboBoxItem()
            item.Content = ft["name"]
            self.cmb_floor_type.Items.Add(item)
        if self._floor_types:
            self.cmb_floor_type.SelectedIndex = 0

    def _populate_ds_categories(self):
        self._ds_categories = get_ds_categories()
        for combo in (self.cmb_part_category, self.cmb_wall_part_category,
                      self.cmb_beam_part_category):
            combo.Items.Clear()
            for cat in self._ds_categories:
                item = ComboBoxItem()
                item.Content = cat["name"]
                combo.Items.Add(item)
            if self._ds_categories:
                combo.SelectedIndex = 0

    def _populate_beam_families(self):
        try:
            beam_symbols = (FilteredElementCollector(self._doc)
                            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
                            .OfClass(DB.FamilySymbol)
                            .ToElements())
            self._family_names = sorted(list(set(s.Family.Name for s in beam_symbols)))
        except Exception:
            self._family_names = []
        self.cb_beam_types.Items.Clear()
        for fname in self._family_names:
            item = ComboBoxItem()
            item.Content = fname
            self.cb_beam_types.Items.Add(item)
        if self._family_names:
            self.cb_beam_types.SelectedIndex = 0

    def _populate_mep_combo(self, combo, items, empty_msg):
        """Fill an MEP type/system combo; disabled hint row when empty."""
        combo.Items.Clear()
        if not items:
            item = ComboBoxItem()
            item.Content = empty_msg
            item.IsEnabled = False
            combo.Items.Add(item)
            return
        for entry in items:
            item = ComboBoxItem()
            item.Content = entry["name"]
            combo.Items.Add(item)
        combo.SelectedIndex = 0

    def _populate_duct_types(self):
        self._duct_types = get_duct_types(self._doc)
        self._populate_mep_combo(self.cmb_duct_type, self._duct_types,
                                 "No duct types loaded")

    def _populate_duct_systems(self):
        self._mech_systems = get_mech_system_types(self._doc)
        self._populate_mep_combo(self.cmb_duct_system, self._mech_systems,
                                 "No mechanical system types")

    def _populate_pipe_types(self):
        self._pipe_types = get_pipe_types(self._doc)
        self._populate_mep_combo(self.cmb_pipe_type, self._pipe_types,
                                 "No pipe types loaded")

    def _populate_pipe_systems(self):
        self._piping_systems = get_piping_system_types(self._doc)
        self._populate_mep_combo(self.cmb_pipe_system, self._piping_systems,
                                 "No piping system types")

    def _populate_tray_types(self):
        self._tray_types = get_cabletray_types(self._doc)
        self._populate_mep_combo(self.cmb_tray_type, self._tray_types,
                                 "No cable tray types loaded")

    def _populate_conduit_types(self):
        self._conduit_types = get_conduit_types(self._doc)
        self._populate_mep_combo(self.cmb_conduit_type, self._conduit_types,
                                 "No conduit types loaded")

    # ------------------------------------------------------------------
    # Sidebar navigation
    # ------------------------------------------------------------------

    def _switch_type(self, type_name):
        self._active_type = type_name
        # Update rail tile state and panel visibility from nav map
        for key, btn, panel in self._nav_items:
            try:
                btn.IsChecked = (key == type_name)
            except Exception:
                pass
            try:
                panel.Visibility = (Visibility.Visible if key == type_name
                                    else Visibility.Collapsed)
            except Exception:
                pass

    def _on_nav_wall(self, sender, e):
        self._switch_type("wall")

    def _on_nav_floor(self, sender, e):
        self._switch_type("floor")

    def _on_nav_beam(self, sender, e):
        self._switch_type("beam")

    def _on_nav_duct(self, sender, e):
        self._switch_type("duct")

    def _on_nav_pipe(self, sender, e):
        self._switch_type("pipe")

    def _on_nav_tray(self, sender, e):
        self._switch_type("tray")

    def _on_nav_conduit(self, sender, e):
        self._switch_type("conduit")

    # ------------------------------------------------------------------
    # Floor mode toggle (Floor / Part)
    # ------------------------------------------------------------------

    def _on_floor_mode_changed(self, sender, e):
        try:
            is_floor_mode = safe_bool(self.rb_floor_mode.IsChecked)
            if is_floor_mode:
                self.pnl_part_category.Visibility = Visibility.Collapsed
                self.pnl_part_thickness.Visibility = Visibility.Collapsed
            else:
                self.pnl_part_category.Visibility = Visibility.Visible
                self.pnl_part_thickness.Visibility = Visibility.Visible
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Wall mode toggle (Wall / Part)
    # ------------------------------------------------------------------

    def _on_wall_mode_changed(self, sender, e):
        try:
            is_wall_mode = safe_bool(self.rb_wall_mode.IsChecked)
            if is_wall_mode:
                self.pnl_wall_part_category.Visibility = Visibility.Collapsed
            else:
                self.pnl_wall_part_category.Visibility = Visibility.Visible
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Beam mode toggle (Beam / Part)
    # ------------------------------------------------------------------

    def _on_beam_mode_changed(self, sender, e):
        try:
            is_beam_mode = safe_bool(self.rb_beam_mode.IsChecked)
            if is_beam_mode:
                self.pnl_beam_part_category.Visibility = Visibility.Collapsed
            else:
                self.pnl_beam_part_category.Visibility = Visibility.Visible
        except Exception:
            pass

    # ------------------------------------------------------------------
    # MEP line mode toggles (single / double line)
    # ------------------------------------------------------------------

    def _on_duct_mode_changed(self, sender, e):
        # Width is auto-detected from the pair spacing in double-line mode
        try:
            self.txt_duct_width.IsEnabled = safe_bool(self.rb_duct_single.IsChecked)
        except Exception:
            pass

    def _on_tray_mode_changed(self, sender, e):
        try:
            self.txt_tray_width.IsEnabled = safe_bool(self.rb_tray_single.IsChecked)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Refresh — scan layers for the active type
    # ------------------------------------------------------------------

    def _on_refresh(self, sender, e):
        self._do_refresh()

    def _do_refresh(self):
        idx = self.cmb_cad_files.SelectedIndex
        if idx < 0 or idx >= len(self._cad_list):
            self._set_status("No CAD file selected.")
            return
        cad = self._cad_list[idx]

        if self._active_type == "wall":
            self._refresh_wall_layers(cad)
        elif self._active_type == "floor":
            self._refresh_floor_layers(cad)
        elif self._active_type == "beam":
            self._refresh_beam_layers(cad)
        elif self._active_type in ("duct", "pipe", "tray", "conduit"):
            self._refresh_mep_layers(cad, self._active_type)

    # ---- Wall layer refresh ----

    def _refresh_wall_layers(self, cad):
        self._set_status("Scanning wall layers...")
        try:
            layers = get_cad_layers_wall(self._doc, cad["element"])
        except Exception as ex:
            self._set_status("Error scanning layers: {}".format(str(ex)))
            return
        self._build_wall_layer_panel(layers)
        self._set_status("{} wall layers found.".format(len(layers)))

    def _build_wall_layer_panel(self, layers):
        self.pnl_wall_layers.Children.Clear()
        self._wall_layer_checkboxes = {}
        if not layers:
            tb = TextBlock()
            tb.Text = "No layers found"
            tb.FontSize = 11
            tb.Margin = Thickness(8, 8, 8, 8)
            self.pnl_wall_layers.Children.Add(tb)
            return
        for layer_name in layers:
            border = Border()
            border.Padding = Thickness(8, 4, 8, 4)
            border.Margin = Thickness(0, 0, 0, 1)
            border.Tag = layer_name
            sp = StackPanel()
            sp.Orientation = Orientation.Horizontal
            cb = CheckBox()
            cb.VerticalContentAlignment = VerticalAlignment.Center
            cb.Margin = Thickness(0, 0, 8, 0)
            cb.IsChecked = System.Nullable[System.Boolean](False)
            cb.Tag = layer_name
            tb = TextBlock()
            tb.Text = layer_name
            tb.FontSize = 11
            tb.VerticalAlignment = VerticalAlignment.Center
            sp.Children.Add(cb)
            sp.Children.Add(tb)
            border.Child = sp
            self.pnl_wall_layers.Children.Add(border)
            self._wall_layer_checkboxes[layer_name] = cb

    def _on_wall_select_all(self, sender, e):
        for cb in self._wall_layer_checkboxes.values():
            cb.IsChecked = System.Nullable[System.Boolean](True)

    def _on_wall_clear(self, sender, e):
        for cb in self._wall_layer_checkboxes.values():
            cb.IsChecked = System.Nullable[System.Boolean](False)

    def _on_wall_search_changed(self, sender, e):
        try:
            if self.txt_layer_search.Text:
                self.lbl_wall_search_placeholder.Visibility = Visibility.Collapsed
            else:
                self.lbl_wall_search_placeholder.Visibility = Visibility.Visible
        except Exception:
            pass
        try:
            txt = self.txt_layer_search.Text.strip().lower()
        except Exception:
            return
        for i in range(self.pnl_wall_layers.Children.Count):
            child = self.pnl_wall_layers.Children[i]
            if isinstance(child, Border) and child.Tag is not None:
                name = str(child.Tag).lower()
                child.Visibility = (Visibility.Visible
                                    if not txt or txt in name
                                    else Visibility.Collapsed)

    def _get_wall_selected_layers(self):
        selected = []
        for name, cb in self._wall_layer_checkboxes.items():
            try:
                if cb.IsChecked == True:
                    selected.append(name)
            except Exception:
                pass
        return selected

    # ---- Floor layer refresh ----

    def _refresh_floor_layers(self, cad):
        self._set_status("Scanning floor layers...")
        self._floor_layer_data = []
        self._floor_layer_checkboxes = []
        try:
            layers_geom = get_cad_layer_geometry_floor(self._doc, cad["element"])
            for layer_name, data in sorted(layers_geom.items()):
                closed_loops = list(data.get("closed_loops", []))
                all_curves_count = data.get("all_curves_count", 0)
                individual_curves = data.get("curves", [])
                if individual_curves:
                    extra_loops = try_build_loops_from_curves(individual_curves)
                    closed_loops.extend(extra_loops)
                ld = LayerData(layer_name, closed_loops, all_curves_count)
                self._floor_layer_data.append(ld)
        except Exception as ex:
            self._set_status("Error scanning floor layers: {}".format(str(ex)))
            return
        self._build_floor_layer_panel()
        total_closed = sum(ld.closed_count for ld in self._floor_layer_data)
        self._set_status("Found {} floor layers with {} closed loops.".format(
            len(self._floor_layer_data), total_closed))

    def _build_floor_layer_panel(self, filter_text=""):
        self.pnl_floor_layers.Children.Clear()
        self._floor_layer_checkboxes = []
        filter_lower = filter_text.lower().strip()
        bc = BrushConverter()

        for ld in self._floor_layer_data:
            if filter_lower and filter_lower not in ld.name.lower():
                continue
            border = Border()
            border.Padding = Thickness(8, 5, 8, 5)
            border.Margin = Thickness(0, 0, 0, 1)

            sp = StackPanel()
            sp.Orientation = Orientation.Horizontal

            cb = CheckBox()
            cb.VerticalContentAlignment = VerticalAlignment.Center
            cb.Margin = Thickness(0, 0, 8, 0)
            cb.IsChecked = System.Nullable[System.Boolean](ld.is_selected)

            tb_name = TextBlock()
            tb_name.Text = ld.name
            tb_name.FontSize = 11
            tb_name.VerticalAlignment = VerticalAlignment.Center
            tb_name.MinWidth = 140

            tb_closed = TextBlock()
            tb_closed.Text = str(ld.closed_count)
            tb_closed.FontSize = 11
            tb_closed.Margin = Thickness(8, 0, 0, 0)
            tb_closed.VerticalAlignment = VerticalAlignment.Center

            try:
                if ld.closed_count > 0:
                    tb_name.FontWeight = FontWeights.SemiBold
                    tb_closed.Foreground = bc.ConvertFromString("#10B981")
                else:
                    tb_closed.Foreground = bc.ConvertFromString("#CBD5E1")
            except Exception:
                pass

            sp.Children.Add(cb)
            sp.Children.Add(tb_name)
            sp.Children.Add(tb_closed)
            border.Child = sp
            self.pnl_floor_layers.Children.Add(border)
            self._floor_layer_checkboxes.append((cb, ld))

            # Checkbox change handler via closure
            def make_chk_handler(layer_data, checkbox):
                def handler(s, args):
                    layer_data.is_selected = safe_bool(checkbox.IsChecked)
                return handler

            h = make_chk_handler(ld, cb)
            cb.Checked += h
            cb.Unchecked += h

    def _on_floor_select_all(self, sender, e):
        for cb, ld in self._floor_layer_checkboxes:
            try:
                cb.IsChecked = True
            except Exception:
                pass
            ld.is_selected = True

    def _on_floor_clear(self, sender, e):
        for cb, ld in self._floor_layer_checkboxes:
            try:
                cb.IsChecked = False
            except Exception:
                pass
            ld.is_selected = False

    def _on_floor_search_changed(self, sender, e):
        try:
            if self.txt_floor_layer_search.Text:
                self.lbl_floor_search_placeholder.Visibility = Visibility.Collapsed
            else:
                self.lbl_floor_search_placeholder.Visibility = Visibility.Visible
        except Exception:
            pass
        try:
            txt = self.txt_floor_layer_search.Text
        except Exception:
            txt = ""
        self._build_floor_layer_panel(filter_text=txt)

    # ---- Beam layer refresh ----

    def _refresh_beam_layers(self, cad):
        self._set_status("Scanning beam layers...")
        self._beam_cad_layers = []
        try:
            instance = cad["element"]
            import_cat = instance.Category
            for sc in import_cat.SubCategories:
                try:
                    self._beam_cad_layers.append(sc.Name)
                except Exception:
                    pass
            self._beam_cad_layers = sorted(self._beam_cad_layers)
        except Exception as ex:
            self._set_status("Error scanning beam layers: {}".format(str(ex)))
            return
        self._build_beam_layer_panel(self._beam_cad_layers)
        self._set_status("{} beam layers found.".format(len(self._beam_cad_layers)))

    def _build_beam_layer_panel(self, layers):
        self.pnl_beam_layers.Children.Clear()
        self._beam_layer_checkboxes = {}
        if not layers:
            tb = TextBlock()
            tb.Text = "No layers found"
            tb.FontSize = 11
            tb.Margin = Thickness(8, 8, 8, 8)
            self.pnl_beam_layers.Children.Add(tb)
            return
        for layer_name in layers:
            border = Border()
            border.Padding = Thickness(8, 4, 8, 4)
            border.Margin = Thickness(0, 0, 0, 1)
            border.Tag = layer_name
            sp = StackPanel()
            sp.Orientation = Orientation.Horizontal
            cb = CheckBox()
            cb.VerticalContentAlignment = VerticalAlignment.Center
            cb.Margin = Thickness(0, 0, 8, 0)
            cb.IsChecked = System.Nullable[System.Boolean](False)
            cb.Tag = layer_name
            tb = TextBlock()
            tb.Text = layer_name
            tb.FontSize = 11
            tb.VerticalAlignment = VerticalAlignment.Center
            sp.Children.Add(cb)
            sp.Children.Add(tb)
            border.Child = sp
            self.pnl_beam_layers.Children.Add(border)
            self._beam_layer_checkboxes[layer_name] = cb

    def _on_beam_select_all(self, sender, e):
        for cb in self._beam_layer_checkboxes.values():
            cb.IsChecked = System.Nullable[System.Boolean](True)

    def _on_beam_clear(self, sender, e):
        for cb in self._beam_layer_checkboxes.values():
            cb.IsChecked = System.Nullable[System.Boolean](False)

    def _on_beam_search_changed(self, sender, e):
        try:
            if self.txt_beam_layer_search.Text:
                self.lbl_beam_search_placeholder.Visibility = Visibility.Collapsed
            else:
                self.lbl_beam_search_placeholder.Visibility = Visibility.Visible
        except Exception:
            pass
        try:
            txt = self.txt_beam_layer_search.Text.strip().lower()
        except Exception:
            return
        for i in range(self.pnl_beam_layers.Children.Count):
            child = self.pnl_beam_layers.Children[i]
            if isinstance(child, Border) and child.Tag is not None:
                name = str(child.Tag).lower()
                child.Visibility = (Visibility.Visible
                                    if not txt or txt in name
                                    else Visibility.Collapsed)

    def _get_beam_selected_layers(self):
        selected = []
        for name, cb in self._beam_layer_checkboxes.items():
            try:
                if cb.IsChecked == True:
                    selected.append(name)
            except Exception:
                pass
        return selected

    # ---- MEP layer refresh (shared by duct / pipe / tray / conduit) ----

    def _refresh_mep_layers(self, cad, key):
        self._set_status("Scanning layers...")
        try:
            layers = get_cad_layers_wall(self._doc, cad["element"])
        except Exception as ex:
            self._set_status("Error scanning layers: {}".format(str(ex)))
            return
        panel = getattr(self, "pnl_{}_layers".format(key))
        self._mep_layer_checkboxes[key] = self._build_mep_layer_panel(panel, layers)
        self._set_status("{} layers found.".format(len(layers)))

    def _build_mep_layer_panel(self, panel, layers):
        """Fill a layer StackPanel with checkbox rows; returns {name: CheckBox}."""
        panel.Children.Clear()
        checkboxes = {}
        if not layers:
            tb = TextBlock()
            tb.Text = "No layers found"
            tb.FontSize = 11
            tb.Margin = Thickness(8, 8, 8, 8)
            panel.Children.Add(tb)
            return checkboxes
        for layer_name in layers:
            border = Border()
            border.Padding = Thickness(8, 4, 8, 4)
            border.Margin = Thickness(0, 0, 0, 1)
            border.Tag = layer_name
            sp = StackPanel()
            sp.Orientation = Orientation.Horizontal
            cb = CheckBox()
            cb.VerticalContentAlignment = VerticalAlignment.Center
            cb.Margin = Thickness(0, 0, 8, 0)
            cb.IsChecked = System.Nullable[System.Boolean](False)
            cb.Tag = layer_name
            tb = TextBlock()
            tb.Text = layer_name
            tb.FontSize = 11
            tb.VerticalAlignment = VerticalAlignment.Center
            sp.Children.Add(cb)
            sp.Children.Add(tb)
            border.Child = sp
            panel.Children.Add(border)
            checkboxes[layer_name] = cb
        return checkboxes

    def _make_mep_setall_handler(self, key, value):
        def handler(sender, e):
            for cb in self._mep_layer_checkboxes.get(key, {}).values():
                cb.IsChecked = System.Nullable[System.Boolean](value)
        return handler

    def _make_mep_search_handler(self, key):
        def handler(sender, e):
            try:
                search_box = getattr(self, "txt_{}_layer_search".format(key))
                placeholder = getattr(self, "lbl_{}_search_placeholder".format(key))
                panel = getattr(self, "pnl_{}_layers".format(key))
            except Exception:
                return
            try:
                placeholder.Visibility = (Visibility.Collapsed if search_box.Text
                                          else Visibility.Visible)
            except Exception:
                pass
            try:
                txt = search_box.Text.strip().lower()
            except Exception:
                return
            for i in range(panel.Children.Count):
                child = panel.Children[i]
                if isinstance(child, Border) and child.Tag is not None:
                    name = str(child.Tag).lower()
                    child.Visibility = (Visibility.Visible
                                        if not txt or txt in name
                                        else Visibility.Collapsed)
        return handler

    def _get_mep_selected_layers(self, key):
        selected = []
        for name, cb in self._mep_layer_checkboxes.get(key, {}).items():
            try:
                if cb.IsChecked == True:
                    selected.append(name)
            except Exception:
                pass
        return selected

    # ------------------------------------------------------------------
    # Run button dispatcher
    # ------------------------------------------------------------------

    def _on_run(self, sender, e):
        self._do_run()

    def _do_run(self):
        if self._active_type == "wall":
            self._run_wall()
        elif self._active_type == "floor":
            self._run_floor()
        elif self._active_type == "beam":
            self._run_beam()
        elif self._active_type in ("duct", "pipe", "tray", "conduit"):
            self._run_mep(self._active_type)

    # ------------------------------------------------------------------
    # Wall creation
    # ------------------------------------------------------------------

    def _run_wall(self):
        idx = self.cmb_cad_files.SelectedIndex
        if idx < 0 or idx >= len(self._cad_list):
            forms.alert("Select a CAD file first.", title="CAD to Wall")
            return
        cad = self._cad_list[idx]

        selected_layers = self._get_wall_selected_layers()
        if not selected_layers:
            forms.alert("Select at least one layer.", title="CAD to Wall")
            return

        lv_idx = self.cmb_levels.SelectedIndex
        if lv_idx < 0 or lv_idx >= len(self._levels):
            forms.alert("Select a Level.", title="CAD to Wall")
            return
        lv = self._levels[lv_idx]

        try:
            height_ft = mm_to_ft(float(self.txt_wall_height.Text.strip()))
        except Exception:
            height_ft = mm_to_ft(3000.0)

        try:
            default_thk_mm = int(float(self.txt_wall_thickness.Text.strip()))
        except Exception:
            default_thk_mm = 200

        structural = False
        try:
            structural = safe_bool(self.chk_structural.IsChecked)
        except Exception:
            pass

        merge_col = False
        try:
            merge_col = safe_bool(self.chk_merge_collinear.IsChecked)
        except Exception:
            pass

        include_unpaired = False
        try:
            include_unpaired = safe_bool(self.chk_include_unpaired.IsChecked)
        except Exception:
            pass

        self._set_status("Extracting lines from CAD...")
        try:
            lines = extract_lines_from_cad(self._doc, cad["element"], selected_layers)
            raw_count = len(lines)
            if merge_col:
                lines = merge_collinear_lines(lines)
            merged_count = len(lines)
            centerlines, unpaired = find_parallel_pairs(lines)
        except Exception as ex:
            self._set_status("Error extracting lines: {}".format(str(ex)))
            forms.alert("Error extracting lines:\n{}".format(str(ex)), title="CAD to Wall")
            return

        total = len(centerlines) + (len(unpaired) if include_unpaired else 0)
        if total == 0:
            forms.alert("No wall lines found in selected layers.", title="CAD to Wall")
            self._set_status("No lines found.")
            return

        is_wall_mode = True
        try:
            is_wall_mode = safe_bool(self.rb_wall_mode.IsChecked)
        except Exception:
            pass

        if is_wall_mode:
            self._set_status("Creating {} walls...".format(total))
            try:
                created, failed, skipped, types_created = create_walls_auto(
                    self._doc, centerlines, unpaired, lv["id"],
                    height_ft, include_unpaired, default_thk_mm, structural)
            except Exception as ex:
                self._set_status("Error creating walls: {}".format(str(ex)))
                forms.alert("Error creating walls:\n{}".format(str(ex)), title="CAD to Wall")
                return

            msg = "Created: {} walls".format(created)
            if failed:
                msg += u" | Failed: {}".format(failed)
            if skipped:
                msg += u" | Skipped: {}".format(skipped)
            self._set_status(msg)
            forms.alert(
                u"Created: {}\nFailed: {}\nSkipped: {}\nTypes used: {}".format(
                    created, failed, skipped, len(types_created)),
                title="CAD to Wall")
        else:
            cat_idx = self.cmb_wall_part_category.SelectedIndex
            if cat_idx < 0 or cat_idx >= len(self._ds_categories):
                forms.alert("Select a Part Category.", title="CAD to Wall Part")
                return
            category_bic = self._ds_categories[cat_idx]["bic"]

            self._set_status("Creating {} wall parts...".format(total))
            try:
                created, failed, skipped, category_used = self._create_wall_parts(
                    centerlines, unpaired, lv["id"], height_ft,
                    include_unpaired, default_thk_mm, category_bic)
            except Exception as ex:
                self._set_status("Error creating wall parts: {}".format(str(ex)))
                forms.alert("Error creating wall parts:\n{}".format(str(ex)), title="CAD to Wall Part")
                return

            msg = "Created: {} part(s)".format(created)
            if failed:
                msg += u" | Failed: {}".format(failed)
            if skipped:
                msg += u" | Skipped: {}".format(skipped)
            self._set_status(msg)
            forms.alert(
                u"Created: {}\nFailed: {}\nSkipped: {}\nCategory: {}".format(
                    created, failed, skipped, category_used),
                title="CAD to Wall Part")

    def _create_wall_parts(self, centerlines, unpaired, level_id, height_ft,
                            use_unpaired, default_thickness_mm, category_bic):
        """Create DirectShape 'parts' for wall centerlines instead of real Wall
        elements — mirrors create_walls_auto's grouping/looping structure but
        extrudes a rectangular footprint upward by the wall height."""
        created = 0
        failed = 0
        skipped = 0

        level = self._doc.GetElement(level_id)
        level_elev = level.Elevation
        height_mm = height_ft * FT_TO_MM

        cat_name = category_bic.ToString()
        for cat in self._ds_categories:
            if cat["bic"] == category_bic:
                cat_name = cat["name"]
                break

        t = Transaction(self._doc, "T3Lab: CAD to Wall Part")
        t.Start()
        try:
            all_lines = list(centerlines)
            if use_unpaired and unpaired:
                all_lines = all_lines + list(unpaired)

            for cl in all_lines:
                try:
                    s = cl["start"]
                    e = cl["end"]
                    start = XYZ(s.X, s.Y, level_elev)
                    end = XYZ(e.X, e.Y, level_elev)
                    if start.DistanceTo(end) < TOLERANCE:
                        skipped += 1
                        continue
                    if abs(end.X - start.X) < TOLERANCE and abs(end.Y - start.Y) < TOLERANCE:
                        skipped += 1
                        continue

                    thickness_mm = _round_thickness_mm(cl.get("thickness", mm_to_ft(default_thickness_mm)))
                    if thickness_mm <= 0:
                        thickness_mm = default_thickness_mm
                    half_width_ft = mm_to_ft(thickness_mm) / 2.0

                    rect_loop = build_rect_loop_from_centerline(start, end, half_width_ft)
                    ds = create_part_from_loop(
                        self._doc, rect_loop, category_bic, level_id,
                        thickness_mm=height_mm, offset_mm=0)
                    if ds:
                        created += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

            status = t.Commit()
            if status != DB.TransactionStatus.Committed:
                print("Wall part transaction did not commit, status: {}".format(status))
                return 0, created + failed, skipped, cat_name
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            print("Wall part transaction error: {}".format(str(ex)))
            return 0, created + failed, skipped, cat_name

        return created, failed, skipped, cat_name

    # ------------------------------------------------------------------
    # Floor / Part creation
    # ------------------------------------------------------------------

    def _run_floor(self):
        selected_layers = [ld for _, ld in self._floor_layer_checkboxes if ld.is_selected]
        if not selected_layers:
            forms.alert("Select at least one floor layer.", title="CAD to Floor")
            return

        total_loops = sum(ld.closed_count for ld in selected_layers)
        if total_loops == 0:
            forms.alert(
                "Selected layers have no closed loops.\nOnly closed polylines can be converted.",
                title="CAD to Floor")
            return

        lv_idx = self.cmb_levels.SelectedIndex
        if lv_idx < 0 or lv_idx >= len(self._levels):
            forms.alert("Select a Level.", title="CAD to Floor")
            return
        level_id = self._levels[lv_idx]["id"]

        try:
            offset_mm = float(self.txt_floor_offset.Text)
        except Exception:
            offset_mm = 0.0

        is_floor_mode = True
        try:
            is_floor_mode = safe_bool(self.rb_floor_mode.IsChecked)
        except Exception:
            pass

        if is_floor_mode:
            self._create_floors(selected_layers, total_loops, level_id, offset_mm)
        else:
            self._create_parts(selected_layers, total_loops, level_id, offset_mm)

    def _create_floors(self, selected_layers, total_loops, level_id, offset_mm):
        ft_idx = self.cmb_floor_type.SelectedIndex
        if ft_idx < 0 or ft_idx >= len(self._floor_types):
            forms.alert("Select a Floor Type.", title="CAD to Floor")
            return
        floor_type_id = self._floor_types[ft_idx]["id"]

        is_structural = False
        try:
            is_structural = safe_bool(self.chk_floor_structural.IsChecked)
        except Exception:
            pass

        self._set_status("Creating floors...")
        created = 0
        failed = 0

        t = Transaction(self._doc, "T3Lab: CAD to Floor")
        t.Start()
        try:
            for ld in selected_layers:
                for loop in ld.closed_loops:
                    try:
                        floor = create_floor_from_loop(
                            self._doc, loop, floor_type_id, level_id,
                            offset_mm, is_structural)
                        if floor:
                            created += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
            if created > 0:
                t.Commit()
            else:
                t.RollBack()
        except Exception as ex:
            try:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
            except Exception:
                pass
            self._set_status("Error: {}".format(str(ex)))
            forms.alert("Error creating floors:\n{}".format(str(ex)), title="CAD to Floor")
            return

        msg = "Created: {} floor(s), Failed: {}".format(created, failed)
        self._set_status(msg)
        if created > 0:
            forms.alert(msg, title="CAD to Floor")
        else:
            forms.alert("No floors were created.", title="CAD to Floor")

    def _create_parts(self, selected_layers, total_loops, level_id, offset_mm):
        cat_idx = self.cmb_part_category.SelectedIndex
        if cat_idx < 0 or cat_idx >= len(self._ds_categories):
            forms.alert("Select a Part Category.", title="CAD to Part")
            return
        category_bic = self._ds_categories[cat_idx]["bic"]

        try:
            thickness_mm = float(self.txt_part_thickness.Text)
            if thickness_mm <= 0:
                forms.alert("Thickness must be > 0.", title="CAD to Part")
                return
        except Exception:
            forms.alert("Enter a valid thickness in mm.", title="CAD to Part")
            return

        self._set_status("Creating parts...")
        created = 0
        failed = 0

        t = Transaction(self._doc, "T3Lab: CAD to Part")
        t.Start()
        try:
            for ld in selected_layers:
                for loop in ld.closed_loops:
                    try:
                        ds = create_part_from_loop(
                            self._doc, loop, category_bic, level_id,
                            thickness_mm, offset_mm)
                        if ds:
                            created += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
            if created > 0:
                t.Commit()
            else:
                t.RollBack()
        except Exception as ex:
            try:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
            except Exception:
                pass
            self._set_status("Error: {}".format(str(ex)))
            forms.alert("Error creating parts:\n{}".format(str(ex)), title="CAD to Part")
            return

        msg = "Created: {} part(s), Failed: {}".format(created, failed)
        self._set_status(msg)
        if created > 0:
            forms.alert(msg, title="CAD to Part")
        else:
            forms.alert("No parts were created.", title="CAD to Part")

    # ------------------------------------------------------------------
    # Beam creation
    # ------------------------------------------------------------------

    def _run_beam(self):
        cad_idx = self.cmb_cad_files.SelectedIndex
        if cad_idx < 0 or cad_idx >= len(self._cad_list):
            forms.alert("Select a CAD file first.", title="CAD to Beam")
            return
        cad = self._cad_list[cad_idx]

        selected_layers = self._get_beam_selected_layers()
        if not selected_layers:
            forms.alert("Select at least one beam layer (click Refresh first).", title="CAD to Beam")
            return

        family_item = self.cb_beam_types.SelectedItem
        if family_item is None:
            forms.alert("Select a beam family.", title="CAD to Beam")
            return
        try:
            family_name = family_item.Content
        except Exception:
            family_name = str(family_item)

        lv_idx = self.cmb_levels.SelectedIndex
        if lv_idx < 0 or lv_idx >= len(self._levels):
            forms.alert("Select a Level.", title="CAD to Beam")
            return
        lv_dict = self._levels[lv_idx]
        level = self._doc.GetElement(lv_dict["id"])

        try:
            z_offset_mm = float(self.txt_beam_offset.Text)
        except Exception:
            z_offset_mm = -50.0

        instance = cad["element"]
        import_cat = instance.Category

        # Extract + pair lines across every selected layer, combining into one list
        all_pairs = []
        for layer_name in selected_layers:
            # Find GraphicsStyle ID for this layer
            beam_gs_id = None
            try:
                for sc in import_cat.SubCategories:
                    if sc.Name == layer_name:
                        beam_gs_id = sc.GetGraphicsStyle(DB.GraphicsStyleType.Projection).Id
                        break
            except Exception:
                pass

            if not beam_gs_id:
                continue

            # Extract geometry curves for the layer
            raw_curves = []
            try:
                opt = DB.Options()
                geom = instance.get_Geometry(opt)

                def scan_geo(geo_iterable, transform=None):
                    for obj in geo_iterable:
                        if isinstance(obj, DB.GeometryInstance):
                            scan_geo(obj.GetInstanceGeometry(), obj.Transform)
                        elif isinstance(obj, (DB.Line, DB.Curve)):
                            if obj.GraphicsStyleId == beam_gs_id:
                                if transform:
                                    raw_curves.append(obj.CreateTransformed(transform))
                                else:
                                    raw_curves.append(obj)

                scan_geo(geom)
            except Exception:
                continue

            # Classify lines as horizontal or vertical
            lines_h = []
            lines_v = []
            for c in raw_curves:
                try:
                    sp = c.GetEndPoint(0)
                    ep = c.GetEndPoint(1)
                    dx = ep.X - sp.X
                    dy = ep.Y - sp.Y
                    length_2d = math.sqrt(dx * dx + dy * dy) * FT_TO_MM
                    if length_2d < 10:
                        continue
                    angle = abs(math.degrees(math.atan2(dy, dx))) % 180
                    entry = {
                        "x1": sp.X * FT_TO_MM, "y1": sp.Y * FT_TO_MM,
                        "x2": ep.X * FT_TO_MM, "y2": ep.Y * FT_TO_MM,
                        "z": sp.Z * FT_TO_MM, "length": length_2d
                    }
                    if angle < 10 or angle > 170:
                        lines_h.append(entry)
                    elif 80 < angle < 100:
                        lines_v.append(entry)
                except Exception:
                    pass

            all_pairs.extend(_pair_lines_h(lines_h) + _pair_lines_v(lines_v))

        if not all_pairs:
            forms.alert("No parallel pairs found in the selected layer(s).",
                        title="CAD to Beam")
            return

        is_beam_mode = True
        try:
            is_beam_mode = safe_bool(self.rb_beam_mode.IsChecked)
        except Exception:
            pass

        if not is_beam_mode:
            cat_idx = self.cmb_beam_part_category.SelectedIndex
            if cat_idx < 0 or cat_idx >= len(self._ds_categories):
                forms.alert("Select a Part Category.", title="CAD to Beam Part")
                return
            category_bic = self._ds_categories[cat_idx]["bic"]
            cat_name = self._ds_categories[cat_idx]["name"]

            self._set_status("Creating {} beam parts...".format(len(all_pairs)))

            t = Transaction(self._doc, "T3Lab: CAD to Beam Part")
            t.Start()
            try:
                created = 0
                failed = 0
                for p in all_pairs:
                    try:
                        width_rounded = round(p["width"] / 50) * 50
                        height_mm = _get_height_for_width(width_rounded)

                        z_ft = level.Elevation + (z_offset_mm * MM_TO_FT)

                        if p["dir"] == "H":
                            sp_pt = DB.XYZ(p["main_s"] * MM_TO_FT, p["perp"] * MM_TO_FT, z_ft)
                            ep_pt = DB.XYZ(p["main_e"] * MM_TO_FT, p["perp"] * MM_TO_FT, z_ft)
                        else:
                            sp_pt = DB.XYZ(p["perp"] * MM_TO_FT, p["main_s"] * MM_TO_FT, z_ft)
                            ep_pt = DB.XYZ(p["perp"] * MM_TO_FT, p["main_e"] * MM_TO_FT, z_ft)

                        if sp_pt.DistanceTo(ep_pt) < 0.1:
                            continue

                        half_width_ft = mm_to_ft(width_rounded) / 2.0
                        rect_loop = build_rect_loop_from_centerline(sp_pt, ep_pt, half_width_ft)
                        ds = create_part_from_loop(
                            self._doc, rect_loop, category_bic, lv_dict["id"],
                            thickness_mm=height_mm, offset_mm=0)
                        if ds:
                            created += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1

                status = t.Commit()
                if status != DB.TransactionStatus.Committed:
                    self._set_status("Beam part transaction did not commit, status: {}".format(status))
                    forms.alert("Beam part transaction did not commit.", title="CAD to Beam Part")
                    return
            except Exception as ex:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                self._set_status("Error creating beam parts: {}".format(str(ex)))
                forms.alert("Error creating beam parts:\n{}".format(str(ex)), title="CAD to Beam Part")
                return

            msg = "Created: {} part(s) | Failed: {} | Category: {}".format(created, failed, cat_name)
            self._set_status(msg)
            forms.alert(msg, title="CAD to Beam Part")
            return

        self._set_status("Creating {} beams...".format(len(all_pairs)))

        t = Transaction(self._doc, "T3Lab: CAD to Beam")
        t.Start()
        try:
            created = 0
            for p in all_pairs:
                width_rounded = round(p["width"] / 50) * 50
                height = _get_height_for_width(width_rounded)
                fam_sym = get_or_create_beam_type(self._doc, family_name, width_rounded, height)
                if not fam_sym:
                    continue
                if not fam_sym.IsActive:
                    fam_sym.Activate()

                z_ft = level.Elevation + (z_offset_mm * MM_TO_FT)

                if p["dir"] == "H":
                    sp_pt = DB.XYZ(p["main_s"] * MM_TO_FT, p["perp"] * MM_TO_FT, z_ft)
                    ep_pt = DB.XYZ(p["main_e"] * MM_TO_FT, p["perp"] * MM_TO_FT, z_ft)
                else:
                    sp_pt = DB.XYZ(p["perp"] * MM_TO_FT, p["main_s"] * MM_TO_FT, z_ft)
                    ep_pt = DB.XYZ(p["perp"] * MM_TO_FT, p["main_e"] * MM_TO_FT, z_ft)

                if sp_pt.DistanceTo(ep_pt) < 0.1:
                    continue

                beam_line = DB.Line.CreateBound(sp_pt, ep_pt)
                beam = self._doc.Create.NewFamilyInstance(
                    beam_line, fam_sym, level, DB.Structure.StructuralType.Beam)

                p_offset = beam.get_Parameter(DB.BuiltInParameter.Z_OFFSET_VALUE)
                if p_offset:
                    p_offset.Set(z_offset_mm * MM_TO_FT)

                created += 1

            t.Commit()
        except Exception as ex:
            try:
                t.RollBack()
            except Exception:
                pass
            self._set_status("Error creating beams: {}".format(str(ex)))
            forms.alert("Error creating beams:\n{}".format(str(ex)), title="CAD to Beam")
            return

        msg = "Created {} beams.".format(created)
        self._set_status(msg)
        forms.alert(msg, title="CAD to Beam")

    # ------------------------------------------------------------------
    # MEP creation (duct / pipe / tray / conduit)
    # ------------------------------------------------------------------

    def _run_mep(self, key):
        cfg = MEP_UI_CONFIG[key]
        title = cfg["title"]

        idx = self.cmb_cad_files.SelectedIndex
        if idx < 0 or idx >= len(self._cad_list):
            forms.alert("Select a CAD file first.", title=title)
            return
        cad = self._cad_list[idx]

        selected_layers = self._get_mep_selected_layers(key)
        if not selected_layers:
            forms.alert("Select at least one layer (click Refresh to scan).",
                        title=title)
            return

        lv_idx = self.cmb_levels.SelectedIndex
        if lv_idx < 0 or lv_idx >= len(self._levels):
            forms.alert("Select a Level.", title=title)
            return
        lv = self._levels[lv_idx]

        # Type selection — combos hold a disabled hint row when nothing is
        # loaded, so validate against the stored lists, not the combo
        type_list = {"duct": self._duct_types, "pipe": self._pipe_types,
                     "tray": self._tray_types, "conduit": self._conduit_types}[key]
        if not type_list:
            forms.alert(
                "No {}s found in this project.\n"
                "Load an MEP template or family first.".format(cfg["type_label"]),
                title=title)
            return
        t_idx = getattr(self, cfg["type_combo"]).SelectedIndex
        if t_idx < 0 or t_idx >= len(type_list):
            forms.alert("Select a {}.".format(cfg["type_label"]), title=title)
            return
        type_id = type_list[t_idx]["id"]

        # System type (duct / pipe only)
        system_type_id = None
        if cfg["system"]:
            if key == "duct":
                sys_list = self._mech_systems
                sys_combo = self.cmb_duct_system
                sys_label = "mechanical"
            else:
                sys_list = self._piping_systems
                sys_combo = self.cmb_pipe_system
                sys_label = "piping"
            if not sys_list:
                forms.alert(
                    "No {} system types found in this project.\n"
                    "Load an MEP template first.".format(sys_label),
                    title=title)
                return
            s_idx = sys_combo.SelectedIndex
            if s_idx < 0 or s_idx >= len(sys_list):
                forms.alert("Select a system type.", title=title)
                return
            system_type_id = sys_list[s_idx]["id"]

        # Inputs (mm -> ft, with defaults)
        try:
            width_ft = mm_to_ft(float(getattr(self, cfg["width_box"]).Text.strip()))
        except Exception:
            width_ft = mm_to_ft(cfg["default_width"])

        height_ft = None
        if cfg["height_box"]:
            try:
                height_ft = mm_to_ft(float(getattr(self, cfg["height_box"]).Text.strip()))
            except Exception:
                height_ft = mm_to_ft(cfg["default_height"])

        try:
            offset_ft = mm_to_ft(float(getattr(self, cfg["offset_box"]).Text.strip()))
        except Exception:
            offset_ft = mm_to_ft(cfg["default_offset"])

        merge_col = safe_bool(getattr(self, cfg["merge_chk"]).IsChecked)
        auto_elbow = safe_bool(getattr(self, cfg["elbow_chk"]).IsChecked)
        double_mode = False
        if cfg["double"]:
            radio = self.rb_duct_double if key == "duct" else self.rb_tray_double
            double_mode = safe_bool(radio.IsChecked)

        # Extract geometry
        self._set_status("Extracting lines from CAD...")
        try:
            lines = extract_lines_from_cad(self._doc, cad["element"], selected_layers)
            if merge_col:
                lines = merge_collinear_lines(lines)
        except Exception as ex:
            self._set_status("Error extracting lines: {}".format(str(ex)))
            forms.alert("Error extracting lines:\n{}".format(str(ex)), title=title)
            return

        unpaired_count = 0
        if double_mode:
            centerlines, unpaired = find_parallel_pairs(
                lines, max_sep=mm_to_ft(MEP_MAX_PAIR_SEP_MM))
            unpaired_count = len(unpaired)
            if not centerlines:
                forms.alert(
                    "No parallel line pairs found in the selected layers.\n"
                    "Switch to single-line mode or check the CAD layers.",
                    title=title)
                self._set_status("No parallel pairs found.")
                return
            segments = [{"start": cl["start"], "end": cl["end"],
                         "width_ft": cl["thickness"]} for cl in centerlines]
        else:
            segments = [{"start": ln["start"], "end": ln["end"], "width_ft": None}
                        for ln in lines]

        if not segments:
            forms.alert("No lines found in the selected layers.", title=title)
            self._set_status("No lines found.")
            return

        self._set_status("Creating {} {}...".format(len(segments), cfg["noun"]))
        try:
            created, failed, skipped, size_failed, elb_ok, elb_skip = create_mep_runs(
                self._doc, key, segments, lv["id"], offset_ft,
                type_id, system_type_id, width_ft, height_ft, auto_elbow)
        except Exception as ex:
            self._set_status("Error creating {}: {}".format(cfg["noun"], str(ex)))
            forms.alert("Error creating {}:\n{}".format(cfg["noun"], str(ex)),
                        title=title)
            return

        msg = "Created: {} {}".format(created, cfg["noun"])
        if failed:
            msg += u" | Failed: {}".format(failed)
        if skipped:
            msg += u" | Skipped: {}".format(skipped)
        if auto_elbow:
            msg += u" | Elbows: {}".format(elb_ok)
        self._set_status(msg)

        detail = u"Created: {}\nFailed: {}\nSkipped: {}".format(created, failed, skipped)
        if size_failed:
            detail += u"\nSize not applied (catalog mismatch): {}".format(size_failed)
        if auto_elbow:
            detail += u"\nElbows placed: {} / skipped: {}".format(elb_ok, elb_skip)
        if double_mode and unpaired_count:
            detail += u"\nUnpaired lines ignored: {}".format(unpaired_count)
        forms.alert(detail, title=title)

    # ------------------------------------------------------------------
    # Window chrome
    # ------------------------------------------------------------------

    def _on_minimize(self, sender, e):
        self.WindowState = WindowState.Minimized

    def _on_maximize(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            try:
                self.btn_maximize.ToolTip = "Maximize"
            except Exception:
                pass
        else:
            self.WindowState = WindowState.Maximized
            try:
                self.btn_maximize.ToolTip = "Restore"
            except Exception:
                pass

    def _on_close(self, sender, e):
        self.Close()

    def _on_key_down(self, sender, e):
        import System.Windows.Input as WI
        if e.Key == WI.Key.Escape:
            self.Close()
        elif e.Key == WI.Key.F5:
            try:
                self._populate_cad_files()
                self._set_status("Refreshed CAD files.")
            except Exception:
                pass


# ===========================================================================
# PUBLIC ENTRY POINT
# ===========================================================================

def show_cad_to_elements(script_dir, revit_app):
    """
    Public entry point called by the pushbutton script.py.

    Parameters
    ----------
    script_dir : str
        Directory of the calling script.py (kept for signature compatibility).
    revit_app : object
        The __revit__ application object passed from the pushbutton.
        Not used here because revit.doc / revit.uidoc are accessed via pyRevit.
    """
    try:
        CADToElementsWindow().ShowDialog()
    except Exception as ex:
        import traceback
        print("CAD to Elements error:")
        print(traceback.format_exc())
        forms.alert("Error launching CAD to Elements:\n{}".format(str(ex)),
                    title="CAD to Elements")
