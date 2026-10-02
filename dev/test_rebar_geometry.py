# -*- coding: utf-8 -*-
"""
Tests for the Revit-free geometry in Snippets/_rebar.py: bar centre-line ->
legs, outer (BVBS) dimensions, curve classification and fingerprint matching.
Run: python dev/test_rebar_geometry.py
"""
import os
import sys
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


def _load_module(name):
    """Exec Snippets/<name>.py with the Revit and .NET imports stubbed out.

    Tests the shipped source rather than a copy; same technique as
    dev/test_group_manager.py.
    """
    class _AnyMeta(type):
        def __getattr__(cls, item):
            return _make_any(item)

    def _make_any(item):
        return _AnyMeta(str(item), (), {})

    def _stub(stub_name):
        mod = types.ModuleType(stub_name)
        mod.__getattr__ = _make_any
        return mod

    saved = {}
    stubs = {
        'Autodesk': _stub('Autodesk'),
        'Autodesk.Revit': _stub('Autodesk.Revit'),
        'Autodesk.Revit.DB': _stub('Autodesk.Revit.DB'),
        'System': _stub('System'),
    }
    stubs['Autodesk'].Revit = stubs['Autodesk.Revit']
    stubs['Autodesk.Revit'].DB = stubs['Autodesk.Revit.DB']
    for stub_name, mod in stubs.items():
        saved[stub_name] = sys.modules.get(stub_name)
        sys.modules[stub_name] = mod
    try:
        path = os.path.join(LIB_DIR, 'Snippets', name + '.py')
        with open(path, 'r', encoding='utf-8') as handle:
            source = handle.read()
        module = types.ModuleType('t3_%s_under_test' % name)
        module.__file__ = path
        exec(compile(source, path, 'exec'), module.__dict__)
        return module
    finally:
        for stub_name, previous in saved.items():
            if previous is None:
                sys.modules.pop(stub_name, None)
            else:
                sys.modules[stub_name] = previous


class _Bar(object):
    """Stand-in for a RebarRecord (fingerprint only reads these four fields)."""

    def __init__(self, kind="Rebar", shape="L", bar_type="Ø12", quantity=1):
        self.kind = kind
        self.shape = shape
        self.bar_type = bar_type
        self.quantity = quantity


def _lengths(legs):
    return [length for length, _ in legs]


def _angles(legs):
    return [angle for _, angle in legs]


class TestCenterlineToLegs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rebar = _load_module('_rebar')

    def test_module_has_no_revit_import_at_load(self):
        """Pure parts must import without Autodesk: loading with no stubs works."""
        import importlib
        for name in ('Autodesk', 'Autodesk.Revit', 'Autodesk.Revit.DB'):
            self.assertNotIn(name, sys.modules)
        importlib.import_module('Snippets._rebar')

    def test_straight_bar_single_leg(self):
        chain = self.rebar.centerline_to_legs([(0, 0, 0), (1000, 0, 0)])
        self.assertTrue(chain.planar)
        self.assertEqual(chain.legs, [(1000.0, 0.0)])

    def test_L_bar_90(self):
        chain = self.rebar.centerline_to_legs([(0, 0, 0), (400, 0, 0), (400, 600, 0)])
        self.assertTrue(chain.planar)
        self.assertEqual(chain.legs, [(400.0, 90.0), (600.0, 0.0)])

    def test_U_bar_signs(self):
        # U: two turns the same way -> both positive (first bend always +).
        u_bar = self.rebar.centerline_to_legs(
            [(0, 0, 0), (400, 0, 0), (400, 600, 0), (0, 600, 0)])
        self.assertEqual(_angles(u_bar.legs), [90.0, 90.0, 0.0])
        # Z / S shape: second turn goes the other way -> negative.
        z_bar = self.rebar.centerline_to_legs(
            [(0, 0, 0), (400, 0, 0), (400, 600, 0), (800, 600, 0)])
        self.assertEqual(_angles(z_bar.legs), [90.0, -90.0, 0.0])

    def test_sign_flips_with_mirror_image(self):
        mirrored = self.rebar.centerline_to_legs(
            [(0, 0, 0), (400, 0, 0), (400, -600, 0), (800, -600, 0)])
        self.assertEqual(_angles(mirrored.legs), [90.0, -90.0, 0.0])

    def test_collinear_points_merged(self):
        chain = self.rebar.centerline_to_legs(
            [(0, 0, 0), (200, 0, 0), (400, 0, 0), (400, 300, 0), (400, 600, 0)])
        self.assertEqual(chain.legs, [(400.0, 90.0), (600.0, 0.0)])

    def test_duplicate_points_ignored(self):
        chain = self.rebar.centerline_to_legs([(0, 0, 0), (0, 0, 0), (500, 0, 0)])
        self.assertEqual(chain.legs, [(500.0, 0.0)])

    def test_180_hook(self):
        chain = self.rebar.centerline_to_legs([(0, 0, 0), (400, 0, 0), (300, 0, 0)])
        self.assertTrue(chain.planar)
        self.assertEqual(chain.legs, [(400.0, 180.0), (100.0, 0.0)])

    def test_180_hook_then_bend_keeps_plane(self):
        chain = self.rebar.centerline_to_legs(
            [(0, 0, 0), (400, 0, 0), (300, 0, 0), (300, 100, 0)])
        self.assertTrue(chain.planar)
        self.assertEqual(_angles(chain.legs), [180.0, 90.0, 0.0])
        self.assertEqual(_lengths(chain.legs), [400.0, 100.0, 100.0])

    def test_45_degree_bend(self):
        chain = self.rebar.centerline_to_legs([(0, 0, 0), (300, 0, 0), (500, 200, 0)])
        self.assertAlmostEqual(chain.legs[0][1], 45.0, places=6)

    def test_non_planar_rejected(self):
        chain = self.rebar.centerline_to_legs(
            [(0, 0, 0), (400, 0, 0), (400, 600, 0), (400, 600, 300)])
        self.assertFalse(chain.planar)
        self.assertEqual(chain.legs, [])
        self.assertIn('plane', chain.reason)

    def test_planar_tolerance_allows_small_deviation(self):
        chain = self.rebar.centerline_to_legs(
            [(0, 0, 0), (400, 0, 0), (400, 600, 0.4)], planar_tol_mm=1.0)
        self.assertTrue(chain.planar)

    def test_single_point_is_not_a_bar(self):
        chain = self.rebar.centerline_to_legs([(5, 5, 5), (5, 5, 5)])
        self.assertFalse(chain.planar)

    def test_chain_works_in_a_vertical_plane(self):
        chain = self.rebar.centerline_to_legs([(0, 0, 0), (0, 0, 400), (0, 600, 400)])
        self.assertTrue(chain.planar)
        self.assertEqual(_angles(chain.legs), [90.0, 0.0])


class TestOuterLegs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rebar = _load_module('_rebar')

    def test_outer_legs_90_adds_half_d_per_bend(self):
        out = self.rebar.outer_legs([(400.0, 90.0), (600.0, 0.0)], 12.0)
        self.assertEqual([round(l, 6) for l in _lengths(out)], [406.0, 606.0])
        self.assertEqual(_angles(out), [90.0, 0.0])

    def test_outer_legs_two_bends_add_to_the_middle_leg(self):
        out = self.rebar.outer_legs([(400.0, 90.0), (600.0, 90.0), (400.0, 0.0)], 12.0)
        self.assertEqual([round(l, 6) for l in _lengths(out)], [406.0, 612.0, 406.0])

    def test_outer_legs_45(self):
        import math
        out = self.rebar.outer_legs([(400.0, 45.0), (500.0, 0.0)], 12.0)
        grow = 6.0 * math.tan(math.radians(22.5))
        self.assertAlmostEqual(out[0][0], 400.0 + grow, places=5)
        self.assertAlmostEqual(out[1][0], 500.0 + grow, places=5)

    def test_outer_legs_hook_caps_at_half_d(self):
        out = self.rebar.outer_legs([(400.0, 180.0), (100.0, 0.0)], 12.0)
        self.assertEqual([round(l, 6) for l in _lengths(out)], [406.0, 106.0])

    def test_outer_legs_negative_angle_uses_magnitude(self):
        out = self.rebar.outer_legs([(400.0, -90.0), (600.0, 0.0)], 12.0)
        self.assertEqual([round(l, 6) for l in _lengths(out)], [406.0, 606.0])

    def test_outer_legs_straight_bar_unchanged(self):
        out = self.rebar.outer_legs([(1000.0, 0.0)], 20.0)
        self.assertEqual(out, [(1000.0, 0.0)])

    def test_legs_to_bvbs_rounding(self):
        segments = self.rebar.legs_to_bvbs_segments([(406.4, 89.96), (605.6, 33.0)])
        # Last angle is forced to 0; lengths round to whole mm; angles to 0.1.
        self.assertEqual(segments, [(406, 90.0), (606, 0.0)])
        self.assertIsInstance(segments[0][0], int)

    def test_legs_to_bvbs_keeps_sign_and_decimals(self):
        segments = self.rebar.legs_to_bvbs_segments(
            [(100.0, 45.04), (424.0, -45.04), (300.0, 0.0)])
        self.assertEqual(segments, [(100, 45.0), (424, -45.0), (300, 0.0)])

    def test_legs_to_bvbs_no_negative_zero(self):
        segments = self.rebar.legs_to_bvbs_segments([(100.0, -0.04), (50.0, 0.0)])
        self.assertEqual(str(segments[0][1]), '0.0')

    def test_pipeline_matches_bvbs_guideline_example_1(self):
        """Guideline example 1: l400 w90 l600 (outer dimensions, d=12)."""
        # Centre-line legs are d/2 shorter at each bend: 394 and 594 -> outer 400, 600.
        outer = self.rebar.outer_legs([(394.0, 90.0), (594.0, 0.0)], 12.0)
        self.assertEqual(self.rebar.legs_to_bvbs_segments(outer), [(400, 90.0), (600, 0.0)])


class TestClassifyAndPolyline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rebar = _load_module('_rebar')

    def test_classify_centerline_arcs(self):
        lines = [('Line', (0, 0, 0), (400, 0, 0)), ('Line', (400, 0, 0), (400, 600, 0))]
        self.assertEqual(self.rebar.classify_centerline(lines), 'planar_lines')
        with_arc = lines + [('Arc', (400, 600, 0), (500, 700, 0), (470, 630, 0))]
        self.assertEqual(self.rebar.classify_centerline(with_arc), 'planar_with_arcs')

    def test_classify_centerline_non_planar(self):
        curves = [('Line', (0, 0, 0), (400, 0, 0)), ('Line', (400, 0, 0), (400, 600, 0)),
                  ('Line', (400, 600, 0), (400, 600, 300))]
        self.assertEqual(self.rebar.classify_centerline(curves), 'non_planar')

    def test_classify_collinear_is_planar(self):
        curves = [('Line', (0, 0, 0), (400, 0, 0)), ('Line', (400, 0, 0), (900, 0, 0))]
        self.assertEqual(self.rebar.classify_centerline(curves), 'planar_lines')

    def test_classify_empty_is_not_a_bar(self):
        self.assertEqual(self.rebar.classify_centerline([]), 'non_planar')

    def test_curves_to_polyline_flips_and_orders(self):
        curves = [('Line', (0, 0, 0), (400, 0, 0)),
                  ('Line', (400, 600, 0), (400, 0, 0)),      # reversed
                  ('Line', (400, 600, 0), (0, 600, 0))]
        points = self.rebar.curves_to_polyline(curves)
        self.assertEqual(points, [(0, 0, 0), (400, 0, 0), (400, 600, 0), (0, 600, 0)])

    def test_curves_to_polyline_disconnected_raises(self):
        curves = [('Line', (0, 0, 0), (400, 0, 0)), ('Line', (900, 900, 0), (950, 900, 0))]
        with self.assertRaises(ValueError):
            self.rebar.curves_to_polyline(curves)


class TestFingerprint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rebar = _load_module('_rebar')

    def test_fingerprint_tolerance_and_mirror(self):
        r = self.rebar
        bar = _Bar()
        same_a = r.fingerprint(bar, (100.0, 200.0, 300.0))
        same_b = r.fingerprint(bar, (103.0, 197.0, 302.0))     # inside the 10 mm cell
        far = r.fingerprint(bar, (130.0, 200.0, 300.0))
        self.assertEqual(same_a, same_b)
        self.assertNotEqual(same_a, far)
        self.assertEqual(same_a[:4], ('Rebar', 'L', 'Ø12', 1))
        # a different bar type is a different fingerprint at the same place
        self.assertNotEqual(same_a, r.fingerprint(_Bar(bar_type='Ø16'), (100.0, 200.0, 300.0)))

    def test_match_exact(self):
        r = self.rebar
        src = [(_Bar(), (0.0, 0.0, 0.0)), (_Bar(), (100.0, 0.0, 0.0))]
        dst = [(_Bar(), (100.0, 0.0, 0.0)), (_Bar(), (0.0, 0.0, 0.0))]
        pairs, unmatched, ambiguous = r.match_by_fingerprint(src, dst)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(unmatched, [])
        self.assertEqual(ambiguous, [])
        for s, d in pairs:
            self.assertEqual(s[1], d[1])

    def test_match_nearest_within_double_tolerance(self):
        r = self.rebar
        src = [(_Bar(), (0.0, 0.0, 0.0))]
        dst = [(_Bar(), (14.0, 0.0, 0.0))]                     # other cell, within 2*tol
        pairs, unmatched, _ = r.match_by_fingerprint(src, dst, tol_mm=10.0)
        self.assertEqual(len(pairs), 1)
        far_dst = [(_Bar(), (25.0, 0.0, 0.0))]
        pairs, unmatched, _ = r.match_by_fingerprint(src, far_dst, tol_mm=10.0)
        self.assertEqual(pairs, [])
        self.assertEqual(len(unmatched), 1)

    def test_match_mirror_in_x(self):
        r = self.rebar
        src = [(_Bar(), (150.0, 20.0, 0.0)), (_Bar(), (300.0, -40.0, 0.0))]
        dst = [(_Bar(), (-150.0, 20.0, 0.0)), (_Bar(), (-300.0, -40.0, 0.0))]
        pairs, unmatched, _ = r.match_by_fingerprint(src, dst, allow_mirror=True)
        self.assertEqual(len(pairs), 2)
        pairs, unmatched, _ = r.match_by_fingerprint(src, dst, allow_mirror=False)
        self.assertEqual(pairs, [])
        self.assertEqual(len(unmatched), 2)

    def test_match_mirror_in_y(self):
        r = self.rebar
        src = [(_Bar(), (50.0, 150.0, 0.0)), (_Bar(), (80.0, 400.0, 10.0))]
        dst = [(_Bar(), (50.0, -150.0, 0.0)), (_Bar(), (80.0, -400.0, 10.0))]
        pairs, unmatched, _ = r.match_by_fingerprint(src, dst)
        self.assertEqual(len(pairs), 2)

    def test_match_by_fingerprint_unmatched_reported(self):
        r = self.rebar
        src = [(_Bar(), (0.0, 0.0, 0.0)), (_Bar(shape='U'), (500.0, 0.0, 0.0)),
               (_Bar(quantity=5), (900.0, 0.0, 0.0))]
        dst = [(_Bar(), (0.0, 0.0, 0.0)), (_Bar(quantity=6), (900.0, 0.0, 0.0))]
        pairs, unmatched, ambiguous = r.match_by_fingerprint(src, dst, allow_mirror=False)
        self.assertEqual(len(pairs), 1)
        self.assertEqual([item[0].shape for item in unmatched], ['U', 'L'])
        self.assertEqual(ambiguous, [])

    def test_match_two_equally_near_candidates_are_ambiguous(self):
        r = self.rebar
        src = [(_Bar(), (0.0, 0.0, 0.0))]
        dst = [(_Bar(), (10.0, 0.0, 0.0)), (_Bar(), (-10.0, 0.0, 0.0))]
        pairs, unmatched, ambiguous = r.match_by_fingerprint(src, dst, allow_mirror=False)
        self.assertEqual(pairs, [])
        self.assertEqual(unmatched, [])
        self.assertEqual(len(ambiguous), 1)

    def test_match_each_destination_used_once(self):
        r = self.rebar
        src = [(_Bar(), (0.0, 0.0, 0.0)), (_Bar(), (3.0, 0.0, 0.0))]
        dst = [(_Bar(), (1.0, 0.0, 0.0))]
        pairs, unmatched, ambiguous = r.match_by_fingerprint(src, dst, allow_mirror=False)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(unmatched) + len(ambiguous), 1)

    def test_match_keeps_source_order(self):
        r = self.rebar
        src = [(_Bar(), (300.0, 0.0, 0.0)), (_Bar(), (0.0, 0.0, 0.0)), (_Bar(), (100.0, 0.0, 0.0))]
        dst = list(reversed(src))
        pairs, _, _ = r.match_by_fingerprint(src, dst)
        self.assertEqual([p[0][1][0] for p in pairs], [300.0, 0.0, 100.0])


if __name__ == '__main__':
    unittest.main()
