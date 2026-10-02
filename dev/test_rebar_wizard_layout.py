# -*- coding: utf-8 -*-
"""
Tests for the pure layout math of Snippets/_rebar_wizard.py (Rebar Wizard,
spec dev/plan/rebar-tekla-implementation-spec.md section 5.6 / 6).

The module is exec'd from the shipped source with Autodesk / System / clr /
pyrevit stubbed out (loader copied from dev/test_group_manager.py), so what
runs here is exactly what runs in Revit - minus the Revit-touching half.
Run: python dev/test_rebar_wizard_layout.py
"""
import os
import shutil
import sys
import tempfile
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


def _load_module(name='_rebar_wizard'):
    """Exec Snippets/<name>.py with the Revit and .NET imports stubbed out."""

    class _AnyMeta(type):
        """Any attribute lookup yields another permissive placeholder type."""
        def __getattr__(cls, item):
            return _make_any(item)

    def _make_any(item):
        return _AnyMeta(str(item), (), {})

    def _stub(mod_name):
        mod = types.ModuleType(mod_name)
        mod.__getattr__ = _make_any
        return mod

    names = ('Autodesk', 'Autodesk.Revit', 'Autodesk.Revit.DB', 'Autodesk.Revit.DB.Structure',
             'Autodesk.Revit.UI', 'Autodesk.Revit.UI.Selection', 'System', 'clr', 'pyrevit')
    stubs = dict((n, _stub(n)) for n in names)
    stubs['Autodesk'].Revit = stubs['Autodesk.Revit']
    stubs['Autodesk.Revit'].DB = stubs['Autodesk.Revit.DB']
    stubs['Autodesk.Revit'].UI = stubs['Autodesk.Revit.UI']
    stubs['Autodesk.Revit.DB'].Structure = stubs['Autodesk.Revit.DB.Structure']
    stubs['Autodesk.Revit.UI'].Selection = stubs['Autodesk.Revit.UI.Selection']
    saved = {}
    for n, mod in stubs.items():
        saved[n] = sys.modules.get(n)
        sys.modules[n] = mod
    try:
        path = os.path.join(LIB_DIR, 'Snippets', name + '.py')
        with open(path, 'r', encoding='utf-8') as handle:
            source = handle.read()
        module = types.ModuleType('t3_%s_under_test' % name.strip('_'))
        module.__file__ = path
        exec(compile(source, path, 'exec'), module.__dict__)
        return module
    finally:
        for n, previous in saved.items():
            if previous is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = previous


W = _load_module()


def _beam(b=300.0, h=600.0, length=6000.0):
    return W.Section(b=b, h=h, length=length, kind=W.KIND_BEAM)


def _gaps(values):
    values = sorted(values)
    return [round(b - a, 6) for a, b in zip(values[:-1], values[1:])]


def _bar_offsets(plan):
    """Absolute positions of every bar of a 'fixed' / 'single' set along its normal."""
    start = plan.points[0]
    axis = plan.normal.index(1.0)
    if plan.layout[0] == 'single':
        return [start[axis]]
    n, length = plan.layout[1], plan.layout[2]
    return [start[axis] + i * length / float(n - 1) for i in range(n)]


class TestBeam(unittest.TestCase):

    def setUp(self):
        self.inp = W.WizardInput(W.KIND_BEAM)       # 4 Ø20 bot, 2 Ø16 top, Ø8 stirrups, cover 25
        self.plans = W.beam_plan(_beam(), self.inp)
        self.by_role = {}
        for p in self.plans:
            self.by_role.setdefault(p.role, []).append(p)

    def test_beam_bottom_bars_spacing_and_cover(self):
        bottom = self.by_role['bottom'][0]
        self.assertEqual(bottom.bar_count, 4)
        self.assertEqual(bottom.layout[0], 'fixed')
        # z = -h/2 + cover + d_stir + d/2 = -300 + 25 + 8 + 10
        for p in bottom.points:
            self.assertAlmostEqual(p[2], -257.0)
        # spread over b - 2(cover + d_stir) - d = 300 - 66 - 20 = 214, centred
        ys = _bar_offsets(bottom)
        self.assertAlmostEqual(ys[0], -107.0)
        self.assertAlmostEqual(ys[-1], 107.0)
        gaps = _gaps(ys)
        self.assertTrue(all(abs(g - gaps[0]) < 1e-6 for g in gaps))
        # outer bar face sits exactly on the stirrup's inner face
        self.assertAlmostEqual(150.0 - (abs(ys[0]) + 10.0), 25.0 + 8.0)
        # main bars run the full length minus the cover at each end, no hooks
        self.assertAlmostEqual(bottom.points[0][0], 25.0)
        self.assertAlmostEqual(bottom.points[-1][0], 6000.0 - 25.0)
        self.assertIsNone(bottom.hook_start)
        top = self.by_role['top'][0]
        self.assertAlmostEqual(top.points[0][2], 300.0 - 25.0 - 8.0 - 8.0)
        self.assertEqual(top.bar_count, 2)

    def test_beam_single_bar_centered(self):
        inp = W.WizardInput(W.KIND_BEAM, bot_n=1, top_n=1)
        plans = W.beam_plan(_beam(), inp)
        for p in plans:
            if p.role in ('bottom', 'top'):
                self.assertEqual(p.layout, ('single',))
                self.assertEqual(p.bar_count, 1)
                self.assertAlmostEqual(p.points[0][1], 0.0)

    def test_beam_too_many_bars_raises_layout_error(self):
        inp = W.WizardInput(W.KIND_BEAM, bot_n=8)
        with self.assertRaises(W.LayoutError) as ctx:
            W.beam_plan(_beam(), inp)
        self.assertIn('do not fit', str(ctx.exception))

    def test_stirrup_points_closed_and_inside_cover(self):
        pts = W.stirrup_points(300.0, 600.0, 25.0, 8.0)
        self.assertEqual(len(pts), 5)
        self.assertEqual(pts[0], pts[-1])                       # closed
        for x, y, z in pts:
            self.assertEqual(x, 0.0)
            # centreline half a bar inside the cover: outer face == cover
            self.assertAlmostEqual(150.0 - abs(y) - 4.0, 25.0)
            self.assertAlmostEqual(300.0 - abs(z) - 4.0, 25.0)
        # 4 distinct corners, every leg axis-aligned
        self.assertEqual(len(set(pts[:4])), 4)
        for a, b in zip(pts[:-1], pts[1:]):
            self.assertTrue(a[1] == b[1] or a[2] == b[2])
        with self.assertRaises(W.LayoutError):
            W.stirrup_points(60.0, 600.0, 25.0, 12.0)

    def test_stirrups_three_zones(self):
        stirrups = self.by_role['stirrup']
        self.assertEqual([p.layout[1] for p in stirrups], [100.0, 150.0, 100.0])
        self.assertEqual([p.layout[2] for p in stirrups], [600.0, 6000.0 - 50.0 - 1200.0, 600.0])
        # zones are contiguous and start at the cover
        self.assertAlmostEqual(stirrups[0].points[0][0], 25.0)
        self.assertAlmostEqual(stirrups[1].points[0][0], 625.0)
        self.assertAlmostEqual(stirrups[2].points[0][0], 6000.0 - 25.0 - 600.0)
        # the middle zone drops its end bars so nothing is placed twice
        self.assertEqual((stirrups[1].include_first, stirrups[1].include_last), (False, False))
        self.assertEqual(stirrups[0].bar_count, 7)                # 600 / 100 + 1
        for p in stirrups:
            self.assertEqual(p.style, W.STYLE_STIRRUP)
            self.assertEqual(p.normal, (1.0, 0.0, 0.0))

    def test_zone_lengths_short_beam_single_zone(self):
        self.assertEqual(W.zone_lengths(1000.0, 600.0), (1000.0, 0.0, 0.0))
        self.assertEqual(W.zone_lengths(1300.0, 600.0), (1300.0, 0.0, 0.0))   # mid 100 < 200
        self.assertEqual(W.zone_lengths(6000.0, 600.0), (600.0, 4800.0, 600.0))
        self.assertEqual(W.zone_lengths(6000.0, 0.0), (0.0, 6000.0, 0.0))
        plans = W.beam_plan(_beam(length=1000.0), self.inp)
        stirrups = [p for p in plans if p.role == 'stirrup']
        self.assertEqual(len(stirrups), 1)
        self.assertEqual(stirrups[0].layout[1], 100.0)          # dense spacing all along
        self.assertTrue(stirrups[0].include_first and stirrups[0].include_last)


class TestColumn(unittest.TestCase):

    def test_column_corner_plus_side_bars(self):
        section = W.Section(b=400.0, h=400.0, length=3000.0, kind=W.KIND_COLUMN)
        inp = W.WizardInput(W.KIND_COLUMN, side_n=1)
        plans = W.column_plan(section, inp)
        verticals = [p for p in plans if p.role == 'vertical']
        self.assertEqual(W.total_bars(verticals), 4 + 4 * 1)
        # every vertical runs bottom cover -> top cover along X (up)
        for p in verticals:
            self.assertAlmostEqual(p.points[0][0], 40.0)
            self.assertAlmostEqual(p.points[-1][0], 3000.0 - 40.0)
        # corners at ±(b/2 - cover - d_tie - d/2) = ±142
        coords = set()
        for p in verticals:
            axis = p.normal.index(1.0)
            n, length = p.layout[1], p.layout[2]
            for i in range(n):
                if (i == 0 and not p.include_first) or (i == n - 1 and not p.include_last):
                    continue
                pt = list(p.points[0])
                pt[axis] += i * length / float(n - 1)
                coords.add((round(pt[1], 6), round(pt[2], 6)))
        self.assertEqual(len(coords), 8)                         # no bar placed twice
        for corner in ((-142.0, -142.0), (142.0, -142.0), (-142.0, 142.0), (142.0, 142.0)):
            self.assertIn(corner, coords)
        ties = [p for p in plans if p.role == 'tie']
        self.assertEqual([p.layout[1] for p in ties], [100.0, 200.0, 100.0])   # dense top/bottom
        cross = [p for p in plans if p.role == 'cross_tie']
        self.assertEqual(len(set(p.group for p in cross)), 2)    # one line each way
        no_cross = W.column_plan(section, W.WizardInput(W.KIND_COLUMN, side_n=1, cross_tie=False))
        self.assertFalse([p for p in no_cross if p.role == 'cross_tie'])

    def test_column_corners_only(self):
        section = W.Section(b=300.0, h=300.0, length=3000.0, kind=W.KIND_COLUMN)
        plans = W.column_plan(section, W.WizardInput(W.KIND_COLUMN, side_n=0))
        verticals = [p for p in plans if p.role == 'vertical']
        self.assertEqual(len(verticals), 2)
        self.assertEqual(W.total_bars(verticals), 4)
        self.assertFalse([p for p in plans if p.role == 'cross_tie'])


class TestFooting(unittest.TestCase):

    def test_footing_mesh_counts(self):
        # 1800 (X) × 1200 (Y) × 500 thick, Ø16 @150 both ways, cover 50
        section = W.Section(b=1200.0, h=500.0, length=1800.0, kind=W.KIND_FOOTING)
        plans = W.footing_plan(section, W.WizardInput(W.KIND_FOOTING))
        x_bars = [p for p in plans if p.role == 'mesh_x'][0]
        y_bars = [p for p in plans if p.role == 'mesh_y'][0]
        # X bars distributed across Y: 1200 - 2*50 - 16 = 1084 -> ceil(1084/150) + 1 = 9
        self.assertAlmostEqual(x_bars.layout[2], 1084.0)
        self.assertEqual(x_bars.bar_count, 9)
        # Y bars distributed across X: 1800 - 100 - 16 = 1684 -> 13
        self.assertEqual(y_bars.bar_count, 13)
        # Y layer at the bottom, X layer on top of it
        self.assertAlmostEqual(y_bars.points[0][2], 50.0 + 8.0)
        self.assertAlmostEqual(x_bars.points[0][2], 50.0 + 16.0 + 8.0)
        # straight bars inside the side cover
        self.assertEqual(len(x_bars.points), 2)
        self.assertAlmostEqual(x_bars.points[0][0], -(900.0 - 50.0 - 8.0))
        self.assertEqual(x_bars.normal, (0.0, 1.0, 0.0))
        self.assertEqual(y_bars.normal, (1.0, 0.0, 0.0))

    def test_footing_hooks_bend_up_inside_top_cover(self):
        section = W.Section(b=1200.0, h=500.0, length=1800.0, kind=W.KIND_FOOTING)
        plans = W.footing_plan(section, W.WizardInput(W.KIND_FOOTING, hooks=True))
        for p in plans:
            self.assertEqual(len(p.points), 4)
            top = max(pt[2] for pt in p.points)
            self.assertLessEqual(top, 500.0 - 50.0 - 8.0 + 1e-6)
            self.assertAlmostEqual(top - p.points[1][2], min(12 * 16.0, 500 - 50 - 8 - p.points[1][2]))


class TestInputAndSummary(unittest.TestCase):

    def test_validate_rejects_negative_and_zero(self):
        self.assertEqual(W.WizardInput(W.KIND_BEAM).validate(), [])
        inp = W.WizardInput(W.KIND_BEAM, s_end='0', cover='-5', bot_n='0', top_dia='abc')
        problems = inp.validate()
        self.assertTrue(any('End zone spacing' in p and 'greater than 0' in p for p in problems))
        self.assertTrue(any(p.startswith('Cover') for p in problems))
        self.assertTrue(any('Bottom bar count' in p for p in problems))
        self.assertTrue(any('Top bar diameter must be a number' in p for p in problems))
        col = W.WizardInput(W.KIND_COLUMN, side_n='-1', l_dense='0')
        self.assertEqual([p for p in col.validate()], ['Extra bars per face cannot be negative.'])
        self.assertTrue(W.WizardInput(W.KIND_COLUMN, side_n='1.5').validate())
        for problem in problems:
            problem.encode('ascii')             # English, no Vietnamese diacritics

    def test_summarize_english_counts(self):
        line = W.summarize(W.beam_plan(_beam(), W.WizardInput(W.KIND_BEAM)))
        self.assertTrue(line.startswith(u'4 × Ø20 bottom · 2 × Ø16 top · stirrups Ø8 @100 (2×600) / @150'))
        self.assertTrue(line.endswith(u'— 5 sets'))
        short = W.summarize(W.beam_plan(_beam(length=1000.0), W.WizardInput(W.KIND_BEAM)))
        self.assertIn(u'stirrups Ø8 @100 —', short)
        self.assertEqual(W.summarize([]), u'No bars.')

    def test_preset_roundtrip_tmpfile(self):
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, 'sub', W.PRESET_FILE)
            self.assertEqual(W.load_presets(path), {})          # missing file
            data = W.default_preset()
            data[W.KIND_BEAM]['bot_n'] = 6
            data[W.KIND_COLUMN]['hook'] = u'Stirrup/Tie - 135 deg.'
            self.assertIsNone(W.save_presets(path, {u'Heavy': data}))
            loaded = W.load_presets(path)
            self.assertEqual(list(loaded), [u'Heavy'])
            self.assertEqual(loaded[u'Heavy'][W.KIND_BEAM]['bot_n'], 6)
            self.assertEqual(loaded[u'Heavy'], data)
            inp = W.WizardInput.from_dict(W.KIND_COLUMN, loaded[u'Heavy'][W.KIND_COLUMN])
            self.assertEqual(inp.hook, u'Stirrup/Tie - 135 deg.')
            load, save = W.preset_io(path)
            self.assertEqual(load(), loaded)
            with open(path, 'w') as handle:
                handle.write('{not json')
            self.assertEqual(W.load_presets(path), {})          # corrupt -> empty, no crash
            self.assertIsNone(save({}))
        finally:
            shutil.rmtree(folder)

    def test_plan_for_rejects_wrong_kind(self):
        with self.assertRaises(W.LayoutError):
            W.plan_for(_beam(), W.WizardInput(W.KIND_COLUMN))

    def test_section_label(self):
        self.assertEqual(_beam().label(), u'300×600')
        self.assertEqual(W.Section(b=1200, h=500, length=1800, kind=W.KIND_FOOTING).label(),
                         u'1800×1200×500')
        self.assertFalse(W.out_of_scope(W.OOS_CURVED, W.KIND_BEAM).in_scope)

    def test_frame_to_model(self):
        frame = W.Frame((1.0, 2.0, 3.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        x, y, z = frame.to_model((304.8, 0.0, 609.6))
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, 3.0)
        self.assertAlmostEqual(z, 5.0)
        self.assertEqual(frame.vector_to_model((0.0, 1.0, 0.0)), (-1.0, 0.0, 0.0))


if __name__ == '__main__':
    unittest.main()
