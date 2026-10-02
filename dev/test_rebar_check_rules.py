# -*- coding: utf-8 -*-
"""Tests for the Revit-free rules in Snippets/_rebar_check.py (Rebar Check): one
test per check id on fake RebarRecord / HostRecord / AssemblyRecord lists, the
A5 "Manual" rows, the A8 scope filter, the summary strip and the fix plan.
Run: python dev/test_rebar_check_rules.py
"""
import os
import sys
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

# Same coverage as dev/test_group_manager.py: the Vietnamese letters that carry a
# diacritic. UI text is English (rule 12), so none may reach a user-facing string.
VN_CHARS = ("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữự"
            "ỳýỷỹỵđ")


def _load_module(name):
    """Exec Snippets/<name>.py with the Revit and .NET imports stubbed out.

    Tests the shipped source rather than a copy; same technique as
    dev/test_group_manager.py and dev/test_assembly_rules.py.
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


rc = _load_module('_rebar_check')
from Snippets._assembly import (AssemblyRecord, HostRecord, RebarRecord, Result,  # noqa: E402
                                SKIP_IN_GROUP)


def bar(id, host=100, asm=-1, partition=u"P1", number=u"1", shape=u"Shape 01",
        bar_type=u"T12", qty=10, kind="Rebar", driven=True, in_group=False, link=False):
    return RebarRecord(id=id, kind=kind, host_id=host, assembly_id=asm,
                       partition=partition, number=number, shape=shape,
                       bar_type=bar_type, quantity=qty, is_shape_driven=driven,
                       in_group=in_group, is_link=link)


def host(id=100, asm=-1, valid=True, name=u"Beam B1"):
    return HostRecord(id=id, name=name, assembly_id=asm, is_valid_host=valid)


def assembly(id, mark=u"C-01", type_id=1, views=(), sheets=(), category=u"Structural Columns"):
    return AssemblyRecord(id=id, mark=mark, type_id=type_id, view_ids=list(views),
                          sheet_ids=list(sheets), naming_category=category)


def only(issues, check):
    return [i for i in issues if i.check == check]


class TestHostCheck(unittest.TestCase):

    def test_missing_host_element(self):
        issues = rc.check_hosts([bar(1, host=999)], {100: host()})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].check, rc.CHECK_HOST)
        self.assertEqual(issues[0].element_id, 1)
        self.assertIn("999", issues[0].detail)
        self.assertIn("no longer exists", issues[0].detail)
        self.assertEqual(issues[0].fix, rc.FIX_MANUAL)

    def test_no_host_recorded(self):
        issues = rc.check_hosts([bar(1, host=-1)], {})
        self.assertEqual(len(issues), 1)
        self.assertIn("No host recorded", issues[0].detail)

    def test_host_that_cannot_host_rebar(self):
        issues = rc.check_hosts([bar(1)], {100: host(valid=False)})
        self.assertEqual(len(issues), 1)
        self.assertIn("can no longer host", issues[0].detail)

    def test_good_host_is_clean(self):
        self.assertEqual(rc.check_hosts([bar(1)], {100: host()}), [])

    def test_coupler_with_unknown_host_is_not_flagged(self):
        coupler = bar(1, host=-1, kind="RebarCoupler")
        self.assertEqual(rc.check_hosts([coupler], {}), [])
        gone = bar(2, host=999, kind="RebarCoupler")
        self.assertEqual(len(rc.check_hosts([gone], {})), 1)


class TestMemberCheck(unittest.TestCase):

    def test_loose_bar_on_assembled_host_is_a_sync_fix(self):
        hosts = {100: host(asm=7)}
        issues = rc.check_members([bar(1, asm=-1)], hosts, {7: u"C-01"})
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue.check, rc.CHECK_MEMBER)
        self.assertEqual(issue.fix, rc.FIX_SYNC)
        self.assertEqual(issue.assembly_id, 7)
        self.assertEqual(issue.assembly_mark, u"C-01")
        self.assertIn("C-01", issue.detail)
        self.assertIn("not a member", issue.detail)

    def test_member_of_the_right_assembly_is_clean(self):
        hosts = {100: host(asm=7)}
        self.assertEqual(rc.check_members([bar(1, asm=7)], hosts, {7: u"C-01"}), [])

    def test_host_outside_any_assembly_is_clean(self):
        self.assertEqual(rc.check_members([bar(1, asm=-1)], {100: host(asm=-1)}), [])

    def test_bar_in_another_assembly_is_manual(self):
        hosts = {100: host(asm=7)}
        issues = rc.check_members([bar(1, asm=8)], hosts, {7: u"C-01", 8: u"C-02"})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].fix, rc.FIX_MANUAL)
        self.assertIn("C-02", issues[0].detail)
        self.assertIn("remove it there first", issues[0].detail)

    def test_group_and_link_rows_are_manual(self):
        hosts = {100: host(asm=7)}
        grouped = rc.check_members([bar(1, in_group=True)], hosts, {7: u"C-01"})[0]
        linked = rc.check_members([bar(2, link=True)], hosts, {7: u"C-01"})[0]
        self.assertEqual(grouped.fix, rc.FIX_MANUAL)
        self.assertEqual(grouped.fix_reason, SKIP_IN_GROUP)
        self.assertIn("ungroup", grouped.detail)
        self.assertEqual(linked.fix, rc.FIX_MANUAL)
        self.assertIn("linked model", linked.detail)
        # A linked bar with no partition cannot be assigned either.
        unpartitioned = rc.check_partitions([bar(3, partition=u"", link=True)])[0]
        self.assertEqual(unpartitioned.fix, rc.FIX_MANUAL)
        self.assertFalse(unpartitioned.fixable)


class TestDrawingCheck(unittest.TestCase):

    def test_no_views(self):
        issues = rc.check_drawings([assembly(7, views=[], sheets=[])])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].check, rc.CHECK_DRAWING)
        self.assertEqual(issues[0].detail, u"No views")
        self.assertEqual(issues[0].element_id, 7)
        self.assertEqual(issues[0].assembly_id, 7)
        self.assertEqual(issues[0].kind, rc.KIND_ASSEMBLY)

    def test_views_but_no_sheet(self):
        issues = rc.check_drawings([assembly(7, views=[1, 2], sheets=[])])
        self.assertEqual([i.detail for i in issues], [u"Views but no sheet"])

    def test_views_and_sheet_is_clean(self):
        self.assertEqual(rc.check_drawings([assembly(7, views=[1], sheets=[2])]), [])

    def test_sibling_with_views_is_named(self):
        done = assembly(7, type_id=5, views=[1], sheets=[2])
        bare = assembly(8, type_id=5)
        issues = rc.check_drawings([done, bare])
        self.assertEqual(len(issues), 1)
        self.assertIn("1 other instance of this type has them", issues[0].detail)


class TestDuplicateNumber(unittest.TestCase):

    def test_same_number_different_bar_type_is_flagged_without_geometry(self):
        rows = [bar(1, bar_type=u"T12"), bar(2, bar_type=u"T16")]
        issues = rc.check_duplicates(rows)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].check, rc.CHECK_DUP)
        self.assertEqual(sorted(issues[0].element_ids), [1, 2])
        self.assertIn("Number 1 in P1 used by 2 different bars", issues[0].detail)

    def test_same_number_different_legs_is_flagged(self):
        rows = [bar(1), bar(2), bar(3)]
        legs = {1: ((400, 90), (600, 0)), 2: ((400, 90), (600, 0)), 3: ((400, 90), (650, 0))}
        issues = rc.check_duplicates(rows, legs.get)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].element_id, 3, "the odd bar is reported first")
        self.assertEqual(issues[0].element_ids, [1, 2, 3])

    def test_same_number_same_legs_is_not_an_issue(self):
        rows = [bar(1), bar(2)]
        legs = {1: ((400, 90), (600, 0)), 2: ((400, 90), (600, 0))}
        self.assertEqual(rc.check_duplicates(rows, legs.get), [])

    def test_unreadable_centerline_never_splits_a_group(self):
        rows = [bar(1), bar(2), bar(3)]
        legs = {1: ((400, 90), (600, 0)), 2: ((400, 90), (600, 0))}   # 3 unread
        self.assertEqual(rc.check_duplicates(rows, legs.get), [])

    def test_different_numbers_or_partitions_are_separate_groups(self):
        rows = [bar(1, number=u"1"), bar(2, number=u"2", bar_type=u"T16"),
                bar(3, partition=u"P2", number=u"1", bar_type=u"T16")]
        self.assertEqual(rc.check_duplicates(rows), [])

    def test_bars_without_a_number_and_non_bars_are_ignored(self):
        rows = [bar(1, number=u""), bar(2, number=u"", bar_type=u"T16"),
                bar(3, kind="FabricSheet", bar_type=u"A"),
                bar(4, kind="FabricSheet", bar_type=u"B")]
        self.assertEqual(rc.check_duplicates(rows), [])

    def test_legs_needed_only_for_groups_that_look_identical(self):
        rows = [bar(1), bar(2),                                     # identical -> need legs
                bar(3, number=u"2"), bar(4, number=u"2", bar_type=u"T16"),   # already differ
                bar(5, number=u"3")]                                 # single bar
        self.assertEqual(sorted(rc.legs_needed(rows)), [1, 2])

    def test_canonical_legs_ignore_the_direction_the_bar_was_drawn(self):
        forward = [(400.2, 90.0), (600.4, -90.0), (300.0, 0.0)]
        backward = [(300.0, 90.0), (600.0, -90.0), (400.0, 0.0)]
        self.assertEqual(rc.canonical_legs(forward), rc.canonical_legs(backward))
        self.assertEqual(rc.canonical_legs([(1000.0, 0.0)]), ((1000, 0),))
        self.assertNotEqual(rc.canonical_legs([(400.0, 90.0), (600.0, 0.0)]),
                            rc.canonical_legs([(400.0, 90.0), (650.0, 0.0)]))


class TestPartitionAndShape(unittest.TestCase):

    def test_empty_partition_is_an_assign_fix(self):
        issues = rc.check_partitions([bar(1, partition=u""), bar(2, partition=u"  "),
                                      bar(3, partition=u"P1")])
        self.assertEqual([i.element_id for i in issues], [1, 2])
        self.assertTrue(all(i.fix == rc.FIX_ASSIGN for i in issues))
        self.assertTrue(all(i.check == rc.CHECK_PARTITION for i in issues))

    def test_only_rebar_elements_need_a_partition(self):
        rows = [bar(1, partition=u"", kind="RebarInSystem"),
                bar(2, partition=u"", kind="AreaReinforcement"),
                bar(3, partition=u"", kind="RebarCoupler")]
        self.assertEqual(rc.check_partitions(rows), [])

    def test_unknown_shape(self):
        rows = [bar(1, shape=u"", driven=True), bar(2, shape=u"", driven=False),
                bar(3, shape=u"Shape 01", driven=True)]
        issues = rc.check_shapes(rows)
        self.assertEqual([i.element_id for i in issues], [1])
        self.assertEqual(issues[0].fix, rc.FIX_MANUAL)
        self.assertIn("Shape not recognised", issues[0].detail)


class TestBoundingBox(unittest.TestCase):
    HOST = ((0.0, 0.0, 0.0), (400.0, 400.0, 3000.0))

    def test_outside_distance(self):
        inside = ((50.0, 50.0, 10.0), (350.0, 350.0, 2900.0))
        sticking = ((50.0, 50.0, 10.0), (350.0, 350.0, 3120.0))
        self.assertEqual(rc.bbox_outside_mm(inside, self.HOST), 0.0)
        self.assertEqual(rc.bbox_outside_mm(sticking, self.HOST), 120.0)

    def test_tolerance_is_at_least_50_mm_and_follows_cover(self):
        near = ((0.0, 0.0, 0.0), (450.0, 400.0, 3000.0))      # 50 mm out
        far = ((0.0, 0.0, 0.0), (500.0, 400.0, 3000.0))       # 100 mm out
        rows = [bar(1), bar(2)]
        boxes = {1: near, 2: far}
        hosts = {100: self.HOST}
        issues = rc.check_bbox(rows, boxes, hosts, {100: 0.0})
        self.assertEqual([i.element_id for i in issues], [2])
        self.assertIn("100 mm outside host 100", issues[0].detail)
        # A 75 mm host cover widens the tolerance: 100 mm is still flagged, 60 is not.
        wide = rc.check_bbox(rows, boxes, hosts, {100: 120.0})
        self.assertEqual(wide, [])

    def test_missing_boxes_and_non_bars_are_skipped(self):
        rows = [bar(1), bar(2, kind="FabricSheet")]
        sticking = ((0.0, 0.0, 0.0), (900.0, 400.0, 3000.0))
        issues = rc.check_bbox(rows, {2: sticking}, {100: self.HOST})
        self.assertEqual(issues, [])


class TestScopeAndFilter(unittest.TestCase):

    def setUp(self):
        self.issues = [
            rc.Issue(rc.CHECK_MEMBER, 1, u"Hosted by Beam B1 (100) in C-01 but not a member",
                     category=u"Rebar", assembly_id=7, assembly_mark=u"C-01",
                     partition=u"P1", number=u"4", fix=rc.FIX_SYNC),
            rc.Issue(rc.CHECK_PARTITION, 2, u"No partition", category=u"Rebar",
                     fix=rc.FIX_ASSIGN),
            rc.Issue(rc.CHECK_DRAWING, 7, u"No views", category=u"Structural Columns",
                     assembly_id=7, assembly_mark=u"C-01"),
        ]

    def test_scope_filter_in_assemblies_loose(self):
        every = rc.filter_issues(self.issues)
        inside = rc.filter_issues(self.issues, scope=rc.SCOPE_IN_ASSEMBLY)
        loose = rc.filter_issues(self.issues, scope=rc.SCOPE_LOOSE)
        self.assertEqual([i.element_id for i in every], [1, 2, 7])
        self.assertEqual([i.element_id for i in inside], [1, 7])
        self.assertEqual([i.element_id for i in loose], [2])
        self.assertEqual(len(inside) + len(loose), len(every))

    def test_check_chip_filters_one_check(self):
        got = rc.filter_issues(self.issues, check=rc.CHECK_DRAWING)
        self.assertEqual([i.element_id for i in got], [7])

    def test_search_matches_labels_ids_and_text(self):
        self.assertEqual([i.element_id for i in rc.filter_issues(self.issues, text=u"c-01")], [1, 7])
        self.assertEqual([i.element_id for i in rc.filter_issues(self.issues, text=u"no partition")], [2])
        self.assertEqual([i.element_id for i in rc.filter_issues(self.issues, text=u"sync into")], [1])
        self.assertEqual(rc.filter_issues(self.issues, text=u"zzz"), [])

    def test_filters_combine(self):
        got = rc.filter_issues(self.issues, check=rc.CHECK_MEMBER,
                               scope=rc.SCOPE_IN_ASSEMBLY, text=u"beam")
        self.assertEqual([i.element_id for i in got], [1])


class TestRunChecks(unittest.TestCase):

    def test_every_check_is_reachable_and_sorted(self):
        rows = [bar(1, host=999),                                   # host
                bar(2, host=100, asm=-1),                           # member (host in asm 7)
                bar(3, partition=u"", number=u""),                  # partition
                bar(4, shape=u"", driven=True, number=u"9"),        # shape
                bar(5, number=u"8"), bar(6, number=u"8", bar_type=u"T16")]   # dup
        hosts = {100: host(asm=7)}
        asms = [assembly(7, mark=u"C-01")]
        issues = rc.run_checks(rows, hosts, asms)
        seen = [i.check for i in issues]
        for check in (rc.CHECK_HOST, rc.CHECK_MEMBER, rc.CHECK_DRAWING, rc.CHECK_DUP,
                      rc.CHECK_PARTITION, rc.CHECK_SHAPE):
            self.assertIn(check, seen)
        order = [rc.CHECK_ORDER.index(c) for c in seen]
        self.assertEqual(order, sorted(order))
        self.assertNotIn(rc.CHECK_BBOX, seen, "bbox needs the deep data")

    def test_unread_hosts_skip_host_and_member_checks(self):
        # A stopped scan has no host list; every bar must not be called "host missing".
        rows = [bar(1, host=100), bar(2, host=999, partition=u"")]
        issues = rc.run_checks(rows, None, [])
        self.assertEqual([i.check for i in issues], [rc.CHECK_PARTITION])

    def test_deep_inputs_add_bbox_and_geometry_dup(self):
        rows = [bar(1), bar(2)]
        hosts = {100: host()}
        boxes = rc.BoxData({1: ((0, 0, 0), (900, 400, 3000)), 2: ((0, 0, 0), (100, 100, 100))},
                           {100: ((0, 0, 0), (400, 400, 3000))}, {100: 0.0})
        legs = {1: ((400, 90), (600, 0)), 2: ((400, 90), (700, 0))}
        issues = rc.run_checks(rows, hosts, [], legs=legs, boxes=boxes)
        self.assertEqual([i.check for i in issues], [rc.CHECK_DUP, rc.CHECK_BBOX])


class TestSummaryAndFixPlan(unittest.TestCase):

    def test_summary_counts_flagged_elements_not_rows(self):
        rows = [bar(1), bar(2), bar(3), bar(4)]
        issues = [rc.Issue(rc.CHECK_PARTITION, 1, u"x", kind="Rebar"),
                  rc.Issue(rc.CHECK_SHAPE, 1, u"y", kind="Rebar"),
                  rc.Issue(rc.CHECK_DUP, 2, u"z", kind="Rebar", element_ids=[2, 3]),
                  rc.Issue(rc.CHECK_DRAWING, 7, u"No views", kind=rc.KIND_ASSEMBLY)]
        summary = rc.summarize(rows, issues, [assembly(7)])
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["issues"], 4)
        self.assertEqual(summary["flagged"], 3)
        self.assertEqual(summary["assemblies"], 1)
        self.assertEqual(summary["pass_rate"], 25)

    def test_empty_model_passes(self):
        self.assertEqual(rc.summarize([], [], [])["pass_rate"], 100)

    def test_plan_fix_groups_by_fix(self):
        sync_a = rc.Issue(rc.CHECK_MEMBER, 1, u"", assembly_id=7, fix=rc.FIX_SYNC)
        sync_b = rc.Issue(rc.CHECK_MEMBER, 2, u"", assembly_id=7, fix=rc.FIX_SYNC)
        sync_c = rc.Issue(rc.CHECK_MEMBER, 3, u"", assembly_id=8, fix=rc.FIX_SYNC)
        assign = rc.Issue(rc.CHECK_PARTITION, 4, u"", fix=rc.FIX_ASSIGN)
        plan = rc.plan_fix([sync_a, sync_b, sync_c, assign])
        self.assertEqual(plan.sync_assembly_ids, [7, 8])
        self.assertEqual(plan.sync_rebar_ids, [1, 2, 3])
        self.assertEqual(plan.assign_ids, [4])
        self.assertTrue(plan.fixable)

    def test_one_manual_row_blocks_the_fix(self):
        manual = rc.Issue(rc.CHECK_HOST, 5, u"", fix=rc.FIX_MANUAL)
        sync = rc.Issue(rc.CHECK_MEMBER, 1, u"", assembly_id=7, fix=rc.FIX_SYNC)
        plan = rc.plan_fix([sync, manual])
        self.assertFalse(plan.fixable)
        self.assertEqual(plan.manual, [manual])
        self.assertFalse(rc.plan_fix([]).fixable)

    def test_issue_element_ids_unique_in_row_order(self):
        issues = [rc.Issue(rc.CHECK_DUP, 2, u"", element_ids=[2, 3]),
                  rc.Issue(rc.CHECK_SHAPE, 3, u""), rc.Issue(rc.CHECK_SHAPE, 1, u"")]
        self.assertEqual(rc.issue_element_ids(issues), [2, 3, 1])


class TestText(unittest.TestCase):

    def test_count_formatting(self):
        self.assertEqual(rc.fmt_count(1286), u"1 286")
        self.assertEqual(rc.fmt_count(12), u"12")
        self.assertEqual(rc.plural(1, u"issue"), u"1 issue")
        self.assertEqual(rc.plural(2, u"assembly", u"assemblies"), u"2 assemblies")

    def test_empty_state_injects_the_count(self):
        clean = rc.empty_text(1286, 0)
        self.assertTrue(clean.startswith(u"No issues found — 1 286 rebar checked."))
        filtered = rc.empty_text(1286, 37)
        self.assertIn(u"37 issues", filtered)
        self.assertNotIn(u"checked", filtered)

    def test_ready_text(self):
        text = rc.ready_text({"total": 1286, "issues": 37, "assemblies": 12})
        self.assertEqual(text, u"Ready — 37 issues in 1 286 rebar, 12 assemblies")
        self.assertIn(u"no issues", rc.ready_text({"total": 5, "issues": 0, "assemblies": 1}))

    def test_fix_summary(self):
        rows = [Result(u"C-01", "ok", 7, u""), Result(u"C-02", "ok", 5, u""),
                Result(u"C-03", "skipped", 0, u""), Result(u"C-04", "failed", 0, u"")]
        self.assertEqual(rc.fix_summary(rows),
                         u"Synced 12 rebar elements into 2 assemblies · 1 skipped · 1 failed")

    def test_every_label_is_english(self):
        texts = list(rc.CHECK_LABELS.values()) + list(rc.FIX_LABELS.values()) \
            + list(rc.KIND_LABELS.values()) + [rc.ISOLATE_NEEDS_MODEL_VIEW]
        for text in texts:
            self.assertFalse(any(ch in VN_CHARS for ch in text.lower()), text)

    def test_every_check_has_a_label_and_a_place_in_the_order(self):
        self.assertEqual(sorted(rc.CHECK_ORDER), sorted(rc.CHECK_LABELS))
        self.assertEqual(set(rc.FIX_LABELS), {rc.FIX_SYNC, rc.FIX_ASSIGN, rc.FIX_MANUAL})


if __name__ == '__main__':
    unittest.main()
