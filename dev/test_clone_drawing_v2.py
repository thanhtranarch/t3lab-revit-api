# -*- coding: utf-8 -*-
"""
Tests for the Revit-free half of Clone Drawing V2 (Snippets/_drawing_clone.py):
clone settings and presets, the existing-drawing policies, manual-view keys,
dedupe keys, delete categories, confirmation and report text.
Design: dev/plan/clone-drawing-v2-design.md.
Run: python dev/test_clone_drawing_v2.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from Snippets import _drawing_clone as dc    # noqa: E402  (pure at module level)


def _spec(view_id, kind, orientation=None, name=None, skip=u"", parent=-1, category_id=-1):
    return dc.ViewSpec(src_view_id=view_id, kind=kind, orientation=orientation,
                       name=name if name is not None else u"v%d" % view_id,
                       skip_reason=skip, parent_view_id=parent, category_id=category_id)


def _source():
    return [_spec(1, "3d", name=u"C-01 3D"),
            _spec(2, "elevation", "ElevationFront", name=u"C-01 Front"),
            _spec(3, "section", "HorizontalDetail", name=u"C-01 Plan"),
            _spec(4, "manual_callout", name=u"C-01 Detail 1", parent=2),
            _spec(5, "manual_section", name=u"C-01 Oblique"),
            _spec(6, "partlist", name=u"C-01 Parts"),
            _spec(7, "sheet", name=u"C-01"),
            _spec(8, "section", skip=u"orientation unknown and no crop box - cannot be re-created "
                                      u"as a section box", name=u"C-01 Bad")]


class TestSettings(unittest.TestCase):

    def test_defaults_follow_the_design_table(self):
        s = dc.CloneSettings()
        self.assertEqual(s.tags, dc.CLONE_CREATE)
        self.assertEqual(s.revisions, dc.SKIP)
        self.assertEqual(s.mra, dc.SKIP)
        self.assertEqual(s.existing, dc.EXISTING_SKIP)
        self.assertEqual(s.crop, dc.CROP_SOURCE)
        self.assertTrue(s.any_layer)
        for field, _, choices, default, hint in dc.SETTINGS_ROWS:
            self.assertIn(default, choices, field)
            self.assertTrue(hint, field)

    def test_every_row_has_a_choice_label(self):
        for _, _, choices, _, _ in dc.SETTINGS_ROWS:
            for choice in choices:
                self.assertIn(choice, dc.CHOICE_LABELS)
        for choice in dc.EXISTING_CHOICES:
            self.assertIn(choice, dc.EXISTING_LABELS)

    def test_normalize_rejects_bad_values_and_keeps_good_ones(self):
        s, invalid = dc.normalize_settings({
            "tags": "create", "texts": "skip", "existing": "nuke", "tol_mm": "0",
            "allow_mirror": "false", "sheet_pattern": "  ", "unknown_field": 1})
        self.assertEqual(sorted(invalid), ["existing", "tags", "tol_mm"])
        self.assertEqual(s.tags, dc.CLONE_CREATE)          # default kept
        self.assertEqual(s.texts, dc.SKIP)
        self.assertEqual(s.existing, dc.EXISTING_SKIP)
        self.assertEqual(s.tol_mm, dc.DEFAULT_TOL_MM)
        self.assertFalse(s.allow_mirror)
        self.assertEqual(s.sheet_pattern, dc.DEFAULT_SHEET_PATTERN)

    def test_wants_and_any_annotation(self):
        s = dc.CloneSettings(dimensions=dc.SKIP, spot=dc.SKIP, tags=dc.SKIP, texts=dc.SKIP,
                             symbols=dc.SKIP, shapes=dc.SKIP, imports=dc.SKIP, images=dc.SKIP,
                             revisions=dc.SKIP)
        self.assertFalse(s.any_annotation)
        self.assertTrue(s.any_layer)                         # views still clone
        s.views = dc.SKIP
        self.assertFalse(s.any_layer)

    def test_t2_categories_follow_rows(self):
        on, off = dc.t2_categories(dc.CloneSettings())
        self.assertIn("OST_TextNotes", on)
        self.assertIn("OST_RevisionClouds", off)             # off by default
        on, off = dc.t2_categories(dc.CloneSettings(texts=dc.SKIP, revisions=dc.CLONE))
        self.assertIn("OST_TextNotes", off)
        self.assertIn("OST_RevisionClouds", on)


class TestPresets(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t3clone_")
        self.path = os.path.join(self.tmp, "sub", "clone_drawing_presets.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_builtins(self):
        presets = dc.builtin_presets()
        self.assertEqual(set(presets), set(dc.BUILTIN_PRESET_NAMES))
        self.assertEqual(presets[dc.PRESET_VIEWS].tags, dc.SKIP)
        self.assertTrue(presets[dc.PRESET_VIEWS].wants("views"))
        self.assertEqual(presets[dc.PRESET_ANNOTATIONS].views, dc.SKIP)
        self.assertEqual(presets[dc.PRESET_ANNOTATIONS].existing, dc.EXISTING_ADD)
        self.assertEqual(presets[dc.PRESET_TEKLA].to_dict(), dc.CloneSettings().to_dict())

    def test_missing_file_is_fine(self):
        presets, last, warnings = dc.load_presets(self.path)
        self.assertEqual((presets, last, warnings), ({}, dc.PRESET_TEKLA, []))

    def test_roundtrip(self):
        mine = dc.CloneSettings(tags=dc.CLONE, revisions=dc.CLONE, tol_mm=25, existing=dc.EXISTING_NEW)
        error = dc.save_presets(self.path, {u"Columns": mine, dc.PRESET_TEKLA: dc.CloneSettings()},
                                u"Columns")
        self.assertIsNone(error)
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["version"], dc.PRESET_VERSION)
        self.assertNotIn(dc.PRESET_TEKLA, data["presets"])     # builtins never written
        presets, last, warnings = dc.load_presets(self.path)
        self.assertEqual(last, u"Columns")
        self.assertEqual(warnings, [])
        self.assertEqual(presets[u"Columns"].to_dict(), mine.to_dict())

    def test_invalid_values_in_file_are_reported_not_fatal(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "last": "Odd",
                       "presets": {"Odd": {"tags": "wrong", "tol_mm": -3},
                                   dc.PRESET_TEKLA: {"tags": "skip"}}}, fh)
        presets, last, warnings = dc.load_presets(self.path)
        self.assertEqual(list(presets), ["Odd"])                # builtin name ignored
        self.assertEqual(presets["Odd"].tags, dc.CLONE_CREATE)
        self.assertEqual(len(warnings), 1)
        self.assertIn("tags", warnings[0])
        self.assertIn("tol_mm", warnings[0])

    def test_corrupt_file_warns(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        presets, last, warnings = dc.load_presets(self.path)
        self.assertEqual(presets, {})
        self.assertEqual(len(warnings), 1)


class TestPolicies(unittest.TestCase):
    """plan_for_target under the four existing-drawing policies (design section 5)."""

    def _existing(self):
        return [_spec(101, "3d", name=u"C-02 3D"),
                _spec(102, "elevation", "ElevationFront", name=u"C-02 Front"),
                _spec(104, "manual_callout", name=u"C-02 Detail 1", parent=102),
                _spec(107, "sheet", name=u"C-02")]

    def test_fresh_target_creates_everything_clonable(self):
        plan = dc.plan_for_target(_source(), [], dc.CloneSettings())
        self.assertEqual([s.src_view_id for s in plan.to_create], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual([(s.src_view_id, r[:19]) for s, r in plan.skipped],
                         [(8, u"orientation unknown")])

    def test_manual_views_off(self):
        plan = dc.plan_for_target(_source(), [], dc.CloneSettings(manual_views=dc.SKIP))
        self.assertEqual([s.src_view_id for s in plan.to_create], [1, 2, 3, 6, 7])
        reasons = dict((s.src_view_id, r) for s, r in plan.skipped)
        self.assertEqual(reasons[4], dc.R_SKIPPED_BY_SETTING)
        self.assertEqual(reasons[5], dc.R_SKIPPED_BY_SETTING)

    def test_skip_policy(self):
        plan = dc.plan_for_target(_source(), self._existing(), dc.CloneSettings())
        self.assertEqual(plan.skip_reason, u"has drawing")
        self.assertEqual(plan.to_create, [])

    def test_add_missing_pairs_by_key_including_manual_names(self):
        plan = dc.plan_for_target(_source(), self._existing(),
                                  dc.CloneSettings(existing=dc.EXISTING_ADD), u"C-01", u"C-02")
        self.assertEqual(plan.paired, {1: 101, 2: 102, 4: 104, 7: 107})
        self.assertEqual([s.src_view_id for s in plan.to_create], [3, 5, 6])
        self.assertEqual(plan.skip_reason, u"")

    def test_replace_policy_plans_like_add_missing(self):
        add = dc.plan_for_target(_source(), self._existing(),
                                 dc.CloneSettings(existing=dc.EXISTING_ADD), u"C-01", u"C-02")
        rep = dc.plan_for_target(_source(), self._existing(),
                                 dc.CloneSettings(existing=dc.EXISTING_REPLACE), u"C-01", u"C-02")
        self.assertEqual(rep.paired, add.paired)
        self.assertEqual([s.src_view_id for s in rep.to_create],
                         [s.src_view_id for s in add.to_create])

    def test_new_set_creates_everything_and_pairs_nothing(self):
        plan = dc.plan_for_target(_source(), self._existing(),
                                  dc.CloneSettings(existing=dc.EXISTING_NEW), u"C-01", u"C-02")
        self.assertEqual(plan.paired, {})
        self.assertEqual([s.src_view_id for s in plan.to_create], [1, 2, 3, 4, 5, 6, 7])

    def test_views_off_annotates_existing_views_only(self):
        plan = dc.plan_for_target(_source(), self._existing(),
                                  dc.CloneSettings(views=dc.SKIP), u"C-01", u"C-02")
        self.assertEqual(plan.to_create, [])
        self.assertEqual(plan.paired, {1: 101, 2: 102, 4: 104, 7: 107})
        plan = dc.plan_for_target(_source(), [], dc.CloneSettings(views=dc.SKIP))
        self.assertIn(u"Views and sheet = Skip", plan.skip_reason)

    def test_nothing_missing(self):
        src = [_spec(1, "3d"), _spec(7, "sheet")]
        dst = [_spec(11, "3d"), _spec(17, "sheet")]
        plan = dc.plan_for_target(src, dst, dc.CloneSettings(existing=dc.EXISTING_ADD))
        self.assertEqual(plan.to_create, [])
        self.assertEqual(plan.paired, {1: 11, 7: 17})
        self.assertEqual(plan.skip_reason, u"")       # annotations still run on paired views

    def test_creation_order_parent_before_callout_sheet_last(self):
        ordered = dc.creation_order(list(reversed(_source())))
        kinds = [s.kind for s in ordered]
        self.assertEqual(kinds[-1], "sheet")
        self.assertLess(kinds.index("elevation"), kinds.index("manual_callout"))
        self.assertLess(kinds.index("manual_section"), kinds.index("partlist"))

    def test_view_key_manual_swaps_marks(self):
        a = _spec(4, "manual_callout", name=u"C-01 Detail 1")
        b = _spec(104, "manual_callout", name=u"C-02 Detail 1")
        self.assertEqual(dc.view_key(a, u"C-01", u"C-02"), dc.view_key(b, u"C-01", u"C-02"))
        self.assertNotEqual(dc.view_key(a, u"C-01", u"C-02"),
                            dc.view_key(_spec(5, "manual_callout", name=u"C-01 Detail 2"), u"C-01", u"C-02"))


class TestHelpers(unittest.TestCase):

    def test_near_key_snaps_to_tolerance(self):
        self.assertEqual(dc.near_key(1, 2, (103.0, -4.0, 0.0), 10.0),
                         dc.near_key(1, 2, (98.0, 1.0, 4.0), 10.0))
        self.assertNotEqual(dc.near_key(1, 2, (103.0, 0.0, 0.0), 10.0),
                            dc.near_key(1, 3, (103.0, 0.0, 0.0), 10.0))

    def test_mean_offset(self):
        self.assertIsNone(dc.mean_offset([]))
        self.assertEqual(dc.mean_offset([(1, 2, 3), None, (3, 2, 1)]), (2.0, 2.0, 2.0))

    def test_deletable_categories_never_touch_view_machinery(self):
        names = dc.deletable_categories()
        for name in dc.T2_IGNORED_CATEGORY_NAMES:
            self.assertNotIn(name, names)
        for name in ("OST_Views", "OST_Sheets", "OST_Viewports", "OST_TitleBlocks"):
            self.assertNotIn(name, names)
        self.assertIn("OST_Dimensions", names)
        self.assertIn("OST_TextNotes", names)

    def test_kind_label_and_summary(self):
        self.assertEqual(dc.kind_label(_spec(2, "elevation", "ElevationFront")),
                         u"Elevation · ElevationFront")
        self.assertEqual(dc.kind_label(_spec(4, "manual_callout")), u"Callout")
        text = dc.summarize_specs([s for s in _source() if not s.skip_reason])
        self.assertIn(u"1 callouts", text.replace(u"1 callouts", u"1 callouts"))
        self.assertIn(u"Sheet", text)

    def test_confirm_text(self):
        message, details, ok_text = dc.confirm_text(u"C-01", _source(), 3, dc.CloneSettings())
        self.assertIn(u"C-01", message)
        self.assertIn(u"3 assemblies", ok_text)
        self.assertIn(u"18 views", message)                 # 6 clonable non-sheet x 3
        self.assertIn(u"3 sheets", message)
        self.assertIn(u"Nothing is deleted", details)
        _, details, _ = dc.confirm_text(u"C-01", _source(), 1,
                                        dc.CloneSettings(existing=dc.EXISTING_REPLACE), 12)
        self.assertIn(u"12 annotation elements", details)
        self.assertIn(u"DELETED", details)
        message, _, _ = dc.confirm_text(u"C-01", _source(), 1, dc.CloneSettings(views=dc.SKIP))
        self.assertIn(u"Views are not created", message)

    def test_outcome_summary_and_tally_text(self):
        out = dc.TargetOutcome(10, "C-02")
        out.views_created = 5
        out.sheet_number = "S-01-C-02"
        out.copied = 3
        out.deleted = 4
        out.tally.tag(True)
        out.tally.tags_created = 2
        out.tally.spot(True)
        out.tally.exists = 1
        out.unmatched.append((1, "x"))
        text = dc.outcome_summary(out)
        self.assertIn("5 views, sheet S-01-C-02", text)
        self.assertIn("4 deleted", text)
        self.assertIn("tags 1 / 1 (+2 created)", text)
        self.assertIn("spots 1 / 1", text)
        self.assertIn("1 already existed", text)
        totals = dc.summarize_outcomes([out])
        self.assertEqual((totals["deleted"], totals["created_tags"], totals["unmatched"]), (4, 2, 1))
        strip = dc.tally_text([out])
        self.assertIn("+2 created", strip)
        self.assertIn("spots 1 / 1", strip)
        self.assertIn("4 deleted", strip)

    def test_reasons_are_sentences(self):
        for name in dir(dc):
            if name.startswith("R_"):
                value = getattr(dc, name)
                self.assertTrue(isinstance(value, str) and value.strip(), name)


if __name__ == '__main__':
    unittest.main()
