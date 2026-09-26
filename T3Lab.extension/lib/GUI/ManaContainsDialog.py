# -*- coding: utf-8 -*-
"""
Contains Manager Dialog
Unified Contains Manager including spatial elements checks and room data collector inside a Lumina UI.
"""

import os
import sys
import datetime
import io
import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

import System
from System.Collections.Generic import List
from System.Windows import (Window, Thickness, CornerRadius, WindowStartupLocation, ResizeMode, FontWeights, WindowState, GridLength, GridUnitType)
from System.Windows.Controls import (RowDefinition, ColumnDefinition, StackPanel, DockPanel, Border, TextBlock, TextBox, Button,
                                      ComboBox, ComboBoxItem, CheckBox, RadioButton, Orientation,
                                      Dock, ScrollViewer, ScrollBarVisibility, SelectionMode, ListBox, Grid as WPFGrid)
from System.Windows.Media import BrushConverter
import System.Windows

from pyrevit import revit, DB, forms
from GUI.WPF_Base import T3WPFWindow
from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory, BuiltInParameter
from Autodesk.Revit.DB import Transaction, StorageType, XYZ, SpatialElementBoundaryOptions, ElementId
from Autodesk.Revit.DB import SpatialElementBoundaryLocation, CurveLoop, AreaVolumeSettings
from Autodesk.Revit.UI import TaskDialog

# ── Paths ─────────────────────────────────────────────────────────────────────
GUI_DIR = os.path.dirname(os.path.abspath(__file__))
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'ManaContains.xaml')

# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
# point report the real problem.
try:
    doc = revit.doc
except Exception:
    doc = None
# `__revit__` members are unavailable when no UIDocument is active, and at
# module scope that kills the import outright. Resolve defensively; the entry
# point reports the real problem (see Snippets._host.resolve_doc()).
try:
    uidoc = __revit__.ActiveUIDocument
except Exception:
    uidoc = None

# ── Constants & Helpers ────────────────────────────────────────────────────────
_conv = BrushConverter()
def brush(c): return _conv.ConvertFromString(c)

PRIMARY = "#0F172A"
SECONDARY = "#F8FAFC"
WHITE = "#FFFFFF"
BORDER = "#E2E8F0"
TEXT_DARK = "#0F172A"
TEXT_GRAY = "#475569"
TEXT_MUTED = "#94A3B8"
SUCCESS = "#10B981"
WARNING_CLR = "#F59E0B"
ERROR_CLR = "#EF4444"
ACCENT = "#3B82F6"

ROOMS = "Rooms"
AREAS = "Areas"
SPACES = "Spaces"
ZONES = "Zones"
MASSES = "Masses"
SCOPEBOXES = "Scope Boxes"

AGG_COUNT = "Count"
AGG_SUM = "Sum"
AGG_AVERAGE = "Average"
AGG_MIN = "Min"
AGG_MAX = "Max"
AGG_FIRST = "First"
AGG_LAST = "Last"
AGG_LIST = "List (Comma)"
AGG_UNIQUE = "Unique List"

ALL_AGG = [AGG_COUNT, AGG_SUM, AGG_AVERAGE, AGG_MIN, AGG_MAX,
           AGG_FIRST, AGG_LAST, AGG_LIST, AGG_UNIQUE]

BBOX_CATEGORIES = [
    BuiltInCategory.OST_Walls, BuiltInCategory.OST_Floors, BuiltInCategory.OST_Ceilings,
    BuiltInCategory.OST_Columns, BuiltInCategory.OST_StructuralColumns,
    BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_CurtainWallPanels,
    BuiltInCategory.OST_Railings, BuiltInCategory.OST_Stairs,
]

# Revit compatibility
def _eid_int(eid):
    try:
        return eid.Value
    except:
        try:
            return eid.IntegerValue
        except:
            return -1

def _safe_call(fn, label=""):
    """Run fn(); on failure, continue so the window still opens with blank data."""
    try:
        fn()
    except Exception:
        pass

def get_room_parameters(elem):
    params = []
    if not elem:
        return params
    try:
        for p in elem.Parameters:
            if p.HasValue:
                name = p.Definition.Name
                if name and name not in params:
                    params.append(name)
    except:
        pass
    return sorted(params)

def get_param_value(elem, param_name):
    try:
        p = elem.LookupParameter(param_name)
        if p and p.HasValue:
            if p.StorageType == StorageType.String:
                return p.AsString() or ""
            elif p.StorageType == StorageType.Integer:
                return str(p.AsInteger())
            elif p.StorageType == StorageType.Double:
                return str(round(p.AsDouble(), 2))
            elif p.StorageType == StorageType.ElementId:
                return str(_eid_int(p.AsElementId()))
    except:
        pass
    return ""

def get_boundary_elements(spatial_elem):
    boundary_ids = set()
    try:
        opt = SpatialElementBoundaryOptions()
        opt.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish
        calculator = DB.SpatialElementGeometryCalculator(doc)
        results = calculator.CalculateSpatialElementGeometry(spatial_elem)
        for face in results.GetBoundaryFaceInfo():
            boundary_ids.add(_eid_int(face.SpatialBoundaryElement.HostElementId))
    except:
        try:
            opt = SpatialElementBoundaryOptions()
            opt.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish
            for loops in spatial_elem.GetBoundarySegments(opt):
                for seg in loops:
                    try:
                        eid = seg.ElementId
                        if eid and _eid_int(eid) != -1:
                            boundary_ids.add(_eid_int(eid))
                    except:
                        pass
        except:
            pass
    return boundary_ids

def get_area_polygon(area):
    polygons = []
    try:
        opt = SpatialElementBoundaryOptions()
        for loops in area.GetBoundarySegments(opt):
            polygon = []
            for seg in loops:
                try:
                    c = seg.GetCurve()
                    pt = c.GetEndPoint(0)
                    polygon.append((pt.X, pt.Y))
                except:
                    pass
            if polygon:
                polygons.append(polygon)
    except:
        pass
    return polygons

def get_elem_bbox(elem):
    try:
        return elem.get_BoundingBox(None)
    except:
        return None

def get_spatial_bbox(spatial_elem):
    try:
        return spatial_elem.get_BoundingBox(None)
    except:
        return None

def bbox_intersects_2d(bb1, bb2, tolerance=2.0):
    if not bb1 or not bb2: return False
    min1, max1 = bb1.Min, bb1.Max
    min2, max2 = bb2.Min, bb2.Max
    if min1.X - tolerance > max2.X or max1.X + tolerance < min2.X: return False
    if min1.Y - tolerance > max2.Y or max1.Y + tolerance < min2.Y: return False
    return True

def bbox_intersects_3d(bb1, bb2, tolerance=2.0):
    if not bb1 or not bb2: return False
    min1, max1 = bb1.Min, bb1.Max
    min2, max2 = bb2.Min, bb2.Max
    if min1.X - tolerance > max2.X or max1.X + tolerance < min2.X: return False
    if min1.Y - tolerance > max2.Y or max1.Y + tolerance < min2.Y: return False
    if min1.Z - tolerance > max2.Z or max1.Z + tolerance < min2.Z: return False
    return True

def get_check_points_2d(elem):
    pts = []
    try:
        loc = elem.Location
        if loc:
            try:
                pt = loc.Point
                pts.append((pt.X, pt.Y))
                return pts
            except:
                try:
                    crv = loc.Curve
                    pt1 = crv.GetEndPoint(0)
                    pt2 = crv.GetEndPoint(1)
                    pts.append((pt1.X, pt1.Y))
                    pts.append((pt2.X, pt2.Y))
                    pts.append(((pt1.X + pt2.X) * 0.5, (pt1.Y + pt2.Y) * 0.5))
                    return pts
                except:
                    pass
    except:
        pass
    try:
        bb = elem.get_BoundingBox(None)
        if bb:
            mn, mx = bb.Min, bb.Max
            pts.append((mn.X, mn.Y))
            pts.append((mx.X, mx.Y))
            pts.append(((mn.X + mx.X) * 0.5, (mn.Y + mx.Y) * 0.5))
    except:
        pass
    return pts

def get_check_points_3d(elem):
    pts = []
    try:
        loc = elem.Location
        if loc:
            try:
                pts.append(loc.Point)
                return pts
            except:
                try:
                    crv = loc.Curve
                    pt1 = crv.GetEndPoint(0)
                    pt2 = crv.GetEndPoint(1)
                    pts.append(pt1)
                    pts.append(pt2)
                    pts.append(XYZ((pt1.X + pt2.X) * 0.5, (pt1.Y + pt2.Y) * 0.5, (pt1.Z + pt2.Z) * 0.5))
                    return pts
                except:
                    pass
    except:
        pass
    try:
        bb = elem.get_BoundingBox(None)
        if bb:
            mn, mx = bb.Min, bb.Max
            pts.append(mn)
            pts.append(mx)
            pts.append(XYZ((mn.X + mx.X) * 0.5, (mn.Y + mx.Y) * 0.5, (mn.Z + mx.Z) * 0.5))
    except:
        pass
    return pts

def safe_get_location_point(elem):
    try:
        loc = elem.Location
        if loc:
            try: return loc.Point
            except:
                try: return loc.Curve.Evaluate(0.5, True)
                except: pass
        bb = elem.get_BoundingBox(None)
        if bb:
            return XYZ((bb.Min.X + bb.Max.X)*0.5, (bb.Min.Y + bb.Max.Y)*0.5, (bb.Min.Z + bb.Max.Z)*0.5)
    except: pass
    return None

def in_room(r, pt):
    try: return r.IsPointInRoom(pt)
    except: return False

def in_space(s, pt):
    try: return s.IsPointInSpace(pt)
    except: return False

def pt_in_poly(pt_2d, poly):
    x, y = pt_2d
    inside = False
    try:
        n = len(poly)
        p1x, p1y = poly[0]
        for i in range(n + 1):
            p2x, p2y = poly[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
    except:
        pass
    return inside

def in_area_2d(pt_2d, polygons):
    for poly in polygons:
        if pt_in_poly(pt_2d, poly):
            return True
    return False

def check_element_in_room(elem, room_elem, use_bbox=False, boundary_ids=None):
    try:
        if boundary_ids and _eid_int(elem.Id) in boundary_ids:
            return True
        if use_bbox:
            bb1 = get_elem_bbox(elem)
            bb2 = get_spatial_bbox(room_elem)
            if not bbox_intersects_3d(bb1, bb2):
                return False
        pts = get_check_points_3d(elem)
        for pt in pts:
            if in_room(room_elem, pt):
                return True
    except: pass
    return False

def check_element_in_area(elem, area_elem, polygons, use_bbox=False):
    try:
        if use_bbox:
            bb1 = get_elem_bbox(elem)
            bb2 = get_spatial_bbox(area_elem)
            if not bbox_intersects_2d(bb1, bb2):
                return False
        pts = get_check_points_2d(elem)
        for pt in pts:
            if in_area_2d(pt, polygons):
                return True
    except: pass
    return False

def check_element_in_space(elem, space_elem, use_bbox=False):
    try:
        if use_bbox:
            bb1 = get_elem_bbox(elem)
            bb2 = get_spatial_bbox(space_elem)
            if not bbox_intersects_3d(bb1, bb2):
                return False
        pts = get_check_points_3d(elem)
        for pt in pts:
            if in_space(space_elem, pt):
                return True
    except: pass
    return False

def check_element_in_spatial(elem, sp_item, use_bbox=False):
    stype = sp_item.spatial_type
    if stype == ROOMS:
        return check_element_in_room(elem, sp_item.element, use_bbox, sp_item.boundary_ids)
    elif stype == AREAS:
        return check_element_in_area(elem, sp_item.element, sp_item.polygons, use_bbox)
    elif stype == SPACES:
        return check_element_in_space(elem, sp_item.element, use_bbox)
    elif stype == ZONES:
        return check_element_in_zone(elem, sp_item, use_bbox)
    elif stype == MASSES:
        return check_element_in_mass(elem, sp_item, use_bbox)
    elif stype == SCOPEBOXES:
        return check_element_in_scopebox(elem, sp_item, use_bbox)
    return False

def get_rooms(vo=False):
    c = FilteredElementCollector(doc)
    if vo: c = FilteredElementCollector(doc, doc.ActiveView.Id)
    return [e for e in c.OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType() if e.Area > 0]

def get_areas(vo=False):
    c = FilteredElementCollector(doc)
    if vo: c = FilteredElementCollector(doc, doc.ActiveView.Id)
    return [e for e in c.OfCategory(BuiltInCategory.OST_Areas).WhereElementIsNotElementType() if e.Area > 0]

def get_spaces(vo=False):
    c = FilteredElementCollector(doc)
    if vo: c = FilteredElementCollector(doc, doc.ActiveView.Id)
    return [e for e in c.OfCategory(BuiltInCategory.OST_MEPSpaces).WhereElementIsNotElementType() if e.Area > 0]

def get_zones(vo=False):
    c = FilteredElementCollector(doc)
    if vo: c = FilteredElementCollector(doc, doc.ActiveView.Id)
    return [e for e in c.OfCategory(BuiltInCategory.OST_HVAC_Zones).WhereElementIsNotElementType()]

def get_masses(vo=False):
    c = FilteredElementCollector(doc)
    if vo: c = FilteredElementCollector(doc, doc.ActiveView.Id)
    return [e for e in c.OfCategory(BuiltInCategory.OST_Mass).WhereElementIsNotElementType()]

def get_scopeboxes(vo=False):
    c = FilteredElementCollector(doc)
    if vo: c = FilteredElementCollector(doc, doc.ActiveView.Id)
    return [e for e in c.OfCategory(BuiltInCategory.OST_VolumeOfInterest).WhereElementIsNotElementType()]

def get_mass_solid(mass):
    try:
        opt = DB.Options()
        geom = mass.get_Geometry(opt)
        for obj in geom:
            if isinstance(obj, DB.Solid) and obj.Volume > 0:
                return obj
            elif isinstance(obj, DB.GeometryInstance):
                inst_geom = obj.GetInstanceGeometry()
                for inst_obj in inst_geom:
                    if isinstance(inst_obj, DB.Solid) and inst_obj.Volume > 0:
                        return inst_obj
    except:
        pass
    return None

def get_scopebox_bbox(scopebox):
    try:
        # Scope Box lacks get_BoundingBox, use its geometry lines
        opt = DB.Options()
        geom = scopebox.get_Geometry(opt)
        pts = []
        for obj in geom:
            if isinstance(obj, DB.Line):
                pts.append(obj.GetEndPoint(0))
                pts.append(obj.GetEndPoint(1))
        if pts:
            min_x = min([p.X for p in pts])
            min_y = min([p.Y for p in pts])
            min_z = min([p.Z for p in pts])
            max_x = max([p.X for p in pts])
            max_y = max([p.Y for p in pts])
            max_z = max([p.Z for p in pts])
            bbox = DB.BoundingBoxXYZ()
            bbox.Min = XYZ(min_x, min_y, min_z)
            bbox.Max = XYZ(max_x, max_y, max_z)
            return bbox
    except:
        pass
    return None

def point_in_bbox(pt, bbox, tolerance=0.1):
    if not bbox: return False
    mn, mx = bbox.Min, bbox.Max
    return (mn.X - tolerance <= pt.X <= mx.X + tolerance and
            mn.Y - tolerance <= pt.Y <= mx.Y + tolerance and
            mn.Z - tolerance <= pt.Z <= mx.Z + tolerance)

def point_in_solid(pt, solid):
    if not solid: return False
    try:
        # Check solid containment
        val = solid.IntersectWithCurve(
            DB.Line.CreateBound(pt, pt + XYZ(0,0,1000)),
            DB.SolidCurveIntersectionOptions()
        )
        # Odd number of intersections means point is inside
        return (val.SegmentCount % 2 != 0)
    except:
        try:
            # Fallback to Bounding Box
            return point_in_bbox(pt, solid.GetBoundingBox())
        except:
            return False

def check_element_in_zone(elem, sp_item, use_bbox=False):
    # If the zone has spaces, check if element is in any of them
    if sp_item.spaces:
        for space in sp_item.spaces:
            if check_element_in_space(elem, space, use_bbox):
                return True
    return False

def check_element_in_mass(elem, sp_item, use_bbox=False):
    if not sp_item.solid: return False
    if use_bbox:
        bb1 = get_elem_bbox(elem)
        bb2 = sp_item.solid.GetBoundingBox()
        if not bbox_intersects_3d(bb1, bb2):
            return False
    pts = get_check_points_3d(elem)
    for pt in pts:
        if point_in_solid(pt, sp_item.solid):
            return True
    return False

def check_element_in_scopebox(elem, sp_item, use_bbox=False):
    if not sp_item.bbox: return False
    if use_bbox:
        bb1 = get_elem_bbox(elem)
        if not bbox_intersects_3d(bb1, sp_item.bbox):
            return False
    pts = get_check_points_3d(elem)
    for pt in pts:
        if point_in_bbox(pt, sp_item.bbox):
            return True
    return False

def get_cats():
    # OST categories to query
    cats = [
        ("Walls", BuiltInCategory.OST_Walls),
        ("Floors", BuiltInCategory.OST_Floors),
        ("Ceilings", BuiltInCategory.OST_Ceilings),
        ("Doors", BuiltInCategory.OST_Doors),
        ("Windows", BuiltInCategory.OST_Windows),
        ("Furniture", BuiltInCategory.OST_Furniture),
        ("Furniture Systems", BuiltInCategory.OST_FurnitureSystems),
        ("Generic Models", BuiltInCategory.OST_GenericModel),
        ("Lighting Fixtures", BuiltInCategory.OST_LightingFixtures),
        ("Mechanical Equipment", BuiltInCategory.OST_MechanicalEquipment),
        ("Plumbing Fixtures", BuiltInCategory.OST_PlumbingFixtures),
        ("Electrical Equipment", BuiltInCategory.OST_ElectricalEquipment),
        ("Electrical Fixtures", BuiltInCategory.OST_ElectricalFixtures),
        ("Specialty Equipment", BuiltInCategory.OST_SpecialityEquipment),
        ("Columns", BuiltInCategory.OST_Columns),
        ("Structural Columns", BuiltInCategory.OST_StructuralColumns),
        ("Structural Framing", BuiltInCategory.OST_StructuralFraming),
        ("Curtain Wall Panels", BuiltInCategory.OST_CurtainWallPanels),
        ("Railings", BuiltInCategory.OST_Railings),
        ("Stairs", BuiltInCategory.OST_Stairs),
        ("Casework", BuiltInCategory.OST_Casework),
        ("Air Terminals", BuiltInCategory.OST_DuctTerminal),
        ("Communication Devices", BuiltInCategory.OST_CommunicationDevices),
        ("Data Devices", BuiltInCategory.OST_DataDevices),
        ("Fire Alarm Devices", BuiltInCategory.OST_FireAlarmDevices),
        ("Lighting Devices", BuiltInCategory.OST_LightingDevices),
        ("Nurse Call Devices", BuiltInCategory.OST_NurseCallDevices),
        ("Security Devices", BuiltInCategory.OST_SecurityDevices),
        ("Telephone Devices", BuiltInCategory.OST_TelephoneDevices),
        ("Pipe Accessories", BuiltInCategory.OST_PipeAccessory),
        ("Pipe Fittings", BuiltInCategory.OST_PipeFitting),
        ("Duct Accessories", BuiltInCategory.OST_DuctAccessory),
        ("Duct Fittings", BuiltInCategory.OST_DuctFitting),
        ("Sprinklers", BuiltInCategory.OST_Sprinklers),
    ]
    return sorted(cats, key=lambda x: x[0])

def get_elems(bic, vo=False):
    c = FilteredElementCollector(doc)
    if vo: c = FilteredElementCollector(doc, doc.ActiveView.Id)
    return c.OfCategory(bic).WhereElementIsNotElementType().ToElements()

def get_str_params(elem):
    params = []
    if not elem: return params
    try:
        for p in elem.Parameters:
            if not p.IsReadOnly and p.StorageType == StorageType.String:
                name = p.Definition.Name
                if name and name not in params:
                    params.append(name)
    except: pass
    return sorted(params)

def get_family_type_name(elem):
    fname = ""
    tname = ""
    try:
        # Try FamilyInstance Family name
        fname = elem.Symbol.Family.Name
    except:
        try:
            # Fallback to category / element type name
            type_id = elem.GetTypeId()
            if type_id and _eid_int(type_id) != -1:
                et = doc.GetElement(type_id)
                if et:
                    fname = et.FamilyName
        except:
            pass
    try:
        tname = elem.Name
    except:
        try:
            type_id = elem.GetTypeId()
            if type_id and _eid_int(type_id) != -1:
                et = doc.GetElement(type_id)
                if et:
                    tname = et.Name
        except:
            pass
    
    if not fname:
        try:
            # Category Name
            fname = elem.Category.Name
        except:
            fname = "Unknown Family"
            
    if not tname:
        tname = "Unknown Type"
        
    return fname, tname


# ── Tab 2 Helpers ─────────────────────────────────────────────────────────────
def get_categories():
    cats = []
    # OST categories to query for Tab 2
    bics = [
        BuiltInCategory.OST_Walls, BuiltInCategory.OST_Floors, BuiltInCategory.OST_Ceilings,
        BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows, BuiltInCategory.OST_Furniture,
        BuiltInCategory.OST_FurnitureSystems, BuiltInCategory.OST_GenericModel, BuiltInCategory.OST_LightingFixtures,
        BuiltInCategory.OST_MechanicalEquipment, BuiltInCategory.OST_PlumbingFixtures, BuiltInCategory.OST_ElectricalEquipment,
        BuiltInCategory.OST_ElectricalFixtures, BuiltInCategory.OST_SpecialityEquipment, BuiltInCategory.OST_Columns,
        BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_CurtainWallPanels,
        BuiltInCategory.OST_Railings, BuiltInCategory.OST_Stairs, BuiltInCategory.OST_Casework,
        BuiltInCategory.OST_DuctTerminal, BuiltInCategory.OST_CommunicationDevices, BuiltInCategory.OST_DataDevices,
        BuiltInCategory.OST_FireAlarmDevices, BuiltInCategory.OST_LightingDevices, BuiltInCategory.OST_NurseCallDevices,
        BuiltInCategory.OST_SecurityDevices, BuiltInCategory.OST_TelephoneDevices, BuiltInCategory.OST_PipeAccessory,
        BuiltInCategory.OST_PipeFitting, BuiltInCategory.OST_DuctAccessory, BuiltInCategory.OST_DuctFitting,
        BuiltInCategory.OST_Sprinklers,
    ]
    for bic in bics:
        try:
            name = DB.Category.GetCategory(doc, bic).Name
            cats.append((name, int(bic)))
        except:
            pass
    return sorted(cats, key=lambda x: x[0])

def get_element_params(elements):
    params = set()
    for elem in elements:
        try:
            for p in elem.Parameters:
                if p.HasValue:
                    params.add(p.Definition.Name)
        except:
            pass
    return sorted(list(params))

def get_param_value_str(elem, param_name):
    try:
        p = elem.LookupParameter(param_name)
        if p and p.HasValue:
            if p.StorageType == StorageType.String:
                return p.AsString() or ""
            elif p.StorageType == StorageType.Integer:
                return str(p.AsInteger())
            elif p.StorageType == StorageType.Double:
                # Format units if possible
                try:
                    return p.AsValueString() or str(round(p.AsDouble(), 2))
                except:
                    return str(round(p.AsDouble(), 2))
            elif p.StorageType == StorageType.ElementId:
                eid = p.AsElementId()
                if eid and _eid_int(eid) != -1:
                    e = doc.GetElement(eid)
                    if e: return e.Name
                    return str(_eid_int(eid))
    except:
        pass
    return ""

def get_param_value_numeric(elem, param_name):
    try:
        p = elem.LookupParameter(param_name)
        if p and p.HasValue:
            if p.StorageType == StorageType.Integer:
                return float(p.AsInteger())
            elif p.StorageType == StorageType.Double:
                return p.AsDouble()
    except:
        pass
    return None

def aggregate_values(elements, param_name, agg_type):
    if not param_name:
        return ""
    if agg_type == AGG_COUNT:
        return str(len(elements))

    # Read values
    vals_str = []
    vals_num = []
    for elem in elements:
        v_str = get_param_value_str(elem, param_name)
        if v_str:
            vals_str.append(v_str)
        v_num = get_param_value_numeric(elem, param_name)
        if v_num is not None:
            vals_num.append(v_num)

    if agg_type == AGG_SUM:
        return str(round(sum(vals_num), 2)) if vals_num else "0"
    elif agg_type == AGG_AVERAGE:
        return str(round(sum(vals_num)/len(vals_num), 2)) if vals_num else "0"
    elif agg_type == AGG_MIN:
        if vals_num: return str(round(min(vals_num), 2))
        return str(min(vals_str)) if vals_str else ""
    elif agg_type == AGG_MAX:
        if vals_num: return str(round(max(vals_num), 2))
        return str(max(vals_str)) if vals_str else ""
    elif agg_type == AGG_FIRST:
        return get_param_value_str(elements[0], param_name) if elements else ""
    elif agg_type == AGG_LAST:
        return get_param_value_str(elements[-1], param_name) if elements else ""
    elif agg_type == AGG_LIST:
        return ", ".join(vals_str)
    elif agg_type == AGG_UNIQUE:
        return ", ".join(sorted(list(set(vals_str))))
    return ""

def get_writable_spatial_params(spatial_elem):
    params = []
    if not spatial_elem:
        return params
    try:
        for p in spatial_elem.Parameters:
            if not p.IsReadOnly:
                name = p.Definition.Name
                if name and name not in params:
                    params.append(name)
    except:
        pass
    return sorted(params)

def get_spatial_info(elem, stype):
    number = ""
    name = ""
    level = ""
    try:
        if stype == ROOMS:
            number = elem.Number or ""
            p = elem.get_Parameter(BuiltInParameter.ROOM_NAME)
            if p: name = p.AsString() or ""
        elif stype == AREAS:
            p = elem.get_Parameter(BuiltInParameter.ROOM_NUMBER)
            if p: number = p.AsString() or ""
            p = elem.get_Parameter(BuiltInParameter.ROOM_NAME)
            if p: name = p.AsString() or ""
        elif stype == SPACES:
            number = elem.Number or ""
            p = elem.get_Parameter(BuiltInParameter.ROOM_NAME)
            if p: name = p.AsString() or ""

        lp = elem.get_Parameter(BuiltInParameter.ROOM_LEVEL_ID)
        if lp:
            lid = lp.AsElementId()
            if lid and _eid_int(lid) != -1:
                le = doc.GetElement(lid)
                if le: level = le.Name
    except:
        pass
    return number, name, level


# ── Tab 1 Classes ─────────────────────────────────────────────────────────────
class Tab1SpatialItem:
    def __init__(self, elem, stype):
        self.element = elem
        self.element_id = _eid_int(elem.Id)
        self.spatial_type = stype
        self.is_selected = False
        self.number = ""
        self.name = ""
        self.level = ""
        self.boundary_ids = set()
        self.all_params = []
        self.polygons = []
        self.solid = None
        self.bbox = None
        self.spaces = []
        
        try:
            if stype == ROOMS:
                self.number = elem.Number or ""
                p = elem.get_Parameter(BuiltInParameter.ROOM_NAME)
                if p: self.name = p.AsString() or ""
                self.boundary_ids = get_boundary_elements(elem)
                self.all_params = get_room_parameters(elem)
            elif stype == AREAS:
                p = elem.get_Parameter(BuiltInParameter.ROOM_NUMBER)
                if p: self.number = p.AsString() or ""
                p = elem.get_Parameter(BuiltInParameter.ROOM_NAME)
                if p: self.name = p.AsString() or ""
                self.boundary_ids = get_boundary_elements(elem)
                self.polygons = get_area_polygon(elem)
                self.all_params = get_room_parameters(elem)
            elif stype == SPACES:
                self.number = elem.Number or ""
                p = elem.get_Parameter(BuiltInParameter.ROOM_NAME)
                if p: self.name = p.AsString() or ""
                self.boundary_ids = get_boundary_elements(elem)
                self.all_params = get_room_parameters(elem)
            elif stype == ZONES:
                self.name = elem.Name or ""
                self.spaces = []
                try:
                    zone_spaces = elem.Spaces
                    if zone_spaces:
                        for space in zone_spaces:
                            if space and space.Area > 0:
                                self.spaces.append(space)
                except: pass
                if not self.name:
                    self.number = str(_eid_int(elem.Id))
                self.all_params = get_room_parameters(elem)
            elif stype == MASSES:
                self.name = elem.Name or ""
                self.solid = get_mass_solid(elem)
                self.all_params = get_room_parameters(elem)
            elif stype == SCOPEBOXES:
                self.name = elem.Name or ""
                self.bbox = get_scopebox_bbox(elem)
                self.all_params = get_room_parameters(elem)
            
            # Level ID
            lp = elem.get_Parameter(BuiltInParameter.ROOM_LEVEL_ID)
            if lp:
                lid = lp.AsElementId()
                if lid and _eid_int(lid) != -1:
                    le = doc.GetElement(lid)
                    if le: self.level = le.Name
            
            if not self.level and stype == SCOPEBOXES:
                self.number = str(_eid_int(elem.Id))
        except: pass

    @property
    def display_name(self):
        parts = []
        if self.number:
            parts.append(self.number)
        if self.name:
            parts.append(self.name)
        if not parts:
            return "ID: " + str(self.element_id)
        return " - ".join(parts)


class Tab1CatItem:
    def __init__(self, name, bic, count):
        self.name = name
        self.bic = bic
        self.count = count
        self.is_selected = False


class Tab1ResultGroup:
    def __init__(self, cat_name, family_name, type_name, spatial_item):
        self.category_name = cat_name
        self.family_name = family_name
        self.type_name = type_name
        self.spatial_item = spatial_item
        self.elements = []
        self.is_selected = True
    
    @property
    def count(self):
        return len(self.elements)
    
    def add_element(self, elem):
        self.elements.append(elem)
        
    def get_define_value(self, params, separator="_"):
        vals = []
        for p in params:
            if p == "Category":
                vals.append(self.category_name)
            elif p == "Family Name":
                vals.append(self.family_name)
            elif p == "Type Name":
                vals.append(self.type_name)
            else:
                vals.append(get_param_value(self.spatial_item.element, p))
        return separator.join([v for v in vals if v])


# DefineValueDialog (for Tab 1 configure defined values)
# Hai dialog con dùng XAML chuẩn T3 — bản dựng tay cũ chết ngay khi mở:
# Application.Current là None trong Revit và DockPanel không có Padding.
DEFINE_XAML = os.path.join(GUI_DIR, 'Tools', 'ContainsDefineValue.xaml')
SETPARAM_XAML = os.path.join(GUI_DIR, 'Tools', 'ContainsSetParam.xaml')


def _list_items(listbox):
    return [str(listbox.Items[i]) for i in range(listbox.Items.Count)]


class DefineValueDialog(T3WPFWindow):
    def __init__(self, available_params, current_selected, current_separator):
        T3WPFWindow.__init__(self, DEFINE_XAML)
        self.available_params = ["Category", "Family Name", "Type Name"] + list(available_params)
        self.selected_params = list(current_selected)
        self.result = None
        self.txt_sep.Text = current_separator or ""
        self._load()

    def _load(self):
        for p in self.available_params:
            if p not in self.selected_params:
                self.avail_list.Items.Add(p)
        for p in self.selected_params:
            self.selected_list.Items.Add(p)
        self._refresh()

    def _refresh(self):
        vis = System.Windows.Visibility
        self.empty_avail.Visibility = vis.Visible if self.avail_list.Items.Count == 0 else vis.Collapsed
        self.empty_selected.Visibility = vis.Visible if self.selected_list.Items.Count == 0 else vis.Collapsed
        self.lbl_prev.Text = "Preview: " + (self.txt_sep.Text or "").join(_list_items(self.selected_list))

    def _move(self, src, dst):
        for item in list(src.SelectedItems):
            dst.Items.Add(item)
            src.Items.Remove(item)
        self._refresh()

    def move_right_clicked(self, sender, e):
        self._move(self.avail_list, self.selected_list)

    def move_left_clicked(self, sender, e):
        self._move(self.selected_list, self.avail_list)

    def _shift(self, step):
        lst = self.selected_list
        idx = lst.SelectedIndex
        new = idx + step
        if idx < 0 or new < 0 or new >= lst.Items.Count:
            return
        val = lst.Items[idx]
        lst.Items.RemoveAt(idx)
        lst.Items.Insert(new, val)
        lst.SelectedIndex = new
        self._refresh()

    def move_up_clicked(self, sender, e):
        self._shift(-1)

    def move_down_clicked(self, sender, e):
        self._shift(1)

    def separator_changed(self, sender, e):
        if hasattr(self, 'selected_list'):
            self._refresh()

    def ok_clicked(self, sender, e):
        params = _list_items(self.selected_list)
        if not params:
            TaskDialog.Show("Configure Defined Value",
                            "No parameter selected. Add at least one parameter to build the value.")
            return
        self.result = {"params": params, "separator": self.txt_sep.Text}
        self.DialogResult = True
        self.Close()

    def cancel_clicked(self, sender, e):
        self.Close()


# SetParamDialog (for Tab 1 Set Parameter Value Dialog)
class SetParamDialog(T3WPFWindow):
    _DEFAULT_PARAMS = ("DQT_Contain_SpatialID", "IFC-SG_RoomNumber", "Comments", "Mark")

    def __init__(self, selected_groups, spatial_type, define_params, define_separator):
        T3WPFWindow.__init__(self, SETPARAM_XAML)
        self.selected_groups = selected_groups
        self.spatial_type = spatial_type
        self.define_params = define_params
        self.define_separator = define_separator
        self.result = None
        self._load()

    def _load(self):
        params = []
        for g in self.selected_groups:
            if g.elements:
                params = get_str_params(g.elements[0])
                break
        for p in params:
            self.param_cb.Items.Add(p)
        for p in self._DEFAULT_PARAMS:
            if p in params:
                self.param_cb.Text = p
                break
        count = sum(g.count for g in self.selected_groups)
        sample = self.selected_groups[0].get_define_value(self.define_params, self.define_separator) \
            if self.selected_groups else ""
        self.lbl_value.Text = "{} element(s) in {} group(s). Example value: {}".format(
            count, len(self.selected_groups), sample or "(empty)")

    def ok_clicked(self, sender, e):
        param = (self.param_cb.Text or "").strip()
        if not param:
            TaskDialog.Show("Set Parameter", "No target parameter. Pick or type a parameter name.")
            return
        self.result = {"mode": "custom", "param": param}
        self.DialogResult = True
        self.Close()

    def cancel_clicked(self, sender, e):
        self.Close()


# ── Tab 2 Classes ─────────────────────────────────────────────────────────────
class Tab2SpatialData:
    def __init__(self, elem, stype):
        self.element = elem
        self.element_id = _eid_int(elem.Id)
        self.spatial_type = stype
        self.is_selected = False
        self.number, self.name, self.level = get_spatial_info(elem, stype)
        self.polygons = []
        self.boundary_ids = set()
        if stype == AREAS:
            self.polygons = get_area_polygon(elem)
        if stype in (ROOMS, SPACES):
            self.boundary_ids = get_boundary_elements(elem)
        self.contained_elements = []
        self.aggregated_value = ""

    @property
    def display_name(self):
        parts = []
        if self.number:
            parts.append(self.number)
        if self.name:
            parts.append(self.name)
        if not parts:
            return "ID: " + str(self.element_id)
        return " - ".join(parts)


class Tab2CatItem:
    def __init__(self, name, cat_id, count):
        self.name = name
        self.cat_id = cat_id
        self.count = count
        self.is_selected = False


class Tab2CollectResult:
    def __init__(self, spatial, elements, param_name, agg_type):
        self.spatial = spatial
        self.elements = elements
        self.element_count = len(elements)
        self.param_name = param_name
        self.agg_type = agg_type
        self.agg_value = aggregate_values(elements, param_name, agg_type) if param_name else str(len(elements))
        self.is_selected = False





# ═══ MAIN WINDOW CONTROLLER ═══
class ManaContainsWindow(T3WPFWindow):
    def __init__(self):
        T3WPFWindow.__init__(self, XAML_FILE)
        
        # Chrome controls
        self.btn_minimize.Click += self._minimize
        self.btn_maximize.Click += self._maximize
        self.btn_close.Click += self._close_chrome
        
        # Tab Control Event
        self.nav_contains.Checked += self._on_tab_changed
        self.nav_collector.Checked += self._on_tab_changed
        
        # ── INITIALIZE TAB 1 VARIABLES ──
        self.t1_spatial_items = []
        self.t1_cat_items = []
        self.t1_result_groups = []
        self.t1_all_groups = []
        self.t1_spatial_type = ROOMS
        self.t1_define_params = ["Number", "Name"]
        self.t1_define_separator = "_"
        self.t1_available_params = []
        self.t1_sort_key = None
        self.t1_sort_ascending = True
        
        # Bind Tab 1 events
        self.tab1_rb_whole.Checked += self._t1_scope
        self.tab1_rb_view.Checked += self._t1_scope
        # NOTE: tab1_type_cb.SelectionChanged is wired after _t1_init_combo() below
        self.tab1_sp_search.TextChanged += self._t1_sp_ss
        self.tab1_sp_all_btn.Click += self._t1_sp_all
        self.tab1_sp_clr_btn.Click += self._t1_sp_clr
        self.tab1_sp_hide.Checked += self._t1_sp_h
        self.tab1_sp_hide.Unchecked += self._t1_sp_h
        
        self.tab1_cat_search.TextChanged += self._t1_cat_ss
        self.tab1_cat_all_btn.Click += self._t1_cat_all
        self.tab1_cat_clr_btn.Click += self._t1_cat_clr
        self.tab1_cat_hide.Checked += self._t1_cat_h
        self.tab1_cat_hide.Unchecked += self._t1_cat_h
        
        self.tab1_res_search.TextChanged += self._t1_res_ss
        self.tab1_btn_cfg.Click += self._t1_cfg_define
        self.tab1_res_all_cb.Checked += self._t1_res_all_ck
        self.tab1_res_all_cb.Unchecked += self._t1_res_all_uck
        
        # Sorting Buttons
        self.tab1_sort_cat.Click += self._t1_sort_click
        self.tab1_sort_fam.Click += self._t1_sort_click
        self.tab1_sort_type.Click += self._t1_sort_click
        self.tab1_sort_val.Click += self._t1_sort_click
        self.tab1_sort_cnt.Click += self._t1_sort_click
        
        self.tab1_sort_cat.Tag = "category"
        self.tab1_sort_fam.Tag = "family"
        self.tab1_sort_type.Tag = "type"
        self.tab1_sort_val.Tag = "define"
        self.tab1_sort_cnt.Tag = "count"
        
        # Action Buttons Tab 1
        self.tab1_btn_reset.Click += self._t1_reset
        self.tab1_btn_viz.Click += self._t1_viz
        self.tab1_btn_find.Click += self._t1_find
        self.tab1_btn_set.Click += self._t1_set
        self.tab1_btn_sel.Click += self._t1_sel
        self.tab1_btn_close.Click += self._close_chrome
        
        # ── INITIALIZE TAB 2 VARIABLES ──
        self.t2_spatial_type = ROOMS
        self.t2_spatial_items = []
        self.t2_cat_items = []
        self.t2_results = []
        self.t2_all_results = []
        self.t2_elem_params = []
        
        # Bind Tab 2 events
        self.tab2_rb_rooms.Checked += self._t2_on_type_changed
        self.tab2_rb_areas.Checked += self._t2_on_type_changed
        self.tab2_rb_spaces.Checked += self._t2_on_type_changed
        self.tab2_cb_view_only.Checked += self._t2_on_scope_changed
        self.tab2_cb_view_only.Unchecked += self._t2_on_scope_changed
        
        self.tab2_sp_all_btn.Click += self._t2_sel_all_spatial
        self.tab2_sp_none_btn.Click += self._t2_sel_none_spatial
        self.tab2_sp_invert_btn.Click += self._t2_sel_invert_spatial
        self.tab2_spatial_search.TextChanged += self._t2_on_spatial_search
        
        self.tab2_cat_all_btn.Click += self._t2_sel_all_cats
        self.tab2_cat_none_btn.Click += self._t2_sel_none_cats
        self.tab2_cat_search.TextChanged += self._t2_on_cat_search
        
        self.tab2_result_search.TextChanged += self._t2_on_result_search
        self.tab2_res_all_btn.Click += self._t2_sel_all_results
        self.tab2_res_none_btn.Click += self._t2_sel_none_results
        
        self.tab2_btn_collect.Click += self._t2_on_collect
        self.tab2_btn_apply.Click += self._t2_on_apply
        self.tab2_btn_select.Click += self._t2_on_select
        self.tab2_btn_close.Click += self._close_chrome
        
        # Load Initial Data
        self._t1_init_combo()
        # Wire type combobox AFTER init so first Items.Add doesn't trigger early load
        self.tab1_type_cb.SelectionChanged += self._t1_type
        self._t1_load()
        self._t2_load_data()

        # Force initial tab content to render: nav_contains.IsChecked was already
        # True when the XAML was parsed, so its Checked event fired before the
        # += wiring above and tab_control.SelectedIndex was never explicitly set
        # (same fix as ManaSheets/ManaViews/ManaAnno/ManaPara).
        self.tab_control.SelectedIndex = 0

    # ── Chrome Event Handlers ────────────────────────────────────
    def _minimize(self, sender, e):
        self.WindowState = WindowState.Minimized

    def _maximize(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def _close_chrome(self, sender, e):
        self.Close()
        
    def _on_tab_changed(self, sender, e):
        if not hasattr(self, 'tab_control'):
            return
        if self.nav_contains.IsChecked:
            self.tab_control.SelectedIndex = 0
        elif self.nav_collector.IsChecked:
            self.tab_control.SelectedIndex = 1

    # =====================================================================
    # TAB 1 LOGIC (Contains Manager)
    # =====================================================================
    def _t1_init_combo(self):
        self.tab1_type_cb.Items.Clear()
        self.tab1_type_cb.Items.Add(ROOMS)
        self.tab1_type_cb.Items.Add(AREAS)
        self.tab1_type_cb.Items.Add(SPACES)
        self.tab1_type_cb.Items.Add(ZONES)
        self.tab1_type_cb.Items.Add(MASSES)
        self.tab1_type_cb.Items.Add(SCOPEBOXES)
        self.tab1_type_cb.SelectedIndex = 0

    def _t1_load(self):
        _safe_call(self._t1_load_sp, "spatial elements")
        _safe_call(self._t1_load_cat, "categories")
        _safe_call(self._t1_ucards, "summary cards")
        _safe_call(self._t1_update_available_params, "available params")
        
    def _t1_update_available_params(self):
        self.t1_available_params = []
        if self.t1_spatial_items:
            self.t1_available_params = self.t1_spatial_items[0].all_params

    def _t1_load_sp(self):
        self.t1_spatial_items = []
        vo = bool(self.tab1_rb_view.IsChecked)
        st = self.t1_spatial_type
        
        elems = []
        if st == ROOMS: elems = get_rooms(vo)
        elif st == AREAS: elems = get_areas(vo)
        elif st == SPACES: elems = get_spaces(vo)
        elif st == ZONES: elems = get_zones(vo)
        elif st == MASSES: elems = get_masses(vo)
        elif st == SCOPEBOXES: elems = get_scopeboxes(vo)
        
        for e in elems:
            try:
                self.t1_spatial_items.append(Tab1SpatialItem(e, st))
            except Exception:
                pass
        self._t1_ref_sp()

    def _t1_load_cat(self):
        self.t1_cat_items = []
        vo = bool(self.tab1_rb_view.IsChecked)
        cats = get_cats()
        for name, bic in cats:
            try:
                col = FilteredElementCollector(doc)
                if vo: col = FilteredElementCollector(doc, doc.ActiveView.Id)
                count = col.OfCategory(bic).WhereElementIsNotElementType().GetElementCount()
                if count > 0:
                    self.t1_cat_items.append(Tab1CatItem(name, bic, count))
            except: pass
        self._t1_ref_cat()

    def _t1_ref_sp(self):
        self.tab1_sp_list.Items.Clear()
        ft = self.tab1_sp_search.Text.lower() if self.tab1_sp_search.Text else ""
        hide = bool(self.tab1_sp_hide.IsChecked)
        
        for item in self.t1_spatial_items:
            if hide and not item.is_selected: continue
            if ft and ft not in item.display_name.lower(): continue
            
            cb = CheckBox()
            cb.Content = item.display_name
            cb.IsChecked = item.is_selected
            cb.Tag = item
            cb.Checked += self._t1_sp_ck
            cb.Unchecked += self._t1_sp_ck
            self.tab1_sp_list.Items.Add(cb)

    def _t1_ref_cat(self):
        self.tab1_cat_list.Items.Clear()
        ft = self.tab1_cat_search.Text.lower() if self.tab1_cat_search.Text else ""
        hide = bool(self.tab1_cat_hide.IsChecked)
        
        for item in self.t1_cat_items:
            if hide and not item.is_selected: continue
            if ft and ft not in item.name.lower(): continue
            
            cb = CheckBox()
            cb.Content = item.name + " (" + str(item.count) + ")"
            cb.IsChecked = item.is_selected
            cb.Tag = item
            cb.Checked += self._t1_cat_ck
            cb.Unchecked += self._t1_cat_ck
            self.tab1_cat_list.Items.Add(cb)

    def _t1_ref_res(self):
        self.tab1_res_list.Items.Clear()
        ft = self.tab1_res_search.Text.lower() if self.tab1_res_search.Text else ""
        
        for grp in self.t1_result_groups:
            match = True
            if ft:
                val = grp.get_define_value(self.t1_define_params, self.t1_define_separator).lower()
                if ft not in grp.category_name.lower() and ft not in grp.family_name.lower() and ft not in grp.type_name.lower() and ft not in val:
                    match = False
            if match:
                row = self._t1_res_row(grp)
                self.tab1_res_list.Items.Add(row)
        self._t1_ustatus()

    def _t1_res_row(self, grp):
        bd = Border()
        bd.Padding = Thickness(2)
        bd.BorderBrush = brush(BORDER)
        bd.BorderThickness = Thickness(0,0,0,1)
        
        gp = WPFGrid()
        _star = GridLength(1, GridUnitType.Star)
        for w in (30, 150, 180, 180, None, 65):
            cd = ColumnDefinition()
            cd.Width = _star if w is None else GridLength(w)
            gp.ColumnDefinitions.Add(cd)
        
        cb = CheckBox()
        cb.IsChecked = grp.is_selected
        cb.Tag = grp
        cb.Checked += self._t1_res_ck
        cb.Unchecked += self._t1_res_ck
        cb.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        cb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        WPFGrid.SetColumn(cb, 0)
        gp.Children.Add(cb)
        
        c_lbl = TextBlock()
        c_lbl.Text = grp.category_name
        c_lbl.VerticalAlignment = System.Windows.VerticalAlignment.Center
        c_lbl.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        c_lbl.Margin = Thickness(4,0,0,0)
        WPFGrid.SetColumn(c_lbl, 1)
        gp.Children.Add(c_lbl)

        f_lbl = TextBlock()
        f_lbl.Text = grp.family_name
        f_lbl.VerticalAlignment = System.Windows.VerticalAlignment.Center
        f_lbl.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        f_lbl.Margin = Thickness(4,0,0,0)
        WPFGrid.SetColumn(f_lbl, 2)
        gp.Children.Add(f_lbl)

        t_lbl = TextBlock()
        t_lbl.Text = grp.type_name
        t_lbl.VerticalAlignment = System.Windows.VerticalAlignment.Center
        t_lbl.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        t_lbl.Margin = Thickness(4,0,0,0)
        WPFGrid.SetColumn(t_lbl, 3)
        gp.Children.Add(t_lbl)

        v_lbl = TextBlock()
        v_lbl.Text = grp.get_define_value(self.t1_define_params, self.t1_define_separator)
        v_lbl.VerticalAlignment = System.Windows.VerticalAlignment.Center
        v_lbl.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        v_lbl.Margin = Thickness(4,0,0,0)
        WPFGrid.SetColumn(v_lbl, 4)
        gp.Children.Add(v_lbl)

        cnt = TextBlock()
        cnt.Text = str(grp.count)
        cnt.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cnt.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        WPFGrid.SetColumn(cnt, 5)
        gp.Children.Add(cnt)
        
        bd.Child = gp
        return bd

    def _t1_sp_ck(self, s, e):
        s.Tag.is_selected = bool(s.IsChecked)
        self._t1_update_available_params()

    def _t1_cat_ck(self, s, e):
        s.Tag.is_selected = bool(s.IsChecked)

    def _t1_res_ck(self, s, e):
        s.Tag.is_selected = bool(s.IsChecked)
        self._t1_ustatus()

    def _t1_sp_all(self, s, e):
        for item in self.t1_spatial_items: item.is_selected = True
        self._t1_ref_sp()
        self._t1_update_available_params()

    def _t1_sp_clr(self, s, e):
        for item in self.t1_spatial_items: item.is_selected = False
        self._t1_ref_sp()

    def _t1_cat_all(self, s, e):
        for item in self.t1_cat_items: item.is_selected = True
        self._t1_ref_cat()

    def _t1_cat_clr(self, s, e):
        for item in self.t1_cat_items: item.is_selected = False
        self._t1_ref_cat()

    def _t1_sp_h(self, s, e):
        self._t1_ref_sp()

    def _t1_cat_h(self, s, e):
        self._t1_ref_cat()

    def _t1_scope(self, s, e):
        if not hasattr(self, 'tab1_type_cb') or self.tab1_type_cb.SelectedIndex == -1: return
        self._t1_load()

    def _t1_type(self, s, e):
        if not hasattr(self, 'tab1_type_cb') or self.tab1_type_cb.SelectedIndex == -1: return
        st = str(self.tab1_type_cb.SelectedItem)
        self.t1_spatial_type = st
        self._t1_load_sp()
        self._t1_update_available_params()
        self._t1_ucards()

    def _t1_sp_ss(self, s, e):
        self._t1_ref_sp()

    def _t1_cat_ss(self, s, e):
        self._t1_ref_cat()

    def _t1_res_ss(self, s, e):
        self._t1_ref_res()

    def _t1_res_all_ck(self, s, e):
        for grp in self.t1_result_groups: grp.is_selected = True
        self._t1_ref_res()

    def _t1_res_all_uck(self, s, e):
        for grp in self.t1_result_groups: grp.is_selected = False
        self._t1_ref_res()

    def _t1_ucards(self):
        vo = bool(self.tab1_rb_view.IsChecked)
        st = self.t1_spatial_type
        
        rooms = len(get_rooms(vo))
        areas = len(get_areas(vo))
        spaces = len(get_spaces(vo))
        zones = len(get_zones(vo))
        masses = len(get_masses(vo))
        scopebox = len(get_scopeboxes(vo))
        
        self.tab1_card_rooms.Text = str(rooms)
        self.tab1_card_areas.Text = str(areas)
        self.tab1_card_spaces.Text = str(spaces)
        self.tab1_card_zones.Text = str(zones)
        self.tab1_card_masses.Text = str(masses)
        self.tab1_card_scopeboxes.Text = str(scopebox)
        
        found = sum([g.count for g in self.t1_all_groups])
        self.tab1_card_found.Text = str(found)

    def _t1_ustatus(self):
        total = sum([g.count for g in self.t1_result_groups])
        sel = sum([g.count for g in self.t1_result_groups if g.is_selected])
        self.tab1_status.Text = "Total number of elements found " + str(total) + " | Selected " + str(sel)

    def _t1_sort_click(self, s, e):
        key = s.Tag
        if self.t1_sort_key == key:
            self.t1_sort_ascending = not self.t1_sort_ascending
        else:
            self.t1_sort_key = key
            self.t1_sort_ascending = True
            
        # Perform sort
        if key == "category":
            self.t1_result_groups.sort(key=lambda g: g.category_name, reverse=not self.t1_sort_ascending)
        elif key == "family":
            self.t1_result_groups.sort(key=lambda g: g.family_name, reverse=not self.t1_sort_ascending)
        elif key == "type":
            self.t1_result_groups.sort(key=lambda g: g.type_name, reverse=not self.t1_sort_ascending)
        elif key == "define":
            self.t1_result_groups.sort(key=lambda g: g.get_define_value(self.t1_define_params, self.t1_define_separator), reverse=not self.t1_sort_ascending)
        elif key == "count":
            self.t1_result_groups.sort(key=lambda g: g.count, reverse=not self.t1_sort_ascending)
            
        self._t1_ref_res()

    def _t1_reset(self, s, e):
        for item in self.t1_spatial_items: item.is_selected = False
        for item in self.t1_cat_items: item.is_selected = False
        self.t1_result_groups = []
        self.t1_all_groups = []
        self.tab1_sp_search.Text = ""
        self.tab1_cat_search.Text = ""
        self.tab1_res_search.Text = ""
        self.tab1_sp_hide.IsChecked = False
        self.tab1_cat_hide.IsChecked = False
        self._t1_ref_sp()
        self._t1_ref_cat()
        self._t1_ref_res()
        self._t1_ucards()

    def _t1_viz(self, s, e):
        sel = [i for i in self.t1_spatial_items if i.is_selected]
        if not sel:
            TaskDialog.Show("Visualize", "Please select spatial element(s) first.")
            return
        ids = List[ElementId]()
        for i in sel: ids.Add(i.element.Id)
        uidoc.Selection.SetElementIds(ids)
        TaskDialog.Show("Visualize", "Selected " + str(len(sel)) + " element(s).")

    def _t1_cfg_define(self, s, e):
        dlg = DefineValueDialog(self.t1_available_params, self.t1_define_params, self.t1_define_separator)
        dlg.Owner = self
        result = dlg.ShowDialog()
        if result and dlg.result:
            self.t1_define_params = dlg.result["params"]
            self.t1_define_separator = dlg.result["separator"]
            self._t1_ref_res()

    def _t1_find(self, s, e):
        sel_sp = [i for i in self.t1_spatial_items if i.is_selected]
        if not sel_sp:
            TaskDialog.Show("Find", "Please select spatial element(s) first.")
            return
        sel_cat = [i for i in self.t1_cat_items if i.is_selected]
        if not sel_cat:
            TaskDialog.Show("Find", "Please select category(ies) first.")
            return
        
        self.t1_result_groups = []
        self.t1_all_groups = []
        groups = {}
        vo = bool(self.tab1_rb_view.IsChecked)
        
        try:
            for cat in sel_cat:
                use_bbox = cat.bic in BBOX_CATEGORIES
                elems = get_elems(cat.bic, vo)
                
                for elem in elems:
                    try:
                        fname, tname = get_family_type_name(elem)
                        for sp_item in sel_sp:
                            inside = check_element_in_spatial(elem, sp_item, use_bbox)
                            if inside:
                                key = (cat.name, fname, tname, sp_item.element_id)
                                if key not in groups:
                                    groups[key] = Tab1ResultGroup(cat.name, fname, tname, sp_item)
                                groups[key].add_element(elem)
                                break
                    except: continue
            
            self.t1_all_groups = list(groups.values())
            self.t1_result_groups = list(groups.values())
            self._t1_ref_res()
            self._t1_ucards()
            
            total = sum([g.count for g in self.t1_all_groups])
            TaskDialog.Show("Find Results", "Found " + str(total) + " element(s) in " + str(len(self.t1_all_groups)) + " groups.")
        except Exception as ex:
            TaskDialog.Show("Error", "Error during find: " + str(ex))

    def _t1_set(self, s, e):
        sel_groups = [g for g in self.t1_result_groups if g.is_selected]
        if not sel_groups:
            TaskDialog.Show("Set Parameter", "No elements selected.")
            return
        dlg = SetParamDialog(sel_groups, self.t1_spatial_type, self.t1_define_params, self.t1_define_separator)
        dlg.Owner = self
        result = dlg.ShowDialog()
        if not result or not dlg.result: return
        mode = dlg.result["mode"]
        param = dlg.result["param"]
        if mode == "none": return
        t = Transaction(doc, "Set Parameters - Contains Manager")
        t.Start()
        try:
            ok, fail = 0, 0
            for grp in sel_groups:
                val = grp.get_define_value(self.t1_define_params, self.t1_define_separator)
                for elem in grp.elements:
                    try:
                        p = elem.LookupParameter(param)
                        if p and not p.IsReadOnly:
                            p.Set(val)
                            ok += 1
                        else: fail += 1
                    except: fail += 1
            t.Commit()
            TaskDialog.Show("Set Parameter", "Updated: " + str(ok) + "\nFailed: " + str(fail))
        except Exception as ex:
            t.RollBack()
            TaskDialog.Show("Error", str(ex))

    def _t1_sel(self, s, e):
        sel_groups = [g for g in self.t1_result_groups if g.is_selected]
        if not sel_groups:
            TaskDialog.Show("Select", "No elements selected.")
            return
        ids = List[ElementId]()
        for grp in sel_groups:
            for elem in grp.elements: ids.Add(elem.Id)
        uidoc.Selection.SetElementIds(ids)
        TaskDialog.Show("Select", "Selected " + str(ids.Count) + " element(s).")


    # =====================================================================
    # TAB 2 LOGIC (Room Data Collector)
    # =====================================================================
    def _t2_load_data(self):
        # Load Aggregation Types
        self.tab2_cmb_agg_type.Items.Clear()
        for agg in ALL_AGG:
            item = ComboBoxItem()
            item.Content = agg
            self.tab2_cmb_agg_type.Items.Add(item)
        self.tab2_cmb_agg_type.SelectedIndex = 0

        _safe_call(self._t2_load_spatial, "spatial elements (collector)")
        _safe_call(self._t2_load_cats, "categories (collector)")
        _safe_call(self._t2_load_target_params, "target params")
        _safe_call(self._t2_update_cards, "summary cards (collector)")

    def _t2_load_spatial(self):
        self.t2_spatial_items = []
        vo = bool(self.tab2_cb_view_only.IsChecked)
        stype = self.t2_spatial_type
        
        elems = []
        if stype == ROOMS: elems = get_rooms(vo)
        elif stype == AREAS: elems = get_areas(vo)
        elif stype == SPACES: elems = get_spaces(vo)
        
        for e in elems:
            try:
                self.t2_spatial_items.append(Tab2SpatialData(e, stype))
            except Exception:
                pass
        self._t2_refresh_spatial_list()

    def _t2_load_cats(self):
        self.t2_cat_items = []
        vo = bool(self.tab2_cb_view_only.IsChecked)
        cats = get_categories()
        for name, cid in cats:
            try:
                col = FilteredElementCollector(doc)
                if vo: col = FilteredElementCollector(doc, doc.ActiveView.Id)
                bic = System.Enum.ToObject(BuiltInCategory, cid)
                count = col.OfCategory(bic).WhereElementIsNotElementType().GetElementCount()
                if count > 0:
                    self.t2_cat_items.append(Tab2CatItem(name, cid, count))
            except:
                pass
        self._t2_refresh_cat_list()

    def _t2_load_target_params(self):
        self.tab2_cmb_target_param.Items.Clear()
        if self.t2_spatial_items:
            params = get_writable_spatial_params(self.t2_spatial_items[0].element)
            for p in params:
                item = ComboBoxItem()
                item.Content = p
                self.tab2_cmb_target_param.Items.Add(item)
            
            # Select default if exists
            defaults = ["DQT_Room_Elements", "Comments", "Description"]
            for d in defaults:
                if d in params:
                    self.tab2_cmb_target_param.Text = d
                    break

    def _t2_refresh_spatial_list(self, filter_text=""):
        self.tab2_spatial_panel.Children.Clear()
        ft = filter_text.lower() if filter_text else ""
        for si in self.t2_spatial_items:
            if ft and ft not in si.display_name.lower():
                continue
            row = self._t2_make_check_row(si.display_name, si, "spatial")
            self.tab2_spatial_panel.Children.Add(row)

    def _t2_refresh_cat_list(self, filter_text=""):
        self.tab2_cat_panel.Children.Clear()
        ft = filter_text.lower() if filter_text else ""
        for ci in self.t2_cat_items:
            if ft and ft not in ci.name.lower():
                continue
            row = self._t2_make_check_row(ci.name + " (" + str(ci.count) + ")", ci, "cat")
            self.tab2_cat_panel.Children.Add(row)

    def _t2_make_check_row(self, text, data_item, tag):
        cb = CheckBox()
        cb.Content = text
        cb.FontSize = 12
        cb.Margin = Thickness(4, 1, 4, 1)
        cb.IsChecked = data_item.is_selected
        cb.Tag = data_item
        cb.Checked += self._t2_on_check_changed
        cb.Unchecked += self._t2_on_check_changed
        return cb

    def _t2_on_check_changed(self, sender, e):
        if sender.Tag:
            sender.Tag.is_selected = bool(sender.IsChecked)
            if isinstance(sender.Tag, Tab2CatItem):
                self._t2_update_source_params()

    def _t2_update_source_params(self):
        sel_cats = [ci for ci in self.t2_cat_items if ci.is_selected]
        if not sel_cats:
            self.tab2_cmb_source_param.Items.Clear()
            return
        
        old_text = ""
        if self.tab2_cmb_source_param.SelectedItem:
            old_text = self.tab2_cmb_source_param.SelectedItem.Content
        elif self.tab2_cmb_source_param.Text:
            old_text = self.tab2_cmb_source_param.Text
            
        sample_elems = []
        for ci in sel_cats:
            try:
                bic = System.Enum.ToObject(BuiltInCategory, ci.cat_id)
                col = FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()
                count = 0
                for elem in col:
                    sample_elems.append(elem)
                    count += 1
                    if count >= 5:
                        break
            except:
                pass
                
        if sample_elems:
            self.t2_elem_params = get_element_params(sample_elems)
            self.tab2_cmb_source_param.Items.Clear()
            for pn in self.t2_elem_params:
                item = ComboBoxItem()
                item.Content = pn
                self.tab2_cmb_source_param.Items.Add(item)
            if old_text:
                self.tab2_cmb_source_param.Text = old_text

    def _t2_update_cards(self):
        vo = bool(self.tab2_cb_view_only.IsChecked)
        rooms = len(get_rooms(vo))
        areas = len(get_areas(vo))
        spaces = len(get_spaces(vo))
        
        self.tab2_card_rooms.Text = str(rooms)
        self.tab2_card_areas.Text = str(areas)
        self.tab2_card_spaces.Text = str(spaces)
        self.tab2_card_found.Text = str(len(self.t2_results))

    def _t2_on_type_changed(self, s, e):
        if not hasattr(self, 'tab2_rb_rooms'): return
        if self.tab2_rb_rooms.IsChecked: self.t2_spatial_type = ROOMS
        elif self.tab2_rb_areas.IsChecked: self.t2_spatial_type = AREAS
        elif self.tab2_rb_spaces.IsChecked: self.t2_spatial_type = SPACES
        self._t2_load_spatial()
        self._t2_load_target_params()
        self._t2_update_cards()

    def _t2_on_scope_changed(self, s, e):
        self._t2_load_spatial()
        self._t2_load_cats()
        self._t2_update_cards()

    def _t2_sel_all_spatial(self, s, e):
        for si in self.t2_spatial_items: si.is_selected = True
        self._t2_refresh_spatial_list()

    def _t2_sel_none_spatial(self, s, e):
        for si in self.t2_spatial_items: si.is_selected = False
        self._t2_refresh_spatial_list()

    def _t2_sel_invert_spatial(self, s, e):
        for si in self.t2_spatial_items: si.is_selected = not si.is_selected
        self._t2_refresh_spatial_list()

    def _t2_on_spatial_search(self, s, e):
        self._t2_refresh_spatial_list(s.Text)

    def _t2_sel_all_cats(self, s, e):
        for ci in self.t2_cat_items: ci.is_selected = True
        self._t2_refresh_cat_list()
        self._t2_update_source_params()

    def _t2_sel_none_cats(self, s, e):
        for ci in self.t2_cat_items: ci.is_selected = False
        self._t2_refresh_cat_list()
        self._t2_update_source_params()

    def _t2_on_cat_search(self, s, e):
        self._t2_refresh_cat_list(s.Text)

    def _t2_on_result_search(self, s, e):
        self._t2_refresh_results(s.Text)

    def _t2_sel_all_results(self, s, e):
        for r in self.t2_results: r.is_selected = True
        self._t2_refresh_results()

    def _t2_sel_none_results(self, s, e):
        for r in self.t2_results: r.is_selected = False
        self._t2_refresh_results()

    def _t2_on_collect(self, s, e):
        sel_spatial = [si for si in self.t2_spatial_items if si.is_selected]
        sel_cats = [ci for ci in self.t2_cat_items if ci.is_selected]
        if not sel_spatial:
            TaskDialog.Show("Room Data Collector", "Please select at least one spatial element.")
            return
        if not sel_cats:
            TaskDialog.Show("Room Data Collector", "Please select at least one element category.")
            return
            
        src_param = ""
        if self.tab2_cmb_source_param.SelectedItem:
            src_param = self.tab2_cmb_source_param.SelectedItem.Content
        elif self.tab2_cmb_source_param.Text:
            src_param = self.tab2_cmb_source_param.Text
            
        agg_idx = self.tab2_cmb_agg_type.SelectedIndex
        agg_type = ALL_AGG[agg_idx] if agg_idx >= 0 else AGG_COUNT
        
        all_elements = []
        vo = bool(self.tab2_cb_view_only.IsChecked)
        for ci in sel_cats:
            try:
                bic = System.Enum.ToObject(BuiltInCategory, ci.cat_id)
                col = FilteredElementCollector(doc)
                if vo: col = FilteredElementCollector(doc, doc.ActiveView.Id)
                col = col.OfCategory(bic).WhereElementIsNotElementType().ToElements()
                for elem in col:
                    all_elements.append(elem)
            except: pass
            
        self.t2_results = []
        self.t2_all_results = []
        total_found = 0
        
        for si in sel_spatial:
            contained = []
            for elem in all_elements:
                found = False
                if si.spatial_type == ROOMS:
                    found = check_element_in_room(elem, si.element, boundary_ids=si.boundary_ids)
                elif si.spatial_type == AREAS:
                    found = check_element_in_area(elem, si.element, si.polygons)
                elif si.spatial_type == SPACES:
                    found = check_element_in_space(elem, si.element)
                if found:
                    contained.append(elem)
            si.contained_elements = contained
            result = Tab2CollectResult(si, contained, src_param, agg_type)
            self.t2_results.append(result)
            self.t2_all_results.append(result)
            total_found += len(contained)
            
        # Update source param
        found_elems = []
        for r in self.t2_results:
            found_elems.extend(r.elements)
        if found_elems:
            self.t2_elem_params = get_element_params(found_elems)
            self.tab2_cmb_source_param.Items.Clear()
            for pn in self.t2_elem_params:
                item = ComboBoxItem()
                item.Content = pn
                self.tab2_cmb_source_param.Items.Add(item)
            if src_param:
                self.tab2_cmb_source_param.Text = src_param
                
        self._t2_refresh_results()
        self._t2_update_cards()
        TaskDialog.Show("Room Data Collector", "Collected data for " + str(len(sel_spatial)) + " spatial elements.\nTotal elements found: " + str(total_found))

    def _t2_refresh_results(self, filter_text=""):
        self.tab2_result_panel.Children.Clear()
        ft = filter_text.lower() if filter_text else ""
        
        # Header Row
        header = self._t2_make_result_header()
        self.tab2_result_panel.Children.Add(header)
        
        visible = 0
        for r in self.t2_results:
            display = r.spatial.display_name
            if ft and ft not in display.lower() and ft not in r.agg_value.lower():
                continue
            row = self._t2_make_result_row(r)
            self.tab2_result_panel.Children.Add(row)
            visible += 1
        self.tab2_lbl_info.Text = str(visible) + " of " + str(len(self.t2_results)) + " results"

    def _t2_make_result_header(self):
        bd = Border()
        bd.Background = brush(PRIMARY)
        bd.Padding = Thickness(4, 6, 4, 6)

        sp = StackPanel()
        sp.Orientation = Orientation.Horizontal

        # Header checkbox acts as select all/none
        cb_all = CheckBox()
        cb_all.Width = 28
        cb_all.Margin = Thickness(4, 0, 0, 0)
        cb_all.Checked += self._t2_sel_all_results
        cb_all.Unchecked += self._t2_sel_none_results
        sp.Children.Add(cb_all)

        cols = [("Spatial Element", 200), ("Level", 100), ("Elements", 70),
                ("Source Param", 150), ("Agg. Method", 110), ("Result Value", 200)]
        for label, w in cols:
            t = TextBlock()
            t.Text = label
            t.Width = w
            t.FontSize = 12
            t.FontWeight = FontWeights.SemiBold
            t.Foreground = brush(WHITE)
            t.Margin = Thickness(4, 0, 0, 0)
            sp.Children.Add(t)

        bd.Child = sp
        return bd

    def _t2_make_result_row(self, result):
        bd = Border()
        bd.Padding = Thickness(4)
        bd.BorderBrush = brush(BORDER)
        bd.BorderThickness = Thickness(0, 0, 0, 1)
        
        sp = StackPanel()
        sp.Orientation = Orientation.Horizontal
        
        cb = CheckBox()
        cb.Width = 28
        cb.Margin = Thickness(4, 0, 0, 0)
        cb.IsChecked = result.is_selected
        cb.Tag = result
        cb.Checked += self._t2_on_result_row_check
        cb.Unchecked += self._t2_on_result_row_check
        sp.Children.Add(cb)
        
        cols = [
            (result.spatial.display_name, 200),
            (result.spatial.level or "N/A", 100),
            (str(result.element_count), 70),
            (result.param_name or "Count Only", 150),
            (result.agg_type, 110),
            (result.agg_value, 200)
        ]
        for val, w in cols:
            t = TextBlock()
            t.Text = val
            t.Width = w
            t.FontSize = 12
            t.Foreground = brush(TEXT_DARK)
            t.Margin = Thickness(4, 0, 0, 0)
            sp.Children.Add(t)
            
        bd.Child = sp
        return bd

    def _t2_on_result_row_check(self, s, e):
        s.Tag.is_selected = bool(s.IsChecked)

    def _t2_on_apply(self, s, e):
        sel_res = [r for r in self.t2_results if r.is_selected]
        if not sel_res:
            TaskDialog.Show("Apply Data", "No results selected.")
            return
            
        tgt_param = self.tab2_cmb_target_param.Text
        if not tgt_param:
            TaskDialog.Show("Apply Data", "Please specify a target parameter.")
            return
            
        t = Transaction(doc, "Apply Collected Data to Rooms")
        t.Start()
        try:
            ok, fail = 0, 0
            for r in sel_res:
                try:
                    p = r.spatial.element.LookupParameter(tgt_param)
                    if p and not p.IsReadOnly:
                        p.Set(r.agg_value)
                        ok += 1
                    else: fail += 1
                except: fail += 1
            t.Commit()
            TaskDialog.Show("Apply Data", "Updated: " + str(ok) + "\nFailed: " + str(fail))
        except Exception as ex:
            t.RollBack()
            TaskDialog.Show("Error", str(ex))

    def _t2_on_select(self, s, e):
        sel_res = [r for r in self.t2_results if r.is_selected]
        if not sel_res:
            TaskDialog.Show("Select Elements", "No results selected.")
            return
        ids = List[ElementId]()
        for r in sel_res:
            for elem in r.elements:
                ids.Add(elem.Id)
        uidoc.Selection.SetElementIds(ids)
        TaskDialog.Show("Select Elements", "Selected " + str(ids.Count) + " element(s) in active view/model.")


def main():
    try:
        win = ManaContainsWindow()
        win.ShowDialog()
    except Exception as ex:
        TaskDialog.Show("Error", str(ex))

if __name__ == "__main__":
    main()
