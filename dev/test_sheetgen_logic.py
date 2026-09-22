"""SheetGen control-flow regressions; no Revit/WPF installation required.

Executes shipped definitions against a simulated API. This does not verify
Revit geometry, PythonNet binding, UI dispatch or native failure processing.
Run: python dev/test_sheetgen_logic.py
"""
import ast
from contextlib import contextmanager
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


PATH = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI/SheetGenDialog.py'
STATUS = NS(Uninitialized='Uninitialized', Started='Started', Committed='Committed',
            RolledBack='RolledBack', Pending='Pending')


class FakeTransaction:
    def __init__(self, outcome='Committed', start_error=False):
        self.status = STATUS.Uninitialized
        self.outcome = outcome
        self.start_error = start_error
        self.rollbacks = 0
        self.disposed = False
        self.options = Mock()

    def Start(self):
        if self.start_error:
            raise RuntimeError('start failed')
        self.status = STATUS.Started
        return self.status

    def GetStatus(self):
        return self.status

    def Commit(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        self.status = self.outcome
        return self.status

    Assimilate = Commit

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


class SheetGenTests(unittest.TestCase):
    def setUp(self):
        self.transactions = []
        self.outcomes = {}
        self.doc = Mock()
        self.dialog = Mock()
        self.viewport = Mock()
        self.sheet = Mock()
        self.db = NS(TransactionStatus=STATUS, SubTransaction=lambda doc: self.factory(doc, 'sub'),
                     ViewPlan=NS(Create=Mock(side_effect=lambda *args: NS(Id=42))),
                     BuiltInParameter=NS(SHEET_NUMBER=1))
        collector = Mock()
        collector.OfClass.return_value = collector
        collector.WhereElementIsNotElementType.return_value = collector
        collector.ToElements.return_value = []
        scope = dict(contextmanager=contextmanager, DB=self.db, doc=self.doc,
                     Transaction=self.factory, TransactionGroup=self.factory,
                     T3WPFWindow=object, TaskDialog=self.dialog, logger=Mock(),
                     FilteredElementCollector=Mock(return_value=collector),
                     View=object, ViewSheet=self.sheet, Viewport=self.viewport,
                     ViewType=NS(FloorPlan=1, CeilingPlan=2, DrawingSheet=3),
                     XYZ=lambda *args: args, math=math, forms=Mock(), revit=Mock(),
                     ElevationMarker=NS(CreateElevationMarker=Mock(return_value=Mock())),
                     ElementTransformUtils=Mock())
        tree = ast.parse(PATH.read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))]
        exec(compile(tree, str(PATH), 'exec'), scope)
        self.module = NS(**scope)
        self.window = object.__new__(self.module.CreateRoomPlanWindow)
        w = self.window
        self.room = NS(Name='Kitchen', Number='101', RoomType='', GenQty=1, Element=Mock())
        self.room.Element.get_BoundingBox.return_value = NS(Min=NS(X=0,Y=0), Max=NS(X=10,Y=10))
        w._get_selected_rooms = Mock(return_value=[self.room])
        for name, checked in [('chk_floor_plan', True), ('chk_ceiling_plan', False),
                              ('chk_elevations', False), ('chk_layout_on_sheet', False)]:
            setattr(w, name, NS(IsChecked=checked))
        w._floor_plan_type_id = 10
        w._ceiling_plan_type_id = 20
        w._elevation_type_id = 30
        w.cmb_titleblock = NS(SelectedItem='A1')
        w._titleblock_map = {'A1': 40}
        w.txt_offset = NS(Text='1')
        for name in ('plan', 'rcp', 'elev'):
            setattr(w, 'cmb_' + name + '_template', NS(SelectedItem='<None>'))
            setattr(w, '_' + name + '_template_map', {})
        w.chk_cropbox_visible = NS(IsChecked=False)
        w._offset_bbox = Mock(return_value=object())
        w.begin_progress = Mock()
        w.end_progress = Mock()
        w.step_progress = Mock(return_value=True)
        w.is_cancelled = False
        w.cmb_generated_sheets = Mock()
        w.btn_open_sheet = Mock()
        w.rdo_layout_combined = NS(IsChecked=True)
        w.status_text = NS(Text='')
        w.Close = Mock()
        w._find_plan_view_for_level = Mock(return_value=NS(Scale=50, Name='Host'))
        w._resolve_elevation_type = Mock(return_value=(30, 'Interior', 4))
        w._log_marker_debug = Mock()
        w._finalize_elevation_name = Mock()
        w._create_interior_elevation_view = Mock(side_effect=lambda *a: NS(Id=99))

    def factory(self, doc, name):
        tx = FakeTransaction(self.outcomes.get(name, STATUS.Committed))
        self.transactions.append((name, tx))
        return tx

    def run_batch(self):
        self.window.create_plans_clicked(Mock(), None)

    def test_success_is_counted_after_commit(self):
        self.run_batch()
        self.assertEqual(self.window.status_text.Text, '1 view(s) created.')
        self.assertEqual(self.transactions[0][1].status, STATUS.Committed)
        self.room.Element.get_BoundingBox.assert_called_once_with(None)
        self.window.end_progress.assert_called_once()

    def test_rejected_commit_does_not_count_view(self):
        self.outcomes['T3Lab: Create Floor Plan'] = STATUS.RolledBack
        self.run_batch()
        self.assertIn('0 view(s) created.', self.window.status_text.Text)
        self.assertIn('1 error(s)', self.window.status_text.Text)

    def test_template_failure_rolls_back_view(self):
        class BadView:
            def __setattr__(self, key, value):
                if key == 'ViewTemplateId':
                    raise RuntimeError('incompatible template')
                object.__setattr__(self, key, value)
        self.db.ViewPlan.Create.side_effect = lambda *a: BadView()
        self.window.cmb_plan_template.SelectedItem = 'Template'
        self.window._plan_template_map = {'Template': 88}
        self.run_batch()
        self.assertEqual(self.transactions[0][1].rollbacks, 1)
        self.assertIn('0 view(s)', self.window.status_text.Text)

    def test_ceiling_template_is_in_creation_transaction(self):
        self.window.chk_floor_plan.IsChecked = False
        self.window.chk_ceiling_plan.IsChecked = True
        self.window.cmb_rcp_template.SelectedItem = 'Template'
        self.window._rcp_template_map = {'Template': 88}
        self.run_batch()
        self.assertEqual([name for name, tx in self.transactions], ['T3Lab: Create Ceiling Plan'])
        self.assertIn('1 view(s)', self.window.status_text.Text)

    def test_validation_prevents_any_transaction(self):
        for invalid in ('NaN', 'inf', '-1', 'abc'):
            with self.subTest(offset=invalid):
                self.window.txt_offset.Text = invalid
                self.run_batch()
                self.assertFalse(self.transactions)
        self.window.txt_offset.Text = '1'
        for invalid in ('abc', -1, 1.5):
            with self.subTest(quantity=invalid):
                self.room.GenQty = invalid
                self.run_batch()
                self.assertFalse(self.transactions)

    def test_missing_titleblock_prevents_views(self):
        self.window.chk_layout_on_sheet.IsChecked = True
        self.window._titleblock_map = {}
        self.run_batch()
        self.assertFalse(self.transactions)
        self.assertIn('Title Block', self.dialog.Show.call_args.args[1])

    def test_missing_requested_view_type_prevents_writes(self):
        self.window._floor_plan_type_id = None
        self.run_batch()
        self.assertFalse(self.transactions)
        self.assertIn('Floor Plan', self.dialog.Show.call_args.args[1])

    def test_cancellation_skips_sheet_layout(self):
        self.window.chk_layout_on_sheet.IsChecked = True
        self.window._get_selected_rooms.return_value = [self.room, self.room]
        def step(index, message):
            self.window.is_cancelled = index == 1
            return not self.window.is_cancelled
        self.window.step_progress.side_effect = step
        self.window._layout_views_on_sheets = Mock()
        self.run_batch()
        self.window._layout_views_on_sheets.assert_not_called()
        self.assertIn('Cancelled', self.window.status_text.Text)
        self.assertEqual(len(self.transactions), 1)
        self.assertFalse(self.window.btn_open_sheet.IsEnabled)

    def test_unexpected_failure_restores_progress(self):
        self.room.Element.get_BoundingBox.side_effect = RuntimeError('deleted room')
        self.run_batch()
        self.window.end_progress.assert_called_once()
        self.assertFalse(self.window._sheetgen_running)
        self.assertIn('Creation interrupted', self.dialog.Show.call_args.args[1])

    def test_reentrant_click_does_not_reset_outer_progress(self):
        self.window._sheetgen_running = True
        self.run_batch()
        self.window.end_progress.assert_not_called()
        self.assertFalse(self.transactions)

    def test_failed_elevation_direction_has_subtransaction_rollback(self):
        self.window.chk_floor_plan.IsChecked = False
        self.window.chk_elevations.IsChecked = True
        self.window._create_interior_elevation_view.side_effect = [NS(Id=1), RuntimeError('crop failed'), NS(Id=3), NS(Id=4)]
        self.run_batch()
        self.assertIn('3 view(s) created.', self.window.status_text.Text)
        subs = [t for name,t in self.transactions if name == 'sub']
        self.assertEqual(len(subs), 4)
        self.assertEqual(subs[1].rollbacks, 1)

    def test_failed_elevation_parent_commit_discards_all_results(self):
        self.window.chk_floor_plan.IsChecked = False
        self.window.chk_elevations.IsChecked = True
        self.window.chk_layout_on_sheet.IsChecked = True
        self.outcomes['T3Lab: Create Interior Elevations'] = STATUS.RolledBack
        self.window._layout_views_on_sheets = Mock(return_value=[])
        self.run_batch()
        self.assertIn('0 view(s)', self.window.status_text.Text)
        result = self.window._layout_views_on_sheets.call_args.args[0]
        self.assertEqual(result['elevations'], [])

    def test_layout_failure_rolls_back_group(self):
        self.window._layout_room_sheets = Mock(side_effect=RuntimeError('viewport failed'))
        with self.assertRaisesRegex(RuntimeError, 'viewport failed'):
            self.window._layout_views_on_sheets({}, 40, True)
        self.assertEqual(self.transactions[0][1].rollbacks, 1)

    def test_failed_group_assimilation_returns_no_sheets(self):
        self.outcomes['T3Lab: Layout room sheets'] = STATUS.RolledBack
        self.window._layout_room_sheets = Mock(return_value=[object()])
        with self.assertRaisesRegex(RuntimeError, 'not committed'):
            self.window._layout_views_on_sheets({}, 40, True)

    def test_viewport_failure_propagates_and_rolls_back(self):
        self.viewport.Create.side_effect = RuntimeError('already placed')
        with self.assertRaisesRegex(RuntimeError, 'already placed'):
            self.window._place_viewport_centered(NS(Id=1), 2, 0, 0)
        self.assertEqual(self.transactions[0][1].rollbacks, 1)

    def test_null_viewport_is_failure(self):
        self.viewport.Create.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'did not create'):
            self.window._place_viewport_centered(NS(Id=1), 2, 0, 0)
        self.assertEqual(self.transactions[0][1].rollbacks, 1)

    def test_unassigned_sheet_number_rolls_back(self):
        self.window._used_view_names = {}
        self.window._used_sheet_numbers = set()
        parameter = self.sheet.Create.return_value.get_Parameter.return_value
        parameter.IsReadOnly = False
        parameter.Set.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Could not assign'):
            self.window._create_sheet(self.room, 40)
        self.assertEqual(self.transactions[0][1].rollbacks, 1)
        parameter.Set.assert_called_once_with('EPL-101')

    def test_commit_exception_rolls_back_and_disposes(self):
        self.outcomes['T3Lab: Test'] = RuntimeError('commit failed')
        with self.assertRaisesRegex(RuntimeError, 'commit failed'):
            with self.module._sheetgen_transaction('Test'):
                pass
        tx = self.transactions[0][1]
        self.assertEqual(tx.rollbacks, 1)
        self.assertTrue(tx.disposed)
        tx.options.SetForcedModalHandling.assert_called_once_with(True)

    def test_start_exception_does_not_rollback(self):
        tx = FakeTransaction(start_error=True)
        self.module._sheetgen_transaction.__wrapped__.__globals__['Transaction'] = Mock(return_value=tx)
        with self.assertRaisesRegex(RuntimeError, 'start failed'):
            with self.module._sheetgen_transaction('Test'):
                self.fail('must not execute')
        self.assertEqual(tx.rollbacks, 0)
        self.assertTrue(tx.disposed)

    def test_pending_aborts_before_next_room(self):
        self.window._get_selected_rooms.return_value = [self.room, self.room]
        self.outcomes['T3Lab: Create Floor Plan'] = STATUS.Pending
        self.run_batch()
        self.assertEqual(len(self.transactions), 1)
        self.assertEqual(self.transactions[0][1].rollbacks, 0)
        self.assertFalse(self.transactions[0][1].disposed)
        self.assertIn('awaiting failure resolution', self.dialog.Show.call_args.args[1])

    def test_pending_child_does_not_rollback_group(self):
        self.outcomes['T3Lab: Child'] = STATUS.Pending
        with self.assertRaises(self.module._SheetGenPending):
            with self.module._sheetgen_transaction('Group', group=True):
                with self.module._sheetgen_transaction('Child'):
                    pass
        self.assertTrue(all(t.rollbacks == 0 and not t.disposed for _,t in self.transactions))

    def test_no_document_shows_alert_instead_of_raising(self):
        class NoDocument:
            @property
            def doc(self):
                raise AttributeError('no active document')
        self.module.show_sheet_gen_dialog.__globals__['revit'] = NoDocument()
        self.module.show_sheet_gen_dialog()
        self.module.forms.alert.assert_called_once()

    def test_missing_room_bounds_skips_only_that_room(self):
        missing = NS(Name='Unplaced', Number='102', GenQty=1, Element=Mock())
        missing.Element.get_BoundingBox.return_value = None
        self.window._get_selected_rooms.return_value = [missing, self.room]
        self.run_batch()
        self.assertEqual(len(self.transactions), 1)
        self.assertIn('1 view(s) created.', self.window.status_text.Text)
        self.assertIn('1 error(s)', self.window.status_text.Text)

    def test_zero_floor_quantity_still_creates_requested_ceiling(self):
        self.room.GenQty = 0
        self.window.chk_ceiling_plan.IsChecked = True
        self.run_batch()
        self.assertEqual([name for name, _ in self.transactions],
                         ['T3Lab: Create Ceiling Plan'])
        self.assertIn('1 view(s) created.', self.window.status_text.Text)

    def test_all_failed_elevation_directions_rollback_parent_marker(self):
        self.window.chk_floor_plan.IsChecked = False
        self.window.chk_elevations.IsChecked = True
        self.window._create_interior_elevation_view.side_effect = RuntimeError('crop failed')
        self.run_batch()
        self.assertEqual(len(self.transactions), 5)
        self.assertTrue(all(tx.rollbacks == 1 and tx.disposed for _, tx in self.transactions))
        self.assertIn('0 view(s) created.', self.window.status_text.Text)

    def test_pending_elevation_subtransaction_aborts_parent_and_batch(self):
        self.window.chk_floor_plan.IsChecked = False
        self.window.chk_elevations.IsChecked = True
        self.window._get_selected_rooms.return_value = [self.room, self.room]
        self.outcomes['sub'] = STATUS.Pending
        self.run_batch()
        self.assertEqual(len(self.transactions), 2)
        self.assertTrue(all(tx.rollbacks == 0 and not tx.disposed for _, tx in self.transactions))
        self.assertIn('awaiting failure resolution', self.dialog.Show.call_args.args[1])

    def test_later_layout_failure_preserves_prior_committed_sheet_results(self):
        self.window.chk_layout_on_sheet.IsChecked = True
        self.window._get_selected_rooms.return_value = [self.room, self.room]
        sheet = Mock()
        sheet.Name = 'Kitchen sheet'
        sheet.get_Parameter.return_value.AsString.return_value = 'EPL-101'
        self.window._layout_room_sheets = Mock(side_effect=[[sheet], RuntimeError('second room layout')])
        self.run_batch()
        self.assertEqual(self.window._generated_sheets, [sheet])
        groups = [tx for name, tx in self.transactions if name == 'T3Lab: Layout room sheets']
        self.assertEqual([tx.status for tx in groups], [STATUS.Committed, STATUS.RolledBack])
        self.assertIn('1 sheet(s) created.', self.window.status_text.Text)
        self.assertTrue(self.window.btn_open_sheet.IsEnabled)


if __name__ == '__main__':
    unittest.main()

