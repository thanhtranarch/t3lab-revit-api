"""Exercise shipped FamiGen workflow callbacks without Revit or WPF."""
import ast
import codecs
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

GUI = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI'
spec = importlib.util.spec_from_file_location('family_schema', GUI.parent / 'Intelligence/family_schema.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def valid():
    return dict(family_name='Chair', family_category='Furniture', geometry=[
        dict(type='Cylinder', start=[0, 0, 0], end=[0, 0, 500], radius=20)])


class Control:
    def __init__(self, enabled=True, **attrs):
        self._enabled = enabled
        self.fail_disable = False
        self.fail_restore = False
        self.__dict__.update(attrs)

    @property
    def IsEnabled(self):
        return self._enabled

    @IsEnabled.setter
    def IsEnabled(self, value):
        if (not value and self.fail_disable) or (value and self.fail_restore):
            raise RuntimeError('control setter failed')
        self._enabled = value


class Poison:
    def __getattribute__(self, name):
        raise AssertionError('worker/callback accessed disposed UI: ' + name)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.alerts, self.calls, self.pending = [], [], []
        env = dict(json=json, os=os, codecs=codecs, WinVis=NS(Visible='visible', Collapsed='collapsed'),
                   forms=NS(alert=lambda text, **kw: self.alerts.append(text)),
                   logger=NS(warning=lambda message: None),
                   generate_family_schema=self.generate, validate_ai_schema=contract.validate_ai_schema)
        source = ast.parse((GUI / 'FamiGenDialog.py').read_text(encoding='utf-8'))
        original = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'FamilyCreatorDialog')
        names = {'ai_generate_clicked', '_set_ai_generation_busy', 'ai_undo_clicked',
                 'ai_window_closed', '_show_panel', 'create_clicked'}
        cls = ast.ClassDef(name='Window', bases=[], keywords=[],
                           body=[n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in names], decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(GUI / 'FamiGenDialog.py'), 'exec'), env)
        self.window = env['Window']()
        w = self.window
        w._ai_generating, w._ai_closed, w._ai_request_id = False, False, 0
        w._ai_control_states, w._prev_json_backup = [], None
        for name in ('btn_ai_generate', 'ai_prompt_tb', 'json_category_combo', 'json_tb', 'create_btn',
                     'btn_ai_undo', 'copy_prompt_btn', 'mode_cad', 'mode_json', 'lbl_status', 'btn_export', 'panel_cad', 'panel_json'):
            setattr(w, name, Control(Text='', Visibility='collapsed'))
        w.ai_prompt_tb.Text = 'a chair in mm'
        w.json_category_combo.SelectedItem = 'Furniture'
        w.json_tb.Text = '  existing draft\n'
        w.copy_prompt_btn.IsEnabled = False
        w.ai_bridge = object()
        w.ai_require = lambda: True
        w._overlay_path = lambda category: None
        w.run_ai_async = lambda *callbacks: self.pending.append(callbacks)
        w.FindName = lambda name: getattr(w, name, None)
        w.FindResource = lambda name: name
        self.result = valid()

    def generate(self, *args):
        self.calls.append(args)
        return self.result

    def start(self):
        self.window.ai_generate_clicked(None, None)
        return self.pending[-1]

    def test_valid_publish_exact_undo_and_prior_disabled_restored(self):
        worker, done, error = self.start()
        done(worker())
        w = self.window
        self.assertEqual(json.loads(w.json_tb.Text), valid())
        self.assertEqual(w._prev_json_backup, '  existing draft\n')
        self.assertFalse(w._ai_generating)
        self.assertTrue(w.create_btn.IsEnabled)
        self.assertFalse(w.copy_prompt_btn.IsEnabled)
        w.ai_undo_clicked(None, None)
        self.assertEqual(w.json_tb.Text, '  existing draft\n')
        self.assertEqual(w.btn_ai_undo.Visibility, 'collapsed')

    def test_invalid_callback_preserves_draft_and_existing_undo(self):
        self.window._prev_json_backup = 'earlier backup'
        _, done, _ = self.start()
        done({'geometry': []})
        self.assertEqual(self.window.json_tb.Text, '  existing draft\n')
        self.assertEqual(self.window._prev_json_backup, 'earlier backup')
        self.assertFalse(self.window._ai_generating)
        self.assertTrue(self.alerts)

    def test_worker_uses_snapshots_without_ui_access(self):
        bridge = self.window.ai_bridge
        worker, _, _ = self.start()
        self.window.ai_bridge = Poison()
        self.window.ai_prompt_tb = Poison()
        self.window.json_category_combo = Poison()
        worker()
        self.assertEqual(self.calls, [(bridge, 'a chair in mm', 'Furniture', '')])

    def test_duplicate_busy_request_and_undo_do_nothing(self):
        self.start()
        self.window.ai_generate_clicked(None, None)
        self.window._prev_json_backup = 'backup'
        self.window.ai_undo_clicked(None, None)
        self.assertEqual(len(self.pending), 1)
        self.assertEqual(self.window.json_tb.Text, '  existing draft\n')

    def test_error_and_schedule_failure_restore_states(self):
        _, _, error = self.start()
        error(RuntimeError('provider failed'))
        self.assertTrue(self.window.create_btn.IsEnabled)
        self.assertFalse(self.window.copy_prompt_btn.IsEnabled)
        def fail(*callbacks):
            raise RuntimeError('schedule failed')
        self.window.run_ai_async = fail
        self.window.ai_generate_clicked(None, None)
        self.assertFalse(self.window._ai_generating)
        self.assertTrue(self.window.btn_ai_generate.IsEnabled)
        self.assertEqual(self.window.json_tb.Text, '  existing draft\n')

    def test_partial_busy_setup_failure_restores_prior_controls(self):
        self.window.json_category_combo.fail_disable = True
        self.window.ai_generate_clicked(None, None)
        self.assertFalse(self.window._ai_generating)
        self.assertTrue(self.window.btn_ai_generate.IsEnabled)
        self.assertTrue(self.window.ai_prompt_tb.IsEnabled)
        self.assertFalse(self.window.copy_prompt_btn.IsEnabled)
        self.assertEqual(self.pending, [])

    def test_restore_failure_does_not_skip_remaining_controls(self):
        _, _, error = self.start()
        self.window.btn_ai_generate.fail_restore = True
        error(RuntimeError('failed'))
        self.assertTrue(self.window.ai_prompt_tb.IsEnabled)
        self.assertTrue(self.window.create_btn.IsEnabled)
        self.assertFalse(self.window._ai_generating)

    def test_close_invalidates_callbacks_without_control_access(self):
        _, done, error = self.start()
        request = self.window._ai_request_id
        self.window.ai_window_closed(None, None)
        self.assertTrue(self.window._ai_closed)
        self.assertGreater(self.window._ai_request_id, request)
        self.assertFalse(self.window._ai_generating)
        self.assertEqual(self.window._ai_control_states, [])
        for name in ('json_tb', 'lbl_status', 'btn_ai_undo'):
            setattr(self.window, name, Poison())
        done(valid())
        error(RuntimeError('late failure'))

    def test_changed_draft_is_preserved_and_restored_editable(self):
        _, done, _ = self.start()
        self.window.json_tb.Text = 'new manual draft'
        done(valid())
        self.assertEqual(self.window.json_tb.Text, 'new manual draft')
        self.assertTrue(self.window.json_tb.IsEnabled)
        self.assertIsNone(self.window._prev_json_backup)

    def test_empty_draft_can_be_undone_exactly(self):
        self.window.json_tb.Text = ''
        worker, done, _ = self.start()
        done(worker())
        self.window.ai_undo_clicked(None, None)
        self.assertEqual(self.window.json_tb.Text, '')
        self.assertIsNone(self.window._prev_json_backup)

    def test_missing_category_or_description_never_schedules(self):
        self.window.json_category_combo.SelectedItem = None
        self.window.ai_generate_clicked(None, None)
        self.window.json_category_combo.SelectedItem = 'Furniture'
        self.window.ai_prompt_tb.Text = '  '
        self.window.ai_generate_clicked(None, None)
        self.assertEqual(self.pending, [])
        self.assertEqual(len(self.alerts), 2)

    def test_each_mode_has_one_primary_and_default(self):
        for mode in ('json', 'cad'):
            self.window._show_panel(mode)
            expected = self.window.create_btn if mode == 'json' else self.window.btn_export
            other = self.window.btn_export if mode == 'json' else self.window.create_btn
            self.assertEqual(expected.Style, 'T3.Button.Primary')
            self.assertTrue(expected.IsDefault)
            self.assertEqual(other.Style, 'T3.Button.Secondary')
            self.assertFalse(other.IsDefault)

    def test_create_invalid_json_stops_before_document_access(self):
        self.window._doc = Poison()
        for schema in ([], {}, {'geometry': []}, {'geometry': [1]}, None):
            self.window.json_tb.Text = json.dumps(schema)
            self.window.create_clicked(None, None)
        self.assertEqual(len(self.alerts), 5)


if __name__ == '__main__':
    unittest.main()
