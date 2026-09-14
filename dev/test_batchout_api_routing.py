"""BatchOut must wait for an accepted API event; execute shipped routing code."""
import ast
from pathlib import Path
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_service_transactions import load_definitions

GUI = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI/T3LabAssistantDialog.py'


class StrictRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tasks = queue.Queue()
        module = load_definitions('Services/revit_context.py',
                                  _TASKS=self.tasks, _EVENT=None,
                                  HAS_REVIT_UI=False, _queue_mod=queue)
        self.run = module.run_in_api_context
        self.callback = Mock()
        self.action = Mock(return_value=(True, 'done'))

    def test_missing_event_refuses_action_and_completes_callback(self):
        self.assertEqual(self.run(self.action, self.callback, True), 'rejected')
        self.action.assert_not_called()
        self.assertFalse(self.callback.call_args.args[0])
        self.assertIn('unavailable', self.callback.call_args.args[1])

    def test_rejected_raise_removes_only_own_task_and_never_runs_inline(self):
        for outcome in ('Denied', 'TimedOut', RuntimeError('Disposed')):
            with self.subTest(outcome=outcome):
                self.setUp()
                other = (object(), Mock(), Mock())
                self.tasks.put(other)
                event = Mock()
                if isinstance(outcome, Exception):
                    event.Raise.side_effect = outcome
                else:
                    event.Raise.return_value = outcome
                self.run.__globals__['_EVENT'] = event
                self.assertEqual(self.run(self.action, self.callback, True), 'rejected')
                self.action.assert_not_called()
                self.assertFalse(self.callback.call_args.args[0])
                self.assertEqual(self.tasks.qsize(), 1)
                self.assertIs(self.tasks.get_nowait(), other)

    def test_accepted_and_pending_wait_for_handler(self):
        for outcome in ('Accepted', 'Pending'):
            with self.subTest(outcome=outcome):
                self.setUp()
                event = Mock()
                event.Raise.return_value = outcome
                self.run.__globals__['_EVENT'] = event
                self.assertEqual(self.run(self.action, self.callback, True), 'queued')
                self.action.assert_not_called()
                self.callback.assert_not_called()
                _, action, callback = self.tasks.get_nowait()
                callback(*action())
                self.callback.assert_called_once_with(True, 'done')

    def test_default_behavior_remains_compatible(self):
        self.assertEqual(self.run(self.action, self.callback), 'inline')
        self.action.assert_called_once()
        self.callback.assert_called_once_with(True, 'done')

    def test_reporter_exception_does_not_execute_rejected_work(self):
        self.callback.side_effect = RuntimeError('Window gone')
        self.assertEqual(self.run(self.action, self.callback, True), 'rejected')
        self.action.assert_not_called()


class AssistantBatchOutTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(GUI.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'T3LabAssistantWindow')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_execute_result')
        self.method_nodes = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
        self.pending = []
        self.export = Mock(return_value=(True, 2, 'Export completed: 2 outputs.'))
        self.configured = Mock(return_value=True)
        self.learn = Mock()
        self.doc = SimpleNamespace(IsValidObject=True)
        self.revit = SimpleNamespace(doc=self.doc)
        self.scope = dict(HAS_NLP=False, HAS_API_CONTEXT=True, HAS_EXECUTOR=True,
                          revit=self.revit, direct_export=self.export,
                          _load_batchout_mod=Mock(return_value='module'),
                          launch_batchout_configured=self.configured,
                          learn_pattern=self.learn, _exc_text=str,
                          run_in_api_context=self.enqueue)
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(GUI), 'exec'), self.scope)
        self.dialog = SimpleNamespace(_last_raw='export all', doc=self.doc,
                                      _append_bot_message=Mock(), _add_to_history=Mock(),
                                      _set_busy=Mock(), _ui_invoke=lambda action: action(),
                                      _request_id=1, _replied=False, _cancelled=Mock(return_value=False))
        def claim(rid):
            if rid != self.dialog._request_id or self.dialog._replied:
                return False
            self.dialog._replied = True
            return True
        self.dialog._claim_turn = claim

    def real_method(self, name):
        exec(compile(ast.Module(body=[self.method_nodes[name]], type_ignores=[]), str(GUI), 'exec'), self.scope)
        return self.scope[name]

    def enqueue(self, action, callback, require_api_context=False):
        self.assertTrue(require_api_context)
        self.pending.append((action, callback))
        return 'queued'

    def start(self, intent='export_direct'):
        self.scope['_execute_result'](self.dialog, {'intent': intent, 'params': {'format': 'pdf'}})

    def drain(self):
        action, callback = self.pending.pop(0)
        callback(*action())

    def test_direct_export_waits_for_api_and_releases_busy_after_result(self):
        self.start()
        self.export.assert_not_called()
        self.dialog._set_busy.assert_not_called()
        self.drain()
        self.export.assert_called_once()
        self.dialog._set_busy.assert_called_once_with(False)
        self.dialog._append_bot_message.assert_called_with('Export completed: 2 outputs.')
        self.learn.assert_called_once()

    def test_partial_export_keeps_result_and_does_not_learn_success(self):
        self.export.return_value = (False, 1, 'Export incomplete: 1 of 2 outputs.')
        self.start()
        self.drain()
        self.learn.assert_not_called()
        self.dialog._append_bot_message.assert_called_with('Export incomplete: 1 of 2 outputs.')
        self.dialog._set_busy.assert_called_once_with(False)

    def test_configured_launch_is_also_queued(self):
        self.start('open_batchout_configured')
        self.configured.assert_not_called()
        self.drain()
        self.configured.assert_called_once()
        self.export.assert_not_called()

    def test_document_switch_or_close_prevents_export(self):
        for closed in (True, False):
            with self.subTest(closed=closed):
                self.setUp()
                self.start()
                if closed:
                    self.doc.IsValidObject = False
                else:
                    self.revit.doc = SimpleNamespace(IsValidObject=True, Title='Different model')
                self.drain()
                self.export.assert_not_called()
                self.learn.assert_not_called()
                self.dialog._set_busy.assert_called_once_with(False)

    def test_missing_runner_never_uses_inline_fallback(self):
        self.scope['HAS_API_CONTEXT'] = False
        self.start()
        self.assertFalse(self.pending)
        self.export.assert_not_called()
        self.dialog._set_busy.assert_called_once_with(False)

    def test_rejected_queue_releases_busy_without_learning(self):
        self.scope['run_in_api_context'] = lambda action, callback, **kw: callback(False, 'Denied')
        self.start()
        self.export.assert_not_called()
        self.dialog._append_bot_message.assert_called_with('Denied')
        self.dialog._set_busy.assert_called_once_with(False)
        self.learn.assert_not_called()

    def test_stop_before_api_execution_creates_no_files(self):
        self.start()
        self.dialog._cancelled.return_value = True
        self.drain()
        self.export.assert_not_called()
        self.dialog._set_busy.assert_called_once_with(False)

    def test_stale_request_does_not_export_or_release_new_turn(self):
        self.start()
        self.dialog._request_id += 1
        self.drain()
        self.export.assert_not_called()
        self.dialog._set_busy.assert_not_called()

    def test_export_receives_live_stop_callback(self):
        self.start()
        self.drain()
        callback = self.export.call_args.kwargs['cancel_check']
        self.assertFalse(callback())
        self.dialog._cancelled.return_value = True
        self.assertTrue(callback())

    def test_actual_route_finally_cannot_release_queued_export(self):
        process = self.method_nodes['_process_input']
        route = next(n for n in ast.walk(process) if isinstance(n, ast.FunctionDef) and n.name == '_route')
        set_busy = self.real_method('_set_busy')
        self.dialog._busy = True
        self.dialog._set_busy = lambda value: set_busy(self.dialog, value)
        self.dialog._forced_skill_id = None
        self.dialog._log_activity = Mock()
        self.dialog._route_input = lambda *_: self.start()
        self.scope.update(self=self.dialog, route_text='export all', attached=[], _rid=1)
        exec(compile(ast.Module(body=[route], type_ignores=[]), str(GUI), 'exec'), self.scope)
        self.scope['_route']()
        self.assertTrue(self.dialog._busy)
        self.assertEqual(len(self.pending), 1)
        self.export.assert_not_called()

    def test_stop_watchdog_defers_to_partial_output_result_during_native_call(self):
        finish_cancelled = self.real_method('_finish_cancelled')
        def native_export(*args, **kwargs):
            self.dialog._cancelled.return_value = True
            finish_cancelled(self.dialog, 1, 'watchdog')
            self.assertFalse(self.dialog._replied)
            return False, 1, 'Export stopped. 1 PDF output was kept.'
        self.export.side_effect = native_export
        self.start()
        self.drain()
        self.dialog._append_bot_message.assert_called_with('Export stopped. 1 PDF output was kept.')
        self.dialog._set_busy.assert_called_once_with(False)
        self.assertIsNone(self.dialog._batchout_request_id)

    def test_stale_watchdog_cannot_clear_new_batchout_ownership(self):
        finish_cancelled = self.real_method('_finish_cancelled')
        self.start()
        finish_cancelled(self.dialog, 0)
        self.assertEqual(self.dialog._batchout_request_id, 1)
        self.assertFalse(self.dialog._replied)
        self.dialog._set_busy.assert_not_called()

    def test_llm_finish_transfers_claim_to_api_result(self):
        finish = next(n for n in ast.walk(self.method_nodes['_route_input'])
                      if isinstance(n, ast.FunctionDef) and n.name == 'finish'
                      and any(isinstance(x, ast.Attribute) and x.attr == '_execute_result'
                              for x in ast.walk(n)))
        self.dialog._stream_tb = None
        self.dialog._remove_stream_bubble = Mock()
        self.dialog._clear_stream_refs = Mock()
        self.dialog._hide_typing_indicator = Mock()
        self.dialog._report_error = Mock()
        self.dialog._execute_result = lambda result: self.scope['_execute_result'](self.dialog, result)
        self.scope.update(self=self.dialog, rid=1,
                          result={'intent': 'export_direct', 'params': {'format': 'pdf'}},
                          nlu_hint=None)
        exec(compile(ast.Module(body=[finish], type_ignores=[]), str(GUI), 'exec'), self.scope)
        self.scope['finish']()
        self.dialog._report_error.assert_not_called()
        self.assertEqual(len(self.pending), 1)
        self.assertFalse(self.dialog._replied)
        self.drain()
        self.dialog._append_bot_message.assert_called_with('Export completed: 2 outputs.')
        self.assertTrue(self.dialog._replied)

    def test_duplicate_handoff_does_not_queue_two_exports(self):
        self.start()
        self.start()
        self.assertEqual(len(self.pending), 1)


if __name__ == '__main__':
    unittest.main()
