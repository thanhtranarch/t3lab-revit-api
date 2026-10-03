# -*- coding: utf-8 -*-
"""Tests for the Revit-free rules in Snippets/_assembly.py: assembly candidate
filtering (A3/A5), batch planning, series names, type split report (A4),
selection expansion (A1) and the Result row.
Run: python dev/test_assembly_rules.py
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



class TestAssemblyRules(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.asm = _load_module('_assembly')

    def host(self, ident, **kwargs):
        return self.asm.HostRecord(id=ident, name="H%d" % ident, **kwargs)

    def bar(self, ident, host_id, **kwargs):
        return self.asm.RebarRecord(id=ident, host_id=host_id, **kwargs)

    # ── A5 / A3 ──────────────────────────────────────────────────────────────

    def test_filter_candidates_group_link_assembly(self):
        a = self.asm
        records = [self.host(1), self.host(2, in_group=True), self.host(3, is_link=True),
                   self.host(4, assembly_id=900), self.host(5, assembly_id=-1)]
        ok, skipped = a.filter_assembly_candidates(records)
        self.assertEqual([r.id for r in ok], [1, 5])
        self.assertEqual([(r.id, why) for r, why in skipped],
                         [(2, a.SKIP_IN_GROUP), (3, a.SKIP_IN_LINK),
                          (4, a.SKIP_IN_ASSEMBLY % 900)])

    def test_filter_allows_the_target_assembly(self):
        records = [self.bar(1, 10, assembly_id=900), self.bar(2, 10, assembly_id=901)]
        ok, skipped = self.asm.filter_assembly_candidates(records, allow_assembly_id=900)
        self.assertEqual([r.id for r in ok], [1])
        self.assertEqual(skipped[0][1], "already in assembly 901")

    def test_group_wins_over_link_and_assembly(self):
        record = self.host(1, in_group=True, is_link=True, assembly_id=5)
        _, skipped = self.asm.filter_assembly_candidates([record])
        self.assertEqual(skipped[0][1], self.asm.SKIP_IN_GROUP)

    def test_rebar_rows_follow_the_same_rule(self):
        ok, skipped = self.asm.filter_assembly_candidates(
            [self.bar(1, 10, in_group=True), self.bar(2, 10, is_link=True), self.bar(3, 10)])
        self.assertEqual([r.id for r in ok], [3])
        self.assertEqual(len(skipped), 2)

    # ── batch plan ───────────────────────────────────────────────────────────

    def test_plan_batch_one_per_host_vs_all(self):
        a = self.asm
        hosts = [self.host(1), self.host(2), self.host(3, in_group=True)]
        rebar = {1: [self.bar(11, 1), self.bar(12, 1)], 2: [self.bar(21, 2)]}

        plans = a.plan_batch_create(hosts, rebar, one_per_host=True)
        self.assertEqual([p.host_ids for p in plans], [[1], [2], []])
        self.assertEqual([p.rebar_ids for p in plans], [[11, 12], [21], []])
        self.assertEqual([p.naming_host_id for p in plans], [1, 2, 3])
        self.assertEqual(plans[2].skips, [(3, a.SKIP_IN_GROUP)])
        self.assertEqual(plans[0].label, "H1")

        plans = a.plan_batch_create(hosts, rebar, one_per_host=False)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].host_ids, [1, 2])
        self.assertEqual(plans[0].rebar_ids, [11, 12, 21])
        self.assertEqual(plans[0].naming_host_id, 1)
        self.assertEqual(plans[0].skips, [(3, a.SKIP_IN_GROUP)])

    def test_plan_excludes_rebar_that_cannot_join(self):
        a = self.asm
        rebar = {1: [self.bar(11, 1), self.bar(12, 1, assembly_id=77), self.bar(13, 1, in_group=True)]}
        plans = a.plan_batch_create([self.host(1)], rebar)
        self.assertEqual(plans[0].rebar_ids, [11])
        self.assertEqual(sorted(plans[0].skips),
                         [(12, "already in assembly 77"), (13, a.SKIP_IN_GROUP)])

    def test_plan_include_rebar_false(self):
        plans = self.asm.plan_batch_create([self.host(1)], {1: [self.bar(11, 1)]},
                                           include_rebar=False)
        self.assertEqual(plans[0].rebar_ids, [])

    def test_plan_rebar_listed_once(self):
        shared = self.bar(11, 1)
        plans = self.asm.plan_batch_create([self.host(1), self.host(2)],
                                           {1: [shared], 2: [shared]})
        self.assertEqual(plans[0].rebar_ids, [11])
        self.assertEqual(plans[1].rebar_ids, [])

    def test_plan_duplicate_hosts_planned_once(self):
        plans = self.asm.plan_batch_create([self.host(1), self.host(1)], {})
        self.assertEqual(len(plans), 1)

    def test_plan_all_hosts_skipped_keeps_the_reasons(self):
        a = self.asm
        plans = a.plan_batch_create([self.host(1, is_link=True), self.host(2, assembly_id=3)],
                                    {}, one_per_host=False)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].host_ids, [])
        self.assertEqual(plans[0].skips, [(1, a.SKIP_IN_LINK), (2, "already in assembly 3")])

    def test_plan_empty_input(self):
        self.assertEqual(self.asm.plan_batch_create([], {}), [])
        self.assertEqual(self.asm.plan_batch_create([], {}, one_per_host=False), [])

    # ── series names ─────────────────────────────────────────────────────────

    def test_series_names_padding_step(self):
        a = self.asm
        self.assertEqual(a.series_names(3, "C-", 1, 1, 3), ["C-001", "C-002", "C-003"])
        self.assertEqual(a.series_names(3, "C-", 10, 5, 2), ["C-10", "C-15", "C-20"])
        self.assertEqual(a.series_names(3, "B", 8, 1, 0), ["B8", "B9", "B10"])
        self.assertEqual(a.series_names(2, "", 1, 1, 4), ["0001", "0002"])
        self.assertEqual(a.series_names(0, "C-", 1, 1, 3), [])
        self.assertEqual(a.series_names(2, "C-", 1000, 1, 3), ["C-1000", "C-1001"])

    # ── A4 ───────────────────────────────────────────────────────────────────

    def test_diff_type_split_counts(self):
        a = self.asm
        # nothing changed
        self.assertEqual(a.diff_type_split({1: 10, 2: 10}, {1: 10, 2: 10}), (0, 0, []))
        # one of two identical assemblies renamed: Revit splits a new type off
        self.assertEqual(a.diff_type_split({1: 10, 2: 10}, {1: 10, 2: 11}), (1, 0, [2]))
        # the only instance of a type renamed to an existing name: types merge
        self.assertEqual(a.diff_type_split({1: 10, 2: 11}, {1: 11, 2: 11}), (0, 1, [1]))
        # a type renamed in place keeps its id: no change at all
        self.assertEqual(a.diff_type_split({1: 10, 2: 10}, {1: 10, 2: 10}), (0, 0, []))

    def test_diff_type_split_changed_ids_sorted_and_ignore_new_assemblies(self):
        a = self.asm
        split, merged, changed = a.diff_type_split({5: 1, 3: 1, 4: 1}, {5: 2, 3: 2, 4: 1, 99: 7})
        self.assertEqual(changed, [3, 5])
        self.assertEqual(split, 2)       # types 2 and 7 are new
        self.assertEqual(merged, 0)

    # ── A1 ───────────────────────────────────────────────────────────────────

    def test_expand_selection_assembly_to_members(self):
        a = self.asm
        hosts = {1: self.host(1), 2: self.host(2), 3: self.host(3), 4: self.host(4)}
        members = {100: [1, 2, 55], 200: [3, 2]}     # 55 is a rebar, not a host
        self.assertEqual(a.expand_selection(hosts, [100], members), [1, 2])
        self.assertEqual(a.expand_selection(hosts, [4, 100, 200], members), [4, 1, 2, 3])
        self.assertEqual(a.expand_selection(hosts, [1, 1, 100], members), [1, 2])
        self.assertEqual(a.expand_selection(hosts, [999], members), [])
        self.assertEqual(a.expand_selection(hosts, [], members), [])

    # ── Result ───────────────────────────────────────────────────────────────

    def test_result_statuses_only_ok_skipped_failed(self):
        a = self.asm
        for status in ("ok", "skipped", "failed"):
            row = a.Result("x", status, 1, "d")
            self.assertEqual((row.name, row.status, row.count, row.detail), ("x", status, 1, "d"))
        for bad in ("done", "OK", "", None, "stopped"):
            with self.assertRaises(ValueError):
                a.Result("x", bad)

    def test_result_defaults_and_tuple_behaviour(self):
        row = self.asm.Result("n", "skipped")
        self.assertEqual(tuple(row), ("n", "skipped", 0, ""))
        name, status, count, detail = row
        self.assertEqual(status, "skipped")

    def test_summarize_results(self):
        a = self.asm
        rows = [a.Result("a", "ok", 3), a.Result("b", "ok", 2), a.Result("c", "skipped"),
                a.Result("d", "failed")]
        self.assertEqual(a.summarize_results(rows),
                         {"ok": 2, "skipped": 1, "failed": 1, "count": 5})

    def test_skip_reasons_are_user_text(self):
        a = self.asm
        for text in (a.SKIP_IN_GROUP, a.SKIP_IN_LINK, a.SKIP_NOT_VALID, a.SKIP_NO_HOST,
                     a.SKIP_STOPPED):
            self.assertTrue(text.isascii() and text == text.strip())
        self.assertEqual(a.SKIP_IN_ASSEMBLY % 12, "already in assembly 12")

    # ── records ──────────────────────────────────────────────────────────────

    def test_records_have_safe_defaults_and_reject_typos(self):
        a = self.asm
        host = a.HostRecord()
        self.assertEqual((host.id, host.assembly_id, host.in_group, host.is_link), (-1, -1, False, False))
        record = a.AssemblyRecord(id=7)
        other = a.AssemblyRecord(id=8)
        record.members.append(1)
        self.assertEqual(other.members, [])            # no shared default list
        with self.assertRaises(TypeError):
            a.HostRecord(idd=1)
        bar = a.RebarRecord()
        self.assertEqual((bar.host_id, bar.system_id, bar.quantity), (-1, -1, 1))

    def test_module_imports_without_revit(self):
        import importlib
        for name in ('Autodesk', 'Autodesk.Revit', 'Autodesk.Revit.DB'):
            self.assertNotIn(name, sys.modules)
        importlib.import_module('Snippets._assembly')


if __name__ == '__main__':
    unittest.main()
