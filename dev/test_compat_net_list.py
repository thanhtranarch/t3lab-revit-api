# -*- coding: utf-8 -*-
"""Snippets._compat.net_list: dựng List[T] .NET bằng Add, KHÔNG qua ctor(list).

Dưới PythonNet 3, `List[ElementId](python_list)` ném "No method matches" — đó là
lý do RoomToFloor / DoorThreshold không chọn được sàn vừa tạo. Stub dưới đây mô
phỏng đúng hành vi đó: ctor nhận list Python thì ném, ctor rỗng + Add thì chạy.
"""
import os
import sys
import types
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'T3Lab.extension', 'lib')


class _StrictNetList(list):
    def __init__(self, *args):
        if args:
            raise TypeError("No method matches given arguments for List`1..ctor")
        list.__init__(self)

    def Add(self, item):
        self.append(item)


class _ListFactory:
    def __getitem__(self, _t):
        return _StrictNetList


generic = types.ModuleType('System.Collections.Generic')
generic.List = _ListFactory()
system = types.ModuleType('System')
collections = types.ModuleType('System.Collections')
collections.Generic = generic
system.Collections = collections
sys.modules.setdefault('System', system)
sys.modules['System.Collections'] = collections
sys.modules['System.Collections.Generic'] = generic
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from Snippets._compat import net_list  # noqa: E402


class NetList(unittest.TestCase):
    def test_python_list(self):
        self.assertEqual(list(net_list(int, [1, 2, 3])), [1, 2, 3])

    def test_skips_none_and_empty(self):
        self.assertEqual(list(net_list(int, [1, None, 2])), [1, 2])
        self.assertEqual(list(net_list(int, None)), [])

    def test_generator(self):
        self.assertEqual(list(net_list(int, (i for i in range(3)))), [0, 1, 2])


if __name__ == '__main__':
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
