#! python3
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
from GUI.WPF_Base import T3WPFWindow

SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))
lib_dir    = os.path.join(EXT_DIR, 'lib')
gui_dir    = os.path.join(lib_dir, 'GUI')   # TileLayoutCore.py lives here
for _p in (lib_dir, gui_dir):
    if _p not in sys.path:
        sys.path.append(_p)

from Snippets._compat import eid_value, elem_name

# pyRevit caches lib modules between runs while pushbutton scripts are always
# re-read — after an extension update this script can end up calling engine
# methods that the cached (stale) module doesn't have yet (symptom: options
# silently stop generating with AttributeError). Force-refresh the engine.
# Engine lives in lib/GUI/TileLayoutCore.py (beside TileLayout.xaml's folder).
from GUI import TileLayoutCore
try:
    from importlib import reload as _reload
    _reload(TileLayoutCore)
except Exception:
    try:
        reload(TileLayoutCore)
    except Exception:
        pass

from GUI.TileLayoutCore import (
    MM_TO_FT, FT_TO_MM, FT2_TO_M2, MIN_CUT_WIDTH_MM,
    PATTERNS, PATTERN_LABELS,
    V2, poly_area, poly_bbox, ensure_ccw,
    OptionGenerator,
)

XAML_FILE  = os.path.join(os.path.dirname(__file__), 'Tools', 'TileLayout.xaml')

from GUI.ProgressPauseMixin import ProgressPauseMixin

# DEFINE VARIABLES
# ==============================================================================
logger        = script.get_logger()
# CPython has no ScriptOutput.GetDefault; safe_output()
# returns a no-op window instead of killing the tool.
try:
    from _cpython_bootstrap import safe_output
    output = safe_output()
except Exception:
    output = script.get_output()
# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
# point report the real problem.
try:
    doc = revit.doc
except Exception:
    doc = None
try:
    uidoc = revit.uidoc
except Exception:
    uidoc = None
# Read from the Application, not the document: `revit.doc` is None when this
# tool is launched from the Assistant pane or with no project open, and the
# old `int(revit.doc.Application.VersionNumber)` raised at IMPORT time
# ('NoneType' object has no attribute 'Application') so the window never opened.
from Snippets._host import get_revit_version
REVIT_VERSION = get_revit_version()

# ── Constants (units & engine limits are imported from TileLayoutCore) ───────
EXTRUDE_H = 10.0 * MM_TO_FT   # DirectShape thickness
MIN_EDGE  = 0.003             # ft — ~0.9 mm; below Revit short-curve tolerance
SLIVER_W  = 2.0 * MM_TO_FT    # ft — skip fragments with mean thickness < ~2 mm

# Name tag stamped on preview DirectShapes so a re-apply can find & clear them.
PREVIEW_DS_NAME = "T3Lab TileLayout Preview"

# Colours for DirectShape overrides
COL_FULL  = Color(189, 195, 199)
COL_CUT   = Color( 39, 174,  96)
COL_REUSE = Color( 52, 152, 219)
COL_WASTE = Color(231,  76,  60)


# CLASS/FUNCTIONS
# ==============================================================================

# SECTIONS 1–5 (2-D geometry, TileGrid, TilePiece/OffCut, NestingEngine,
# LayoutOption/OptionGenerator) live in lib/TileLayoutCore.py — pure Python,
# unit-tested by dev/debug/tile_layout_harness.py (no Revit required).

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
            try:
                ds.SetName(PREVIEW_DS_NAME)
            except Exception:
                pass

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

        # Sliver guard: mean thickness of a polygon ≈ 2·area/perimeter. Sub-2mm
        # cut slivers extrude into solids with pathologically long, thin faces
        # that crash Revit's faceter when the view regenerates during PDF
        # export ("long, thin face" + "Problems during faceting" → assertion
        # Arr.cpp:229 → fatal 0xc0000005; Revit 2023.1 journals 1053/1057,
        # 2026-07-20, same face ids in both sessions).
        area = 0.0
        perim = 0.0
        n = len(kept)
        for i in range(n):
            a = kept[i]
            b = kept[(i + 1) % n]
            area += a.X * b.Y - b.X * a.Y
            perim += a.DistanceTo(b)
        area = abs(area) * 0.5
        if perim <= 0 or (2.0 * area / perim) < SLIVER_W:
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


def clear_previous_preview(view):
    """Delete DirectShapes + TextNotes left by an earlier Apply so re-applying
    never stacks duplicate geometry. DirectShapes are matched by their name
    tag; TextNotes by ownership of the dedicated preview view. Must run inside
    an open transaction."""
    from System.Collections.Generic import List as NetList
    dead = NetList[ElementId]()
    for ds in FilteredElementCollector(doc).OfClass(DirectShape):
        try:
            if elem_name(ds) == PREVIEW_DS_NAME:
                dead.Add(ds.Id)
        except Exception:
            continue
    try:
        for tn in FilteredElementCollector(doc, view.Id).OfClass(TextNote):
            dead.Add(tn.Id)
    except Exception as ex:
        logger.debug("TextNote sweep failed: {}".format(ex))
    if dead.Count > 0:
        doc.Delete(dead)


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
            lines.append(u"  FLOOR  {}  ({})".format(
                fi.floor.Id, PATTERN_LABELS.get(pat, pat)))
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
                eid_value(fi.floor.Id),
                fi.area_ft2 * FT2_TO_M2,
                fi.width_ft * FT_TO_MM, fi.height_ft * FT_TO_MM,
                PATTERN_LABELS.get(chosen_pat, chosen_pat))))
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
        outer.CornerRadius = SW.CornerRadius(8)
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

import uuid

class _FloorFilter(ISelectionFilter):
    __namespace__ = "T3Lab.TileLayout"
    def AllowElement(self, e):
        return (e.Category is not None and
                eid_value(e.Category.Id) == int(BuiltInCategory.OST_Floors))
    def AllowReference(self, r, p): return False


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 12 — LIST-VIEW ROW VIEWMODELS (for ListView + ItemsControl binding)
# ═════════════════════════════════════════════════════════════════════════════

class FloorRowVM(object):
    """Row data for the Step 1 ListView (read-only display)."""

    def __init__(self, floor_info, display_index):
        self._fi = floor_info
        self.Name        = "Floor #{}  (id {})".format(
            display_index, eid_value(floor_info.floor.Id))
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


class TileLayoutWindow(T3WPFWindow):

    # ProgressPauseMixin — TileLayout.xaml status-bar progress panel
    PP_PANEL      = "tl_progress_panel"
    PP_BAR        = "tl_pb"
    PP_PAUSE      = "tl_btn_pause"
    PP_STOP       = "tl_btn_stop"
    PP_PAUSE_ICON = "tl_btn_pause_icon"
    PP_PAUSE_TEXT = "tl_btn_pause_label"
    PP_STATUS     = "status_text"
    PP_STOP_MSG   = u"Stopping… finishing current floor"

    def __init__(self, preselected_floors=None):
        T3WPFWindow.__init__(self, XAML_FILE)

        # ── wizard state ──
        self._floors = []        # [FloorInfo]
        self._rows   = []        # [FloorRowVM] (parallel to _floors)
        self._params = {}
        self._step   = STEP_BOUNDARIES
        self._max_step = STEP_BOUNDARIES   # furthest step reached — gates sidebar nav
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

    # ── sidebar step navigation ──────────────────────────────────────────────
    def step_nav_clicked(self, sender, args):
        """Sidebar step icon clicked — jump back to an already-reached step."""
        target = {"step1_toggle": STEP_BOUNDARIES,
                  "step2_toggle": STEP_PATTERN,
                  "step3_toggle": STEP_CONCEPTS}.get(sender.Name)
        if target is not None and target <= self._max_step:
            self._step = target
        self._refresh_step_ui()

    # ── step indicator / action-bar refresh ──────────────────────────────────
    def _refresh_step_ui(self):
        """Update sidebar step toggles, Back/Next button state and labels."""
        self._max_step = max(self._max_step, self._step)

        toggles = [self.step1_toggle, self.step2_toggle, self.step3_toggle]
        for i, tb in enumerate(toggles):
            tb.IsChecked = (i == self._step)
            tb.IsEnabled = (i <= self._max_step)

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
        from System import Object
        coll = ObservableCollection[Object]()
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

            # Pattern combo — items come from the shared PATTERNS list so
            # the engine keys and the UI can never drift apart.
            cmb = WC.ComboBox()
            for _key, label in PATTERNS:
                cmb.Items.Add(label)
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

        def _pump_ui():
            """Flush pending render ops so the status bar repaints while the
            sweep blocks the UI thread."""
            try:
                from System.Windows.Threading import DispatcherPriority
                from System import Action
                self.Dispatcher.Invoke(Action(lambda: None),
                                       DispatcherPriority.Background)
            except Exception:
                pass

        gen = OptionGenerator(params['tile_w_ft'], params['tile_h_ft'],
                              params['joint_ft'], params['optimize_nesting'],
                              top_n=4)

        n_kept = 0
        bw_gap = False
        n_floors = len(self._floors)
        for i_floor, (fi, (cmb, txt)) in enumerate(
                zip(self._floors, self._pattern_ctrls)):
            idx = cmb.SelectedIndex
            if idx < 0 or idx >= len(PATTERNS):
                idx = 0
            pattern = PATTERNS[idx][0]
            try:
                base_angle = float((txt.Text or "0").strip())
            except ValueError:
                base_angle = 0.0

            if pattern == 'basketweave':
                we = params['tile_w_ft'] + params['joint_ft']
                he = params['tile_h_ft'] + params['joint_ft']
                k = int(math.floor((we + 1e-9) / he)) if he > 1e-12 else 1
                if k < 1 or abs(we - k * he) > 0.5 * MM_TO_FT:
                    bw_gap = True

            _lbl = PATTERN_LABELS.get(pattern, pattern)
            def _prog(i, total, _f=i_floor, _p=_lbl):
                if i % 4 and i != total:
                    return   # update every 4th variant — enough feedback
                self.status_text.Text = (
                    u"Generating floor {}/{} — {} — variant {}/{}…".format(
                        _f + 1, n_floors, _p, i, total))
                _pump_ui()

            # Snapshot the option the user currently has selected (with any
            # angle/shift tweaks made on its card) BEFORE regenerating, so a
            # re-run of Generate updates it instead of discarding it.
            prev = None
            if fi.options and fi.chosen_option_id:
                match = [o for o in fi.options
                         if o.option_id == fi.chosen_option_id]
                if match and match[0].gen_params:
                    prev = dict(match[0].gen_params)

            fi.options = gen.generate(fi, pattern, base_angle,
                                      progress=_prog)
            chosen_id = fi.options[0].option_id if fi.options else None

            if prev is not None and fi.options:
                p_angle = prev.get('angle', 0.0) or 0.0
                p_dx    = prev.get('dx', 0.0) or 0.0
                p_dy    = prev.get('dy', 0.0) or 0.0
                twin = None
                for o in fi.options:
                    if o.matches_params(p_angle, p_dx, p_dy):
                        twin = o
                        break
                if twin is not None:
                    # The new sweep already contains the same grid — just
                    # keep it selected.
                    chosen_id = twin.option_id
                else:
                    # Rebuild the user's exact grid with the NEW tile
                    # parameters and append it as an extra card.
                    restored = gen.build_variant(
                        fi, pattern, p_angle, p_dx, p_dy)
                    n = len(fi.options)
                    restored.option_id = "ABCDEF"[n] if n < 6 else str(n + 1)
                    restored.variant += u"  ·  previous choice"
                    fi.options.append(restored)
                    chosen_id = restored.option_id
                n_kept += 1

            fi.chosen_option_id = chosen_id
            fi._pattern = pattern

        self._build_concepts_ui()
        msg = (
            "Generated {} options × {} floor(s). Click a card to change the choice."
            .format(sum(len(f.options) for f in self._floors),
                    len(self._floors)))
        if n_kept:
            msg += "  Previous choice kept on {} floor(s).".format(n_kept)
        if bw_gap:
            msg += (u"  ⚠ Basket Weave needs tile length = k × width "
                    u"(e.g. 600×300) — current size leaves gaps.")
        self.status_text.Text = msg
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
        outer.CornerRadius = SW.CornerRadius(8)
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

        # Shift handlers — deltas are SCREEN-space; shift_screen converts
        # them into the grid's rotated frame so the arrows track the screen
        # even on angled layouts.
        def _shift(ddx, ddy):
            if not opt.shift_screen(ddx, ddy):
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
            # Screen-space deltas — converted to the grid frame inside.
            if opt.shift_screen(ddx, ddy):
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

        self.begin_progress(len(chosen))
        try:
            with revit.Transaction("Tile Layout — apply selected concepts"):
                view = get_or_create_3d_view()
                # TextNote needs a locked 3D view — on an unlocked one
                # TextNote.Create throws and the piece labels silently vanish.
                try:
                    if not view.IsLocked:
                        view.SaveOrientationAndLock()
                except Exception as ex:
                    logger.debug("Could not lock 3D view: {}".format(ex))
                clear_previous_preview(view)
                for _tl_idx, (fi, opt, _pat) in enumerate(chosen):
                    if not self.step_progress(_tl_idx, "Applying floor {}/{}...".format(_tl_idx + 1, len(chosen))):
                        break
                    vis = RevitVisualizer(view)
                    for piece in opt.pieces:
                        # Waste is bookkeeping only (spare left inside the
                        # purchased tiles) — drawing it would stack a full
                        # tile on top of the cut piece and spill past the
                        # floor boundary.
                        if piece.piece_type == 'waste':
                            continue
                        vis.draw_piece(piece, fi.z)
            uidoc.ActiveView = view
        except Exception as exc:
            self.end_progress()
            TaskDialog.Show("Apply Error",
                "{}\n\n{}".format(exc, traceback.format_exc()))
            return

        cancelled = self.is_cancelled
        self.end_progress()

        # Defer report construction — it's only built lazily when the user
        # actually clicks Export CSV / Export PDF, so apply stays snappy.
        self._applied = True
        self.btn_export.IsEnabled = True
        self.btn_export_report.IsEnabled = True
        if cancelled:
            self.status_text.Text = (
                "Cancelled. DirectShapes created in 'Tile Layout Preview' for the applied floors.")
        else:
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

def _get_all_floors():
    """Every Floor instance in the model (used as the default Step 1 list)."""
    return list(FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_Floors)
                .WhereElementIsNotElementType()
                .ToElements())


def _pick_floors_from_revit():
    """Run PickObjects on the clean main UI thread (no modal window open).
    Used only by the "Pick Floors in Model" button's repick cycle — always
    an interactive pick, never falls back to listing every floor."""
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, _FloorFilter(),
            "Select floor(s) for tile layout — press Finish when done")
    except Exception:
        return []
    return [doc.GetElement(r.ElementId) for r in refs]


def _initial_floors():
    """Default floor set when the wizard first opens: honour a live
    pre-selection if it already contains floors, otherwise list every
    floor in the model so the user doesn't have to pick anything first."""
    try:
        sel_ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        sel_ids = []
    pre = []
    for eid in sel_ids:
        el = doc.GetElement(eid)
        if (el is not None and el.Category is not None and
                eid_value(el.Category.Id) == int(BuiltInCategory.OST_Floors)):
            pre.append(el)
    if pre:
        return pre
    return _get_all_floors()


def show_tile_layout_dialog(doc_param=None, uidoc_param=None):
    """Driver loop: list all floors (or honour pre-selection) → show dialog
    → user may re-pick specific floors via 'Pick Floors in Model'."""
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
    if not doc:
        forms.alert("No active Revit document found.", title="Tile Layout")
        return

    floors = _initial_floors()
    while True:
        win = TileLayoutWindow(preselected_floors=floors)
        win.ShowDialog()
        if not win.wants_repick:
            break
        floors = _pick_floors_from_revit()
        if not floors:
            break


def run():
    show_tile_layout_dialog()


if __name__ == '__main__':
    run()
