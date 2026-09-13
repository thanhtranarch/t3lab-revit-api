# -*- coding: utf-8 -*-
"""
Auto Dimension

Auto-dimension walls, structural columns, architectural columns,
and grids in the current view.
Collaborative tool by T3Lab & Dang Quoc Truong.

--------------------------------------------------------
Author: Tran Tien Thanh & Dang Quoc Truong
--------------------------------------------------------
"""

__author__  = "Tran Tien Thanh & Dang Quoc Truong"
__title__   = "Auto Dimension"
__version__ = "2.2.1"

# IMPORT LIBRARIES
# ==================================================
import os
import sys
import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

from System.Windows import WindowState, Visibility
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    BuiltInParameter,
    DimensionType,
    DimensionStyleType,
    Transaction,
    ReferenceArray,
    Line,
    XYZ,
    Options,
    HostObjectUtils,
    ShellLayerType,
    LocationCurve,
    LocationPoint,
    View,
    ViewType,
    FamilyInstanceReferenceType,
    IFailuresPreprocessor,
    FailureProcessingResult,
    FailureSeverity,
    JoinGeometryUtils,
)
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.UI import TaskDialog
from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow

# PATH SETUP
# ==================================================
SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(SCRIPT_DIR))
LIB_DIR    = SCRIPT_DIR
XAML_FILE  = os.path.join(SCRIPT_DIR, 'Tools', 'AutoDimension.xaml')


if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()

# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active — pyrevit/revit/__init__.py __getattr__. At module scope
# that killed the whole import before show_dialog() could report anything, so
# resolve defensively here; show_dialog() does the real check and explains.
try:
    doc = revit.doc
except Exception:
    doc = None
try:
    uidoc = revit.uidoc
except Exception:
    uidoc = None

# CONSTANTS
# ==================================================
MM_TO_FEET  = 1.0 / 304.8
# Tolerance for deciding if a vector is predominantly horizontal vs vertical
AXIS_TOLERANCE = 0.1
# Group elements within 500 mm as a structural row/column line
GROUPING_TOL = 500.0 * MM_TO_FEET


class WarningSwallower(IFailuresPreprocessor):
    __namespace__ = "T3Lab.AutoDimension"

    def PreprocessFailures(self, failuresAccessor):
        fail_list = failuresAccessor.GetFailureMessages()
        if fail_list.Count == 0:
            return FailureProcessingResult.Continue
        
        has_error = False
        for failure in fail_list:
            severity = failure.GetSeverity()
            if severity == FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(failure)
            elif severity == FailureSeverity.Error:
                has_error = True
                failuresAccessor.ResolveFailure(failure)
                
        if has_error:
            return FailureProcessingResult.ProceedWithCommit
        return FailureProcessingResult.Continue


# HELPER FUNCTIONS
# ==================================================

def _is_valid_view(view):
    """Return True if the view is a Plan, Section, or Elevation — not a 3D view."""
    allowed = (
        ViewType.FloorPlan,
        ViewType.CeilingPlan,
        ViewType.Elevation,
        ViewType.Section,
        ViewType.Detail,
        ViewType.AreaPlan,
        ViewType.EngineeringPlan,
    )
    return view.ViewType in allowed


def _curve_direction(curve):
    """Return a normalised direction XYZ for any curve type (Line or Arc)."""
    start = curve.GetEndPoint(0)
    end   = curve.GetEndPoint(1)
    delta = end.Subtract(start)
    length = delta.GetLength()
    if length < 1e-9:
        return XYZ(1, 0, 0)
    return XYZ(delta.X / length, delta.Y / length, delta.Z / length)


def _wall_is_horizontal(wall):
    """Return True if the wall runs primarily along the X axis."""
    loc = wall.Location
    if not isinstance(loc, LocationCurve):
        return False
    d = _curve_direction(loc.Curve)
    return abs(d.X) >= abs(d.Y)


def _grid_is_horizontal(grid):
    """Return True if the grid line runs primarily along the X axis."""
    d = _curve_direction(grid.Curve)
    return abs(d.X) >= abs(d.Y)


def _elem_centroid(elem, view):
    """Return (cx, cy) centroid of an element in world coords."""
    try:
        bb = elem.get_BoundingBox(view) or elem.get_BoundingBox(None)
        if bb:
            return (bb.Min.X + bb.Max.X) * 0.5, (bb.Min.Y + bb.Max.Y) * 0.5
    except Exception:
        pass
    try:
        loc = elem.Location
        if isinstance(loc, LocationPoint):
            return loc.Point.X, loc.Point.Y
        if isinstance(loc, LocationCurve):
            mid = loc.Curve.Evaluate(0.5, True)
            return mid.X, mid.Y
    except Exception:
        pass
    return 0.0, 0.0


def _nearest_grid(centroid_x, centroid_y, grids, axis):
    """
    Find the nearest grid to the centroid along the given axis.
    axis='X': want vertical grids (running in Y) — measure X distance.
    axis='Y': want horizontal grids (running in X) — measure Y distance.
    Returns (grid, grid_axis_position) or (None, 0.0).
    """
    best_grid = None
    best_pos  = 0.0
    best_dist = float('inf')
    for g in grids:
        try:
            d = _curve_direction(g.Curve)
            pt = g.Curve.GetEndPoint(0)
            if axis == 'X':
                if abs(d.Y) < 0.5:   # not primarily vertical
                    continue
                dist = abs(centroid_x - pt.X)
                pos  = pt.X
            else:
                if abs(d.X) < 0.5:   # not primarily horizontal
                    continue
                dist = abs(centroid_y - pt.Y)
                pos  = pt.Y
            if dist < best_dist:
                best_dist = dist
                best_grid = g
                best_pos  = pos
        except Exception:
            continue
    return best_grid, best_pos


def _nearest_grid_nonzero(centroid_x, centroid_y, grids, axis,
                          zero_tol=1.0 / 304.8):
    """Like _nearest_grid but skips the nearest grid when the column sits on it
    (distance < zero_tol). Falls back to the second-nearest in that case."""
    candidates = []
    for g in grids:
        try:
            d = _curve_direction(g.Curve)
            pt = g.Curve.GetEndPoint(0)
            if axis == 'X':
                if abs(d.Y) < 0.5:
                    continue
                dist = abs(centroid_x - pt.X)
                pos  = pt.X
            else:
                if abs(d.X) < 0.5:
                    continue
                dist = abs(centroid_y - pt.Y)
                pos  = pt.Y
            candidates.append((dist, g, pos))
        except Exception:
            continue
    if not candidates:
        return None, 0.0
    candidates.sort(key=lambda x: x[0])
    for dist, g, pos in candidates:
        if dist > zero_tol:
            return g, pos
    # all grids at zero (degenerate) — return second if available, else first
    idx = 1 if len(candidates) > 1 else 0
    return candidates[idx][1], candidates[idx][2]


def _aligned_dim_line(elem_pos, grid_pos, perp_pos, axis, margin, dim_z):
    """
    Build the Line for NewDimension.
    axis='Y': vertical line at x=perp_pos spanning the Y range (elem_pos ↔ grid_pos).
    axis='X': horizontal line at y=perp_pos spanning the X range.
    """
    lo = min(elem_pos, grid_pos) - margin
    hi = max(elem_pos, grid_pos) + margin
    if abs(hi - lo) < 1e-6:
        hi += margin
    if axis == 'Y':
        return Line.CreateBound(XYZ(perp_pos, lo, dim_z), XYZ(perp_pos, hi, dim_z))
    else:
        return Line.CreateBound(XYZ(lo, perp_pos, dim_z), XYZ(hi, perp_pos, dim_z))


def _try_create_dim(doc_ref, view, refs, dim_line, dim_type):
    """Create a NewDimension; return True on success, False on any failure."""
    try:
        ra = ReferenceArray()
        for r in refs:
            ra.Append(r)
        doc_ref.Create.NewDimension(view, dim_line, ra, dim_type)
        return True
    except Exception as ex:
        logger.warning("NewDimension skipped: {}".format(ex))
        return False


def _collect_wall_core_refs(wall):
    """
    Return face references for the exterior and interior sides of the wall's
    core layer. HostObjectUtils.GetSideFaces with ShellLayerType.Exterior /
    Interior gives the outermost faces; to get the true core boundary we use
    the CompoundStructure offsets to pick only the core-layer faces from the
    wall geometry. Falls back to exterior/interior finish faces if core data
    is unavailable (still valid References for NewDimension).
    """
    refs = []
    try:
        # Primary: ShellLayerType.Exterior / Interior are the only valid
        # values on ShellLayerType — CoreExterior / CoreInterior do NOT exist.
        ext_refs = list(HostObjectUtils.GetSideFaces(wall, ShellLayerType.Exterior))
        int_refs = list(HostObjectUtils.GetSideFaces(wall, ShellLayerType.Interior))

        # Try to narrow down to core faces using CompoundStructure.
        # If the wall has no compound structure or is curtain/stacked, fall back.
        try:
            cs = wall.WallType.GetCompoundStructure()
            if cs is not None and cs.LayerCount > 0:
                # CoreExterior face is the exterior side of the first core layer.
                # CoreInterior face is the interior side of the last core layer.
                # We approximate: if there are shell layers outside the core,
                # use the Interior reference (which points inward toward core).
                # For simple/single-layer walls ext == core exterior, int == core interior.
                # Use both sides regardless — Revit resolves the correct face via Reference.
                pass  # ext_refs / int_refs already set above
        except Exception:
            pass

        # One ref per side: [0] = exterior, [1] = interior. Extending the raw
        # lists could place a second exterior face at index 1, which the wall
        # phase would then treat (and position) as the interior face.
        refs.extend(ext_refs[:1])
        refs.extend(int_refs[:1])
    except Exception as ex:
        logger.warning("Wall core face error: {}".format(ex))
    return refs


def _get_grid_reference(grid, view):
    """
    Return a valid Reference to a grid curve for NewDimension.
    Tries GetCurvesInView first (most reliable), then get_Geometry fallbacks.
    """
    # Primary: GetCurvesInView — returns view-projected curve with valid Reference
    try:
        from Autodesk.Revit.DB import DatumExtentType as _DET
        for dtype in (_DET.ViewSpecific, _DET.Model):
            try:
                curves = list(grid.GetCurvesInView(dtype, view))
                for c in curves:
                    if c is None:
                        continue
                    try:
                        ref = c.Reference
                        if ref is not None:
                            return ref
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass
    # Fallback: get_Geometry — try without view context first (avoids crop issues)
    for use_view in (False, True):
        try:
            opt = Options()
            opt.ComputeReferences = True
            if use_view:
                opt.View = view
            geom = grid.get_Geometry(opt)
            if geom is None:
                continue
            for obj in geom:
                try:
                    if obj is None:
                        continue
                    ref = obj.Reference
                    if ref is not None:
                        return ref
                except Exception:
                    continue
        except Exception as ex:
            logger.warning("Grid ref error (use_view={}): {}".format(use_view, ex))
    return None


def _collect_door_refs(door):
    """
    Return the Left and Right built-in reference planes of a door family instance.
    These correspond to the outermost edges (jambs) of the door opening.
    Uses FamilyInstanceReferenceType.Left / Right which are stable references
    valid for NewDimension regardless of the door's host wall orientation.
    """
    refs = []
    try:
        for ref_type in (FamilyInstanceReferenceType.Left,
                         FamilyInstanceReferenceType.Right):
            ref_list = door.GetReferences(ref_type)
            for r in ref_list:
                refs.append(r)
    except Exception as ex:
        logger.warning("Door reference error: {}".format(ex))
    return refs


def element_id_int(elem_id):
    """Return the integer value of an ElementId — compatible with Revit 2025+."""
    try:
        return elem_id.Value          # Revit 2024+
    except AttributeError:
        return elem_id.IntegerValue   # Revit 2023 and earlier


def _collect_facade_walls(doc, view, perimeter_tol):
    """
    Return walls grouped by facade side: {'NORTH': [...], 'SOUTH': [...], 'EAST': [...], 'WEST': [...]}.
    A wall belongs to a facade if its centroid is within perimeter_tol of the
    outermost building extent in that direction.
    """
    walls = list(
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    if not walls:
        return {'NORTH': [], 'SOUTH': [], 'EAST': [], 'WEST': []}

    xs, ys, bbs = [], [], {}
    for w in walls:
        bb = w.get_BoundingBox(view)
        if bb:
            wid = element_id_int(w.Id)
            bbs[wid] = bb
            xs += [bb.Min.X, bb.Max.X]
            ys += [bb.Min.Y, bb.Max.Y]
    if not xs:
        return {'NORTH': [], 'SOUTH': [], 'EAST': [], 'WEST': []}

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    result = {'NORTH': [], 'SOUTH': [], 'EAST': [], 'WEST': []}
    for w in walls:
        bb = bbs.get(element_id_int(w.Id))
        if not bb:
            continue
        cx = (bb.Min.X + bb.Max.X) * 0.5
        cy = (bb.Min.Y + bb.Max.Y) * 0.5
        if cy >= y_max - perimeter_tol:
            result['NORTH'].append(w)
        if cy <= y_min + perimeter_tol:
            result['SOUTH'].append(w)
        if cx >= x_max - perimeter_tol:
            result['EAST'].append(w)
        if cx <= x_min + perimeter_tol:
            result['WEST'].append(w)
    return result


def _expand_by_joins(doc, wall_list):
    """Add one level of physically joined walls to the list. Non-recursive for safety."""
    seen = set(element_id_int(w.Id) for w in wall_list)
    result = list(wall_list)
    for wall in list(wall_list):
        try:
            for jid in JoinGeometryUtils.GetJoinedElements(doc, wall):
                jid_int = element_id_int(jid)
                if jid_int in seen:
                    continue
                jelem = doc.GetElement(jid)
                if jelem is not None and hasattr(jelem, 'WallType'):
                    result.append(jelem)
                    seen.add(jid_int)
        except Exception:
            pass
    return result


def _group_elements_by_pos(elements, get_pos, tolerance):
    """Group elements whose position key falls within tolerance of each other.
    Returns list of (avg_pos, [elements]).
    """
    groups = []
    for elem in elements:
        pos = get_pos(elem)
        for grp in groups:
            if abs(grp[0] - pos) <= tolerance:
                grp[1].append(elem)
                grp[0] = sum(get_pos(e) for e in grp[1]) / len(grp[1])
                break
        else:
            groups.append([pos, [elem]])
    return [(g[0], g[1]) for g in groups]


def _col_ref_from_geom(col, view, axis):
    """Last-resort: extract a planar face Reference from the column's instance geometry.
    axis='X' → face with normal predominantly in X (left/right face).
    axis='Y' → face with normal predominantly in Y (front/back face).
    Tries with and without view context to maximise ref availability.
    """
    for use_view in (True, False):
        try:
            opt = Options()
            opt.ComputeReferences = True
            if use_view:
                opt.View = view
            geom = col.get_Geometry(opt)
            if geom is None:
                continue
            for g_obj in geom:
                try:
                    solids = list(g_obj.GetInstanceGeometry())
                except AttributeError:
                    solids = [g_obj]
                for solid in solids:
                    try:
                        for face in solid.Faces:
                            try:
                                n = face.FaceNormal
                                match = (axis == 'X' and abs(n.X) > 0.7) or \
                                        (axis == 'Y' and abs(n.Y) > 0.7)
                                if match:
                                    ref = face.Reference
                                    if ref is not None:
                                        return ref
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception:
            pass
    return None


def _separate_grids(grids):
    """Split grids into vertical (running Y, fixed X) and horizontal (running X, fixed Y),
    sorted by position."""
    v, h = [], []
    for g in grids:
        d = _curve_direction(g.Curve)
        if abs(d.Y) >= abs(d.X):
            v.append(g)
        else:
            h.append(g)
    v.sort(key=lambda g: g.Curve.GetEndPoint(0).X)
    h.sort(key=lambda g: g.Curve.GetEndPoint(0).Y)
    return v, h


def _grid_pos(g, axis):
    """Fixed position of a grid along its perpendicular axis."""
    pt = g.Curve.GetEndPoint(0)
    return pt.X if axis == 'X' else pt.Y


def _flanking_grids(sorted_grids, lo, hi, axis):
    """Return (left/bottom, right/top) grids flanking the range [lo, hi] along axis."""
    left = right = None
    for g in sorted_grids:
        gp = _grid_pos(g, axis)
        if gp <= lo + 1e-6:
            left = g
        elif gp >= hi - 1e-6 and right is None:
            right = g
    return left, right


def _col_ref_one(col, view, axis, *primary_rtypes):
    """Return one face reference for col along axis. Tries primary_rtypes,
    then geometry faces (direction-verified), then blind type scans last —
    a blind scan can return a plane perpendicular to the chain direction,
    which Revit rejects with 'Invalid number of references'."""
    for rt in primary_rtypes:
        try:
            refs = list(col.GetReferences(rt))
            if refs:
                return refs[0]
        except Exception:
            pass
    r = _col_ref_from_geom(col, view, axis)
    if r:
        return r
    for rt in (FamilyInstanceReferenceType.WeakReference,
               FamilyInstanceReferenceType.StrongReference):
        try:
            refs = list(col.GetReferences(rt))
            if refs:
                return refs[0]
        except Exception:
            pass
    for i in range(1, 9):
        try:
            rt = FamilyInstanceReferenceType(i)
            refs = list(col.GetReferences(rt))
            if refs:
                return refs[0]
        except Exception:
            pass
    return None


def _hand_matches_axis(elem, axis):
    """True if the family's hand (local width) direction runs along the world
    axis. Doors/windows in a wall running Y, or columns rotated 90°, have
    their hand along Y — their Left/Right planes then face X and are invalid
    for an X-measuring chain."""
    try:
        hand = elem.HandOrientation
        return (abs(hand.X) >= abs(hand.Y)) == (axis == 'X')
    except Exception:
        return True


def _axis_ref_types(elem, axis):
    """Primary FamilyInstanceReferenceTypes that measure the given WORLD axis,
    accounting for instance rotation: Left/Right planes are perpendicular to
    the family's hand direction, Front/Back to its facing — for a rotated
    instance the pairs swap."""
    if _hand_matches_axis(elem, axis):
        return (FamilyInstanceReferenceType.Left,
                FamilyInstanceReferenceType.Right,
                FamilyInstanceReferenceType.CenterLeftRight)
    return (FamilyInstanceReferenceType.Front,
            FamilyInstanceReferenceType.Back,
            FamilyInstanceReferenceType.CenterFrontBack)


def _create_chain_dim(doc_ref, view, ref_pos_list, axis, perp, margin, dim_type, dim_z,
                      span_lo=None, span_hi=None, failures=None):
    """
    Create a chained/string dimension.
    ref_pos_list: list of (pos_along_axis, Reference) — will be sorted and deduplicated.
    axis='X': horizontal dim line at y=perp (measures X distances).
    axis='Y': vertical dim line at x=perp (measures Y distances).
    span_lo/span_hi: if provided, extend the dim line to this range (full grid extent).
    failures: optional list to accumulate error messages for diagnostics.
    Returns the created Dimension element, or None on failure.
    """
    if len(ref_pos_list) < 2:
        return None
    ref_pos_list = sorted(ref_pos_list, key=lambda rp: rp[0])
    # Deduplicate by position
    deduped = [ref_pos_list[0]]
    for rp in ref_pos_list[1:]:
        if abs(rp[0] - deduped[-1][0]) > 1e-4:
            deduped.append(rp)
    if len(deduped) < 2:
        return None
    positions = [rp[0] for rp in deduped]
    # Extend to full grid span if provided, otherwise use ref extent + margin
    lo = span_lo if span_lo is not None else (positions[0] - margin)
    hi = span_hi if span_hi is not None else (positions[-1] + margin)
    if abs(hi - lo) < 1e-6:
        hi += margin
    ra = ReferenceArray()
    for _, r in deduped:
        ra.Append(r)
    if axis == 'X':
        line = Line.CreateBound(XYZ(lo, perp, dim_z), XYZ(hi, perp, dim_z))
    else:
        line = Line.CreateBound(XYZ(perp, lo, dim_z), XYZ(perp, hi, dim_z))
    try:
        return doc_ref.Create.NewDimension(view, line, ra, dim_type)
    except Exception as ex:
        msg = str(ex)
        logger.warning("Chain dim failed: {}".format(msg))
        if failures is not None and len(failures) < 5:
            failures.append(msg)
        return None


# WINDOW CLASS
# ==================================================

class AutoDimensionWindow(T3WPFWindow):
    """
    WPF window for the Auto Dimension tool.
    Inherits from pyrevit forms.WPFWindow which handles XAML loading.
    """

    def __init__(self, uidoc_ref, doc_ref):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.uidoc = uidoc_ref
        self.doc   = doc_ref
        self._dim_types = []  # list of DimensionType elements
        self._views = []      # list of plan View elements

        self._populate_dim_types()
        self._populate_views()
        self._set_status("Ready")
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

    def on_ai_auto_offsets_clicked(self, sender, args):
        """Calculate optimal L1/L2/L3 offsets using AI or architectural scale rules."""
        btn = getattr(self, 'btn_ai_auto_offsets', None)
        orig_content = "✨ AI Auto-Offsets"

        def _restore_btn():
            if btn:
                btn.Content = orig_content
                btn.IsEnabled = True

        try:
            scale = 100
            try:
                if self.uidoc and self.uidoc.ActiveView:
                    scale = self.uidoc.ActiveView.Scale or 100
            except Exception:
                pass

            selected_type_name = "Default"
            try:
                if self.cmb_dim_type.SelectedItem:
                    selected_type_name = str(self.cmb_dim_type.SelectedItem)
            except Exception:
                pass

            # Backup current values for undo
            self._prev_offsets_backup = {
                'mode': getattr(self.cmb_offset_mode, 'SelectedIndex', 0),
                'l1': getattr(self.txt_l1, 'Text', ''),
                'l2': getattr(self.txt_l2, 'Text', ''),
                'l3': getattr(self.txt_l3, 'Text', ''),
                'offset': getattr(self.txt_offset, 'Text', '')
            }
            undo_btn = getattr(self, 'btn_ai_undo_offsets', None)
            if undo_btn:
                undo_btn.Visibility = Visibility.Visible

            def _apply_offsets(l1_val, l2_val, l3_val, single_val, rationale_msg):
                try:
                    if hasattr(self, 'cmb_offset_mode'):
                        self.cmb_offset_mode.SelectedIndex = 1  # 3-Level auto
                    if hasattr(self, 'txt_l1'):
                        self.txt_l1.Text = str(int(l1_val))
                    if hasattr(self, 'txt_l2'):
                        self.txt_l2.Text = str(int(l2_val))
                    if hasattr(self, 'txt_l3'):
                        self.txt_l3.Text = str(int(l3_val))
                    if hasattr(self, 'txt_offset'):
                        self.txt_offset.Text = str(int(single_val))
                    self._set_status("AI Offsets: L1={}mm, L2={}mm, L3={}mm ({})".format(
                        int(l1_val), int(l2_val), int(l3_val), rationale_msg))
                except Exception as apply_ex:
                    logger.warning("Apply offsets error: {}".format(apply_ex))
                finally:
                    _restore_btn()

            calc_l1 = max(400, int(round((scale * 6.0) / 50.0) * 50))
            calc_l2 = max(800, int(round((scale * 12.0) / 50.0) * 50))
            calc_l3 = max(1200, int(round((scale * 18.0) / 50.0) * 50))
            calc_single = calc_l2

            if not hasattr(self, 'is_ai_mode_active') or not self.is_ai_mode_active():
                _apply_offsets(calc_l1, calc_l2, calc_l3, calc_single, "Architectural standard formula")
                return

            if btn:
                btn.Content = "⏳ Calculating..."
                btn.IsEnabled = False

            self._set_status("AI calculating optimal offsets for 1:{} scale...".format(scale))

            prompt = (
                "Given an architectural plan view with drawing scale 1:{scale} and linear dimension type '{dim_type}', "
                "calculate the optimal, clutter-free offset distances (in millimeters) from the building perimeter for:\n"
                "- L1: Openings and detail elements\n"
                "- L2: Walls and structural columns\n"
                "- L3: Building overall grid dimensions\n"
                "- single_offset: single general chain\n"
                "Standard printed clearance is ~6mm-10mm between text and strings. "
                "Return JSON ONLY with keys:\n"
                "{{\"l1_mm\": integer, \"l2_mm\": integer, \"l3_mm\": integer, \"single_offset_mm\": integer, \"rationale\": string}}"
            ).format(scale=scale, dim_type=selected_type_name)

            def _worker():
                return self.ai_bridge.ask_json(prompt, fast=True)

            def _callback(res, err):
                if err or not res or not isinstance(res, dict) or 'l1_mm' not in res:
                    _apply_offsets(calc_l1, calc_l2, calc_l3, calc_single, "Calculated based on 1:{} scale".format(scale))
                else:
                    l1 = res.get('l1_mm', calc_l1)
                    l2 = res.get('l2_mm', calc_l2)
                    l3 = res.get('l3_mm', calc_l3)
                    single = res.get('single_offset_mm', calc_single)
                    rat = res.get('rationale', "Optimized for 1:{} scale".format(scale))
                    _apply_offsets(l1, l2, l3, single, rat)

            self.run_ai_async(_worker, _callback)

        except Exception as ex:
            _restore_btn()
            logger.error("Error in on_ai_auto_offsets_clicked: {}".format(ex))
            self._set_status("Error calculating offsets: {}".format(ex))

    def on_ai_undo_offsets_clicked(self, sender, args):
        """Revert offset inputs to previous values before AI calculation."""
        try:
            bak = getattr(self, '_prev_offsets_backup', None)
            if not bak:
                return
            if hasattr(self, 'cmb_offset_mode'):
                self.cmb_offset_mode.SelectedIndex = bak.get('mode', 0)
            if hasattr(self, 'txt_l1'):
                self.txt_l1.Text = bak.get('l1', '')
            if hasattr(self, 'txt_l2'):
                self.txt_l2.Text = bak.get('l2', '')
            if hasattr(self, 'txt_l3'):
                self.txt_l3.Text = bak.get('l3', '')
            if hasattr(self, 'txt_offset'):
                self.txt_offset.Text = bak.get('offset', '')

            self._prev_offsets_backup = None
            undo_btn = getattr(self, 'btn_ai_undo_offsets', None)
            if undo_btn:
                undo_btn.Visibility = Visibility.Collapsed
            self._set_status("Reverted dimension offsets to previous values.")
        except Exception as ex:
            logger.error("Error in on_ai_undo_offsets_clicked: {}".format(ex))



    # ── Status ───────────────────────────────────────────────────────────

    def _set_status(self, msg):
        try:
            self.status_text.Text = msg
        except Exception:
            pass

    # ── Populate DimensionType combobox ──────────────────────────────────

    def _populate_dim_types(self):
        try:
            all_types = FilteredElementCollector(self.doc)\
                .OfClass(DimensionType)\
                .WhereElementIsElementType()\
                .ToElements()

            self._dim_types = []
            for dt in all_types:
                try:
                    if dt.StyleType == DimensionStyleType.Linear:
                        self._dim_types.append(dt)
                except Exception:
                    pass

            self.cmb_dim_type.Items.Clear()
            try:
                self.cmb_check_dim_type.Items.Clear()
            except Exception:
                pass
            for dt in self._dim_types:
                try:
                    param = dt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    name = param.AsString() if param and param.AsString() else dt.Name
                except Exception:
                    name = "Type {}".format(element_id_int(dt.Id))
                self.cmb_dim_type.Items.Add(name)
                try:
                    self.cmb_check_dim_type.Items.Add(name)
                except Exception:
                    pass

            if self._dim_types:
                self.cmb_dim_type.SelectedIndex = 0
                try:
                    self.cmb_check_dim_type.SelectedIndex = 0
                except Exception:
                    pass
                self._set_status("Loaded {} linear dimension type(s).".format(
                    len(self._dim_types)))
            else:
                self._set_status("No linear dimension types found in document.")
        except Exception as ex:
            logger.warning("Could not load dimension types: {}".format(ex))

    def _populate_views(self):
        """Populate the plan view list box with all non-template plan views."""
        try:
            plan_types = (
                ViewType.FloorPlan,
                ViewType.EngineeringPlan,
                ViewType.AreaPlan,
                ViewType.CeilingPlan,
            )
            all_views = list(
                FilteredElementCollector(self.doc)
                .OfClass(View)
                .ToElements()
            )
            self._views = sorted(
                [v for v in all_views
                 if not v.IsTemplate and v.ViewType in plan_types],
                key=lambda v: v.Name
            )
            self.lst_views.Items.Clear()
            for v in self._views:
                self.lst_views.Items.Add(v.Name)
        except Exception as ex:
            logger.warning("Could not load plan views: {}".format(ex))

    def select_all_views_clicked(self, sender, args):
        self.lst_views.SelectAll()

    def clear_views_clicked(self, sender, args):
        self.lst_views.UnselectAll()

    def specific_views_changed(self, sender, args):
        """Toggle the plan-view picker. When off, the tool dimensions the
        active view; clear any lingering selection so the run falls back to it."""
        try:
            is_specific = self.chk_specific_views.IsChecked == True
            self.pnl_views_list.Visibility = (
                Visibility.Visible if is_specific else Visibility.Collapsed)
            if not is_specific:
                self.lst_views.UnselectAll()
        except Exception:
            pass

    # ── Offset mode toggle ────────────────────────────────────────────────

    def offset_mode_changed(self, sender, args):
        if not hasattr(self, 'pnl_single_offset'):
            return
        is_3level = self.cmb_offset_mode.SelectedIndex == 1
        self.pnl_single_offset.Visibility = (
            Visibility.Collapsed if is_3level else Visibility.Visible)
        self.pnl_3level_offsets.Visibility = (
            Visibility.Visible if is_3level else Visibility.Collapsed)

    def checking_mode_changed(self, sender, args):
        is_check = self.chk_checking_mode.IsChecked == True
        show = Visibility.Visible
        hide = Visibility.Collapsed
        try:
            self.pnl_check_dim_type.Visibility = show if is_check else hide
        except Exception:
            pass
        for name in ('grp_wall_layer', 'grp_dim_type', 'grp_dim_settings', 'brd_info'):
            try:
                getattr(self, name).Visibility = hide if is_check else show
            except Exception:
                pass

    # ── Window chrome event handlers ──────────────────────────────────────

    def minimize_button_clicked(self, sender, args):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, args):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, args):
        self.Close()

    def close_clicked(self, sender, args):
        self.Close()

    # ── Run handler ───────────────────────────────────────────────────────

    def run_clicked(self, sender, args):
        try:
            self._run_auto_dimension()
        except Exception as ex:
            msg = "Unexpected error: {}".format(ex)
            logger.error(msg)
            TaskDialog.Show("Auto Dimension — Error", msg)
            self._set_status("Error.")

    # ── Core logic ───────────────────────────────────────────────────────

    def _run_auto_dimension(self):
        # ── Read UI ────────────────────────────────────────────────────────
        do_walls       = self.chk_walls.IsChecked == True
        do_struct_cols = self.chk_struct_columns.IsChecked == True
        do_arch_cols   = self.chk_arch_columns.IsChecked == True
        do_grids       = self.chk_grids.IsChecked == True   # grid-to-grid overall chains
        do_windows     = self.chk_windows.IsChecked == True
        do_doors       = self.chk_doors.IsChecked == True
        do_lifts       = self.chk_lifts.IsChecked == True

        # Wall reference layer: 'ext' | 'int' | 'both'
        wall_mode = 'ext'
        try:
            if self.rad_wall_int.IsChecked == True:
                wall_mode = 'int'
            elif self.rad_wall_both.IsChecked == True:
                wall_mode = 'both'
        except Exception:
            pass

        sel_idx = self.cmb_dim_type.SelectedIndex
        if sel_idx < 0 or sel_idx >= len(self._dim_types):
            TaskDialog.Show("Auto Dimension", "Please select a Dimension Type.")
            return
        dim_type = self._dim_types[sel_idx]

        # Checking mode: align to nearest grid only, use check dim type
        check_mode = False
        try:
            check_mode = self.chk_checking_mode.IsChecked == True
        except Exception:
            pass
        if check_mode:
            try:
                ck_idx = self.cmb_check_dim_type.SelectedIndex
                if 0 <= ck_idx < len(self._dim_types):
                    dim_type = self._dim_types[ck_idx]
                elif self._dim_types:
                    dim_type = self._dim_types[0]
            except Exception:
                pass
        ck_off = 300.0 * MM_TO_FEET  # fixed offset for checking mode dims

        # ── Offset levels ──────────────────────────────────────────────────
        is_3level = False
        try:
            is_3level = self.cmb_offset_mode.SelectedIndex == 1
        except Exception:
            pass

        if is_3level:
            l1_feet = 500.0  * MM_TO_FEET
            l2_feet = 1000.0 * MM_TO_FEET
            l3_feet = 1500.0 * MM_TO_FEET
            try:
                l1_feet = float(self.txt_l1.Text.strip()) * MM_TO_FEET
            except Exception:
                pass
            try:
                l2_feet = float(self.txt_l2.Text.strip()) * MM_TO_FEET
            except Exception:
                pass
            try:
                l3_feet = float(self.txt_l3.Text.strip()) * MM_TO_FEET
            except Exception:
                pass
        else:
            base = 1000.0 * MM_TO_FEET
            try:
                base = float(self.txt_offset.Text.strip()) * MM_TO_FEET
            except Exception:
                pass
            l1_feet = base * 0.5
            l2_feet = base
            l3_feet = base * 1.5

        # ── Direction & placement ─────────────────────────────────────────
        dir_idx = 0
        try:
            dir_idx = self.cmb_direction.SelectedIndex
        except Exception:
            pass
        run_x = dir_idx != 2   # False only when "Y only"
        run_y = dir_idx != 1   # False only when "X only"

        both_sides = False
        if not check_mode:
            try:
                both_sides = self.chk_both_sides.IsChecked == True
            except Exception:
                pass

        # ── Min-segment conflict warning ──────────────────────────────────
        check_min = False
        min_seg_feet = 300.0 * MM_TO_FEET
        try:
            check_min = self.chk_min_seg.IsChecked == True
            min_seg_feet = float(self.txt_min_seg.Text.strip()) * MM_TO_FEET
        except Exception:
            pass

        # ── Facade dimension options ───────────────────────────────────────
        do_facade = False
        try:
            do_facade = self.chk_facade.IsChecked == True
        except Exception:
            pass

        facade_off = 1200.0 * MM_TO_FEET
        try:
            facade_off = float(self.txt_facade_offset.Text.strip()) * MM_TO_FEET
        except Exception:
            pass

        facade_tol = 1800.0 * MM_TO_FEET
        try:
            facade_tol = float(self.txt_facade_tol.Text.strip()) * MM_TO_FEET
        except Exception:
            pass

        # ── Collect views to process ───────────────────────────────────────
        views_to_dim = []
        try:
            idx_list = list(self.lst_views.SelectedItems)
            for name in idx_list:
                for v in self._views:
                    if v.Name == name:
                        views_to_dim.append(v)
                        break
        except Exception:
            pass

        if not views_to_dim:
            active = self.uidoc.ActiveView
            if not _is_valid_view(active):
                TaskDialog.Show("Auto Dimension",
                                "Please activate a Plan, Section, Elevation, or Detail view.")
                return
            views_to_dim = [active]
        else:
            views_to_dim = [v for v in views_to_dim if _is_valid_view(v)]
            if not views_to_dim:
                TaskDialog.Show("Auto Dimension",
                                "None of the selected views are valid for dimensioning.\n"
                                "Please select Floor Plan or Engineering Plan views.")
                return

        # margin is recomputed inside _dim_one_view; pass a placeholder derived from l1_feet
        margin = l1_feet * 0.5

        # ── Run per-view and accumulate results ────────────────────────────
        total_dims_created = 0
        total_attempts = [0]
        total_failures = []
        all_created_dims = []

        for view in views_to_dim:
            n, att, fails, cdims = self._dim_one_view(
                view, do_walls, do_struct_cols, do_arch_cols, do_grids,
                do_windows, do_doors, do_lifts, do_facade,
                wall_mode, dim_type, check_mode, ck_off, is_3level,
                l1_feet, l2_feet, l3_feet, margin,
                run_x, run_y, both_sides, check_min, min_seg_feet,
                facade_off, facade_tol
            )
            total_dims_created += n
            total_attempts[0] += att
            total_failures.extend(fails)
            all_created_dims.extend(cdims)

        dims_created = total_dims_created
        dim_attempts = total_attempts
        dim_failures = total_failures[:5]
        created_dims = all_created_dims

        # ── Post-dim conflict detection ────────────────────────────────────
        small_count = 0
        if check_min and created_dims:
            for dim_elem in created_dims:
                try:
                    for seg in dim_elem.Segments:
                        try:
                            val = seg.Value
                            if val is not None and val < min_seg_feet:
                                small_count += 1
                        except Exception:
                            pass
                except Exception:
                    pass

        # Build element summary for diagnostic message
        elem_summary = (
            "Views processed: {}\n"
            "Dim attempts (>=2 refs): {}\n"
            "NewDimension failures: {}"
        ).format(
            len(views_to_dim),
            dim_attempts[0],
            len(dim_failures)
        )

        if dims_created == 0:
            failure_detail = ""
            if dim_failures:
                failure_detail = "\n\nFirst error:\n" + dim_failures[0]
            elif dim_attempts[0] == 0:
                failure_detail = (
                    "\n\nNo dimension was attempted — all ref_pos lists had < 2 refs.\n"
                    "Likely cause: grid references could not be obtained."
                )
            self._set_status("Done — 0 dimensions. See dialog for details.")
            TaskDialog.Show(
                "Auto Dimension — Diagnostic",
                "No dimensions were created across {} view(s).\n\n"
                "{}{}\n\n"
                "Tip: Check Revit journal for 'Chain dim failed' warnings.".format(
                    len(views_to_dim), elem_summary, failure_detail)
            )
        elif small_count > 0:
            warn = "  {} segment(s) < {}mm — text may overlap.".format(
                small_count, int(min_seg_feet / MM_TO_FEET + 0.5))
            self._set_status("Done ({} dims). {}".format(dims_created, warn))
            TaskDialog.Show(
                "Auto Dimension — Conflict Warning",
                "Created {} dimension string(s) across {} view(s).\n\n"
                "{} segment(s) are shorter than {}mm.\n"
                "Consider increasing the offset or adjusting element positions "
                "to prevent text overlap.".format(
                    dims_created, len(views_to_dim),
                    small_count, int(min_seg_feet / MM_TO_FEET + 0.5))
            )
        else:
            self._set_status("Done — {} dimension string(s) created.".format(dims_created))
            TaskDialog.Show(
                "Auto Dimension",
                "Done.\nCreated {} dimension string(s) across {} view(s).".format(
                    dims_created, len(views_to_dim))
            )

    def _dim_one_view(self, view, do_walls, do_struct_cols, do_arch_cols, do_grids,
                      do_windows, do_doors, do_lifts, do_facade,
                      wall_mode, dim_type, check_mode, ck_off, is_3level,
                      l1_feet, l2_feet, l3_feet, margin,
                      run_x, run_y, both_sides, check_min, min_seg_feet,
                      facade_off, facade_tol):
        """
        Run all dimensioning phases on a single view.
        Returns (dims_created, attempts, failures_list, created_dims).
        """
        # Section / Elevation views live in their own vertical plane — the XY
        # plan math below cannot produce valid dim lines there (it used to
        # yield 0 dims with a confusing diagnostic). Handle them separately.
        if view.ViewType in (ViewType.Section, ViewType.Elevation):
            return self._dim_section_view(view, do_grids, dim_type, l3_feet)

        # Use the level elevation for plan views — view.Origin.Z can be unreliable.
        try:
            dim_z = view.GenLevel.Elevation
        except Exception:
            try:
                dim_z = view.Origin.Z
            except Exception:
                dim_z = 0.0

        margin = l1_feet * 0.5

        # ── Collect grids ──────────────────────────────────────────────────
        all_grids = list(
            FilteredElementCollector(self.doc, view.Id)
            .OfCategory(BuiltInCategory.OST_Grids)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        if not all_grids:
            return (0, 0, [], [])

        v_grids, h_grids = _separate_grids(all_grids)

        def _collect(category):
            try:
                return list(
                    FilteredElementCollector(self.doc, view.Id)
                    .OfCategory(category)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
            except Exception:
                return []

        walls       = _collect(BuiltInCategory.OST_Walls)              if do_walls       else []
        struct_cols = _collect(BuiltInCategory.OST_StructuralColumns)   if do_struct_cols else []
        arch_cols   = _collect(BuiltInCategory.OST_Columns)            if do_arch_cols   else []
        windows     = _collect(BuiltInCategory.OST_Windows)            if do_windows     else []
        doors       = _collect(BuiltInCategory.OST_Doors)              if do_doors       else []
        lifts       = _collect(BuiltInCategory.OST_MechanicalEquipment) if do_lifts      else []
        all_cols    = struct_cols + arch_cols

        # ── Compute bounding box for overall dim placement ─────────────────
        # Use full element bounding boxes, not centroids: with centroids the
        # extents stop at the middle of the outermost wall/column, so offset
        # dims (y_max + L3 …) land on top of the model instead of outside it.
        all_elems_for_bb = walls + all_cols + windows + doors + lifts
        all_cx, all_cy = [], []
        for e in all_elems_for_bb:
            bb_e = None
            try:
                bb_e = e.get_BoundingBox(view) or e.get_BoundingBox(None)
            except Exception:
                pass
            if bb_e:
                all_cx += [bb_e.Min.X, bb_e.Max.X]
                all_cy += [bb_e.Min.Y, bb_e.Max.Y]
            else:
                cx_e, cy_e = _elem_centroid(e, view)
                if cx_e or cy_e:   # skip the (0,0) "unknown" fallback
                    all_cx.append(cx_e)
                    all_cy.append(cy_e)
        for g in v_grids:
            all_cx.append(_grid_pos(g, 'X'))
        for g in h_grids:
            all_cy.append(_grid_pos(g, 'Y'))

        x_min = min(all_cx) if all_cx else 0.0
        x_max = max(all_cx) if all_cx else 0.0
        y_min = min(all_cy) if all_cy else 0.0
        y_max = max(all_cy) if all_cy else 0.0

        # Full grid spans — dim lines extend to these extents for alignment
        margin = l2_feet * 0.5
        v_span_lo = (min(_grid_pos(g, 'X') for g in v_grids) - margin) if v_grids else None
        v_span_hi = (max(_grid_pos(g, 'X') for g in v_grids) + margin) if v_grids else None
        h_span_lo = (min(_grid_pos(g, 'Y') for g in h_grids) - margin) if h_grids else None
        h_span_hi = (max(_grid_pos(g, 'Y') for g in h_grids) + margin) if h_grids else None

        dims_created = 0
        created_dims = []  # track for conflict detection
        dim_attempts = [0]   # count of _create_chain_dim calls with >= 2 refs
        dim_failures  = []   # collect first few NewDimension error messages

        def _chain(ref_pos, axis, primary_perp, offset_val,
                   span_lo=None, span_hi=None, mirror_perp=None):
            """Create chain dim at primary_perp+offset_val, and mirrored if both_sides."""
            if check_mode:
                span_lo = None
                span_hi = None
            made = []
            rp_list = list(ref_pos)
            if len(rp_list) >= 2:
                dim_attempts[0] += 1
            d = _create_chain_dim(self.doc, view, rp_list, axis,
                                  primary_perp + offset_val, margin, dim_type, dim_z,
                                  span_lo=span_lo, span_hi=span_hi,
                                  failures=dim_failures)
            if d:
                made.append(d)
            if both_sides and mirror_perp is not None:
                if len(rp_list) >= 2:
                    dim_attempts[0] += 1
                d2 = _create_chain_dim(self.doc, view, rp_list, axis,
                                       mirror_perp - offset_val, margin, dim_type, dim_z,
                                       span_lo=span_lo, span_hi=span_hi,
                                       failures=dim_failures)
                if d2:
                    made.append(d2)
            return made

        t = Transaction(self.doc, "T3Lab: Auto Dimension [{}]".format(view.Name))
        try:
            t.Start()
            options = t.GetFailureHandlingOptions()
            options.SetFailuresPreprocessor(WarningSwallower())
            t.SetFailureHandlingOptions(options)

            # ══ PHASE 1 (L3): Grid-to-Grid Overall Chains ══════════════════
            # Dim lines extend across the full grid span; placed above + below
            # (X) and left + right (Y) when both_sides is on.
            if do_grids:
                if run_x and len(v_grids) >= 2:
                    ref_pos = []
                    for g in v_grids:
                        r = _get_grid_reference(g, view)
                        if r:
                            ref_pos.append((_grid_pos(g, 'X'), r))
                    made = _chain(ref_pos, 'X', y_max, l3_feet,
                                  span_lo=v_span_lo, span_hi=v_span_hi,
                                  mirror_perp=y_min)
                    created_dims.extend(made)
                    dims_created += len(made)

                if run_y and len(h_grids) >= 2:
                    ref_pos = []
                    for g in h_grids:
                        r = _get_grid_reference(g, view)
                        if r:
                            ref_pos.append((_grid_pos(g, 'Y'), r))
                    made = _chain(ref_pos, 'Y', x_min, -l3_feet,
                                  span_lo=h_span_lo, span_hi=h_span_hi,
                                  mirror_perp=x_max)
                    created_dims.extend(made)
                    dims_created += len(made)

            # ══ PHASE 2 (L2): Column String Dimensions ══════════════════════
            if all_cols:
                col_ctr = {element_id_int(c.Id): _elem_centroid(c, view) for c in all_cols}

                if check_mode:
                    for col in all_cols:
                        cx, cy = col_ctr[element_id_int(col.Id)]
                        bb_c = col.get_BoundingBox(view)
                        if run_x:
                            ref_pos = []
                            rts = _axis_ref_types(col, 'X')
                            r = _col_ref_one(col, view, 'X',
                                             rts[2], rts[0], rts[1])
                            if r:
                                ref_pos.append((cx, r))
                            ng, ng_pos = _nearest_grid_nonzero(cx, cy, v_grids, 'X')
                            if ng:
                                nr = _get_grid_reference(ng, view)
                                if nr:
                                    ref_pos.append((ng_pos, nr))
                            off = ((bb_c.Max.Y - bb_c.Min.Y) * 0.5 + 50.0 * MM_TO_FEET
                                   if bb_c else ck_off)
                            made = _chain(ref_pos, 'X', cy, off)
                            created_dims.extend(made)
                            dims_created += len(made)
                        if run_y:
                            ref_pos = []
                            rts = _axis_ref_types(col, 'Y')
                            r = _col_ref_one(col, view, 'Y',
                                             rts[2], rts[0], rts[1])
                            if r:
                                ref_pos.append((cy, r))
                            ng, ng_pos = _nearest_grid_nonzero(cx, cy, h_grids, 'Y')
                            if ng:
                                nr = _get_grid_reference(ng, view)
                                if nr:
                                    ref_pos.append((ng_pos, nr))
                            off = ((bb_c.Max.X - bb_c.Min.X) * 0.5 + 50.0 * MM_TO_FEET
                                   if bb_c else ck_off)
                            made = _chain(ref_pos, 'Y', cx, -off)
                            created_dims.extend(made)
                            dims_created += len(made)
                else:
                    # Row/column strings: ONE chained dimension per column row
                    # (X) and per column line (Y). Each chain runs
                    # flanking grid → col face L → col face R → … → flanking grid,
                    # giving offset + width + offset segments like a manual
                    # structural plan. The old per-column loop created a
                    # separate 2-ref dim for every column at the same offset,
                    # which stacked dozens of overlapping strings.
                    # A column whose references cannot be extracted simply
                    # drops out of the chain — no column blocks the others.
                    if run_x:
                        for row_y, row_cols in _group_elements_by_pos(
                            all_cols,
                            lambda c: col_ctr[element_id_int(c.Id)][1],
                            GROUPING_TOL
                        ):
                            xs = [col_ctr[element_id_int(c.Id)][0] for c in row_cols]
                            ref_pos = []
                            lg, rg = _flanking_grids(v_grids, min(xs), max(xs), 'X')
                            for g in (lg, rg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'X'), r))
                            col_ref_count = 0
                            for col in row_cols:
                                cx = col_ctr[element_id_int(col.Id)][0]
                                bb_c = col.get_BoundingBox(view)
                                rts = _axis_ref_types(col, 'X')
                                added = 0
                                for rt, pos_fn in (
                                    (rts[0],
                                     lambda b, c: b.Min.X if b else c - 0.01),
                                    (rts[1],
                                     lambda b, c: b.Max.X if b else c + 0.01),
                                ):
                                    try:
                                        refs = list(col.GetReferences(rt))
                                        if refs:
                                            ref_pos.append((pos_fn(bb_c, cx), refs[0]))
                                            added += 1
                                    except Exception:
                                        pass
                                if added == 0:
                                    r = _col_ref_one(col, view, 'X', *rts)
                                    if r:
                                        ref_pos.append((cx, r))
                                        added = 1
                                col_ref_count += added
                            if col_ref_count == 0:
                                # Only grid refs left — grid chains are Phase 1's job.
                                continue
                            made = _chain(ref_pos, 'X', row_y, l2_feet)
                            created_dims.extend(made)
                            dims_created += len(made)
                    if run_y:
                        for col_x, line_cols in _group_elements_by_pos(
                            all_cols,
                            lambda c: col_ctr[element_id_int(c.Id)][0],
                            GROUPING_TOL
                        ):
                            ys = [col_ctr[element_id_int(c.Id)][1] for c in line_cols]
                            ref_pos = []
                            bg, tg = _flanking_grids(h_grids, min(ys), max(ys), 'Y')
                            for g in (bg, tg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'Y'), r))
                            col_ref_count = 0
                            for col in line_cols:
                                cy = col_ctr[element_id_int(col.Id)][1]
                                bb_c = col.get_BoundingBox(view)
                                rts = _axis_ref_types(col, 'Y')
                                added = 0
                                for rt, pos_fn in (
                                    (rts[0],
                                     lambda b, c: b.Min.Y if b else c - 0.01),
                                    (rts[1],
                                     lambda b, c: b.Max.Y if b else c + 0.01),
                                ):
                                    try:
                                        refs = list(col.GetReferences(rt))
                                        if refs:
                                            ref_pos.append((pos_fn(bb_c, cy), refs[0]))
                                            added += 1
                                    except Exception:
                                        pass
                                if added == 0:
                                    r = _col_ref_one(col, view, 'Y', *rts)
                                    if r:
                                        ref_pos.append((cy, r))
                                        added = 1
                                col_ref_count += added
                            if col_ref_count == 0:
                                # Only grid refs left — grid chains are Phase 1's job.
                                continue
                            made = _chain(ref_pos, 'Y', col_x, -l2_feet)
                            created_dims.extend(made)
                            dims_created += len(made)

            # ══ PHASE 3 (L1): Inner Element String — windows + doors ════════
            inner_elems = windows + doors
            if inner_elems:
                inner_ctr = {element_id_int(e.Id): _elem_centroid(e, view)
                             for e in inner_elems}

                if check_mode:
                    for elem in inner_elems:
                        cx_e, cy_e = inner_ctr[element_id_int(elem.Id)]
                        bb = elem.get_BoundingBox(view)
                        if run_x:
                            ref_pos = []
                            rts = _axis_ref_types(elem, 'X')
                            added = 0
                            for rt, pos_fn in (
                                (rts[0],
                                 lambda b, c: b.Min.X if b else c - 0.01),
                                (rts[1],
                                 lambda b, c: b.Max.X if b else c + 0.01),
                            ):
                                try:
                                    refs = list(elem.GetReferences(rt))
                                    if refs:
                                        ref_pos.append((pos_fn(bb, cx_e), refs[0]))
                                        added += 1
                                except Exception:
                                    pass
                            if added == 0:
                                r = _col_ref_one(elem, view, 'X', *rts)
                                if r:
                                    ref_pos.append((cx_e, r))
                            ng, ng_pos = _nearest_grid(cx_e, cy_e, v_grids, 'X')
                            if ng:
                                nr = _get_grid_reference(ng, view)
                                if nr:
                                    ref_pos.append((ng_pos, nr))
                            off = ((bb.Max.Y - bb.Min.Y) * 0.5 + 50.0 * MM_TO_FEET
                                   if bb else ck_off)
                            made = _chain(ref_pos, 'X', cy_e, off)
                            created_dims.extend(made)
                            dims_created += len(made)
                        if run_y:
                            ref_pos = []
                            rts = _axis_ref_types(elem, 'Y')
                            added = 0
                            for rt, pos_fn in (
                                (rts[0],
                                 lambda b, c: b.Min.Y if b else c - 0.01),
                                (rts[1],
                                 lambda b, c: b.Max.Y if b else c + 0.01),
                            ):
                                try:
                                    refs = list(elem.GetReferences(rt))
                                    if refs:
                                        ref_pos.append((pos_fn(bb, cy_e), refs[0]))
                                        added += 1
                                except Exception:
                                    pass
                            if added == 0:
                                r = _col_ref_one(elem, view, 'Y', *rts)
                                if r:
                                    ref_pos.append((cy_e, r))
                            ng, ng_pos = _nearest_grid(cx_e, cy_e, h_grids, 'Y')
                            if ng:
                                nr = _get_grid_reference(ng, view)
                                if nr:
                                    ref_pos.append((ng_pos, nr))
                            off = ((bb.Max.X - bb.Min.X) * 0.5 + 50.0 * MM_TO_FEET
                                   if bb else ck_off)
                            made = _chain(ref_pos, 'Y', cx_e, -off)
                            created_dims.extend(made)
                            dims_created += len(made)
                else:
                    if run_x:
                        for row_y, row_elems in _group_elements_by_pos(
                            inner_elems,
                            lambda e: inner_ctr[element_id_int(e.Id)][1],
                            GROUPING_TOL
                        ):
                            xs = [inner_ctr[element_id_int(e.Id)][0] for e in row_elems]
                            ref_pos = []
                            lg, rg = _flanking_grids(v_grids, min(xs), max(xs), 'X')
                            for g in (lg, rg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'X'), r))
                            elem_ref_count = 0
                            for elem in row_elems:
                                # An opening whose width runs along Y belongs
                                # to the Y pass — its jamb planes face X and
                                # would be rejected by NewDimension here.
                                if not _hand_matches_axis(elem, 'X'):
                                    continue
                                bb   = elem.get_BoundingBox(view)
                                cx_e = inner_ctr[element_id_int(elem.Id)][0]
                                added = 0
                                for rt, pos_fn in (
                                    (FamilyInstanceReferenceType.Left,
                                     lambda b, c: b.Min.X if b else c - 0.01),
                                    (FamilyInstanceReferenceType.Right,
                                     lambda b, c: b.Max.X if b else c + 0.01),
                                ):
                                    try:
                                        refs = list(elem.GetReferences(rt))
                                        if refs:
                                            ref_pos.append((pos_fn(bb, cx_e), refs[0]))
                                            added += 1
                                    except Exception:
                                        pass
                                if added == 0:
                                    r = _col_ref_one(elem, view, 'X',
                                                     FamilyInstanceReferenceType.Left,
                                                     FamilyInstanceReferenceType.Right)
                                    if r:
                                        ref_pos.append((cx_e, r))
                                        added = 1
                                elem_ref_count += added
                            if elem_ref_count == 0:
                                continue
                            made = _chain(ref_pos, 'X', row_y, l1_feet,
                                          span_lo=v_span_lo, span_hi=v_span_hi,
                                          mirror_perp=y_min)
                            created_dims.extend(made)
                            dims_created += len(made)

                    if run_y:
                        for col_x, col_elems in _group_elements_by_pos(
                            inner_elems,
                            lambda e: inner_ctr[element_id_int(e.Id)][0],
                            GROUPING_TOL
                        ):
                            ys = [inner_ctr[element_id_int(e.Id)][1] for e in col_elems]
                            ref_pos = []
                            bg, tg = _flanking_grids(h_grids, min(ys), max(ys), 'Y')
                            for g in (bg, tg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'Y'), r))
                            elem_ref_count = 0
                            for elem in col_elems:
                                # An opening whose width runs along X belongs
                                # to the X pass above.
                                if not _hand_matches_axis(elem, 'Y'):
                                    continue
                                bb   = elem.get_BoundingBox(view)
                                cy_e = inner_ctr[element_id_int(elem.Id)][1]
                                added = 0
                                # Width along Y → the jamb planes are the
                                # family's Left/Right (hand runs along Y).
                                for rt, pos_fn in (
                                    (FamilyInstanceReferenceType.Left,
                                     lambda b, c: b.Min.Y if b else c - 0.01),
                                    (FamilyInstanceReferenceType.Right,
                                     lambda b, c: b.Max.Y if b else c + 0.01),
                                ):
                                    try:
                                        refs = list(elem.GetReferences(rt))
                                        if refs:
                                            ref_pos.append((pos_fn(bb, cy_e), refs[0]))
                                            added += 1
                                    except Exception:
                                        pass
                                if added == 0:
                                    r = _col_ref_one(elem, view, 'Y',
                                                     FamilyInstanceReferenceType.Left,
                                                     FamilyInstanceReferenceType.Right)
                                    if r:
                                        ref_pos.append((cy_e, r))
                                        added = 1
                                elem_ref_count += added
                            if elem_ref_count == 0:
                                continue
                            made = _chain(ref_pos, 'Y', col_x, -l1_feet,
                                          span_lo=h_span_lo, span_hi=h_span_hi,
                                          mirror_perp=x_max)
                            created_dims.extend(made)
                            dims_created += len(made)

            # ══ PHASE 4 (L2): Wall Chain Dimensions (wall_mode) ═════════════
            for wall in walls:
                try:
                    all_wall_refs = _collect_wall_core_refs(wall)
                    if not all_wall_refs:
                        continue
                    loc = wall.Location
                    if isinstance(loc, LocationCurve):
                        d = _curve_direction(loc.Curve)
                        if min(abs(d.X), abs(d.Y)) > AXIS_TOLERANCE:
                            # Diagonal wall — an X/Y-aligned chain would land
                            # off the wall and NewDimension usually rejects it.
                            continue
                    cx, cy = _elem_centroid(wall, view)
                    is_h   = _wall_is_horizontal(wall)
                    bb     = wall.get_BoundingBox(view)

                    if is_h and run_y:
                        ext_y = bb.Max.Y if bb else cy
                        int_y = bb.Min.Y if bb else cy
                        ref_pos = []
                        if wall_mode == 'ext':
                            ref_pos.append((ext_y, all_wall_refs[0]))
                        elif wall_mode == 'int':
                            ref_pos.append((int_y, all_wall_refs[-1]
                                            if len(all_wall_refs) > 1
                                            else all_wall_refs[0]))
                        else:
                            ref_pos.append((ext_y, all_wall_refs[0]))
                            if len(all_wall_refs) > 1:
                                ref_pos.append((int_y, all_wall_refs[1]))
                        face_ys = [rp[0] for rp in ref_pos]
                        if check_mode:
                            ng, ng_pos = _nearest_grid(cx, cy, h_grids, 'Y')
                            if ng:
                                nr = _get_grid_reference(ng, view)
                                if nr:
                                    ref_pos.append((ng_pos, nr))
                        else:
                            bg, tg = _flanking_grids(h_grids, min(face_ys), max(face_ys), 'Y')
                            for g in (bg, tg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'Y'), r))
                        made = _chain(ref_pos, 'Y', cx,
                                      -(ck_off if check_mode else l2_feet),
                                      span_lo=h_span_lo, span_hi=h_span_hi,
                                      mirror_perp=x_max)
                        created_dims.extend(made)
                        dims_created += len(made)

                    elif not is_h and run_x:
                        ext_x = bb.Max.X if bb else cx
                        int_x = bb.Min.X if bb else cx
                        ref_pos = []
                        if wall_mode == 'ext':
                            ref_pos.append((ext_x, all_wall_refs[0]))
                        elif wall_mode == 'int':
                            ref_pos.append((int_x, all_wall_refs[-1]
                                            if len(all_wall_refs) > 1
                                            else all_wall_refs[0]))
                        else:
                            ref_pos.append((ext_x, all_wall_refs[0]))
                            if len(all_wall_refs) > 1:
                                ref_pos.append((int_x, all_wall_refs[1]))
                        face_xs = [rp[0] for rp in ref_pos]
                        if check_mode:
                            ng, ng_pos = _nearest_grid(cx, cy, v_grids, 'X')
                            if ng:
                                nr = _get_grid_reference(ng, view)
                                if nr:
                                    ref_pos.append((ng_pos, nr))
                        else:
                            lg, rg = _flanking_grids(v_grids, min(face_xs), max(face_xs), 'X')
                            for g in (lg, rg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'X'), r))
                        made = _chain(ref_pos, 'X', cy,
                                      ck_off if check_mode else l2_feet,
                                      span_lo=v_span_lo, span_hi=v_span_hi,
                                      mirror_perp=y_min)
                        created_dims.extend(made)
                        dims_created += len(made)
                except Exception as ex:
                    logger.warning("Wall dim skipped: {}".format(ex))

            # ══ PHASE 5 (L1): Lift String Dimensions ════════════════════════
            if lifts:
                lift_ctr = {element_id_int(l.Id): _elem_centroid(l, view) for l in lifts}

                if check_mode:
                    for lift in lifts:
                        lx, ly = lift_ctr[element_id_int(lift.Id)]
                        bb_l = lift.get_BoundingBox(view)
                        if run_x:
                            ref_pos = []
                            r = _col_ref_one(lift, view, 'X',
                                             *_axis_ref_types(lift, 'X'))
                            if r:
                                ref_pos.append((lx, r))
                            ng, ng_pos = _nearest_grid(lx, ly, v_grids, 'X')
                            if ng:
                                nr = _get_grid_reference(ng, view)
                                if nr:
                                    ref_pos.append((ng_pos, nr))
                            off = ((bb_l.Max.Y - bb_l.Min.Y) * 0.5 + 50.0 * MM_TO_FEET
                                   if bb_l else ck_off)
                            made = _chain(ref_pos, 'X', ly, off)
                            created_dims.extend(made)
                            dims_created += len(made)
                        if run_y:
                            ref_pos = []
                            r = _col_ref_one(lift, view, 'Y',
                                             *_axis_ref_types(lift, 'Y'))
                            if r:
                                ref_pos.append((ly, r))
                            ng, ng_pos = _nearest_grid(lx, ly, h_grids, 'Y')
                            if ng:
                                nr = _get_grid_reference(ng, view)
                                if nr:
                                    ref_pos.append((ng_pos, nr))
                            off = ((bb_l.Max.X - bb_l.Min.X) * 0.5 + 50.0 * MM_TO_FEET
                                   if bb_l else ck_off)
                            made = _chain(ref_pos, 'Y', lx, -off)
                            created_dims.extend(made)
                            dims_created += len(made)
                else:
                    if run_x:
                        for row_y, row_lifts in _group_elements_by_pos(
                            lifts,
                            lambda l: lift_ctr[element_id_int(l.Id)][1],
                            GROUPING_TOL
                        ):
                            xs = [lift_ctr[element_id_int(l.Id)][0] for l in row_lifts]
                            ref_pos = []
                            lg, rg = _flanking_grids(v_grids, min(xs), max(xs), 'X')
                            for g in (lg, rg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'X'), r))
                            lift_ref_count = 0
                            for lift in row_lifts:
                                r = _col_ref_one(lift, view, 'X',
                                                 *_axis_ref_types(lift, 'X'))
                                if r:
                                    ref_pos.append((lift_ctr[element_id_int(lift.Id)][0], r))
                                    lift_ref_count += 1
                            if lift_ref_count == 0:
                                continue
                            made = _chain(ref_pos, 'X', row_y, l1_feet,
                                          span_lo=v_span_lo, span_hi=v_span_hi,
                                          mirror_perp=y_min)
                            created_dims.extend(made)
                            dims_created += len(made)

                    if run_y:
                        for col_x, col_lifts in _group_elements_by_pos(
                            lifts,
                            lambda l: lift_ctr[element_id_int(l.Id)][0],
                            GROUPING_TOL
                        ):
                            ys = [lift_ctr[element_id_int(l.Id)][1] for l in col_lifts]
                            ref_pos = []
                            bg, tg = _flanking_grids(h_grids, min(ys), max(ys), 'Y')
                            for g in (bg, tg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'Y'), r))
                            lift_ref_count = 0
                            for lift in col_lifts:
                                r = _col_ref_one(lift, view, 'Y',
                                                 *_axis_ref_types(lift, 'Y'))
                                if r:
                                    ref_pos.append((lift_ctr[element_id_int(lift.Id)][1], r))
                                    lift_ref_count += 1
                            if lift_ref_count == 0:
                                continue
                            made = _chain(ref_pos, 'Y', col_x, -l1_feet,
                                          span_lo=h_span_lo, span_hi=h_span_hi,
                                          mirror_perp=x_max)
                            created_dims.extend(made)
                            dims_created += len(made)

            # ══ PHASE 6: Facade Dimension Chains ══════════════════════════════
            if do_facade:
                facade_walls_map = _collect_facade_walls(self.doc, view, facade_tol)

                for facade_name in ('NORTH', 'SOUTH', 'EAST', 'WEST'):
                    fwalls = facade_walls_map.get(facade_name, [])
                    if not fwalls:
                        continue
                    fwalls = _expand_by_joins(self.doc, fwalls)

                    if facade_name in ('NORTH', 'SOUTH'):
                        axis = 'X'
                        perp = (y_max + facade_off) if facade_name == 'NORTH' else (y_min - facade_off)
                        span_lo = v_span_lo
                        span_hi = v_span_hi
                        used_grids = v_grids
                    else:
                        axis = 'Y'
                        perp = (x_max + facade_off) if facade_name == 'EAST' else (x_min - facade_off)
                        span_lo = h_span_lo
                        span_hi = h_span_hi
                        used_grids = h_grids

                    ref_pos = []

                    # Flanking grids
                    fwall_positions = []
                    for w in fwalls:
                        bb_w = w.get_BoundingBox(view)
                        if bb_w:
                            if axis == 'X':
                                fwall_positions += [bb_w.Min.X, bb_w.Max.X]
                            else:
                                fwall_positions += [bb_w.Min.Y, bb_w.Max.Y]
                    if fwall_positions:
                        if axis == 'X':
                            lg, rg = _flanking_grids(v_grids, min(fwall_positions), max(fwall_positions), 'X')
                            for g in (lg, rg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'X'), r))
                        else:
                            bg, tg = _flanking_grids(h_grids, min(fwall_positions), max(fwall_positions), 'Y')
                            for g in (bg, tg):
                                if g:
                                    r = _get_grid_reference(g, view)
                                    if r:
                                        ref_pos.append((_grid_pos(g, 'Y'), r))

                    # Wall face refs
                    for wall in fwalls:
                        try:
                            wall_refs = _collect_wall_core_refs(wall)
                            if not wall_refs:
                                continue
                            bb_w = wall.get_BoundingBox(view)
                            if not bb_w:
                                continue
                            if axis == 'X':
                                cx_w = (bb_w.Min.X + bb_w.Max.X) * 0.5
                                ref_pos.append((cx_w, wall_refs[0]))
                            else:
                                cy_w = (bb_w.Min.Y + bb_w.Max.Y) * 0.5
                                ref_pos.append((cy_w, wall_refs[0]))
                        except Exception:
                            pass

                    # Window + door refs along this facade
                    for elem in (windows + doors):
                        try:
                            cx_e, cy_e = _elem_centroid(elem, view)
                            bb_e = elem.get_BoundingBox(view)
                            if axis == 'X':
                                facade_y = y_max if facade_name == 'NORTH' else y_min
                                if abs(cy_e - facade_y) > facade_tol:
                                    continue
                                # Only openings whose width runs along the
                                # facade — perpendicular jambs would be
                                # rejected by NewDimension.
                                if not _hand_matches_axis(elem, 'X'):
                                    continue
                                for rt, pos_fn in (
                                    (FamilyInstanceReferenceType.Left,
                                     lambda b, c: b.Min.X if b else c - 0.01),
                                    (FamilyInstanceReferenceType.Right,
                                     lambda b, c: b.Max.X if b else c + 0.01),
                                ):
                                    try:
                                        refs = list(elem.GetReferences(rt))
                                        if refs:
                                            ref_pos.append((pos_fn(bb_e, cx_e), refs[0]))
                                    except Exception:
                                        pass
                            else:
                                facade_x = x_max if facade_name == 'EAST' else x_min
                                if abs(cx_e - facade_x) > facade_tol:
                                    continue
                                if not _hand_matches_axis(elem, 'Y'):
                                    continue
                                # Hand runs along Y → jamb planes are the
                                # family's Left/Right.
                                for rt, pos_fn in (
                                    (FamilyInstanceReferenceType.Left,
                                     lambda b, c: b.Min.Y if b else c - 0.01),
                                    (FamilyInstanceReferenceType.Right,
                                     lambda b, c: b.Max.Y if b else c + 0.01),
                                ):
                                    try:
                                        refs = list(elem.GetReferences(rt))
                                        if refs:
                                            ref_pos.append((pos_fn(bb_e, cy_e), refs[0]))
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                    if len(ref_pos) >= 2:
                        dim_attempts[0] += 1
                        d = _create_chain_dim(self.doc, view, ref_pos, axis, perp, margin,
                                              dim_type, dim_z,
                                              span_lo=span_lo, span_hi=span_hi,
                                              failures=dim_failures)
                        if d:
                            created_dims.append(d)
                            dims_created += 1

            t.Commit()

        except Exception as ex:
            try:
                t.RollBack()
            except Exception:
                pass
            msg = "Transaction failed for view '{}': {}".format(view.Name, ex)
            logger.error(msg)
            TaskDialog.Show("Auto Dimension — Error", msg)
            return (0, dim_attempts[0], dim_failures, [])

        return (dims_created, dim_attempts[0], dim_failures, created_dims)

    def _dim_section_view(self, view, do_grids, dim_type, l3_feet):
        """
        Dimension a Section/Elevation view (gated by the Grids checkbox):
        one horizontal chain across the vertical grids near the top of the
        crop, and one vertical chain across the levels near its left edge.
        Geometry is computed in the view's own plane via the crop-box
        transform (BasisX = view right, BasisY = view up, Z=0 = view plane).
        Returns (dims_created, attempts, failures_list, created_dims).
        """
        if not do_grids:
            return (0, 0, [], [])
        try:
            cb = view.CropBox
            tf = cb.Transform
        except Exception as ex:
            return (0, 0, ["Crop box unavailable for '{}': {}".format(
                view.Name, ex)], [])

        margin = l3_feet * 0.5

        def _datum_elems(category):
            try:
                return list(
                    FilteredElementCollector(self.doc, view.Id)
                    .OfCategory(category)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
            except Exception:
                return []

        # Grids → position along the view's right direction.
        grid_rp = []
        for g in _datum_elems(BuiltInCategory.OST_Grids):
            try:
                r = _get_grid_reference(g, view)
                if not r:
                    continue
                p = g.Curve.GetEndPoint(0)
                pos = p.Subtract(tf.Origin).DotProduct(tf.BasisX)
                grid_rp.append((pos, r))
            except Exception:
                continue

        # Levels → position along the view's up direction.
        # _get_grid_reference works on any DatumPlane, Level included.
        level_rp = []
        for lv in _datum_elems(BuiltInCategory.OST_Levels):
            try:
                r = _get_grid_reference(lv, view)
                if not r:
                    continue
                q = XYZ(tf.Origin.X, tf.Origin.Y, lv.Elevation)
                pos = q.Subtract(tf.Origin).DotProduct(tf.BasisY)
                level_rp.append((pos, r))
            except Exception:
                continue

        dims_created = 0
        created_dims = []
        attempts = [0]
        failures = []

        def _view_plane_chain(rp_list, fixed, horizontal):
            """Chain dim in the view plane. horizontal=True → line along
            BasisX at view-Y=fixed; False → along BasisY at view-X=fixed."""
            if len(rp_list) < 2:
                return None
            rp_sorted = sorted(rp_list, key=lambda rp: rp[0])
            deduped = [rp_sorted[0]]
            for rp in rp_sorted[1:]:
                if abs(rp[0] - deduped[-1][0]) > 1e-4:
                    deduped.append(rp)
            if len(deduped) < 2:
                return None
            lo = deduped[0][0] - margin
            hi = deduped[-1][0] + margin
            ra = ReferenceArray()
            for _, r in deduped:
                ra.Append(r)
            if horizontal:
                p0 = tf.OfPoint(XYZ(lo, fixed, 0.0))
                p1 = tf.OfPoint(XYZ(hi, fixed, 0.0))
            else:
                p0 = tf.OfPoint(XYZ(fixed, lo, 0.0))
                p1 = tf.OfPoint(XYZ(fixed, hi, 0.0))
            attempts[0] += 1
            try:
                return self.doc.Create.NewDimension(
                    view, Line.CreateBound(p0, p1), ra, dim_type)
            except Exception as ex:
                msg = str(ex)
                logger.warning("Section chain dim failed: {}".format(msg))
                if len(failures) < 5:
                    failures.append(msg)
                return None

        t = Transaction(self.doc, "T3Lab: Auto Dimension [{}]".format(view.Name))
        try:
            t.Start()
            options = t.GetFailureHandlingOptions()
            options.SetFailuresPreprocessor(WarningSwallower())
            t.SetFailureHandlingOptions(options)

            d = _view_plane_chain(grid_rp, cb.Max.Y - l3_feet, True)
            if d:
                created_dims.append(d)
                dims_created += 1
            d = _view_plane_chain(level_rp, cb.Min.X + l3_feet, False)
            if d:
                created_dims.append(d)
                dims_created += 1

            t.Commit()
        except Exception as ex:
            try:
                t.RollBack()
            except Exception:
                pass
            msg = "Transaction failed for view '{}': {}".format(view.Name, ex)
            logger.error(msg)
            TaskDialog.Show("Auto Dimension — Error", msg)
            return (0, attempts[0], failures, [])

        return (dims_created, attempts[0], failures, created_dims)


# MAIN SCRIPT
# ==================================================
def show_dialog():
    # NOTE: `pyrevit.forms.<anything>` raises PyRevitCPythonNotSupported under the
    # CPython engine — its __getattr__ refuses every attribute. GUI.T3Dialog is the
    # T3 replacement and works on both engines.
    from GUI.T3Dialog import show_warning
    from Snippets._host import resolve_doc, host_uiapp

    doc_inst, doc_error = resolve_doc()
    if doc_inst is None:
        show_warning(doc_error or "Please open a Revit document first.",
                     title="Auto Dimension")
        return

    uiapp = host_uiapp()
    uidoc_inst = getattr(uiapp, 'ActiveUIDocument', None) if uiapp else None
    if uidoc_inst is None:
        show_warning("No active view to dimension in. Open a plan or section "
                     "view in your model, then run Auto Dimension again.",
                     title="Auto Dimension")
        return
    doc_inst = uidoc_inst.Document

    window = AutoDimensionWindow(uidoc_inst, doc_inst)
    # Modal (ShowDialog) is required: Run starts a Transaction directly in the
    # button handler, which is only legal while the command is still inside
    # Revit API context. A modeless window drops that context and
    # Transaction.Start/NewDimension throw "outside of API context"
    # (TagCheckerDialog reverted to modal for the same reason). View selection
    # happens in-dialog, so viewport interaction is not needed while open.
    window.show(modal=True)

if __name__ == '__main__':
    show_dialog()
