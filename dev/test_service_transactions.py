"""Regression tests for service transaction results, using a simulated Revit API.

Run: python3 dev/test_service_transactions.py
These tests execute shipped functions/classes without importing Revit or WPF.
They verify control flow and reporting, not actual Revit failure processing.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


LIB = Path(__file__).resolve().parents[1] / 'T3Lab.extension' / 'lib'
STATUS = SimpleNamespace(Uninitialized='Uninitialized', Started='Started',
                         Committed='Committed', RolledBack='RolledBack', Pending='Pending')


def load_definitions(relative_path, only=None, **dependencies):
    path = LIB / relative_path
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    tree.body = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                 and (only is None or node.name in only)]
    scope = dict(dependencies, TransactionStatus=STATUS)
    exec(compile(tree, str(path), 'exec'), scope)
    return SimpleNamespace(**scope)


class FakeTransaction:
    def __init__(self, outcome='Committed', start_error=False):
        self.outcome = outcome
        self.start_error = start_error
        self.status = STATUS.Uninitialized
        self.rollbacks = 0
        self.options = Mock()

    def Start(self):
        if self.start_error:
            raise RuntimeError('Start failed')
        self.status = STATUS.Started

    def Commit(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        self.status = self.outcome
        return self.status

    Assimilate = Commit

    def GetStatus(self):
        return self.status

    def RollBack(self):
        if self.status != STATUS.Started:
            raise AssertionError('Rollback called on an inactive transaction')
        self.rollbacks += 1
        self.status = STATUS.RolledBack

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        self.options = options


class WorksetTransactionTests(unittest.TestCase):
    def setUp(self):
        self.transaction = FakeTransaction()
        self.factory = Mock(return_value=self.transaction)
        self.table = Mock()
        self.workset = Mock()
        self.service = load_definitions(
            'Services/workset_service.py', Transaction=self.factory,
            WorksetTable=self.table, Workset=self.workset, logger=Mock(),
            DeleteWorksetSettings=Mock(),
            DeleteWorksetOption=SimpleNamespace(DeleteElements=1, MoveElementsToWorkset=2))
        # Inject the collector boundary into the actual function globals.
        self.service.delete_workset.__globals__['get_user_worksets'] = lambda doc: [
            SimpleNamespace(Name='Source', Id=1), SimpleNamespace(Name='Target', Id=2)]

    def test_delete_exception_rolls_back_before_returning_error(self):
        self.table.DeleteWorkset.side_effect = RuntimeError('Deletion rejected')
        ok, message = self.service.delete_workset(object(), 'Source', 'Target')
        self.assertFalse(ok)
        self.assertIn('Deletion rejected', message)
        self.assertEqual(self.transaction.rollbacks, 1)

    def test_delete_success_requires_committed_status(self):
        self.transaction.outcome = STATUS.RolledBack
        ok, message = self.service.delete_workset(object(), 'Source')
        self.assertFalse(ok)
        self.assertIn('RolledBack', message)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_delete_commit_exception_rolls_back(self):
        self.transaction.outcome = RuntimeError('Commit failed')
        self.assertFalse(self.service.delete_workset(object(), 'Source')[0])
        self.assertEqual(self.transaction.rollbacks, 1)

    def test_delete_start_failure_does_not_rollback(self):
        self.transaction.start_error = True
        ok, message = self.service.delete_workset(object(), 'Source')
        self.assertFalse(ok)
        self.assertIn('Start failed', message)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_missing_workset_does_not_start_transaction(self):
        self.assertFalse(self.service.delete_workset(object(), 'Missing')[0])
        self.factory.assert_not_called()

    def test_delete_committed_returns_success(self):
        self.assertEqual(self.service.delete_workset(object(), 'Source'), (True, None))
        self.transaction.options.SetForcedModalHandling.assert_called_once_with(True)

    def test_create_does_not_report_rolled_back_workset(self):
        self.transaction.outcome = STATUS.RolledBack
        names = set()
        self.assertEqual(self.service.create_worksets(object(), ['A'], names), [])
        self.assertEqual(names, set())

    def test_create_pending_aborts_batch_before_next_transaction(self):
        self.transaction.outcome = STATUS.Pending
        with self.assertRaisesRegex(RuntimeError, 'Pending'):
            self.service.create_worksets(object(), ['A', 'B'], set())
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_create_committed_updates_existing_names(self):
        names = set()
        self.assertEqual(self.service.create_worksets(object(), ['A', 'A'], names), ['A'])
        self.assertEqual(names, {'A'})
        self.assertEqual(self.factory.call_count, 1)

    def test_create_accepts_existing_name_list_used_by_dialog(self):
        self.assertEqual(self.service.create_worksets(object(), ['A', 'A'], ['B']), ['A'])
        self.assertEqual(self.factory.call_count, 1)

    def test_dialog_delete_routes_through_service_and_reports_commit_failure(self):
        self.transaction.outcome = STATUS.RolledBack
        forms = Mock()
        dialog = load_definitions(
            'GUI/ManaWorksetDialog.py', only={'_remove_workset'},
            forms=forms, delete_workset=self.service.delete_workset)
        worksets = [SimpleNamespace(Name='Source'), SimpleNamespace(Name='Target')]
        self.assertFalse(dialog._remove_workset(object(), 'Source', 'Target', worksets))
        self.assertIn('RolledBack', forms.alert.call_args.args[0])
        self.assertEqual(self.factory.call_count, 1)

    def test_dialog_delete_success_has_no_error_alert(self):
        forms = Mock()
        dialog = load_definitions(
            'GUI/ManaWorksetDialog.py', only={'_remove_workset'},
            forms=forms, delete_workset=self.service.delete_workset)
        worksets = [SimpleNamespace(Name='Source'), SimpleNamespace(Name='Target')]
        self.assertTrue(dialog._remove_workset(object(), 'Source', 'Target', worksets))
        forms.alert.assert_not_called()

    def test_dialog_pending_delete_propagates_to_stop_batch(self):
        self.transaction.outcome = STATUS.Pending
        dialog = load_definitions(
            'GUI/ManaWorksetDialog.py', only={'_remove_workset'},
            forms=Mock(), delete_workset=self.service.delete_workset)
        worksets = [SimpleNamespace(Name='Source'), SimpleNamespace(Name='Target')]
        with self.assertRaisesRegex(RuntimeError, 'Pending'):
            for _ in range(2):
                dialog._remove_workset(object(), 'Source', 'Target', worksets)
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_workset_views_no_document_has_friendly_error(self):
        self.assertEqual(self.service.create_workset_views(None),
                         (None, None, 'Document is not workshared.'))


class PurgeTransactionTests(unittest.TestCase):
    def setUp(self):
        self.transaction = FakeTransaction()
        self.group = FakeTransaction()
        self.factory = Mock(return_value=self.transaction)
        self.doc = Mock()
        self.doc.Delete.return_value = SimpleNamespace(Count=1)
        module = load_definitions(
            'Services/ModelAuditor/smart_purge/purge_executor.py',
            Transaction=self.factory, TransactionGroup=Mock(return_value=self.group))
        self.executor = module.PurgeExecutor(self.doc)
        self.items = [dict(name='A', element=SimpleNamespace(Id=1)),
                      dict(name='B', element=SimpleNamespace(Id=2))]

    def test_commit_exception_clears_successes_and_marks_each_item_once(self):
        self.transaction.outcome = RuntimeError('Commit failed')
        self.items[1]['can_delete'] = False
        self.items[1]['warning'] = 'Protected'
        deleted, failed = self.executor.delete_category_items('Views', self.items)
        self.assertEqual(deleted, [])
        self.assertEqual(len(failed), 2)
        self.assertEqual(len({id(entry['item']) for entry in failed}), 2)
        self.assertIn('Protected', [entry['reason'] for entry in failed])
        self.assertEqual(self.transaction.rollbacks, 1)

    def test_rolled_back_commit_reports_no_deletions(self):
        self.transaction.outcome = STATUS.RolledBack
        deleted, failed = self.executor.delete_category_items('Views', self.items)
        self.assertEqual(deleted, [])
        self.assertEqual(len(failed), 2)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_committed_partial_success_preserves_item_error(self):
        self.doc.Delete.side_effect = [SimpleNamespace(Count=1), RuntimeError('In use')]
        deleted, failed = self.executor.delete_category_items('Views', self.items)
        self.assertEqual(deleted, [self.items[0]])
        self.assertEqual(failed, [{'item': self.items[1], 'reason': 'In use'}])
        self.transaction.options.SetForcedModalHandling.assert_called_once_with(True)

    def test_start_failure_reports_all_failed_without_rollback(self):
        self.transaction.start_error = True
        deleted, failed = self.executor.delete_category_items('Views', self.items)
        self.assertEqual(deleted, [])
        self.assertEqual(len(failed), 2)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_pending_aborts_remaining_categories_without_invalid_rollback(self):
        self.transaction.outcome = STATUS.Pending
        categories = [SimpleNamespace(name='Views', unused_items=self.items),
                      SimpleNamespace(name='Families', unused_items=self.items)]
        with self.assertRaisesRegex(Exception, 'awaiting Revit failure resolution'):
            self.executor.execute_purge(categories)
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(self.transaction.rollbacks, 0)
        self.assertEqual(self.group.rollbacks, 0)
        self.assertEqual(self.executor.deleted_items, [])

    def test_group_failure_clears_previously_committed_category_results(self):
        self.group.outcome = RuntimeError('Group failed')
        with self.assertRaisesRegex(Exception, 'Group failed'):
            self.executor.execute_purge([SimpleNamespace(name='Views', unused_items=self.items)])
        self.assertEqual(self.executor.deleted_items, [])
        self.assertEqual(self.group.rollbacks, 1)

    def test_group_returning_rollback_cannot_report_success(self):
        self.group.outcome = STATUS.RolledBack
        with self.assertRaisesRegex(Exception, 'not committed'):
            self.executor.execute_purge([SimpleNamespace(name='Views', unused_items=self.items)])
        self.assertEqual(self.executor.deleted_items, [])

    def test_dry_run_keeps_simulation_counts_without_deleting(self):
        self.executor.dry_run = True
        result = self.executor.execute_purge([
            SimpleNamespace(name='Views', unused_items=self.items)])
        self.assertEqual(result[:2], (2, 0))
        self.doc.Delete.assert_not_called()
        self.assertEqual(self.transaction.rollbacks, 1)
        self.assertEqual(self.group.rollbacks, 1)


if __name__ == '__main__':
    unittest.main()
