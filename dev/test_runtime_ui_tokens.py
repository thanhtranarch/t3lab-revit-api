"""Exercise runtime UI state transitions with shared resource sentinels."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import sys
from unittest.mock import patch
GUI = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI'


def load(file, name, env=None):
    tree = ast.parse((GUI / file).read_text(encoding='utf-8'))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    scope = {} if env is None else env
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, 'exec'), scope)
    return scope[name]


class RuntimeUI(unittest.TestCase):
    def test_select_sidebar_all_modes_with_fresh_wrappers(self):
        env = dict(MODE_EXPLORE=0, MODE_QUICK=1, MODE_SIMILAR=2, MODE_SHEETS=3, MODE_WARNINGS=4)
        handler = load('ManaSelectDialog.py', '_on_nav_toggle_clicked', env)
        modes = []
        window = NS(_mode=3, _apply_mode=modes.append)
        for name in ('explore', 'quick_select', 'select_similar', 'select_sheets', 'warnings'):
            setattr(window, 'nav_toggle_' + name, NS(Name='nav_toggle_' + name))
            handler(window, NS(Name='nav_toggle_' + name), None)
        self.assertEqual(modes, list(range(5)))
        handler(window, NS(Name='other'), None)
        self.assertEqual(modes[-1], 3)

    def test_property_status_resource_and_label_semantics(self):
        handler = load('PropertyLineDialog.py', '_set_status')
        window = NS(txt_status=NS(), dot_status=NS(), txt_status_label=NS(), FindResource=lambda key: key)
        for flags, expected in (({}, ('T3.TextMuted', 'T3.TextMuted', 'Idle')),
                                ({'error': True}, ('T3.Danger.Text', 'T3.Danger.Accent', 'Error')),
                                ({'success': True}, ('T3.Success.Text', 'T3.Success.Accent', 'Done')),
                                ({'busy': True}, ('T3.Progress.Fill', 'T3.Progress.Fill', 'Working...'))):
            handler(window, 'message', **flags)
            self.assertEqual((window.txt_status.Foreground, window.dot_status.Fill, window.txt_status_label.Text), expected)
            self.assertEqual(window.txt_status.Text, 'message')

    def test_feedback_status_semantics(self):
        handler = load('FeedbackDialog.py', '_set_status')
        window = NS(status_text=NS(), FindResource=lambda key: key)
        handler(window, 'ready')
        self.assertEqual(window.status_text.Foreground, 'T3.TextMuted')
        handler(window, 'failed', error=True)
        self.assertEqual(window.status_text.Foreground, 'T3.Danger.Text')
        self.assertEqual(window.status_text.Text, 'failed')

    def test_batchout_expansion_preserves_visibility_and_uses_tokens(self):
        handler = load('BatchOutDialog.py', '_toggle_format_panel',
                       {'logger': NS(debug=lambda message: None)})
        visibility = NS(Collapsed='collapsed', Visible='visible')
        window = NS(body=NS(Visibility=visibility.Collapsed), arrow=NS(), border=NS(),
                    FindResource=lambda key: key)
        with patch.dict(sys.modules, {'System.Windows': NS(Visibility=visibility)}):
            handler(window, 'body', 'arrow', 'border')
            self.assertEqual((window.body.Visibility, window.arrow.Text, window.border.BorderBrush),
                             ('visible', '\uE70E', 'T3.Ink'))
            handler(window, 'body', 'arrow', 'border')
            self.assertEqual((window.body.Visibility, window.arrow.Text, window.border.BorderBrush),
                             ('collapsed', '\uE70D', 'T3.Border'))

    def test_mcp_shared_helpers_preserve_button_behavior(self):
        env = {'HAS_SERVICE': True}
        env['_brush'] = load('MCPControlDialog.py', '_brush')
        env['_apply_btn_style'] = load('MCPControlDialog.py', '_apply_btn_style')
        resources = {key: object() for key in ('T3.Success.Accent', 'T3.Danger.Accent', 'T3.TextMuted',
                      'T3.TextDisabled', 'T3.Button.Primary', 'T3.Button.Danger', 'T3.Button.Secondary')}
        server = load('MCPControlDialog.py', 'apply_server_status', env)
        for status, key, text, enabled in (({}, 'T3.TextMuted', 'Start Server', True),
                      ({'running': True}, 'T3.Success.Accent', 'Stop Server', True),
                      ({'running': True, 'foreign': True}, 'T3.Success.Accent', 'Stop Server', False),
                      ({'error': 'failed'}, 'T3.Danger.Accent', 'Start Server', True)):
            dot, label, btn = NS(), NS(), NS()
            server(status, dot, label, btn, resources)
            self.assertIs(dot.Background, resources[key])
            self.assertEqual((btn.Content, btn.IsEnabled), (text, enabled))
        watcher = load('MCPControlDialog.py', 'apply_watcher_status', env)
        for status, key in (({}, 'T3.TextMuted'), ({'running': True}, 'T3.Success.Accent'), ({'error': 'failed'}, 'T3.TextDisabled')):
            dot, label, btn = NS(), NS(), NS()
            watcher(status, dot, label, btn, resources)
            self.assertIs(dot.Background, resources[key])

    def test_mcp_target_resources_and_missing_brush(self):
        brush = load('MCPControlDialog.py', '_brush')
        expected = object()
        self.assertIs(brush('T3.TextMuted', NS(TryFindResource=lambda key: expected)), expected)
        self.assertIsNone(brush('T3.TextMuted'))


if __name__ == '__main__':
    unittest.main()
