"""Join transaction regressions against shipped functions with simulated Revit APIs."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_service_transactions import FakeTransaction, STATUS, load_definitions


class JoinTransactionTests(unittest.TestCase):
    def setUp(self):
        self.transaction = FakeTransaction()
        self.factory = Mock(return_value=self.transaction)
        self.geometry = Mock()
        self.geometry.AreElementsJoined.return_value = False
        self.preprocessor = Mock()
        self.doc = SimpleNamespace(ActiveView=SimpleNamespace(Id=10))
        self.elements = [SimpleNamespace(Id=1), SimpleNamespace(Id=2)]
        self.rules = [{'priority': 'Walls', 'join_with': 'Floors'}]
        self.cancel_message = 'Cancelled by user; completed changes were committed. Use Undo to revert this run.'
        self.service = load_definitions(
            'Services/join_service.py', only={'run_join', '_commit_join'},
            Transaction=self.factory, JoinFailuresPreprocessor=self.preprocessor,
            JOINABLE_CATEGORIES={'Walls': 1, 'Floors': 2},
            JOIN_CANCELLED_MESSAGE=self.cancel_message,
            _collect_elements=Mock(return_value=self.elements),
            _get_intersecting_elements=Mock(return_value=[SimpleNamespace(Id=3)]),
            _same_design_option=Mock(return_value=True),
            eid_value=lambda value: value, JoinGeometryUtils=self.geometry, logger=Mock())

    def run_join(self, **kwargs):
        return self.service.run_join(self.rules, doc=self.doc, uidoc=Mock(), **kwargs)

    def test_committed_count_matches_processed_pairs(self):
        self.assertEqual(self.run_join(), (2, 0, 0, None))

    def test_start_resets_options_then_preprocessor_is_configured(self):
        original_start = self.transaction.Start

        def reset_options():
            original_start()
            self.transaction.options = Mock()

        self.transaction.Start = reset_options
        self.run_join()
        self.transaction.options.SetFailuresPreprocessor.assert_called_once_with(
            self.preprocessor.return_value)
        self.transaction.options.SetForcedModalHandling.assert_called_once_with(True)

    def test_commit_rollback_returns_no_saved_joins(self):
        self.transaction.outcome = STATUS.RolledBack
        joined, _, errors, message = self.run_join()
        self.assertEqual(joined, 0)
        self.assertEqual(errors, 1)
        self.assertIn('RolledBack', message)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_commit_exception_rolls_back_and_clears_count(self):
        self.transaction.outcome = RuntimeError('Commit rejected')
        result = self.run_join()
        self.assertEqual(result[0], 0)
        self.assertIn('Commit rejected', result[3])
        self.assertEqual(self.transaction.rollbacks, 1)

    def test_pending_returns_unresolved_error_without_invalid_rollback(self):
        self.transaction.outcome = STATUS.Pending
        result = self.run_join()
        self.assertEqual(result[0], 0)
        self.assertIn('Pending', result[3])
        self.assertNotEqual(result[3], self.cancel_message)
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_start_failure_is_returned_without_rollback(self):
        self.transaction.start_error = True
        result = self.run_join()
        self.assertEqual(result[0], 0)
        self.assertIn('Start failed', result[3])
        self.assertEqual(self.transaction.rollbacks, 0)

    def test_constructor_failure_preserves_result_contract(self):
        self.factory.side_effect = RuntimeError('No API context')
        self.assertEqual(self.run_join(), (0, 0, 1, 'No API context'))

    def test_option_configuration_failure_rolls_back_started_transaction(self):
        self.transaction.options.SetFailuresPreprocessor.side_effect = RuntimeError('Options failed')
        self.assertEqual(self.run_join()[0], 0)
        self.assertEqual(self.transaction.rollbacks, 1)

    def test_cancel_commits_partial_work_and_returns_distinct_notice(self):
        result = self.run_join(cancel_check=Mock(side_effect=[False, True]))
        self.assertEqual(result, (1, 0, 0, self.cancel_message))
        self.assertEqual(self.transaction.status, STATUS.Committed)

    def test_cancel_commit_failure_is_not_reported_as_successful_cancel(self):
        self.transaction.outcome = STATUS.RolledBack
        result = self.run_join(cancel_check=Mock(side_effect=[False, True]))
        self.assertEqual(result[0], 0)
        self.assertNotEqual(result[3], self.cancel_message)
        self.assertIn('RolledBack', result[3])

    def test_progress_exception_after_changes_rolls_back_and_clears_count(self):
        self.rules.append(dict(self.rules[0]))
        result = self.run_join(progress_callback=Mock(side_effect=[None, RuntimeError('UI failed')]))
        self.assertEqual(result[0], 0)
        self.assertIn('UI failed', result[3])
        self.assertEqual(self.transaction.rollbacks, 1)


if __name__ == '__main__':
    unittest.main()
