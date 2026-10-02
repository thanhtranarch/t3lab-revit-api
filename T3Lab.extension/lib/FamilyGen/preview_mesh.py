# -*- coding: utf-8 -*-
"""Pure-Python tessellation of a FamiGen family schema for the review pane.

No Revit, no WPF: the dialog turns the returned triangles into a WPF
``Viewport3D`` scene, and dev/test_famigen_preview_mesh.py exercises this file
on its own. Coordinates stay in millimeters (the schema's unit).

The interpretation mirrors ``FamilyGen.builder`` so the preview shows what
Revit will be asked to build:

* profiles are projected onto their sketch plane (``sketch_plane_x/y/z``, default
  z = 0) and arcs use the same plane axes (z: +X,+Y; x: +Y,+Z; y: +Z,+X);
* Extrusion offsets and Blend base/top offsets are measured from that plane
  along its normal; a Blend without explicit offsets takes the height its loop
  coordinates were drawn at;
* Revolution turns the profile about the projected axis (right-hand rule);
* Sweep profiles are used as drawn and carried along the path - an
  approximation (parallel transport, no mitre scaling at sharp corners);
* Cylinder is a ring between ``start`` and ``end``.

It is a preview, not a validator: what Revit rejects (self-intersecting
profiles, impossible blends) can still look plausible here.
"""
import math

FULL_TURN = 6.283185307
CIRCLE_SEGMENTS = 48
SPLINE_SUBDIVISIONS = 8
MAX_LOOP_POINTS = 400
MAX_TRIANGLES = 250000
SNAP_MM = 2.0          # same tolerance the builder uses to read a blend height
MIN_LEN_MM = 1.0

PLANE_AXES = {
    'z': ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    'x': ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    'y': ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
}
_NORMAL_INDEX = {'x': 0, 'y': 1, 'z': 2}

DEFAULT_YAW = -0.9      # camera from the front-right, like Revit's default 3D view
DEFAULT_PITCH = 0.45
FIELD_OF_VIEW = 45.0


# ── vector helpers ───────────────────────────────────────────────────────────

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _length(a)
    if n < 1e-12:
        raise ValueError('zero-length direction')
    return (a[0] / n, a[1] / n, a[2] / n)


def _perpendicular(d):
    ref = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = _unit(_cross(d, ref))
    return u, _cross(d, u)


def _rotate(p, origin, axis, angle):
    """Rotate point `p` about the line (origin, unit axis) by `angle` (Rodrigues)."""
    v = _sub(p, origin)
    c, s = math.cos(angle), math.sin(angle)
    term = _add(_add(_mul(v, c), _mul(_cross(axis, v), s)),
                _mul(axis, _dot(axis, v) * (1.0 - c)))
    return _add(origin, term)


def _rotation_between(a, b):
    """Function rotating vectors by the minimal rotation taking unit a to unit b."""
    axis = _cross(a, b)
    s = _length(axis)
    c = max(-1.0, min(1.0, _dot(a, b)))
    if s < 1e-9:
        if c > 0:
            return lambda v: v
        u, _ = _perpendicular(a)
        return lambda v: _rotate(v, (0.0, 0.0, 0.0), u, math.pi)
    axis = _mul(axis, 1.0 / s)
    angle = math.atan2(s, c)
    return lambda v: _rotate(v, (0.0, 0.0, 0.0), axis, angle)


# ── schema reading ───────────────────────────────────────────────────────────

def plane_info(geom):
    """('x'|'y'|'z', offset_mm) of the sketch plane a geometry entry uses."""
    for kind in ('x', 'y'):
        key = 'sketch_plane_' + kind
        if key in geom:
            return (kind, float(geom[key]))
    return ('z', float(geom.get('sketch_plane_z', 0.0)))


def _point(coords, plane=None):
    p = [float(coords[0]), float(coords[1]), float(coords[2])]
    if plane is not None:
        p[_NORMAL_INDEX[plane[0]]] = plane[1]
    return tuple(p)


def _arc_points(center, rx, ry, a0, a1, ax, ay):
    span = a1 - a0
    n = max(2, int(math.ceil(abs(span) / (FULL_TURN / CIRCLE_SEGMENTS))))
    out = []
    for i in range(n + 1):
        a = a0 + span * i / float(n)
        out.append(_add(center, _add(_mul(ax, rx * math.cos(a)), _mul(ay, ry * math.sin(a)))))
    return out


def _three_point_arc(p0, p1, pm):
    """Points from p0 to p1 through pm on their circle (a line if collinear)."""
    a, b = _sub(p1, p0), _sub(pm, p0)
    n = _cross(a, b)
    nn = _dot(n, n)
    if nn < 1e-12:
        return [p0, p1]
    # circumcentre of the triangle (p0, p1, pm)
    term = _add(_mul(_cross(n, a), _dot(b, b)), _mul(_cross(b, n), _dot(a, a)))
    center = _add(p0, _mul(term, 1.0 / (2.0 * nn)))
    radius = _length(_sub(p0, center))
    e1 = _unit(_sub(p0, center))
    e2 = _unit(_cross(_unit(n), e1))

    def angle(q):
        d = _sub(q, center)
        return math.atan2(_dot(d, e2), _dot(d, e1)) % FULL_TURN

    # p0 sits at angle 0: sweep forward when the mid point comes first,
    # otherwise backward, so the arc always passes through `mid`.
    am, a1 = angle(pm), angle(p1)
    end = a1 if am <= a1 else a1 - FULL_TURN
    pts = _arc_points(center, radius, radius, 0.0, end, e1, e2)
    pts[0], pts[-1] = p0, p1          # exact ends, so chained segments meet
    return pts


def _catmull_rom(points):
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    ext = [points[0]] + list(points) + [points[-1]]
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for k in range(1, SPLINE_SUBDIVISIONS + 1):
            t = k / float(SPLINE_SUBDIVISIONS)
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t
                       + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                       + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)
                for j in range(3)))
    return out


def sample_segment(seg, plane=None):
    """3D points (mm) along one schema segment, first and last included."""
    kind = seg.get('type', 'Line')
    ax, ay = PLANE_AXES[plane[0] if plane else 'z']
    if kind == 'Line':
        return [_point(seg['start'], plane), _point(seg['end'], plane)]
    if kind in ('Arc3P', 'ArcThreePoint'):
        return _three_point_arc(_point(seg['start'], plane), _point(seg['end'], plane),
                                _point(seg['mid'], plane))
    if kind == 'Spline':
        return _catmull_rom([_point(p, plane) for p in seg.get('points') or []])
    if kind in ('Arc', 'Circle'):
        center = _point(seg['center'], plane)
        r = float(seg['radius'])
        if kind == 'Circle':
            a0, a1 = 0.0, FULL_TURN
        else:
            a0, a1 = float(seg.get('start_angle', 0.0)), float(seg.get('end_angle', FULL_TURN))
        return _arc_points(center, r, r, a0, a1, ax, ay)
    if kind == 'Ellipse':
        center = _point(seg['center'], plane)
        a0, a1 = float(seg.get('start_angle', 0.0)), float(seg.get('end_angle', FULL_TURN))
        return _arc_points(center, float(seg['radius_x']), float(seg['radius_y']), a0, a1, ax, ay)
    raise ValueError('unsupported segment type {!r}'.format(kind))


def sample_loop(segments, plane=None, closed=True):
    """Ordered points of a chain of segments, duplicates and closure removed."""
    pts = []
    for seg in segments or []:
        for p in sample_segment(seg, plane):
            if not pts or _length(_sub(p, pts[-1])) > 1e-6:
                pts.append(p)
    if closed and len(pts) > 1 and _length(_sub(pts[0], pts[-1])) <= 1e-6:
        pts.pop()
    if len(pts) > MAX_LOOP_POINTS:
        step = int(math.ceil(len(pts) / float(MAX_LOOP_POINTS)))
        pts = pts[::step]
    return pts


def _to_uv(points, plane):
    ax, ay = PLANE_AXES[plane[0]]
    return [(_dot(p, ax), _dot(p, ay)) for p in points]


def _from_uvw(u, v, w, plane):
    ax, ay = PLANE_AXES[plane[0]]
    n = _cross(ax, ay)
    return _add(_add(_mul(ax, u), _mul(ay, v)), _mul(n, w))


# ── 2D polygon triangulation (ear clipping with hole bridging) ──────────────

def signed_area(uv):
    area = 0.0
    for i in range(len(uv)):
        x0, y0 = uv[i]
        x1, y1 = uv[(i + 1) % len(uv)]
        area += x0 * y1 - x1 * y0
    return 0.5 * area


def _cross2(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(p1, p2, q1, q2):
    d1, d2 = _cross2(q1, q2, p1), _cross2(q1, q2, p2)
    d3, d4 = _cross2(p1, p2, q1), _cross2(p1, p2, q2)
    return ((d1 > 1e-9 and d2 < -1e-9) or (d1 < -1e-9 and d2 > 1e-9)) and \
           ((d3 > 1e-9 and d4 < -1e-9) or (d3 < -1e-9 and d4 > 1e-9))


def _point_in_triangle(p, a, b, c):
    d1, d2, d3 = _cross2(a, b, p), _cross2(b, c, p), _cross2(c, a, p)
    has_neg = d1 < -1e-12 or d2 < -1e-12 or d3 < -1e-12
    has_pos = d1 > 1e-12 or d2 > 1e-12 or d3 > 1e-12
    return not (has_neg and has_pos)


def triangulate(outer, holes=()):
    """Triangulate a polygon with holes.

    Returns (points, triangles): `points` is outer + hole vertices (2D) and
    each triangle is three indices into it, counter-clockwise.
    """
    outer = list(outer)
    if len(outer) < 3:
        return outer, []
    points = list(outer)
    ring = list(range(len(outer)))
    if signed_area(outer) < 0:
        ring.reverse()
    hole_rings = []
    for hole in holes or ():
        if len(hole) < 3:
            continue
        start = len(points)
        points.extend(hole)
        idx = list(range(start, start + len(hole)))
        if signed_area(hole) > 0:
            idx.reverse()
        hole_rings.append(idx)

    hole_rings.sort(key=lambda h: -max(points[i][0] for i in h))
    for h_index, hole in enumerate(hole_rings):
        m_pos = max(range(len(hole)), key=lambda k: points[hole[k]][0])
        m = points[hole[m_pos]]
        others = [r for r in hole_rings[h_index + 1:]]
        edges = [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]
        for r in others + [hole]:
            edges.extend((r[i], r[(i + 1) % len(r)]) for i in range(len(r)))
        candidates = sorted(range(len(ring)),
                            key=lambda k: (points[ring[k]][0] - m[0]) ** 2
                            + (points[ring[k]][1] - m[1]) ** 2)
        bridge = candidates[0] if candidates else 0
        for k in candidates:
            p = points[ring[k]]
            if not any(_segments_cross(m, p, points[a], points[b]) for a, b in edges
                       if a not in (ring[k], hole[m_pos]) and b not in (ring[k], hole[m_pos])):
                bridge = k
                break
        rotated = hole[m_pos:] + hole[:m_pos]
        ring = ring[:bridge + 1] + rotated + [hole[m_pos], ring[bridge]] + ring[bridge + 1:]

    triangles = []
    work = list(ring)
    guard = 0
    while len(work) > 3 and guard < 4 * len(ring) + 16:
        guard += 1
        n = len(work)
        reflex = [work[i] for i in range(n)
                  if _cross2(points[work[i - 1]], points[work[i]], points[work[(i + 1) % n]]) <= 0]
        clipped = False
        for i in range(n):
            ia, ib, ic = work[i - 1], work[i], work[(i + 1) % n]
            a, b, c = points[ia], points[ib], points[ic]
            if _cross2(a, b, c) <= 1e-12:
                continue
            blocked = False
            for r in reflex:
                if r in (ia, ib, ic):
                    continue
                pr = points[r]
                if pr in (a, b, c):
                    continue
                if _point_in_triangle(pr, a, b, c):
                    blocked = True
                    break
            if blocked:
                continue
            triangles.append((ia, ib, ic))
            del work[i]
            clipped = True
            break
        if not clipped:
            # Degenerate or self-intersecting input: fan the rest rather than
            # dropping the face - this is a preview, not a validator.
            for i in range(1, len(work) - 1):
                triangles.append((work[0], work[i], work[i + 1]))
            work = []
            break
    if len(work) == 3:
        triangles.append(tuple(work))
    return points, triangles


# ── meshes ───────────────────────────────────────────────────────────────────

class Mesh(object):
    """One schema part: positions (mm) and triangle indices."""

    def __init__(self, label, kind, material=None, is_solid=True, index=0):
        self.label = label
        self.kind = kind
        self.material = material
        self.is_solid = is_solid
        self.index = index
        self.positions = []
        self.triangles = []

    def add_vertices(self, pts):
        base = len(self.positions)
        self.positions.extend(pts)
        return base

    def add_cap(self, points3d, tri_indices, flip=False):
        base = self.add_vertices(points3d)
        for a, b, c in tri_indices:
            self.triangles.append((base + a, base + c, base + b) if flip else (base + a, base + b, base + c))

    def add_strip(self, row_a, row_b, closed):
        """Quads between two equally long point rows (row_a[i] -> row_b[i])."""
        n = len(row_a)
        if n < 2 or n != len(row_b):
            return
        base = self.add_vertices(list(row_a) + list(row_b))
        count = n if closed else n - 1
        for i in range(count):
            j = (i + 1) % n
            a0, a1, b0, b1 = base + i, base + j, base + n + i, base + n + j
            self.triangles.append((a0, a1, b1))
            self.triangles.append((a0, b1, b0))

    def add_flat_walls(self, loop_a, loop_b):
        """Side faces with their own vertices so each face shades flat."""
        n = len(loop_a)
        for i in range(n):
            j = (i + 1) % n
            self.add_strip([loop_a[i], loop_a[j]], [loop_b[i], loop_b[j]], closed=False)


class PreviewModel(object):
    def __init__(self):
        self.meshes = []
        self.warnings = []
        self.bbox_min = None
        self.bbox_max = None
        self.triangle_count = 0

    @property
    def is_empty(self):
        return not any(m.triangles for m in self.meshes)

    def size_mm(self):
        if self.bbox_min is None:
            return (0.0, 0.0, 0.0)
        return tuple(self.bbox_max[i] - self.bbox_min[i] for i in range(3))

    def material_counts(self):
        counts = {}
        for mesh in self.meshes:
            if mesh.is_solid and mesh.material and mesh.triangles:
                counts[mesh.material] = counts.get(mesh.material, 0) + 1
        return counts


def _prism(mesh, outer, holes, plane, w0, w1):
    outer_uv = _to_uv(outer, plane)
    holes_uv = [_to_uv(h, plane) for h in holes]
    pts, tris = triangulate(outer_uv, holes_uv)
    bottom = [_from_uvw(u, v, w0, plane) for u, v in pts]
    top = [_from_uvw(u, v, w1, plane) for u, v in pts]
    mesh.add_cap(bottom, tris, flip=True)
    mesh.add_cap(top, tris)
    for loop in [outer_uv] + holes_uv:
        mesh.add_flat_walls([_from_uvw(u, v, w0, plane) for u, v in loop],
                            [_from_uvw(u, v, w1, plane) for u, v in loop])


def _loop_plane_offset(segs, plane):
    """Constant offset of raw segment coordinates from the sketch plane (or 0)."""
    idx = _NORMAL_INDEX[plane[0]]
    vals = []
    for seg in segs or []:
        for key in ('start', 'end', 'center', 'mid'):
            if key in seg:
                try:
                    vals.append(float(seg[key][idx]))
                except (TypeError, ValueError, IndexError):
                    pass
        for p in seg.get('points') or []:
            try:
                vals.append(float(p[idx]))
            except (TypeError, ValueError, IndexError):
                pass
    if not vals or max(vals) - min(vals) > SNAP_MM:
        return 0.0
    return (min(vals) + max(vals)) / 2.0 - plane[1]


def _resample(uv, n):
    """`n` points evenly spaced by arc length around a closed 2D loop."""
    m = len(uv)
    lengths = [math.hypot(uv[(i + 1) % m][0] - uv[i][0], uv[(i + 1) % m][1] - uv[i][1])
               for i in range(m)]
    total = sum(lengths)
    if total <= 1e-9:
        return [uv[0]] * n
    out, seg, acc = [], 0, 0.0
    for k in range(n):
        target = total * k / float(n)
        while seg < m - 1 and acc + lengths[seg] < target:
            acc += lengths[seg]
            seg += 1
        t = 0.0 if lengths[seg] <= 1e-12 else (target - acc) / lengths[seg]
        a, b = uv[seg], uv[(seg + 1) % m]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _centroid(uv):
    return (sum(p[0] for p in uv) / len(uv), sum(p[1] for p in uv) / len(uv))


def _blend(mesh, geom, plane):
    base_segs, top_segs = geom.get('profile', []), geom.get('top_profile', [])
    base = _to_uv(sample_loop(base_segs, plane), plane)
    top = _to_uv(sample_loop(top_segs, plane), plane)
    if len(base) < 3 or len(top) < 3:
        raise ValueError('Blend needs closed profile and top_profile loops')
    base_off = geom.get('base_offset')
    top_off = geom.get('top_offset')
    b = float(base_off) if base_off is not None else _loop_plane_offset(base_segs, plane)
    t = float(top_off) if top_off is not None else _loop_plane_offset(top_segs, plane)
    if t < b:
        b, t = t, b
    if t - b < MIN_LEN_MM:
        t = b + MIN_LEN_MM
    w0, w1 = plane[1] + b, plane[1] + t
    for loop, w, flip in ((base, w0, True), (top, w1, False)):
        pts, tris = triangulate(loop)
        mesh.add_cap([_from_uvw(u, v, w, plane) for u, v in pts], tris, flip=flip)
    if signed_area(base) < 0:
        base = base[::-1]
    if signed_area(top) < 0:
        top = top[::-1]
    n = max(16, min(96, max(len(base), len(top))))
    rb, rt = _resample(base, n), _resample(top, n)
    cb, ct = _centroid(rb), _centroid(rt)
    ref = math.atan2(rb[0][1] - cb[1], rb[0][0] - cb[0])

    def gap(p):
        a = math.atan2(p[1] - ct[1], p[0] - ct[0])
        return abs(math.atan2(math.sin(a - ref), math.cos(a - ref)))

    k = min(range(n), key=lambda i: gap(rt[i]))
    rt = rt[k:] + rt[:k]
    mesh.add_strip([_from_uvw(u, v, w0, plane) for u, v in rb],
                   [_from_uvw(u, v, w1, plane) for u, v in rt], closed=True)


def _revolution(mesh, geom, plane):
    outer = sample_loop(geom.get('profile', []), plane)
    holes = [sample_loop(loop, plane) for loop in geom.get('inner_loops') or []]
    holes = [h for h in holes if len(h) >= 3]
    if len(outer) < 3:
        raise ValueError('Revolution needs a closed profile')
    origin = _point(geom['axis_start'], plane)
    axis = _unit(_sub(_point(geom['axis_end'], plane), origin))
    a0 = float(geom.get('start_angle', 0.0))
    a1 = float(geom.get('end_angle', FULL_TURN))
    span = a1 - a0
    steps = max(4, int(math.ceil(abs(span) / (FULL_TURN / CIRCLE_SEGMENTS))))
    full = abs(abs(span) - FULL_TURN) < 1e-3

    def ring(p):
        return [_rotate(p, origin, axis, a0 + span * j / float(steps)) for j in range(steps + 1)]

    for loop in [outer] + holes:
        rings = [ring(p) for p in loop]
        for i in range(len(loop)):
            j = (i + 1) % len(loop)
            mesh.add_strip(rings[i], rings[j], closed=False)
    if not full:
        uv_outer = _to_uv(outer, plane)
        pts, tris = triangulate(uv_outer, [_to_uv(h, plane) for h in holes])
        w = plane[1]
        flat = [_from_uvw(u, v, w, plane) for u, v in pts]
        mesh.add_cap([_rotate(p, origin, axis, a0) for p in flat], tris, flip=True)
        mesh.add_cap([_rotate(p, origin, axis, a1) for p in flat], tris)


def _best_fit_normal(points):
    """Newell normal of a closed loop."""
    n = [0.0, 0.0, 0.0]
    for i in range(len(points)):
        p, q = points[i], points[(i + 1) % len(points)]
        n[0] += (p[1] - q[1]) * (p[2] + q[2])
        n[1] += (p[2] - q[2]) * (p[0] + q[0])
        n[2] += (p[0] - q[0]) * (p[1] + q[1])
    return _unit(tuple(n))


def _sweep(mesh, geom, plane):
    path = sample_loop(geom.get('path', []), plane, closed=False)
    profile = sample_loop(geom.get('profile', []), None)
    if len(path) < 2 or len(profile) < 3:
        raise ValueError('Sweep needs an open path and a closed profile')
    tangents = []
    for i in range(len(path)):
        a = path[max(0, i - 1)]
        b = path[min(len(path) - 1, i + 1)]
        tangents.append(_unit(_sub(b, a)))
    normal = _best_fit_normal(profile)
    if _dot(normal, tangents[0]) < 0:
        normal = _mul(normal, -1.0)
    centroid = _mul(tuple(sum(p[k] for p in profile) for k in range(3)), 1.0 / len(profile))
    to_start = _rotation_between(normal, tangents[0])
    offsets = [to_start(_sub(p, centroid)) for p in profile]
    sections = []
    prev_t = tangents[0]
    for station, t in zip(path, tangents):
        turn = _rotation_between(prev_t, t)
        offsets = [turn(o) for o in offsets]
        prev_t = t
        sections.append([_add(station, o) for o in offsets])
    for i in range(len(path) - 1):
        mesh.add_strip(sections[i], sections[i + 1], closed=True)
    e1, e2 = _perpendicular(tangents[0])
    first = [to_start(_sub(p, centroid)) for p in profile]
    uv = [(_dot(o, e1), _dot(o, e2)) for o in first]
    _, tris = triangulate(uv)        # no holes: its points are `uv`, in order
    mesh.add_cap(sections[0], tris, flip=True)
    mesh.add_cap(sections[-1], tris)


def _cylinder(mesh, geom):
    s = _point(geom['start'])
    e = _point(geom['end'])
    r = float(geom['radius'])
    d = _unit(_sub(e, s))
    u, v = _perpendicular(d)
    ring_s, ring_e = [], []
    for i in range(CIRCLE_SEGMENTS):
        a = FULL_TURN * i / CIRCLE_SEGMENTS
        off = _add(_mul(u, r * math.cos(a)), _mul(v, r * math.sin(a)))
        ring_s.append(_add(s, off))
        ring_e.append(_add(e, off))
    mesh.add_strip(ring_s, ring_e, closed=True)
    fan = [(0, i + 1, (i + 1) % CIRCLE_SEGMENTS + 1) for i in range(CIRCLE_SEGMENTS)]
    mesh.add_cap([s] + ring_s, fan, flip=True)
    mesh.add_cap([e] + ring_e, fan)


def build_preview(schema):
    """PreviewModel for a schema dict; never raises for bad parts, warns instead."""
    model = PreviewModel()
    geometry = schema.get('geometry') if isinstance(schema, dict) else None
    if not isinstance(geometry, list):
        model.warnings.append('No geometry array to preview.')
        return model
    for index, geom in enumerate(geometry):
        if not isinstance(geom, dict):
            continue
        kind = geom.get('type')
        label = geom.get('id') if isinstance(geom.get('id'), str) and geom.get('id') \
            else '#{} ({})'.format(index + 1, kind)
        is_solid = geom.get('is_solid', True) is not False
        material = geom.get('material') if is_solid and isinstance(geom.get('material'), str) else None
        mesh = Mesh(label, kind, material, is_solid, index)
        try:
            plane = plane_info(geom)
            if kind == 'Extrusion':
                outer = sample_loop(geom.get('profile', []), plane)
                holes = [sample_loop(loop, plane) for loop in geom.get('inner_loops') or []]
                if len(outer) < 3:
                    raise ValueError('profile does not enclose an area')
                start = float(geom.get('extrusion_start', 0.0))
                end = float(geom.get('extrusion_end', 1.0))
                if end < start:
                    start, end = end, start
                end = max(end, start + MIN_LEN_MM)
                _prism(mesh, outer, [h for h in holes if len(h) >= 3], plane,
                       plane[1] + start, plane[1] + end)
            elif kind == 'Blend':
                _blend(mesh, geom, plane)
            elif kind == 'Revolution':
                _revolution(mesh, geom, plane)
            elif kind == 'Sweep':
                _sweep(mesh, geom, plane)
            elif kind == 'Cylinder':
                _cylinder(mesh, geom)
            else:
                raise ValueError('unsupported type {!r}'.format(kind))
        except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError) as ex:
            model.warnings.append('{}: not previewed ({})'.format(label, ex))
            continue
        if model.triangle_count + len(mesh.triangles) > MAX_TRIANGLES:
            model.warnings.append('{}: not previewed (preview triangle budget reached)'.format(label))
            continue
        model.triangle_count += len(mesh.triangles)
        model.meshes.append(mesh)
    solids = [m for m in model.meshes if m.is_solid] or model.meshes
    pts = [p for m in solids for p in m.positions]
    if pts:
        model.bbox_min = tuple(min(p[i] for p in pts) for i in range(3))
        model.bbox_max = tuple(max(p[i] for p in pts) for i in range(3))
    voids = sum(1 for m in model.meshes if not m.is_solid)
    if voids:
        model.warnings.append('{} void form(s) shown translucent; Revit cuts them '
                              'from the solids they touch.'.format(voids))
    return model


# ── camera ───────────────────────────────────────────────────────────────────

def clamp_pitch(pitch):
    return max(-1.45, min(1.45, pitch))


def camera_pose(bbox_min, bbox_max, yaw=DEFAULT_YAW, pitch=DEFAULT_PITCH, zoom=1.0,
                fov_degrees=FIELD_OF_VIEW):
    """(position, look_direction, up) framing the box from (yaw, pitch).

    Z is up, as in Revit. `zoom` multiplies the fitted distance (< 1 is closer).
    """
    if bbox_min is None or bbox_max is None:
        bbox_min, bbox_max = (-500.0, -500.0, 0.0), (500.0, 500.0, 1000.0)
    center = tuple((bbox_min[i] + bbox_max[i]) / 2.0 for i in range(3))
    radius = max(1.0, 0.5 * _length(_sub(bbox_max, bbox_min)))
    distance = radius / math.sin(math.radians(fov_degrees) / 2.0) * max(0.05, zoom)
    pitch = clamp_pitch(pitch)
    direction = (math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch))
    position = _add(center, _mul(direction, distance))
    return position, _mul(direction, -distance), (0.0, 0.0, 1.0)
