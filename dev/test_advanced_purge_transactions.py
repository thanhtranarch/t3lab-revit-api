"""Advanced Purge transaction regressions using shipped code and simulated Revit APIs."""
from collections import namedtuple
import unittest
from unittest.mock import Mock

from test_service_transactions import FakeTransaction, STATUS, load_definitions


Category = namedtuple('Category', 'name')


class AdvancedPurgeTransactionTests(unittest.TestCase):
    def setUp(self):
        self.transaction = FakeTransaction()
        self.group = FakeTransaction()
        self.factory = Mock(return_value=self.transaction)
        self.doc = Mock()
        module = load_definitions(
            'Services/ModelAuditor/advanced_purge/advanced_purge_executor.py',
            Transaction=self.factory, TransactionGroup=Mock(return_value=self.group))
        self.executor = module.AdvancedPurgeExecutor(self.doc)
        self.executor.dry_run = False
        self.items = [{'id': 1, 'name': 'A'}, {'id': 2, 'name': 'B'}]
        self.category = Category('Views')

    def execute(self, callback=None):
        return self.executor.execute_purge({self.category: self.items}, callback)

    def test_committed_deletions_are_returned(self):
        self.assertEqual(self.execute(), (self.items, []))
        self.transaction.options.SetForcedModalHandling.assert_called_once_with(True)

    def test_failed_commit_reports_each_item_once_without_successes(self):
        self.transaction.outcome = RuntimeError('Commit failed')
        self.doc.Delete.side_effect = [None, RuntimeError('Protected')]
        deleted, failed = self.execute()
        self.assertEqual(deleted, [])
        self.assertEqual(len(failed), 2)
        self.assertEqual(len({id(entry['item']) for entry in failed}), 2)
        self.assertIn('Protected', [entry['error'] for entry in failed])
        self.assertEqual(self.transaction.rollbacks, 1)

    def test_returned_rollback_reports_no_deletions(self):
        self.transaction.outcome = STATUS.RolledBack
        deleted, failed = self.execute()
        self.assertEqual(deleted, [])
        self.assertEqual(len(failed), 2)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_start_failure_has_no_invalid_rollback(self):
        self.transaction.start_error = True
        deleted, failed = self.execute()
        self.assertEqual(deleted, [])
        self.assertEqual(len(failed), 2)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_pending_aborts_next_category_and_keeps_group_open_for_revit(self):
        self.transaction.outcome = STATUS.Pending
        with self.assertRaisesRegex(RuntimeError, 'awaiting Revit failure resolution'):
            self.executor.execute_purge({self.category: self.items, Category('Families'): self.items})
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(self.transaction.rollbacks, 0)
        self.assertEqual(self.group.rollbacks, 0)

    def test_group_rollback_cannot_report_saved_items(self):
        self.group.outcome = STATUS.RolledBack
        with self.assertRaisesRegex(RuntimeError, 'not committed'):
            self.execute()
        self.assertEqual(self.group.rollbacks, 0)

    def test_group_commit_exception_rolls_back(self):
        self.group.outcome = RuntimeError('Assimilate failed')
        with self.assertRaisesRegex(RuntimeError, 'Assimilate failed'):
            self.execute()
        self.assertEqual(self.group.rollbacks, 1)

    def test_group_start_failure_does_not_rollback_unstarted_group(self):
        self.group.start_error = True
        with self.assertRaisesRegex(RuntimeError, 'Start failed'):
            self.execute()
        self.assertEqual(self.group.rollbacks, 0)

    def test_final_progress_failure_does_not_undo_committed_result(self):
        def callback(current, total, message):
            if message == 'Purge complete!':
                raise RuntimeError('Window closed')

        self.assertEqual(self.execute(callback), (self.items, []))
        self.assertEqual(self.group.status, STATUS.Committed)
        self.assertEqual(self.group.rollbacks, 0)
        self.assertEqual(self.executor.progress_errors, ['Window closed'])

    def test_error_callback_does_not_mask_original_transaction_exception(self):
        self.group.outcome = RuntimeError('Original group failure')
        with self.assertRaisesRegex(RuntimeError, 'Original group failure'):
            self.execute(Mock(side_effect=RuntimeError('Callback failed')))
        self.assertTrue(self.executor.progress_errors)

    def test_dry_run_final_callback_failure_keeps_simulation_result(self):
        self.executor.dry_run = True
        self.assertEqual(self.execute(Mock(side_effect=RuntimeError('UI failed'))), (self.items, []))
        self.doc.Delete.assert_not_called()
        self.assertEqual(self.transaction.rollbacks, 1)
        self.assertEqual(self.group.rollbacks, 1)


if __name__ == '__main__':
    unittest.main()
