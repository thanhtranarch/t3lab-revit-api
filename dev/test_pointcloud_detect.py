# -*- coding: utf-8 -*-
"""
Regression tests for the Point Cloud to Model detection engine.

Sections 1-5 of the PointCloud pushbutton (unit helpers, geometry math, the
DetectedElement classes and PointCloudAnalyzer) are pure Python: they take a
list of (x, y, z) tuples and return detections. Only Sections 6-7 (the
ElementBuilder and the WPF window) actually need Revit. This harness execs the
pure half against stubbed Revit names, so the detector can be regression-tested
on any machine with no Revit and no pyRevit engine.

The plan notes for this tool record synthetic runs ("test 11 case pass") that
were never committed, so every tuning pass since has been unguarded — this file
makes those cases permanent.

Run:  python3 dev/test_pointcloud_detect.py

Author: Tran Tien Thanh
"""

import io
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_dialog = os.path.join(REPO, 'T3Lab.extension', 'lib', 'GUI', 'PointCloudDialog.py')
SCRIPT = _dialog if os.path.exists(_dialog) else os.path.join(
    REPO, 'T3Lab.extension', 'T3Lab.tab', 'Modeling & Datum.panel',
    'Create.stack', 'Create Elements.pulldown', 'PointCloud.pushbutton',
    'script.py')

MM_PER_FOOT = 304.8
M = 1000.0 / MM_PER_FOOT   # one metre in Revit internal feet


# ── Revit stubs ───────────────────────────────────────────────────────────────

class _Logger(object):
    def debug(self, *a, **k):  pass
    def error(self, *a, **k):  pass
    def warn(self, *a, **k):   pass


class _Collector(object):
    """FilteredElementCollector stub — the analyzer only ever asks for Grids
    and Levels, and an empty project is the strictest case (no grid angles to
    snap to, no levels to name)."""
    def __init__(self, doc):        pass
    def OfClass(self, cls):         return self
    def OfCategory(self, cat):      return self
    def WhereElementIsNotElementType(self): return self
    def ToElements(self):           return []


class _Marker(object):
    pass


def load_analyzer_module():
    """Exec Sections 1-5 of the pushbutton against stubbed Revit names."""
    src = io.open(SCRIPT, encoding='utf-8').read()

    start = src.index('# ── Section 1')
    end   = src.index('# ── Section 6')
    body  = src[start:end]

    ns = {
        '__name__': 'pointcloud_pure',
        'math': math,
        'logger': _Logger(),
        'doc': object(),
        'REVIT_VERSION': 2026,
        'FilteredElementCollector': _Collector,
        'Grid': _Marker,
        'Level': _Marker,
        'XYZ': _Marker,
        'Plane': _Marker,
        'List': {},
        'PointCloudFilterFactory': _Marker,
        'PointCloudInstance': _Marker,
        'ISelectionFilter': object,
    }
    exec(compile(body, SCRIPT, 'exec'), ns)
    return ns


# ── Synthetic cloud builders ──────────────────────────────────────────────────

def _grid(lo, hi, step):
    """Inclusive sample positions from lo to hi."""
    out, v = [], lo
    n = int(round((hi - lo) / step))
    for i in range(n + 1):
        out.append(lo + i * step)
    return out


def synth_room(width_m=8.0, depth_m=5.0, height_m=3.0, thick_mm=200.0,
               step_mm=60.0,
               door=(3.0, 3.9, 0.0, 2.1),      # wall y=0: x0,x1,z0,z1
               window=(2.0, 3.2, 0.9, 2.4),    # wall x=0: y0,y1,z0,z1
               extras=None):
    """
    A rectangular room scanned from the inside, in metres, returned as Revit
    internal feet. Wall faces are sampled on both sides so thickness reads
    correctly; the door and window rectangles are left empty on their wall.
    """
    s   = step_mm / 1000.0
    t   = thick_mm / 1000.0
    W, D, H = width_m, depth_m, height_m
    pts = []

    xs_in = _grid(0.0, W, s)
    ys_in = _grid(0.0, D, s)
    zs    = _grid(0.0, H, s)

    def blocked_door(x, z):
        if door is None:
            return False
        x0, x1, z0, z1 = door
        return x0 <= x <= x1 and z0 <= z <= z1

    def blocked_window(y, z):
        if window is None:
            return False
        y0, y1, z0, z1 = window
        return y0 <= y <= y1 and z0 <= z <= z1

    # Walls y=0 and y=D (inner + outer face)
    for x in xs_in:
        for z in zs:
            if not blocked_door(x, z):
                pts.append((x, 0.0, z))
                pts.append((x, -t, z))
            pts.append((x, D, z))
            pts.append((x, D + t, z))

    # Walls x=0 and x=W (inner + outer face)
    for y in ys_in:
        for z in zs:
            if not blocked_window(y, z):
                pts.append((0.0, y, z))
                pts.append((-t, y, z))
            pts.append((W, y, z))
            pts.append((W + t, y, z))

    # Floor + ceiling slabs
    for x in _grid(0.0, W, s):
        for y in _grid(0.0, D, s):
            pts.append((x, y, 0.0))
            pts.append((x, y, H))

    if extras:
        pts.extend(extras)

    return [(x * M, y * M, z * M) for (x, y, z) in pts]


def box_points(x0, x1, y0, y1, z0, z1, step_mm=60.0):
    """Solid-ish box shell in metres — used for furniture / MEP decoys."""
    s = step_mm / 1000.0
    out = []
    for x in _grid(x0, x1, s):
        for y in _grid(y0, y1, s):
            out.append((x, y, z1))          # top face
    for x in _grid(x0, x1, s):
        for z in _grid(z0, z1, s):
            out.append((x, y0, z))
            out.append((x, y1, z))
    for y in _grid(y0, y1, s):
        for z in _grid(z0, z1, s):
            out.append((x0, y, z))
            out.append((x1, y, z))
    return out


# ── Test harness ──────────────────────────────────────────────────────────────

FAILURES = []
PASSES   = []


def check(name, cond, detail=''):
    if cond:
        PASSES.append(name)
        print('  PASS  {}'.format(name))
    else:
        FAILURES.append((name, detail))
        print('  FAIL  {}  {}'.format(name, detail))


def counts_by_type(elements):
    out = {}
    for e in elements:
        out[e.Type] = out.get(e.Type, 0) + 1
    return out


SETTINGS = {
    'detect_wall': True, 'snap_tol': 1.0, 'wall_min_len': 500.0,
    'detect_floor': True, 'floor_zbin': 50.0, 'floor_min_area': 1.0,
    'detect_ceiling': True, 'ceil_zbin': 50.0,
    'detect_door': True, 'door_min_w': 600.0, 'door_min_h': 1800.0,
    'detect_window': True, 'win_min_w': 400.0, 'win_min_h': 400.0,
    'detect_column': True, 'col_min': 200.0, 'col_max': 600.0,
    'detect_stair': True, 'stair_riser': 165.0,
    'detect_roof': True, 'roof_slope': 5.0,
}


def run():
    ns = load_analyzer_module()
    Analyzer = ns['PointCloudAnalyzer']

    # ── Case 1: bare room shell ───────────────────────────────────────────────
    print('\nCase 1 — empty 8x5x3 m room, door 900x2100, window 1200x1500')
    pts = synth_room()
    res = Analyzer(pts).run(dict(SETTINGS))
    c = counts_by_type(res)
    print('    detected: {}'.format(c))
    check('C1 four walls', c.get('Wall', 0) == 4,
          'got {}'.format(c.get('Wall', 0)))
    check('C1 one floor', c.get('Floor', 0) >= 1,
          'got {}'.format(c.get('Floor', 0)))
    check('C1 one ceiling', c.get('Ceiling', 0) >= 1,
          'got {}'.format(c.get('Ceiling', 0)))
    check('C1 door found', c.get('Door', 0) >= 1,
          'got {}'.format(c.get('Door', 0)))
    check('C1 window found', c.get('Window', 0) >= 1,
          'got {}'.format(c.get('Window', 0)))
    check('C1 no phantom columns', c.get('Column', 0) == 0,
          'got {}'.format(c.get('Column', 0)))
    check('C1 no phantom roof', c.get('Roof', 0) == 0,
          'got {}'.format(c.get('Roof', 0)))

    # ── Case 2: wall geometry accuracy ────────────────────────────────────────
    print('\nCase 2 — wall dimensions within tolerance')
    walls = [e for e in res if e.Type == 'Wall']
    if walls:
        lens = sorted(ft_to_m(w._data['length_ft']) for w in walls)
        thks = [w._data['thickness_ft'] * MM_PER_FOOT for w in walls]
        print('    lengths (m): {}'.format(
            ['{:.2f}'.format(v) for v in lens]))
        print('    thicknesses (mm): {}'.format(
            ['{:.0f}'.format(v) for v in thks]))
        check('C2 two ~5 m walls', sum(1 for v in lens if 4.3 <= v <= 5.6) == 2,
              'lengths {}'.format(['{:.2f}'.format(v) for v in lens]))
        check('C2 two ~8 m walls', sum(1 for v in lens if 7.3 <= v <= 8.6) == 2,
              'lengths {}'.format(['{:.2f}'.format(v) for v in lens]))
        check('C2 thickness plausible',
              all(100.0 <= v <= 500.0 for v in thks),
              'thicknesses {}'.format(['{:.0f}'.format(v) for v in thks]))
    else:
        check('C2 two ~5 m walls', False, 'no walls detected')
        check('C2 two ~8 m walls', False, 'no walls detected')
        check('C2 thickness plausible', False, 'no walls detected')

    # ── Case 3: anti-furniture — a 1.8 m shelf must not become a wall ─────────
    print('\nCase 3 — 4.0 x 0.6 m shelving unit, 1.8 m tall, mid-room')
    shelf = box_points(2.0, 6.0, 2.0, 2.6, 0.0, 1.8)
    pts3 = synth_room(extras=shelf)
    res3 = Analyzer(pts3).run(dict(SETTINGS))
    c3 = counts_by_type(res3)
    print('    detected: {}'.format(c3))
    check('C3 still exactly 4 walls', c3.get('Wall', 0) == 4,
          'got {} — shelf leaked in as a wall'.format(c3.get('Wall', 0)))
    check('C3 shelf is not a column', c3.get('Column', 0) == 0,
          'got {}'.format(c3.get('Column', 0)))

    # ── Case 4: anti-MEP — a 150 mm standpipe must not become a column ────────
    print('\nCase 4 — 150 mm vertical pipe, full height')
    pipe = box_points(6.0, 6.15, 3.5, 3.65, 0.0, 3.0)
    pts4 = synth_room(extras=pipe)
    res4 = Analyzer(pts4).run(dict(SETTINGS))
    c4 = counts_by_type(res4)
    print('    detected: {}'.format(c4))
    check('C4 pipe rejected as column', c4.get('Column', 0) == 0,
          'got {} — 150 mm pipe leaked in'.format(c4.get('Column', 0)))

    # ── Case 5: a real 300 mm column must be found ────────────────────────────
    print('\nCase 5 — real 300 x 300 mm column, full height')
    col = box_points(4.0, 4.3, 2.5, 2.8, 0.0, 3.0)
    pts5 = synth_room(extras=col)
    res5 = Analyzer(pts5).run(dict(SETTINGS))
    c5 = counts_by_type(res5)
    print('    detected: {}'.format(c5))
    check('C5 column detected', c5.get('Column', 0) >= 1,
          'got {} — real column missed'.format(c5.get('Column', 0)))
    check('C5 walls unaffected', c5.get('Wall', 0) == 4,
          'got {}'.format(c5.get('Wall', 0)))

    # ── Case 6: anti-furniture — a large table must not become a floor ────────
    print('\nCase 6 — 4.0 x 1.5 m table top at 750 mm')
    table = box_points(2.0, 6.0, 1.5, 3.0, 0.72, 0.75)
    pts6 = synth_room(extras=table)
    res6 = Analyzer(pts6).run(dict(SETTINGS))
    c6 = counts_by_type(res6)
    print('    detected: {}'.format(c6))
    check('C6 no phantom slab from table', c6.get('Floor', 0) <= 1,
          'got {} floors — table read as a slab'.format(c6.get('Floor', 0)))

    # ── Case 7: no-door room must report no door ──────────────────────────────
    print('\nCase 7 — sealed room, no openings at all')
    pts7 = synth_room(door=None, window=None)
    res7 = Analyzer(pts7).run(dict(SETTINGS))
    c7 = counts_by_type(res7)
    print('    detected: {}'.format(c7))
    check('C7 no phantom doors', c7.get('Door', 0) == 0,
          'got {}'.format(c7.get('Door', 0)))
    check('C7 no phantom windows', c7.get('Window', 0) == 0,
          'got {}'.format(c7.get('Window', 0)))
    check('C7 walls still 4', c7.get('Wall', 0) == 4,
          'got {}'.format(c7.get('Wall', 0)))

    # ── Case 8: degenerate inputs must not raise ──────────────────────────────
    print('\nCase 8 — degenerate inputs')
    for label, sample in [
            ('single point', [(0.0, 0.0, 0.0)]),
            ('collinear',    [(i * 0.1, 0.0, 0.0) for i in range(50)]),
            ('coincident',   [(1.0, 1.0, 1.0)] * 200)]:
        try:
            Analyzer(sample).run(dict(SETTINGS))
            check('C8 {} survives'.format(label), True)
        except Exception as ex:
            check('C8 {} survives'.format(label), False, repr(ex))

    # ── Case 9: unit helper round-trip ────────────────────────────────────────
    print('\nCase 9 — unit helpers')
    ft_to_mm, mm_to_ft = ns['ft_to_mm'], ns['mm_to_ft']
    check('C9 mm->ft->mm round trip',
          abs(ft_to_mm(mm_to_ft(2400.0)) - 2400.0) < 1e-6)
    check('C9 one foot is 304.8 mm', abs(ft_to_mm(1.0) - 304.8) < 1e-9)


def ft_to_m(ft):
    return ft * MM_PER_FOOT / 1000.0


if __name__ == '__main__':
    quiet = '--quiet' in sys.argv
    run()
    print('\n{} passed, {} failed'.format(len(PASSES), len(FAILURES)))
    if FAILURES:
        print('\nFailures:')
        for name, detail in FAILURES:
            print('  - {}: {}'.format(name, detail))
        sys.exit(1)
    print('POINTCLOUD DETECT: clean')
