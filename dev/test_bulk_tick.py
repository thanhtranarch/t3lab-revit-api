# -*- coding: utf-8 -*-
"""Bulk tick gestures (Shift+click range, drag-paint, Space, context menu).

The WPF wiring lives in GUI/bulk_tick.py:BulkTick and needs Revit to test;
every decision it makes goes through the pure functions tested here.

Run: python dev/test_bulk_tick.py
"""
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
sys.path.insert(0, LIB)

from GUI import bulk_tick as bt  # noqa: E402  (imports cleanly without .NET)


class Row(object):
    def __init__(self, name, sel=False):
        self.name = name
        self.is_selected = sel

    def __repr__(self):
        return '{}:{}'.format(self.name, int(self.is_selected))


class Placeholder(object):
    """DataGrid NewItemPlaceholder: no row attribute."""


class ReadOnlyRow(object):
    @property
    def is_selected(self):
        return False


def rows(*flags):
    return [Row(str(i), f) for i, f in enumerate(flags)]


def ticks(rs):
    return [int(r.is_selected) for r in rs if isinstance(r, Row)]


class TickSpan(unittest.TestCase):
    def test_forward_and_backward_are_inclusive(self):
        self.assertEqual(bt.tick_span(10, 2, 5), [2, 3, 4, 5])
        self.assertEqual(bt.tick_span(10, 5, 2), [2, 3, 4, 5])
        self.assertEqual(bt.tick_span(10, 4, 4), [4])

    def test_clamped_and_invalid(self):
        self.assertEqual(bt.tick_span(3, 1, 9), [1, 2])
        self.assertEqual(bt.tick_span(0, 0, 0), [])
        self.assertEqual(bt.tick_span(5, -1, 3), [])
        self.assertEqual(bt.tick_span(5, None, 3), [])


class IndexOf(unittest.TestCase):
    def test_identity_not_equality_first(self):
        rs = rows(False, False)
        self.assertEqual(bt.index_of(rs, rs[1]), 1)
        self.assertEqual(bt.index_of(rs, Row('x')), -1)
        self.assertEqual(bt.index_of(rs, None), -1)


class ShiftClickRange(unittest.TestCase):
    def test_range_takes_anchor_state_in_visual_order(self):
        rs = rows(False, True, False, False, False, False)
        # anchor = row 1 (ticked), shift+click row 4 -> 1..4 ticked
        value, changed = bt.range_tick(rs, 'is_selected', rs[1], rs[4])
        self.assertIs(value, True)
        self.assertEqual(ticks(rs), [0, 1, 1, 1, 1, 0])
        self.assertEqual(changed, [rs[2], rs[3], rs[4]])

    def test_upward_range_unticks(self):
        rs = rows(True, True, True, False, True)
        rs[3].is_selected = False          # anchor just unticked by a click
        value, _ = bt.range_tick(rs, 'is_selected', rs[3], rs[0])
        self.assertIs(value, False)
        self.assertEqual(ticks(rs), [0, 0, 0, 0, 1])

    def test_sorted_view_order_not_source_order(self):
        src = rows(False, False, False, False)
        view = [src[3], src[0], src[2], src[1]]  # what the grid shows
        view[0].is_selected = True              # anchor = src[3]
        bt.range_tick(view, 'is_selected', view[0], view[2])
        self.assertEqual(ticks(src), [1, 0, 1, 1])

    def test_anchor_filtered_out_falls_back(self):
        rs = rows(False, False, False)
        value, changed = bt.range_tick(rs, 'is_selected', Row('gone', True), rs[2])
        self.assertIsNone(value)
        self.assertEqual(changed, [])
        self.assertEqual(ticks(rs), [0, 0, 0])

    def test_placeholder_rows_are_skipped(self):
        rs = rows(True, False)
        rs.insert(1, Placeholder())
        value, changed = bt.range_tick(rs, 'is_selected', rs[0], rs[2])
        self.assertIs(value, True)
        self.assertEqual(ticks(rs), [1, 1])


class DragPaint(unittest.TestCase):
    def test_fast_move_skipping_rows_paints_every_row_between(self):
        rs = rows(*([False] * 8))
        rs[1].is_selected = True                  # press on row 1 -> paint True
        last = 1
        for idx in (2, 5, 7):                     # mouse jumps
            bt.paint_step(rs, 'is_selected', last, idx, True)
            last = idx
        self.assertEqual(ticks(rs), [0, 1, 1, 1, 1, 1, 1, 1])

    def test_paint_untick_and_reverse_direction(self):
        rs = rows(*([True] * 6))
        rs[4].is_selected = False
        changed = bt.paint_step(rs, 'is_selected', 4, 1, False)
        self.assertEqual(ticks(rs), [1, 0, 0, 0, 0, 1])
        self.assertEqual(len(changed), 3)

    def test_paint_does_not_rewrite_unchanged_rows(self):
        rs = rows(True, True)
        self.assertEqual(bt.paint_step(rs, 'is_selected', 0, 1, True), [])
        self.assertEqual(bt.paint_step(rs, 'is_selected', 0, -1, False), [])

    def test_first_step_without_last_index(self):
        rs = rows(False, False)
        bt.paint_step(rs, 'is_selected', -1, 1, True)
        self.assertEqual(ticks(rs), [0, 1])


class SpaceAndMenu(unittest.TestCase):
    def test_space_ticks_when_any_unticked_else_unticks(self):
        self.assertIs(bt.toggle_value(rows(True, False), 'is_selected'), True)
        self.assertIs(bt.toggle_value(rows(True, True), 'is_selected'), False)
        self.assertIs(bt.toggle_value(rows(False), 'is_selected'), True)
        self.assertIsNone(bt.toggle_value([], 'is_selected'))
        self.assertIsNone(bt.toggle_value([Placeholder()], 'is_selected'))

    def test_apply_and_invert(self):
        rs = rows(True, False, True)
        self.assertEqual(len(bt.apply_tick(rs, 'is_selected', True)), 1)
        self.assertEqual(ticks(rs), [1, 1, 1])
        rs = rows(True, False, True)
        bt.invert_ticks(rs + [Placeholder()], 'is_selected')
        self.assertEqual(ticks(rs), [0, 1, 0])

    def test_read_only_row_is_not_reported_changed(self):
        self.assertEqual(bt.apply_tick([ReadOnlyRow()], 'is_selected', True), [])


class HeaderAndEdges(unittest.TestCase):
    def test_tri_state(self):
        self.assertIs(bt.tri_state([True, True]), True)
        self.assertIs(bt.tri_state([False, False]), False)
        self.assertIs(bt.tri_state([]), False)
        self.assertIsNone(bt.tri_state([True, False]))
        self.assertIs(bt.tri_state([True, None]), True)   # placeholder ignored
        self.assertIs(bt.tri_state(iter([True])), True)   # generator input

    def test_edge_direction(self):
        self.assertEqual(bt.edge_direction(30, 28, 400, 16), -1)
        self.assertEqual(bt.edge_direction(200, 28, 400, 16), 0)
        self.assertEqual(bt.edge_direction(395, 28, 400, 16), 1)


class Wiring(unittest.TestCase):
    """Static checks: ManaPara opts in; the helper is on T3WPFWindow."""

    def _read(self, *parts):
        with open(os.path.join(LIB, *parts), encoding='utf-8') as f:
            return f.read()

    def test_wpf_base_exposes_enable_bulk_tick(self):
        src = self._read('GUI', 'WPF_Base.py')
        self.assertIn('def enable_bulk_tick(self, grid, prop', src)

    def test_manapara_opts_in_for_both_grids(self):
        src = self._read('GUI', 'ManaParaDialog.py')
        self.assertRegex(src, r'enable_bulk_tick\(self\.dg_parameters, "is_selected"')
        self.assertRegex(src, r'enable_bulk_tick\(self\.dg_loader_params, "is_selected"\)')

    def test_menu_text_is_english(self):
        for text in (bt.MENU_TICK_SELECTED, bt.MENU_UNTICK_SELECTED, bt.MENU_TICK_ALL,
                     bt.MENU_UNTICK_ALL, bt.MENU_INVERT):
            self.assertTrue(re.match(r'^[A-Za-z ]+$', text), text)

    def test_no_hardcoded_colours_in_helper(self):
        src = self._read('GUI', 'bulk_tick.py')
        self.assertNotRegex(src, r'#[0-9A-Fa-f]{6}\b')
        self.assertNotIn('Brushes.', src)


if __name__ == '__main__':
    unittest.main(verbosity=1)
