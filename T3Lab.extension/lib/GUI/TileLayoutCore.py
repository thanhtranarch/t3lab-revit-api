# -*- coding: utf-8 -*-
"""
TileLayoutCore — pure-Python engine of the Tile Layout tool.

Extracted from `T3Lab.tab/Modeling & Datum.panel/Create.stack/Tile Layout.pushbutton/script.py`
so the geometry / grid / nesting / scoring logic can be unit-tested with
CPython outside Revit (see `dev/debug/tile_layout_harness.py`).

This module MUST NOT import clr, pyrevit, or any Revit API — only stdlib.

Contents (former script.py sections):
    SECTION 1 — 2-D geometry primitives (V2, polygon ops, clipping)
    SECTION 2 — TileGrid (virtual grid generator)
    SECTION 3 — Data models (TilePiece, OffCut)
    SECTION 4 — NestingEngine (clip + assign + offcut reuse)
    SECTION 5 — LayoutOption + scoring + OptionGenerator
"""

import math

# ── Unit constants ────────────────────────────────────────────────────────────
MM_TO_FT  = 1.0 / 304.8
FT_TO_MM  = 304.8
FT2_TO_M2 = 0.092903

MIN_AREA  = 1e-9              # ft² — anything smaller discarded

# Thin-cut constraint: cut pieces narrower than this are hard to install
# cleanly, so options that produce them are heavily penalised in scoring.
MIN_CUT_WIDTH_MM = 50.0
MIN_CUT_WIDTH_FT = MIN_CUT_WIDTH_MM * MM_TO_FT

# ── Tile patterns ─────────────────────────────────────────────────────────────
# (key, combo label) — order defines the Step-2 pattern dropdown.
PATTERNS = [
    ('grid',         u'Grid (Stacked)'),
    ('staggered',    u'Running Bond 1/2'),
    ('staggered3',   u'Running Bond 1/3'),
    ('staggered4',   u'Running Bond 1/4'),
    ('vstaggered',   u'Vertical Bond'),
    ('herringbone',  u'Herringbone 90°'),
    ('herringbone2', u'Double Herringbone'),
    ('basketweave',  u'Basket Weave'),
]
PATTERN_LABELS = dict(PATTERNS)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — 2-D GEOMETRY (pure Python)
# ═════════════════════════════════════════════════════════════════════════════

class V2(object):
    __slots__ = ('x', 'y')
    def __init__(self, x, y):
        self.x = float(x); self.y = float(y)
    def __repr__(self):
        return "V2({:.4f},{:.4f})".format(self.x, self.y)


def _poly_area_signed(pts):
    n, a = len(pts), 0.0
    for i in range(n):
        j = (i + 1) % n
        a += pts[i].x * pts[j].y - pts[j].x * pts[i].y
    return a * 0.5


def poly_area(pts): return abs(_poly_area_signed(pts))


def poly_centroid(pts):
    n = len(pts)
    if n == 0:
        return V2(0.0, 0.0)
    cx = cy = a = 0.0
    for i in range(n):
        j = (i + 1) % n
        f = pts[i].x * pts[j].y - pts[j].x * pts[i].y
        cx += (pts[i].x + pts[j].x) * f
        cy += (pts[i].y + pts[j].y) * f
        a  += f
    a *= 0.5
    if abs(a) < 1e-14:
        return V2(sum(p.x for p in pts)/n, sum(p.y for p in pts)/n)
    return V2(cx / (6.0 * a), cy / (6.0 * a))


def poly_bbox(pts):
    xs = [p.x for p in pts]; ys = [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def ensure_ccw(pts):
    return list(reversed(pts)) if _poly_area_signed(pts) < 0 else list(pts)


def clean_poly(pts):
    if not pts: return None
    out = [pts[0]]
    for p in pts[1:]:
        dx, dy = p.x - out[-1].x, p.y - out[-1].y
        if dx*dx + dy*dy > 1e-16: out.append(p)
    if len(out) > 1:
        dx, dy = out[-1].x - out[0].x, out[-1].y - out[0].y
        if dx*dx + dy*dy < 1e-16: out = out[:-1]
    return out if len(out) >= 3 else None


def sutherland_hodgman(subject, clip):
    def _inside(p, a, b):
        return (b.x - a.x)*(p.y - a.y) - (b.y - a.y)*(p.x - a.x) >= 0.0
    def _isect(p1, p2, p3, p4):
        x1,y1 = p1.x,p1.y; x2,y2 = p2.x,p2.y
        x3,y3 = p3.x,p3.y; x4,y4 = p4.x,p4.y
        d = (x1-x2)*(y3-y4) - (y1-y2)*(x3-x4)
        if abs(d) < 1e-15: return p2
        t = ((x1-x3)*(y3-y4) - (y1-y3)*(x3-x4)) / d
        return V2(x1 + t*(x2-x1), y1 + t*(y2-y1))

    out = list(subject); nc = len(clip)
    for i in range(nc):
        if not out: return []
        inp = out; out = []
        a, b = clip[i], clip[(i+1) % nc]
        for j in range(len(inp)):
            cur, prv = inp[j], inp[j-1]
            if _inside(cur, a, b):
                if not _inside(prv, a, b):
                    out.append(_isect(prv, cur, a, b))
                out.append(cur)
            elif _inside(prv, a, b):
                out.append(_isect(prv, cur, a, b))
    return out


def rotate_poly(pts, angle_deg, cx=0.0, cy=0.0):
    a = math.radians(angle_deg); c, s = math.cos(a), math.sin(a)
    return [V2(cx + (p.x-cx)*c - (p.y-cy)*s,
               cy + (p.x-cx)*s + (p.y-cy)*c) for p in pts]


def tile_rect(ox, oy, tw, th):
    return [V2(ox, oy), V2(ox+tw, oy), V2(ox+tw, oy+th), V2(ox, oy+th)]


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TILE GRID GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

class TileGrid(object):
    """Virtual grid generator; supports origin offset for layout variants.

    Patterns (all built axis-aligned in the grid's local frame, then rotated
    by `angle` around the floor bbox centre — one consistent rotation, no
    per-tile spin):
        grid         — stacked bond: rows and columns aligned
        staggered    — running bond 1/2: odd rows offset by half a tile
        staggered3   — running bond 1/3: each row steps by a third of a tile
        staggered4   — running bond 1/4: each row steps by a quarter of a tile
        vstaggered   — vertical bond: odd COLUMNS offset by half a tile
        herringbone  — 90° herringbone: an H tile + a V tile per lattice anchor
        herringbone2 — double herringbone: two parallel planks per arm
        basketweave  — checkerboard cells of k stacked H / k side-by-side V
                       tiles (exact when tile module length = k × width)

    dx/dy are honoured EXACTLY: rows/columns/anchors are indexed from the
    grid origin (absolute indices), never wrapped to the floor bbox. Wrapping
    dy modulo one row period silently flipped the row parity of staggered
    patterns, so a manual shift across the period boundary made the layout
    jump instead of moving continuously."""

    def __init__(self, tile_w, tile_h, joint, pattern, angle_deg,
                 origin_dx=0.0, origin_dy=0.0):
        self.tw, self.th, self.jw = tile_w, tile_h, joint
        self.pat, self.angle = pattern, angle_deg
        self.dx, self.dy = origin_dx, origin_dy

    def generate(self, floor_pts):
        # Pivot: floor bbox centre. Project floor points into the tile frame
        # (rotate by -angle) so the grid is built axis-aligned, then every
        # tile is rotated back by +angle around the same pivot.
        bx0, by0, bx1, by1 = poly_bbox(floor_pts)
        cx = (bx0 + bx1) * 0.5
        cy = (by0 + by1) * 0.5

        local_pts = rotate_poly(floor_pts, -self.angle, cx, cy)
        lx0, ly0, lx1, ly1 = poly_bbox(local_pts)
        margin = max(self.tw, self.th) * 2.0
        lx0 -= margin; ly0 -= margin
        lx1 += margin; ly1 += margin

        if self.pat in ('herringbone', 'herringbone2'):
            planks = 2 if self.pat == 'herringbone2' else 1
            rects = self._herringbone_tiles(lx0, ly0, lx1, ly1, planks)
        elif self.pat == 'basketweave':
            rects = self._basketweave_tiles(lx0, ly0, lx1, ly1)
        else:
            rects = self._row_tiles(lx0, ly0, lx1, ly1)

        tiles, tid = [], 1
        for pts in rects:
            if self.angle != 0.0:
                pts = rotate_poly(pts, self.angle, cx, cy)
            tiles.append((tid, pts))
            tid += 1
        return tiles

    def _row_offset(self, k):
        """Horizontal offset of row index k (absolute — negative k included;
        Python's % keeps the cycle continuous across zero)."""
        if self.pat == 'staggered':
            return self.tw * 0.5 if (k % 2) else 0.0
        if self.pat == 'staggered3':
            return self.tw * (k % 3) / 3.0
        if self.pat == 'staggered4':
            return self.tw * (k % 4) / 4.0
        return 0.0

    def _row_tiles(self, lx0, ly0, lx1, ly1):
        sx = self.tw + self.jw
        sy = self.th + self.jw
        out = []

        if self.pat == 'vstaggered':
            m0 = int(math.floor((lx0 - self.dx) / sx)) - 1
            m1 = int(math.ceil((lx1 - self.dx) / sx)) + 1
            for m in range(m0, m1 + 1):
                x = self.dx + m * sx
                y_off = self.th * 0.5 if (m % 2) else 0.0
                n0 = int(math.floor((ly0 - self.dy - y_off) / sy)) - 1
                n1 = int(math.ceil((ly1 - self.dy - y_off) / sy)) + 1
                for n in range(n0, n1 + 1):
                    out.append(tile_rect(x, self.dy + y_off + n * sy,
                                         self.tw, self.th))
            return out

        k0 = int(math.floor((ly0 - self.dy) / sy)) - 1
        k1 = int(math.ceil((ly1 - self.dy) / sy)) + 1
        for k in range(k0, k1 + 1):
            y = self.dy + k * sy
            x_off = self._row_offset(k)
            m0 = int(math.floor((lx0 - self.dx - x_off) / sx)) - 1
            m1 = int(math.ceil((lx1 - self.dx - x_off) / sx)) + 1
            for m in range(m0, m1 + 1):
                out.append(tile_rect(self.dx + x_off + m * sx, y,
                                     self.tw, self.th))
        return out

    def _herringbone_tiles(self, lx0, ly0, lx1, ly1, planks=1):
        """90° herringbone, generalised to n parallel planks per arm
        (n=1 classic, n=2 double herringbone). Anchor lattice:
            (x, y) = (dx, dy) + a·(n·he, n·he) + b·(we, −we)
        with we/he = tile module incl. joint. Per anchor: n H planks stacked
        at (x, y + i·he) and n V planks side-by-side at
        (x + we + i·he, y + n·he − we) — top edges aligned, which tessellates
        the plane for any tile rectangle."""
        we = self.tw + self.jw
        he = self.th + self.jw
        if we <= 1e-12 or he <= 1e-12:
            return []
        n = planks

        amin = amax = bmin = bmax = None
        for px in (lx0, lx1):
            for py in (ly0, ly1):
                rx, ry = px - self.dx, py - self.dy
                a = (rx + ry) / (2.0 * n * he)
                b = (rx - ry) / (2.0 * we)
                if amin is None:
                    amin = amax = a
                    bmin = bmax = b
                else:
                    if a < amin: amin = a
                    if a > amax: amax = a
                    if b < bmin: bmin = b
                    if b > bmax: bmax = b

        pad = 2 + int(math.ceil((self.tw + n * self.th) / min(we, he)))
        out = []
        for a in range(int(math.floor(amin)) - pad,
                       int(math.ceil(amax)) + pad + 1):
            for b in range(int(math.floor(bmin)) - pad,
                           int(math.ceil(bmax)) + pad + 1):
                x = self.dx + a * n * he + b * we
                y = self.dy + a * n * he - b * we
                for i in range(n):
                    out.append(tile_rect(x, y + i * he,
                                         self.tw, self.th))          # H planks
                for i in range(n):
                    out.append(tile_rect(x + we + i * he, y + n * he - we,
                                         self.th, self.tw))          # V planks

        return self._cull_outside(out, lx0, ly0, lx1, ly1)

    def _basketweave_tiles(self, lx0, ly0, lx1, ly1):
        """Basket weave: square checkerboard cells of side we (tile module).
        Even cells hold k horizontal tiles stacked, odd cells k vertical
        tiles side-by-side, k = floor(we / he). Exact tessellation when
        we == k·he (e.g. 600×300 → k=2, 450×150 → k=3); any remainder shows
        as an extra joint inside the cell — never an overlap."""
        we = self.tw + self.jw
        he = self.th + self.jw
        if we <= 1e-12 or he <= 1e-12:
            return []
        k = int(math.floor((we + 1e-9) / he))
        if k < 1:
            k = 1

        out = []
        i0 = int(math.floor((lx0 - self.dx) / we)) - 1
        i1 = int(math.ceil((lx1 - self.dx) / we)) + 1
        j0 = int(math.floor((ly0 - self.dy) / we)) - 1
        j1 = int(math.ceil((ly1 - self.dy) / we)) + 1
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                x = self.dx + i * we
                y = self.dy + j * we
                if (i + j) % 2 == 0:
                    for t in range(k):
                        out.append(tile_rect(x, y + t * he,
                                             self.tw, self.th))      # H stack
                else:
                    for t in range(k):
                        out.append(tile_rect(x + t * he, y,
                                             self.th, self.tw))      # V row
        return self._cull_outside(out, lx0, ly0, lx1, ly1)

    @staticmethod
    def _cull_outside(rects, lx0, ly0, lx1, ly1):
        """Drop rects entirely outside the working bbox — keeps the engine's
        input small; precise clipping happens in the engine anyway."""
        kept = []
        for r in rects:
            if (r[2].x < lx0 or r[0].x > lx1 or
                    r[2].y < ly0 or r[0].y > ly1):
                continue
            kept.append(r)
        return kept


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — DATA MODELS
# ═════════════════════════════════════════════════════════════════════════════

class TilePiece(object):
    """A cut/full/reuse/waste piece — may consist of multiple convex fragments
    when the source tile straddles a concave floor boundary."""

    def __init__(self, parent_id, sub_id, fragments, piece_type,
                 area_override=None):
        self.parent_id  = parent_id
        self.sub_id     = sub_id
        self.piece_type = piece_type

        # Normalize: accept either a single polygon (list of V2) or a list of
        # polygons. Internally always store a list of polygons.
        if fragments and isinstance(fragments[0], V2):
            self.fragments = [list(fragments)]
        else:
            self.fragments = [list(f) for f in fragments if f and len(f) >= 3]

        # `area_override` decouples the accounting area from the drawn
        # geometry — used by waste pieces, whose geometry is the whole tile
        # (for reference) but whose real area is only the offcut remainder.
        if area_override is not None:
            self.area = area_override
        else:
            self.area = sum(poly_area(f) for f in self.fragments)

        tot_a = 0.0; cx = cy = 0.0
        for f in self.fragments:
            a = poly_area(f); c = poly_centroid(f)
            cx += c.x * a; cy += c.y * a; tot_a += a
        self.centroid = V2(cx / tot_a, cy / tot_a) if tot_a > 1e-14 else V2(0, 0)

    @property
    def pts(self):
        """Largest fragment — preserved for callers that still expect a single
        polygon (e.g., simple geometry checks)."""
        if not self.fragments:
            return []
        return max(self.fragments, key=poly_area)

    @property
    def label(self):
        if self.sub_id and self.sub_id != 'waste':
            return "{}{}".format(self.parent_id, self.sub_id)
        return str(self.parent_id)


class OffCut(object):
    """Spare material left in a purchased tile after its own piece is cut.

    State flags:
        retired — the tile is NOT purchased any more (its piece was relocated
                  into another tile's offcut), so this spare doesn't exist.
        nested  — another piece has been cut from this spare (one reuse per
                  offcut, conservative).
        remaining — spare area still unclaimed (waste_area minus any nested
                  piece), which is what finally counts as waste.
    """
    def __init__(self, parent_id, tile_pts, inside_area, tile_area,
                 fragments=None):
        self.parent_id  = parent_id
        self.tile_pts   = tile_pts
        self.fragments  = fragments or []   # installed portion (inside floor)
        self.waste_area = tile_area - inside_area
        self.remaining  = self.waste_area
        self.retired    = False
        self.nested     = False


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — NESTING ENGINE
# ═════════════════════════════════════════════════════════════════════════════

class NestingEngine(object):

    def __init__(self, tile_w, tile_h, use_nesting, floor_pts):
        self.tile_w      = tile_w
        self.tile_h      = tile_h
        self.use_nesting = use_nesting
        self.floor_pts   = ensure_ccw(floor_pts)

        self.pieces      = []
        self.offcuts     = []
        self.nesting_log = []
        self._sub_cnt    = {}

    def process(self, tiles):
        raw = self._intersect_all(tiles)
        self._assign_pieces(raw)
        if self.use_nesting:
            self._nest()
        self._collect_waste()
        return self.pieces

    @staticmethod
    def _is_axis_rect(pts, x0, y0, x1, y1, tol=1e-6):
        """True if the 4-point polygon is an axis-aligned rectangle spanning
        exactly (x0,y0)-(x1,y1) — every vertex sits on a bbox corner."""
        if len(pts) != 4:
            return False
        for p in pts:
            if abs(p.x - x0) > tol and abs(p.x - x1) > tol:
                return False
            if abs(p.y - y0) > tol and abs(p.y - y1) > tol:
                return False
        return True

    def _intersect_all(self, tiles):
        """Produce ONE polygon per tile representing the portion that lies
        inside the floor. Sutherland-Hodgman is correct whenever the CLIP
        polygon is convex — a tile is always convex, so we use the tile as
        the clipper and the (possibly concave) floor as the subject. Result
        is a single polygon per tile, no triangle-fragment puzzles.

        Fast path: when the floor is an axis-aligned rectangle AND the tile
        is too (angle 0/90/180/270), the intersection is a plain interval
        overlap — no polygon clipping. This is the overwhelmingly common
        case (rectangular room, unrotated grid) and skips ~all of the
        geometry cost."""
        fpts = self.floor_pts
        fx0 = min(v.x for v in fpts); fx1 = max(v.x for v in fpts)
        fy0 = min(v.y for v in fpts); fy1 = max(v.y for v in fpts)
        floor_rect = (self._is_axis_rect(fpts, fx0, fy0, fx1, fy1) and
                      abs(poly_area(fpts) - (fx1 - fx0) * (fy1 - fy0))
                      <= 1e-6 * max(1.0, (fx1 - fx0) * (fy1 - fy0)))

        out = []
        for tid, tp in tiles:
            # Early reject: tile bbox disjoint from floor bbox
            tx0 = min(v.x for v in tp); tx1 = max(v.x for v in tp)
            ty0 = min(v.y for v in tp); ty1 = max(v.y for v in tp)
            if tx1 < fx0 or tx0 > fx1 or ty1 < fy0 or ty0 > fy1:
                continue

            if floor_rect and self._is_axis_rect(tp, tx0, ty0, tx1, ty1):
                ix0 = tx0 if tx0 > fx0 else fx0
                ix1 = tx1 if tx1 < fx1 else fx1
                iy0 = ty0 if ty0 > fy0 else fy0
                iy1 = ty1 if ty1 < fy1 else fy1
                if ix1 - ix0 <= 0 or iy1 - iy0 <= 0:
                    continue
                clipped = tile_rect(ix0, iy0, ix1 - ix0, iy1 - iy0)
            else:
                clipped = clean_poly(sutherland_hodgman(fpts, tp))
                if clipped is None: continue

            ia = poly_area(clipped)
            if ia < MIN_AREA: continue
            ta = poly_area(tp)
            out.append({'tid': tid, 'fragments': [clipped],
                        'inside_area': ia, 'tile_pts': tp,
                        'tile_area': ta,
                        'ratio': ia / ta if ta > 1e-14 else 0.0})
        return out

    def _assign_pieces(self, raw):
        for r in raw:
            if r['ratio'] > 0.9999:
                # Full tile — store the clean tile rect, not the clipped fragments.
                self.pieces.append(TilePiece(r['tid'], '', r['tile_pts'], 'full'))
            else:
                sub = self._next_sub(r['tid'])
                self.pieces.append(TilePiece(r['tid'], sub, r['fragments'], 'cut'))
                c = self.pieces[-1].centroid
                self.nesting_log.append(
                    "Tile {:>4}: {:>4}{} at ({:.2f},{:.2f}) area={:.4f} ft²".format(
                        r['tid'], r['tid'], sub, c.x, c.y, r['inside_area']))
                self.offcuts.append(OffCut(r['tid'], r['tile_pts'],
                                           r['inside_area'], r['tile_area'],
                                           r['fragments']))

    @staticmethod
    def _tile_frame(tile_pts):
        """Local frame of a (possibly rotated) tile rect: origin at corner 0,
        u along edge 0→1, v perpendicular. Returns
        (p0, ux, uy, vx, vy, tile_w, tile_h) or None if degenerate."""
        if not tile_pts or len(tile_pts) < 4:
            return None
        p0, p1, p3 = tile_pts[0], tile_pts[1], tile_pts[3]
        ex, ey = p1.x - p0.x, p1.y - p0.y
        tw = math.hypot(ex, ey)
        if tw < 1e-12:
            return None
        ux, uy = ex / tw, ey / tw
        th = math.hypot(p3.x - p0.x, p3.y - p0.y)
        return p0, ux, uy, -uy, ux, tw, th

    @classmethod
    def _piece_fits_offcut(cls, piece, oc):
        """Conservative feasibility check: the candidate piece (bbox measured
        in the grid's own frame) must fit into the free strip left in the
        offcut tile beside/above its installed portion. Area alone is NOT
        enough — a piece larger than the spare, or longer than the spare
        strip, cannot physically be cut from it."""
        frame = cls._tile_frame(oc.tile_pts)
        if frame is None:
            return False
        p0, ux, uy, vx, vy, tw, th = frame

        def _bbox(frags):
            smin = tmin = float('inf'); smax = tmax = float('-inf')
            for f in frags:
                for p in f:
                    dx, dy = p.x - p0.x, p.y - p0.y
                    s = dx * ux + dy * uy
                    t = dx * vx + dy * vy
                    if s < smin: smin = s
                    if s > smax: smax = s
                    if t < tmin: tmin = t
                    if t > tmax: tmax = t
            return smin, smax, tmin, tmax

        if not oc.fragments or not piece.fragments:
            return False
        is0, is1, it0, it1 = _bbox(oc.fragments)     # installed portion
        ps0, ps1, pt0, pt1 = _bbox(piece.fragments)  # piece to relocate
        pw, ph = ps1 - ps0, pt1 - pt0
        free_u = tw - (is1 - is0)   # strip beside the installed part
        free_v = th - (it1 - it0)   # strip above/below the installed part
        eps = 1e-9
        return ((pw <= free_u + eps and ph <= th + eps) or
                (pw <= tw + eps and ph <= free_v + eps))

    def _nest(self):
        pool = sorted(self.offcuts, key=lambda o: o.remaining, reverse=True)
        # Map each cut piece (by identity) to its own offcut — created in the
        # same order in _assign_pieces.
        cut_pieces = [p for p in self.pieces if p.piece_type == 'cut']
        piece_to_offcut = {}
        for piece, oc in zip(cut_pieces, self.offcuts):
            piece_to_offcut[id(piece)] = oc

        for piece in cut_pieces:
            src = piece_to_offcut.get(id(piece))
            # If another piece already nests in THIS tile's spare, the tile
            # must be purchased anyway — relocating its own piece would save
            # nothing and would orphan the nested piece.
            if src is not None and src.nested:
                continue
            needed = piece.area
            for oc in pool:
                if oc.retired or oc.nested: continue
                if oc.parent_id == piece.parent_id: continue
                if oc.remaining < needed: continue
                if not self._piece_fits_offcut(piece, oc): continue
                original_label = piece.label
                oc.nested = True
                oc.remaining -= needed
                new_sub = self._next_sub(oc.parent_id)
                piece.parent_id  = oc.parent_id
                piece.sub_id     = new_sub
                piece.piece_type = 'reuse'
                # Retire the source tile's offcut — we no longer buy it.
                if src is not None:
                    src.retired = True
                self.nesting_log.append(
                    "  → {} reused as {}{}".format(
                        original_label, oc.parent_id, new_sub))
                break

    def _collect_waste(self):
        """Emit one waste piece per PURCHASED cut tile, carrying the true
        leftover area (tile − installed − nested), not the whole tile."""
        for oc in self.offcuts:
            if oc.retired:
                continue                 # tile not purchased — no material
            if oc.remaining <= MIN_AREA:
                continue
            wp = clean_poly(oc.tile_pts)
            if wp:
                self.pieces.append(
                    TilePiece(oc.parent_id, 'waste', wp, 'waste',
                              area_override=oc.remaining))

    def _next_sub(self, parent_id):
        idx = self._sub_cnt.get(parent_id, 0)
        self._sub_cnt[parent_id] = idx + 1
        return 'ABCDEFGHIJ'[idx] if idx < 10 else str(idx)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — LAYOUT OPTION + SCORING + OPTION GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

class LayoutOption(object):
    """One candidate tiling arrangement for a single floor."""

    def __init__(self, option_id, pieces, variant_desc, tile_area_ft2,
                 gen_params=None):
        self.option_id   = option_id          # 'A' 'B' 'C' 'D'
        self.pieces      = pieces
        self.variant     = variant_desc       # human-readable variation tag
        self.tile_area   = tile_area_ft2
        # Parameters needed to regenerate this option with a tweaked angle.
        # Keys: pattern, angle, dx, dy, tile_w, tile_h, joint, use_nesting,
        #       floor_pts.
        self.gen_params  = gen_params or {}

        self._renumber_pieces()
        self._recompute_stats()

    def _renumber_pieces(self):
        """Relabel parent_ids starting at 1 in reading order (top-to-bottom,
        left-to-right). For rotated grids the ordering is computed in the
        grid's own (unrotated) frame so rows fall out naturally."""
        if not self.pieces: return

        gp = self.gen_params
        angle = gp.get('angle', 0.0) if gp else 0.0
        tile_h = gp.get('tile_h', 0.0) if gp else 0.0
        pivot_pts = gp.get('floor_pts') if gp else None
        if pivot_pts:
            bx0, by0, bx1, by1 = poly_bbox(pivot_pts)
            cx, cy = (bx0 + bx1) * 0.5, (by0 + by1) * 0.5
        else:
            cx = cy = 0.0

        # Group pieces by original parent_id; compute an area-weighted
        # centroid per group.
        groups = {}
        for p in self.pieces:
            groups.setdefault(p.parent_id, []).append(p)

        a_rad = math.radians(-angle)
        ca, sa = math.cos(a_rad), math.sin(a_rad)
        anchors = {}
        for pid, plist in groups.items():
            tot_a = 0.0; ax = ay = 0.0
            for p in plist:
                a = p.area
                if a <= 0: continue
                ax += p.centroid.x * a
                ay += p.centroid.y * a
                tot_a += a
            if tot_a > 0:
                wx, wy = ax / tot_a, ay / tot_a
            else:
                wx, wy = 0.0, 0.0
            # Rotate into the grid's local (axis-aligned) frame.
            dx, dy = wx - cx, wy - cy
            lx = cx + dx * ca - dy * sa
            ly = cy + dx * sa + dy * ca
            anchors[pid] = (lx, ly)

        row_step = tile_h if tile_h > 1e-6 else 1.0
        def _sort_key(pid):
            lx, ly = anchors[pid]
            # Bucket Y by row so slightly varying centroids still group;
            # higher Y first (reading top-to-bottom on screen → -Y row index).
            row = int(round(-ly / row_step))
            return (row, lx)

        ordered = sorted(groups.keys(), key=_sort_key)
        mapping = {}
        for new_idx, old_pid in enumerate(ordered, start=1):
            mapping[old_pid] = new_idx

        for p in self.pieces:
            p.parent_id = mapping.get(p.parent_id, p.parent_id)

    def _recompute_stats(self):
        pieces = self.pieces
        self.n_full  = sum(1 for p in pieces if p.piece_type == 'full')
        self.n_cut   = sum(1 for p in pieces if p.piece_type == 'cut')
        self.n_reuse = sum(1 for p in pieces if p.piece_type == 'reuse')

        parents_full = set(p.parent_id for p in pieces if p.piece_type == 'full')
        parents_cut  = set(p.parent_id for p in pieces if p.piece_type == 'cut')
        self.tiles_to_buy = len(parents_full) + len(parents_cut)

        waste_area = sum(p.area for p in pieces if p.piece_type == 'waste')
        purch_area = self.tiles_to_buy * self.tile_area
        self.waste_pct = (waste_area / purch_area * 100.0) if purch_area > 0 else 0.0

        # Thin-cut tally: cut/reuse pieces whose narrowest dimension (in the
        # grid's own frame) falls below MIN_CUT_WIDTH_FT are considered
        # "slivers" — hard to cut, hard to install.
        self.n_thin_cuts = self._count_thin_cuts()

        self.score = self._compute_score()

    def _count_thin_cuts(self):
        gp = self.gen_params
        if not gp: return 0
        angle = gp.get('angle', 0.0)
        floor_pts = gp.get('floor_pts')
        if floor_pts:
            bx0, by0, bx1, by1 = poly_bbox(floor_pts)
            px, py = (bx0 + bx1) * 0.5, (by0 + by1) * 0.5
        else:
            px = py = 0.0
        a = math.radians(-angle)
        ca, sa = math.cos(a), math.sin(a)

        count = 0
        for p in self.pieces:
            if p.piece_type not in ('cut', 'reuse'): continue
            mn_x = mn_y = float('inf')
            mx_x = mx_y = float('-inf')
            for frag in p.fragments:
                for v in frag:
                    dx, dy = v.x - px, v.y - py
                    lx = px + dx * ca - dy * sa
                    ly = py + dx * sa + dy * ca
                    if lx < mn_x: mn_x = lx
                    if lx > mx_x: mx_x = lx
                    if ly < mn_y: mn_y = ly
                    if ly > mx_y: mx_y = ly
            if mn_x == float('inf'): continue
            narrow = min(mx_x - mn_x, mx_y - mn_y)
            if narrow < MIN_CUT_WIDTH_FT:
                count += 1
        return count

    def _compute_score(self):
        """Lower is better. Penalise waste %, sliver cuts, and reward
        reused pieces. Sliver penalty is heavy so any option WITHOUT thin
        cuts is strongly preferred over an option WITH them."""
        thin_penalty = self.n_thin_cuts * 50.0
        return (self.waste_pct * 10.0
                - self.n_reuse * 0.5
                + self.n_cut * 0.1
                + thin_penalty)

    def regenerate(self, angle=None, dx=None, dy=None):
        """Rebuild pieces using new grid parameters. Any None arg is kept
        at its current value. Returns True on success."""
        gp = self.gen_params
        if not gp: return False
        if angle is None: angle = gp.get('angle', 0.0)
        if dx    is None: dx    = gp.get('dx', 0.0)
        if dy    is None: dy    = gp.get('dy', 0.0)

        grid = TileGrid(gp['tile_w'], gp['tile_h'], gp['joint'],
                        gp['pattern'], angle, dx, dy)
        tiles = grid.generate(gp['floor_pts'])
        engine = NestingEngine(gp['tile_w'], gp['tile_h'],
                               gp['use_nesting'], gp['floor_pts'])
        self.pieces = engine.process(tiles)
        self._nesting_log = engine.nesting_log
        gp['angle'] = angle
        gp['dx']    = dx
        gp['dy']    = dy
        self.variant = "shift {:+.0f}/{:+.0f} mm, angle {:+.1f}°".format(
            dx * FT_TO_MM, dy * FT_TO_MM, angle)
        self._renumber_pieces()
        self._recompute_stats()
        return True


    def shift_screen(self, sdx, sdy):
        """Shift the grid by a SCREEN-space (world-axis) vector. dx/dy live
        in the grid's rotated local frame, so pressing → must convert the
        world vector by −angle first — otherwise arrow keys move the pattern
        diagonally on rotated layouts."""
        gp = self.gen_params
        if not gp: return False
        a = math.radians(gp.get('angle', 0.0) or 0.0)
        ca, sa = math.cos(a), math.sin(a)
        ddx =  sdx * ca + sdy * sa
        ddy = -sdx * sa + sdy * ca
        return self.regenerate(dx=(gp.get('dx', 0.0) or 0.0) + ddx,
                               dy=(gp.get('dy', 0.0) or 0.0) + ddy)

    def stats_signature(self):
        """Coarse identity of the RESULT (not the parameters): two variants
        with the same signature are indistinguishable to the user."""
        return (self.n_full, self.n_cut, self.n_reuse, self.tiles_to_buy,
                round(self.waste_pct, 1), self.n_thin_cuts)

    def matches_params(self, angle, dx, dy):
        """True if (angle, dx, dy) describe THIS option's grid: same angle
        (mod 360) and same shift PHASE. The identity period depends on the
        pattern — running bond repeats every 2 rows, 1/3 bond every 3 rows,
        vertical bond every 2 columns; herringbone has no simple x/y period
        so it compares exactly. Conservative by design: a false negative
        just rebuilds the option, a false positive would swap the user's
        layout for a different one."""
        gp = self.gen_params
        if not gp: return False
        da = abs((gp.get('angle', 0.0) or 0.0) - angle) % 360.0
        if min(da, 360.0 - da) > 1e-6:
            return False

        gdx = gp.get('dx', 0.0) or 0.0
        gdy = gp.get('dy', 0.0) or 0.0
        pat = gp.get('pattern', 'grid')
        if pat in ('herringbone', 'herringbone2'):
            return abs(gdx - dx) < 1e-9 and abs(gdy - dy) < 1e-9

        sx = (gp.get('tile_w', 0.0) or 0.0) + (gp.get('joint', 0.0) or 0.0)
        sy = (gp.get('tile_h', 0.0) or 0.0) + (gp.get('joint', 0.0) or 0.0)
        if pat == 'staggered':
            px, py = sx, 2.0 * sy
        elif pat == 'staggered3':
            px, py = sx, 3.0 * sy
        elif pat == 'staggered4':
            px, py = sx, 4.0 * sy
        elif pat == 'vstaggered':
            px, py = 2.0 * sx, sy
        elif pat == 'basketweave':
            px = py = 2.0 * sx   # checkerboard of square we-cells
        else:
            px, py = sx, sy

        def _phase_eq(a, b, period):
            if period <= 1e-12:
                return abs(a - b) < 1e-9
            d = abs(a - b) % period
            return min(d, period - d) < 1e-9
        return _phase_eq(gdx, dx, px) and _phase_eq(gdy, dy, py)


class OptionGenerator(object):
    """Produce N candidate layouts per floor by varying origin + angle.

    The variant sweep combines three sources:
      · phase fractions of the tile size (0/¼/½/¾ — halved on huge floors),
      · two industry-standard anchors per angle: grid CORNER-aligned to the
        floor bbox corner, and a tile CENTRED on the floor centre (these are
        what a tiler actually sets out first, and the fraction sweep misses
        them whenever the floor origin isn't a multiple of the tile),
      · every angle delta on top of the user's base angle."""

    # Shift fractions (of tile size) × angle deltas (°) = variants to try.
    _SHIFT_FRACS      = [0.0, 0.25, 0.5, 0.75]
    _SHIFT_FRACS_FAST = [0.0, 0.5]     # large floors: quarter the sweep
    _ANGLE_DELTAS     = [0.0, 45.0]
    _FAST_TILE_COUNT  = 600            # est. tiles/variant beyond which the
                                       # reduced sweep kicks in

    def __init__(self, tile_w, tile_h, joint, use_nesting, top_n=4):
        self.tile_w = tile_w
        self.tile_h = tile_h
        self.joint  = joint
        self.use_nesting = use_nesting
        self.top_n  = top_n

    def build_variant(self, floor_info, pattern, angle, dx, dy):
        """Build ONE LayoutOption for an explicit (angle, dx, dy) using the
        generator's current tile parameters. Used by the sweep, and to
        rebuild a user-tweaked choice after tile parameters change."""
        grid = TileGrid(self.tile_w, self.tile_h, self.joint,
                        pattern, angle, dx, dy)
        tiles = grid.generate(floor_info.pts)
        engine = NestingEngine(self.tile_w, self.tile_h,
                               self.use_nesting, floor_info.pts)
        pieces = engine.process(tiles)

        desc = "shift {:+.0f}/{:+.0f} mm, angle {:+.1f}°".format(
            dx * FT_TO_MM, dy * FT_TO_MM, angle)
        gen_params = {
            'pattern'     : pattern,
            'angle'       : angle,
            'dx'          : dx,
            'dy'          : dy,
            'tile_w'      : self.tile_w,
            'tile_h'      : self.tile_h,
            'joint'       : self.joint,
            'use_nesting' : self.use_nesting,
            'floor_pts'   : floor_info.pts,
        }
        # Option id assigned by the caller (after sorting/dedup)
        opt = LayoutOption('?', pieces, desc,
                           self.tile_w * self.tile_h, gen_params)
        opt._nesting_log = engine.nesting_log
        return opt

    def _aligned_anchors(self, floor_pts, angle):
        """Two setting-out anchors in the grid's LOCAL frame: grid corner on
        the floor bbox corner, and a tile centred on the floor centre."""
        bx0, by0, bx1, by1 = poly_bbox(floor_pts)
        cx, cy = (bx0 + bx1) * 0.5, (by0 + by1) * 0.5
        local = rotate_poly(floor_pts, -angle, cx, cy)
        lx0, ly0, lx1, ly1 = poly_bbox(local)
        corner   = (lx0, ly0)
        centered = ((lx0 + lx1) * 0.5 - self.tile_w * 0.5,
                    (ly0 + ly1) * 0.5 - self.tile_h * 0.5)
        return [corner, centered]

    def generate(self, floor_info, pattern, base_angle, progress=None):
        """Return list of top-N LayoutOption, sorted by score ASC.
        `progress(i, total)` — optional callback fired before each variant
        so the UI can stay alive during long sweeps."""
        sx = self.tile_w + self.joint
        sy = self.tile_h + self.joint
        est_tiles = (poly_area(floor_info.pts) / (sx * sy)
                     if sx > 1e-14 and sy > 1e-14 else 0.0)
        fracs = (self._SHIFT_FRACS if est_tiles <= self._FAST_TILE_COUNT
                 else self._SHIFT_FRACS_FAST)

        variants = []
        for da in self._ANGLE_DELTAS:
            angle = base_angle + da
            for fx in fracs:
                for fy in fracs:
                    variants.append((angle, fx * self.tile_w,
                                     fy * self.tile_h))
            for adx, ady in self._aligned_anchors(floor_info.pts, angle):
                variants.append((angle, adx, ady))

        candidates = []
        total = len(variants)
        for i, (angle, dx, dy) in enumerate(variants):
            if progress is not None:
                try:
                    progress(i + 1, total)
                except Exception:
                    pass
            candidates.append(self.build_variant(
                floor_info, pattern, angle, dx, dy))

        # Keep the N best DISTINCT results. Many shift variants collapse to
        # the same layout on regular floors (mirror-symmetric phases, exact
        # multiples); without dedup the user gets N identical cards.
        candidates.sort(key=lambda o: o.score)
        best, seen = [], set()
        for cand in candidates:
            s = cand.stats_signature()
            if s in seen:
                continue
            seen.add(s)
            best.append(cand)
            if len(best) >= self.top_n:
                break
        for i, opt in enumerate(best):
            opt.option_id = "ABCDEF"[i] if i < 6 else str(i+1)
        return best
