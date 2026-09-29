# -*- coding: utf-8 -*-
"""
Tests for Snippets._compat.disposing — `with` for .NET IDisposable objects.

pythonnet 3 (CPython) does not make IDisposable a context manager, so
`with DB.TransactionGroup(doc, "x") as tg:` raised "TypeError:
'TransactionGroup' object does not support the context manager protocol" and
killed Datum Sync's Sync button (2026-09-29). `disposing` must behave exactly
like IronPython's `with` did: hand back the object untouched (no auto-Start),
and on the way out roll back whatever is still open, then Dispose.

Run: python dev/test_compat_disposing.py
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from Snippets._compat import disposing     # noqa: E402


class _Txn(object):
    """Stand-in for Transaction / TransactionGroup / SubTransaction."""

    def __init__(self):
        self.calls = []
        self._started = self._ended = False

    def Start(self):
        self.calls.append("Start")
        self._started = True

    def Commit(self):
        self.calls.append("Commit")
        self._ended = True

    def RollBack(self):
        self.calls.append("RollBack")
        self._ended = True

    def HasStarted(self):
        return self._started

    def HasEnded(self):
        return self._ended

    def Dispose(self):
        self.calls.append("Dispose")


class _Collector(object):
    """FilteredElementCollector: IDisposable, but no transaction state."""

    def __init__(self):
        self.disposed = False

    def Dispose(self):
        self.disposed = True


class DisposingTests(unittest.TestCase):

    def test_returns_the_object_without_starting_it(self):
        txn = _Txn()
        with disposing(txn) as t:
            self.assertIs(t, txn)
            self.assertEqual(txn.calls, [])

    def test_committed_transaction_is_only_disposed(self):
        txn = _Txn()
        with disposing(txn) as t:
            t.Start()
            t.Commit()
        self.assertEqual(txn.calls, ["Start", "Commit", "Dispose"])

    def test_exception_rolls_back_disposes_and_propagates(self):
        txn = _Txn()
        with self.assertRaises(ValueError):
            with disposing(txn) as t:
                t.Start()
                raise ValueError("boom")
        self.assertEqual(txn.calls, ["Start", "RollBack", "Dispose"])

    def test_forgotten_commit_is_rolled_back(self):
        txn = _Txn()
        with disposing(txn) as t:
            t.Start()
        self.assertEqual(txn.calls, ["Start", "RollBack", "Dispose"])

    def test_nested_group_and_transactions(self):
        # Datum Sync's shape: a group with one transaction per view.
        group, inner = _Txn(), _Txn()
        with disposing(group) as tg:
            tg.Start()
            with disposing(inner) as t:
                t.Start()
                t.Commit()
            tg.Commit()
        self.assertEqual(group.calls, ["Start", "Commit", "Dispose"])
        self.assertEqual(inner.calls, ["Start", "Commit", "Dispose"])

    def test_collector_is_disposed(self):
        collector = _Collector()
        with disposing(collector) as c:
            self.assertIs(c, collector)
        self.assertTrue(collector.disposed)


if __name__ == '__main__':
    unittest.main()
