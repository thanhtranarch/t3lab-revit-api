# -*- coding: utf-8 -*-
"""Snippets._project_params.delete_project_parameters — xoá hàng loạt (ManaPara).

Mô phỏng doc.ParameterBindings + Transaction bằng đối tượng giả; không cần Revit.
Chạy: python3 dev/test_project_params.py
"""
import os
import sys
import types
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'T3Lab.extension', 'lib')
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from Snippets._project_params import definition_key, delete_project_parameters  # noqa: E402


class Eid:
    def __init__(self, v):
        self.Value = v


class Definition:
    def __init__(self, name, did):
        self.Name = name
        self.Id = Eid(did)


class Iterator:
    def __init__(self, items):
        self._items = list(items)
        self._i = -1

    def MoveNext(self):
        self._i += 1
        return self._i < len(self._items)

    @property
    def Key(self):
        return self._items[self._i]


class Bindings:
    def __init__(self, defs, refuse=(), explode=()):
        self.defs = list(defs)
        self.refuse = set(refuse)
        self.explode = set(explode)
        self.iterating = False

    def ForwardIterator(self):
        return Iterator(self.defs)

    def Remove(self, d):
        if d.Name in self.explode:
            raise RuntimeError("Parameter is used by a schedule filter\nstack…")
        if d.Name in self.refuse:
            return False
        self.defs.remove(d)
        return True


class Tx:
    log = []

    def __init__(self, doc, name):
        self.name = name
        self.state = 'new'
        Tx.log.append(self)

    def Start(self):
        self.state = 'started'

    def Commit(self):
        self.state = 'committed'

    def RollBack(self):
        self.state = 'rolled_back'

    def HasStarted(self):
        return self.state != 'new'

    def HasEnded(self):
        return self.state in ('committed', 'rolled_back')


def make_doc(defs, **kw):
    return types.SimpleNamespace(ParameterBindings=Bindings(defs, **kw))


class DeleteProjectParameters(unittest.TestCase):
    def setUp(self):
        Tx.log = []

    def test_batch_is_one_transaction(self):
        a, b, c = Definition('A', 1), Definition('B', 2), Definition('C', 3)
        doc = make_doc([a, b, c])
        deleted, failed = delete_project_parameters(doc, [1, 3], transaction_factory=Tx)
        self.assertEqual(deleted, ['A', 'C'])
        self.assertEqual(failed, [])
        self.assertEqual([d.Name for d in doc.ParameterBindings.defs], ['B'])
        self.assertEqual(len(Tx.log), 1, 'one Ctrl+Z step for the whole batch')
        self.assertEqual(Tx.log[0].state, 'committed')
        self.assertIn('2 Parameters', Tx.log[0].name)

    def test_duplicate_names_delete_by_id_not_name(self):
        """Hai parameter cùng tên 'Mark_X': chỉ xoá đúng cái được chọn."""
        shared, project = Definition('Mark_X', 10), Definition('Mark_X', 11)
        doc = make_doc([shared, project])
        deleted, _ = delete_project_parameters(doc, [11], transaction_factory=Tx)
        self.assertEqual(deleted, ['Mark_X'])
        self.assertEqual(doc.ParameterBindings.defs, [shared])

    def test_one_failure_does_not_block_others(self):
        doc = make_doc([Definition('A', 1), Definition('B', 2), Definition('C', 3)],
                       explode={'B'}, refuse={'C'})
        deleted, failed = delete_project_parameters(doc, [1, 2, 3], transaction_factory=Tx)
        self.assertEqual(deleted, ['A'])
        self.assertEqual(failed, [('B', 'Parameter is used by a schedule filter'),
                                  ('C', 'Revit refused to remove the binding')])
        self.assertEqual(Tx.log[0].state, 'committed')

    def test_nothing_removed_rolls_back(self):
        doc = make_doc([Definition('A', 1)], refuse={'A'})
        deleted, failed = delete_project_parameters(doc, [1], transaction_factory=Tx)
        self.assertEqual(deleted, [])
        self.assertEqual(len(failed), 1)
        self.assertEqual(Tx.log[0].state, 'rolled_back', 'no empty Undo entry')

    def test_stale_key_reported_and_no_transaction(self):
        doc = make_doc([Definition('A', 1)])
        deleted, failed = delete_project_parameters(doc, [99], transaction_factory=Tx)
        self.assertEqual(deleted, [])
        self.assertEqual(failed, [('99', 'No longer in the model')])
        self.assertEqual(Tx.log, [])

    def test_empty_input(self):
        self.assertEqual(delete_project_parameters(make_doc([]), [], transaction_factory=Tx),
                         ([], []))
        self.assertEqual(delete_project_parameters(None, [1], transaction_factory=Tx), ([], []))
        self.assertEqual(Tx.log, [])

    def test_definition_key(self):
        self.assertEqual(definition_key(Definition('A', 7)), 7)
        self.assertIsNone(definition_key(object()))


if __name__ == '__main__':
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
