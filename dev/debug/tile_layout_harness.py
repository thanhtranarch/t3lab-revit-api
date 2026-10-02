# -*- coding: utf-8 -*-
"""
Tile Layout engine harness — runs under CPython, no Revit required.

    python3 dev/debug/tile_layout_harness.py

Asserts the CORRECT hand-computed numbers, so on the pre-fix engine the
failing tests double as the bug proof (see dev/plan/panel-2-modeling-datum.md,
"Phát sinh" 2026-07-13). All tests must pass after fixes B1–B4.

Test model (all in mm, converted to ft like the tool does):
    tile 600×600, joint 0, pattern grid, angle 0 unless stated.
"""
import os
import sys
import time

# TileLayoutCore.py sits in the same folder (lib/GUI) as this harness
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
sys.path.insert(0, os.path.join(LIB, 'GUI'))
sys.path.insert(0, LIB)

import math
try:
    from GUI.TileLayoutCore import (
        MM_TO_FT, V2, TileGrid, NestingEngine, OptionGenerator, LayoutOption,
        PATTERNS,
    )
except (ImportError, ValueError):
    from TileLayoutCore import (
        MM_TO_FT, V2, TileGrid, NestingEngine, OptionGenerator, LayoutOption,
        PATTERNS,
    )

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("  [{}] {}{}".format(status, name, ("  — " + detail) if detail else ""))
    if not cond:
        FAILURES.append(name)


def rect_mm(w_mm, h_mm):
    w, h = w_mm * MM_TO_FT, h_mm * MM_TO_FT
    return [V2(0, 0), V2(w, 0), V2(w, h), V2(0, h)]


class FloorStub(object):
    def __init__(self, pts):
        self.pts = pts


def run_engine(floor_mm_w, floor_mm_h, tile_mm=600.0, joint_mm=0.0,
               nesting=False, angle=0.0, dx=0.0, dy=0.0, pattern='grid'):
    tw = tile_mm * MM_TO_FT
    jw = joint_mm * MM_TO_FT
    floor = rect_mm(floor_mm_w, floor_mm_h)
    grid = TileGrid(tw, tw, jw, pattern, angle, dx, dy)
    tiles = grid.generate(floor)
    engine = NestingEngine(tw, tw, nesting, floor)
    pieces = engine.process(tiles)
    opt = LayoutOption('A', pieces, 'test', tw * tw, {
        'pattern': pattern, 'angle': angle, 'dx': dx, 'dy': dy,
        'tile_w': tw, 'tile_h': tw, 'joint': jw,
        'use_nesting': nesting, 'floor_pts': floor,
    })
    return opt


def sig(o):
    return (o.n_full, o.n_cut, o.n_reuse, o.tiles_to_buy,
            round(o.waste_pct, 1), o.n_thin_cuts)


# ─────────────────────────────────────────────────────────────────────────────
print("T1. Floor 3000x2400, tile 600 — exact fit, zero waste")
o = run_engine(3000, 2400)
check("n_full == 20", o.n_full == 20, "got {}".format(o.n_full))
check("n_cut == 0", o.n_cut == 0, "got {}".format(o.n_cut))
check("waste_pct == 0", abs(o.waste_pct) < 0.01, "got {:.2f}%".format(o.waste_pct))

# ─────────────────────────────────────────────────────────────────────────────
print("T2. Floor 3100x2400 — 4 cut tiles of 100x600; TRUE waste")
# Each cut tile: uses 100/600 of the tile, offcut = 500x600.
# tiles_to_buy = 20 full + 4 cut = 24
# waste_true = 4*(500*600) / (24*600*600) = 13.889 %
# (pre-fix engine reports 16.667 % because waste counts the WHOLE tile)
o = run_engine(3100, 2400)
check("n_full == 20", o.n_full == 20, "got {}".format(o.n_full))
check("n_cut == 4", o.n_cut == 4, "got {}".format(o.n_cut))
check("tiles_to_buy == 24", o.tiles_to_buy == 24, "got {}".format(o.tiles_to_buy))
check("waste_pct == 13.889 (true offcut area)",
      abs(o.waste_pct - 13.8889) < 0.05, "got {:.3f}%".format(o.waste_pct))

# ─────────────────────────────────────────────────────────────────────────────
print("T3. Floor 3100x2400 + nesting ON — legit reuse, correct accounting")
# piece 100x600 (0.06 m2) fits in another tile's 500x600 offcut.
# Greedy: piece1→tile2's offcut (tile1 not bought), piece3→tile4's offcut.
# n_reuse = 2, tiles_to_buy = 20 + 2 = 22
# waste = 2 * (500*600 - 100*600) / (22*600*600) = 6.061 %
o = run_engine(3100, 2400, nesting=True)
check("n_reuse == 2", o.n_reuse == 2, "got {}".format(o.n_reuse))
check("tiles_to_buy == 22", o.tiles_to_buy == 22, "got {}".format(o.tiles_to_buy))
check("waste_pct == 6.061 (remaining offcut after nest)",
      abs(o.waste_pct - 6.0606) < 0.05, "got {:.3f}%".format(o.waste_pct))

# ─────────────────────────────────────────────────────────────────────────────
print("T4. Floor 3310x2400 + nesting ON — reuse must be IMPOSSIBLE")
# piece 310x600 = 186000 mm2, offcut only 290x600 = 174000 mm2.
# Pre-fix condition (offcut >= 0.9*needed → 167400 <= 174000) wrongly nests.
o = run_engine(3310, 2400, nesting=True)
check("n_reuse == 0 (piece bigger than any offcut)",
      o.n_reuse == 0, "got {}".format(o.n_reuse))
check("tiles_to_buy == 24", o.tiles_to_buy == 24, "got {}".format(o.tiles_to_buy))
check("waste_pct == 8.056",
      abs(o.waste_pct - 8.0556) < 0.05, "got {:.3f}%".format(o.waste_pct))

# ─────────────────────────────────────────────────────────────────────────────
print("T5. OptionGenerator on 3000x2400 — returned options must be distinct")
tw = 600.0 * MM_TO_FT
gen = OptionGenerator(tw, tw, 0.0, False, top_n=4)
opts = gen.generate(FloorStub(rect_mm(3000, 2400)), 'grid', 0.0)
sigs = [sig(x) for x in opts]
check("no duplicate signatures in top-N",
      len(sigs) == len(set(sigs)),
      "sigs: {}".format(sigs))
check("best option is the zero-waste one",
      opts[0].waste_pct < 0.01, "got {:.2f}%".format(opts[0].waste_pct))

# ─────────────────────────────────────────────────────────────────────────────
print("T6. shift_screen — screen vector maps to grid-local dx/dy (angle 45)")
o = run_engine(3000, 2400, angle=45.0)
step = 60.0 * MM_TO_FT   # 60 mm screen-shift to the right
ok = hasattr(o, 'shift_screen')
check("LayoutOption.shift_screen exists", ok)
if ok:
    dx0 = o.gen_params.get('dx', 0.0)
    dy0 = o.gen_params.get('dy', 0.0)
    o.shift_screen(step, 0.0)
    ddx = o.gen_params['dx'] - dx0
    ddy = o.gen_params['dy'] - dy0
    # Rotating (ddx, ddy) by +45° must give back the screen vector (step, 0)
    a = math.radians(45.0)
    wx = ddx * math.cos(a) - ddy * math.sin(a)
    wy = ddx * math.sin(a) + ddy * math.cos(a)
    check("R(angle)·(ddx,ddy) == screen (step, 0)",
          abs(wx - step) < 1e-9 and abs(wy) < 1e-9,
          "world delta = ({:.6f}, {:.6f}) ft, expected ({:.6f}, 0)".format(
              wx, wy, step))

# ─────────────────────────────────────────────────────────────────────────────
print("T7. build_variant + matches_params — preserve chosen option on re-generate")
tw7 = 600.0 * MM_TO_FT
gen7 = OptionGenerator(tw7, tw7, 0.0, False, top_n=4)
floor7 = FloorStub(rect_mm(3100, 2400))
bv = gen7.build_variant(floor7, 'grid', 0.0, 0.0, 0.0)
check("build_variant reproduces T2 stats",
      bv.n_full == 20 and bv.n_cut == 4 and abs(bv.waste_pct - 13.8889) < 0.05,
      "got full={} cut={} waste={:.3f}%".format(bv.n_full, bv.n_cut, bv.waste_pct))
check("matches_params: identical params match",
      bv.matches_params(0.0, 0.0, 0.0))
check("matches_params: shift of one full period is the same grid",
      bv.matches_params(0.0, tw7, 0.0))
check("matches_params: different angle rejected",
      not bv.matches_params(5.0, 0.0, 0.0))
check("matches_params: half-tile shift rejected",
      not bv.matches_params(0.0, tw7 * 0.5, 0.0))

# ─────────────────────────────────────────────────────────────────────────────
print("T8. re-generate flow: grid@0 chosen -> switch to staggered@150")
# Mirrors _generate_concepts' preserve logic (user report 2026-07-13:
# 'chon lai staggered 150 -> options khong chay').
fi8 = FloorStub(rect_mm(3100, 2400))
gen8 = OptionGenerator(600.0 * MM_TO_FT, 600.0 * MM_TO_FT, 0.0, False, top_n=4)
opts1 = gen8.generate(fi8, 'grid', 0.0)
chosen1 = opts1[0]
prev8 = dict(chosen1.gen_params)

opts2 = gen8.generate(fi8, 'staggered', 150.0)
check("staggered@150 sweep produced options", len(opts2) > 0,
      "got {}".format(len(opts2)))
p_a = prev8.get('angle', 0.0) or 0.0
p_x = prev8.get('dx', 0.0) or 0.0
p_y = prev8.get('dy', 0.0) or 0.0
twin8 = None
for o in opts2:
    if o.matches_params(p_a, p_x, p_y):
        twin8 = o
        break
check("no twin at angle 0 inside the 150-deg sweep", twin8 is None)
restored8 = gen8.build_variant(fi8, 'staggered', p_a, p_x, p_y)
check("restored option rebuilt without error",
      restored8 is not None and len(restored8.pieces) > 0,
      "pieces={}".format(len(restored8.pieces) if restored8 else 0))
check("restored keeps sane stats",
      restored8.n_full > 0 and 0.0 <= restored8.waste_pct <= 100.0,
      "full={} waste={:.1f}%".format(restored8.n_full, restored8.waste_pct))

# ─────────────────────────────────────────────────────────────────────────────
print("T9. coverage invariant — every pattern must tile the floor EXACTLY")
# Sum of piece areas (full+cut+reuse) must equal the floor area for joint 0:
# a gap makes it smaller, an overlapping/double-emitted tile makes it bigger.
# Rectangular tile 600x300 exercises herringbone/vertical bond properly.
def coverage_dev(pattern, angle, dx_mm, dy_mm,
                 floor_mm=(3050, 2380), tile_mm=(600, 300)):
    tw = tile_mm[0] * MM_TO_FT
    th = tile_mm[1] * MM_TO_FT
    floor = rect_mm(*floor_mm)
    grid = TileGrid(tw, th, 0.0, pattern, angle,
                    dx_mm * MM_TO_FT, dy_mm * MM_TO_FT)
    tiles = grid.generate(floor)
    eng = NestingEngine(tw, th, False, floor)
    pieces = eng.process(tiles)
    covered = sum(p.area for p in pieces
                  if p.piece_type in ('full', 'cut', 'reuse'))
    fa = (floor_mm[0] * MM_TO_FT) * (floor_mm[1] * MM_TO_FT)
    return abs(covered - fa) / fa

for pat_key, _lbl in PATTERNS:
    worst = 0.0
    for ang in (0.0, 45.0, 150.0):
        for sdx, sdy in ((0.0, 0.0), (137.0, 89.0), (-950.0, 411.0)):
            dev = coverage_dev(pat_key, ang, sdx, sdy)
            if dev > worst:
                worst = dev
    check("{} covers floor at all angles/shifts".format(pat_key),
          worst < 1e-4, "worst rel. deviation = {:.2e}".format(worst))

# ─────────────────────────────────────────────────────────────────────────────
print("T10. new patterns through OptionGenerator — sane, distinct options")
gen10 = OptionGenerator(600.0 * MM_TO_FT, 300.0 * MM_TO_FT, 0.0, False, top_n=4)
fi10 = FloorStub(rect_mm(3050, 2380))
for pat_key in ('staggered3', 'staggered4', 'vstaggered',
                'herringbone', 'herringbone2', 'basketweave'):
    opts10 = gen10.generate(fi10, pat_key, 0.0)
    sigs10 = [sig(o) for o in opts10]
    ok10 = (len(opts10) > 0 and
            len(sigs10) == len(set(sigs10)) and
            all(o.n_full > 0 for o in opts10) and
            all(0.0 <= o.waste_pct <= 100.0 for o in opts10))
    check("{}: options valid & distinct".format(pat_key), ok10,
          "sigs: {}".format(sigs10))

# staggered parity must survive a manual shift across one row period
# (the old phase-wrap flipped odd/even rows at the boundary).
o10 = run_engine(3100, 2400, pattern='staggered')
sy10 = 600.0 * MM_TO_FT
before = sig(o10)
o10.regenerate(dy=(o10.gen_params.get('dy') or 0.0) + 2.0 * sy10)
check("staggered: +2 row periods is the identical layout",
      sig(o10) == before, "before {} after {}".format(before, sig(o10)))

# ─────────────────────────────────────────────────────────────────────────────
print("T11. basket weave with mismatched ratio — gaps allowed, overlap NEVER")
# tile 600x250: k = floor(600/250) = 2, 2x250 = 500 < 600 → remainder shows
# as gaps. Covered area must stay <= floor area (an overlap would exceed it).
dev_cov = None
tw11 = 600.0 * MM_TO_FT
th11 = 250.0 * MM_TO_FT
floor11 = rect_mm(3050, 2380)
grid11 = TileGrid(tw11, th11, 0.0, 'basketweave', 0.0, 0.0, 0.0)
eng11 = NestingEngine(tw11, th11, False, floor11)
p11 = eng11.process(grid11.generate(floor11))
cov11 = sum(p.area for p in p11 if p.piece_type in ('full', 'cut', 'reuse'))
fa11 = (3050.0 * MM_TO_FT) * (2380.0 * MM_TO_FT)
check("covered <= floor area (no overlaps)",
      cov11 <= fa11 * (1.0 + 1e-9),
      "covered/floor = {:.4f}".format(cov11 / fa11))
check("gaps are bounded (still a usable layout)",
      cov11 >= fa11 * 0.75, "covered/floor = {:.4f}".format(cov11 / fa11))

# ─────────────────────────────────────────────────────────────────────────────
print("T12. aligned anchors — floor OFF-origin, exact-fit tile must win")
# Floor 3000x2400 at offset (317, 253) mm; tile 600x300 fits exactly 5x8.
# The fraction sweep (origin-based phases) can never hit the 317mm offset;
# only the corner-aligned anchor produces the zero-waste layout.
def rect_mm_at(x0_mm, y0_mm, w_mm, h_mm):
    x0, y0 = x0_mm * MM_TO_FT, y0_mm * MM_TO_FT
    w, h = w_mm * MM_TO_FT, h_mm * MM_TO_FT
    return [V2(x0, y0), V2(x0 + w, y0), V2(x0 + w, y0 + h), V2(x0, y0 + h)]

gen12 = OptionGenerator(600.0 * MM_TO_FT, 300.0 * MM_TO_FT, 0.0, False, top_n=4)
opts12 = gen12.generate(FloorStub(rect_mm_at(317, 253, 3000, 2400)), 'grid', 0.0)
check("best option is the corner-anchored zero-waste layout",
      opts12 and opts12[0].waste_pct < 0.01 and opts12[0].n_cut == 0,
      "best: waste={:.2f}% cut={}".format(
          opts12[0].waste_pct if opts12 else -1,
          opts12[0].n_cut if opts12 else -1))

# ─────────────────────────────────────────────────────────────────────────────
print("T13. performance + fast-path equivalence")
t13 = time.time()
gen13 = OptionGenerator(150.0 * MM_TO_FT, 150.0 * MM_TO_FT, 0.0, False, top_n=4)
opts13 = gen13.generate(FloorStub(rect_mm(5000, 4000)), 'grid', 0.0)
dt13 = time.time() - t13
check("large floor (150mm tile, 20 m²) sweep completes fast",
      dt13 < 5.0 and len(opts13) > 0, "{:.2f}s, {} options".format(
          dt13, len(opts13)))

# Same floor once as a clean 4-pt rect (rect fast path) and once with a
# redundant collinear vertex (forces the Sutherland-Hodgman path) — the
# results must be identical.
w13 = 3100.0 * MM_TO_FT
h13 = 2400.0 * MM_TO_FT
floor_5pt = [V2(0, 0), V2(w13 * 0.5, 0), V2(w13, 0),
             V2(w13, h13), V2(0, h13)]
o_fast = gen7.build_variant(FloorStub(rect_mm(3100, 2400)), 'grid', 0.0, 0.0, 0.0)
o_slow = gen7.build_variant(FloorStub(floor_5pt), 'grid', 0.0, 0.0, 0.0)
check("fast path and polygon path give identical stats",
      sig(o_fast) == sig(o_slow),
      "fast {} vs slow {}".format(sig(o_fast), sig(o_slow)))

# ─────────────────────────────────────────────────────────────────────────────
print("")
if FAILURES:
    print("RESULT: {} test(s) FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("RESULT: all tests passed.")
