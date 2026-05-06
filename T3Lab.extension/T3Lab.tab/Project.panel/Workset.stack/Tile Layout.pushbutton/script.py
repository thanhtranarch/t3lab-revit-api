# -*- coding: utf-8 -*-
"""
Tile Layout

3-step wizard:
    (1) Extract floor boundaries,
    (2) Choose tile pattern per floor,
    (3) Generate multiple candidate layouts per floor and let the user pick.

All geometry is computed in memory (no physical Revit elements for the tiles
themselves — only temporary DirectShape + TextNote for the chosen layout).

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Tile Layout"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==============================================================================
import os
import sys
import math
import csv
import traceback

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, ElementId,
    XYZ, Line, CurveLoop,
    GeometryCreationUtilities,
    DirectShape,
    View3D, ViewFamilyType, ViewFamily,
    OverrideGraphicSettings, Color,
    TextNote, TextNoteOptions,
    TextNoteType,
    Options, PlanarFace,
    FillPatternElement,
    HorizontalTextAlignment,
)
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from System.Windows import WindowState

from pyrevit import revit, forms, script

SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))
lib_dir    = os.path.join(EXT_DIR, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

XAML_FILE  = os.path.join(EXT_DIR, 'lib', 'GUI', 'Tools', 'TileLayout.xaml')

# DEFINE VARIABLES
# ==============================================================================
logger        = script.get_logger()
output        = script.get_output()
doc           = revit.doc
uidoc         = revit.uidoc
REVIT_VERSION = int(revit.doc.Application.VersionNumber)

# ── Constants ─────────────────────────────────────────────────────────────────
MM_TO_FT  = 1.0 / 304.8
FT_TO_MM  = 304.8
FT2_TO_M2 = 0.092903

EXTRUDE_H = 10.0 * MM_TO_FT   # DirectShape thickness
MIN_AREA  = 1e-9              # ft² — anything smaller discarded
MIN_EDGE  = 0.003             # ft — ~0.9 mm; below Revit short-curve tolerance

# Thin-cut constraint: cut pieces narrower than this are hard to install
# cleanly, so options that produce them are heavily penalised in scoring.
MIN_CUT_WIDTH_MM = 50.0
MIN_CUT_WIDTH_FT = MIN_CUT_WIDTH_MM * MM_TO_FT

# Colours for DirectShape overrides
COL_FULL  = Color(189, 195, 199)
COL_CUT   = Color( 39, 174,  96)
COL_REUSE = Color( 52, 152, 219)
COL_WASTE = Color(231,  76,  60)


# CLASS/FUNCTIONS
# ==============================================================================

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


def _point_in_triangle(p, a, b, c):
    """Barycentric point-in-triangle test (inclusive of edges)."""
    v0x, v0y = c.x - a.x, c.y - a.y
    v1x, v1y = b.x - a.x, b.y - a.y
    v2x, v2y = p.x - a.x, p.y - a.y
    dot00 = v0x * v0x + v0y * v0y
    dot01 = v0x * v1x + v0y * v1y
    dot02 = v0x * v2x + v0y * v2y
    dot11 = v1x * v1x + v1y * v1y
    dot12 = v1x * v2x + v1y * v2y
    denom = dot00 * dot11 - dot01 * dot01
    if abs(denom) < 1e-18:
        return False
    inv = 1.0 / denom
    u = (dot11 * dot02 - dot01 * dot12) * inv
    v = (dot00 * dot12 - dot01 * dot02) * inv
    eps = -1e-12
    return u >= eps and v >= eps and (u + v) <= 1.0 - eps


def ear_clip_triangulate(pts):
    """Ear-clipping triangulation of a simple polygon (may be concave).
    Returns a list of triangles; each triangle is a list of 3 V2s (CCW)."""
    src = ensure_ccw(list(pts))
    n = len(src)
    if n < 3:
        return []
    if n == 3:
        return [src]

    # Work on an index list so we can pop ears in O(n)
    idx = list(range(n))
    triangles = []

    def _cross(a, b, c):
        return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)

    def _is_ear_at(pos, live):
        m = len(live)
        a = src[live[(pos - 1) % m]]
        b = src[live[pos]]
        c = src[live[(pos + 1) % m]]
        if _cross(a, b, c) <= 0:   # reflex vertex
            return False
        # No other vertex may lie inside triangle (a,b,c)
        for k, vi in enumerate(live):
            if k in ((pos - 1) % m, pos, (pos + 1) % m):
                continue
            if _point_in_triangle(src[vi], a, b, c):
                return False
        return True

    guard = 3 * n
    while len(idx) > 3 and guard > 0:
        guard -= 1
        m = len(idx)
        clipped_one = False
        for j in range(m):
            if _is_ear_at(j, idx):
                a = src[idx[(j - 1) % m]]
                b = src[idx[j]]
                c = src[idx[(j + 1) % m]]
                triangles.append([a, b, c])
                idx.pop(j)
                clipped_one = True
                break
        if not clipped_one:
            # Degenerate polygon — fan-triangulate the remainder to avoid loop.
            break

    if len(idx) == 3:
        triangles.append([src[idx[0]], src[idx[1]], src[idx[2]]])
    elif len(idx) > 3:
        # Fallback fan from the first remaining vertex
        anchor = src[idx[0]]
        for k in range(1, len(idx) - 1):
            triangles.append([anchor, src[idx[k]], src[idx[k + 1]]])
    return triangles


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TILE GRID GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

class TileGrid(object):
    """Virtual grid generator; supports origin offset for layout variants."""

    def __init__(self, tile_w, tile_h, joint, pattern, angle_deg,
                 origin_dx=0.0, origin_dy=0.0):
        self.tw, self.th, self.jw = tile_w, tile_h, joint
        self.pat, self.angle = pattern, angle_deg
        self.dx, self.dy = origin_dx, origin_dy

    def generate(self, floor_pts):
        # Pivot: floor bbox centre. The whole grid is built in a tile-aligned
        # frame that is rotated by `self.angle` around this pivot, so every
        # tile shares one consistent rotation (no gaps, no per-tile spin).
        bx0, by0, bx1, by1 = poly_bbox(floor_pts)
        cx = (bx0 + bx1) * 0.5
        cy = (by0 + by1) * 0.5

        # Project floor points into the tile frame (rotate by -angle around
        # pivot) so the grid can be built axis-aligned, then every tile is
        # rotated back by +angle around the same pivot.
        local_pts = rotate_poly(floor_pts, -self.angle, cx, cy)
        lx0, ly0, lx1, ly1 = poly_bbox(local_pts)
        margin = max(self.tw, self.th) * 2.0
        lx0 -= margin; ly0 -= margin
        lx1 += margin; ly1 += margin

        sx = self.tw + self.jw
        sy = self.th + self.jw

        # A shift moves the grid's PHASE, not its overall position. A shift
        # of one full period is visually identical to no shift, so the only
        # thing that matters is dx/dy modulo the period. This guarantees the
        # tile field covers the floor regardless of how far the user has
        # shifted.
        phase_x = (self.dx - math.floor(self.dx / sx) * sx) if sx > 1e-14 else 0.0
        phase_y = (self.dy - math.floor(self.dy / sy) * sy) if sy > 1e-14 else 0.0

        tiles, tid, row = [], 1, 0
        # Start one extra period before the bbox so the first column/row is
        # safely outside the floor regardless of phase.
        y = ly0 + phase_y - sy
        while y <= ly1 + sy:
            x_off = (self.tw * 0.5) if (self.pat == 'staggered' and row % 2 == 1) else 0.0
            x = lx0 + phase_x + x_off - sx
            while x <= lx1 + sx:
                pts = tile_rect(x, y, self.tw, self.th)
                if self.angle != 0.0:
                    pts = rotate_poly(pts, self.angle, cx, cy)
                tiles.append((tid, pts))
                tid += 1
                x += sx
            y += sy
            row += 1
        return tiles


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — DATA MODELS
# ═════════════════════════════════════════════════════════════════════════════

class TilePiece(object):
    """A cut/full/reuse/waste piece — may consist of multiple convex fragments
    when the source tile straddles a concave floor boundary."""

    def __init__(self, parent_id, sub_id, fragments, piece_type):
        self.parent_id  = parent_id
        self.sub_id     = sub_id
        self.piece_type = piece_type

        # Normalize: accept either a single polygon (list of V2) or a list of
        # polygons. Internally always store a list of polygons.
        if fragments and isinstance(fragments[0], V2):
            self.fragments = [list(fragments)]
        else:
            self.fragments = [list(f) for f in fragments if f and len(f) >= 3]

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
    def __init__(self, parent_id, tile_pts, inside_area, tile_area):
        self.parent_id  = parent_id
        self.tile_pts   = tile_pts
        self.waste_area = tile_area - inside_area
        self.used       = False


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

    def _intersect_all(self, tiles):
        """Produce ONE polygon per tile representing the portion that lies
        inside the floor. Sutherland-Hodgman is correct whenever the CLIP
        polygon is convex — a tile is always convex, so we use the tile as
        the clipper and the (possibly concave) floor as the subject. Result
        is a single polygon per tile, no triangle-fragment puzzles."""
        fpts = self.floor_pts
        out = []
        for tid, tp in tiles:
            # Early reject: tile bbox disjoint from floor bbox
            tx0 = min(v.x for v in tp); tx1 = max(v.x for v in tp)
            ty0 = min(v.y for v in tp); ty1 = max(v.y for v in tp)
            fx0 = min(v.x for v in fpts); fx1 = max(v.x for v in fpts)
            fy0 = min(v.y for v in fpts); fy1 = max(v.y for v in fpts)
            if tx1 < fx0 or tx0 > fx1 or ty1 < fy0 or ty0 > fy1:
                continue

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
                                           r['inside_area'], r['tile_area']))

    def _nest(self):
        pool = sorted(self.offcuts, key=lambda o: o.waste_area, reverse=True)
        # Map each piece (by identity) to its original offcut so we can drop it
        # from waste emission when the piece is relocated to another tile.
        piece_to_offcut = {}
        for piece, oc in zip(
                [p for p in self.pieces if p.piece_type == 'cut'], self.offcuts):
            piece_to_offcut[id(piece)] = oc

        for piece in list(self.pieces):
            if piece.piece_type != 'cut': continue
            needed = piece.area
            for oc in pool:
                if oc.used or oc.parent_id == piece.parent_id: continue
                if oc.waste_area >= needed * 0.90:
                    original_label = piece.label
                    oc.used = True
                    new_sub = self._next_sub(oc.parent_id)
                    piece.parent_id  = oc.parent_id
                    piece.sub_id     = new_sub
                    piece.piece_type = 'reuse'
                    # Retire the source tile's offcut — we no longer buy it.
                    retired = piece_to_offcut.get(id(piece))
                    if retired is not None:
                        retired.used = True
                    self.nesting_log.append(
                        "  → {} reused as {}{}".format(
                            original_label, oc.parent_id, new_sub))
                    break

    def _collect_waste(self):
        for oc in self.offcuts:
            if not oc.used:
                wp = clean_poly(oc.tile_pts)
                if wp:
                    self.pieces.append(
                        TilePiece(oc.parent_id, 'waste', wp, 'waste'))

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

    def regenerate_with_angle(self, new_angle):
        return self.regenerate(angle=new_angle)


class OptionGenerator(object):
    """Produce N candidate layouts per floor by varying origin + angle."""

    # Shift fractions (of tile size) × angle deltas (°) = variants to try.
    _SHIFT_FRACS = [0.0, 0.25, 0.5, 0.75]
    _ANGLE_DELTAS = [0.0, 45.0]

    def __init__(self, tile_w, tile_h, joint, use_nesting, top_n=4):
        self.tile_w = tile_w
        self.tile_h = tile_h
        self.joint  = joint
        self.use_nesting = use_nesting
        self.top_n  = top_n

    def generate(self, floor_info, pattern, base_angle):
        """Return list of top-N LayoutOption, sorted by score ASC."""
        candidates = []
        tile_area = self.tile_w * self.tile_h

        variants = []
        for fx in self._SHIFT_FRACS:
            for fy in self._SHIFT_FRACS:
                for da in self._ANGLE_DELTAS:
                    variants.append((fx, fy, da))

        for fx, fy, da in variants:
            dx = fx * self.tile_w
            dy = fy * self.tile_h
            angle = base_angle + da
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
            # Option id assigned after sorting
            candidates.append(
                LayoutOption('?', pieces, desc, tile_area, gen_params))
            # cache for later
            candidates[-1]._nesting_log = engine.nesting_log

        # Keep the N best
        candidates.sort(key=lambda o: o.score)
        best = candidates[:self.top_n]
        for i, opt in enumerate(best):
            opt.option_id = "ABCDEF"[i] if i < 6 else str(i+1)
        return best


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — FLOOR INFO (wizard-level data model)
# ═════════════════════════════════════════════════════════════════════════════

class FloorInfo(object):
    """Data carried between wizard steps for one floor."""

    def __init__(self, floor_elem, pts, z):
        self.floor = floor_elem
        self.pts   = pts            # [V2] — CCW floor boundary
        self.z     = z              # top-face elevation (ft)

        bx0, by0, bx1, by1 = poly_bbox(pts)
        self.width_ft  = bx1 - bx0
        self.height_ft = by1 - by0
        self.area_ft2  = poly_area(pts)

        # wizard state
        self.options           = []       # [LayoutOption]
        self.chosen_option_id  = None     # 'A' | 'B' | ...


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — REVIT FLOOR BOUNDARY EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def _curveloop_to_v2(curveloop):
    """Tessellate a CurveLoop into a V2 polygon so arcs/splines are captured
    faithfully (not just their chord endpoints)."""
    pts = []
    for c in curveloop:
        try:
            tess = list(c.Tessellate())
        except Exception:
            tess = [c.GetEndPoint(0), c.GetEndPoint(1)]
        for i, xyz in enumerate(tess):
            # Avoid duplicating the junction between consecutive curves.
            if pts and i == 0:
                continue
            pts.append(V2(xyz.X, xyz.Y))
    # Drop closing duplicate if present.
    if len(pts) >= 2:
        dx = pts[-1].x - pts[0].x
        dy = pts[-1].y - pts[0].y
        if dx * dx + dy * dy < 1e-12:
            pts.pop()
    return pts


def extract_floor_boundary(floor):
    """Return (outer_polygon_V2, z) for the floor's largest top-facing area.

    - Picks the top face with the largest area (handles floors split into
      multiple top faces: we want the main one, not whichever happens to be
      at the highest Z).
    - Tessellates curved edges so arcs/splines are sampled, not truncated.
    """
    opts = Options(); opts.ComputeReferences = False
    geom = floor.get_Geometry(opts)
    best_area, best_loop, best_z = 0.0, None, None

    for g in (geom or []):
        if not hasattr(g, 'Faces'): continue
        for face in g.Faces:
            if not isinstance(face, PlanarFace): continue
            if abs(face.FaceNormal.Z - 1.0) > 0.05: continue
            loops = face.GetEdgesAsCurveLoops()
            if not loops: continue
            pts = _curveloop_to_v2(loops[0])
            if len(pts) < 3: continue
            a = poly_area(pts)
            if a > best_area:
                best_area = a
                best_loop = pts
                best_z = face.Origin.Z

    if best_loop is None:
        bb = floor.get_BoundingBox(None)
        if bb:
            mn, mx = bb.Min, bb.Max
            best_z = mx.Z
            best_loop = [V2(mn.X, mn.Y), V2(mx.X, mn.Y),
                         V2(mx.X, mx.Y), V2(mn.X, mx.Y)]
    return best_loop, best_z


def get_floor_level_name(floor):
    try:
        lvl = doc.GetElement(floor.LevelId)
        return lvl.Name if lvl else "—"
    except Exception:
        return "—"


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8 — REVIT VISUALISER (DirectShape + TextNote)
# ═════════════════════════════════════════════════════════════════════════════

class RevitVisualizer(object):

    _solid_fill_id = None

    def __init__(self, view):
        self.view = view
        self._resolve_solid_fill()

    def draw_piece(self, piece, z_base):
        if not piece.fragments: return
        col = self._piece_colour(piece)
        drew_any = False
        for frag in piece.fragments:
            if not frag or len(frag) < 3: continue
            loop = self._make_curve_loop(frag, z_base)
            if loop is None: continue
            try:
                solid = GeometryCreationUtilities.CreateExtrusionGeometry(
                    [loop], XYZ(0,0,1), EXTRUDE_H)
            except Exception as ex:
                logger.debug("Extrude failed for {}: {}".format(piece.label, ex))
                continue

            ds = DirectShape.CreateElement(
                doc, ElementId(BuiltInCategory.OST_GenericModel))
            ds.SetShape([solid])

            ogs = OverrideGraphicSettings()
            ogs.SetSurfaceForegroundPatternColor(col)
            ogs.SetSurfaceForegroundPatternVisible(True)
            if RevitVisualizer._solid_fill_id:
                ogs.SetSurfaceForegroundPatternId(RevitVisualizer._solid_fill_id)
            ogs.SetProjectionLineColor(Color(120, 120, 120))
            self.view.SetElementOverrides(ds.Id, ogs)
            drew_any = True

        if drew_any:
            c = piece.centroid
            self._make_textnote(piece.label,
                                XYZ(c.x, c.y, z_base + EXTRUDE_H + 0.005))

    @staticmethod
    def _make_curve_loop(pts, z):
        # Pre-filter points so short edges don't break CurveLoop continuity.
        xyz_pts = [XYZ(p.x, p.y, z) for p in pts]
        kept = [xyz_pts[0]]
        for q in xyz_pts[1:]:
            if kept[-1].DistanceTo(q) >= MIN_EDGE:
                kept.append(q)
        if len(kept) >= 2 and kept[-1].DistanceTo(kept[0]) < MIN_EDGE:
            kept.pop()
        if len(kept) < 3:
            return None

        loop = CurveLoop()
        n = len(kept)
        try:
            for i in range(n):
                loop.Append(Line.CreateBound(kept[i], kept[(i + 1) % n]))
            _ = loop.GetExactLength()
            return loop
        except Exception as ex:
            logger.debug("CurveLoop build failed: {}".format(ex))
            return None

    def _make_textnote(self, text, position):
        try:
            tn_types = (FilteredElementCollector(doc)
                        .OfClass(TextNoteType).ToElements())
            if not tn_types: return
            opts = TextNoteOptions(tn_types[0].Id)
            opts.HorizontalAlignment = HorizontalTextAlignment.Center
            TextNote.Create(doc, self.view.Id, position, text, opts)
        except Exception as ex:
            logger.debug("TextNote failed: {}".format(ex))

    @staticmethod
    def _piece_colour(piece):
        t = piece.piece_type
        if t == 'full':  return COL_FULL
        if t == 'cut':   return COL_CUT
        if t == 'reuse': return COL_REUSE
        return COL_WASTE

    @classmethod
    def _resolve_solid_fill(cls):
        if cls._solid_fill_id is not None: return
        for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
            if fp.GetFillPattern().IsSolidFill:
                cls._solid_fill_id = fp.Id
                return


def get_or_create_3d_view(view_name="Tile Layout Preview"):
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if not v.IsTemplate and v.Name == view_name:
            return v
    for vt in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if vt.ViewFamily == ViewFamily.ThreeDimensional:
            v3d = View3D.CreateIsometric(doc, vt.Id)
            v3d.Name = view_name
            return v3d
    raise RuntimeError("No 3D ViewFamilyType found in project.")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 9 — WPF PREVIEW RENDERER (mini canvas drawing of a LayoutOption)
# ═════════════════════════════════════════════════════════════════════════════

# Import WPF drawing primitives lazily to keep pyRevit startup light.
def _wpf():
    from System.Windows import Point as WPoint
    from System.Windows.Media import (
        SolidColorBrush, Color as WColor, PointCollection,
        Pen as WPen,
    )
    from System.Windows.Shapes import Polygon as WPolygon, Polyline as WPolyline
    from System.Windows.Controls import Canvas
    return dict(
        Point=WPoint, Brush=SolidColorBrush, Color=WColor,
        Points=PointCollection, Pen=WPen,
        Polygon=WPolygon, Polyline=WPolyline, Canvas=Canvas)


_PIECE_FILLS = {
    'full':  (189, 195, 199),
    'cut':   ( 39, 174,  96),
    'reuse': ( 52, 152, 219),
    'waste': (231,  76,  60),
}

def render_option_preview_static(option, floor_pts, canvas_w=320,
                                 canvas_h=210):
    """Plain non-interactive Canvas (no scroll, no zoom). Suitable for
    embedding inside a FlowDocument / XPS / PDF export."""
    w = _wpf()
    canvas = w['Canvas']()
    canvas.Width  = canvas_w
    canvas.Height = canvas_h
    canvas.Background = w['Brush'](w['Color'].FromRgb(250, 250, 250))

    bx0, by0, bx1, by1 = poly_bbox(floor_pts)
    pad = 8
    fw, fh = bx1 - bx0, by1 - by0
    if fw <= 0 or fh <= 0:
        return canvas
    sx = (canvas_w - 2*pad) / fw
    sy = (canvas_h - 2*pad) / fh
    s  = min(sx, sy)
    ox = pad + (canvas_w - 2*pad - fw*s) * 0.5
    oy = pad + (canvas_h - 2*pad - fh*s) * 0.5

    def _to_canvas(p):
        return w['Point']((p.x - bx0) * s + ox,
                          canvas_h - ((p.y - by0) * s + oy))

    for piece in option.pieces:
        if piece.piece_type == 'waste': continue
        r, g, b = _PIECE_FILLS[piece.piece_type]
        for frag in piece.fragments:
            if not frag or len(frag) < 3: continue
            polygon = w['Polygon']()
            pc = w['Points']()
            for v in frag:
                pc.Add(_to_canvas(v))
            polygon.Points = pc
            polygon.Fill = w['Brush'](w['Color'].FromArgb(235, r, g, b))
            polygon.Stroke = w['Brush'](w['Color'].FromRgb(140, 140, 140))
            polygon.StrokeThickness = 0.4
            canvas.Children.Add(polygon)

    outline = w['Polyline']()
    pc = w['Points']()
    for v in list(floor_pts) + [floor_pts[0]]:
        pc.Add(_to_canvas(v))
    outline.Points = pc
    outline.Stroke = w['Brush'](w['Color'].FromRgb(44, 62, 80))
    outline.StrokeThickness = 1.6
    canvas.Children.Add(outline)

    return canvas


def render_option_preview(option, floor_pts, canvas_w=190, canvas_h=130):
    """Return a ScrollViewer wrapping the layout canvas with wheel-zoom and
    right-drag pan. Double-right-click resets the view."""
    w = _wpf()
    from System.Windows.Controls import ScrollViewer, ScrollBarVisibility
    from System.Windows.Media import ScaleTransform
    import System.Windows.Input as SWI

    canvas = w['Canvas']()
    canvas.Width  = canvas_w
    canvas.Height = canvas_h
    canvas.Background = w['Brush'](w['Color'].FromRgb(250, 250, 250))

    # Compute transform: fit floor bbox into canvas with padding
    bx0, by0, bx1, by1 = poly_bbox(floor_pts)
    pad = 6
    fw, fh = bx1 - bx0, by1 - by0
    if fw > 0 and fh > 0:
        sx = (canvas_w - 2*pad) / fw
        sy = (canvas_h - 2*pad) / fh
        s  = min(sx, sy)
        ox = pad + (canvas_w - 2*pad - fw*s) * 0.5
        oy = pad + (canvas_h - 2*pad - fh*s) * 0.5

        def _to_canvas(p):
            return w['Point']((p.x - bx0) * s + ox,
                              canvas_h - ((p.y - by0) * s + oy))

        # Draw pieces (skip waste to keep preview tidy). A cut piece may be
        # split into multiple fragments when it straddles a concave boundary;
        # render one polygon per fragment so the full piece shows.
        for piece in option.pieces:
            if piece.piece_type == 'waste': continue
            r, g, b = _PIECE_FILLS[piece.piece_type]
            for frag in piece.fragments:
                if not frag or len(frag) < 3: continue
                polygon = w['Polygon']()
                pc = w['Points']()
                for v in frag:
                    pc.Add(_to_canvas(v))
                polygon.Points = pc
                polygon.Fill = w['Brush'](w['Color'].FromArgb(230, r, g, b))
                polygon.Stroke = w['Brush'](w['Color'].FromRgb(140, 140, 140))
                polygon.StrokeThickness = 0.4
                canvas.Children.Add(polygon)

        # Floor outline
        outline = w['Polyline']()
        pc = w['Points']()
        for v in list(floor_pts) + [floor_pts[0]]:
            pc.Add(_to_canvas(v))
        outline.Points = pc
        outline.Stroke = w['Brush'](w['Color'].FromRgb(44, 62, 80))
        outline.StrokeThickness = 1.5
        canvas.Children.Add(outline)

    # Zoom transform
    scale = ScaleTransform(1.0, 1.0)
    canvas.LayoutTransform = scale

    sv = ScrollViewer()
    sv.Width  = canvas_w
    sv.Height = canvas_h
    sv.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto
    sv.VerticalScrollBarVisibility   = ScrollBarVisibility.Auto
    sv.Content = canvas
    sv.Background = w['Brush'](w['Color'].FromRgb(250, 250, 250))
    sv.ToolTip = ("Ctrl + Wheel = zoom   ·   Right-drag = pan   ·   "
                  "Right-double-click = reset")

    # ── Zoom: Ctrl + wheel (plain wheel still scrolls the Step 3 page) ──
    def _on_wheel(s_, e):
        ctrl_held = (SWI.Keyboard.IsKeyDown(SWI.Key.LeftCtrl) or
                     SWI.Keyboard.IsKeyDown(SWI.Key.RightCtrl))
        if not ctrl_held:
            return  # let it bubble to outer page scroller
        factor = 1.15 if e.Delta > 0 else (1.0 / 1.15)
        new_s = max(0.5, min(10.0, scale.ScaleX * factor))
        scale.ScaleX = new_s
        scale.ScaleY = new_s
        e.Handled = True
    sv.PreviewMouseWheel += _on_wheel

    # ── Pan: right-drag ──
    drag = {'on': False, 'sx': 0.0, 'sy': 0.0, 'hoff': 0.0, 'voff': 0.0}

    def _on_rdown(s_, e):
        # Right-double-click → reset zoom & pan
        if e.ClickCount >= 2:
            scale.ScaleX = 1.0
            scale.ScaleY = 1.0
            sv.ScrollToHorizontalOffset(0)
            sv.ScrollToVerticalOffset(0)
            e.Handled = True
            return
        drag['on']   = True
        p            = e.GetPosition(sv)
        drag['sx']   = p.X; drag['sy'] = p.Y
        drag['hoff'] = sv.HorizontalOffset
        drag['voff'] = sv.VerticalOffset
        sv.CaptureMouse()
        sv.Cursor = SWI.Cursors.SizeAll
        e.Handled = True

    def _on_move(s_, e):
        if not drag['on']: return
        p = e.GetPosition(sv)
        sv.ScrollToHorizontalOffset(drag['hoff'] - (p.X - drag['sx']))
        sv.ScrollToVerticalOffset  (drag['voff'] - (p.Y - drag['sy']))

    def _on_rup(s_, e):
        if drag['on']:
            drag['on'] = False
            sv.ReleaseMouseCapture()
            sv.Cursor = SWI.Cursors.Arrow
            e.Handled = True

    sv.PreviewMouseRightButtonDown += _on_rdown
    sv.PreviewMouseMove            += _on_move
    sv.PreviewMouseRightButtonUp   += _on_rup

    return sv


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 10 — REPORTING
# ═════════════════════════════════════════════════════════════════════════════

class ReportGenerator(object):

    def __init__(self, chosen_per_floor, params, all_floors=None):
        """chosen_per_floor: list of (FloorInfo, LayoutOption, pattern).
        all_floors: full list of FloorInfo (for PDF report showing every
        option, not just the chosen one)."""
        self.chosen = chosen_per_floor
        self.params = params
        self.all_floors = all_floors or [fi for fi, _o, _p in chosen_per_floor]

    def summary_text(self):
        tw_mm = self.params['tile_w_mm']
        th_mm = self.params['tile_h_mm']
        jw_mm = self.params['joint_mm']

        lines = [
            "=" * 68,
            "  TILE LAYOUT REPORT",
            "=" * 68,
            "  Tile size     : {:.0f} x {:.0f} mm".format(tw_mm, th_mm),
            "  Joint width   : {:.1f} mm".format(jw_mm),
            "  Nesting       : {}".format(
                "ON" if self.params['optimize_nesting'] else "OFF"),
            "=" * 68,
        ]

        grand_buy = 0
        grand_waste_area = 0.0
        grand_waste_denom = 0.0

        for fi, opt, pat in self.chosen:
            lines.append("")
            lines.append("  FLOOR  {}  ({})".format(fi.floor.Id, pat))
            lines.append("  Option {} — {}".format(opt.option_id, opt.variant))
            lines.append("  " + "-" * 62)
            lines.append("    Full tiles     : {:5d}".format(opt.n_full))
            lines.append("    Cut tiles (A)  : {:5d}".format(opt.n_cut))
            lines.append("    Reused (B/C..) : {:5d}".format(opt.n_reuse))
            lines.append("    TILES TO BUY   : {:5d}".format(opt.tiles_to_buy))
            lines.append("    Waste          : {:5.1f} %".format(opt.waste_pct))
            lines.append("    Cuts < {:.0f} mm    : {:5d}".format(
                MIN_CUT_WIDTH_MM, opt.n_thin_cuts))

            grand_buy += opt.tiles_to_buy
            grand_waste_area += sum(
                p.area for p in opt.pieces if p.piece_type == 'waste')
            grand_waste_denom += opt.tiles_to_buy * opt.tile_area

            log = getattr(opt, '_nesting_log', [])
            if log:
                lines.append("    Nesting log:")
                lines.extend("      " + e for e in log)

        grand_pct = (grand_waste_area / grand_waste_denom * 100.0
                     if grand_waste_denom > 0 else 0.0)
        lines.extend([
            "",
            "=" * 68,
            "  GRAND TOTAL",
            "    Tiles to buy   : {}".format(grand_buy),
            "    Overall waste  : {:.1f} %".format(grand_pct),
            "=" * 68,
        ])
        return "\n".join(lines)

    def export_csv(self, filepath):
        with open(filepath, 'wb') as fh:
            w = csv.writer(fh)
            w.writerow(['Floor_Id', 'Pattern', 'Option',
                        'Label', 'Type', 'Parent_ID',
                        'Area_ft2', 'Centroid_X', 'Centroid_Y'])
            for fi, opt, pat in self.chosen:
                for p in opt.pieces:
                    w.writerow([
                        str(fi.floor.Id), pat, opt.option_id,
                        p.label, p.piece_type, p.parent_id,
                        "{:.6f}".format(p.area),
                        "{:.4f}".format(p.centroid.x),
                        "{:.4f}".format(p.centroid.y),
                    ])

    # ── PDF REPORT ─────────────────────────────────────────────────────────

    def build_report_document(self, page_w=816.0, page_h=1056.0):
        """Build a WPF FlowDocument showing EVERY option of EVERY floor."""
        from System.Windows.Documents import (
            FlowDocument, Paragraph, Run, BlockUIContainer, LineBreak)
        from System.Windows import Thickness, FontWeights, TextAlignment
        from System.Windows.Media import (
            SolidColorBrush, Color as WColor, FontFamily)
        import System.Windows.Controls as WC
        import System.Windows.Controls.Primitives as WCP

        chosen_ids = {id(fi): (opt.option_id, pat)
                      for fi, opt, pat in self.chosen}

        fdoc = FlowDocument()
        fdoc.PageWidth  = page_w
        fdoc.PageHeight = page_h
        fdoc.ColumnWidth = page_w
        fdoc.PagePadding = Thickness(40)
        fdoc.FontFamily  = FontFamily("Segoe UI")
        fdoc.FontSize    = 11

        dark = SolidColorBrush(WColor.FromRgb(44, 62, 80))
        sub  = SolidColorBrush(WColor.FromRgb(127, 140, 141))

        # ── Title ──
        title = Paragraph(Run("Tile Layout Report"))
        title.FontSize = 22; title.FontWeight = FontWeights.Bold
        title.Foreground = dark
        title.Margin = Thickness(0, 0, 0, 4)
        fdoc.Blocks.Add(title)

        tw_mm = self.params.get('tile_w_mm', 0)
        th_mm = self.params.get('tile_h_mm', 0)
        jw_mm = self.params.get('joint_mm', 0)
        nest  = "ON" if self.params.get('optimize_nesting') else "OFF"
        meta  = Paragraph(Run(
            u"Tile {:.0f} × {:.0f} mm  \u00b7  Joint {:.1f} mm  \u00b7  "
            u"Nesting {}  \u00b7  {} floor(s)".format(
                tw_mm, th_mm, jw_mm, nest, len(self.all_floors))))
        meta.FontSize = 11; meta.Foreground = sub
        meta.Margin = Thickness(0, 0, 0, 16)
        fdoc.Blocks.Add(meta)

        # ── Per-floor sections ──
        for fi_idx, fi in enumerate(self.all_floors):
            chosen_id, chosen_pat = chosen_ids.get(
                id(fi), (None, getattr(fi, '_pattern', 'grid')))

            # Floor header
            hdr = Paragraph()
            hdr.Inlines.Add(Run(u"Floor #{}  ".format(fi_idx + 1)))
            hdr.Inlines.Add(Run(u"(id {})  \u2014  {:.1f} m\u00b2  \u00b7  "
                                u"{:.0f} \u00d7 {:.0f} mm  \u00b7  {}".format(
                fi.floor.Id.IntegerValue,
                fi.area_ft2 * FT2_TO_M2,
                fi.width_ft * FT_TO_MM, fi.height_ft * FT_TO_MM,
                chosen_pat)))
            hdr.FontSize = 15; hdr.FontWeight = FontWeights.SemiBold
            hdr.Foreground = dark
            hdr.Margin = Thickness(0, 12, 0, 8)
            fdoc.Blocks.Add(hdr)

            if not fi.options:
                p = Paragraph(Run("No options generated for this floor."))
                p.Foreground = sub; fdoc.Blocks.Add(p)
                continue

            # 2×2 grid of option cards (or 1×N if fewer)
            n_opts = len(fi.options)
            cols = 2 if n_opts >= 2 else 1
            rows = int(math.ceil(n_opts / float(cols)))
            grid = WCP.UniformGrid()
            grid.Columns = cols
            grid.Rows = rows
            grid.Width = page_w - 80  # leave for page padding

            for opt in fi.options:
                is_chosen = (opt.option_id == chosen_id)
                grid.Children.Add(
                    self._build_report_card(opt, fi, is_chosen))

            container = BlockUIContainer(grid)
            container.Margin = Thickness(0, 0, 0, 10)
            fdoc.Blocks.Add(container)

        # ── Grand totals ──
        grand_buy = sum(o.tiles_to_buy for _fi, o, _p in self.chosen)
        grand_waste_area = sum(
            p.area for _fi, o, _p in self.chosen
            for p in o.pieces if p.piece_type == 'waste')
        grand_denom = sum(o.tiles_to_buy * o.tile_area
                          for _fi, o, _p in self.chosen)
        grand_pct = (grand_waste_area / grand_denom * 100.0
                     if grand_denom > 0 else 0.0)

        tot = Paragraph()
        tot.Inlines.Add(Run(u"GRAND TOTAL (chosen options)"))
        tot.Inlines.Add(LineBreak())
        tot.Inlines.Add(Run(
            u"Tiles to buy: {}   \u00b7   Overall waste: {:.1f}%".format(
                grand_buy, grand_pct)))
        tot.FontSize = 13; tot.FontWeight = FontWeights.SemiBold
        tot.Foreground = dark
        tot.Margin = Thickness(0, 16, 0, 0)
        fdoc.Blocks.Add(tot)

        return fdoc

    @staticmethod
    def _build_report_card(opt, fi, is_chosen):
        import System.Windows.Controls as WC
        import System.Windows as SW
        from System.Windows.Media import SolidColorBrush, Color as WColor

        brush_border = SolidColorBrush(WColor.FromRgb(189, 195, 199))
        brush_chosen = SolidColorBrush(WColor.FromRgb(52, 152, 219))
        brush_text   = SolidColorBrush(WColor.FromRgb(44, 62, 80))
        brush_sub    = SolidColorBrush(WColor.FromRgb(127, 140, 141))

        outer = WC.Border()
        outer.BorderBrush = brush_chosen if is_chosen else brush_border
        outer.BorderThickness = SW.Thickness(2)
        outer.CornerRadius = SW.CornerRadius(6)
        outer.Margin = SW.Thickness(6)
        outer.Padding = SW.Thickness(10)
        outer.Background = SolidColorBrush(WColor.FromRgb(255, 255, 255))

        stack = WC.StackPanel()

        # Header: "Option A — chosen"
        name = WC.TextBlock()
        name.Text = u"Option {}".format(opt.option_id)
        if is_chosen:
            name.Text += u"   \u2605 chosen"
        name.FontSize = 13; name.FontWeight = SW.FontWeights.SemiBold
        name.Foreground = brush_chosen if is_chosen else brush_text
        stack.Children.Add(name)

        desc = WC.TextBlock()
        desc.Text = opt.variant; desc.FontSize = 10; desc.Foreground = brush_sub
        desc.Margin = SW.Thickness(0, 0, 0, 6)
        desc.TextWrapping = SW.TextWrapping.Wrap
        stack.Children.Add(desc)

        # Preview (plain Canvas, fits card width)
        canvas = render_option_preview_static(
            opt, fi.pts, canvas_w=320, canvas_h=210)
        preview_host = WC.Border()
        preview_host.Background = SolidColorBrush(WColor.FromRgb(250, 250, 250))
        preview_host.BorderBrush = SolidColorBrush(WColor.FromRgb(236, 240, 241))
        preview_host.BorderThickness = SW.Thickness(1)
        preview_host.Child = canvas
        preview_host.HorizontalAlignment = SW.HorizontalAlignment.Center
        preview_host.Margin = SW.Thickness(0, 0, 0, 6)
        stack.Children.Add(preview_host)

        # Stats rows
        def _row(k, v, color=None):
            row = WC.StackPanel(); row.Orientation = WC.Orientation.Horizontal
            row.Margin = SW.Thickness(0, 1, 0, 1)
            kt = WC.TextBlock(); kt.Text = k; kt.FontSize = 11
            kt.Foreground = brush_sub; kt.Width = 110
            vt = WC.TextBlock(); vt.Text = v; vt.FontSize = 11
            vt.FontWeight = SW.FontWeights.SemiBold
            vt.Foreground = color or brush_text
            row.Children.Add(kt); row.Children.Add(vt)
            return row

        ok_col   = SolidColorBrush(WColor.FromRgb( 39, 174,  96))
        warn_col = SolidColorBrush(WColor.FromRgb(231,  76,  60))
        waste_col = ok_col if opt.waste_pct < 10 else warn_col

        stack.Children.Add(_row("Full tiles:",   "{}".format(opt.n_full)))
        stack.Children.Add(_row("Cut (A):",      "{}".format(opt.n_cut)))
        stack.Children.Add(_row("Reused (B+):",  "{}".format(opt.n_reuse),
                                ok_col if opt.n_reuse else brush_text))
        stack.Children.Add(_row("Tiles to buy:", "{}".format(opt.tiles_to_buy)))
        stack.Children.Add(_row("Waste:",        "{:.1f} %".format(opt.waste_pct),
                                waste_col))
        thin_col = warn_col if opt.n_thin_cuts else ok_col
        stack.Children.Add(_row(
            u"Cuts < {:.0f}mm:".format(MIN_CUT_WIDTH_MM),
            "{}".format(opt.n_thin_cuts), thin_col))

        outer.Child = stack
        return outer

    def export_pdf(self, job_name="Tile Layout Report"):
        """Open the system Print dialog with the FlowDocument; the user
        picks 'Microsoft Print to PDF' (or any other printer) and Windows
        prompts for the output file path.

        Returns True if the print job was dispatched, False if cancelled.
        """
        from System.Windows.Controls import PrintDialog
        pd = PrintDialog()
        result = pd.ShowDialog()
        if not (result is True or result == True):  # nullable bool
            return False
        fdoc = self.build_report_document(
            page_w=pd.PrintableAreaWidth  or 816.0,
            page_h=pd.PrintableAreaHeight or 1056.0)
        from System.Windows import Size as WSize
        paginator = fdoc.DocumentPaginator
        paginator.PageSize = WSize(pd.PrintableAreaWidth,
                                   pd.PrintableAreaHeight)
        pd.PrintDocument(paginator, job_name)
        return True


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 11 — SELECTION FILTER
# ═════════════════════════════════════════════════════════════════════════════

class _FloorFilter(ISelectionFilter):
    def AllowElement(self, e):
        return (e.Category is not None and
                e.Category.Id.IntegerValue == int(BuiltInCategory.OST_Floors))
    def AllowReference(self, r, p): return False


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 12 — LIST-VIEW ROW VIEWMODELS (for ListView + ItemsControl binding)
# ═════════════════════════════════════════════════════════════════════════════

class FloorRowVM(object):
    """Row data for the Step 1 ListView (read-only display)."""

    def __init__(self, floor_info, display_index):
        self._fi = floor_info
        self.Name        = "Floor #{}  (id {})".format(
            display_index, floor_info.floor.Id.IntegerValue)
        self.LevelName   = get_floor_level_name(floor_info.floor)
        w_mm = floor_info.width_ft  * FT_TO_MM
        h_mm = floor_info.height_ft * FT_TO_MM
        self.Dimensions  = "{:.0f} × {:.0f}".format(w_mm, h_mm)
        self.AreaM2      = "{:.1f}".format(floor_info.area_ft2 * FT2_TO_M2)
        self.VertexCount = str(len(floor_info.pts))


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 13 — WIZARD WINDOW
# ═════════════════════════════════════════════════════════════════════════════

STEP_BOUNDARIES = 0
STEP_PATTERN    = 1
STEP_CONCEPTS   = 2


class TileLayoutWindow(forms.WPFWindow):

    def __init__(self, preselected_floors=None):
        forms.WPFWindow.__init__(self, XAML_FILE)

        # ── wizard state ──
        self._floors = []        # [FloorInfo]
        self._rows   = []        # [FloorRowVM] (parallel to _floors)
        self._params = {}
        self._step   = STEP_BOUNDARIES
        self._selection_buttons = {}   # (floor_idx, option_id) → Border
        self._pattern_ctrls     = []   # [(ComboBox, TextBox)] per floor
        self._applied = False
        self.wants_repick = False   # set to True to re-pick after Close()


        if preselected_floors:
            self._extract_boundaries(preselected_floors)

        self._refresh_step_ui()

    # ── logo ──────────────────────────────────────────────────────────────────
    # ── chrome ────────────────────────────────────────────────────────────────
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

    # ── step indicator / action-bar refresh ──────────────────────────────────
    def _refresh_step_ui(self):
        """Update step circles, Back/Next button state and labels."""
        from System.Windows.Media import SolidColorBrush, Color as WColor
        active = SolidColorBrush(WColor.FromRgb(52, 152, 219))   # #3498DB
        inactive = SolidColorBrush(WColor.FromRgb(189, 195, 199)) # #BDC3C7
        active_text = SolidColorBrush(WColor.FromRgb(44, 62, 80))
        inactive_text = SolidColorBrush(WColor.FromRgb(127, 140, 141))

        circles = [self.step1_circle, self.step2_circle, self.step3_circle]
        labels  = [self.step1_label,  self.step2_label,  self.step3_label]
        for i, (c, lb) in enumerate(zip(circles, labels)):
            if i <= self._step:
                c.Background = active
                lb.Foreground = active_text
                lb.FontWeight = __import__('System').Windows.FontWeights.SemiBold
            else:
                c.Background = inactive
                lb.Foreground = inactive_text
                lb.FontWeight = __import__('System').Windows.FontWeights.Normal

        self.wizard_tabs.SelectedIndex = self._step
        self.btn_back.IsEnabled = self._step > STEP_BOUNDARIES

        if self._step == STEP_BOUNDARIES:
            self.btn_next.Content = "Next →"
            self.btn_next.IsEnabled = bool(self._floors)
        elif self._step == STEP_PATTERN:
            self.btn_next.Content = "Generate Concepts →"
            self.btn_next.IsEnabled = bool(self._floors)
        else:
            self.btn_next.Content = "Apply to Model ✓"
            self.btn_next.IsEnabled = self._every_floor_has_choice()

    def _every_floor_has_choice(self):
        return all(fi.chosen_option_id is not None for fi in self._floors)

    # ═════════════════════════════════════════════════════════════════════════
    # STEP 1 — floor selection
    # ═════════════════════════════════════════════════════════════════════════

    def select_floors_clicked(self, sender, args):
        # Calling PickObjects from inside a modal WPF dialog causes fatal
        # Revit crashes (native reentrancy during the WPF message pump).
        # Instead, close the window, let the outer driver run the pick on the
        # clean UI thread, then re-open a fresh window with the results.
        self.wants_repick = True
        self.Close()

    def _extract_boundaries(self, floor_elems):
        self._floors = []
        self._rows   = []
        total_area = 0.0
        for f in floor_elems:
            pts, z = extract_floor_boundary(f)
            if not pts:
                logger.warning("No boundary for floor {}".format(f.Id))
                continue
            fi = FloorInfo(f, ensure_ccw(pts), z)
            self._floors.append(fi)
            self._rows.append(FloorRowVM(fi, len(self._rows) + 1))
            total_area += fi.area_ft2

        from System.Collections.ObjectModel import ObservableCollection
        coll = ObservableCollection[object]()
        for r in self._rows: coll.Add(r)
        self.floors_listview.ItemsSource = coll

        self._build_pattern_ui()

        self.floors_count_text.Text = "{} floor(s) — boundaries extracted".format(
            len(self._floors))
        self.floors_total_text.Text = "Total area: {:.1f} m²".format(
            total_area * FT2_TO_M2)
        self.status_text.Text = "Step 1 done — click Next to configure patterns."

    def _build_pattern_ui(self):
        """Populate Step 2 — one row per floor with pattern combo + angle box."""
        import System.Windows.Controls as WC
        import System.Windows as SW
        from System.Windows.Media import SolidColorBrush, Color as WColor

        host = self.pattern_stack
        host.Children.Clear()
        self._pattern_ctrls = []

        row_brush_alt = SolidColorBrush(WColor.FromRgb(250, 250, 250))

        for i, (fi, row) in enumerate(zip(self._floors, self._rows)):
            outer = WC.Border()
            outer.BorderBrush = SolidColorBrush(WColor.FromRgb(236, 240, 241))
            outer.BorderThickness = SW.Thickness(0, 0, 0, 1)
            outer.Padding = SW.Thickness(14, 10, 14, 10)
            if i % 2 == 1: outer.Background = row_brush_alt

            grid = WC.Grid()
            for w in (SW.GridLength(1, SW.GridUnitType.Star),
                      SW.GridLength(200),
                      SW.GridLength(70)):
                cd = WC.ColumnDefinition(); cd.Width = w
                grid.ColumnDefinitions.Add(cd)

            # Label block
            lbl = WC.StackPanel(); lbl.VerticalAlignment = SW.VerticalAlignment.Center
            name_tb = WC.TextBlock()
            name_tb.Text = row.Name
            name_tb.FontSize = 13
            name_tb.FontWeight = SW.FontWeights.SemiBold
            name_tb.Foreground = SolidColorBrush(WColor.FromRgb(44, 62, 80))
            sub_tb = WC.TextBlock()
            sub_tb.Text = "{}  ·  {} m²  ·  {} mm".format(
                row.LevelName, row.AreaM2, row.Dimensions)
            sub_tb.FontSize = 11
            sub_tb.Foreground = SolidColorBrush(WColor.FromRgb(127, 140, 141))
            sub_tb.Margin = SW.Thickness(0, 2, 0, 0)
            lbl.Children.Add(name_tb); lbl.Children.Add(sub_tb)
            WC.Grid.SetColumn(lbl, 0); grid.Children.Add(lbl)

            # Pattern combo
            cmb = WC.ComboBox()
            cmb.Items.Add("Grid (Stacked Bond)")
            cmb.Items.Add("Staggered (Running Bond)")
            cmb.SelectedIndex = 0
            cmb.FontSize = 12
            cmb.Padding = SW.Thickness(8, 5, 8, 5)
            cmb.BorderBrush = SolidColorBrush(WColor.FromRgb(189, 195, 199))
            cmb.Margin = SW.Thickness(0, 0, 10, 0)
            cmb.VerticalAlignment = SW.VerticalAlignment.Center
            WC.Grid.SetColumn(cmb, 1); grid.Children.Add(cmb)

            # Angle box
            txt = WC.TextBox()
            txt.Text = "0"
            txt.FontSize = 12
            txt.Padding = SW.Thickness(8, 6, 8, 6)
            txt.BorderBrush = SolidColorBrush(WColor.FromRgb(189, 195, 199))
            txt.BorderThickness = SW.Thickness(1)
            txt.VerticalAlignment = SW.VerticalAlignment.Center
            txt.ToolTip = "Layout angle (degrees)"
            WC.Grid.SetColumn(txt, 2); grid.Children.Add(txt)

            outer.Child = grid
            host.Children.Add(outer)
            self._pattern_ctrls.append((cmb, txt))

    # ═════════════════════════════════════════════════════════════════════════
    # STEP 2 → generate options
    # ═════════════════════════════════════════════════════════════════════════

    def _generate_concepts(self):
        try:
            params = self._read_params()
        except ValueError as exc:
            TaskDialog.Show("Input Error", str(exc))
            return False
        self._params = params

        self.status_text.Text = "Generating candidate layouts…"
        self.UpdateLayout()

        gen = OptionGenerator(params['tile_w_ft'], params['tile_h_ft'],
                              params['joint_ft'], params['optimize_nesting'],
                              top_n=4)

        for fi, (cmb, txt) in zip(self._floors, self._pattern_ctrls):
            pattern = 'staggered' if cmb.SelectedIndex == 1 else 'grid'
            try:
                base_angle = float((txt.Text or "0").strip())
            except ValueError:
                base_angle = 0.0
            fi.options = gen.generate(fi, pattern, base_angle)
            fi.chosen_option_id = fi.options[0].option_id if fi.options else None
            fi._pattern = pattern

        self._build_concepts_ui()
        self.status_text.Text = (
            "Generated {} options × {} floor(s). Click a card to change the choice."
            .format(sum(len(f.options) for f in self._floors),
                    len(self._floors)))
        return True

    def _build_concepts_ui(self):
        """Populate the Step 3 stack with one section per floor."""
        import System.Windows.Controls as WC
        import System.Windows.Controls.Primitives as WCP
        import System.Windows as SW
        from System.Windows.Media import SolidColorBrush, Color as WColor

        host = self.concepts_host
        host.Children.Clear()
        self._selection_buttons = {}

        for fi_idx, fi in enumerate(self._floors):
            # Section header
            hdr = WC.TextBlock()
            hdr.Text = "{}  —  {} · {} mm".format(
                self._rows[fi_idx].Name,
                self._rows[fi_idx].AreaM2 + " m²",
                self._rows[fi_idx].Dimensions)
            hdr.FontSize = 14
            hdr.FontWeight = SW.FontWeights.SemiBold
            hdr.Foreground = SolidColorBrush(WColor.FromRgb(44, 62, 80))
            hdr.Margin = SW.Thickness(0, 16, 0, 8)
            host.Children.Add(hdr)

            # 2 cards per row — split 4 options into 2×2. Extra options wrap
            # to additional rows automatically via UniformGrid.
            grid = WCP.UniformGrid()
            n_opts = len(fi.options) if fi.options else 1
            grid.Columns = min(2, n_opts)
            grid.Rows = int(math.ceil(n_opts / 2.0)) if n_opts else 1

            for opt in fi.options:
                card = self._build_option_card(fi_idx, fi, opt)
                grid.Children.Add(card)
            host.Children.Add(grid)

        # Paint the default-selected option ("A") for each floor
        for i in range(len(self._floors)):
            self._refresh_selection_highlights(i)

    def _build_option_card(self, fi_idx, fi, opt):
        import System.Windows.Controls as WC
        import System.Windows as SW
        import System.Windows.Input as SWI
        from System.Windows.Media import SolidColorBrush, Color as WColor

        brush_border = SolidColorBrush(WColor.FromRgb(189, 195, 199))
        brush_select = SolidColorBrush(WColor.FromRgb(52, 152, 219))
        brush_text   = SolidColorBrush(WColor.FromRgb(44, 62, 80))
        brush_sub    = SolidColorBrush(WColor.FromRgb(127, 140, 141))

        outer = WC.Border()
        outer.BorderBrush = brush_border
        outer.BorderThickness = SW.Thickness(2)
        outer.CornerRadius = SW.CornerRadius(6)
        outer.Margin = SW.Thickness(6)
        outer.Padding = SW.Thickness(10)
        outer.Background = SolidColorBrush(WColor.FromRgb(255, 255, 255))
        outer.Cursor = SWI.Cursors.Hand

        stack = WC.StackPanel()

        # Header: "● Option A                              [↗ Expand]"
        header = WC.Grid()
        cdef0 = WC.ColumnDefinition(); cdef0.Width = SW.GridLength(1, SW.GridUnitType.Star)
        cdef1 = WC.ColumnDefinition(); cdef1.Width = SW.GridLength.Auto
        header.ColumnDefinitions.Add(cdef0)
        header.ColumnDefinitions.Add(cdef1)

        header_left = WC.StackPanel(); header_left.Orientation = WC.Orientation.Horizontal
        dot = WC.TextBlock()
        dot.Text = "●"; dot.FontSize = 16; dot.Foreground = brush_sub
        dot.Margin = SW.Thickness(0, 0, 6, 0)
        name = WC.TextBlock()
        name.Text = "Option {}".format(opt.option_id)
        name.FontSize = 13; name.FontWeight = SW.FontWeights.SemiBold
        name.Foreground = brush_text
        header_left.Children.Add(dot); header_left.Children.Add(name)
        WC.Grid.SetColumn(header_left, 0)
        header.Children.Add(header_left)

        btn_expand = WC.Button()
        btn_expand.Content = u"\u2197 Expand"
        btn_expand.FontSize = 10
        btn_expand.Height = 22
        btn_expand.Padding = SW.Thickness(6, 0, 6, 0)
        btn_expand.ToolTip = "Open this option in a large detail window"
        WC.Grid.SetColumn(btn_expand, 1)
        header.Children.Add(btn_expand)

        stack.Children.Add(header)

        desc = WC.TextBlock()
        desc.Text = opt.variant; desc.FontSize = 10; desc.Foreground = brush_sub
        desc.Margin = SW.Thickness(0, 0, 0, 8)
        stack.Children.Add(desc)

        # Preview canvas
        preview = render_option_preview(opt, fi.pts, canvas_w=190, canvas_h=130)
        preview_host = WC.Border()
        preview_host.Background = SolidColorBrush(WColor.FromRgb(250, 250, 250))
        preview_host.BorderBrush = SolidColorBrush(WColor.FromRgb(236, 240, 241))
        preview_host.BorderThickness = SW.Thickness(1)
        preview_host.CornerRadius = SW.CornerRadius(4)
        preview_host.Child = preview
        preview_host.HorizontalAlignment = SW.HorizontalAlignment.Center
        preview_host.Margin = SW.Thickness(0, 0, 0, 8)
        stack.Children.Add(preview_host)

        # Stat rows
        def _stat_row(k, v, color=None):
            row = WC.StackPanel(); row.Orientation = WC.Orientation.Horizontal
            row.Margin = SW.Thickness(0, 1, 0, 1)
            kt = WC.TextBlock(); kt.Text = k; kt.FontSize = 11
            kt.Foreground = brush_sub; kt.Width = 105
            vt = WC.TextBlock(); vt.Text = v; vt.FontSize = 11
            vt.FontWeight = SW.FontWeights.SemiBold
            vt.Foreground = color or brush_text
            row.Children.Add(kt); row.Children.Add(vt)
            return row

        ok_col    = SolidColorBrush(WColor.FromRgb( 39, 174,  96))
        warn_col  = SolidColorBrush(WColor.FromRgb(231,  76,  60))
        waste_col = ok_col if opt.waste_pct < 10 else warn_col

        stack.Children.Add(_stat_row(
            "Full tiles:", "{}".format(opt.n_full)))
        stack.Children.Add(_stat_row(
            "Cut (A):",    "{}".format(opt.n_cut)))
        stack.Children.Add(_stat_row(
            "Reused (B):", "{}".format(opt.n_reuse), ok_col if opt.n_reuse else brush_text))
        stack.Children.Add(_stat_row(
            "Tiles to buy:", "{}".format(opt.tiles_to_buy)))
        stack.Children.Add(_stat_row(
            "Waste:", "{:.1f} %".format(opt.waste_pct), waste_col))

        thin_col = warn_col if opt.n_thin_cuts else ok_col
        stack.Children.Add(_stat_row(
            u"Cuts < {:.0f}mm:".format(MIN_CUT_WIDTH_MM),
            "{}".format(opt.n_thin_cuts),
            thin_col))

        # ── Angle adjustment row ──────────────────────────────────────────
        cur_angle = opt.gen_params.get('angle', 0.0) if opt.gen_params else 0.0

        angle_row = WC.StackPanel()
        angle_row.Orientation = WC.Orientation.Horizontal
        angle_row.Margin = SW.Thickness(0, 8, 0, 0)
        angle_row.HorizontalAlignment = SW.HorizontalAlignment.Center

        lbl = WC.TextBlock()
        lbl.Text = "Angle:"; lbl.FontSize = 11; lbl.Foreground = brush_sub
        lbl.VerticalAlignment = SW.VerticalAlignment.Center
        lbl.Margin = SW.Thickness(0, 0, 6, 0)
        angle_row.Children.Add(lbl)

        btn_minus = WC.Button()
        btn_minus.Content = "−"; btn_minus.Width = 22; btn_minus.Height = 22
        btn_minus.Margin = SW.Thickness(0, 0, 2, 0)
        btn_minus.ToolTip = "Rotate −5°"
        angle_row.Children.Add(btn_minus)

        txt_angle = WC.TextBox()
        txt_angle.Text = "{:.1f}".format(cur_angle)
        txt_angle.Width = 46; txt_angle.Height = 22
        txt_angle.FontSize = 11
        txt_angle.TextAlignment = SW.TextAlignment.Center
        txt_angle.VerticalContentAlignment = SW.VerticalAlignment.Center
        angle_row.Children.Add(txt_angle)

        btn_plus = WC.Button()
        btn_plus.Content = "+"; btn_plus.Width = 22; btn_plus.Height = 22
        btn_plus.Margin = SW.Thickness(2, 0, 6, 0)
        btn_plus.ToolTip = "Rotate +5°"
        angle_row.Children.Add(btn_plus)

        btn_apply = WC.Button()
        btn_apply.Content = "Apply"; btn_apply.Height = 22
        btn_apply.Padding = SW.Thickness(6, 0, 6, 0)
        btn_apply.ToolTip = "Apply angle value from textbox"
        angle_row.Children.Add(btn_apply)

        stack.Children.Add(angle_row)

        # ── Shift adjustment row (← ↑ ↓ → + reset) ───────────────────────
        # Step size = 10% of the tile dimension, same minimum practical step
        # as the initial variant sweep (which uses 25% / 50% / 75%).
        step_x = opt.gen_params.get('tile_w', 0.0) * 0.1 if opt.gen_params else 0.0
        step_y = opt.gen_params.get('tile_h', 0.0) * 0.1 if opt.gen_params else 0.0

        shift_row = WC.StackPanel()
        shift_row.Orientation = WC.Orientation.Horizontal
        shift_row.Margin = SW.Thickness(0, 6, 0, 0)
        shift_row.HorizontalAlignment = SW.HorizontalAlignment.Center

        lbl_s = WC.TextBlock()
        lbl_s.Text = "Shift:"; lbl_s.FontSize = 11; lbl_s.Foreground = brush_sub
        lbl_s.VerticalAlignment = SW.VerticalAlignment.Center
        lbl_s.Margin = SW.Thickness(0, 0, 6, 0)
        shift_row.Children.Add(lbl_s)

        def _nav_btn(content, tooltip):
            b = WC.Button()
            b.Content = content
            b.Width = 24; b.Height = 22
            b.Margin = SW.Thickness(0, 0, 2, 0)
            b.ToolTip = tooltip
            b.FontSize = 11
            return b

        btn_left  = _nav_btn(u"\u2190", "Shift left (10% of tile width)")
        btn_up    = _nav_btn(u"\u2191", "Shift up (10% of tile height)")
        btn_down  = _nav_btn(u"\u2193", "Shift down (10% of tile height)")
        btn_right = _nav_btn(u"\u2192", "Shift right (10% of tile width)")
        btn_reset = WC.Button()
        btn_reset.Content = "Reset"
        btn_reset.Height = 22; btn_reset.Margin = SW.Thickness(4, 0, 0, 0)
        btn_reset.Padding = SW.Thickness(6, 0, 6, 0)
        btn_reset.FontSize = 11
        btn_reset.ToolTip = "Reset shift to 0 / 0"

        shift_row.Children.Add(btn_left)
        shift_row.Children.Add(btn_up)
        shift_row.Children.Add(btn_down)
        shift_row.Children.Add(btn_right)
        shift_row.Children.Add(btn_reset)
        stack.Children.Add(shift_row)

        # Shift readout
        cur_dx_mm = (opt.gen_params.get('dx', 0.0) or 0.0) * FT_TO_MM
        cur_dy_mm = (opt.gen_params.get('dy', 0.0) or 0.0) * FT_TO_MM
        shift_readout = WC.TextBlock()
        shift_readout.Text = "dx {:+.0f} · dy {:+.0f} mm".format(
            cur_dx_mm, cur_dy_mm)
        shift_readout.FontSize = 10; shift_readout.Foreground = brush_sub
        shift_readout.HorizontalAlignment = SW.HorizontalAlignment.Center
        shift_readout.Margin = SW.Thickness(0, 2, 0, 0)
        stack.Children.Add(shift_readout)

        outer.Child = stack

        # Click → select this option. Use bubbling event so clicks on the
        # inner buttons / textbox (which mark e.Handled) don't bubble up.
        def _on_click(s, e):
            fi.chosen_option_id = opt.option_id
            self._refresh_selection_highlights(fi_idx)
            self._refresh_step_ui()
        outer.MouseLeftButtonUp += _on_click

        # Angle handlers
        def _apply_angle(new_angle):
            if not opt.regenerate(angle=new_angle):
                return
            self._replace_option_card(fi_idx, fi, opt)

        def _on_minus(s, e):
            a = (opt.gen_params.get('angle', 0.0) or 0.0) - 5.0
            _apply_angle(a); e.Handled = True

        def _on_plus(s, e):
            a = (opt.gen_params.get('angle', 0.0) or 0.0) + 5.0
            _apply_angle(a); e.Handled = True

        def _on_apply(s, e):
            try:
                a = float((txt_angle.Text or "0").strip())
            except ValueError:
                a = opt.gen_params.get('angle', 0.0) or 0.0
            _apply_angle(a); e.Handled = True

        btn_minus.Click += _on_minus
        btn_plus.Click  += _on_plus
        btn_apply.Click += _on_apply

        # Shift handlers
        def _shift(ddx, ddy):
            cur_dx = opt.gen_params.get('dx', 0.0) or 0.0
            cur_dy = opt.gen_params.get('dy', 0.0) or 0.0
            if not opt.regenerate(dx=cur_dx + ddx, dy=cur_dy + ddy):
                return
            self._replace_option_card(fi_idx, fi, opt)

        def _on_left (s, e): _shift(-step_x, 0); e.Handled = True
        def _on_right(s, e): _shift( step_x, 0); e.Handled = True
        def _on_up   (s, e): _shift(0,  step_y); e.Handled = True
        def _on_down (s, e): _shift(0, -step_y); e.Handled = True
        def _on_reset(s, e):
            if opt.regenerate(dx=0.0, dy=0.0):
                self._replace_option_card(fi_idx, fi, opt)
            e.Handled = True

        btn_left.Click  += _on_left
        btn_right.Click += _on_right
        btn_up.Click    += _on_up
        btn_down.Click  += _on_down
        btn_reset.Click += _on_reset

        # Expand handler
        def _on_expand(s, e):
            self._open_option_detail(fi_idx, fi, opt)
            # After dialog closes, refresh the card to reflect any changes.
            self._replace_option_card(fi_idx, fi, opt)
            e.Handled = True
        btn_expand.Click += _on_expand

        self._selection_buttons[(fi_idx, opt.option_id)] = outer
        return outer

    def _open_option_detail(self, fi_idx, fi, opt):
        """Large modal window that shows one option with a big interactive
        preview + full angle / shift controls. Edits made inside regenerate
        `opt` in place; the caller refreshes the originating card when this
        dialog closes."""
        import System.Windows as SW
        import System.Windows.Controls as WC
        from System.Windows.Media import SolidColorBrush, Color as WColor, FontFamily

        brush_dark = SolidColorBrush(WColor.FromRgb(44, 62, 80))
        brush_sub  = SolidColorBrush(WColor.FromRgb(127, 140, 141))
        brush_ok   = SolidColorBrush(WColor.FromRgb(39, 174, 96))
        brush_warn = SolidColorBrush(WColor.FromRgb(231, 76, 60))
        brush_line = SolidColorBrush(WColor.FromRgb(236, 240, 241))

        win = SW.Window()
        win.Title = u"Option {}  \u2014  Floor #{}".format(
            opt.option_id, fi_idx + 1)
        win.Width = 1180
        win.Height = 760
        win.WindowStartupLocation = SW.WindowStartupLocation.CenterOwner
        win.Background = SolidColorBrush(WColor.FromRgb(255, 255, 255))
        win.FontFamily = FontFamily("Segoe UI")
        try:
            win.Owner = self
        except Exception:
            pass

        root = WC.Grid()
        root.Margin = SW.Thickness(16)
        c0 = WC.ColumnDefinition(); c0.Width = SW.GridLength(1, SW.GridUnitType.Star)
        c1 = WC.ColumnDefinition(); c1.Width = SW.GridLength(340)
        root.ColumnDefinitions.Add(c0)
        root.ColumnDefinitions.Add(c1)

        # ── Left: large preview host ──
        preview_host = WC.Border()
        preview_host.Background = SolidColorBrush(WColor.FromRgb(250, 250, 250))
        preview_host.BorderBrush = brush_line
        preview_host.BorderThickness = SW.Thickness(1)
        preview_host.CornerRadius = SW.CornerRadius(4)
        preview_host.Margin = SW.Thickness(0, 0, 16, 0)
        WC.Grid.SetColumn(preview_host, 0)
        root.Children.Add(preview_host)

        # ── Right: controls panel ──
        panel = WC.StackPanel()
        WC.Grid.SetColumn(panel, 1)
        root.Children.Add(panel)

        title = WC.TextBlock()
        title.Text = u"Option {}".format(opt.option_id)
        title.FontSize = 22; title.FontWeight = SW.FontWeights.Bold
        title.Foreground = brush_dark
        panel.Children.Add(title)

        floor_info = WC.TextBlock()
        floor_info.Text = u"Floor #{}  \u00b7  {:.1f} m\u00b2  \u00b7  {:.0f} \u00d7 {:.0f} mm".format(
            fi_idx + 1, fi.area_ft2 * FT2_TO_M2,
            fi.width_ft * FT_TO_MM, fi.height_ft * FT_TO_MM)
        floor_info.FontSize = 11; floor_info.Foreground = brush_sub
        floor_info.Margin = SW.Thickness(0, 0, 0, 12)
        panel.Children.Add(floor_info)

        variant_lbl = WC.TextBlock()
        variant_lbl.FontSize = 11; variant_lbl.Foreground = brush_sub
        variant_lbl.TextWrapping = SW.TextWrapping.Wrap
        variant_lbl.Margin = SW.Thickness(0, 0, 0, 12)
        panel.Children.Add(variant_lbl)

        stats_panel = WC.StackPanel()
        stats_panel.Margin = SW.Thickness(0, 0, 0, 16)
        panel.Children.Add(stats_panel)

        # Angle row
        def _row_label(t):
            b = WC.TextBlock()
            b.Text = t; b.FontSize = 12; b.FontWeight = SW.FontWeights.SemiBold
            b.Foreground = brush_dark
            b.Margin = SW.Thickness(0, 8, 0, 4)
            return b

        panel.Children.Add(_row_label("Angle"))
        angle_row = WC.StackPanel(); angle_row.Orientation = WC.Orientation.Horizontal
        btn_a_minus = WC.Button(); btn_a_minus.Content = u"\u2212"
        btn_a_minus.Width = 28; btn_a_minus.Height = 26
        btn_a_minus.Margin = SW.Thickness(0, 0, 3, 0)
        btn_a_plus = WC.Button(); btn_a_plus.Content = "+"
        btn_a_plus.Width = 28; btn_a_plus.Height = 26
        btn_a_plus.Margin = SW.Thickness(3, 0, 6, 0)
        txt_a = WC.TextBox()
        txt_a.Width = 70; txt_a.Height = 26
        txt_a.TextAlignment = SW.TextAlignment.Center
        txt_a.VerticalContentAlignment = SW.VerticalAlignment.Center
        btn_a_apply = WC.Button(); btn_a_apply.Content = "Apply"
        btn_a_apply.Height = 26
        btn_a_apply.Padding = SW.Thickness(10, 0, 10, 0)
        btn_a_apply.Margin = SW.Thickness(6, 0, 0, 0)
        angle_row.Children.Add(btn_a_minus)
        angle_row.Children.Add(btn_a_plus)
        angle_row.Children.Add(txt_a)
        angle_row.Children.Add(btn_a_apply)
        panel.Children.Add(angle_row)

        # Shift row
        panel.Children.Add(_row_label("Shift"))
        shift_row = WC.StackPanel(); shift_row.Orientation = WC.Orientation.Horizontal
        def _nav(content, tt):
            b = WC.Button(); b.Content = content
            b.Width = 32; b.Height = 28
            b.Margin = SW.Thickness(0, 0, 3, 0); b.FontSize = 12
            b.ToolTip = tt
            return b
        btn_s_left  = _nav(u"\u2190", "Shift left  (10% of tile width)")
        btn_s_up    = _nav(u"\u2191", "Shift up    (10% of tile height)")
        btn_s_down  = _nav(u"\u2193", "Shift down  (10% of tile height)")
        btn_s_right = _nav(u"\u2192", "Shift right (10% of tile width)")
        btn_s_reset = WC.Button()
        btn_s_reset.Content = "Reset"; btn_s_reset.Height = 28
        btn_s_reset.Padding = SW.Thickness(10, 0, 10, 0)
        btn_s_reset.Margin = SW.Thickness(6, 0, 0, 0)
        for b in (btn_s_left, btn_s_up, btn_s_down, btn_s_right, btn_s_reset):
            shift_row.Children.Add(b)
        panel.Children.Add(shift_row)

        shift_readout = WC.TextBlock()
        shift_readout.FontSize = 11; shift_readout.Foreground = brush_sub
        shift_readout.Margin = SW.Thickness(0, 6, 0, 0)
        panel.Children.Add(shift_readout)

        # Close button at bottom
        spacer = WC.Grid(); spacer.Height = 24
        panel.Children.Add(spacer)
        btn_close = WC.Button()
        btn_close.Content = "Close"
        btn_close.Height = 30; btn_close.Padding = SW.Thickness(20, 0, 20, 0)
        btn_close.HorizontalAlignment = SW.HorizontalAlignment.Right
        panel.Children.Add(btn_close)

        # ── Render helpers ─────────────────────────────────────────────────
        def _redraw():
            # Large preview with zoom/pan (reuses existing interactive renderer).
            preview_host.Child = render_option_preview(
                opt, fi.pts, canvas_w=760, canvas_h=640)

            variant_lbl.Text = opt.variant

            # Rebuild stats
            stats_panel.Children.Clear()
            def _stat(k, v, color=None):
                r = WC.StackPanel(); r.Orientation = WC.Orientation.Horizontal
                r.Margin = SW.Thickness(0, 2, 0, 2)
                kt = WC.TextBlock(); kt.Text = k; kt.FontSize = 12
                kt.Foreground = brush_sub; kt.Width = 140
                vt = WC.TextBlock(); vt.Text = v; vt.FontSize = 12
                vt.FontWeight = SW.FontWeights.SemiBold
                vt.Foreground = color or brush_dark
                r.Children.Add(kt); r.Children.Add(vt)
                stats_panel.Children.Add(r)

            waste_col = brush_ok if opt.waste_pct < 10 else brush_warn
            thin_col  = brush_warn if opt.n_thin_cuts else brush_ok
            _stat("Full tiles:",     "{}".format(opt.n_full))
            _stat("Cut (A):",        "{}".format(opt.n_cut))
            _stat("Reused (B+):",    "{}".format(opt.n_reuse),
                  brush_ok if opt.n_reuse else brush_dark)
            _stat("Tiles to buy:",   "{}".format(opt.tiles_to_buy))
            _stat("Waste:",          "{:.1f} %".format(opt.waste_pct), waste_col)
            _stat(u"Cuts < {:.0f}mm:".format(MIN_CUT_WIDTH_MM),
                  "{}".format(opt.n_thin_cuts), thin_col)

            gp = opt.gen_params or {}
            txt_a.Text = "{:.1f}".format(gp.get('angle', 0.0) or 0.0)
            shift_readout.Text = u"dx {:+.0f} mm  \u00b7  dy {:+.0f} mm".format(
                (gp.get('dx', 0.0) or 0.0) * FT_TO_MM,
                (gp.get('dy', 0.0) or 0.0) * FT_TO_MM)

        # ── Handlers ──
        step_x = (opt.gen_params.get('tile_w', 0.0) or 0.0) * 0.1
        step_y = (opt.gen_params.get('tile_h', 0.0) or 0.0) * 0.1

        def _apply_angle(new_angle):
            if opt.regenerate(angle=new_angle): _redraw()

        def _on_a_minus(s, e):
            _apply_angle((opt.gen_params.get('angle', 0.0) or 0.0) - 5.0)
        def _on_a_plus (s, e):
            _apply_angle((opt.gen_params.get('angle', 0.0) or 0.0) + 5.0)
        def _on_a_apply(s, e):
            try: a = float((txt_a.Text or "0").strip())
            except ValueError: a = opt.gen_params.get('angle', 0.0) or 0.0
            _apply_angle(a)

        def _shift(ddx, ddy):
            cur_dx = opt.gen_params.get('dx', 0.0) or 0.0
            cur_dy = opt.gen_params.get('dy', 0.0) or 0.0
            if opt.regenerate(dx=cur_dx + ddx, dy=cur_dy + ddy):
                _redraw()

        def _on_s_left (s, e): _shift(-step_x, 0)
        def _on_s_right(s, e): _shift( step_x, 0)
        def _on_s_up   (s, e): _shift(0,  step_y)
        def _on_s_down (s, e): _shift(0, -step_y)
        def _on_s_reset(s, e):
            if opt.regenerate(dx=0.0, dy=0.0): _redraw()

        def _on_close(s, e): win.Close()

        btn_a_minus.Click += _on_a_minus
        btn_a_plus.Click  += _on_a_plus
        btn_a_apply.Click += _on_a_apply
        btn_s_left.Click  += _on_s_left
        btn_s_right.Click += _on_s_right
        btn_s_up.Click    += _on_s_up
        btn_s_down.Click  += _on_s_down
        btn_s_reset.Click += _on_s_reset
        btn_close.Click   += _on_close

        win.Content = root
        _redraw()
        win.ShowDialog()

    def _replace_option_card(self, fi_idx, fi, opt):
        """Swap the card for `opt` in place after regeneration."""
        key = (fi_idx, opt.option_id)
        old = self._selection_buttons.get(key)
        if old is None: return
        parent = old.Parent  # UniformGrid
        if parent is None: return
        idx = parent.Children.IndexOf(old)
        if idx < 0: return
        new_card = self._build_option_card(fi_idx, fi, opt)
        parent.Children.RemoveAt(idx)
        parent.Children.Insert(idx, new_card)
        self._refresh_selection_highlights(fi_idx)
        gp = opt.gen_params or {}
        self.status_text.Text = (
            u"Option {}  \u00b7  angle {:+.1f}\u00b0  \u00b7  "
            u"shift {:+.0f}/{:+.0f} mm  \u00b7  waste {:.1f}%".format(
                opt.option_id,
                gp.get('angle', 0.0),
                gp.get('dx', 0.0) * FT_TO_MM,
                gp.get('dy', 0.0) * FT_TO_MM,
                opt.waste_pct))

    def _refresh_selection_highlights(self, fi_idx):
        import System.Windows as SW
        from System.Windows.Media import SolidColorBrush, Color as WColor
        selected = SolidColorBrush(WColor.FromRgb(52, 152, 219))
        normal   = SolidColorBrush(WColor.FromRgb(189, 195, 199))
        fi = self._floors[fi_idx]
        for (idx, oid), border in self._selection_buttons.items():
            if idx != fi_idx: continue
            if oid == fi.chosen_option_id:
                border.BorderBrush = selected
                border.BorderThickness = SW.Thickness(2.5)
            else:
                border.BorderBrush = normal
                border.BorderThickness = SW.Thickness(2)

    # ═════════════════════════════════════════════════════════════════════════
    # STEP 3 → apply to model
    # ═════════════════════════════════════════════════════════════════════════

    def _apply_selection(self):
        chosen = []
        for fi in self._floors:
            if fi.chosen_option_id is None: continue
            match = [o for o in fi.options if o.option_id == fi.chosen_option_id]
            if not match: continue
            chosen.append((fi, match[0], getattr(fi, '_pattern', 'grid')))

        if not chosen:
            TaskDialog.Show("Tile Layout", "No selections to apply.")
            return

        try:
            with revit.Transaction("Tile Layout — apply selected concepts"):
                view = get_or_create_3d_view()
                for fi, opt, _pat in chosen:
                    vis = RevitVisualizer(view)
                    for piece in opt.pieces:
                        vis.draw_piece(piece, fi.z)
            uidoc.ActiveView = view
        except Exception as exc:
            TaskDialog.Show("Apply Error",
                "{}\n\n{}".format(exc, traceback.format_exc()))
            return

        # Defer report construction — it's only built lazily when the user
        # actually clicks Export CSV / Export PDF, so apply stays snappy.
        self._applied = True
        self.btn_export.IsEnabled = True
        self.btn_export_report.IsEnabled = True
        self.status_text.Text = (
            "Applied {} option(s). DirectShapes created in 'Tile Layout Preview'."
            .format(len(chosen)))

    # ═════════════════════════════════════════════════════════════════════════
    # Action-bar buttons
    # ═════════════════════════════════════════════════════════════════════════

    def next_clicked(self, sender, args):
        if self._step == STEP_BOUNDARIES:
            if not self._floors:
                TaskDialog.Show("Tile Layout", "Select floors first.")
                return
            self._step = STEP_PATTERN
            self._refresh_step_ui()
        elif self._step == STEP_PATTERN:
            if self._generate_concepts():
                self._step = STEP_CONCEPTS
                self._refresh_step_ui()
        elif self._step == STEP_CONCEPTS:
            self._apply_selection()

    def back_clicked(self, sender, args):
        if self._step > STEP_BOUNDARIES:
            self._step -= 1
            self._refresh_step_ui()

    def _build_report(self):
        """Build a ReportGenerator on demand from the current selections.
        No report work is done until the user clicks an Export button."""
        if not getattr(self, '_applied', False) or not self._params:
            TaskDialog.Show("Export", "Apply the layout first.")
            return None
        chosen = []
        for fi in self._floors:
            if fi.chosen_option_id is None: continue
            match = [o for o in fi.options if o.option_id == fi.chosen_option_id]
            if not match: continue
            chosen.append((fi, match[0], getattr(fi, '_pattern', 'grid')))
        if not chosen:
            TaskDialog.Show("Export", "No selections to export.")
            return None
        params_mm = dict(
            tile_w_mm=self._params['tile_w_ft'] * FT_TO_MM,
            tile_h_mm=self._params['tile_h_ft'] * FT_TO_MM,
            joint_mm =self._params['joint_ft']  * FT_TO_MM,
            optimize_nesting=self._params['optimize_nesting'])
        return ReportGenerator(chosen, params_mm, self._floors)

    def export_csv_clicked(self, sender, args):
        rpt = self._build_report()
        if rpt is None: return
        try:
            from System.Windows.Forms import SaveFileDialog, DialogResult
            dlg = SaveFileDialog()
            dlg.Title    = "Save Tile Layout CSV"
            dlg.Filter   = "CSV files (*.csv)|*.csv"
            dlg.FileName = "TileLayout.csv"
            if dlg.ShowDialog() != DialogResult.OK: return
            rpt.export_csv(dlg.FileName)
            self.status_text.Text = "CSV saved: {}".format(
                os.path.basename(dlg.FileName))
        except Exception as exc:
            TaskDialog.Show("CSV Export Error", str(exc))

    def export_report_clicked(self, sender, args):
        """Export a PDF report of ALL options for every floor.

        The system Print dialog opens so the user can pick
        'Microsoft Print to PDF' (or any installed PDF printer); Windows
        then prompts for the output filename. The heavy FlowDocument
        (canvases, polygons) is built only on this click — nothing PDF-
        related runs until then.
        """
        self.status_text.Text = "Preparing PDF report…"
        self.UpdateLayout()
        rpt = self._build_report()
        if rpt is None: return
        try:
            ok = rpt.export_pdf("Tile Layout Report")
            if ok:
                self.status_text.Text = (
                    "Report sent to printer  \xb7  choose 'Microsoft Print "
                    "to PDF' to save as PDF.")
            else:
                self.status_text.Text = "Report export cancelled."
        except Exception as exc:
            TaskDialog.Show("Report Export Error",
                "{}\n\n{}".format(exc, traceback.format_exc()))

    # ═════════════════════════════════════════════════════════════════════════
    # Input parsing
    # ═════════════════════════════════════════════════════════════════════════

    def _read_params(self):
        def _mm(ctrl, name):
            try: v = float(ctrl.Text.strip())
            except (ValueError, AttributeError):
                raise ValueError("'{}' is not a valid number.".format(name))
            if v <= 0:
                raise ValueError("'{}' must be greater than zero.".format(name))
            return v * MM_TO_FT
        return dict(
            tile_w_ft = _mm(self.txt_tile_w, "Tile Width"),
            tile_h_ft = _mm(self.txt_tile_h, "Tile Height"),
            joint_ft  = _mm(self.txt_joint,  "Joint Width"),
            optimize_nesting = bool(self.chk_nesting.IsChecked),
        )


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def _pick_floors_from_revit():
    """Run PickObjects on the clean main UI thread (no modal window open)."""
    # Honour a live pre-selection if it already contains floors.
    try:
        sel_ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        sel_ids = []
    pre = []
    for eid in sel_ids:
        el = doc.GetElement(eid)
        if (el is not None and el.Category is not None and
                el.Category.Id.IntegerValue == int(BuiltInCategory.OST_Floors)):
            pre.append(el)
    if pre:
        return pre

    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, _FloorFilter(),
            "Select floor(s) for tile layout — press Finish when done")
    except Exception:
        return []
    return [doc.GetElement(r.ElementId) for r in refs]


def run():
    """Driver loop: pick → show dialog → maybe re-pick."""
    floors = _pick_floors_from_revit()
    while True:
        win = TileLayoutWindow(preselected_floors=floors)
        win.ShowDialog()
        if not win.wants_repick:
            break
        floors = _pick_floors_from_revit()
        if not floors:
            break


# MAIN SCRIPT
# ==============================================================================
if __name__ == '__main__':
    run()
