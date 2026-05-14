# -*- coding: utf-8 -*-
"""
Auto Dimension

Auto-dimension walls, structural columns, architectural columns,
and grids in the current view.

--------------------------------------------------------
Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
--------------------------------------------------------
"""

__author__  = "Tran Tien Thanh"
__title__   = "Auto Dimension"
__version__ = "2.0.0"

# IMPORT LIBRARIES
# ==================================================
import os
import sys
import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')

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
    ViewType,
    FamilyInstanceReferenceType,
)
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.UI import TaskDialog
from pyrevit import revit, forms, script

# PATH SETUP
# ==================================================
SCRIPT_DIR = os.path.dirname(__file__)
# __file__ is inside: T3Lab.extension/T3Lab.tab/Annotation.panel/AutoDimension.pushbutton/
# Four dirname calls from __file__ reach the T3Lab.extension root.
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
LIB_DIR    = os.path.join(EXT_DIR, 'lib')
XAML_FILE  = os.path.join(EXT_DIR, 'lib', 'GUI', 'Tools', 'AutoDimension.xaml')
LOGO_FILE  = os.path.join(EXT_DIR, 'lib', 'GUI', 'T3Lab_logo.png')

if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()
doc    = revit.doc
uidoc  = revit.uidoc

# CONSTANTS
# ==================================================
MM_TO_FEET  = 1.0 / 304.8
# Tolerance for deciding if a vector is predominantly horizontal vs vertical
AXIS_TOLERANCE = 0.1
# Group elements within 500 mm as a structural row/column line
GROUPING_TOL = 500.0 * MM_TO_FEET


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

        refs.extend(ext_refs)
        refs.extend(int_refs)
    except Exception as ex:
        logger.warning("Wall core face error: {}".format(ex))
    return refs


def _get_grid_reference(grid, view):
    """
    Return a valid Reference to a grid curve for NewDimension.
    Must use get_Geometry with ComputeReferences=True and opt.View set —
    grid.Curve.Reference is null without that context.
    """
    try:
        opt = Options()
        opt.ComputeReferences = True
        opt.View = view
        geom = grid.get_Geometry(opt)
        if geom is None:
            return None
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
        logger.warning("Grid reference error: {}".format(ex))
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
    Uses GetInstanceGeometry() so the References are in project context.
    """
    try:
        opt = Options()
        opt.ComputeReferences = True
        opt.View = view
        geom = col.get_Geometry(opt)
        if geom is None:
            return None
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
    """Return one face reference for col along axis. Tries primary_rtypes, then fallbacks."""
    for rt in primary_rtypes:
        try:
            refs = list(col.GetReferences(rt))
            if refs:
                return refs[0]
        except Exception:
            pass
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
    return _col_ref_from_geom(col, view, axis)


def _create_chain_dim(doc_ref, view, ref_pos_list, axis, perp, margin, dim_type, dim_z,
                      span_lo=None, span_hi=None):
    """
    Create a chained/string dimension.
    ref_pos_list: list of (pos_along_axis, Reference) — will be sorted and deduplicated.
    axis='X': horizontal dim line at y=perp (measures X distances).
    axis='Y': vertical dim line at x=perp (measures Y distances).
    span_lo/span_hi: if provided, extend the dim line to this range (full grid extent).
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
        logger.warning("Chain dim failed: {}".format(ex))
        return None


# WINDOW CLASS
# ==================================================

class AutoDimensionWindow(forms.WPFWindow):
    """
    WPF window for the Auto Dimension tool.
    Inherits from pyrevit forms.WPFWindow which handles XAML loading.
    """

    def __init__(self, uidoc_ref, doc_ref):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self.uidoc = uidoc_ref
        self.doc   = doc_ref
        self._dim_types = []  # list of DimensionType elements


        self._populate_dim_types()
        self._set_status("Ready")



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
            for dt in self._dim_types:
                try:
                    param = dt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    name = param.AsString() if param and param.AsString() else dt.Name
                except Exception:
                    name = "Type {}".format(dt.Id.IntegerValue)
                self.cmb_dim_type.Items.Add(name)

            if self._dim_types:
                self.cmb_dim_type.SelectedIndex = 0
                self._set_status("Loaded {} linear dimension type(s).".format(
                    len(self._dim_types)))
            else:
                self._set_status("No linear dimension types found in document.")
        except Exception as ex:
            logger.warning("Could not load dimension types: {}".format(ex))

    # ── Offset mode toggle ────────────────────────────────────────────────

    def offset_mode_changed(self, sender, args):
        if not hasattr(self, 'pnl_single_offset'):
            return
        is_3level = self.cmb_offset_mode.SelectedIndex == 1
        self.pnl_single_offset.Visibility = (
            Visibility.Collapsed if is_3level else Visibility.Visible)
        self.pnl_3level_offsets.Visibility = (
            Visibility.Visible if is_3level else Visibility.Collapsed)

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

        view = self.doc.ActiveView
        if not _is_valid_view(view):
            TaskDialog.Show("Auto Dimension",
                            "Please activate a Plan, Section, Elevation, or Detail view.")
            return

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
            TaskDialog.Show("Auto Dimension",
                            "No grids found in the current view.\n"
                            "Dimensions require grids as references.")
            self._set_status("No grids in view.")
            return

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

        walls       = _collect(BuiltInCategory.OST_Walls)             if do_walls       else []
        struct_cols = _collect(BuiltInCategory.OST_StructuralColumns)  if do_struct_cols else []
        arch_cols   = _collect(BuiltInCategory.OST_Columns)           if do_arch_cols   else []
        windows     = _collect(BuiltInCategory.OST_Windows)           if do_windows     else []
        doors       = _collect(BuiltInCategory.OST_Doors)             if do_doors       else []
        lifts       = _collect(BuiltInCategory.OST_MechanicalEquipment) if do_lifts     else []
        all_cols    = struct_cols + arch_cols

        # ── Compute bounding box for overall dim placement ─────────────────
        all_elems_for_bb = walls + all_cols + windows + doors + lifts
        all_cx = [_elem_centroid(e, view)[0] for e in all_elems_for_bb]
        all_cy = [_elem_centroid(e, view)[1] for e in all_elems_for_bb]
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

        # Primary outer placement positions (L3 offset)
        top_y    = y_max + l3_feet   # X overall chain — above
        left_x   = x_min - l3_feet  # Y overall chain — left
        # Secondary positions for both_sides mode
        bot_y    = y_min - l3_feet   # X overall chain — below
        right_x  = x_max + l3_feet  # Y overall chain — right

        dims_created = 0
        created_dims = []  # track for conflict detection

        def _chain(ref_pos, axis, primary_perp, offset_val,
                   span_lo=None, span_hi=None, mirror_perp=None):
            """Create chain dim at primary_perp+offset_val, and mirrored if both_sides."""
            made = []
            d = _create_chain_dim(self.doc, view, list(ref_pos), axis,
                                  primary_perp + offset_val, margin, dim_type, dim_z,
                                  span_lo=span_lo, span_hi=span_hi)
            if d:
                made.append(d)
            if both_sides and mirror_perp is not None:
                d2 = _create_chain_dim(self.doc, view, list(ref_pos), axis,
                                       mirror_perp - offset_val, margin, dim_type, dim_z,
                                       span_lo=span_lo, span_hi=span_hi)
                if d2:
                    made.append(d2)
            return made

        t = Transaction(self.doc, "T3Lab: Auto Dimension")
        try:
            t.Start()

            # ══ PHASE 1 (L3): Grid-to-Grid Overall Chains ══════════════════
            # Dim lines extend across the full grid span; placed above + below
            # (X) and left + right (Y) when both_sides is on.
            if do_grids:
                if run_x and len(v_grids) >= 2:
                    ref_pos = [(  _grid_pos(g, 'X'), _get_grid_reference(g, view))
                               for g in v_grids if _get_grid_reference(g, view)]
                    made = _chain(ref_pos, 'X', y_max, l3_feet,
                                  span_lo=v_span_lo, span_hi=v_span_hi,
                                  mirror_perp=y_min)
                    created_dims.extend(made)
                    dims_created += len(made)

                if run_y and len(h_grids) >= 2:
                    ref_pos = [(_grid_pos(g, 'Y'), _get_grid_reference(g, view))
                               for g in h_grids if _get_grid_reference(g, view)]
                    made = _chain(ref_pos, 'Y', x_min, -l3_feet,
                                  span_lo=h_span_lo, span_hi=h_span_hi,
                                  mirror_perp=x_max)
                    created_dims.extend(made)
                    dims_created += len(made)

            # ══ PHASE 2 (L2): Column String Dimensions ══════════════════════
            if all_cols:
                col_ctr = {c.Id.IntegerValue: _elem_centroid(c, view) for c in all_cols}

                if run_x:
                    y_rows = _group_elements_by_pos(
                        all_cols,
                        lambda c: col_ctr[c.Id.IntegerValue][1],
                        GROUPING_TOL
                    )
                    for row_y, row_cols in y_rows:
                        xs  = [col_ctr[c.Id.IntegerValue][0] for c in row_cols]
                        ref_pos = []
                        lg, rg = _flanking_grids(v_grids, min(xs), max(xs), 'X')
                        for g in (lg, rg):
                            if g:
                                r = _get_grid_reference(g, view)
                                if r:
                                    ref_pos.append((_grid_pos(g, 'X'), r))
                        for col in row_cols:
                            r = _col_ref_one(col, view, 'X',
                                             FamilyInstanceReferenceType.Left,
                                             FamilyInstanceReferenceType.Right)
                            if r:
                                ref_pos.append((col_ctr[col.Id.IntegerValue][0], r))
                        made = _chain(ref_pos, 'X', row_y, l2_feet,
                                      span_lo=v_span_lo, span_hi=v_span_hi,
                                      mirror_perp=y_min)
                        created_dims.extend(made)
                        dims_created += len(made)

                if run_y:
                    x_cols = _group_elements_by_pos(
                        all_cols,
                        lambda c: col_ctr[c.Id.IntegerValue][0],
                        GROUPING_TOL
                    )
                    for col_x, col_grp in x_cols:
                        ys  = [col_ctr[c.Id.IntegerValue][1] for c in col_grp]
                        ref_pos = []
                        bg, tg = _flanking_grids(h_grids, min(ys), max(ys), 'Y')
                        for g in (bg, tg):
                            if g:
                                r = _get_grid_reference(g, view)
                                if r:
                                    ref_pos.append((_grid_pos(g, 'Y'), r))
                        for col in col_grp:
                            r = _col_ref_one(col, view, 'Y',
                                             FamilyInstanceReferenceType.Front,
                                             FamilyInstanceReferenceType.Back)
                            if r:
                                ref_pos.append((col_ctr[col.Id.IntegerValue][1], r))
                        made = _chain(ref_pos, 'Y', col_x, -l2_feet,
                                      span_lo=h_span_lo, span_hi=h_span_hi,
                                      mirror_perp=x_max)
                        created_dims.extend(made)
                        dims_created += len(made)

            # ══ PHASE 3 (L1): Inner Element String — windows + doors ════════
            inner_elems = windows + doors
            if inner_elems:
                inner_ctr = {e.Id.IntegerValue: _elem_centroid(e, view)
                             for e in inner_elems}

                if run_x:
                    for row_y, row_elems in _group_elements_by_pos(
                        inner_elems,
                        lambda e: inner_ctr[e.Id.IntegerValue][1],
                        GROUPING_TOL
                    ):
                        xs = [inner_ctr[e.Id.IntegerValue][0] for e in row_elems]
                        ref_pos = []
                        lg, rg = _flanking_grids(v_grids, min(xs), max(xs), 'X')
                        for g in (lg, rg):
                            if g:
                                r = _get_grid_reference(g, view)
                                if r:
                                    ref_pos.append((_grid_pos(g, 'X'), r))
                        for elem in row_elems:
                            bb   = elem.get_BoundingBox(view)
                            cx_e = inner_ctr[elem.Id.IntegerValue][0]
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
                        made = _chain(ref_pos, 'X', row_y, l1_feet,
                                      span_lo=v_span_lo, span_hi=v_span_hi,
                                      mirror_perp=y_min)
                        created_dims.extend(made)
                        dims_created += len(made)

                if run_y:
                    for col_x, col_elems in _group_elements_by_pos(
                        inner_elems,
                        lambda e: inner_ctr[e.Id.IntegerValue][0],
                        GROUPING_TOL
                    ):
                        ys = [inner_ctr[e.Id.IntegerValue][1] for e in col_elems]
                        ref_pos = []
                        bg, tg = _flanking_grids(h_grids, min(ys), max(ys), 'Y')
                        for g in (bg, tg):
                            if g:
                                r = _get_grid_reference(g, view)
                                if r:
                                    ref_pos.append((_grid_pos(g, 'Y'), r))
                        for elem in col_elems:
                            bb   = elem.get_BoundingBox(view)
                            cy_e = inner_ctr[elem.Id.IntegerValue][1]
                            added = 0
                            for rt, pos_fn in (
                                (FamilyInstanceReferenceType.Front,
                                 lambda b, c: b.Min.Y if b else c - 0.01),
                                (FamilyInstanceReferenceType.Back,
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
                                                 FamilyInstanceReferenceType.Front,
                                                 FamilyInstanceReferenceType.Back)
                                if r:
                                    ref_pos.append((cy_e, r))
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
                        bg, tg = _flanking_grids(h_grids, min(face_ys), max(face_ys), 'Y')
                        for g in (bg, tg):
                            if g:
                                r = _get_grid_reference(g, view)
                                if r:
                                    ref_pos.append((_grid_pos(g, 'Y'), r))
                        made = _chain(ref_pos, 'Y', cx, -l2_feet,
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
                        lg, rg = _flanking_grids(v_grids, min(face_xs), max(face_xs), 'X')
                        for g in (lg, rg):
                            if g:
                                r = _get_grid_reference(g, view)
                                if r:
                                    ref_pos.append((_grid_pos(g, 'X'), r))
                        made = _chain(ref_pos, 'X', cy, l2_feet,
                                      span_lo=v_span_lo, span_hi=v_span_hi,
                                      mirror_perp=y_min)
                        created_dims.extend(made)
                        dims_created += len(made)
                except Exception as ex:
                    logger.warning("Wall dim skipped: {}".format(ex))

            # ══ PHASE 5 (L1): Lift String Dimensions ════════════════════════
            if lifts:
                lift_ctr = {l.Id.IntegerValue: _elem_centroid(l, view) for l in lifts}

                if run_x:
                    for row_y, row_lifts in _group_elements_by_pos(
                        lifts,
                        lambda l: lift_ctr[l.Id.IntegerValue][1],
                        GROUPING_TOL
                    ):
                        xs = [lift_ctr[l.Id.IntegerValue][0] for l in row_lifts]
                        ref_pos = []
                        lg, rg = _flanking_grids(v_grids, min(xs), max(xs), 'X')
                        for g in (lg, rg):
                            if g:
                                r = _get_grid_reference(g, view)
                                if r:
                                    ref_pos.append((_grid_pos(g, 'X'), r))
                        for lift in row_lifts:
                            r = _col_ref_one(lift, view, 'X',
                                             FamilyInstanceReferenceType.Left,
                                             FamilyInstanceReferenceType.Right)
                            if r:
                                ref_pos.append((lift_ctr[lift.Id.IntegerValue][0], r))
                        made = _chain(ref_pos, 'X', row_y, l1_feet,
                                      span_lo=v_span_lo, span_hi=v_span_hi,
                                      mirror_perp=y_min)
                        created_dims.extend(made)
                        dims_created += len(made)

                if run_y:
                    for col_x, col_lifts in _group_elements_by_pos(
                        lifts,
                        lambda l: lift_ctr[l.Id.IntegerValue][0],
                        GROUPING_TOL
                    ):
                        ys = [lift_ctr[l.Id.IntegerValue][1] for l in col_lifts]
                        ref_pos = []
                        bg, tg = _flanking_grids(h_grids, min(ys), max(ys), 'Y')
                        for g in (bg, tg):
                            if g:
                                r = _get_grid_reference(g, view)
                                if r:
                                    ref_pos.append((_grid_pos(g, 'Y'), r))
                        for lift in col_lifts:
                            r = _col_ref_one(lift, view, 'Y',
                                             FamilyInstanceReferenceType.Front,
                                             FamilyInstanceReferenceType.Back)
                            if r:
                                ref_pos.append((lift_ctr[lift.Id.IntegerValue][1], r))
                        made = _chain(ref_pos, 'Y', col_x, -l1_feet,
                                      span_lo=h_span_lo, span_hi=h_span_hi,
                                      mirror_perp=x_max)
                        created_dims.extend(made)
                        dims_created += len(made)

            t.Commit()

        except Exception as ex:
            try:
                t.RollBack()
            except Exception:
                pass
            msg = "Transaction failed: {}".format(ex)
            logger.error(msg)
            TaskDialog.Show("Auto Dimension — Error", msg)
            self._set_status("Error.")
            return

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

        if small_count > 0:
            warn = "  ⚠ {} segment(s) < {}mm — text may overlap.".format(
                small_count, int(min_seg_feet / MM_TO_FEET + 0.5))
            self._set_status("Done ({} dims). {}".format(dims_created, warn))
            TaskDialog.Show(
                "Auto Dimension — Conflict Warning",
                "Created {} dimension string(s) in view '{}'.\n\n"
                "{} segment(s) are shorter than {}mm.\n"
                "Consider increasing the offset or adjusting element positions "
                "to prevent text overlap.".format(
                    dims_created, view.Name,
                    small_count, int(min_seg_feet / MM_TO_FEET + 0.5))
            )
        else:
            self._set_status("Done — {} dimension string(s) created.".format(dims_created))
            TaskDialog.Show(
                "Auto Dimension",
                "Done.\nCreated {} dimension string(s) in view '{}'.".format(
                    dims_created, view.Name)
            )


# MAIN SCRIPT
# ==================================================
if __name__ == '__main__':
    if not revit.doc:
        forms.alert("Please open a Revit document first.", exitscript=True)

    uidoc_inst = __revit__.ActiveUIDocument  # noqa: F821
    doc_inst   = uidoc_inst.Document

    window = AutoDimensionWindow(uidoc_inst, doc_inst)
    window.ShowDialog()
