"""Mocked Revit/WPF navigation checks. Run: python dev/test_dwg_view_navigation.py."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI/ManaDWGDialog.py'


class View:
    IsValidObject = True
    IsTemplate = False


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.alerts = []
        self.requests = []
        self.doc = NS(IsValidObject=True, GetElement=lambda eid: self.view)
        self.view = View()
        self.uidoc = NS(Document=self.doc, RequestViewChange=self.requests.append)
        self.env = dict(View=View, ElementId=NS(InvalidElementId=-1),
                        forms=NS(alert=lambda message, **kw: self.alerts.append(message)),
                        logger=NS(warning=lambda message: None), revit=NS(doc=self.doc, uidoc=self.uidoc),
                        VisualTreeHelper=NS(GetParent=lambda node: getattr(node, 'parent', None)),
                        LogicalTreeHelper=NS(GetParent=lambda node: getattr(node, 'logical_parent', None)))
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        selected = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == 'DWGItem':
                selected.append(node)
            if isinstance(node, ast.FunctionDef) and node.name in ('resolve_dwg_view', 'request_dwg_view', 'show_dwg_manager'):
                selected.append(node)
            if isinstance(node, ast.ClassDef) and node.name == 'DWGManagementWindow':
                selected.extend(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'view_name_mouse_down')
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), self.env)
        self.item = self.env['DWGItem'](None, None, True, 'cad', 'view', '', 42)
        self.win = NS(_doc=self.doc, _requested_view_id=None, Close=lambda: self.requests.append('closed'))

    def click(self, count=2, marker=True, child=False):
        source = NS(Name='DWGViewName' if marker else 'OtherCell', DataContext=self.item)
        if child:
            source = NS(logical_parent=source)  # e.g. a Run inside the TextBlock
        e = NS(ClickCount=count, OriginalSource=source, Handled=False)
        self.env['view_name_mouse_down'](self.win, object(), e)
        return e

    def test_double_click_stages_view_and_closes_without_api_request(self):
        self.assertTrue(self.click(child=True).Handled)
        self.assertEqual(self.win._requested_view_id, 42)
        self.assertEqual(self.requests, ['closed'])

    def test_single_click_and_other_cells_do_nothing(self):
        self.click(count=1)
        self.click(marker=False)
        self.assertEqual(self.requests, [])
        self.assertIsNone(self.win._requested_view_id)

    def test_invalid_owner_remains_open(self):
        for eid in (None, -1):
            self.item.OwnerViewId = eid
            self.click()
        self.assertEqual(self.requests, [])
        self.assertEqual(len(self.alerts), 2)

    def test_stale_and_template_views_remain_open(self):
        for view in (None, NS(IsValidObject=True), View()):
            self.view = view
            if isinstance(view, View):
                view.IsTemplate = True
            self.click()
        self.assertEqual(len(self.alerts), 3)
        self.assertEqual(self.requests, [])

    def test_request_runs_only_after_modal_returns(self):
        def show():
            self.assertEqual(self.requests, [])
            self.click()
            self.assertEqual(self.requests, ['closed'])
            self.requests.append('modal returned')
        self.win.ShowDialog = show
        self.env['DWGManagementWindow'] = lambda document: self.win
        self.env['show_dwg_manager'](self.doc)
        self.assertEqual(self.requests, ['closed', 'modal returned', self.view])

    def test_changed_document_and_api_failure_are_reported(self):
        self.uidoc.Document = object()
        self.env['request_dwg_view'](self.doc, 42)
        self.assertEqual(self.requests, [])
        self.uidoc.Document = self.doc
        def fail(view):
            raise RuntimeError('cannot activate this view')
        self.uidoc.RequestViewChange = fail
        self.env['request_dwg_view'](self.doc, 42)
        self.assertEqual(len(self.alerts), 2)

    def test_view_revalidated_after_modal_closes(self):
        self.click()
        self.view = None
        self.env['request_dwg_view'](self.doc, 42)
        self.assertEqual(self.requests, ['closed'])
        self.assertEqual(len(self.alerts), 1)


if __name__ == '__main__':
    unittest.main()
