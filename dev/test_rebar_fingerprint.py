# -*- coding: utf-8 -*-
"""
Tests for the Revit-free half of Clone Drawing: Snippets/_drawing_clone.py
(similarity score, view classification, mark substitution, clone planning,
face matching) and the member pairing it borrows from
Snippets/_rebar.match_by_fingerprint.
Run: python dev/test_rebar_fingerprint.py
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


def _profile(category="Structural Columns", bbox=(400.0, 400.0, 3000.0), rebar=24, members=25):
    return {"category": category, "bbox_mm": bbox, "rebar_count": rebar,
            "member_count": members}


class TestSimilarity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dc = _load_module('_drawing_clone')

    def test_similarity_same_category_within_tol_is_100(self):
        score, reasons = self.dc.similarity(_profile(), _profile(bbox=(410.0, 395.0, 3050.0)))
        self.assertEqual(score, 100)
        self.assertEqual(reasons, [])

    def test_similarity_identical_is_100(self):
        self.assertEqual(self.dc.similarity(_profile(), _profile()), (100, []))

    def test_similarity_penalties(self):
        src = _profile()
        score, reasons = self.dc.similarity(src, _profile(category="Structural Framing"))
        self.assertEqual(score, 60)
        self.assertEqual(len(reasons), 1)
        self.assertIn("Structural Framing", reasons[0])

        score, reasons = self.dc.similarity(src, _profile(bbox=(400.0, 500.0, 3000.0)))
        self.assertEqual(score, 70)
        self.assertIn("size", reasons[0])

        score, reasons = self.dc.similarity(src, _profile(rebar=20))
        self.assertEqual(score, 80)
        self.assertIn("20 rebar", reasons[0])

        score, reasons = self.dc.similarity(
            src, _profile(category="Walls", bbox=(200.0, 4000.0, 3000.0), rebar=0))
        self.assertEqual(score, 10)
        self.assertEqual(len(reasons), 3)

    def test_similarity_tolerance_is_a_percentage(self):
        src = _profile(bbox=(1000.0, 1000.0, 1000.0))
        self.assertEqual(self.dc.similarity(src, _profile(bbox=(1050.0, 1000.0, 1000.0)))[0], 100)
        self.assertEqual(self.dc.similarity(src, _profile(bbox=(1060.0, 1000.0, 1000.0)))[0], 70)
        self.assertEqual(
            self.dc.similarity(src, _profile(bbox=(1060.0, 1000.0, 1000.0)), tol_pct=10.0)[0], 100)

    def test_similarity_never_negative(self):
        score, _ = self.dc.similarity(_profile(), {"category": "x", "bbox_mm": None})
        self.assertGreaterEqual(score, 0)

    def test_member_count_is_not_scored(self):
        self.assertEqual(self.dc.similarity(_profile(), _profile(members=40))[0], 100)


class TestClassifyView(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dc = _load_module('_drawing_clone')

    def test_classify_view_table(self):
        c = self.dc.classify_view
        # through the centre -> detail sections
        self.assertEqual(c("Section", (0, 0, -1), 0.0, True), "HorizontalDetail")
        self.assertEqual(c("Section", (0, 1, 0), 0.0, True), "DetailSectionA")
        self.assertEqual(c("Section", (-1, 0, 0), 0.1, True), "DetailSectionB")
        self.assertEqual(c("Detail", (0, 1, 0), -0.2, False), "DetailSectionA")
        # on the near face -> elevations
        self.assertEqual(c("Section", (0, 0, -1), -1.0, True), "ElevationTop")
        self.assertEqual(c("Section", (0, 0, 1), -1.0, True), "ElevationBottom")
        self.assertEqual(c("Section", (0, 1, 0), -1.0, True), "ElevationFront")
        self.assertEqual(c("Section", (0, -1, 0), -0.9, True), "ElevationBack")
        self.assertEqual(c("Section", (1, 0, 0), -1.0, True), "ElevationLeft")
        self.assertEqual(c("Section", (-1, 0, 0), -1.0, True), "ElevationRight")
        # cut position unknown -> the view type decides
        self.assertEqual(c("Elevation", (0, 1, 0), None, True), "ElevationFront")
        self.assertEqual(c("Section", (0, 1, 0), None, True), "DetailSectionA")
        # small tilt still classifies
        self.assertEqual(c("Section", (0.05, 0.99, 0), 0.0, True), "DetailSectionA")

    def test_classify_view_unknown_is_none(self):
        c = self.dc.classify_view
        self.assertIsNone(c("Section", (0.7071, 0.7071, 0), 0.0, True))     # oblique
        self.assertIsNone(c("Section", (0, -1, 0), 0.0, True))              # not an assembly section
        self.assertIsNone(c("Section", (0, 0, 1), 0.0, True))               # looking up through centre
        self.assertIsNone(c("Section", (0, 1, 0), 1.0, True))               # cut on the far face
        self.assertIsNone(c("ThreeD", (0, 0, -1), 0.0, True))
        self.assertIsNone(c("FloorPlan", (0, 0, -1), 0.0, True))
        self.assertIsNone(c("Section", None, 0.0, True))
        self.assertIsNone(c("Section", (0, 0, 0), 0.0, True))

    def test_classify_accepts_enum_like_names(self):
        self.assertEqual(self.dc.classify_view("ViewType.Section", (0, 0, -1), 0.0, True),
                         "HorizontalDetail")

    def test_kind_of_orientation(self):
        self.assertEqual(self.dc.kind_of_orientation("ElevationTop"), "elevation")
        self.assertEqual(self.dc.kind_of_orientation("HorizontalDetail"), "section")


class TestNames(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dc = _load_module('_drawing_clone')

    def test_substitute_mark(self):
        s = self.dc.substitute_mark
        self.assertEqual(s("C-01 Elevation Front", "C-01", "C-02"), "C-02 Elevation Front")
        self.assertEqual(s("C-01 / C-01", "C-01", "C-02"), "C-02 / C-02")
        self.assertEqual(s("Elevation Front", "C-01", "C-02"), "Elevation Front (C-02)")
        self.assertEqual(s("Elevation Front", "", "C-02"), "Elevation Front (C-02)")
        self.assertEqual(s("Elevation Front", "C-01", "C-02", fallback_suffix=False),
                         "Elevation Front")
        self.assertEqual(s("", "C-01", "C-02"), "C-02")

    def test_render_pattern_and_view_names(self):
        spec = self.dc.ViewSpec(kind="section", name="C-01 Section A", sheet_number="S-01")
        self.assertEqual(self.dc.view_name_for(spec, "C-01", "C-02", "{SourceName}"),
                         "C-02 Section A")
        # pattern with an explicit {Mark}: no extra suffix
        spec2 = self.dc.ViewSpec(kind="section", name="Section A")
        self.assertEqual(self.dc.view_name_for(spec2, "C-01", "C-02", "{Mark} {SourceName}"),
                         "C-02 Section A")
        self.assertEqual(self.dc.view_name_for(spec2, "C-01", "C-02", "{SourceName}"),
                         "Section A (C-02)")
        self.assertEqual(self.dc.render_pattern("{Unknown}-{Mark}", {"Mark": "C-02"}),
                         "{Unknown}-C-02")

    def test_sheet_number_pattern(self):
        spec = self.dc.ViewSpec(kind="sheet", sheet_number="S-01", sheet_name="C-01 Column")
        self.assertEqual(self.dc.sheet_number_for(spec, "C-01", "C-02", "{SourceNumber}-{Mark}"),
                         "S-01-C-02")

    def test_unique_name(self):
        self.assertEqual(self.dc.unique_name("A", set()), "A")
        self.assertEqual(self.dc.unique_name("A", {"A"}), "A (2)")
        self.assertEqual(self.dc.unique_name("A", {"A", "A (2)"}), "A (3)")


def _spec(dc, view_id, kind, orientation=None, category_id=-1, skip=u""):
    return dc.ViewSpec(src_view_id=view_id, kind=kind, orientation=orientation,
                       category_id=category_id, name="v%d" % view_id, skip_reason=skip)


class TestPlanClone(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dc = _load_module('_drawing_clone')

    def _source(self):
        dc = self.dc
        return [_spec(dc, 1, "3d"),
                _spec(dc, 2, "elevation", "ElevationFront"),
                _spec(dc, 3, "section", "HorizontalDetail"),
                _spec(dc, 4, "partlist"),
                _spec(dc, 5, "sheet"),
                _spec(dc, 6, "section", skip=u"orientation unknown")]

    def test_plan_clone_target_without_views_creates_all(self):
        plan = self.dc.plan_for_target(self._source(), [], self.dc.CloneSettings())
        self.assertEqual([s.src_view_id for s in plan.to_create], [1, 2, 3, 4, 5])
        self.assertEqual([(s.src_view_id, r) for s, r in plan.skipped], [(6, u"orientation unknown")])
        self.assertEqual(plan.skip_reason, u"")

    def test_plan_clone_skip_existing_vs_add_missing(self):
        dc = self.dc
        existing = [_spec(dc, 101, "3d"), _spec(dc, 102, "elevation", "ElevationFront")]

        plan = dc.plan_for_target(self._source(), existing, dc.CloneSettings(existing=dc.EXISTING_SKIP))
        self.assertEqual(plan.to_create, [])
        self.assertEqual(plan.skip_reason, u"has drawing")

        plan = dc.plan_for_target(self._source(), existing, dc.CloneSettings(existing=dc.EXISTING_ADD))
        self.assertEqual([s.src_view_id for s in plan.to_create], [3, 4, 5])
        self.assertEqual(plan.paired, {1: 101, 2: 102})
        reasons = dict((s.src_view_id, r) for s, r in plan.skipped)
        self.assertEqual(reasons, {1: u"exists", 2: u"exists", 6: u"orientation unknown"})

    def test_pair_existing_counts_duplicates(self):
        dc = self.dc
        src = [_spec(dc, 1, "section", "DetailSectionA"), _spec(dc, 2, "section", "DetailSectionA"),
               _spec(dc, 3, "single_schedule", category_id=7),
               _spec(dc, 4, "single_schedule", category_id=8)]
        dst = [_spec(dc, 11, "section", "DetailSectionA"),
               _spec(dc, 13, "single_schedule", category_id=8)]
        self.assertEqual(dc.pair_existing(src, dst), {1: 11, 4: 13})

    def test_creation_order_puts_sheet_last(self):
        ordered = self.dc.creation_order(list(reversed(self._source())))
        self.assertEqual(ordered[-1].kind, "sheet")
        self.assertEqual(ordered[0].kind, "3d")

    def test_summarize_specs(self):
        dc = self.dc
        specs = [_spec(dc, 1, "3d"), _spec(dc, 2, "elevation", "ElevationFront"),
                 _spec(dc, 3, "elevation", "ElevationLeft"),
                 _spec(dc, 4, "section", "HorizontalDetail"),
                 _spec(dc, 5, "section", "DetailSectionA"), _spec(dc, 6, "partlist"),
                 dc.ViewSpec(kind="sheet", sheet_number="S-01", title_block_name="A1")]
        self.assertEqual(dc.summarize_specs(specs),
                         u"3D · 2 elevations · 2 sections · Part list "
                         u"· Sheet S-01 (A1)")
        self.assertEqual(dc.summarize_specs([]), u"No assembly views")

    def test_target_status(self):
        dc = self.dc
        ts = dc.target_status
        self.assertEqual(ts(False, True, False, dc.EXISTING_SKIP), (u"Ready", "Success", True))
        self.assertEqual(ts(False, True, True, dc.EXISTING_SKIP)[2], False)
        self.assertEqual(ts(False, True, True, dc.EXISTING_ADD)[2], True)
        self.assertEqual(ts(False, True, True, dc.EXISTING_NEW)[2], True)
        self.assertEqual(ts(False, True, True, dc.EXISTING_REPLACE)[1], "Danger")
        self.assertEqual(ts(False, False, False, dc.EXISTING_SKIP)[2], False)   # Tekla K4
        self.assertEqual(ts(True, True, False, dc.EXISTING_ADD)[2], dc.SAME_TYPE_POLICY != "skip")


class TestFaceMatching(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dc = _load_module('_drawing_clone')

    def test_match_face_exact_and_tolerance(self):
        faces = [((1, 0, 0), 200.0), ((-1, 0, 0), 200.0), ((0, 0, 1), 1500.0)]
        self.assertEqual(self.dc.match_face(((1, 0, 0), 203.0), faces), (0, u""))
        self.assertEqual(self.dc.match_face(((0, 0, 1), 1500.0), faces)[0], 2)
        index, reason = self.dc.match_face(((1, 0, 0), 210.0), faces)
        self.assertIsNone(index)
        self.assertEqual(reason, self.dc.R_FACE_NO_MATCH)

    def test_match_face_mirror_and_ambiguous(self):
        faces = [((-1, 0, 0), 200.0), ((0, 1, 0), 150.0)]
        self.assertEqual(self.dc.match_face(((1, 0, 0), 200.0), faces)[0], 0)
        self.assertIsNone(self.dc.match_face(((1, 0, 0), 200.0), faces, allow_mirror=False)[0])
        twins = [((0, 1, 0), 150.0), ((0, 1, 0), 151.0)]
        index, reason = self.dc.match_face(((0, 1, 0), 150.0), twins)
        self.assertIsNone(index)
        self.assertEqual(reason, self.dc.R_FACE_AMBIGUOUS)


class _Bar(object):
    def __init__(self, ident, kind="Rebar", shape="M_00", bar_type="T16", quantity=1):
        self.id = ident
        self.kind = kind
        self.shape = shape
        self.bar_type = bar_type
        self.quantity = quantity


class TestMemberMatching(unittest.TestCase):
    """The pairing Clone Drawing T3 relies on (_rebar.match_by_fingerprint)."""

    @classmethod
    def setUpClass(cls):
        cls.rebar = _load_module('_rebar')
        cls.dc = _load_module('_drawing_clone')

    def test_identical_assemblies_pair_everything(self):
        src = [(_Bar(1), (100.0, 100.0, 0.0)), (_Bar(2), (-100.0, 100.0, 0.0)),
               (_Bar(3, kind="Element:Structural Columns", shape="", bar_type="400x400"),
                (0.0, 0.0, 1500.0))]
        dst = [(_Bar(11), (101.0, 99.0, 0.0)), (_Bar(12), (-100.0, 100.0, 0.0)),
               (_Bar(13, kind="Element:Structural Columns", shape="", bar_type="400x400"),
                (0.0, 0.0, 1500.0))]
        pairs, unmatched, ambiguous = self.rebar.match_by_fingerprint(src, dst)
        self.assertEqual([(s[0].id, d[0].id) for s, d in pairs], [(1, 11), (2, 12), (3, 13)])
        self.assertEqual(unmatched, [])
        self.assertEqual(ambiguous, [])

    def test_different_shape_is_reported_unmatched(self):
        src = [(_Bar(1), (0.0, 0.0, 0.0)), (_Bar(2, bar_type="T20"), (0.0, 50.0, 0.0))]
        dst = [(_Bar(11), (0.0, 0.0, 0.0))]
        pairs, unmatched, _ = self.rebar.match_by_fingerprint(src, dst)
        self.assertEqual(len(pairs), 1)
        self.assertEqual([s[0].id for s in unmatched], [2])

    def test_mirrored_assembly_pairs_with_mirror_on(self):
        src = [(_Bar(1), (150.0, 40.0, 0.0)), (_Bar(2, bar_type="T20"), (-80.0, 10.0, 0.0))]
        dst = [(_Bar(11), (-150.0, 40.0, 0.0)), (_Bar(12, bar_type="T20"), (80.0, 10.0, 0.0))]
        pairs, unmatched, _ = self.rebar.match_by_fingerprint(src, dst, allow_mirror=True)
        self.assertEqual(len(pairs), 2)
        pairs, unmatched, _ = self.rebar.match_by_fingerprint(src, dst, allow_mirror=False)
        self.assertEqual(len(pairs), 0)
        self.assertEqual(len(unmatched), 2)

    def test_tie_is_ambiguous_not_guessed(self):
        src = [(_Bar(1), (0.0, 0.0, 0.0))]
        dst = [(_Bar(11), (12.0, 0.0, 0.0)), (_Bar(12), (-12.0, 0.0, 0.0))]
        pairs, unmatched, ambiguous = self.rebar.match_by_fingerprint(src, dst,
                                                                      allow_mirror=False)
        self.assertEqual(pairs, [])
        self.assertEqual([s[0].id for s in ambiguous], [1])


class TestTallyAndOutcome(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dc = _load_module('_drawing_clone')

    def test_tally_counts_and_reasons(self):
        a = self.dc.Tally()
        a.tag(True)
        a.tag(False, 5, self.dc.R_REBAR_POSITION)
        a.dim(False, 6, self.dc.R_DIM_REBAR)
        b = self.dc.Tally()
        b.dim(True)
        b.other(7, u"annotation: " + self.dc.R_MRA)
        b.spot(False, 8, self.dc.R_SPOT_KIND % "coordinate")
        b.spot(True)
        b.tags_created = 2
        a.merge(b)
        self.assertEqual((a.tags_ok, a.tags_total, a.dims_ok, a.dims_total), (1, 2, 1, 2))
        self.assertEqual((a.spots_ok, a.spots_total, a.tags_created), (1, 2, 2))
        self.assertEqual([i for i, _ in a.unmatched], [5, 6, 7, 8])
        self.assertTrue(all(reason for _, reason in a.unmatched))

    def test_outcome_summary_and_failures(self):
        out = self.dc.TargetOutcome(10, "C-02")
        out.views_created = 5
        out.sheet_number = "S-01-C-02"
        out.copied = 3
        out.tally.tag(True)
        out.tally.tag(False, 1, "x")
        out.unmatched.append((1, "x"))
        out.note(self.dc.LEVEL_FAILED, "failed C-02: something")
        self.assertEqual(out.failures, 1)
        text = self.dc.outcome_summary(out)
        self.assertIn("5 views, sheet S-01-C-02", text)
        self.assertIn("tags 1 / 2", text)
        self.assertIn("1 unmatched", text)
        totals = self.dc.summarize_outcomes([out])
        self.assertEqual((totals["ok"], totals["views"], totals["unmatched"]), (1, 5, 1))


if __name__ == '__main__':
    unittest.main()
