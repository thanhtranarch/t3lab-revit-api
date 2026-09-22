"""Execute production BatchOut queue code without WPF/Revit."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib'

def definitions(path, cls_name, wanted=None):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8-sig'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    cls.bases = []
    if wanted:
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    scope = dict(logger=Mock(), DispatcherPriority=SimpleNamespace(Background=0), Action=lambda f: f)
    exec(compile(ast.Module(body=[cls], type_ignores=[]), path, 'exec'), scope)
    return scope[cls_name]

Handler = definitions('Services/batchout_handler.py', 'BatchOutEventHandler')
Window = definitions('GUI/BatchOutDialog.py', 'ExportManagerWindow',
                     {'_run_in_api_context', 'go_next', '_window_closed_dispose'})

class WindowQueueTests(unittest.TestCase):
    def setUp(self):
        self.win = Window()
        self.win.modeless = True
        self.win._api_handler = Handler(Mock())
        self.win._api_event = Mock()
        self.win.status_text = SimpleNamespace(Text='')
        self.win.main_tabs = SimpleNamespace(SelectedIndex=2)
        self.win._export_running = False
        self.win.IsEnabled = True
        self.win.start_export = Mock()

    def test_denied_request_unfreezes_window_and_never_runs_later(self):
        for response in ('Denied', 'TimedOut', RuntimeError('disposed')):
            with self.subTest(response=response):
                self.setUp()
                other = Mock()
                self.win._api_handler.add(other)
                if isinstance(response, Exception):
                    self.win._api_event.Raise.side_effect = response
                else:
                    self.win._api_event.Raise.return_value = response
                self.win.go_next(None, None)
                self.assertTrue(self.win.IsEnabled)
                self.assertFalse(self.win._export_running)
                self.win._api_handler.Execute(None)
                self.win.start_export.assert_not_called()
                other.assert_called_once()

    def test_accepted_and_pending_run_only_inside_handler(self):
        for response in ('Accepted', 'Pending'):
            with self.subTest(response=response):
                self.setUp()
                self.win._api_event.Raise.return_value = response
                self.win.go_next(None, None)
                self.assertFalse(self.win.IsEnabled)
                self.win.start_export.assert_not_called()
                self.win._api_handler.Execute(None)
                self.win.start_export.assert_called_once()

    def test_missing_event_recovers_without_queueing(self):
        self.win._api_event = None
        self.win.go_next(None, None)
        self.assertTrue(self.win.IsEnabled)
        self.assertFalse(self.win._api_handler._queue)

    def test_close_discards_queued_actions(self):
        self.win._api_handler.add(self.win.start_export)
        event = self.win._api_event
        self.win._window_closed_dispose(None, None)
        self.win._api_handler.Execute(None)
        self.win.start_export.assert_not_called()
        event.Dispose.assert_called_once()

    def test_modal_dispatch_failure_also_recovers(self):
        self.win.modeless = False
        self.win.Dispatcher = Mock()
        self.win.Dispatcher.BeginInvoke.side_effect = RuntimeError('shutdown')
        self.win.go_next(None, None)
        self.assertTrue(self.win.IsEnabled)
        self.assertFalse(self.win._export_running)

if __name__ == '__main__':
    unittest.main()
