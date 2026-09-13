"""Exercise shipped Group Manager operations with transaction/model doubles.

Run: python3 dev/test_group_transactions.py
This does not simulate Revit failure finalizers or PythonNet.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_service_transactions import FakeTransaction, STATUS, load_definitions


class GroupType:
    def __init__(self, model, name):
        self.model = model
        self._name = name
        self.Id = len(model) + 1
        self.reject = set()
        model.append(self)

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        if value in self.reject:
            raise RuntimeError('Name rejected')
        if any(other is not self and other.Name == value for other in self.model):
            raise RuntimeError('Duplicate name')
        self._name = value


class ModelTransaction(FakeTransaction):
    def __init__(self, model, **kwargs):
        super().__init__(**kwargs)
        self.model = model
        self.snapshot = []

    def Start(self):
        super().Start()
        self.snapshot = [group.Name for group in self.model]

    def restore(self):
        for group, name in zip(self.model, self.snapshot):
            group._name = name

    def Commit(self):
        result = super().Commit()
        if result == STATUS.RolledBack:
            self.restore()
        return result

    def RollBack(self):
        super().RollBack()
        self.restore()


class GroupTransactionTests(unittest.TestCase):
    def setUp(self):
        self.model = []
        self.records = [self.record('A'), self.record('B')]
        self.transaction = ModelTransaction(self.model)
        self.factory = Mock(return_value=self.transaction)
        self.ops = load_definitions(
            'Snippets/_group_ops.py', Transaction=self.factory,
            only={'_commit_transaction', '_rollback_started', 'rename_group_types',
                  'plan_rename', 'illegal_chars_in', '_short_error', '_set_workset',
                  'apply_workset', 'purge_group_types', 'ungroup_instances'},
            TEMP_NAME_PREFIX='T3TMP-', ILLEGAL_CHARS='\\:{}[]|;<>?`~',
            BuiltInParameter=SimpleNamespace(ELEM_PARTITION_PARAM=1),
            System=SimpleNamespace(Int32=int))
        self.ops.rename_group_types.__globals__['element_name'] = lambda g: g.Name
        self.doc = Mock()

    def record(self, name):
        instance = Mock()
        parameter = instance.get_Parameter.return_value
        parameter.IsReadOnly = False
        parameter.AsInteger.return_value = 1
        parameter.Set.return_value = True
        return SimpleNamespace(name=name, group_type=GroupType(self.model, name),
                               instances=[instance], instance_count=0)

    def run_operation(self, operation, progress=None):
        if operation == 'rename':
            return self.ops.rename_group_types(self.doc, [(self.records[0], 'New')], progress)
        if operation == 'workset':
            return self.ops.apply_workset(self.doc, self.records, 2, progress=progress)
        if operation == 'purge':
            return self.ops.purge_group_types(self.doc, self.records, progress)
        return self.ops.ungroup_instances(self.doc, self.records, progress)

    def test_all_operations_reject_noncommitted_results(self):
        for operation in ('rename', 'workset', 'purge', 'ungroup'):
            for outcome in (STATUS.RolledBack, STATUS.Pending):
                with self.subTest(operation=operation, outcome=outcome):
                    self.setUp()
                    self.transaction.outcome = outcome
                    with self.assertRaisesRegex(RuntimeError, 'not committed'):
                        self.run_operation(operation)
                    self.assertEqual(self.transaction.rollbacks, 0)

    def test_all_operations_rollback_commit_exception(self):
        for operation in ('rename', 'workset', 'purge', 'ungroup'):
            with self.subTest(operation=operation):
                self.setUp()
                self.transaction.outcome = RuntimeError('Commit rejected')
                with self.assertRaisesRegex(RuntimeError, 'Commit rejected'):
                    self.run_operation(operation)
                self.assertEqual(self.transaction.rollbacks, 1)

    def test_start_failure_never_rolls_back_unstarted_transaction(self):
        for operation in ('rename', 'workset', 'purge', 'ungroup'):
            with self.subTest(operation=operation):
                self.setUp()
                self.transaction.start_error = True
                with self.assertRaisesRegex(RuntimeError, 'Start failed'):
                    self.run_operation(operation)
                self.assertEqual(self.transaction.rollbacks, 0)

    def test_all_operations_commit_successfully(self):
        for operation in ('rename', 'workset', 'purge', 'ungroup'):
            with self.subTest(operation=operation):
                self.setUp()
                self.assertTrue(self.run_operation(operation))
                self.assertEqual(self.transaction.GetStatus(), STATUS.Committed)
                self.transaction.options.SetForcedModalHandling.assert_called_once_with(True)

    def test_swap_renames_without_leaving_temporary_names(self):
        a, b = self.records
        result = self.ops.rename_group_types(self.doc, [(a, 'B'), (b, 'A')])
        self.assertTrue(all(ok for _, ok, _ in result))
        self.assertEqual([g.Name for g in self.model], ['B', 'A'])
        self.assertEqual([r.name for r in self.records], ['B', 'A'])

    def test_failed_swap_restore_rolls_back_entire_batch_and_cached_names(self):
        a, b = self.records
        b.group_type.reject.add('A')
        with self.assertRaisesRegex(RuntimeError, 'Could not restore temporary'):
            self.ops.rename_group_types(self.doc, [(a, 'B'), (b, 'A')])
        self.assertEqual(self.transaction.rollbacks, 1)
        self.assertEqual([g.Name for g in self.model], ['A', 'B'])
        self.assertEqual([r.name for r in self.records], ['A', 'B'])

    def test_rolled_back_rename_restores_cached_names(self):
        self.transaction.outcome = STATUS.RolledBack
        with self.assertRaises(RuntimeError):
            self.run_operation('rename')
        self.assertEqual(self.records[0].name, 'A')
        self.assertEqual(self.model[0].Name, 'A')

    def test_progress_exception_restores_cached_names_after_partial_rename(self):
        progress = Mock(side_effect=[None, RuntimeError('Progress failed')])
        with self.assertRaisesRegex(RuntimeError, 'Progress failed'):
            self.ops.rename_group_types(self.doc, [(self.records[0], 'C'),
                                                     (self.records[1], 'D')], progress)
        self.assertEqual(self.transaction.rollbacks, 1)
        self.assertEqual([r.name for r in self.records], ['A', 'B'])

    def test_user_requested_temporary_prefix_is_not_mistaken_for_unfinished_rename(self):
        a, b = self.records
        result = self.ops.rename_group_types(self.doc, [(a, 'B'), (b, 'T3TMP-user')])
        self.assertTrue(all(ok for _, ok, _ in result))
        self.assertEqual(b.group_type.Name, 'T3TMP-user')

    def test_set_false_is_reported_as_failure_not_moved(self):
        record = self.records[0]
        record.instances[0].get_Parameter.return_value.Set.return_value = False
        result = self.ops.apply_workset(self.doc, [record], 2)
        self.assertEqual(result[0][1:4], (0, 0, 1))
        self.assertIn('rejected', result[0][4])


if __name__ == '__main__':
    unittest.main()
