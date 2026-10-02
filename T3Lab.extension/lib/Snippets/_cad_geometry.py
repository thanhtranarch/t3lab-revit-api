# -*- coding: utf-8 -*-
"""CAD to Elements — pure-Python 2D geometry and the mode table.

Nothing here imports clr or the Revit API, so every rule that decides WHAT gets
created (pairing parallel lines, chaining loose lines into closed outlines,
reading a column footprint, naming grids) runs and is unit-tested outside Revit
(`dev/test_cad_to_elements_geometry.py`). The Revit side
(`Snippets/_cad_revit.py`) only converts CAD geometry into these plain tuples
and turns the answers back into elements.

Units: every coordinate and length is in Revit internal feet unless a name
ends in `_mm`.

Shapes
------
segment  (x0, y0, x1, y1, layer)                    straight CAD line piece
edge     ("L", (x0, y0), (x1, y1), None)            line edge of an outline
         ("A", (x0, y0), (x1, y1), (mx, my))        arc edge, mid point on the arc
loop     [edge, ...] head-to-tail, closed
polygon  [(x, y), ...] (not repeated at the end)
"""
from __future__ import division

import math

MM_PER_FT = 304.8
FT_PER_MM = 1.0 / 304.8

TOL = 0.01              # ft (~3 mm): two points closer than this are the same
MERGE_TOL = 0.15        # ft (~46 mm): collinear gap / offset that still merges
PARALLEL_TOL = 0.998    # |cos| above this = parallel (~3.6 deg)
MAX_WALL_THICKNESS = 2.0  # ft (~610 mm)


def mm(value_mm):
    """Millimetres -> feet."""
    return value_mm * FT_PER_MM


def to_mm(value_ft):
    """Feet -> millimetres."""
    return value_ft * MM_PER_FT


# ═══════════════════════════════════════════════════════════════════════════
# MODE TABLE — one row per rail tile. The dialog and the XAML follow it:
# rail tile `btn_mode_<key>`, options grid `opt_<key>`.
# ═══════════════════════════════════════════════════════════════════════════

# count_kind -> header of the per-layer count column in the layer list
COUNT_HEADERS = {
    "lines": "LINES",
    "loops": "OUTLINES",
    "shapes": "SHAPES",
    "curves": "CURVES",
}

MODES = [
    dict(key="wall", title="Walls",
         desc="Trace paired parallel lines into walls; thickness comes from the line spacing.",
         verb="Create Walls", count_kind="lines", ai_label="Walls / Partitions"),
    dict(key="floor", title="Floors",
         desc="Turn closed outlines into floors; outlines inside outlines become openings.",
         verb="Create Floors", count_kind="loops", ai_label="Floors / Slabs"),
    dict(key="ceiling", title="Ceilings",
         desc="Turn closed outlines into ceilings at a height above the level.",
         verb="Create Ceilings", count_kind="loops", ai_label="Ceilings"),
    dict(key="room", title="Rooms",
         desc="Draw room separation lines from linework and place a room in every closed outline.",
         verb="Create Rooms", count_kind="loops", ai_label="Rooms / Room boundaries / Spaces"),
    dict(key="column", title="Columns",
         desc="Place columns on closed rectangles and circles, sized and rotated to match.",
         verb="Create Columns", count_kind="shapes", ai_label="Columns"),
    dict(key="beam", title="Beams",
         desc="Trace paired parallel lines into structural beams sized from the spacing.",
         verb="Create Beams", count_kind="lines", ai_label="Structural Beams / Framing"),
    dict(key="grid", title="Grids",
         desc="Turn axis lines into grids, numbered left to right and lettered bottom to top.",
         verb="Create Grids", count_kind="lines", ai_label="Grid / Axis lines"),
    dict(key="lines", title="Lines",
         desc="Copy linework into native model lines or detail lines on a chosen line style.",
         verb="Create Lines", count_kind="curves", ai_label="Linework to keep as Revit lines"),
    dict(key="mep", title="MEP Runs",
         desc="Turn single or double lines into ducts, pipes, cable trays or conduits.",
         verb="Create Ducts", count_kind="lines", ai_label="MEP runs"),
]

MODE_KEYS = [m["key"] for m in MODES]


def mode(key):
    """The MODES row for `key` (KeyError on an unknown key)."""
    for m in MODES:
        if m["key"] == key:
            return m
    raise KeyError(key)


MEP_CATEGORIES = [
    dict(key="duct", label="Duct", noun="ducts", verb="Create Ducts",
         type_label="duct type", system=True, double=True, height=True,
         width_label="WIDTH / DIAMETER (MM)", width=300.0, height_mm=250.0,
         offset=2800.0, ai_label="HVAC duct lines"),
    dict(key="pipe", label="Pipe", noun="pipes", verb="Create Pipes",
         type_label="pipe type", system=True, double=False, height=False,
         width_label="DIAMETER (MM)", width=100.0, height_mm=None,
         offset=2600.0, ai_label="Plumbing / mechanical pipe lines"),
    dict(key="tray", label="Cable Tray", noun="cable trays", verb="Create Cable Trays",
         type_label="cable tray type", system=False, double=True, height=True,
         width_label="WIDTH (MM)", width=300.0, height_mm=100.0,
         offset=2700.0, ai_label="Cable tray lines"),
    dict(key="conduit", label="Conduit", noun="conduits", verb="Create Conduits",
         type_label="conduit type", system=False, double=False, height=False,
         width_label="DIAMETER (MM)", width=25.0, height_mm=None,
         offset=2700.0, ai_label="Electrical conduit lines"),
]


def mep_category(key):
    for c in MEP_CATEGORIES:
        if c["key"] == key:
            return c
    raise KeyError(key)


# ═══════════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def parse_number(text, default, minimum=None, maximum=None):
    """Read a number typed by the user; `default` when empty/invalid/out of range.

    Returns (value, ok). `ok` is False when the text was not usable, so the
    caller can say which field it replaced instead of silently guessing.
    """
    try:
        raw = (text or "").strip().replace(",", ".")
        if not raw:
            return default, False
        value = float(raw)
    except (TypeError, ValueError):
        return default, False
    if math.isnan(value) or math.isinf(value):
        return default, False
    if minimum is not None and value < minimum:
        return default, False
    if maximum is not None and value > maximum:
        return default, False
    return value, True


def round_to(value, step):
    """Round to the nearest multiple of `step` (step <= 0 -> plain round)."""
    if not step or step <= 0:
        return int(round(value))
    return int(round(value / float(step)) * step)


def seg_length(s):
    return math.hypot(s[2] - s[0], s[3] - s[1])


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def beam_height_for_width(width_mm):
    """Standard beam depth (mm) for a detected beam width (mm)."""
    if width_mm <= 200:
        return 500
    if width_mm <= 300:
        return 600
    if width_mm <= 400:
        return 800
    if width_mm <= 500:
        return 1000
    return int(width_mm * 2)


# ═══════════════════════════════════════════════════════════════════════════
# COLLINEAR MERGE
# ═══════════════════════════════════════════════════════════════════════════

def merge_collinear(segs, gap_tol=MERGE_TOL, parallel_tol=PARALLEL_TOL):
    """Join collinear pieces that touch, overlap or leave a gap <= gap_tol.

    CAD drafts one wall face as several pieces (broken at every window or
    T-junction). Merging first makes each face one line, so the pairing step
    sees whole faces. Different layers are never merged together.
    """
    result = [tuple(s) for s in segs if seg_length(s) >= TOL]
    merged = True
    while merged:
        merged = False
        out = []
        used = [False] * len(result)
        for i in range(len(result)):
            if used[i]:
                continue
            used[i] = True
            x0, y0, x1, y1, layer = result[i]
            length = math.hypot(x1 - x0, y1 - y0)
            dx, dy = (x1 - x0) / length, (y1 - y0) / length
            for j in range(i + 1, len(result)):
                if used[j]:
                    continue
                ox0, oy0, ox1, oy1, olayer = result[j]
                if olayer != layer:
                    continue
                olen = math.hypot(ox1 - ox0, oy1 - oy0)
                if abs(dx * (ox1 - ox0) / olen + dy * (oy1 - oy0) / olen) < parallel_tol:
                    continue
                # both ends of the other piece must sit on this line
                off_a = abs((ox0 - x0) * dy - (oy0 - y0) * dx)
                off_b = abs((ox1 - x0) * dy - (oy1 - y0) * dx)
                if off_a > gap_tol or off_b > gap_tol:
                    continue
                ta = (ox0 - x0) * dx + (oy0 - y0) * dy
                tb = (ox1 - x0) * dx + (oy1 - y0) * dy
                lo, hi = min(ta, tb), max(ta, tb)
                if lo > length + gap_tol or hi < -gap_tol:
                    continue            # collinear but far apart
                t_start, t_end = min(0.0, lo), max(length, hi)
                nx0, ny0 = x0 + dx * t_start, y0 + dy * t_start
                nx1, ny1 = x0 + dx * t_end, y0 + dy * t_end
                x0, y0, x1, y1 = nx0, ny0, nx1, ny1
                length = math.hypot(x1 - x0, y1 - y0)
                dx, dy = (x1 - x0) / length, (y1 - y0) / length
                used[j] = True
                merged = True
            out.append((x0, y0, x1, y1, layer))
        result = out
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PARALLEL PAIRS -> CENTERLINES (walls, beams, double-line ducts/trays)
# ═══════════════════════════════════════════════════════════════════════════

def find_parallel_pairs(segs, max_sep=MAX_WALL_THICKNESS, min_sep=TOL,
                        min_overlap=0.2, same_side_tol=None):
    """Pair parallel lines and return their centerlines.

    For each line the closest parallel line(s) between min_sep and max_sep
    that overlap at least `min_overlap` of the shorter one become its pair;
    the centerline spans the UNION of both extents.

    Returns (centerlines, unpaired) where a centerline is
    (x0, y0, x1, y1, separation_ft, layer).
    """
    if same_side_tol is None:
        same_side_tol = mm(20)
    n = len(segs)
    paired = [False] * n
    centerlines = []
    dirs = []
    for s in segs:
        length = seg_length(s)
        if length > TOL:
            dirs.append(((s[2] - s[0]) / length, (s[3] - s[1]) / length, length))
        else:
            dirs.append((0.0, 0.0, 0.0))

    for i in range(n):
        if paired[i] or dirs[i][2] == 0:
            continue
        dxi, dyi, leni = dirs[i]
        sx, sy = segs[i][0], segs[i][1]
        candidates = []
        for j in range(n):
            if j == i or paired[j] or dirs[j][2] == 0:
                continue
            dxj, dyj, lenj = dirs[j]
            if abs(dxi * dxj + dyi * dyj) < PARALLEL_TOL:
                continue
            ax, ay, bx, by = segs[j][0], segs[j][1], segs[j][2], segs[j][3]
            dist_a = abs((ax - sx) * dyi - (ay - sy) * dxi)
            dist_b = abs((bx - sx) * dyi - (by - sy) * dxi)
            sep = (dist_a + dist_b) / 2.0
            if sep > max_sep or sep < max(min_sep, TOL):
                continue
            ta = (ax - sx) * dxi + (ay - sy) * dyi
            tb = (bx - sx) * dxi + (by - sy) * dyi
            overlap = min(leni, max(ta, tb)) - max(0.0, min(ta, tb))
            if overlap < min(leni, lenj) * min_overlap:
                continue
            candidates.append((sep, j, ta, tb))
        if not candidates:
            continue
        candidates.sort(key=lambda c: c[0])
        best = candidates[0][0]
        same_side = [c for c in candidates if abs(c[0] - best) < same_side_tol]

        ts = [0.0, leni]
        for c in same_side:
            ts.extend((c[2], c[3]))
        t0, t1 = min(ts), max(ts)
        if t1 - t0 < TOL:
            continue
        # which side of line i is the partner on?
        j0 = same_side[0][1]
        side = (segs[j0][0] - sx) * (-dyi) + (segs[j0][1] - sy) * dxi
        sign = 1.0 if side > 0 else -1.0
        ox, oy = -dyi * best / 2.0 * sign, dxi * best / 2.0 * sign
        centerlines.append((sx + dxi * t0 + ox, sy + dyi * t0 + oy,
                            sx + dxi * t1 + ox, sy + dyi * t1 + oy,
                            best, segs[i][4]))
        paired[i] = True
        for c in same_side:
            paired[c[1]] = True

    unpaired = [segs[k] for k in range(n) if not paired[k]]
    return centerlines, unpaired


def group_by_size(items, size_of, step_mm=1):
    """{rounded size in mm: [items]} — one Revit type per size."""
    groups = {}
    for it in items:
        key = round_to(to_mm(size_of(it)), step_mm)
        groups.setdefault(key, []).append(it)
    return groups


# ═══════════════════════════════════════════════════════════════════════════
# OUTLINES — chain loose edges into closed loops
# ═══════════════════════════════════════════════════════════════════════════

class _NodeIndex(object):
    """Snap points closer than `tol` to one shared node (grid hashing)."""

    def __init__(self, tol):
        self.tol = tol
        self.cells = {}
        self.points = []

    def _cell(self, p):
        return (int(math.floor(p[0] / self.tol)), int(math.floor(p[1] / self.tol)))

    def node(self, p):
        cx, cy = self._cell(p)
        for ix in (cx - 1, cx, cx + 1):
            for iy in (cy - 1, cy, cy + 1):
                for nid in self.cells.get((ix, iy), ()):
                    if _dist(self.points[nid], p) <= self.tol:
                        return nid
        nid = len(self.points)
        self.points.append((p[0], p[1]))
        self.cells.setdefault((cx, cy), []).append(nid)
        return nid


def _edge_dir_from(edge, forward):
    """Unit direction of travel when leaving the edge's start (or end)."""
    kind, a, b, mid = edge
    start, toward = (a, mid if kind == "A" else b) if forward else (b, mid if kind == "A" else a)
    dx, dy = toward[0] - start[0], toward[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    return dx / length, dy / length


def chain_loops(edges, tol=TOL):
    """Chain loose line/arc edges into closed loops (the faces of the drawing).

    Dangling chains are pruned first (an edge with a free end can never close).
    Every edge is then walked once in each direction, always taking the
    sharpest LEFT turn at a junction: that traces each enclosed face
    counter-clockwise, so two rooms sharing a wall line come out as two loops
    instead of one outline around both. The unbounded outside face comes out
    clockwise (negative area) and is dropped.
    Each loop's points are the shared node points, so consecutive edges meet
    exactly (Revit's CurveLoop insists on it).
    """
    index = _NodeIndex(tol)
    items = []
    seen_pairs = set()
    for e in edges:
        kind, a, b, mid = e
        if _dist(a, b) < tol:
            continue
        na, nb = index.node(a), index.node(b)
        if na == nb:
            continue
        if kind == "L":
            key = (min(na, nb), max(na, nb))
            if key in seen_pairs:
                continue            # the same line drawn twice
            seen_pairs.add(key)
        items.append((kind, na, nb, mid))

    adj = {}
    for k, it in enumerate(items):
        adj.setdefault(it[1], set()).add(k)
        adj.setdefault(it[2], set()).add(k)

    alive = set(range(len(items)))
    stack = [nid for nid, ks in adj.items() if len(ks) == 1]
    while stack:
        nid = stack.pop()
        live = [k for k in adj.get(nid, ()) if k in alive]
        if len(live) != 1:
            continue
        k = live[0]
        alive.discard(k)
        other = items[k][2] if items[k][1] == nid else items[k][1]
        if len([q for q in adj[other] if q in alive]) == 1:
            stack.append(other)

    pts = index.points

    def as_edge(k, forward):
        kind, na, nb, mid = items[k]
        a, b = (pts[na], pts[nb]) if forward else (pts[nb], pts[na])
        return (kind, a, b, mid)

    def head(half):
        k, forward = half
        return items[k][2] if forward else items[k][1]

    def next_half(half):
        """Outgoing half-edge at the head of `half` with the sharpest left turn."""
        k, forward = half
        ix, iy = _edge_dir_from(as_edge(k, forward), False)
        in_dx, in_dy = -ix, -iy                 # direction of travel on arrival
        node = head(half)
        best, best_turn = None, None
        for q in adj.get(node, ()):
            if q not in alive:
                continue
            q_forward = items[q][1] == node
            if q == k and q_forward != forward:
                continue                        # never straight back on itself
            ox, oy = _edge_dir_from(as_edge(q, q_forward), True)
            turn = math.atan2(in_dx * oy - in_dy * ox, in_dx * ox + in_dy * oy)
            if turn >= math.pi - 1e-9:
                turn = -math.pi                 # a U-turn is the last resort
            if best_turn is None or turn > best_turn:
                best, best_turn = (q, q_forward), turn
        return best

    loops = []
    visited = set()
    for k in sorted(alive):
        for forward in (True, False):
            start = (k, forward)
            if start in visited:
                continue
            face = []
            half = start
            for _ in range(2 * len(items) + 2):
                if half is None or half in visited:
                    break
                visited.add(half)
                face.append(half)
                half = next_half(half)
            if half != start or not face:
                continue
            kinds = [items[q][0] for q, _f in face]
            if len(face) < 3 and "A" not in kinds:
                continue
            loop = [as_edge(q, f) for q, f in face]
            if polygon_area(loop_polygon(loop)) > tol * tol:
                loops.append(loop)
    return loops


def loop_from_polygon(points):
    """Closed polygon -> loop of line edges (repeated end point dropped)."""
    pts = [tuple(p[:2]) for p in points]
    if len(pts) > 1 and _dist(pts[0], pts[-1]) < TOL:
        pts = pts[:-1]
    clean = []
    for p in pts:
        if not clean or _dist(clean[-1], p) >= TOL:
            clean.append(p)
    if len(clean) > 2 and _dist(clean[0], clean[-1]) < TOL:
        clean.pop()
    if len(clean) < 3:
        return None
    return [("L", clean[i], clean[(i + 1) % len(clean)], None) for i in range(len(clean))]


def loop_from_circle(cx, cy, r):
    """Full circle -> two half-arc edges (a CurveLoop cannot hold a closed arc)."""
    a, b = (cx + r, cy), (cx - r, cy)
    return [("A", a, b, (cx, cy + r)), ("A", b, a, (cx, cy - r))]


def reverse_loop(loop):
    """Same loop walked the other way (arc mid points stay on the arc)."""
    return [(kind, b, a, mid) for kind, a, b, mid in reversed(loop)]


def orient_loop(loop, ccw=True):
    """Outer profiles counter-clockwise, openings clockwise."""
    area = polygon_area(loop_polygon(loop))
    if (area > 0) != bool(ccw):
        return reverse_loop(loop)
    return loop


def centerline_rect(x0, y0, x1, y1, half_width):
    """Rectangle polygon around a centerline (wall/beam footprint)."""
    length = math.hypot(x1 - x0, y1 - y0)
    if length < TOL or half_width <= 0:
        return None
    nx, ny = -(y1 - y0) / length * half_width, (x1 - x0) / length * half_width
    return [(x0 - nx, y0 - ny), (x1 - nx, y1 - ny), (x1 + nx, y1 + ny), (x0 + nx, y0 + ny)]


def loop_polygon(loop, arc_steps=8):
    """Polygon approximation of a loop (arcs sampled) for area/containment."""
    poly = []
    for kind, a, b, mid in loop:
        poly.append(a)
        if kind == "A" and mid is not None:
            poly.extend(_arc_samples(a, b, mid, arc_steps))
    return poly


def _arc_samples(a, b, mid, steps):
    """Points strictly between a and b along the circle through a, mid, b."""
    c = _circumcenter(a, mid, b)
    if c is None:
        return [mid]
    r = _dist(c, a)
    ang_a = math.atan2(a[1] - c[1], a[0] - c[0])
    ang_m = math.atan2(mid[1] - c[1], mid[0] - c[0])
    ang_b = math.atan2(b[1] - c[1], b[0] - c[0])
    sweep = (ang_b - ang_a) % (2 * math.pi)
    if (ang_m - ang_a) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi          # the arc runs clockwise
    return [(c[0] + r * math.cos(ang_a + sweep * k / steps),
             c[1] + r * math.sin(ang_a + sweep * k / steps)) for k in range(1, steps)]


def _circumcenter(a, b, c):
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d
    uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d
    return (ux, uy)


# ═══════════════════════════════════════════════════════════════════════════
# POLYGONS
# ═══════════════════════════════════════════════════════════════════════════

def polygon_area(poly):
    """Signed area (counter-clockwise > 0)."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def polygon_centroid(poly):
    a = polygon_area(poly)
    if abs(a) < 1e-12:
        n = float(len(poly)) or 1.0
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return (cx / (6 * a), cy / (6 * a))


def point_in_polygon(pt, poly):
    """Even-odd ray cast. Points exactly on the boundary may go either way."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def interior_point(poly, holes=()):
    """A point strictly inside `poly` and outside every hole, or None.

    The centroid when it qualifies (convex rooms); otherwise the middle of the
    widest inside span on a few horizontal scan lines (L/U-shaped rooms).
    """
    if len(poly) < 3:
        return None

    def ok(p):
        return point_in_polygon(p, poly) and not any(point_in_polygon(p, h) for h in holes)

    c = polygon_centroid(poly)
    if ok(c):
        return c
    x_min, y_min, x_max, y_max = _bbox(poly)
    rings = [poly] + list(holes)
    best, best_w = None, 0.0
    for frac in (0.5, 0.25, 0.75, 0.375, 0.625, 0.125, 0.875):
        y = y_min + (y_max - y_min) * frac
        xs = []
        for ring in rings:
            n = len(ring)
            for i in range(n):
                (xa, ya), (xb, yb) = ring[i], ring[(i + 1) % n]
                if (ya > y) != (yb > y):
                    xs.append(xa + (y - ya) * (xb - xa) / (yb - ya))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            w = xs[k + 1] - xs[k]
            p = ((xs[k] + xs[k + 1]) / 2.0, y)
            if w > best_w and ok(p):
                best, best_w = p, w
    return best


def _parents(polys):
    """(areas, order, parent) — parent[i] = smallest polygon containing i, or None."""
    n = len(polys)
    areas = [abs(polygon_area(p)) for p in polys]
    boxes = [_bbox(p) if p else (0, 0, 0, 0) for p in polys]
    order = sorted(range(n), key=lambda i: -areas[i])
    parent = {}
    for pos, i in enumerate(order):
        probe = interior_point(polys[i]) if areas[i] > 1e-9 else None
        parent[i] = None
        if probe is None:
            continue
        for j in reversed(order[:pos]):        # smallest larger polygon first
            if areas[j] <= areas[i]:
                continue
            bx = boxes[j]
            if not (bx[0] <= probe[0] <= bx[2] and bx[1] <= probe[1] <= bx[3]):
                continue
            if point_in_polygon(probe, polys[j]):
                parent[i] = j
                break
    return areas, order, parent


def nest_loops(polys):
    """Group polygons into (outer_index, [hole_indices]).

    Even nesting depth = an outline, odd depth = an opening in the outline that
    directly contains it. An island inside an opening is an outline again.
    """
    areas, order, parent = _parents(polys)

    def depth(i):
        d = 0
        while parent.get(i) is not None:
            i = parent[i]
            d += 1
        return d

    groups = {}
    for i in order:
        if areas[i] <= 1e-9:
            continue
        if depth(i) % 2 == 0:
            groups.setdefault(i, [])
    for i in order:
        if areas[i] <= 1e-9:
            continue
        if depth(i) % 2 == 1 and parent[i] in groups:
            groups[parent[i]].append(i)
    return [(i, groups[i]) for i in order if i in groups]


def room_points(polys, min_area=None, min_width=None):
    """One room point per enclosed outline: (points, too_small_count).

    Unlike floors, an outline inside another outline is a room of its own (an
    office inside a hall); the inner outline is only cut out of its parent so
    the parent's point never lands in the child. Outlines smaller than
    `min_area` or narrower than `min_width` (2*area/perimeter — the strip
    between the two face lines of a wall) are skipped.
    """
    if min_area is None:
        min_area = 1.0 / (0.3048 ** 2)          # 1 m2
    if min_width is None:
        min_width = mm(500)
    areas, order, parent = _parents(polys)
    children = {}
    for i in order:
        if parent.get(i) is not None:
            children.setdefault(parent[i], []).append(i)
    points, small = [], 0
    for i in order:
        poly = polys[i]
        perim = sum(_dist(poly[k], poly[(k + 1) % len(poly)]) for k in range(len(poly))) or 1.0
        if areas[i] < min_area or 2.0 * areas[i] / perim < min_width:
            small += 1
            continue
        pt = interior_point(poly, [polys[c] for c in children.get(i, [])])
        if pt is not None:
            points.append(pt)
    return points, small


def dedupe_polygons(polys, tol=TOL * 5):
    """Indices of polygons left after dropping copies (same area and centroid)."""
    kept = []
    sigs = []
    for i, p in enumerate(polys):
        a = abs(polygon_area(p))
        c = polygon_centroid(p)
        dup = False
        for (ka, kc) in sigs:
            if abs(ka - a) <= max(1e-6, 0.01 * max(ka, a)) and _dist(kc, c) <= tol:
                dup = True
                break
        if not dup:
            kept.append(i)
            sigs.append((a, c))
    return kept


# ═══════════════════════════════════════════════════════════════════════════
# COLUMN FOOTPRINTS
# ═══════════════════════════════════════════════════════════════════════════

def _simplify(poly, tol=TOL):
    """Drop repeated and collinear vertices."""
    pts = []
    for p in poly:
        if not pts or _dist(pts[-1], p) >= tol:
            pts.append(p)
    if len(pts) > 1 and _dist(pts[0], pts[-1]) < tol:
        pts.pop()
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if abs(cross) <= tol * max(_dist(a, b), _dist(b, c), tol):
                pts.pop(i)
                changed = True
                break
    return pts


def rectangle_footprint(poly, angle_tol=0.02, side_tol=0.02):
    """Read a rectangle: dict(shape, cx, cy, width, depth, angle) or None.

    `angle` (radians) is the rotation of the `width` side, normalised to
    (-45deg, 45deg] so an axis-aligned column needs no rotation and its width
    is its X extent.
    """
    pts = _simplify(poly)
    if len(pts) != 4:
        return None
    sides = []
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        length = _dist(a, b)
        if length < TOL:
            return None
        sides.append(((b[0] - a[0]) / length, (b[1] - a[1]) / length, length))
    for i in range(4):
        d0, d1 = sides[i], sides[(i + 1) % 4]
        if abs(d0[0] * d1[0] + d0[1] * d1[1]) > angle_tol:
            return None
    if abs(sides[0][2] - sides[2][2]) > side_tol * max(sides[0][2], sides[2][2]) or \
            abs(sides[1][2] - sides[3][2]) > side_tol * max(sides[1][2], sides[3][2]):
        return None
    width, depth = sides[0][2], sides[1][2]
    angle = math.atan2(sides[0][1], sides[0][0])
    # fold into (-90, 90] then into (-45, 45] swapping width/depth
    while angle <= -math.pi / 2:
        angle += math.pi
    while angle > math.pi / 2:
        angle -= math.pi
    if angle > math.pi / 4 + 1e-9:
        angle -= math.pi / 2
        width, depth = depth, width
    elif angle <= -math.pi / 4 + 1e-9:
        angle += math.pi / 2
        width, depth = depth, width
    cx = sum(p[0] for p in pts) / 4.0
    cy = sum(p[1] for p in pts) / 4.0
    return dict(shape="rect", cx=cx, cy=cy, width=width, depth=depth, angle=angle)


def circle_footprint(cx, cy, r):
    return dict(shape="round", cx=cx, cy=cy, width=2 * r, depth=2 * r, angle=0.0)


def filter_footprints(footprints, min_mm=100.0, max_mm=3000.0, center_tol=None):
    """Drop shapes outside the size window and keep the LARGEST shape per centre.

    CAD columns are often drawn twice (finish outline + structure, or outline +
    hatch boundary); one column per centre is what the user wants.
    """
    if center_tol is None:
        center_tol = mm(25)
    sized = [f for f in footprints
             if min_mm <= to_mm(min(f["width"], f["depth"]))
             and to_mm(max(f["width"], f["depth"])) <= max_mm]
    sized.sort(key=lambda f: -(f["width"] * f["depth"]))
    kept = []
    for f in sized:
        if any(_dist((f["cx"], f["cy"]), (k["cx"], k["cy"])) <= center_tol for k in kept):
            continue
        kept.append(f)
    return kept


def footprint_type_name(fp, step_mm):
    """Type name for a footprint size: '400x600mm' or 'D500mm'."""
    w = round_to(to_mm(fp["width"]), step_mm)
    if fp["shape"] == "round":
        return "D{}mm".format(w)
    return "{}x{}mm".format(w, round_to(to_mm(fp["depth"]), step_mm))


# ═══════════════════════════════════════════════════════════════════════════
# GRIDS
# ═══════════════════════════════════════════════════════════════════════════

def collapse_axis_lines(segs, min_len=0.0, angle_tol=math.radians(0.5), offset_tol=TOL * 3):
    """One line per CAD axis: collinear pieces (dashed axes) become one line
    spanning their union. Returns [(x0, y0, x1, y1)]."""
    groups = []          # [theta, dx, dy, offset, t_min, t_max]
    for s in segs:
        length = seg_length(s)
        if length < TOL:
            continue
        theta = math.atan2(s[3] - s[1], s[2] - s[0]) % math.pi
        if theta >= math.pi - angle_tol:
            theta = 0.0
        hit = None
        for g in groups:
            diff = abs(theta - g[0])
            diff = min(diff, math.pi - diff)
            if diff > angle_tol:
                continue
            dx, dy = g[1], g[2]
            off0 = s[1] * dx - s[0] * dy
            off1 = s[3] * dx - s[2] * dy
            if abs(off0 - g[3]) <= offset_tol and abs(off1 - g[3]) <= offset_tol:
                hit = g
                break
        if hit is None:
            dx, dy = math.cos(theta), math.sin(theta)
            hit = [theta, dx, dy, s[1] * dx - s[0] * dy, float("inf"), float("-inf")]
            groups.append(hit)
        dx, dy = hit[1], hit[2]
        for (x, y) in ((s[0], s[1]), (s[2], s[3])):
            t = x * dx + y * dy
            hit[4] = min(hit[4], t)
            hit[5] = max(hit[5], t)
    out = []
    for theta, dx, dy, off, t0, t1 in groups:
        if t1 - t0 < max(min_len, TOL):
            continue
        # point on the line: t*d + off*n where n = (-dy, dx)
        nx, ny = -dy, dx
        out.append((t0 * dx + off * nx, t0 * dy + off * ny,
                    t1 * dx + off * nx, t1 * dy + off * ny))
    return out


_LETTERS = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in "IO"]


def grid_letter(index):
    """0 -> A ... 23 -> Z, 24 -> AA (I and O skipped: they read as 1 and 0)."""
    name = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, len(_LETTERS))
        name = _LETTERS[rem] + name
    return name


def letter_index(text):
    """Inverse of grid_letter; 0 for anything that is not a grid letter."""
    text = (text or "").strip().upper()
    if not text or any(c not in _LETTERS for c in text):
        return 0
    n = 0
    for c in text:
        n = n * len(_LETTERS) + _LETTERS.index(c) + 1
    return n - 1


def name_grids(lines, start_number=1, start_letter="A"):
    """Names for axis lines: steep lines get numbers left->right, flat lines
    get letters bottom->top. Returns a list aligned with `lines`."""
    steep, flat = [], []
    for i, (x0, y0, x1, y1) in enumerate(lines):
        theta = math.atan2(y1 - y0, x1 - x0) % math.pi
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if math.pi / 4 < theta < 3 * math.pi / 4:
            steep.append((mx, my, i))
        else:
            flat.append((my, mx, i))
    names = [None] * len(lines)
    for k, (_mx, _my, i) in enumerate(sorted(steep)):
        names[i] = str(int(start_number) + k)
    first = letter_index(start_letter)
    for k, (_my, _mx, i) in enumerate(sorted(flat)):
        names[i] = grid_letter(first + k)
    return names


def extend_line(x0, y0, x1, y1, ext):
    length = math.hypot(x1 - x0, y1 - y0)
    if length < TOL or not ext:
        return (x0, y0, x1, y1)
    dx, dy = (x1 - x0) / length, (y1 - y0) / length
    return (x0 - dx * ext, y0 - dy * ext, x1 + dx * ext, y1 + dy * ext)


def same_axis(a, b, angle_tol=math.radians(0.5), offset_tol=TOL * 3):
    """True when two lines lie on the same infinite axis (grid already there)."""
    ta = math.atan2(a[3] - a[1], a[2] - a[0]) % math.pi
    tb = math.atan2(b[3] - b[1], b[2] - b[0]) % math.pi
    diff = abs(ta - tb)
    if min(diff, math.pi - diff) > angle_tol:
        return False
    dx, dy = math.cos(ta), math.sin(ta)
    off_a = a[1] * dx - a[0] * dy
    return (abs(b[1] * dx - b[0] * dy - off_a) <= offset_tol and
            abs(b[3] * dx - b[2] * dy - off_a) <= offset_tol)
