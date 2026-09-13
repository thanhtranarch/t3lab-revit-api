#! python3
# -*- coding: utf-8 -*-
"""
Point Cloud to Model
--------------------
Scan-to-BIM wizard: extract point cloud data, detect the architectural
and structural shell — Walls, Floors, Ceilings, Doors, Windows, Columns,
Stairs, Roof — then create all elements in a single TransactionGroup.
MEP content (ducts, pipes, equipment) is deliberately OUT of scope:
walls require tall evidence below the ceiling (rejects shelving/racks/
low MEP runs) and columns must be near-square, >= 200 mm and rise above
furniture height (rejects pipes, flat ducts and floor-mounted units).

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__title__   = "Point Cloud\nto Model"
__author__  = "Tran Tien Thanh"
__version__  = "1.0.0"

# IMPORT LIBRARIES
# ==============================================================================
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
import math

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System import Action
from System.Windows import WindowState, Visibility
from System.Windows.Threading import DispatcherPriority
from System.Collections.Generic import List

from pyrevit import revit, DB
from Autodesk.Revit.DB import (
    Transaction,
    TransactionGroup,
    FilteredElementCollector,
    BuiltInCategory,
    BuiltInParameter,
    WallType,
    Wall,
    Level,
    Grid,
    FloorType,
    Floor,
    XYZ,
    Plane,
    Line,
    CurveLoop,
    CurveArray,
    BoundingBoxXYZ,
    PointCloudInstance,
    IFailuresPreprocessor,
    FailureProcessingResult,
    FailureSeverity,
    ElementId,
    FamilySymbol,
    DetailLine,
    ElementTransformUtils
)
try:
    from Autodesk.Revit.DB import StructuralType
except ImportError:
    from Autodesk.Revit.DB.Structure import StructuralType

from Autodesk.Revit.DB.PointClouds import PointCloudFilterFactory
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter, PickBoxStyle
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import forms, script
from GUI.WPF_Base import T3WPFWindow, to_items_source

# Path setup
# ==============================================================================
SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))))
lib_dir    = os.path.join(EXT_DIR, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

XAML_FILE  = os.path.join(os.path.dirname(__file__), 'Tools', 'PointCloud.xaml')

# DEFINE VARIABLES
# ==============================================================================
logger        = script.get_logger()
# Read from the Application, not the document: `revit.doc` is None when this
# tool is launched from the Assistant pane or with no project open, and the
# old `int(revit.doc.Application.VersionNumber)` raised at IMPORT time
# ('NoneType' object has no attribute 'Application') so the window never opened.
from Snippets._host import get_revit_version, resolve_doc, resolve_uidoc, host_uiapp
REVIT_VERSION = get_revit_version()

# Same null-document trap, one layer deeper: `revit.doc` is None from the
# Assistant pane, and ElementBuilder runs a FilteredElementCollector in
# PointCloudModelWindow.__init__ — a bare `revit.doc` took the window down
# before it ever rendered. resolve_doc() is the repo-wide fallback chain and
# hands back an actionable message instead of a null-document crash.
doc, DOC_ERROR = resolve_doc()

_resolve_uidoc = resolve_uidoc
uidoc = resolve_uidoc()

# CLASS / FUNCTIONS
# ==============================================================================

# ── Section 1: Unit Helpers ────────────────────────────────────────────────────

MM_PER_FOOT = 304.8

def ft_to_mm(ft):
    return ft * MM_PER_FOOT

def mm_to_ft(mm):
    return mm / MM_PER_FOOT

def internal_to_mm(value_ft):
    """Convert Revit internal (feet) to millimetres, version-safe."""
    try:
        if REVIT_VERSION >= 2022:
            from Autodesk.Revit.DB import UnitUtils, UnitTypeId
            return UnitUtils.ConvertFromInternalUnits(value_ft, UnitTypeId.Millimeters)
        else:
            from Autodesk.Revit.DB import UnitUtils, DisplayUnitType
            return UnitUtils.ConvertFromInternalUnits(value_ft, DisplayUnitType.DUT_MILLIMETERS)
    except Exception:
        return value_ft * MM_PER_FOOT

def mm_to_internal(value_mm):
    """Convert millimetres to Revit internal (feet), version-safe."""
    try:
        if REVIT_VERSION >= 2022:
            from Autodesk.Revit.DB import UnitUtils, UnitTypeId
            return UnitUtils.ConvertToInternalUnits(value_mm, UnitTypeId.Millimeters)
        else:
            from Autodesk.Revit.DB import UnitUtils, DisplayUnitType
            return UnitUtils.ConvertToInternalUnits(value_mm, DisplayUnitType.DUT_MILLIMETERS)
    except Exception:
        return value_mm / MM_PER_FOOT


# ── Section 2: Point Cloud Extraction ─────────────────────────────────────────

import uuid

class PointCloudSelectionFilter(ISelectionFilter):
    __namespace__ = "T3Lab.PointCloud"
    def AllowElement(self, element):
        return isinstance(element, PointCloudInstance)

    def AllowReference(self, reference, position):
        return False


def build_cloud_filter(pc_instance, center_pt, half_x_ft, half_y_ft, half_z_ft):
    """
    Six-plane axis-aligned box filter. The box is defined in MODEL space
    (center + half extents) but the Revit point cloud engine evaluates the
    filter in the CLOUD's local coordinate space, so every plane is mapped
    through the inverse of the instance's total transform.
    Each plane's normal points into the box interior — points inside all
    six planes pass the filter (CreateMultiPlaneFilter expects IList<Plane>).
    """
    x, y, z = center_pt.X, center_pt.Y, center_pt.Z
    faces = [
        (XYZ(-1.0, 0.0, 0.0), XYZ(x + half_x_ft, y, z)),   # +X face, inward -X
        (XYZ( 1.0, 0.0, 0.0), XYZ(x - half_x_ft, y, z)),   # -X face, inward +X
        (XYZ(0.0, -1.0, 0.0), XYZ(x, y + half_y_ft, z)),   # +Y face, inward -Y
        (XYZ(0.0,  1.0, 0.0), XYZ(x, y - half_y_ft, z)),   # -Y face, inward +Y
        (XYZ(0.0, 0.0, -1.0), XYZ(x, y, z + half_z_ft)),   # +Z face, inward -Z
        (XYZ(0.0, 0.0,  1.0), XYZ(x, y, z - half_z_ft)),   # -Z face, inward +Z
    ]
    inv = pc_instance.GetTotalTransform().Inverse
    planes = List[Plane]()
    for normal, origin in faces:
        n = inv.OfVector(normal).Normalize()
        planes.Add(Plane.CreateByNormalAndOrigin(n, inv.OfPoint(origin)))
    return PointCloudFilterFactory.CreateMultiPlaneFilter(planes)


def _read_points_to_model(pc_instance, raw):
    """
    Copy a PointCollection into a plain list of model-space (x, y, z) tuples.
    GetPoints returns points in the cloud's local space backed by engine
    memory — copy immediately and Dispose so the buffer is never touched
    after the engine invalidates it.
    """
    tf  = pc_instance.GetTotalTransform()
    pts = []
    try:
        for cp in raw:
            p = tf.OfPoint(XYZ(cp.X, cp.Y, cp.Z))
            pts.append((p.X, p.Y, p.Z))
    finally:
        try:
            raw.Dispose()
        except Exception:
            pass
    return pts


def _extract_tile(pc_instance, center, hx, hy, hz, budget):
    """
    One conservative GetPoints call for a sub-box.
    averageDistance is clamped to 1.5–100 mm: the naive volume/budget estimate
    explodes to metres on big site scans, and oversized queries have been seen
    to hard-crash the ReCap engine in Revit 2023 (uncatchable native
    AccessViolation inside AdskRcPointCloudEngine.dll, journal 0xe0434352).
    """
    try:
        vol = (2.0 * hx) * (2.0 * hy) * (2.0 * hz)
        est = (vol / max(budget, 1)) ** (1.0 / 3.0)
        avg_dist = min(max(0.005, est), mm_to_ft(100.0))
        pcf = build_cloud_filter(pc_instance, center, hx, hy, hz)
        raw = pc_instance.GetPoints(pcf, avg_dist, budget)
        return _read_points_to_model(pc_instance, raw)
    except Exception as ex:
        logger.debug("_extract_tile: {}".format(ex))
        return []


def _extract_region(pc_instance, center, hx, hy, hz, density_cap,
                    progress_cb=None):
    """
    Extract up to density_cap points from an axis-aligned model-space box,
    split into an adaptive grid of XY sub-boxes (target tile ~8 m, 2x2 up to
    6x6). Many small engine queries spread the sample spatially (GetPoints
    returns points in page order, so one big capped request clusters in a
    corner) and are far gentler on the point cloud engine than a single
    giant request. Each tile is isolated — one failing tile costs only its
    own points.
    """
    target_tile = mm_to_ft(8000.0)
    tx = int(max(2, min(6, math.ceil((2.0 * hx) / target_tile))))
    ty = int(max(2, min(6, math.ceil((2.0 * hy) / target_tile))))
    total  = tx * ty
    budget = max(300, int(density_cap / float(total)))

    pts   = []
    empty = 0
    n     = 0
    for i in range(tx):
        for j in range(ty):
            n += 1
            if progress_cb:
                try:
                    progress_cb(u"Extracting tile {}/{} — {} points so far…"
                                .format(n, total, len(pts)))
                except Exception:
                    pass
            sub_cx = center.X - hx + (2.0 * i + 1.0) * hx / tx
            sub_cy = center.Y - hy + (2.0 * j + 1.0) * hy / ty
            sub = _extract_tile(
                pc_instance, XYZ(sub_cx, sub_cy, center.Z),
                hx / tx, hy / ty, hz, budget)
            if not sub:
                empty += 1
            pts.extend(sub)
    if empty:
        logger.debug("_extract_region: {}/{} tiles empty or failed".format(
            empty, total))
    return pts


def extract_full_cloud(pc_instance, density_cap, progress_cb=None):
    """
    Extract up to density_cap points from the full bounding box of pc_instance.
    Returns list of model-space (x, y, z) tuples in feet. Returns [] on error.
    """
    try:
        bbox = pc_instance.get_BoundingBox(None)
        if bbox is None:
            return []
        cx = (bbox.Min.X + bbox.Max.X) / 2.0
        cy = (bbox.Min.Y + bbox.Max.Y) / 2.0
        cz = (bbox.Min.Z + bbox.Max.Z) / 2.0
        hx = (bbox.Max.X - bbox.Min.X) / 2.0 + 0.5
        hy = (bbox.Max.Y - bbox.Min.Y) / 2.0 + 0.5
        hz = (bbox.Max.Z - bbox.Min.Z) / 2.0 + 0.5
        return _extract_region(pc_instance, XYZ(cx, cy, cz), hx, hy, hz,
                               density_cap, progress_cb)
    except Exception as ex:
        import traceback
        logger.error("extract_full_cloud: {}\n{}".format(ex, traceback.format_exc()))
        return []


def extract_full_cloud_from_region(pc_instance, center, hx, hy, hz,
                                   density_cap, progress_cb=None):
    """
    Extract up to density_cap points from a user-defined axis-aligned region.
    center is a model-space XYZ; hx/hy/hz are half-extents in feet.
    Returns list of model-space (x, y, z) tuples. Returns [] on error.
    """
    try:
        return _extract_region(pc_instance, center, hx, hy, hz, density_cap,
                               progress_cb)
    except Exception as ex:
        import traceback
        logger.error("extract_full_cloud_from_region: {}\n{}".format(ex, traceback.format_exc()))
        return []


# ── Section 3: Geometry Math (pure Python) ────────────────────────────────────

def fit_line_2d(pts_xy):
    """
    Principal-axis LSR on 2D points.
    Returns (angle_rad, cx, cy) or None if fewer than 10 points.
    """
    if len(pts_xy) < 10:
        return None
    xs = [p[0] for p in pts_xy]
    ys = [p[1] for p in pts_xy]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    syy = sum((y - my) ** 2 for y in ys)
    angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    return angle, mx, my


def line_thickness_2d(pts_xy, angle, cx, cy):
    """97.5th–2.5th percentile span on the wall-normal axis (in feet)."""
    if not pts_xy:
        return 0.0
    nx = -math.sin(angle)
    ny =  math.cos(angle)
    proj = sorted((p[0] - cx) * nx + (p[1] - cy) * ny for p in pts_xy)
    n = len(proj)
    lo = int(round(0.025 * n))
    hi = int(round(0.975 * n)) - 1
    if hi <= lo:
        return abs(proj[-1] - proj[0])
    return abs(proj[hi] - proj[lo])


def line_length_2d(pts_xy, angle, cx, cy, percentile_lo=0.01, percentile_hi=0.99):
    """99th–1st percentile span along the wall direction (in feet)."""
    if not pts_xy:
        return 0.0
    dx = math.cos(angle)
    dy = math.sin(angle)
    proj = sorted((p[0] - cx) * dx + (p[1] - cy) * dy for p in pts_xy)
    n = len(proj)
    lo = int(round(percentile_lo * n))
    hi = int(round(percentile_hi * n)) - 1
    if hi <= lo:
        return abs(proj[-1] - proj[0])
    return abs(proj[hi] - proj[lo])


def cluster_2d(pts_xy, cell_size_ft, min_pts_cell=2, min_cluster_size=20, reach=2):
    """
    Groups nearby 2D points into clusters using grid cells + BFS connected
    components. reach is the neighbour radius in cells — sampled clouds often
    space points wider than one cell, so strict 8-connectivity would shatter
    every wall into sub-threshold fragments.
    Returns list of clusters; each cluster is a list of (x, y).
    """
    grid = {}
    for (x, y) in pts_xy:
        key = (int(x / cell_size_ft), int(y / cell_size_ft))
        grid.setdefault(key, []).append((x, y))

    dense = {k: v for k, v in grid.items() if len(v) >= min_pts_cell}
    visited = set()
    clusters = []

    for cell in list(dense.keys()):
        if cell in visited:
            continue
        cluster_pts = []
        queue = [cell]
        while queue:
            k = queue.pop()
            if k in visited:
                continue
            visited.add(k)
            cluster_pts.extend(dense.get(k, []))
            ck, cl = k
            for dk in range(-reach, reach + 1):
                for dl in range(-reach, reach + 1):
                    nb = (ck + dk, cl + dl)
                    if nb in dense and nb not in visited:
                        queue.append(nb)
        if len(cluster_pts) >= min_cluster_size:
            clusters.append(cluster_pts)

    return clusters


def z_histogram(all_pts, bin_mm=50.0):
    """
    Builds a Z-value histogram from a list of (x, y, z) tuples.
    Returns (hist dict, z_min_ft, bin_size_ft).
    """
    if not all_pts:
        return {}, 0.0, 0.0
    z_vals = [p[2] for p in all_pts]
    z_min = min(z_vals)
    bin_ft = bin_mm / 304.8
    hist = {}
    for z in z_vals:
        bi = int((z - z_min) / bin_ft)
        hist[bi] = hist.get(bi, 0) + 1
    return hist, z_min, bin_ft


def find_histogram_peaks(hist, z_min, bin_ft, min_ratio=0.05):
    """
    Returns list of (z_ft, count) for local maxima above min_ratio of the
    global max count. Also includes the first bin if it qualifies.
    """
    if not hist:
        return []
    max_count = max(hist.values())
    threshold = max_count * min_ratio
    keys = sorted(hist.keys())
    peaks = []
    for i in range(1, len(keys) - 1):
        k = keys[i]
        c = hist.get(k, 0)
        prev_c = hist.get(keys[i - 1], 0)
        next_c = hist.get(keys[i + 1], 0)
        if c >= threshold and c >= prev_c and c >= next_c:
            z_ft = z_min + (k + 0.5) * bin_ft
            peaks.append((z_ft, c))
    # Boundary bins never enter the local-max loop — include them explicitly.
    # The first bin holds the floor, the last bin holds the ceiling.
    if keys:
        k0 = keys[0]
        if hist.get(k0, 0) >= threshold:
            peaks.append((z_min + (k0 + 0.5) * bin_ft, hist[k0]))
        kn = keys[-1]
        if kn != k0 and hist.get(kn, 0) >= threshold:
            peaks.append((z_min + (kn + 0.5) * bin_ft, hist[kn]))
    return sorted(peaks, key=lambda p: p[0])


# ── Section 4: DetectedElement Data Classes ────────────────────────────────────

class DetectedElement(object):
    """Base class bound to the DataGrid in the WPF results view."""

    def __init__(self, elem_type, level_name, dimensions_str, confidence, data):
        self.Type           = elem_type
        self.LevelName      = level_name
        self.Dimensions     = dimensions_str
        self.ConfidenceText = u"{}%".format(confidence)
        self.Include        = True
        self._data          = data


class DetectedWall(DetectedElement):
    def __init__(self, angle, cx, cy, length_ft, thickness_ft, level_name,
                 snapped, snap_desc, base_z=0.0):
        dims = u"L={:.0f} mm  T={:.0f} mm".format(ft_to_mm(length_ft), ft_to_mm(thickness_ft))
        super(DetectedWall, self).__init__(
            'Wall', level_name, dims, 85 if snapped else 70,
            {
                'angle': angle, 'cx': cx, 'cy': cy,
                'length_ft': length_ft, 'thickness_ft': thickness_ft,
                'base_z': base_z,
            }
        )


class DetectedFloor(DetectedElement):
    def __init__(self, z_ft, corners_xy, surface_type, level_name):
        w = abs(corners_xy[1][0] - corners_xy[0][0])
        h = abs(corners_xy[2][1] - corners_xy[0][1])
        area_m2 = (ft_to_mm(w) / 1000.0) * (ft_to_mm(h) / 1000.0)
        dims = u"~{:.1f} m²  @Z={:.0f} mm".format(area_m2, ft_to_mm(z_ft))
        elem_type = 'Ceiling' if surface_type == 'ceiling' else 'Floor'
        super(DetectedFloor, self).__init__(
            elem_type, level_name, dims, 80,
            {'z_ft': z_ft, 'corners_xy': corners_xy, 'surface_type': surface_type}
        )


class DetectedColumn(DetectedElement):
    def __init__(self, cx, cy, width_ft, depth_ft, z_bot_ft, z_top_ft, level_name):
        dims = u"W={:.0f} D={:.0f} H={:.0f} mm".format(
            ft_to_mm(width_ft), ft_to_mm(depth_ft), ft_to_mm(z_top_ft - z_bot_ft))
        super(DetectedColumn, self).__init__(
            'Column', level_name, dims, 65,
            {
                'cx': cx, 'cy': cy,
                'width_ft': width_ft, 'depth_ft': depth_ft,
                'z_bot_ft': z_bot_ft, 'z_top_ft': z_top_ft,
            }
        )


class DetectedOpening(DetectedElement):
    """Door or Window opening detected in a wall."""

    def __init__(self, elem_type, host_wall_data, u_center, w_bottom, width_ft, height_ft, level_name):
        dims = u"W={:.0f} H={:.0f} mm".format(ft_to_mm(width_ft), ft_to_mm(height_ft))
        super(DetectedOpening, self).__init__(
            elem_type, level_name, dims, 60,
            {
                'host_wall_data': host_wall_data,
                'u_center': u_center, 'w_bottom': w_bottom,
                'width_ft': width_ft, 'height_ft': height_ft,
            }
        )


class DetectedStair(DetectedElement):
    def __init__(self, z_bot_ft, z_top_ft, tread_count, level_name):
        dims = u"{} treads  Rise={:.0f}–{:.0f} mm".format(
            tread_count, ft_to_mm(z_bot_ft), ft_to_mm(z_top_ft))
        super(DetectedStair, self).__init__(
            'Stair', level_name, dims, 55,
            {'z_bot_ft': z_bot_ft, 'z_top_ft': z_top_ft, 'tread_count': tread_count}
        )


class DetectedRoof(DetectedElement):
    def __init__(self, z_ft, corners_xy, slope_deg, level_name):
        dims = u"Slope={:.1f}°  @Z={:.0f} mm".format(slope_deg, ft_to_mm(z_ft))
        super(DetectedRoof, self).__init__(
            'Roof', level_name, dims, 60,
            {'z_ft': z_ft, 'corners_xy': corners_xy, 'slope_deg': slope_deg}
        )


# ── Section 5: PointCloudAnalyzer ─────────────────────────────────────────────

class PointCloudAnalyzer(object):
    """Runs all detection algorithms on extracted point cloud data."""

    RISER_TYPICAL_MM = (140.0, 200.0)

    XY_CELL_FT = 1000.0 / 304.8   # 1 m spatial-index cell

    def __init__(self, all_pts):
        self.pts    = all_pts
        self._z_min = min(p[2] for p in all_pts) if all_pts else 0.0
        self._z_max = max(p[2] for p in all_pts) if all_pts else 0.0
        self._x_min = min(p[0] for p in all_pts) if all_pts else 0.0
        self._x_max = max(p[0] for p in all_pts) if all_pts else 0.0
        self._y_min = min(p[1] for p in all_pts) if all_pts else 0.0
        self._y_max = max(p[1] for p in all_pts) if all_pts else 0.0
        self._grid_angles = self._load_grid_angles()
        self._xy_index    = None   # built lazily by _points_near
        self._footprint_cells = None   # built lazily (500 mm occupancy)
        self._levels_cache    = None   # built lazily by _all_levels

    def _points_near(self, cx, cy, ex, ey):
        """
        Points within the axis-aligned XY window (cx±ex, cy±ey), via a lazily
        built 1 m grid index. Bounds per-wall scans to nearby points instead
        of the whole cloud (O(walls x cloud) freezes the UI on big extracts).
        """
        if self._xy_index is None:
            grid = {}
            cell = self.XY_CELL_FT
            for p in self.pts:
                key = (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)))
                grid.setdefault(key, []).append(p)
            self._xy_index = grid
        cell = self.XY_CELL_FT
        gx_lo = int(math.floor((cx - ex) / cell))
        gx_hi = int(math.floor((cx + ex) / cell))
        gy_lo = int(math.floor((cy - ey) / cell))
        gy_hi = int(math.floor((cy + ey) / cell))
        out = []
        for gx in range(gx_lo, gx_hi + 1):
            for gy in range(gy_lo, gy_hi + 1):
                out.extend(self._xy_index.get((gx, gy), ()))
        return out

    def _load_grid_angles(self):
        angles = [0.0, math.pi / 2.0]
        try:
            grids = FilteredElementCollector(doc).OfClass(Grid).ToElements()
            for g in grids:
                curve = g.Curve
                if curve is None:
                    continue
                d = curve.Direction
                a = math.atan2(d.Y, d.X) % math.pi
                angles.append(a)
        except Exception:
            pass
        return angles

    def _snap_angle(self, angle_rad, tol_deg=1.0):
        tol = math.radians(tol_deg)
        a = angle_rad % math.pi
        for ref in self._grid_angles:
            r = ref % math.pi
            for cand in [r, (r + math.pi / 2.0) % math.pi]:
                diff = abs(a - cand)
                if diff > math.pi / 2.0:
                    diff = math.pi - diff
                if diff <= tol:
                    return cand, True, u"Snapped {:.1f}°".format(math.degrees(cand))
        return a, False, u"{:.2f}°".format(math.degrees(a))

    def _all_levels(self):
        """Project levels, queried once. _best_level runs per detected element,
        so a fresh FilteredElementCollector sweep each call turned level
        lookup into the dominant cost of a large detection run."""
        if self._levels_cache is None:
            try:
                self._levels_cache = list(
                    FilteredElementCollector(doc).OfClass(Level).ToElements())
            except Exception:
                self._levels_cache = []
        return self._levels_cache

    def _best_level(self, z_ft):
        """Find the closest Level element at or below z_ft."""
        levels = self._all_levels()
        best      = None
        best_diff = float('inf')
        for lv in levels:
            diff = z_ft - lv.Elevation
            if 0.0 <= diff < best_diff:
                best_diff = diff
                best = lv
        if best is None and levels:
            best = sorted(levels, key=lambda l: abs(l.Elevation - z_ft))[0]
        return best

    # ── Wall detection ─────────────────────────────────────────────────────────

    def detect_walls(self, snap_tol_deg=1.0, min_length_mm=500.0, z_bin_mm=50.0):
        """
        Axis-sweep wall detection. For each detected floor level, take a
        horizontal band at floor_z + 0.9–1.5 m, then for every candidate wall
        direction (project grids + orthogonal axes) histogram the points
        along the perpendicular offset: dense offset bands are wall lines.
        Each band is split into contiguous runs along the wall direction.
        This survives corner-connected wall rings that defeat blob clustering.
        Returns list of DetectedWall.
        """
        results = []
        min_length_ft = mm_to_ft(min_length_mm)
        max_thickness = mm_to_ft(800.0)

        hist, z_min_h, bin_ft = z_histogram(self.pts, z_bin_mm)
        peaks = find_histogram_peaks(hist, z_min_h, bin_ft, min_ratio=0.25)
        floor_zs = [p[0] for p in peaks if p[0] < (self._z_max - mm_to_ft(300.0))]
        if not floor_zs:
            floor_zs = [self._z_min]

        # Candidate wall directions: 0°/90° plus every project grid direction
        # (and its perpendicular), deduplicated within 2°
        axis_angles = []
        for a in self._grid_angles:
            for cand in (a % math.pi, (a + math.pi / 2.0) % math.pi):
                if not any(abs(cand - e) < math.radians(2.0) for e in axis_angles):
                    axis_angles.append(cand)

        seen_walls = []  # (cx, cy, angle) for deduplication

        for fz in floor_zs:
            scan_z = fz + mm_to_ft(1200.0)
            if scan_z > self._z_max:
                continue
            dz = mm_to_ft(300.0)
            slice_xy = [(p[0], p[1]) for p in self.pts if abs(p[2] - scan_z) <= dz]
            if len(slice_xy) < 30:
                continue

            # This storey's ceiling estimate — the lowest histogram peak
            # comfortably above the floor; used to bound the tall-evidence
            # band below the ceiling plane
            local_ceils = [p[0] for p in peaks if p[0] > fz + mm_to_ft(2000.0)]
            ceil_est = min(local_ceils) if local_ceils else self._z_max
            tall_lo  = fz + mm_to_ft(2200.0)
            tall_hi  = min(fz + mm_to_ft(4000.0), ceil_est - mm_to_ft(300.0))

            lv = self._best_level(fz)
            level_name = lv.Name if lv else u"Level 1"

            # Each slice point may belong to one wall only. Orthogonal axes
            # are swept first (0°/90° head the list), so oblique grid sweeps
            # cannot re-consume points of walls already found.
            claimed = set()

            for angle in axis_angles:
                dx, dy = math.cos(angle), math.sin(angle)
                nx, ny = -dy, dx

                # Histogram of perpendicular offsets, 50 mm bins
                bin_n = mm_to_ft(50.0)
                off_bins = {}
                for idx, (x, y) in enumerate(slice_xy):
                    bi = int(math.floor((x * nx + y * ny) / bin_n))
                    off_bins.setdefault(bi, []).append((x, y, idx))

                counts = dict((k, len(v)) for k, v in off_bins.items())
                if not counts:
                    continue
                thresh = max(10, max(counts.values()) * 0.25)

                # Merge consecutive dense bins into thickness bands.
                # The gap tolerance must span a whole wall, not just sampling
                # noise: a wall scanned from both sides gives TWO dense faces
                # with an EMPTY core between them (no points inside solid
                # masonry). A 100 mm tolerance split every partition thicker
                # than ~150 mm into two parallel walls, and each half-band
                # then measured ~0 thickness and fell back to the 75 mm floor
                # clamp — so every generated wall also got the thinnest wall
                # type in the project. Merging across max_thickness lets both
                # faces land in one band; the 900 mm band-width guard below
                # still throws out genuine oblique smears.
                max_gap_bins = max(2, int(max_thickness / bin_n))
                bands, band = [], []
                for k in sorted(counts.keys()):
                    if counts[k] >= thresh:
                        if band and k - band[-1] > max_gap_bins:
                            bands.append(band)
                            band = []
                        band.append(k)
                if band:
                    bands.append(band)

                for band_keys in bands:
                    # A wall band is at most ~900 mm wide; wider bands are the
                    # smear of orthogonal walls swept at an oblique grid angle
                    if (band_keys[-1] - band_keys[0] + 1) * bin_n > mm_to_ft(900.0):
                        continue
                    band_pts = []
                    for k in band_keys:
                        band_pts.extend(off_bins[k])
                    if len(band_pts) < 20:
                        continue

                    # Split along the wall direction on gaps > 2 m.
                    # Door/window voids (~0.9–1.8 m) must stay INSIDE one run
                    # so detect_openings can find them in the host wall.
                    proj = sorted((p[0] * dx + p[1] * dy, p) for p in band_pts)
                    gap_split = mm_to_ft(2000.0)
                    runs, run = [], [proj[0]]
                    for item in proj[1:]:
                        if item[0] - run[-1][0] > gap_split:
                            runs.append(run)
                            run = []
                        run.append(item)
                    runs.append(run)

                    for r in runs:
                        if len(r) < 20:
                            continue
                        length_ft = r[-1][0] - r[0][0]
                        if length_ft < min_length_ft:
                            continue
                        seg_pts = [p for _u, p in r]

                        # Skip runs mostly built from already-claimed points
                        seg_ids = set(p[2] for p in seg_pts)
                        if claimed and len(seg_ids & claimed) > 0.5 * len(seg_ids):
                            continue

                        cx = sum(p[0] for p in seg_pts) / len(seg_pts)
                        cy = sum(p[1] for p in seg_pts) / len(seg_pts)

                        # Architectural-shell check: a real wall keeps points
                        # above furniture height (2.2 m) and below the ceiling
                        # plane; shelving, racks and low MEP runs do not.
                        # Only the MIDDLE HALF of the run counts — corner
                        # slivers of perpendicular walls at the run ends must
                        # not vouch for a furniture row bridged between them.
                        if tall_hi > tall_lo:
                            half_u = length_ft / 2.0
                            vtol   = max(mm_to_ft(150.0),
                                         (band_keys[-1] - band_keys[0] + 1) * bin_n)
                            exw = abs(dx) * half_u + abs(nx) * vtol + self.XY_CELL_FT
                            eyw = abs(dy) * half_u + abs(ny) * vtol + self.XY_CELL_FT
                            tall   = 0
                            levels = set()
                            z_bin  = mm_to_ft(100.0)
                            for p in self._points_near(cx, cy, exw, eyw):
                                if not (tall_lo <= p[2] <= tall_hi):
                                    continue
                                rx, ry = p[0] - cx, p[1] - cy
                                if (abs(rx * dx + ry * dy) <= half_u * 0.5
                                        and abs(rx * nx + ry * ny) <= vtol):
                                    tall += 1
                                    levels.add(int(math.floor(p[2] / z_bin)))
                                    if tall >= 10 and len(levels) >= 3:
                                        break
                            # >= 3 distinct heights: the single top edge of a
                            # tall cabinet must not read as wall evidence
                            if tall < 10 or len(levels) < 3:
                                continue
                        thickness_ft = line_thickness_2d(seg_pts, angle, cx, cy)
                        if thickness_ft > max_thickness:
                            continue
                        thickness_ft = max(thickness_ft, mm_to_ft(75.0))
                        # Aspect >= 5: real walls are long and thin; oblique
                        # sweep artifacts land around 3–4
                        if length_ft < thickness_ft * 5.0:
                            continue

                        # Dedup: same position AND same direction
                        too_close = False
                        for (ex_cx, ex_cy, ex_a) in seen_walls:
                            da = abs(((angle - ex_a) + math.pi / 2.0) % math.pi
                                     - math.pi / 2.0)
                            if (da < math.radians(5.0)
                                    and math.sqrt((cx - ex_cx) ** 2
                                                  + (cy - ex_cy) ** 2) < mm_to_ft(200.0)):
                                too_close = True
                                break
                        if too_close:
                            continue
                        seen_walls.append((cx, cy, angle))
                        claimed |= seg_ids

                        results.append(
                            DetectedWall(angle, cx, cy, length_ft, thickness_ft,
                                         level_name, True,
                                         u"{:.1f}°".format(math.degrees(angle)),
                                         base_z=fz)
                        )

        return results

    # ── Horizontal surface detection ───────────────────────────────────────────

    def detect_horizontal_surfaces(self, z_bin_mm=50.0, min_area_m2=1.0,
                                   detected_walls=None):
        """Detect floors and ceilings via Z-histogram peak analysis."""
        results = []
        hist, z_min_h, bin_ft = z_histogram(self.pts, z_bin_mm)
        # 0.25 ratio: a slab slice holds a large share of the points; anything
        # thinner (wall bands, furniture) must not spawn phantom floors
        peaks = find_histogram_peaks(hist, z_min_h, bin_ft, min_ratio=0.25)
        if not peaks:
            return results

        total = len(peaks)
        for idx, (z_ft, _count) in enumerate(peaks):
            # topmost peak is the ceiling — unless it is the only peak,
            # in which case it can only be the floor
            stype = 'ceiling' if (total > 1 and idx == total - 1) else 'floor'
            dz = bin_ft / 2.0
            slice_xy = [(p[0], p[1]) for p in self.pts if abs(p[2] - z_ft) <= dz]
            if len(slice_xy) < 20:
                continue

            min_x = min(p[0] for p in slice_xy)
            max_x = max(p[0] for p in slice_xy)
            min_y = min(p[1] for p in slice_xy)
            max_y = max(p[1] for p in slice_xy)

            width_ft = max_x - min_x
            depth_ft = max_y - min_y
            area_m2  = (ft_to_mm(width_ft) / 1000.0) * (ft_to_mm(depth_ft) / 1000.0)
            if area_m2 < min_area_m2:
                continue

            # Furniture guard: an INTERMEDIATE slab must cover a solid share
            # of the scanned footprint. Desk/table/counter tops and shelf
            # caps all peak at one height but only blanket a fraction of the
            # plan; real floors and ceilings blanket most of it. The lowest
            # and highest peaks are exempt (nothing scans below the floor or
            # above the ceiling).
            if 0 < idx < total - 1:
                cell = mm_to_ft(500.0)
                # Wall rows at this height must not vouch for a furniture
                # slab — only interior coverage counts
                interior = slice_xy
                if detected_walls:
                    interior = [
                        (x, y) for (x, y) in slice_xy
                        if not self._near_detected_wall(
                            x, y, detected_walls, mm_to_ft(300.0))]
                slab_cells = set()
                for (x, y) in interior:
                    slab_cells.add((int(math.floor(x / cell)),
                                    int(math.floor(y / cell))))
                if self._footprint_cells is None:
                    cells = set()
                    for p in self.pts:
                        cells.add((int(math.floor(p[0] / cell)),
                                   int(math.floor(p[1] / cell))))
                    self._footprint_cells = max(1, len(cells))
                if len(slab_cells) < 0.3 * self._footprint_cells:
                    continue

            corners = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]
            lv = self._best_level(z_ft)
            level_name = lv.Name if lv else u""
            results.append(DetectedFloor(z_ft, corners, stype, level_name))

        return results

    # ── Column detection ───────────────────────────────────────────────────────

    def _near_detected_wall(self, cx, cy, walls, margin_ft):
        """True if (cx, cy) lies on/near any detected wall segment."""
        for w in walls:
            d = w._data
            ang = d['angle']
            dx, dy = math.cos(ang), math.sin(ang)
            rx, ry = cx - d['cx'], cy - d['cy']
            u = rx * dx + ry * dy
            if abs(u) > d['length_ft'] / 2.0 + margin_ft:
                continue
            v = abs(-rx * dy + ry * dx)
            if v <= d['thickness_ft'] / 2.0 + margin_ft:
                return True
        return False

    def detect_columns(self, min_size_mm=100.0, max_size_mm=600.0,
                       detected_walls=None):
        """
        Find vertical clusters that are small in XY (column cross-section)
        but tall in Z (spanning at least 40% of room height). Clusters lying
        on a detected wall line are wall fragments, not columns.
        """
        results = []
        min_ft      = mm_to_ft(min_size_mm)
        max_ft      = mm_to_ft(max_size_mm)
        cell_size_ft = mm_to_ft(50.0)
        room_height = self._z_max - self._z_min

        mid_z = (self._z_min + self._z_max) / 2.0
        dz    = mm_to_ft(100.0)
        slice_xy = [(p[0], p[1]) for p in self.pts if abs(p[2] - mid_z) <= dz]
        if len(slice_xy) < 10:
            return results

        clusters = cluster_2d(slice_xy, cell_size_ft, min_pts_cell=2, min_cluster_size=5)

        for cluster in clusters:
            xs = [p[0] for p in cluster]
            ys = [p[1] for p in cluster]
            w = max(xs) - min(xs)
            d = max(ys) - min(ys)

            if w < min_ft or d < min_ft:
                continue
            if w > max_ft or d > max_ft:
                continue
            if max(w, d) < min_ft * 0.5:
                continue
            # Anti-MEP: rectangular ducts are flat (high aspect); structural
            # columns are near-square or round
            if max(w, d) > min(w, d) * 2.5:
                continue

            cx = (max(xs) + min(xs)) / 2.0
            cy = (max(ys) + min(ys)) / 2.0

            if detected_walls and self._near_detected_wall(
                    cx, cy, detected_walls, mm_to_ft(200.0)):
                continue
            r  = max(w, d)
            vert_pts = [p for p in self._points_near(cx, cy, r + self.XY_CELL_FT,
                                                     r + self.XY_CELL_FT)
                        if abs(p[0] - cx) <= r and abs(p[1] - cy) <= r]
            if not vert_pts:
                continue

            z_bot   = min(p[2] for p in vert_pts)
            z_top   = max(p[2] for p in vert_pts)
            height_ft = z_top - z_bot
            # Real columns rise well above furniture/equipment height —
            # shelving (~1.8 m) and floor-mounted MEP units fail this
            if height_ft < max(mm_to_ft(2200.0), room_height * 0.4):
                continue
            # Continuity check in the 1.9–2.6 m band (above furniture,
            # below the ceiling plane): a true column shaft has points
            # there; a shelf corner only "spans" the room because floor
            # and ceiling points share its XY window
            band_lo = self._z_min + mm_to_ft(1900.0)
            band_hi = min(self._z_min + mm_to_ft(2600.0),
                          self._z_max - mm_to_ft(300.0))
            if band_hi > band_lo:
                n_band = 0
                for p in vert_pts:
                    if band_lo <= p[2] <= band_hi:
                        n_band += 1
                        if n_band >= 3:
                            break
                if n_band < 3:
                    continue

            lv = self._best_level(z_bot)
            level_name = lv.Name if lv else u""
            results.append(DetectedColumn(cx, cy, w, d, z_bot, z_top, level_name))

        return results

    # ── Opening detection ──────────────────────────────────────────────────────

    def detect_openings(self, detected_walls,
                        min_door_w_mm=600.0, min_door_h_mm=1800.0,
                        min_win_w_mm=400.0,  min_win_h_mm=400.0):
        """
        For each detected wall, project nearby points onto the wall face plane
        and look for vertical rectangular gaps indicating door/window openings.
        Returns list of DetectedOpening.
        """
        results = []
        if not detected_walls:
            return results

        for wall in detected_walls:
            d = wall._data
            angle    = d['angle']
            cx, cy   = d['cx'], d['cy']
            length_ft = d['length_ft']
            thick_ft  = d['thickness_ft']
            # Per-wall vertical bounds: the wall's own storey, not the whole cloud
            floor_z = d.get('base_z', self._z_min)
            ceil_z  = min(self._z_max, floor_z + mm_to_ft(4000.0))

            nx = -math.sin(angle)
            ny =  math.cos(angle)
            dx_wall = math.cos(angle)
            dy_wall = math.sin(angle)

            # Candidate window from the spatial index, then the exact
            # u/v/z filter — avoids scanning the whole cloud per wall
            half_u = length_ft / 2.0 * 1.1
            margin = thick_ft * 1.5
            ex = abs(dx_wall) * half_u + abs(nx) * margin + self.XY_CELL_FT
            ey = abs(dy_wall) * half_u + abs(ny) * margin + self.XY_CELL_FT

            face_pts = []
            for p in self._points_near(cx, cy, ex, ey):
                rel_x = p[0] - cx
                rel_y = p[1] - cy
                u_proj = rel_x * dx_wall + rel_y * dy_wall
                v_proj = rel_x * nx      + rel_y * ny
                if (abs(u_proj) <= half_u
                        and abs(v_proj) <= margin
                        and floor_z - mm_to_ft(100.0) <= p[2] <= ceil_z):
                    face_pts.append((u_proj, p[2], v_proj))

            if len(face_pts) < 20:
                continue

            # Gap scan runs on the 1.0–2.0 m band above the wall base: both
            # door voids and window voids are empty there, while the full-
            # height histogram only dips ~30% at windows (sill+head survive)
            # which is indistinguishable from noise.
            band_lo = floor_z + mm_to_ft(1000.0)
            band_hi = floor_z + mm_to_ft(2000.0)
            wall_pts_uz = [(p[0], p[1]) for p in face_pts
                           if abs(p[2]) <= thick_ft and band_lo <= p[1] <= band_hi]
            if len(wall_pts_uz) < 20:
                # sparse band — fall back to the full wall height
                wall_pts_uz = [(p[0], p[1]) for p in face_pts
                               if abs(p[2]) <= thick_ft]
            u_vals = [p[0] for p in wall_pts_uz]
            if not u_vals:
                continue

            u_min, u_max = min(u_vals), max(u_vals)
            u_bin_ft = mm_to_ft(100.0)
            n_ubins  = max(1, int((u_max - u_min) / u_bin_ft) + 1)
            u_hist   = [0] * n_ubins
            for (u, z) in wall_pts_uz:
                bi = min(int((u - u_min) / u_bin_ft), n_ubins - 1)
                u_hist[bi] += 1

            if not u_hist or max(u_hist) == 0:
                continue
            avg_density   = sum(u_hist) / float(len(u_hist))
            gap_threshold = avg_density * 0.45

            in_gap    = False
            gap_start = 0
            for i, cnt in enumerate(u_hist):
                if cnt <= gap_threshold and not in_gap:
                    in_gap    = True
                    gap_start = i
                elif cnt > gap_threshold and in_gap:
                    in_gap   = False
                    gap_end  = i
                    gap_width    = (gap_end - gap_start) * u_bin_ft
                    gap_u_center = u_min + (gap_start + gap_end) / 2.0 * u_bin_ft

                    # Points REMAINING in the gap columns (lintel above a door,
                    # sill/head strips around a window). The opening itself is
                    # the largest vertical VOID between those remaining points,
                    # bounded by floor and ceiling. Inset both edges by half a
                    # bin — the boundary columns belong to the solid wall and
                    # would otherwise fill the void with full-height points.
                    u_lo = u_min + (gap_start + 0.5) * u_bin_ft
                    u_hi = u_min + (gap_end   - 0.5) * u_bin_ft
                    gap_pts_z = [p[1] for p in face_pts
                                 if u_lo <= p[0] <= u_hi]
                    zs = [floor_z] + sorted(gap_pts_z) + [ceil_z]
                    gap_z_min  = floor_z
                    gap_height = 0.0
                    for zi in range(1, len(zs)):
                        void = zs[zi] - zs[zi - 1]
                        if void > gap_height:
                            gap_height = void
                            gap_z_min  = zs[zi - 1]

                    # Furniture-occlusion guard: a hole in the wall band with
                    # something standing right in front of it is a scan
                    # SHADOW (wardrobe/cabinet blocking the scanner), not an
                    # opening. Blockers must spread across the gap width — a
                    # thin open door leaf at one jamb must not veto a real
                    # doorway.
                    b_lo  = thick_ft * 1.5
                    b_hi  = mm_to_ft(1200.0)
                    bz_lo = gap_z_min + mm_to_ft(300.0)
                    bz_hi = gap_z_min + gap_height - mm_to_ft(100.0)
                    if bz_hi > bz_lo and gap_width > 0:
                        gx = cx + dx_wall * gap_u_center
                        gy = cy + dy_wall * gap_u_center
                        half_g = gap_width / 2.0
                        exb = abs(dx_wall) * half_g + abs(nx) * b_hi + self.XY_CELL_FT
                        eyb = abs(dy_wall) * half_g + abs(ny) * b_hi + self.XY_CELL_FT
                        blocker_bins   = set()
                        blocker_z_bins = set()
                        z_bin_b = mm_to_ft(200.0)
                        for p in self._points_near(gx, gy, exb, eyb):
                            if not (bz_lo <= p[2] <= bz_hi):
                                continue
                            rxb = p[0] - cx
                            ryb = p[1] - cy
                            ub  = rxb * dx_wall + ryb * dy_wall
                            vb  = abs(rxb * nx + ryb * ny)
                            if abs(ub - gap_u_center) <= half_g and b_lo < vb <= b_hi:
                                blocker_bins.add(int(math.floor(
                                    (ub - gap_u_center) / u_bin_ft)))
                                blocker_z_bins.add(int(math.floor(p[2] / z_bin_b)))
                        n_gap_bins = max(1, int(gap_width / u_bin_ft))
                        # Veto only when the blocker covers most of the gap in
                        # BOTH width and height — a wardrobe hides its own
                        # shadow fully; a low table in front of a real door
                        # must not erase the door
                        if (len(blocker_bins) >= max(3, int(0.5 * n_gap_bins))
                                and len(blocker_z_bins) * z_bin_b >= 0.5 * gap_height):
                            continue

                    is_door_bottom = (gap_z_min - floor_z) < mm_to_ft(100.0)

                    if (is_door_bottom
                            and gap_width  >= mm_to_ft(min_door_w_mm)
                            and gap_height >= mm_to_ft(min_door_h_mm)):
                        results.append(DetectedOpening(
                            'Door', d, gap_u_center, gap_z_min,
                            gap_width, gap_height, wall.LevelName))
                    elif (not is_door_bottom
                            and gap_width  >= mm_to_ft(min_win_w_mm)
                            and gap_height >= mm_to_ft(min_win_h_mm)):
                        results.append(DetectedOpening(
                            'Window', d, gap_u_center, gap_z_min,
                            gap_width, gap_height, wall.LevelName))

        return results

    # ── Stair detection ────────────────────────────────────────────────────────

    def detect_stairs(self, riser_height_mm=165.0):
        """
        Look for regions where Z increases in discrete steps of ~riser_height.
        Returns list of DetectedStair (confidence ~55%).
        """
        results = []
        riser_ft     = mm_to_ft(riser_height_mm)
        riser_tol_ft = mm_to_ft(30.0)

        hist, z_min_h, bin_ft = z_histogram(self.pts, riser_height_mm * 0.5)
        peaks = find_histogram_peaks(hist, z_min_h, bin_ft, min_ratio=0.1)
        if len(peaks) < 4:
            return results

        # A believable flight needs at least 4 evenly spaced treads —
        # 3 is regularly produced by aliasing between bins and wall bands
        stair_sequences = []
        current_seq = [peaks[0]]
        for i in range(1, len(peaks)):
            dz = peaks[i][0] - peaks[i - 1][0]
            if abs(dz - riser_ft) <= riser_tol_ft:
                current_seq.append(peaks[i])
            else:
                if len(current_seq) >= 4:
                    stair_sequences.append(current_seq)
                current_seq = [peaks[i]]
        if len(current_seq) >= 4:
            stair_sequences.append(current_seq)

        for seq in stair_sequences:
            z_bot       = seq[0][0]
            z_top       = seq[-1][0]
            tread_count = len(seq)
            lv          = self._best_level(z_bot)
            level_name  = lv.Name if lv else u""
            results.append(DetectedStair(z_bot, z_top, tread_count, level_name))

        return results

    # ── Roof detection ─────────────────────────────────────────────────────────

    def detect_roof(self, min_slope_deg=5.0):
        """
        Find inclined planes near the top of the cloud using a simplified 3D
        variance check. Returns list of DetectedRoof.
        """
        results = []
        z_range      = self._z_max - self._z_min
        top_z_thresh = self._z_max - z_range * 0.15
        top_pts      = [p for p in self.pts if p[2] >= top_z_thresh]
        if len(top_pts) < 20:
            return results

        n  = len(top_pts)
        mx = sum(p[0] for p in top_pts) / n
        my = sum(p[1] for p in top_pts) / n
        mz = sum(p[2] for p in top_pts) / n

        sxx = sum((p[0] - mx) ** 2 for p in top_pts)
        syy = sum((p[1] - my) ** 2 for p in top_pts)
        szz = sum((p[2] - mz) ** 2 for p in top_pts)

        # If Z variance is negligible relative to XY variance it is a flat ceiling
        if szz < (sxx + syy) * 0.01:
            return results

        sxz = sum((p[0] - mx) * (p[2] - mz) for p in top_pts)
        syz = sum((p[1] - my) * (p[2] - mz) for p in top_pts)

        slope_x     = sxz / max(sxx, 1e-10)
        slope_y     = syz / max(syy, 1e-10)
        slope_total = math.sqrt(slope_x ** 2 + slope_y ** 2)
        slope_deg   = math.degrees(math.atan(slope_total))

        if slope_deg < min_slope_deg:
            return results

        min_x = min(p[0] for p in top_pts)
        max_x = max(p[0] for p in top_pts)
        min_y = min(p[1] for p in top_pts)
        max_y = max(p[1] for p in top_pts)
        corners = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]

        lv = self._best_level(top_z_thresh)
        level_name = lv.Name if lv else u""
        results.append(DetectedRoof(top_z_thresh, corners, slope_deg, level_name))
        return results

    # ── Main run method ────────────────────────────────────────────────────────

    def run(self, settings, progress_cb=None):
        """
        Run all enabled detectors. settings is a dict populated from the UI.
        progress_cb, if given, is called with a status string per stage.
        Returns a list of DetectedElement sorted by type.
        """
        all_results = []
        failed      = []

        def report(msg):
            if progress_cb:
                try:
                    progress_cb(msg)
                except Exception:
                    pass

        def run_stage(name, func):
            """Isolate each detector — one failing stage costs only its own
            results, never the whole analysis."""
            report(u"Detecting {}…".format(name))
            try:
                return func()
            except Exception as ex:
                import traceback
                logger.error("detect {} failed: {}\n{}".format(
                    name, ex, traceback.format_exc()))
                failed.append(name)
                return []

        if settings.get('detect_wall', True):
            walls = run_stage(u"walls", lambda: self.detect_walls(
                snap_tol_deg=settings.get('snap_tol', 1.0),
                min_length_mm=settings.get('wall_min_len', 500.0)))
            all_results.extend(walls)
        else:
            walls = []

        if settings.get('detect_floor', True) or settings.get('detect_ceiling', True):
            surfaces = run_stage(u"floors and ceilings",
                                 lambda: self.detect_horizontal_surfaces(
                                     z_bin_mm=settings.get('floor_zbin', 50.0),
                                     min_area_m2=settings.get('floor_min_area', 1.0),
                                     detected_walls=walls))
            for s in surfaces:
                if s.Type == 'Floor' and settings.get('detect_floor', True):
                    all_results.append(s)
                elif s.Type == 'Ceiling' and settings.get('detect_ceiling', True):
                    all_results.append(s)

        if settings.get('detect_column', False):
            all_results.extend(run_stage(u"columns", lambda: self.detect_columns(
                min_size_mm=settings.get('col_min', 100.0),
                max_size_mm=settings.get('col_max', 600.0),
                detected_walls=walls)))

        if settings.get('detect_door', False) or settings.get('detect_window', False):
            openings = run_stage(u"doors and windows",
                                 lambda: self.detect_openings(
                                     walls,
                                     min_door_w_mm=settings.get('door_min_w', 600.0),
                                     min_door_h_mm=settings.get('door_min_h', 1800.0),
                                     min_win_w_mm=settings.get('win_min_w', 400.0),
                                     min_win_h_mm=settings.get('win_min_h', 400.0)))
            for o in openings:
                if o.Type == 'Door' and settings.get('detect_door', False):
                    all_results.append(o)
                elif o.Type == 'Window' and settings.get('detect_window', False):
                    all_results.append(o)

        if settings.get('detect_stair', False):
            all_results.extend(run_stage(u"stairs", lambda: self.detect_stairs(
                riser_height_mm=settings.get('stair_riser', 165.0))))

        if settings.get('detect_roof', False):
            all_results.extend(run_stage(u"roof planes", lambda: self.detect_roof(
                min_slope_deg=settings.get('roof_slope', 5.0))))

        if failed:
            report(u"Some detectors failed ({}) — see pyRevit log."
                   .format(u", ".join(failed)))

        return all_results


# ── Section 6: ElementBuilder ─────────────────────────────────────────────────

class WarningSwallower(IFailuresPreprocessor):
    __namespace__ = "T3Lab.PointCloud_Warning"
    """
    Suppress Revit's modal warning dialogs during batch creation.

    Scan-derived geometry is noisy by nature, so nearly every element raises
    a warning: walls slightly off axis, walls overlapping at unmitred corners,
    floors/ceilings overlapping a wall, openings not fully inside their host.
    Without a preprocessor each of those stops the batch on a modal dialog —
    with a TransactionGroup open and hundreds of elements queued the user sees
    a frozen Revit and kills the process, losing the whole session. Warnings
    are deleted; genuine errors are resolved and the commit proceeds.
    """

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
                try:
                    failuresAccessor.ResolveFailure(failure)
                except Exception:
                    pass

        if has_error:
            return FailureProcessingResult.ProceedWithCommit
        return FailureProcessingResult.Continue


class ElementBuilder(object):
    """Creates Revit elements from DetectedElement data inside named Transactions."""

    def __init__(self, document):
        self.doc         = document
        self._wall_types  = self._load_wall_types()
        self._floor_types = self._load_floor_types()
        self._levels      = self._load_levels()

    def _start(self, t):
        """Start a transaction with warnings suppressed.

        Must run after Start(): the options object is per-transaction, and a
        rollback has to clear its own failures or the next commit inherits
        them.
        """
        t.Start()
        try:
            opts = t.GetFailureHandlingOptions()
            opts.SetFailuresPreprocessor(WarningSwallower())
            opts.SetClearAfterRollback(True)
            t.SetFailureHandlingOptions(opts)
        except Exception as ex:
            logger.debug("failure handling options: {}".format(ex))

    def _load_wall_types(self):
        if self.doc is None:
            return []
        return list(FilteredElementCollector(self.doc).OfClass(WallType).ToElements())

    def _load_floor_types(self):
        if self.doc is None:
            return []
        return list(FilteredElementCollector(self.doc).OfClass(FloorType).ToElements())

    def _load_levels(self):
        if self.doc is None:
            return {}
        lvs = FilteredElementCollector(self.doc).OfClass(Level).ToElements()
        return {lv.Name: lv for lv in lvs}

    def _best_wall_type(self, thickness_ft):
        """Find the WallType whose compound structure width is closest to thickness_ft."""
        best      = None
        best_diff = float('inf')
        for wt in self._wall_types:
            try:
                cs = wt.GetCompoundStructure()
                w  = cs.GetTotalWidth() if cs else wt.Width
                d  = abs(w - thickness_ft)
                if d < best_diff:
                    best_diff = d
                    best = wt
            except Exception:
                pass
        return best or (self._wall_types[0] if self._wall_types else None)

    def _best_floor_type(self):
        return self._floor_types[0] if self._floor_types else None

    def _get_level(self, level_name):
        return self._levels.get(level_name) or (
            sorted(self._levels.values(), key=lambda l: l.Elevation)[0]
            if self._levels else None)

    def _set_offset_from_level(self, elem, bip, offset_ft):
        """Set a height-above-level offset parameter, ignoring failures."""
        try:
            p = elem.get_Parameter(bip)
            if p and not p.IsReadOnly:
                p.Set(offset_ft)
        except Exception:
            pass

    def _rect_curve_loop(self, corners_xy, z_ft):
        """Build a closed rectangular CurveLoop from 4 (x, y) corner tuples."""
        cl  = CurveLoop()
        pts = [XYZ(x, y, z_ft) for (x, y) in corners_xy]
        for i in range(len(pts)):
            p1 = pts[i]
            p2 = pts[(i + 1) % len(pts)]
            if p1.DistanceTo(p2) > 0.01:
                cl.Append(Line.CreateBound(p1, p2))
        return cl

    def build_wall(self, elem, height_ft=None):
        d = elem._data
        angle      = d['angle']
        cx, cy     = d['cx'], d['cy']
        length_ft  = d['length_ft']
        thick_ft   = d['thickness_ft']
        if height_ft is None:
            height_ft = mm_to_ft(3000.0)

        half  = length_ft / 2.0
        pt1   = XYZ(cx - math.cos(angle) * half, cy - math.sin(angle) * half, 0.0)
        pt2   = XYZ(cx + math.cos(angle) * half, cy + math.sin(angle) * half, 0.0)
        wline = Line.CreateBound(pt1, pt2)

        lv = self._get_level(elem.LevelName)
        if not lv:
            return None
        wt = self._best_wall_type(thick_ft)
        if not wt:
            return None

        with Transaction(self.doc, "T3Lab: Create Wall") as t:
            self._start(t)
            try:
                wall = Wall.Create(self.doc, wline, wt.Id, lv.Id,
                                   height_ft, 0.0, False, False)
                t.Commit()
                return wall
            except Exception as ex:
                logger.error("build_wall: {}".format(ex))
                t.RollBack()
                return None

    def build_floor(self, elem):
        d  = elem._data
        lv = self._get_level(elem.LevelName)
        if not lv:
            return None
        ft = self._best_floor_type()
        if not ft:
            return None
        cl = self._rect_curve_loop(d['corners_xy'], d['z_ft'])

        with Transaction(self.doc, "T3Lab: Create Floor") as t:
            self._start(t)
            try:
                if REVIT_VERSION >= 2022:
                    profile = List[CurveLoop]()
                    profile.Add(cl)
                    floor = Floor.Create(self.doc, profile, ft.Id, lv.Id)
                else:
                    ca = CurveArray()
                    for curve in cl:
                        ca.Append(curve)
                    floor = self.doc.Create.NewFloor(ca, ft, lv, False)
                self._set_offset_from_level(
                    floor, BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM,
                    d['z_ft'] - lv.Elevation)
                t.Commit()
                return floor
            except Exception as ex:
                logger.error("build_floor: {}".format(ex))
                t.RollBack()
                return None

    def build_ceiling(self, elem):
        """Create a ceiling (Revit 2022+ Ceiling.Create, else floor-based fallback)."""
        d  = elem._data
        lv = self._get_level(elem.LevelName)
        if not lv:
            return None
        cl = self._rect_curve_loop(d['corners_xy'], d['z_ft'])

        with Transaction(self.doc, "T3Lab: Create Ceiling") as t:
            self._start(t)
            try:
                if REVIT_VERSION >= 2022:
                    from Autodesk.Revit.DB import Ceiling, CeilingType
                    ceil_types = FilteredElementCollector(self.doc) \
                        .OfClass(CeilingType).ToElements()
                    if not ceil_types:
                        t.RollBack()
                        return None
                    ct = ceil_types[0]
                    profile = List[CurveLoop]()
                    profile.Add(cl)
                    ceiling = Ceiling.Create(self.doc, profile, ct.Id, lv.Id)
                    self._set_offset_from_level(
                        ceiling, BuiltInParameter.CEILING_HEIGHTABOVELEVEL_PARAM,
                        d['z_ft'] - lv.Elevation)
                else:
                    ft = self._best_floor_type()
                    if not ft:
                        t.RollBack()
                        return None
                    ca = CurveArray()
                    for curve in cl:
                        ca.Append(curve)
                    ceiling = self.doc.Create.NewFloor(ca, ft, lv, False)
                t.Commit()
                return ceiling
            except Exception as ex:
                logger.error("build_ceiling: {}".format(ex))
                t.RollBack()
                return None

    def build_column(self, elem):
        """Place a structural (or architectural) column family instance."""
        d  = elem._data
        lv = self._get_level(elem.LevelName)
        if not lv:
            return None

        col_symbols = (FilteredElementCollector(self.doc)
                       .OfClass(FamilySymbol)
                       .OfCategory(BuiltInCategory.OST_StructuralColumns)
                       .ToElements())
        if not col_symbols:
            col_symbols = (FilteredElementCollector(self.doc)
                           .OfClass(FamilySymbol)
                           .OfCategory(BuiltInCategory.OST_Columns)
                           .ToElements())
        if not col_symbols:
            return None

        sym = col_symbols[0]
        pt  = XYZ(d['cx'], d['cy'], d['z_bot_ft'])

        with Transaction(self.doc, "T3Lab: Create Column") as t:
            self._start(t)
            try:
                if not sym.IsActive:
                    sym.Activate()
                col = self.doc.Create.NewFamilyInstance(
                    pt, sym, lv, StructuralType.Column)
                t.Commit()
                return col
            except Exception as ex:
                logger.error("build_column: {}".format(ex))
                t.RollBack()
                return None

    def build_opening(self, elem, host_wall_revit_elem):
        """Place a door or window family instance in the given host wall."""
        if host_wall_revit_elem is None:
            return None
        d     = elem._data
        angle = d['host_wall_data']['angle']
        cx    = d['host_wall_data']['cx']
        cy    = d['host_wall_data']['cy']
        u_c   = d['u_center']

        ox = cx + math.cos(angle) * u_c
        oy = cy + math.sin(angle) * u_c
        # insertion point at the opening BOTTOM (door threshold / window sill),
        # not mid-height — Revit derives the level offset from the point Z
        oz = d['w_bottom']
        pt = XYZ(ox, oy, oz)

        cat = (BuiltInCategory.OST_Doors
               if elem.Type == 'Door' else BuiltInCategory.OST_Windows)
        syms = (FilteredElementCollector(self.doc)
                .OfClass(FamilySymbol)
                .OfCategory(cat)
                .ToElements())
        if not syms:
            return None
        sym = syms[0]
        lv  = self._get_level(elem.LevelName)
        if not lv:
            return None

        with Transaction(self.doc, "T3Lab: Create {}".format(elem.Type)) as t:
            self._start(t)
            try:
                if not sym.IsActive:
                    sym.Activate()
                try:
                    from Autodesk.Revit.DB.Structure import StructuralType as ST
                    non_structural = ST.NonStructural
                except ImportError:
                    non_structural = StructuralType.NonStructural
                inst = self.doc.Create.NewFamilyInstance(
                    pt, sym, host_wall_revit_elem, lv, non_structural)
                t.Commit()
                return inst
            except Exception as ex:
                logger.error("build_opening: {}".format(ex))
                t.RollBack()
                return None

    def build_roof(self, elem):
        """Create a basic FootPrintRoof from the detected boundary."""
        d  = elem._data
        lv = self._get_level(elem.LevelName)
        if not lv:
            return None

        roof_types = (FilteredElementCollector(self.doc)
                      .OfCategory(BuiltInCategory.OST_Roofs)
                      .OfClass(DB.RoofType)
                      .ToElements())
        if not roof_types:
            return None
        rt = roof_types[0]

        z_ft    = d['z_ft']
        corners = d['corners_xy']
        pts     = [XYZ(x, y, z_ft) for (x, y) in corners]
        n       = len(pts)

        with Transaction(self.doc, "T3Lab: Create Roof") as t:
            self._start(t)
            try:
                ca = CurveArray()
                for i in range(n):
                    p1 = pts[i]
                    p2 = pts[(i + 1) % n]
                    if p1.DistanceTo(p2) > 0.01:
                        ca.Append(Line.CreateBound(p1, p2))
                # NewFootPrintRoof has an 'out ModelCurveArray' parameter —
                # IronPython requires a clr.Reference box for it
                ma_ref = clr.Reference[DB.ModelCurveArray]()
                roof = self.doc.Create.NewFootPrintRoof(ca, lv, rt, ma_ref)
                self._set_offset_from_level(
                    roof, BuiltInParameter.ROOF_LEVEL_OFFSET_PARAM,
                    z_ft - lv.Elevation)
                t.Commit()
                return roof
            except Exception as ex:
                logger.error("build_roof: {}".format(ex))
                t.RollBack()
                return None

    def build_stair_marker(self, elem):
        """Stub — stair creation requires the Architecture API; skipped here."""
        return None

    def build_all(self, elements, default_wall_height_ft=None):
        """
        Create all selected elements in dependency order.
        Returns a summary dict {'counts': {...}, 'errors': int}.
        """
        if default_wall_height_ft is None:
            default_wall_height_ft = mm_to_ft(3000.0)

        counts = {k: 0 for k in ['Wall', 'Floor', 'Ceiling', 'Column',
                                  'Door', 'Window', 'Stair', 'Roof']}
        skipped       = {'Stair': 0}
        errors        = 0
        created_walls = {}   # id(detected wall _data dict) → Revit Wall element

        tg = TransactionGroup(self.doc, "T3Lab: Point Cloud to Model")
        tg.Start()
        try:
            # Pass 1: walls
            for elem in elements:
                if not elem.Include:
                    continue
                if elem.Type == 'Wall':
                    w = self.build_wall(elem, default_wall_height_ft)
                    if w:
                        counts['Wall'] += 1
                        created_walls[id(elem._data)] = w
                    else:
                        errors += 1

            # Pass 2: floors, ceilings, columns, roof
            for elem in elements:
                if not elem.Include:
                    continue
                if elem.Type == 'Floor':
                    r = self.build_floor(elem)
                    if r:   counts['Floor'] += 1
                    else:   errors += 1
                elif elem.Type == 'Ceiling':
                    r = self.build_ceiling(elem)
                    if r:   counts['Ceiling'] += 1
                    else:   errors += 1
                elif elem.Type == 'Column':
                    r = self.build_column(elem)
                    if r:   counts['Column'] += 1
                    else:   errors += 1
                elif elem.Type == 'Roof':
                    r = self.build_roof(elem)
                    if r:   counts['Roof'] += 1
                    else:   errors += 1
                elif elem.Type == 'Stair':
                    # build_stair_marker is a stub — creation needs the
                    # Architecture stairs API. Counting it as created reported
                    # "N Stairs" in the summary for elements that were never
                    # placed; track it separately and tell the truth.
                    skipped['Stair'] += 1

            # Pass 3: doors / windows — host into the wall each opening was
            # detected in; fall back to any created wall
            fallback_wall = None
            for w in created_walls.values():
                fallback_wall = w
                break
            for elem in elements:
                if not elem.Include:
                    continue
                if elem.Type not in ('Door', 'Window'):
                    continue
                host = created_walls.get(id(elem._data['host_wall_data']),
                                         fallback_wall)
                r = self.build_opening(elem, host)
                if r:   counts[elem.Type] += 1
                else:   errors += 1

            tg.Assimilate()
        except Exception:
            if tg.HasStarted():
                tg.RollBack()
            raise

        return {'counts': counts, 'errors': errors, 'skipped': skipped}


# ── Section 7: WPF Wizard Window ──────────────────────────────────────────────

class PointCloudModelWindow(T3WPFWindow):
    """Single-window UI for Point Cloud to Model analysis and element generation."""

    DENSITY_CAPS = [5000, 20000, 50000]

    def __init__(self, state=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        state = state or {}
        self._pc_instance       = state.get('pc_instance')
        self._custom_min_pt     = state.get('min_pt')
        self._custom_max_pt     = state.get('max_pt')
        self._detected_elements = state.get('elements') or []
        self._wall_height_ft    = state.get('wall_height')
        self._builder           = ElementBuilder(doc)
        self.result             = None
        # 'cloud' / 'region' — set by pick buttons. The window CLOSES for
        # every pick: hiding a ShowDialog window ends its modal loop, the
        # command's Execute returns, and every later click then runs Revit
        # API calls OUTSIDE the API context — the uncatchable 0xe0434352
        # crashes behind journal 1034-1037. The main loop below re-opens the
        # window after performing the pick inside the still-live command.
        self.pick_request       = None

        self.status_count.Text = u"Revit {}".format(REVIT_VERSION)
        if self._pc_instance is not None:
            self._set_cloud(self._pc_instance)
        else:
            self._try_preselect_cloud()
        if state.get('custom_checked') or self._custom_min_pt is not None:
            self.rb_custom_region.IsChecked = True
        if self._custom_min_pt is not None:
            self._show_region_state()
        if state.get('region_error'):
            self._show_region_state(state['region_error'])
        if self._detected_elements:
            self._populate_results()
            self.status_text.Text = (
                u"Detected {} elements. Review and click Generate.".format(
                    len(self._detected_elements)))
        if state.get('status'):
            self.status_text.Text = state['status']

    def _try_preselect_cloud(self):
        try:
            sel_ids = uidoc.Selection.GetElementIds()
            for eid in sel_ids:
                el = doc.GetElement(eid)
                if isinstance(el, PointCloudInstance):
                    self._set_cloud(el)
                    break
        except Exception:
            pass

    def _set_cloud(self, pc_inst):
        self._pc_instance = pc_inst
        try:
            name = pc_inst.Name or u"Point Cloud"
        except Exception:
            name = u"Point Cloud"
        self.lbl_cloud_name.Text       = name
        self.lbl_cloud_name.Foreground = self._brush('#0F172A')
        self.status_text.Text          = u"Cloud selected: {}".format(name)

    def _brush(self, hex_color):
        from System.Windows.Media import BrushConverter
        return BrushConverter().ConvertFromString(hex_color)

    def _set_status(self, msg):
        """Update the status bar and pump the dispatcher so the text repaints
        while long work runs on the UI thread."""
        self.status_text.Text = msg
        try:
            # Render priority repaints the label without pumping queued
            # input events (no re-entrant clicks mid-analysis).
            # NOTE: Action first, priority second — the (priority, delegate)
            # legacy overload fails to bind under IronPython.
            self.Dispatcher.Invoke(Action(lambda: None),
                                   DispatcherPriority.Render)
        except Exception:
            pass

    # ── Window chrome ──────────────────────────────────────────────────────────

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        self.WindowState = (WindowState.Normal
                            if self.WindowState == WindowState.Maximized
                            else WindowState.Maximized)

    def close_button_clicked(self, sender, e):
        self.Close()

    # ── Step 0 handlers ────────────────────────────────────────────────────────

    def btn_pick_cloud_clicked(self, sender, e):
        # Close and let the main loop pick inside the live command context —
        # NEVER Hide() a ShowDialog window for picking (see __init__ note).
        self.pick_request = 'cloud'
        self.Close()

    def rb_custom_region_checked(self, sender, e):
        self.btn_pick_region.IsEnabled = True
        if self._custom_min_pt is None:
            self._show_region_state()

    def rb_full_extent_checked(self, sender, e):
        self.btn_pick_region.IsEnabled = False
        self._custom_min_pt = None
        self._custom_max_pt = None
        self._show_region_state()

    def _show_region_state(self, error_msg=None):
        """Reflect the custom-region state in the label under the pick button."""
        try:
            if error_msg:
                self.lbl_region_info.Text       = error_msg
                self.lbl_region_info.Foreground = self._brush('#D23B3B')
            elif self._custom_min_pt is not None:
                w_m = ft_to_mm(self._custom_max_pt.X - self._custom_min_pt.X) / 1000.0
                d_m = ft_to_mm(self._custom_max_pt.Y - self._custom_min_pt.Y) / 1000.0
                self.lbl_region_info.Text = (
                    u"✓ Region set: {:.1f} × {:.1f} m".format(w_m, d_m))
                self.lbl_region_info.Foreground = self._brush('#0B8A5A')
                self.btn_pick_region.Content    = u"Re-pick Region"
            else:
                self.lbl_region_info.Text       = u"No region defined"
                self.lbl_region_info.Foreground = self._brush('#94A3B8')
                self.btn_pick_region.Content    = u"Drag Region Box"
        except Exception:
            pass

    def btn_use_crop_clicked(self, sender, e):
        """Region from the active view's crop box. Read-only API — never
        enters a selection mode, so it cannot trip the Revit 2023 point
        cloud engine crash that interactive picking triggers on some clouds."""
        try:
            view = None
            try:
                view = uidoc.ActiveGraphicalView
            except Exception:
                pass
            if view is None:
                view = doc.ActiveView
            if not view.CropBoxActive:
                TaskDialog.Show(
                    "Point Cloud to Model",
                    "The active view has no crop region enabled.\n"
                    "Turn on the crop region, size it around the scan area, "
                    "then click 'Use View Crop' again.")
                return
            cb = view.CropBox
            tf = cb.Transform
            corners = [
                XYZ(cb.Min.X, cb.Min.Y, cb.Min.Z),
                XYZ(cb.Max.X, cb.Min.Y, cb.Min.Z),
                XYZ(cb.Min.X, cb.Max.Y, cb.Min.Z),
                XYZ(cb.Max.X, cb.Max.Y, cb.Min.Z),
                XYZ(cb.Min.X, cb.Min.Y, cb.Max.Z),
                XYZ(cb.Max.X, cb.Max.Y, cb.Max.Z),
            ]
            mpts = [tf.OfPoint(p) for p in corners]
            xs = [p.X for p in mpts]
            ys = [p.Y for p in mpts]
            zs = [p.Z for p in mpts]
            self._custom_min_pt = XYZ(min(xs), min(ys), min(zs) - mm_to_ft(500.0))
            self._custom_max_pt = XYZ(max(xs), max(ys), max(zs) + mm_to_ft(500.0))
            self.rb_custom_region.IsChecked = True
            self._show_region_state()
            self.status_text.Text = (
                u"Region taken from the view crop — click Analyze.")
        except Exception as ex:
            import traceback
            logger.error("use_crop: {}\n{}".format(ex, traceback.format_exc()))
            self._show_region_state(u"Could not read the view crop")
            self.status_text.Text = u"Use View Crop failed: {}".format(ex)

    def btn_pick_region_clicked(self, sender, e):
        # Close and let the main loop run PickBox inside the live command
        # context — NEVER Hide() a ShowDialog window for picking.
        self.pick_request = 'region'
        self.Close()

    def btn_analyze_clicked(self, sender, e):
        if self._pc_instance is None:
            TaskDialog.Show("Point Cloud to Model",
                            "Please select a Point Cloud Instance first.")
            return
        if self.rb_custom_region.IsChecked and self._custom_min_pt is None:
            TaskDialog.Show("Point Cloud to Model",
                            "Custom Region selected but no region defined.\n"
                            "Use 'Drag Region Box' or 'Use View Crop' first, "
                            "or switch to Full Cloud Extent.")
            return
        if REVIT_VERSION <= 2023:
            # Reading big clouds through the 2023 API can crash Revit itself
            # (ReCap engine AV — uncatchable). Warn and let the user back out.
            from Autodesk.Revit.UI import TaskDialogCommonButtons, TaskDialogResult
            td = TaskDialog("Point Cloud to Model")
            td.MainInstruction = "Continue on Revit {}?".format(REVIT_VERSION)
            td.MainContent = (
                "Reading large point clouds through the Revit 2023 API can "
                "crash Revit itself (a point cloud engine bug outside this "
                "tool). Save your work before continuing.\n\n"
                "Revit 2026 reads the same clouds reliably.\n\n"
                "Run the analysis now?")
            td.CommonButtons = (TaskDialogCommonButtons.Yes
                                | TaskDialogCommonButtons.No)
            if td.Show() != TaskDialogResult.Yes:
                self.status_text.Text = u"Analysis cancelled."
                return
        self.btn_analyze.IsEnabled = False
        try:
            from System.Windows.Input import Cursors
            self.Cursor = Cursors.Wait
        except Exception:
            pass
        try:
            self._run_analysis()
        except Exception as ex:
            import traceback
            logger.error("Analysis error: {}\n{}".format(ex, traceback.format_exc()))
            self._set_status(u"Analysis failed — see pyRevit log.")
            TaskDialog.Show("Point Cloud to Model",
                            u"Analysis error: {}".format(ex))
        finally:
            self.btn_analyze.IsEnabled = True
            try:
                self.Cursor = None
            except Exception:
                pass

    def _default_settings(self):
        """
        Architectural + structural scope: Walls, Floors, Ceilings, Doors,
        Windows, Columns, Stairs, Roof. MEP (ducts, pipes, equipment) must
        NOT be modelled — the column detector carries anti-MEP filters
        (min 200 mm cross-section, aspect <= 2.5, must rise above furniture
        height), and walls require tall evidence below the ceiling.
        """
        return {
            'detect_wall':    True,
            'snap_tol':       1.0,
            'wall_min_len':   500.0,
            'detect_floor':   True,
            'floor_zbin':     50.0,
            'floor_min_area': 1.0,
            'detect_ceiling': True,
            'ceil_zbin':      50.0,
            'detect_door':    True,
            'door_min_w':     600.0,
            'door_min_h':     1800.0,
            'detect_window':  True,
            'win_min_w':      400.0,
            'win_min_h':      400.0,
            'detect_column':  True,
            'col_min':        200.0,   # pipes/small risers are thinner
            'col_max':        600.0,
            'detect_stair':   True,
            'stair_riser':    165.0,
            'detect_roof':    True,
            'roof_slope':     5.0,
        }

    def _run_analysis(self):
        idx = self.cmb_density.SelectedIndex
        if idx < 0:
            idx = 1
        density_cap = self.DENSITY_CAPS[idx]

        self._set_status(u"Extracting up to {} points…".format(density_cap))

        if self.rb_custom_region.IsChecked and self._custom_min_pt:
            min_pt = self._custom_min_pt
            max_pt = self._custom_max_pt
            # Corners picked in a plan view share one Z (span = the ±500 mm
            # padding only) — expand to the cloud's full height instead of
            # scanning a 1 m slab
            if (max_pt.Z - min_pt.Z) < mm_to_ft(1500.0):
                bbox = self._pc_instance.get_BoundingBox(None)
                if bbox is not None:
                    min_pt = XYZ(min_pt.X, min_pt.Y, bbox.Min.Z - 0.5)
                    max_pt = XYZ(max_pt.X, max_pt.Y, bbox.Max.Z + 0.5)
            half_x = (max_pt.X - min_pt.X) / 2.0
            half_y = (max_pt.Y - min_pt.Y) / 2.0
            half_z = (max_pt.Z - min_pt.Z) / 2.0
            center = XYZ(
                (min_pt.X + max_pt.X) / 2.0,
                (min_pt.Y + max_pt.Y) / 2.0,
                (min_pt.Z + max_pt.Z) / 2.0)
            pts = extract_full_cloud_from_region(
                self._pc_instance, center, half_x, half_y, half_z, density_cap,
                progress_cb=self._set_status)
        else:
            pts = extract_full_cloud(self._pc_instance, density_cap,
                                     progress_cb=self._set_status)

        if len(pts) < 50:
            self._set_status(u"Extraction returned {} points.".format(len(pts)))
            TaskDialog.Show(
                "Point Cloud to Model",
                "Only {} points extracted. "
                "The cloud may be out of range, unloaded or empty.\n"
                "Check that the point cloud file (.rcp/.rcs) is loaded and "
                "available offline (OneDrive cloud-only placeholders can't "
                "be read), then try again.".format(len(pts)))
            return

        self._set_status(u"Analyzing {} points…".format(len(pts)))
        settings = self._default_settings()
        analyzer = PointCloudAnalyzer(pts)
        self._detected_elements = analyzer.run(settings, progress_cb=self._set_status)

        # Wall height for generation: full scan height, clamped to a sane range
        z_range = analyzer._z_max - analyzer._z_min
        self._wall_height_ft = min(max(z_range, mm_to_ft(2200.0)), mm_to_ft(6000.0))

        self._populate_results()
        total = len(self._detected_elements)
        self._set_status(
            u"Detected {} elements. Review and click Generate.".format(total))

    def _populate_results(self):
        elems = self._detected_elements

        type_counts = {}
        for e in elems:
            type_counts[e.Type] = type_counts.get(e.Type, 0) + 1

        badge_map = {
            'Wall':    self.badge_wall,
            'Floor':   self.badge_floor,
            'Ceiling': self.badge_ceiling,
            'Door':    self.badge_door,
            'Window':  self.badge_window,
            'Column':  self.badge_column,
            'Stair':   self.badge_stair,
            'Roof':    self.badge_roof,
        }
        labels = {
            'Wall': 'Walls', 'Floor': 'Floors', 'Ceiling': 'Ceilings',
            'Door': 'Doors', 'Window': 'Windows', 'Column': 'Columns',
            'Stair': 'Stairs', 'Roof': 'Roofs',
        }
        for t, badge in badge_map.items():
            cnt = type_counts.get(t, 0)
            badge.Text = u"{} {}".format(cnt, labels[t])

        total = len(elems)
        self.pnl_empty_state.Visibility = Visibility.Collapsed
        if total > 0:
            self.results_grid.ItemsSource     = to_items_source(elems)
            self.results_grid_card.Visibility = Visibility.Visible
            self.pnl_no_results.Visibility    = Visibility.Collapsed
            self.btn_generate.IsEnabled       = True
            self.btn_generate.Content         = u"Generate {} Selected".format(total)
        else:
            self.results_grid_card.Visibility = Visibility.Collapsed
            self.pnl_no_results.Visibility    = Visibility.Visible
            self.btn_generate.IsEnabled       = False
            self.btn_generate.Content         = u"Generate"

    # ── Step 2 handlers ────────────────────────────────────────────────────────

    def btn_generate_clicked(self, sender, e):
        selected = [el for el in self._detected_elements if el.Include]
        if not selected:
            TaskDialog.Show(
                "Point Cloud to Model",
                "No elements selected. Check the Include checkboxes.")
            return

        self.btn_generate.IsEnabled = False
        self._set_status(u"Creating {} elements…".format(len(selected)))
        try:
            summary = self._builder.build_all(selected, self._wall_height_ft)
            counts  = summary['counts']
            errors  = summary['errors']
            skipped = summary.get('skipped') or {}
            parts   = [u"{} {}".format(v, k)
                       for k, v in counts.items() if v > 0]
            msg = (u"Created: " + u", ".join(parts)) if parts else u"No elements created."
            n_stair = skipped.get('Stair', 0)
            if n_stair:
                msg += (u"\n{} stair(s) detected but NOT created — stair "
                        u"creation is not supported yet; model them "
                        u"manually.".format(n_stair))
            if errors:
                msg += u"\n{} element(s) failed — see pyRevit log.".format(errors)
            TaskDialog.Show("Point Cloud to Model", msg)
            self.result = summary
            self.Close()
        except Exception as ex:
            import traceback
            logger.error("Generate error: {}\n{}".format(ex, traceback.format_exc()))
            self._set_status(u"Generation failed — see pyRevit log.")
            TaskDialog.Show("Point Cloud to Model",
                            u"Generation error: {}".format(ex))
        finally:
            self.btn_generate.IsEnabled = True

    def btn_cancel_clicked(self, sender, e):
        self.result = None
        self.Close()

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_results_grid_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua results_grid."""
        self.toggle_all_rows(self.results_grid, "Include", sender.IsChecked)


# MAIN
# ==============================================================================

def _pick_cloud_into(state):
    """PickObject inside the live command context; updates state in place."""
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            PointCloudSelectionFilter(),
            "Select a Point Cloud Instance")
        state['pc_instance'] = doc.GetElement(ref.ElementId)
        state['status'] = None
    except OperationCanceledException:
        pass  # Esc — keep the previous cloud, if any
    except Exception as ex:
        logger.error("PickObject: {}".format(ex))
        state['status'] = u"Cloud selection failed: {}".format(ex)


def _pick_region_into(state):
    """PickBox inside the live command context; updates state in place."""
    state['custom_checked'] = True
    try:
        box = uidoc.Selection.PickBox(
            PickBoxStyle.Directional,
            "Drag a rectangle around the scan region")
        pt1, pt2 = box.Min, box.Max
        w = abs(pt1.X - pt2.X)
        d = abs(pt1.Y - pt2.Y)
        if w < mm_to_ft(100.0) or d < mm_to_ft(100.0):
            state['min_pt'] = None
            state['max_pt'] = None
            state['region_error'] = u"Region too small — drag a larger rectangle"
            state['status'] = u"Region not set: the dragged rectangle is nearly empty."
        else:
            state['min_pt'] = XYZ(
                min(pt1.X, pt2.X), min(pt1.Y, pt2.Y),
                min(pt1.Z, pt2.Z) - mm_to_ft(500.0))
            state['max_pt'] = XYZ(
                max(pt1.X, pt2.X), max(pt1.Y, pt2.Y),
                max(pt1.Z, pt2.Z) + mm_to_ft(500.0))
            state['status'] = u"Custom region captured — click Analyze."
    except OperationCanceledException:
        if state.get('min_pt') is not None:
            state['status'] = u"Pick cancelled — keeping the previous region."
        else:
            state['status'] = u"Pick cancelled — no region set yet."
    except Exception as ex:
        logger.error("PickBox: {}".format(ex))
        state['region_error'] = u"Pick failed — see pyRevit log"
        state['status'] = u"Pick failed: {}".format(ex)


def show_point_cloud_dialog(doc_param=None, uidoc_param=None):
    global doc, uidoc
    if doc_param:
        doc = doc_param
    if uidoc_param:
        uidoc = uidoc_param
    if not doc:
        try:
            doc = revit.doc
            uidoc = revit.uidoc
        except Exception:
            pass

    try:
        if not doc or not uidoc:
            TaskDialog.Show(
                "Point Cloud to Model",
                u"No active document found.\nOpen a project and try again.")
            return

        if not doc.ActiveView:
            TaskDialog.Show(
                "Point Cloud to Model",
                u"This tool needs an active Revit view to select a point "
                u"cloud in.\nOpen the model's plan or 3D view and run it "
                u"from the T3Lab ribbon button.")
            return

        state = {}
        while True:
            window = PointCloudModelWindow(state)
            window.ShowDialog()
            req = window.pick_request
            state = {
                'pc_instance':    window._pc_instance,
                'min_pt':         window._custom_min_pt,
                'max_pt':         window._custom_max_pt,
                'elements':       window._detected_elements,
                'wall_height':    window._wall_height_ft,
                'custom_checked': bool(window.rb_custom_region.IsChecked),
            }
            if req == 'cloud':
                _pick_cloud_into(state)
            elif req == 'region':
                _pick_region_into(state)
            else:
                break
    except SystemExit:
        pass
    except Exception as ex:
        logger.error("Point Cloud to Model error: {}".format(ex))
        import traceback
        logger.error(traceback.format_exc())
        TaskDialog.Show("Point Cloud to Model",
                        u"Unexpected error: {}".format(ex))


if __name__ == '__main__':
    show_point_cloud_dialog()
