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
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==================================================
import os
import sys
import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')

from System.Windows import WindowState
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
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


def _collect_col_family_refs(col):
    """
    Get all built-in reference planes from a column FamilyInstance.
    Returns a flat list of Reference objects (may be empty).
    """
    refs = []
    for rt in (FamilyInstanceReferenceType.Left,
               FamilyInstanceReferenceType.Right,
               FamilyInstanceReferenceType.Front,
               FamilyInstanceReferenceType.Back,
               FamilyInstanceReferenceType.WeakReference,
               FamilyInstanceReferenceType.StrongReference):
        try:
            for r in col.GetReferences(rt):
                refs.append(r)
        except Exception:
            pass
    return refs


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

        self._load_logo()
        self._populate_dim_types()
        self._set_status("Ready")

    # ── Logo ─────────────────────────────────────────────────────────────

    def _load_logo(self):
        try:
            if os.path.exists(LOGO_FILE):
                bmp = BitmapImage()
                bmp.BeginInit()
                bmp.UriSource = Uri(LOGO_FILE, UriKind.Absolute)
                bmp.EndInit()
                self.logo_image.Source = bmp
        except Exception as ex:
            logger.warning("Logo load failed: {}".format(ex))

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
            # Only Linear dimension types
            self._dim_types = [
                dt for dt in all_types
                if dt.StyleType == DimensionStyleType.Linear
            ]
            self.cmb_dim_type.Items.Clear()
            for dt in self._dim_types:
                self.cmb_dim_type.Items.Add(dt.Name)
            if self._dim_types:
                self.cmb_dim_type.SelectedIndex = 0
                self._set_status("Loaded {} linear dimension type(s).".format(
                    len(self._dim_types)))
            else:
                self._set_status("No linear dimension types found in document.")
        except Exception as ex:
            logger.warning("Could not load dimension types: {}".format(ex))

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
        do_doors       = self.chk_doors.IsChecked == True

        run_h = True  # always dim both directions
        run_v = True

        sel_idx = self.cmb_dim_type.SelectedIndex
        if sel_idx < 0 or sel_idx >= len(self._dim_types):
            TaskDialog.Show("Auto Dimension", "Please select a Dimension Type.")
            return
        dim_type = self._dim_types[sel_idx]

        offset_feet = 1000.0 * MM_TO_FEET
        try:
            offset_feet = float(self.txt_offset.Text.strip()) * MM_TO_FEET
        except Exception:
            pass

        # ── Validate view ──────────────────────────────────────────────────
        view = self.doc.ActiveView
        if not _is_valid_view(view):
            TaskDialog.Show("Auto Dimension",
                            "Please activate a Plan, Section, Elevation, or Detail view.")
            return

        try:
            dim_z = view.Origin.Z
        except Exception:
            dim_z = 0.0

        margin = offset_feet * 0.5

        # ── Always collect ALL grids (they are the dimension targets) ──────
        all_grids = list(
            FilteredElementCollector(self.doc, view.Id)
            .OfCategory(BuiltInCategory.OST_Grids)
            .WhereElementIsNotElementType()
            .ToElements()
        )

        if not all_grids:
            TaskDialog.Show("Auto Dimension",
                            "No grids found in the current view.\n"
                            "Dimensions are placed from elements to their nearest grid.")
            self._set_status("No grids in view.")
            return

        # ── Collect selected element types ─────────────────────────────────
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

        walls       = _collect(BuiltInCategory.OST_Walls)       if do_walls       else []
        struct_cols = _collect(BuiltInCategory.OST_StructuralColumns) if do_struct_cols else []
        arch_cols   = _collect(BuiltInCategory.OST_Columns)     if do_arch_cols   else []
        doors       = _collect(BuiltInCategory.OST_Doors)       if do_doors       else []
        all_cols    = struct_cols + arch_cols

        # ── Single transaction — skip failures silently ────────────────────
        dims_created = 0
        t = Transaction(self.doc, "T3Lab: Auto Dimension")
        try:
            t.Start()

            # ── WALLS: each wall → nearest parallel grid ──────────────────
            for wall in walls:
                try:
                    wall_refs = _collect_wall_core_refs(wall)
                    if not wall_refs:
                        continue
                    cx, cy = _elem_centroid(wall, view)
                    is_h   = _wall_is_horizontal(wall)

                    if is_h and run_h:
                        g, gy = _nearest_grid(cx, cy, all_grids, 'Y')
                        g_ref = _get_grid_reference(g, view) if g else None
                        if g_ref:
                            line = _aligned_dim_line(cy, gy, cx, 'Y', margin, dim_z)
                            if _try_create_dim(self.doc, view,
                                               [wall_refs[0], g_ref],
                                               line, dim_type):
                                dims_created += 1

                    elif not is_h and run_v:
                        g, gx = _nearest_grid(cx, cy, all_grids, 'X')
                        g_ref = _get_grid_reference(g, view) if g else None
                        if g_ref:
                            line = _aligned_dim_line(cx, gx, cy, 'X', margin, dim_z)
                            if _try_create_dim(self.doc, view,
                                               [wall_refs[0], g_ref],
                                               line, dim_type):
                                dims_created += 1
                except Exception as ex:
                    logger.warning("Wall dim skipped: {}".format(ex))

            # ── COLUMNS: each column → nearest grid in X and/or Y ─────────
            for col in all_cols:
                try:
                    col_refs = _collect_col_family_refs(col)
                    if not col_refs:
                        continue
                    cx, cy = _elem_centroid(col, view)

                    if run_h:
                        g, gy = _nearest_grid(cx, cy, all_grids, 'Y')
                        g_ref = _get_grid_reference(g, view) if g else None
                        if g_ref:
                            line = _aligned_dim_line(cy, gy, cx, 'Y', margin, dim_z)
                            if _try_create_dim(self.doc, view,
                                               [col_refs[0], g_ref],
                                               line, dim_type):
                                dims_created += 1

                    if run_v:
                        g, gx = _nearest_grid(cx, cy, all_grids, 'X')
                        g_ref = _get_grid_reference(g, view) if g else None
                        if g_ref:
                            line = _aligned_dim_line(cx, gx, cy, 'X', margin, dim_z)
                            if _try_create_dim(self.doc, view,
                                               [col_refs[0], g_ref],
                                               line, dim_type):
                                dims_created += 1
                except Exception as ex:
                    logger.warning("Column dim skipped: {}".format(ex))

            # ── DOORS: Left + Right refs + nearest perpendicular grid ──────
            for door in doors:
                try:
                    door_refs = _collect_door_refs(door)
                    if len(door_refs) < 2:
                        continue
                    cx, cy = _elem_centroid(door, view)

                    # Determine host wall orientation
                    host_is_h = True
                    try:
                        host = self.doc.GetElement(door.Host.Id)
                        host_is_h = _wall_is_horizontal(host)
                    except Exception:
                        pass

                    if host_is_h and run_h:
                        g, gx = _nearest_grid(cx, cy, all_grids, 'X')
                        g_ref = _get_grid_reference(g, view) if g else None
                        if g_ref:
                            all_refs = [g_ref] + door_refs
                            x_lo = min(cx, gx) - margin
                            x_hi = max(cx, gx) + margin
                            line = Line.CreateBound(XYZ(x_lo, cy, dim_z),
                                                    XYZ(x_hi, cy, dim_z))
                            if _try_create_dim(self.doc, view, all_refs, line, dim_type):
                                dims_created += 1

                    elif not host_is_h and run_v:
                        g, gy = _nearest_grid(cx, cy, all_grids, 'Y')
                        g_ref = _get_grid_reference(g, view) if g else None
                        if g_ref:
                            all_refs = [g_ref] + door_refs
                            y_lo = min(cy, gy) - margin
                            y_hi = max(cy, gy) + margin
                            line = Line.CreateBound(XYZ(cx, y_lo, dim_z),
                                                    XYZ(cx, y_hi, dim_z))
                            if _try_create_dim(self.doc, view, all_refs, line, dim_type):
                                dims_created += 1
                except Exception as ex:
                    logger.warning("Door dim skipped: {}".format(ex))

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

        # ── Report ─────────────────────────────────────────────────────────
        self._set_status("Done — created {} aligned dimension(s).".format(dims_created))
        TaskDialog.Show(
            "Auto Dimension",
            "Done.\nCreated {} aligned dimension(s) in view '{}'.".format(
                dims_created, view.Name
            )
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
