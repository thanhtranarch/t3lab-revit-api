"""Placement uses real sheet bounds and distributes every input exactly once."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/Services/SheetManager/place_views_service.py'
tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
viewport = NS(CanAddViewToSheet=Mock(return_value=True), Create=Mock(side_effect=lambda *args: args))
scope = dict(Viewport=viewport, XYZ=lambda x,y,z: (x,y,z))
exec(compile(ast.Module(body=[cls], type_ignores=[]), str(SOURCE), 'exec'), scope)
Service = scope['PlaceViewsService']

def sheet(number):
    return NS(Id=number, SheetNumber=str(number), Outline=NS(Min=NS(U=10,V=20), Max=NS(U=14,V=22)))

def view(number):
    return NS(Id=number, Name=str(number))

class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(object())
        viewport.Create.reset_mock()
        viewport.CanAddViewToSheet.return_value = True

    def test_default_center_uses_nonzero_sheet_origin(self):
        self.service.place_view_on_sheet(sheet(1), view(5))
        self.assertEqual(viewport.Create.call_args.args[-1], (12,21,0))

    def test_legends_may_repeat_when_revit_accepts_them(self):
        results = self.service.batch_place_views([sheet(1),sheet(2)], [view(3)], 'all_on_each')
        self.assertEqual(len(results),2)

    def test_distribute_keeps_remainder_and_uses_distinct_grid_positions(self):
        results = self.service.batch_place_views([sheet(1),sheet(2)], [view(i) for i in range(5)], 'distribute')
        self.assertEqual(len(results),5)
        calls = [call.args for call in viewport.Create.call_args_list]
        self.assertEqual(sorted(call[2] for call in calls),list(range(5)))
        self.assertEqual(len(set((call[1],call[3]) for call in calls)),5)
        self.assertTrue(all(10 < call[3][0] < 14 and 20 < call[3][1] < 22 for call in calls))

    def test_capacity_rejected_before_any_view_is_placed(self):
        with self.assertRaises(ValueError):
            self.service.batch_place_views([sheet(1)], [view(i) for i in range(5)], 'all_on_each')
        viewport.Create.assert_not_called()

    def test_insufficient_sheets_does_not_silently_drop_views(self):
        with self.assertRaises(ValueError):
            self.service.batch_place_views([sheet(1)], [view(1),view(2)])
        viewport.Create.assert_not_called()

    def test_no_sheets_is_empty_not_divide_by_zero(self):
        self.assertEqual(self.service.batch_place_views([], [view(1)], 'distribute'),[])

    def test_revit_rejected_view_is_not_counted(self):
        viewport.CanAddViewToSheet.return_value = False
        self.assertEqual(self.service.batch_place_views([sheet(1)], [view(2)]),[])
        viewport.Create.assert_not_called()

if __name__ == '__main__':
    unittest.main()
