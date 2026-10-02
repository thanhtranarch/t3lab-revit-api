# -*- coding: utf-8 -*-
"""Workset Manager: natural sort + inline rename, tested without Revit/WPF.

Run: python3 dev/test_workset_naming.py
"""
import ast
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / 'T3Lab.extension' / 'lib'
sys.path.insert(0, str(LIB))

from Services.workset_naming import (  # noqa: E402
    INVALID_NAME_CHARS, natural_sort_key, sort_names, validate_workset_rename)


class NaturalSortTests(unittest.TestCase):
    def test_numbered_prefixes_before_text_and_in_numeric_order(self):
        names = ["ARC_Floor", "10_Do not use_OFF", "02_Link Architecture Models_OFF",
                 "01_Shared Levels and Grids_CORE_OFF", "09_Link MEP Models_OFF",
                 "Workset1"]
        self.assertEqual(sort_names(names), [
            "01_Shared Levels and Grids_CORE_OFF", "02_Link Architecture Models_OFF",
            "09_Link MEP Models_OFF", "10_Do not use_OFF", "ARC_Floor", "Workset1"])

    def test_numbers_inside_names(self):
        self.assertEqual(sort_names(["Level 10", "Level 2", "Level 1"]),
                         ["Level 1", "Level 2", "Level 10"])
        self.assertEqual(sort_names(["L2 Zone 10", "L2 Zone 9", "L10 Zone 1"]),
                         ["L2 Zone 9", "L2 Zone 10", "L10 Zone 1"])

    def test_leading_zeros_compare_by_value_and_stay_deterministic(self):
        self.assertEqual(sort_names(["10_A", "2_A", "01_A"]), ["01_A", "2_A", "10_A"])
        # Same value: total order, independent of input order.
        self.assertEqual(sort_names(["1_A", "01_A"]), sort_names(["01_A", "1_A"]))

    def test_case_insensitive(self):
        self.assertEqual(sort_names(["arc_b", "ARC_a", "Arc_C"]), ["ARC_a", "arc_b", "Arc_C"])
        self.assertEqual(sort_names(["b", "A"]), ["A", "b"])

    def test_names_without_numbers(self):
        self.assertEqual(sort_names(["Railing", "Ceiling", "Misc"]),
                         ["Ceiling", "Misc", "Railing"])

    def test_identical_prefix_shorter_first(self):
        self.assertEqual(sort_names(["ARC_Walls 2", "ARC_Walls", "ARC_Walls 10"]),
                         ["ARC_Walls", "ARC_Walls 2", "ARC_Walls 10"])

    def test_descending_and_empty(self):
        self.assertEqual(sort_names(["Level 2", "Level 10"], descending=True),
                         ["Level 10", "Level 2"])
        self.assertEqual(natural_sort_key(None), natural_sort_key(""))

    def test_mixed_keys_never_compare_str_with_int(self):
        names = ["1", "a", "1a", "a1", "", "_1", "1_", "A01b02", "a1b2"]
        sort_names(names)       # raises TypeError if the key mixed types


class RenameValidationTests(unittest.TestCase):
    EXISTING = ["ARC_Floor", "ARC_Ceiling", "01_Shared Levels"]

    def check(self, new, unique=None, old="ARC_Floor"):
        return validate_workset_rename(old, new, self.EXISTING, unique)

    def test_valid_name_is_trimmed(self):
        self.assertEqual(self.check("  ARC_Floor Finish  "), (True, "ARC_Floor Finish", ""))

    def test_empty_or_blank_rejected_with_message(self):
        for raw in ("", "   ", None):
            ok, _, msg = self.check(raw)
            self.assertFalse(ok)
            self.assertIn("cannot be empty", msg)
            self.assertIn("ARC_Floor", msg)      # where
            self.assertIn("Esc", msg)            # next

    def test_unchanged_is_silent_no_op(self):
        self.assertEqual(self.check(" ARC_Floor "), (False, "ARC_Floor", ""))

    def test_duplicate_is_case_insensitive(self):
        ok, _, msg = self.check("arc_ceiling")
        self.assertFalse(ok)
        self.assertIn("'ARC_Ceiling' already exists", msg)

    def test_case_only_change_of_same_workset_allowed(self):
        unique = Mock(return_value=False)    # Revit may see the old name as taken
        self.assertEqual(self.check("arc_floor", unique), (True, "arc_floor", ""))
        unique.assert_not_called()

    def test_invalid_characters(self):
        for ch in INVALID_NAME_CHARS:
            ok, _, msg = self.check("ARC" + ch + "X")
            self.assertFalse(ok, ch)
            self.assertIn("does not allow", msg)
        ok, _, msg = self.check("ARC\tX")
        self.assertFalse(ok)
        self.assertIn("\\x09", msg)

    def test_revit_uniqueness_backstop(self):
        ok, _, msg = self.check("ARC_New", Mock(return_value=False))
        self.assertFalse(ok)
        self.assertIn("Revit reports", msg)
        self.assertEqual(self.check("ARC_New", Mock(return_value=True))[0], True)
        # A failing API probe does not block the rename (local checks already ran).
        self.assertTrue(self.check("ARC_New", Mock(side_effect=RuntimeError))[0])


# ── rename_workset (service) with a simulated Revit API ────────────────────
STATUS = SimpleNamespace(Uninitialized='Uninitialized', Started='Started',
                         Committed='Committed', RolledBack='RolledBack', Pending='Pending')


class FakeTransaction:
    def __init__(self, doc, name):
        self.name = name
        self.status = STATUS.Uninitialized
        self.rollbacks = 0
        self.disposed = False
        self.outcome = STATUS.Committed
        self.options = Mock()

    def Start(self):
        self.status = STATUS.Started

    def Commit(self):
        self.status = self.outcome
        return self.status

    def GetStatus(self):
        return self.status

    def HasStarted(self):
        return self.status != STATUS.Uninitialized

    def HasEnded(self):
        return self.status in (STATUS.Committed, STATUS.RolledBack)

    def RollBack(self):
        assert self.status == STATUS.Started
        self.rollbacks += 1
        self.status = STATUS.RolledBack

    def Dispose(self):
        self.disposed = True

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        self.options = options


def load_service(**deps):
    path = LIB / 'Services' / 'workset_service.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    from Snippets._compat import disposing
    scope = dict(deps, TransactionStatus=STATUS, disposing=disposing,
                 eid_value=lambda i: i, logger=Mock())
    exec(compile(tree, str(path), 'exec'), scope)
    return SimpleNamespace(**scope)


class RenameServiceTests(unittest.TestCase):
    def setUp(self):
        self.made = []

        def factory(doc, name):
            t = FakeTransaction(doc, name)
            self.made.append(t)
            return t

        self.table = Mock()
        self.svc = load_service(Transaction=factory, WorksetTable=self.table)
        self.ws = SimpleNamespace(Name='ARC_Floor', Id=7, Owner='', IsEditable=True)
        self.svc.rename_workset.__globals__['get_user_worksets'] = lambda doc: [self.ws]
        self.doc = SimpleNamespace(Application=SimpleNamespace(Username='me'))

    def test_success_one_named_transaction(self):
        self.assertEqual(self.svc.rename_workset(self.doc, 7, 'ARC_New'), (True, ''))
        self.table.RenameWorkset.assert_called_once_with(self.doc, 7, 'ARC_New')
        self.assertEqual([t.name for t in self.made], ['Rename Workset'])
        self.assertTrue(self.made[0].disposed)

    def test_api_error_rolls_back_and_reports(self):
        self.table.RenameWorkset.side_effect = RuntimeError('Name rejected')
        ok, msg = self.svc.rename_workset(self.doc, 7, 'ARC_New')
        self.assertFalse(ok)
        self.assertIn('Name rejected', msg)
        self.assertEqual(self.made[0].rollbacks, 1)

    def test_rolled_back_commit_is_failure(self):
        def factory(doc, name):
            t = FakeTransaction(doc, name)
            t.outcome = STATUS.RolledBack
            self.made.append(t)
            return t
        self.svc.rename_workset.__globals__['Transaction'] = factory
        ok, msg = self.svc.rename_workset(self.doc, 7, 'ARC_New')
        self.assertFalse(ok)
        self.assertIn('RolledBack', msg)

    def test_owned_by_other_user_reports_owner_without_transaction(self):
        self.ws.Owner = 'alice'
        ok, msg = self.svc.rename_workset(self.doc, 7, 'ARC_New')
        self.assertFalse(ok)
        self.assertIn('owned by alice', msg)
        self.assertEqual(self.made, [])
        self.table.RenameWorkset.assert_not_called()

    def test_owned_by_me_is_allowed(self):
        self.ws.Owner = 'ME'
        self.assertTrue(self.svc.rename_workset(self.doc, 7, 'ARC_New')[0])

    def test_missing_workset(self):
        ok, msg = self.svc.rename_workset(self.doc, 99, 'ARC_New')
        self.assertFalse(ok)
        self.assertIn('Refresh', msg)
        self.assertEqual(self.made, [])


# ── XAML / dialog wiring ────────────────────────────────────────────────────
P = '{http://schemas.microsoft.com/winfx/2006/xaml/presentation}'


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = ET.parse(LIB / 'GUI' / 'Tools' / 'ManaWorkset.xaml').getroot()
        cls.grid = next(g for g in root.iter(P + 'DataGrid')
                        if g.get('{http://schemas.microsoft.com/winfx/2006/xaml}Name') == 'ws_grid')
        cls.dialog = (LIB / 'GUI' / 'ManaWorksetDialog.py').read_text(encoding='utf-8')

    def test_only_name_column_is_editable(self):
        self.assertEqual(self.grid.get('IsReadOnly'), 'False')
        cols = list(self.grid.find(P + 'DataGrid.Columns'))
        editable = [c for c in cols if c.get('IsReadOnly') != 'True']
        self.assertEqual(len(editable), 1)
        self.assertEqual(editable[0].get('SortMemberPath'), 'Name')
        self.assertIsNotNone(editable[0].find(P + 'DataGridTemplateColumn.CellEditingTemplate'))

    def test_edit_events_wired_to_existing_handlers(self):
        for event in ('MouseDoubleClick', 'PreviewKeyDown', 'BeginningEdit', 'CellEditEnding'):
            handler = self.grid.get(event)
            self.assertTrue(handler, event)
            self.assertIn('def %s(self, sender, e)' % handler, self.dialog)

    def test_rename_tooltip(self):
        tips = [n.get('ToolTip') for n in self.grid.iter() if n.get('ToolTip')]
        self.assertIn('Double-click or press F2 to rename', tips)


if __name__ == '__main__':
    unittest.main(verbosity=1)
