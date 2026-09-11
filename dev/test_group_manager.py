# -*- coding: utf-8 -*-
"""
Tests for Group Manager logic: the Revit-free naming helpers in
Snippets/_group_ops.py (rename rules, cleanup, name validation) and the
duplicate detection the Rename tab relies on.
Run: python dev/test_group_manager.py
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


def _load_group_ops_module():
    """Exec Snippets/_group_ops.py with the Revit and .NET imports stubbed out.

    The module is only importable inside Revit, so the Revit-free helpers are
    exercised against the real shipped source rather than a copy.
    """
    import types

    class _AnyMeta(type):
        """Any attribute lookup yields another permissive placeholder type."""
        def __getattr__(cls, item):
            return _make_any(item)

    def _make_any(name):
        return _AnyMeta(str(name), (), {})

    def _stub(name):
        mod = types.ModuleType(name)
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
    for name, mod in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    try:
        source_path = os.path.join(LIB_DIR, 'Snippets', '_group_ops.py')
        with open(source_path, 'r', encoding='utf-8') as handle:
            source = handle.read()
        module = types.ModuleType('t3_group_ops_under_test')
        module.__file__ = source_path
        exec(compile(source, source_path, 'exec'), module.__dict__)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class _FakeRecord(object):
    def __init__(self, name):
        self.name = name


class TestRenameRules(unittest.TestCase):
    """The rename rules that drive the NEW NAME preview column."""

    @classmethod
    def setUpClass(cls):
        cls.ops = _load_group_ops_module()

    def test_no_rule_keeps_the_name(self):
        self.assertEqual(self.ops.build_new_name("Bathroom Pod"), "Bathroom Pod")

    def test_find_replace_is_case_insensitive_by_default(self):
        self.assertEqual(
            self.ops.build_new_name("BATHROOM Pod", find="bathroom", replace="Bath"),
            "Bath Pod")

    def test_find_replace_honours_match_case(self):
        self.assertEqual(
            self.ops.build_new_name("BATHROOM Pod", find="bathroom", replace="Bath",
                                    match_case=True),
            "BATHROOM Pod")

    def test_find_replace_hits_every_occurrence(self):
        self.assertEqual(
            self.ops.build_new_name("AR-AR-AR", find="AR", replace="ST"),
            "ST-ST-ST")

    def test_find_with_empty_replace_deletes(self):
        self.assertEqual(
            self.ops.build_new_name("OLD_Bathroom", find="OLD_"), "Bathroom")

    def test_prefix_and_suffix(self):
        self.assertEqual(
            self.ops.build_new_name("Pod", prefix="AR-", suffix="-L06"),
            "AR-Pod-L06")

    def test_case_modes(self):
        self.assertEqual(
            self.ops.build_new_name("bath pod", case_mode=self.ops.CASE_UPPER),
            "BATH POD")
        self.assertEqual(
            self.ops.build_new_name("Bath POD", case_mode=self.ops.CASE_LOWER),
            "bath pod")
        self.assertEqual(
            self.ops.build_new_name("bath POD", case_mode=self.ops.CASE_TITLE),
            "Bath Pod")

    def test_cleanup_strips_illegal_characters_and_spaces(self):
        self.assertEqual(
            self.ops.build_new_name("  Bath{Pod}  x  ", cleanup=True), "BathPod x")

    def test_rules_apply_in_order_find_prefix_suffix_case_cleanup(self):
        result = self.ops.build_new_name(
            "old pod", find="old", replace="new", prefix="ar-", suffix=" {x}",
            case_mode=self.ops.CASE_UPPER, cleanup=True)
        self.assertEqual(result, "AR-NEW POD X")


class TestNameValidation(unittest.TestCase):
    """Name problems surfaced in the STATUS and FINDINGS columns."""

    @classmethod
    def setUpClass(cls):
        cls.ops = _load_group_ops_module()

    def test_clean_name_is_idempotent(self):
        once = self.ops.clean_name("  A  B|C  ")
        self.assertEqual(once, "A BC")
        self.assertEqual(self.ops.clean_name(once), once)

    def test_illegal_chars_are_reported_once_each(self):
        self.assertEqual(self.ops.illegal_chars_in("a{b}{c}"), ["{", "}"])

    def test_illegal_chars_empty_for_a_good_name(self):
        self.assertEqual(self.ops.illegal_chars_in("AR-Bathroom Pod 01"), [])

    def test_name_problems_flags_empty(self):
        self.assertEqual(self.ops.name_problems("   "), ["Empty name"])

    def test_name_problems_flags_spaces_and_illegal(self):
        problems = self.ops.name_problems(" Bath|Pod  01 ")
        self.assertTrue(any("Illegal" in p for p in problems))
        self.assertIn("Leading/trailing spaces", problems)
        self.assertIn("Double spaces", problems)

    def test_name_problems_empty_for_a_good_name(self):
        self.assertEqual(self.ops.name_problems("AR-Bathroom Pod 01"), [])


class TestDuplicateDetection(unittest.TestCase):
    """Two group types may not end up sharing a name."""

    @classmethod
    def setUpClass(cls):
        cls.ops = _load_group_ops_module()

    def test_duplicates_ignore_case_and_padding(self):
        records = [_FakeRecord("Bath Pod"), _FakeRecord("bath pod "),
                   _FakeRecord("Kitchen Pod")]
        self.assertEqual(self.ops.duplicate_names(records), {"bath pod"})

    def test_no_duplicates_returns_empty(self):
        records = [_FakeRecord("A"), _FakeRecord("B")]
        self.assertEqual(self.ops.duplicate_names(records), set())



class TestPlacementFilters(unittest.TestCase):
    """Filtering and grouping behind the Placement tab's legend and level list."""

    @classmethod
    def setUpClass(cls):
        cls.ops = _load_group_ops_module()

    def _p(self, name, kind="Model", x=0.0, y=0.0, level="", view="", type_id=1,
           instance_id=None):
        return self.ops.GroupPlacement(
            instance_id=instance_id if instance_id is not None else id(name) % 100000,
            type_id=type_id, type_name=name, kind=kind, x=x, y=y, z=0.0,
            level_name=level, view_name=view)

    def test_location_label_prefers_level_then_view(self):
        self.assertEqual(self._p("A", level="L1", view="Plan 1").location_label, "L1")
        self.assertEqual(self._p("A", view="Plan 1").location_label, "Plan 1")
        self.assertEqual(self._p("A").location_label, "-")

    def test_level_names_are_sorted_and_deduped(self):
        placements = [self._p("A", level="L2"), self._p("B", level="l1"),
                      self._p("C", level="L2"), self._p("D")]
        self.assertEqual(self.ops.level_names_of(placements), ["l1", "L2"])

    def test_unlocated_rows_stay_out_of_the_level_list(self):
        self.assertEqual(self.ops.level_names_of([self._p("A")]), [])

    def test_filter_by_kind(self):
        placements = [self._p("A", kind="Model"), self._p("B", kind="Detail")]
        got = self.ops.filter_placements(placements, kind="Detail")
        self.assertEqual([p.type_name for p in got], ["B"])

    def test_filter_by_level(self):
        placements = [self._p("A", level="L1"), self._p("B", level="L2")]
        got = self.ops.filter_placements(placements, level="L2")
        self.assertEqual([p.type_name for p in got], ["B"])

    def test_filter_by_type_ids(self):
        placements = [self._p("A", type_id=1), self._p("B", type_id=2)]
        got = self.ops.filter_placements(placements, type_ids=[2])
        self.assertEqual([p.type_name for p in got], ["B"])

    def test_empty_type_ids_hides_everything(self):
        """Unticking every legend row must clear the plan, not show all of it."""
        placements = [self._p("A", type_id=1), self._p("B", type_id=2)]
        self.assertEqual(self.ops.filter_placements(placements, type_ids=[]), [])

    def test_no_filters_returns_everything(self):
        placements = [self._p("A", type_id=1), self._p("B", type_id=2)]
        self.assertEqual(len(self.ops.filter_placements(placements)), 2)

    def test_count_by_type(self):
        placements = [self._p("A", type_id=1), self._p("A", type_id=1),
                      self._p("B", type_id=2)]
        self.assertEqual(self.ops.count_by_type(placements), {1: 2, 2: 1})

    def test_extent_covers_every_point(self):
        placements = [self._p("A", x=-10, y=5), self._p("B", x=30, y=-2)]
        self.assertEqual(self.ops.placements_extent(placements), (-10, -2, 30, 5))

    def test_extent_of_nothing_is_none(self):
        self.assertIsNone(self.ops.placements_extent([]))


class TestPlanTransform(unittest.TestCase):
    """The feet-to-pixels maths the plan view is drawn with."""

    @classmethod
    def setUpClass(cls):
        cls.ops = _load_group_ops_module()

    def test_extent_centre_lands_on_canvas_centre(self):
        t = self.ops.PlanTransform.fit((0, 0, 100, 50), 400, 300, padding=0)
        px, py = t.to_canvas(50, 25)
        self.assertAlmostEqual(px, 200.0, places=6)
        self.assertAlmostEqual(py, 150.0, places=6)

    def test_y_axis_is_flipped(self):
        """Revit Y grows north, WPF Y grows down — north must be higher up."""
        t = self.ops.PlanTransform.fit((0, 0, 100, 100), 400, 400, padding=0)
        _, low = t.to_canvas(50, 0)
        _, high = t.to_canvas(50, 100)
        self.assertLess(high, low)

    def test_aspect_ratio_is_preserved(self):
        """A square in the model must not come out as a rectangle."""
        t = self.ops.PlanTransform.fit((0, 0, 100, 100), 800, 200, padding=0)
        ax, ay = t.to_canvas(0, 0)
        bx, by = t.to_canvas(100, 100)
        self.assertAlmostEqual(abs(bx - ax), abs(by - ay), places=6)

    def test_content_fits_inside_the_padded_box(self):
        w, h, pad = 400.0, 300.0, 24.0
        t = self.ops.PlanTransform.fit((-20, -20, 80, 60), w, h, padding=pad)
        for x, y in ((-20, -20), (80, -20), (-20, 60), (80, 60)):
            px, py = t.to_canvas(x, y)
            self.assertGreaterEqual(px, pad - 1e-6)
            self.assertLessEqual(px, w - pad + 1e-6)
            self.assertGreaterEqual(py, pad - 1e-6)
            self.assertLessEqual(py, h - pad + 1e-6)

    def test_round_trip_to_model_and_back(self):
        t = self.ops.PlanTransform.fit((0, 0, 120, 90), 640, 480, padding=16)
        for x, y in ((0, 0), (120, 90), (37.5, 11.25)):
            px, py = t.to_canvas(x, y)
            mx, my = t.to_model(px, py)
            self.assertAlmostEqual(mx, x, places=6)
            self.assertAlmostEqual(my, y, places=6)

    def test_single_instance_still_gives_a_usable_transform(self):
        """One group in the model must not divide by a zero span."""
        t = self.ops.PlanTransform.fit((10, 10, 10, 10), 400, 300, padding=24)
        px, py = t.to_canvas(10, 10)
        self.assertAlmostEqual(px, 200.0, places=6)
        self.assertAlmostEqual(py, 150.0, places=6)
        self.assertTrue(t.scale > 0)

    def test_instances_in_a_straight_line_still_fit(self):
        """Zero span on one axis only — the other axis must still drive scale."""
        t = self.ops.PlanTransform.fit((0, 5, 100, 5), 400, 300, padding=20)
        left, _ = t.to_canvas(0, 5)
        right, _ = t.to_canvas(100, 5)
        self.assertGreaterEqual(left, 20 - 1e-6)
        self.assertLessEqual(right, 400 - 20 + 1e-6)

    def test_no_extent_centres_on_the_canvas(self):
        t = self.ops.PlanTransform.fit(None, 400, 300)
        self.assertEqual((t.offset_x, t.offset_y), (200.0, 150.0))

    def test_zoom_keeps_the_point_under_the_cursor(self):
        t = self.ops.PlanTransform.fit((0, 0, 100, 100), 400, 400, padding=0)
        anchor = (120.0, 260.0)
        before = t.to_model(*anchor)
        t.zoom_at(anchor[0], anchor[1], 2.0)
        after = t.to_model(*anchor)
        self.assertAlmostEqual(before[0], after[0], places=6)
        self.assertAlmostEqual(before[1], after[1], places=6)

    def test_zoom_scales(self):
        t = self.ops.PlanTransform.fit((0, 0, 100, 100), 400, 400, padding=0)
        start = t.scale
        t.zoom_at(200, 200, 2.0)
        self.assertAlmostEqual(t.scale, start * 2.0, places=9)

    def test_zoom_is_clamped_both_ways(self):
        t = self.ops.PlanTransform.fit((0, 0, 100, 100), 400, 400, padding=0)
        for _ in range(200):
            t.zoom_at(200, 200, 4.0)
        self.assertLessEqual(t.scale, t.MAX_ZOOM_SCALE)
        for _ in range(400):
            t.zoom_at(200, 200, 0.25)
        self.assertGreaterEqual(t.scale, t.MIN_ZOOM_SCALE)

    def test_zoom_by_a_non_positive_factor_is_ignored(self):
        t = self.ops.PlanTransform.fit((0, 0, 100, 100), 400, 400, padding=0)
        start = t.scale
        t.zoom_at(200, 200, 0)
        t.zoom_at(200, 200, -2)
        self.assertEqual(t.scale, start)

    def test_pan_moves_by_exactly_the_pixel_delta(self):
        t = self.ops.PlanTransform.fit((0, 0, 100, 100), 400, 400, padding=0)
        before = t.to_canvas(50, 50)
        t.pan_by(17, -9)
        after = t.to_canvas(50, 50)
        self.assertAlmostEqual(after[0] - before[0], 17.0, places=6)
        self.assertAlmostEqual(after[1] - before[1], -9.0, places=6)

    def test_pan_does_not_change_scale(self):
        t = self.ops.PlanTransform.fit((0, 0, 100, 100), 400, 400, padding=0)
        start = t.scale
        t.pan_by(100, 100)
        self.assertEqual(t.scale, start)

    def test_degenerate_canvas_size_does_not_blow_up(self):
        t = self.ops.PlanTransform.fit((0, 0, 100, 100), 0, 0, padding=100)
        self.assertTrue(t.scale > 0)
        t.to_canvas(50, 50)     # must not raise


class TestPlacementLabels(unittest.TestCase):
    """The strings shown under the plan."""

    @classmethod
    def setUpClass(cls):
        cls.ops = _load_group_ops_module()

    def test_feet_convert_to_metres(self):
        self.assertEqual(self.ops.feet_to_metres_label(0), "0.0 m")
        self.assertEqual(self.ops.feet_to_metres_label(1 / 0.3048), "1.0 m")

    def test_long_spans_switch_to_kilometres(self):
        self.assertEqual(self.ops.feet_to_metres_label(2000 / 0.3048), "2.0 km")

    def test_extent_label_reads_as_width_by_height(self):
        extent = (0, 0, 10 / 0.3048, 20 / 0.3048)
        self.assertEqual(self.ops.extent_label(extent), "10.0 m x 20.0 m")

    def test_extent_label_of_nothing(self):
        self.assertEqual(self.ops.extent_label(None), "no extent")


class TestPlacedInstanceTotal(unittest.TestCase):
    """The "N instances have no location" counter on the info strip."""

    @classmethod
    def setUpClass(cls):
        cls.ops = _load_group_ops_module()

    def test_totals_every_record(self):
        class R(object):
            def __init__(self, n):
                self.instance_count = n
        self.assertEqual(self.ops.placed_instance_total([R(3), R(0), R(5)]), 8)

    def test_empty_model_totals_zero(self):
        self.assertEqual(self.ops.placed_instance_total([]), 0)



class TestOutlineGeometry(unittest.TestCase):
    """The plan-context extent maths that keeps building and markers both in view."""

    @classmethod
    def setUpClass(cls):
        cls.ops = _load_group_ops_module()

    def _seg(self, x1, y1, x2, y2, ext=False):
        return self.ops.OutlineSegment(x1, y1, x2, y2, ext)

    def test_outline_extent_covers_both_ends_of_every_segment(self):
        segments = [self._seg(0, 0, 10, 4), self._seg(-5, 20, 3, -2)]
        self.assertEqual(self.ops.outline_extent(segments), (-5, -2, 10, 20))

    def test_outline_extent_of_nothing_is_none(self):
        self.assertIsNone(self.ops.outline_extent([]))

    def test_segment_stores_floats_and_the_exterior_flag(self):
        s = self._seg(1, 2, 3, 4, ext=True)
        self.assertEqual((s.x1, s.y1, s.x2, s.y2), (1.0, 2.0, 3.0, 4.0))
        self.assertTrue(s.is_exterior)
        self.assertFalse(self._seg(0, 0, 1, 1).is_exterior)

    def test_union_extent_covers_both(self):
        self.assertEqual(
            self.ops.union_extent((0, 0, 10, 10), (-5, 2, 4, 30)),
            (-5, 0, 10, 30))

    def test_union_extent_tolerates_a_missing_side(self):
        self.assertEqual(self.ops.union_extent(None, (1, 2, 3, 4)), (1, 2, 3, 4))
        self.assertEqual(self.ops.union_extent((1, 2, 3, 4), None), (1, 2, 3, 4))
        self.assertIsNone(self.ops.union_extent(None, None))

    def test_markers_in_one_corner_still_fit_with_the_building(self):
        """The whole reason for union_extent: groups clustered in a corner must
        not push the building outline off screen."""
        building = (0, 0, 300, 200)
        markers = (280, 180, 290, 190)
        both = self.ops.union_extent(markers, building)
        t = self.ops.PlanTransform.fit(both, 600, 400, padding=10)
        for x, y in ((0, 0), (300, 200), (280, 180)):
            px, py = t.to_canvas(x, y)
            self.assertGreaterEqual(px, 10 - 1e-6)
            self.assertLessEqual(px, 600 - 10 + 1e-6)
            self.assertGreaterEqual(py, 10 - 1e-6)
            self.assertLessEqual(py, 400 - 10 + 1e-6)


class TestRenamePlan(unittest.TestCase):
    """plan_rename decides what is rejected, what is parked, and what is written.

    Revit refuses a name another type still carries, and renames are sequential,
    so A->B while B->A used to fail on whichever went first.
    """

    @classmethod
    def setUpClass(cls):
        cls.ops = _load_group_ops_module()

    def test_swap_parks_one_of_the_two(self):
        a = _FakeRecord("A")
        b = _FakeRecord("B")
        rejected, parked, writes = self.ops.plan_rename([(a, "B"), (b, "A")])
        self.assertEqual(rejected, [])
        self.assertEqual(len(writes), 2)
        # Both names are wanted by the other record, so both get parked.
        self.assertEqual(sorted(id(r) for r in parked), sorted([id(a), id(b)]))

    def test_chain_parks_the_name_in_the_middle(self):
        a = _FakeRecord("A")
        b = _FakeRecord("B")
        rejected, parked, writes = self.ops.plan_rename([(a, "B"), (b, "C")])
        self.assertEqual(rejected, [])
        self.assertEqual([id(r) for r in parked], [id(b)])

    def test_independent_renames_park_nothing(self):
        a = _FakeRecord("A")
        b = _FakeRecord("B")
        _rejected, parked, writes = self.ops.plan_rename([(a, "A2"), (b, "B2")])
        self.assertEqual(parked, [])
        self.assertEqual(len(writes), 2)

    def test_rejects_empty_illegal_and_unchanged(self):
        empty = _FakeRecord("Keep")
        same = _FakeRecord("Same")
        bad = _FakeRecord("Bad")
        rejected, parked, writes = self.ops.plan_rename(
            [(empty, "   "), (same, "Same"), (bad, "No{brace}")])
        self.assertEqual(writes, [])
        self.assertEqual(parked, [])
        messages = dict((id(r), m) for r, m in rejected)
        self.assertEqual(messages[id(empty)], "Empty name")
        self.assertEqual(messages[id(same)], "Unchanged")
        self.assertTrue(messages[id(bad)].startswith("Illegal"))

    def test_parking_is_case_insensitive(self):
        a = _FakeRecord("pod")
        b = _FakeRecord("POD 2")
        _rejected, parked, _writes = self.ops.plan_rename([(a, "POD 2"), (b, "pod")])
        self.assertEqual(len(parked), 2)

    def test_handles_empty_input(self):
        self.assertEqual(self.ops.plan_rename([]), ([], [], []))
        self.assertEqual(self.ops.plan_rename(None), ([], [], []))


if __name__ == '__main__':
    unittest.main()
