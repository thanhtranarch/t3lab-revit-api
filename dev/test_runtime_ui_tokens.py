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
    def test_select_primary_button_shows_checked_count(self):
        # ManaSelect chỉ còn Explore (2026-10-02): nút primary mang số đã tick
        # để người dùng biết Select sẽ chọn bao nhiêu trước khi bấm.
        handler = load('ManaSelectDialog.py', '_update_selected_count')
        window = NS(txt_explore_tally_checked=NS(), btn_apply=NS())
        for ids, label in (([], 'Select'), ([1, 2, 3], 'Select (3)')):
            window._checked_ids = lambda ids=ids: ids
            handler(window)
            self.assertEqual(window.btn_apply.Content, label)
            self.assertEqual(window.txt_explore_tally_checked.Text, '%d checked' % len(ids))

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

    def test_mcp_footer_names_first_unfinished_step(self):
        # MCP Control (2026-10-02): the footer states the first setup step that
        # is not done yet. It has no button since 2026-10-02 — Start/Stop Server
        # is Step 1's button and the title-bar X closes the window.
        env = {'HAS_SERVICE': True, '_SVC_ERR_MSG': '', 'CLIENT_KEYS': ['a', 'b'],
               'DEFAULT_PORT': 48884}
        footer = load('MCPControlDialog.py', '_refresh_footer', env)
        ok = {'configured': True}
        cases = (
            ({}, {}, False, None, ('T3.TextMuted', 'Server stopped')),
            ({'error': 'boom'}, {}, False, None, ('T3.Danger.Accent', 'Server error')),
            ({}, {}, False, 'port busy', ('T3.Danger.Accent', 'Server error')),
            ({'running': True}, {'a': {}}, False, None,
             ('T3.Warning.Accent', 'No AI app connected')),
            ({'running': True}, {'a': ok}, False, None,
             ('T3.Warning.Accent', 'No active model')),
            ({'running': True, 'port': 48885}, {'a': ok, 'b': {}}, True, None,
             ('T3.Success.Accent', 'Ready · port 48885')),
        )
        for server, clients, doc_ok, err, expected in cases:
            dots = []
            window = NS(_server_state=server, _client_states=clients, _active_doc_ok=doc_ok,
                        _server_error=err, _footer_dot=NS(), _footer_status=NS())
            window._set_dot = lambda dot, key, dots=dots: dots.append(key)
            footer(window)
            self.assertEqual((dots[-1], window._footer_status.Text), expected)
        self.assertIn('1 of 2', window._footer_status.ToolTip)

    def test_mcp_target_resources_and_missing_brush(self):
        brush = load('MCPControlDialog.py', '_brush')
        expected = object()
        self.assertIs(brush('T3.TextMuted', NS(TryFindResource=lambda key: expected)), expected)
        self.assertIsNone(brush('T3.TextMuted'))


if __name__ == '__main__':
    unittest.main()
