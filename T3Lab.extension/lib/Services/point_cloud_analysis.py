# -*- coding: utf-8 -*-
"""
Point cloud analysis engine — shared by the Point Cloud to Model wizard
(GUI/PointCloudDialog.py) and the MCP server's point cloud tools
(core/server.py: list_point_clouds, analyze_point_cloud,
detect_point_cloud_elements).

Moved out of the dialog 2026-10-02 so the MCP tools run exactly the same
extraction safeguards and detectors as the wizard. No WPF and no pyRevit
forms here. The Revit API names are imported when Revit is present; under
plain CPython (dev/test_pointcloud_detect.py) they are None and the test
injects stubs.

THREADING: every GetPoints call must run on Revit's main thread. The MCP
server routes these tools through its ExternalEvent and never through its
HTTP-thread read fallback — an oversized or off-thread query has hard-crashed
the ReCap engine (native AccessViolation, journal 0xe0434352).
"""

import math

try:
    from Autodesk.Revit.DB import FilteredElementCollector, Grid, Level, Plane, XYZ
    from Autodesk.Revit.DB.PointClouds import PointCloudFilterFactory
    from System.Collections.Generic import List
except Exception:   # plain CPython: dev tests inject stubs
    FilteredElementCollector = Grid = Level = Plane = XYZ = None
    PointCloudFilterFactory = List = None

try:
    from pyrevit import script as _script
    logger = _script.get_logger()
except Exception:   # outside pyRevit
    import logging
    logger = logging.getLogger('T3Lab.point_cloud_analysis')


# ── Section 1: Unit Helpers ────────────────────────────────────────────────────

MM_PER_FOOT = 304.8

def ft_to_mm(ft):
    return ft * MM_PER_FOOT

def mm_to_ft(mm):
    return mm / MM_PER_FOOT


# ── Section 2: Point Cloud Extraction ─────────────────────────────────────────

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

    def __init__(self, all_pts, doc=None):
        # doc: the Revit document whose Grids (snap angles) and Levels (level
        # names) the detections are matched against. None = no project
        # context — detection still runs, nothing is snapped or named.
        self.doc    = doc
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
        if self.doc is None:
            return angles
        try:
            grids = FilteredElementCollector(self.doc).OfClass(Grid).ToElements()
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


    def _all_levels(self):
        """Project levels, queried once. _best_level runs per detected element,
        so a fresh FilteredElementCollector sweep each call turned level
        lookup into the dominant cost of a large detection run."""
        if self._levels_cache is None:
            if self.doc is None:
                self._levels_cache = []
                return self._levels_cache
            try:
                self._levels_cache = list(
                    FilteredElementCollector(self.doc).OfClass(Level).ToElements())
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


# ── Section 6: MCP summaries — JSON-safe dicts in millimetres ─────────────────
# Used by the MCP tools in core/server.py. Coordinates leave here in mm, in the
# project's internal coordinates (the space Revit's own create tools take);
# angles in degrees. Everything below is pure Python so dev/test_pointcloud_mcp.py
# can drive it without Revit.

MCP_DEFAULT_POINTS = 20000
MCP_MAX_POINTS = 100000      # tiled GetPoints keeps each engine query small
DETECTABLE = ('walls', 'floors', 'ceilings', 'doors', 'windows',
              'columns', 'stairs', 'roof')
_TYPE_TO_KEY = {'Wall': 'walls', 'Floor': 'floors', 'Ceiling': 'ceilings',
                'Door': 'doors', 'Window': 'windows', 'Column': 'columns',
                'Stair': 'stairs', 'Roof': 'roof'}


def _mm(value_ft):
    return round(float(value_ft) * MM_PER_FOOT, 1)


def clamp_point_budget(value):
    """MCP max_points argument -> a safe GetPoints budget."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return MCP_DEFAULT_POINTS
    return max(1000, min(MCP_MAX_POINTS, n))


def detection_settings(elements=None, min_wall_length_mm=None):
    """The wizard's default detector settings, limited to `elements`.

    Openings, floors, ceilings and columns use the detected walls as context
    (host walls, edge trimming, anti-MEP filters), so walls are always
    detected when any of them is wanted — summarize_detections() drops them
    from the answer if walls were not asked for.
    """
    wanted = set(elements or DETECTABLE)
    needs_walls = bool(wanted & {'walls', 'doors', 'windows', 'floors',
                                 'ceilings', 'columns'})
    settings = {
        'detect_wall':    needs_walls,
        'snap_tol':       1.0,
        'wall_min_len':   500.0,
        'detect_floor':   'floors' in wanted,
        'floor_zbin':     50.0,
        'floor_min_area': 1.0,
        'detect_ceiling': 'ceilings' in wanted,
        'ceil_zbin':      50.0,
        'detect_door':    'doors' in wanted,
        'door_min_w':     600.0,
        'door_min_h':     1800.0,
        'detect_window':  'windows' in wanted,
        'win_min_w':      400.0,
        'win_min_h':      400.0,
        'detect_column':  'columns' in wanted,
        'col_min':        200.0,
        'col_max':        600.0,
        'detect_stair':   'stairs' in wanted,
        'stair_riser':    165.0,
        'detect_roof':    'roof' in wanted,
        'roof_slope':     5.0,
    }
    if min_wall_length_mm is not None:
        try:
            settings['wall_min_len'] = max(100.0, float(min_wall_length_mm))
        except (TypeError, ValueError):
            pass
    return settings


def serialize_detection(element):
    """One DetectedElement -> dict with its geometry in mm."""
    d = element._data
    kind = element.Type
    try:
        confidence = int(str(element.ConfidenceText).rstrip('%'))
    except ValueError:
        confidence = None
    out = {'type': kind, 'level': element.LevelName,
           'dimensions': element.Dimensions, 'confidence_pct': confidence}
    if kind == 'Wall':
        a, half = d['angle'], d['length_ft'] / 2.0
        dx, dy = math.cos(a) * half, math.sin(a) * half
        out.update({
            'start_mm': [_mm(d['cx'] - dx), _mm(d['cy'] - dy)],
            'end_mm': [_mm(d['cx'] + dx), _mm(d['cy'] + dy)],
            'length_mm': _mm(d['length_ft']),
            'thickness_mm': _mm(d['thickness_ft']),
            'base_z_mm': _mm(d.get('base_z', 0.0)),
            'angle_deg': round(math.degrees(a) % 180.0, 2),
        })
    elif kind in ('Floor', 'Ceiling'):
        out.update({'z_mm': _mm(d['z_ft']),
                    'corners_mm': [[_mm(x), _mm(y)] for x, y in d['corners_xy']]})
    elif kind == 'Column':
        out.update({'center_mm': [_mm(d['cx']), _mm(d['cy'])],
                    'width_mm': _mm(d['width_ft']), 'depth_mm': _mm(d['depth_ft']),
                    'bottom_z_mm': _mm(d['z_bot_ft']), 'top_z_mm': _mm(d['z_top_ft'])})
    elif kind in ('Door', 'Window'):
        host = d['host_wall_data']
        a, u = host['angle'], d['u_center']
        # Same insertion point the wizard's ElementBuilder uses: along the
        # host wall from its centre, at the opening bottom (threshold / sill).
        out.update({
            'location_mm': [_mm(host['cx'] + math.cos(a) * u),
                            _mm(host['cy'] + math.sin(a) * u),
                            _mm(d['w_bottom'])],
            'width_mm': _mm(d['width_ft']), 'height_mm': _mm(d['height_ft']),
            'host_wall_center_mm': [_mm(host['cx']), _mm(host['cy'])],
            'host_wall_angle_deg': round(math.degrees(a) % 180.0, 2),
        })
    elif kind == 'Stair':
        out.update({'bottom_z_mm': _mm(d['z_bot_ft']), 'top_z_mm': _mm(d['z_top_ft']),
                    'tread_count': d['tread_count']})
    elif kind == 'Roof':
        out.update({'z_mm': _mm(d['z_ft']), 'slope_deg': round(d['slope_deg'], 1),
                    'corners_mm': [[_mm(x), _mm(y)] for x, y in d['corners_xy']]})
    return out


def summarize_detections(results, elements=None, limit_per_type=100):
    """Group serialized detections by kind, keeping only `elements`.

    Returns {'counts', 'detections', 'truncated'}; counts are always the full
    totals, `detections` lists at most limit_per_type per kind, highest
    confidence first.
    """
    wanted = set(elements or DETECTABLE)
    limit = max(1, int(limit_per_type or 100))
    groups = {}
    for el in results or []:
        key = _TYPE_TO_KEY.get(el.Type)
        if key is None or key not in wanted:
            continue
        groups.setdefault(key, []).append(el)
    counts, detections, truncated = {}, {}, []
    for key in DETECTABLE:
        if key not in wanted:
            continue
        items = [serialize_detection(e) for e in groups.get(key, [])]
        counts[key] = len(items)
        items.sort(key=lambda row: -(row['confidence_pct'] or 0))
        detections[key] = items[:limit]
        if len(items) > limit:
            truncated.append(key)
    return {'counts': counts, 'detections': detections, 'truncated': truncated}


def analyze_points(pts, levels=None, bin_mm=50.0, tolerance_mm=25.0, max_planes=20):
    """Statistics of a sampled cloud + how project Levels line up with it.

    pts: model-space (x, y, z) tuples in feet.
    levels: [(name, elevation_ft)] in the same internal coordinates.
    """
    n = len(pts)
    if not n:
        return {'sampled_points': 0}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    lo = [min(xs), min(ys), min(zs)]
    hi = [max(xs), max(ys), max(zs)]

    hist, z_min, bin_ft = z_histogram(pts, bin_mm)
    peaks = find_histogram_peaks(hist, z_min, bin_ft, min_ratio=0.05)
    # Strongest horizontal concentrations first, then listed bottom to top.
    peaks = sorted(sorted(peaks, key=lambda p: -p[1])[:max_planes], key=lambda p: p[0])

    cell = 500.0 / MM_PER_FOOT
    cells = set((int(math.floor(x / cell)), int(math.floor(y / cell))) for x, y in zip(xs, ys))
    footprint_m2 = round(len(cells) * 0.25, 1)

    tol_ft = float(tolerance_mm) / MM_PER_FOOT
    levels = list(levels or [])
    planes = []
    for z, count in peaks:
        plane = {'z_mm': _mm(z), 'points_in_band': count,
                 'share_pct': round(100.0 * count / n, 1)}
        if levels:
            name, elev = min(levels, key=lambda lv: abs(lv[1] - z))
            plane['nearest_level'] = name
            plane['offset_from_level_mm'] = _mm(z - elev)
        planes.append(plane)

    level_check = []
    for name, elev in sorted(levels, key=lambda lv: lv[1]):
        entry = {'level': name, 'elevation_mm': _mm(elev)}
        if peaks:
            z, _count = min(peaks, key=lambda p: abs(p[0] - elev))
            entry['nearest_plane_mm'] = _mm(z)
            entry['offset_mm'] = _mm(z - elev)
            entry['status'] = 'matches scan' if abs(z - elev) <= tol_ft else 'no scan plane within tolerance'
        else:
            entry['status'] = 'no horizontal planes found'
        level_check.append(entry)

    return {
        'sampled_points': n,
        'extents_mm': {'min': [_mm(v) for v in lo], 'max': [_mm(v) for v in hi]},
        'size_m': [round((hi[i] - lo[i]) * MM_PER_FOOT / 1000.0, 2) for i in range(3)],
        'footprint_m2': footprint_m2,
        'horizontal_planes': planes,
        'level_check': level_check,
        'tolerance_mm': float(tolerance_mm),
        'note': ('Sampled points, not the full scan: counts and shares describe the '
                 'sample. Planes are 50 mm bands with many points — floors, ceilings, '
                 'slab soffits or large horizontal surfaces such as roofs and tables.'),
    }
