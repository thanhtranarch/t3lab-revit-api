# -*- coding: utf-8 -*-
"""
MCP Control Dialog

Thin WPF wrapper around MCPService. All backend logic lives in
Services/mcp_service.py and can be reused by any other tool.

Layout — three tabs, ordered by who needs them:
  Setup         the three steps every user follows, in order: start the
                server, connect an AI app, ask it about the active model.
  Manual Config paste-it-yourself entry for apps not listed in Setup.
  Advanced      auto-start, port, file task watcher, teaching capture.
The footer always states how ready the whole chain is, and its one primary
button names the next step: "Start Server" while the server is down, "Done"
once it runs.
"""

import os
import sys

try:
    import clr
    for _ref in ('System', 'WindowsBase', 'PresentationCore', 'PresentationFramework'):
        try:
            clr.AddReference(_ref)
        except Exception:
            pass
except Exception:
    clr = None

try:
    from System.Windows import WindowState
except Exception:
    WindowState = None

from pyrevit import forms, script
from GUI.WPF_Base import T3WPFWindow

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'MCPControl.xaml')

# ─── Backend service ───────────────────────────────────────────────────────────
try:
    _LIB_DIR = os.path.dirname(os.path.dirname(__file__))
    if _LIB_DIR not in sys.path:
        sys.path.insert(0, _LIB_DIR)
    from Services.mcp_service import MCPService
    HAS_SERVICE = True
except Exception as _svc_err:
    HAS_SERVICE  = False
    _SVC_ERR_MSG = str(_svc_err)

# Auto-configurable MCP clients, in the order their rows appear in the XAML.
# Read from the service so adding a client there needs no change here.
try:
    CLIENTS = [(key, label) for key, label, _fmt in MCPService.clients()]
except Exception:
    CLIENTS = [('claude', 'Claude Desktop'), ('chatgpt', 'ChatGPT (Codex)'),
               ('antigravity', 'Antigravity')]
CLIENT_KEYS = [key for key, _label in CLIENTS]

DEFAULT_PORT = 48884

logger = script.get_logger()


def _brush(key, target=None, resources=None):
    """Resolve a shared T3 semantic brush for standalone or embedded widgets."""
    if target is not None:
        try:
            brush = target.TryFindResource(key)
            if brush is not None:
                return brush
        except Exception:
            pass
    if resources is not None:
        try:
            return resources[key]
        except Exception:
            pass
    return None


def _apply_btn_style(btn, style_key, resources=None):
    """Safely apply a resource style to a button using WPF TryFindResource."""
    if not btn or not style_key:
        return
    try:
        if hasattr(btn, 'TryFindResource'):
            st = btn.TryFindResource(style_key)
            if st is not None:
                btn.Style = st
                return
    except Exception:
        pass
    try:
        if resources is not None:
            if hasattr(resources, 'Contains') and resources.Contains(style_key):
                btn.Style = resources[style_key]
                return
            elif hasattr(resources, '__contains__') and style_key in resources:
                btn.Style = resources[style_key]
                return
    except Exception:
        pass


# ─── Status helpers shared with embedded widgets ───────────────────────────────

def apply_server_status(status, indicator, label, btn, resources=None):
    """
    Update server status widgets from an MCPService.server_status() dict.
    All widget args may be None (skipped gracefully).

    The button is never Primary here: the window keeps exactly one Primary
    (the footer's next-step button), so Start is Secondary and Stop is Danger.
    """
    if not status:
        status = {}
    if status.get('error'):
        color, text = 'T3.Danger.Accent', 'Error: {}'.format(status['error'])
        btn_content, btn_style_key = 'Start Server', 'T3.Button.Secondary'
        enabled = True
    elif status.get('running') and status.get('foreign'):
        # Live and serving, but owned by another pyRevit engine in this Revit
        # session — its Python object is not callable from here, so Stop would
        # only throw. Say who owns it and what actually releases it.
        color       = 'T3.Success.Accent'
        text        = ('Running on port {} — started by another pyRevit engine; '
                       'restart Revit to stop it').format(status.get('port', 48884))
        btn_content = 'Stop Server'
        btn_style_key = 'T3.Button.Danger'
        enabled     = False
    elif status.get('running'):
        color       = 'T3.Success.Accent'
        text        = 'Running on port {}'.format(status.get('port', 48884))
        btn_content = 'Stop Server'
        btn_style_key = 'T3.Button.Danger'
        enabled     = True
    else:
        color, text = 'T3.TextMuted', 'Stopped'
        btn_content, btn_style_key = 'Start Server', 'T3.Button.Secondary'
        enabled = True

    if indicator:
        b = _brush(color, indicator, resources)
        if b is not None:
            indicator.Background = b
    if label:
        label.Text = text
    if btn:
        btn.Content   = btn_content
        btn.IsEnabled = enabled
        _apply_btn_style(btn, btn_style_key, resources)


def apply_watcher_status(status, indicator, label, btn, resources=None):
    """
    Update watcher status widgets from an MCPService.watcher_status() dict.
    """
    if not status:
        status = {}
    if not HAS_SERVICE or status.get('error'):
        err = status.get('error', 'Service unavailable') if status else 'Service unavailable'
        if indicator:
            b = _brush('T3.TextDisabled', indicator, resources)
            if b is not None:
                indicator.Background = b
        if label:
            label.Text = err
        if btn:
            btn.IsEnabled = False
        return

    if status.get('running'):
        color = 'T3.Success.Accent'
        text  = 'Running — watching task.json'
        btn_content, btn_style_key = 'Stop Watcher', 'T3.Button.Danger'
    else:
        color = 'T3.TextMuted'
        text  = 'Stopped'
        btn_content, btn_style_key = 'Start Watcher', 'T3.Button.Secondary'

    if indicator:
        b = _brush(color, indicator, resources)
        if b is not None:
            indicator.Background = b
    if label:
        label.Text = text
    if btn:
        btn.Content   = btn_content
        btn.IsEnabled = True
        _apply_btn_style(btn, btn_style_key, resources)


# ─── Dialog ────────────────────────────────────────────────────────────────────

class MCPControlWindow(T3WPFWindow):
    """
    MCP Control dialog — thin UI layer over MCPService.
    """

    def __init__(self):
        T3WPFWindow.__init__(self, _XAML)

        # Last known state of each step — the footer summary and the primary
        # button are derived from these, never re-queried on their own.
        self._server_state  = {}
        self._server_error  = None     # last Start/Stop failure, shown in step 1
        self._client_states = {}
        self._active_doc_ok = False
        self._auto_start    = True

        # Tabs (chips are wired from the XAML: Checked="tab_chip_checked")
        self._tab_control = self.FindName('tab_control')

        # Step 1 · server
        self.toggle_btn        = self.FindName('toggle_btn')
        self.status_indicator  = self.FindName('status_indicator')
        self.status_label      = self.FindName('status_label')
        self._server_hint      = self.FindName('server_hint_label')
        if self.toggle_btn:
            self.toggle_btn.Click += self._on_toggle

        # Step 2 · AI apps — one row per client key, plus "Connect All".
        # Widget names follow <key>_cfg_* so a client added to
        # MCPService.AI_CLIENTS only needs its row in the XAML.
        self._client_labels  = dict(CLIENTS)
        self._client_widgets = {}
        # Strong refs to the per-client closures: PythonNet wraps each one in a
        # .NET delegate, and a closure kept alive only by the event can be
        # collected — the button then silently stops responding.
        self._client_handlers = []
        for key in CLIENT_KEYS:
            btn = self.FindName('configure_{}_btn'.format(key))
            self._client_widgets[key] = {
                'indicator': self.FindName('{}_cfg_indicator'.format(key)),
                'label':     self.FindName('{}_cfg_label'.format(key)),
                'button':    btn,
                'tooltip':   btn.ToolTip if btn else None,
            }
            if btn:
                handler = self._make_configure_handler(key)
                self._client_handlers.append(handler)
                btn.Click += handler

        self._configure_all_label = self.FindName('configure_all_label')
        configure_all_btn         = self.FindName('configure_all_btn')
        if configure_all_btn:
            configure_all_btn.Click += self._on_configure_all

        # Step 3 · active model (read-only status)
        self._doc_indicator = self.FindName('active_doc_indicator')
        self._doc_label     = self.FindName('active_doc_label')

        # Manual Config tab
        self.config_box          = self.FindName('config_box')
        self.copy_btn            = self.FindName('copy_btn')
        self._copy_status        = self.FindName('copy_status_label')
        self._snippet_format_cb  = self.FindName('snippet_format_cb')
        if self.copy_btn:
            self.copy_btn.Click += self._on_copy
        if self._snippet_format_cb:
            self._snippet_format_cb.SelectionChanged += self._on_snippet_format_changed

        # Advanced tab · server
        self._auto_start_toggle = self.FindName('auto_start_toggle')
        self.port_tb            = self.FindName('port_tb')
        self._port_hint         = self.FindName('port_hint_label')
        self._port_hint_default = self._port_hint.Text if self._port_hint else ''
        if self._auto_start_toggle:
            self._auto_start_toggle.Click += self._on_auto_start_toggle
        if self.port_tb:
            self.port_tb.TextChanged += self._on_port_changed

        # Advanced tab · file watcher
        self._watcher_indicator = self.FindName('watcher_indicator')
        self._watcher_label     = self.FindName('watcher_label')
        self._watcher_btn       = self.FindName('watcher_toggle_btn')
        self._dir_label         = self.FindName('data_dir_label')
        open_dir_btn            = self.FindName('open_dir_btn')
        if self._watcher_btn:
            self._watcher_btn.Click += self._on_watcher_toggle
        if open_dir_btn:
            open_dir_btn.Click += self._on_open_dir

        # Advanced tab · teaching capture
        self._teaching_toggle    = self.FindName('teaching_toggle')
        self._teaching_indicator = self.FindName('teaching_indicator')
        self._teaching_label     = self.FindName('teaching_status_label')
        self._sandbox_label      = self.FindName('sandbox_label')
        mark_sandbox_btn         = self.FindName('mark_sandbox_btn')
        if self._teaching_toggle:
            self._teaching_toggle.Click += self._on_teaching_toggle
        if mark_sandbox_btn:
            mark_sandbox_btn.Click += self._on_mark_sandbox

        # Footer
        self._footer_dot    = self.FindName('footer_dot')
        self._footer_status = self.FindName('footer_status')
        self._next_btn      = self.FindName('next_btn')
        refresh_btn         = self.FindName('refresh_btn')
        if self._next_btn:
            self._next_btn.Click += self._on_next
        if refresh_btn:
            refresh_btn.Click += self._on_refresh

        self._init_port()
        self._refresh_all()

    # ── Window chrome ──────────────────────────────────────────────────────────

    def minimize_button_clicked(self, sender, e):
        if WindowState is not None:
            self.WindowState = WindowState.Minimized

    def close_button_clicked(self, sender, e):
        self.Close()

    def tab_chip_checked(self, sender, e):
        if self._tab_control is None:
            return
        try:
            self._tab_control.SelectedIndex = int(str(sender.Tag))
        except Exception as ex:
            logger.debug('Tab switch failed: {}'.format(ex))

    # ── Small helpers ──────────────────────────────────────────────────────────

    def _set_dot(self, dot, key):
        if dot is None:
            return
        b = _brush(key, resources=self.Resources)
        if b is not None:
            dot.Background = b

    def _can_start_server(self):
        """The footer's next step is Start Server only when that can work."""
        state = self._server_state or {}
        return HAS_SERVICE and not state.get('running') and not state.get('error')

    def _current_port(self):
        """Valid port typed in the box, or None to let the service decide."""
        try:
            port = int(self.port_tb.Text.strip()) if self.port_tb else None
        except Exception:
            return None
        if port is None or not (1024 <= port <= 65535):
            return None
        return port

    # ── Init helpers ───────────────────────────────────────────────────────────

    def _init_port(self):
        port = DEFAULT_PORT
        if HAS_SERVICE:
            try:
                port = MCPService.server_status().get('port', DEFAULT_PORT)
            except Exception:
                pass
        if self.port_tb:
            self.port_tb.Text = str(port)

    # ── Refresh ────────────────────────────────────────────────────────────────

    def _refresh_all(self):
        # Order matters: the step-1 hint reads the auto-start flag, and the
        # footer reads every step's state.
        for name, refresh in (('auto-start',  self._refresh_auto_start),
                              ('server',      self._refresh_server),
                              ('documents',   self._refresh_documents),
                              ('AI clients',  self._refresh_clients),
                              ('watcher',     self._refresh_watcher),
                              ('teaching',    self._refresh_teaching)):
            try:
                refresh()
            except Exception as ex:
                logger.debug('Error refreshing {}: {}'.format(name, ex))
        self._refresh_footer()

    def _refresh_server(self):
        if not HAS_SERVICE:
            self._server_state = {'error': 'Service unavailable: ' + _SVC_ERR_MSG}
            self._set_dot(self.status_indicator, 'T3.TextDisabled')
            if self.status_label:
                self.status_label.Text = 'Service unavailable: ' + _SVC_ERR_MSG
            if self._server_hint:
                self._server_hint.Text = 'The MCP service failed to load — restart Revit and try again.'
            if self.toggle_btn:
                self.toggle_btn.IsEnabled = False
            return

        status = MCPService.server_status() or {}
        self._server_state = status
        apply_server_status(
            status,
            self.status_indicator,
            self.status_label,
            self.toggle_btn,
            self.Resources,
        )

        running = bool(status.get('running'))
        if self._server_hint:
            tools = status.get('tools_count') or 0
            if self._server_error:
                hint = 'Could not change the server: {}'.format(self._server_error)
            elif running and tools:
                hint = '{} tools ready · keeps running after you close this window.'.format(tools)
            elif running:
                hint = 'Keeps running after you close this window.'
            elif self._auto_start:
                hint = 'Starts by itself when a model opens in Revit.'
            else:
                hint = 'Auto-start is off (Advanced) — start it here each session.'
            self._server_hint.Text = hint

        # A running server has its port fixed: show the real one and lock the
        # box, so the snippet and every Connect write the port that answers.
        if self.port_tb:
            if running:
                self.port_tb.Text = str(status.get('port', 48884))
            self.port_tb.IsEnabled = not running
        self._refresh_snippet()

    def _refresh_documents(self):
        self._active_doc_ok = False
        if not self._doc_label:
            return
        if not HAS_SERVICE:
            self._set_dot(self._doc_indicator, 'T3.TextDisabled')
            self._doc_label.Text = 'Service unavailable'
            return

        docs, err = MCPService.list_open_documents()
        if err:
            self._set_dot(self._doc_indicator, 'T3.Danger.Accent')
            self._doc_label.Text = 'Error: {}'.format(err)
            return

        active_title = next((d['title'] for d in (docs or []) if d.get('is_active')), None)
        if active_title:
            self._active_doc_ok = True
            self._set_dot(self._doc_indicator, 'T3.Success.Accent')
            self._doc_label.Text = 'Active model: {}'.format(active_title)
        elif docs:
            self._set_dot(self._doc_indicator, 'T3.Warning.Accent')
            self._doc_label.Text = '{} model(s) open — none active'.format(len(docs))
        else:
            self._set_dot(self._doc_indicator, 'T3.TextDisabled')
            self._doc_label.Text = 'No model open — open one in Revit first'

    def _refresh_clients(self):
        """Refresh every AI app row (Claude Desktop, ChatGPT/Codex, Antigravity)."""
        if not HAS_SERVICE:
            for widgets in self._client_widgets.values():
                self._set_dot(widgets.get('indicator'), 'T3.TextDisabled')
                if widgets.get('label'):
                    widgets['label'].Text = 'Service unavailable'
                if widgets.get('button'):
                    widgets['button'].IsEnabled = False
            return

        for key, widgets in self._client_widgets.items():
            try:
                status = MCPService.client_status(key)
            except Exception as ex:
                status = {'error': str(ex)}
            self._client_states[key] = status

            path = status.get('path') or ''
            if status.get('error'):
                color   = 'T3.Danger.Accent'
                text    = 'Error: {}'.format(status['error'])
                btn_txt = 'Connect'
                tip     = path
            elif status.get('configured'):
                color   = 'T3.Success.Accent'
                text    = 'Connected'
                btn_txt = 'Update'
                tip     = 'T3Lab entry found in {}'.format(path)
            elif not status.get('file_exists'):
                color   = 'T3.TextMuted'
                text    = 'Not connected · no config file yet'
                btn_txt = 'Connect'
                tip     = '{} does not exist yet — Connect creates it.'.format(path)
            else:
                color   = 'T3.Warning.Accent'
                text    = 'Not connected'
                btn_txt = 'Connect'
                tip     = 'No T3Lab entry in {}'.format(path)

            self._set_dot(widgets.get('indicator'), color)
            if widgets.get('label'):
                widgets['label'].Text = text
                widgets['label'].ToolTip = tip or None
            btn = widgets.get('button')
            if btn:
                btn.Content = btn_txt
                btn.IsEnabled = True
                btn.ToolTip = ('Rewrite the T3Lab entry — use after changing the port'
                               if btn_txt == 'Update' else widgets.get('tooltip'))

    def _refresh_watcher(self):
        if not HAS_SERVICE:
            apply_watcher_status(None, self._watcher_indicator,
                                 self._watcher_label, self._watcher_btn, self.Resources)
            return
        status = MCPService.watcher_status()
        apply_watcher_status(
            status,
            self._watcher_indicator,
            self._watcher_label,
            self._watcher_btn,
            self.Resources,
        )
        if self._dir_label:
            data_dir = status.get('data_dir') if status else None
            self._dir_label.Text = data_dir or MCPService.data_dir()

    def _refresh_footer(self):
        """Overall readiness: the first step that is not done yet, in words."""
        state     = self._server_state or {}
        total     = len(self._client_states) or len(CLIENT_KEYS)
        connected = sum(1 for s in self._client_states.values() if s.get('configured'))
        error     = state.get('error') or (None if state.get('running') else self._server_error)

        if not HAS_SERVICE:
            tone, text, tip = ('T3.Danger.Accent', 'Service unavailable',
                               'The MCP service failed to load: {}'.format(_SVC_ERR_MSG))
        elif error:
            tone, text, tip = 'T3.Danger.Accent', 'Server error', error
        elif not state.get('running'):
            tone, text, tip = ('T3.TextMuted', 'Server stopped',
                               'Step 1 — start the server.')
        elif connected == 0:
            tone, text, tip = ('T3.Warning.Accent', 'No AI app connected',
                               'Step 2 — connect the AI app you use.')
        elif not self._active_doc_ok:
            tone, text, tip = ('T3.Warning.Accent', 'No active model',
                               'Step 3 — open a model in Revit.')
        else:
            tone = 'T3.Success.Accent'
            text = 'Ready · port {}'.format(state.get('port', DEFAULT_PORT))
            tip  = '{} of {} AI apps connected. Restart an app if it was open while you connected it.'.format(
                connected, total)

        self._set_dot(self._footer_dot, tone)
        if self._footer_status:
            self._footer_status.Text = text
            self._footer_status.ToolTip = tip
        if self._next_btn:
            self._next_btn.Content = 'Start Server' if self._can_start_server() else 'Done'

    # ── Server ─────────────────────────────────────────────────────────────────

    def _toggle_server(self):
        if not HAS_SERVICE:
            return
        new_state, err = MCPService.toggle_server(current_port=self._current_port())
        self._server_error = err
        if err:
            logger.error('MCP server toggle error: {}'.format(err))
        elif new_state == 'running':
            ok, ee_err = MCPService.ensure_external_event()
            if not ok:
                logger.error('MCP ExternalEvent init error: {}'.format(ee_err))
        # Documents come from the server, so refresh every step, not just 1.
        self._refresh_all()

    def _on_toggle(self, sender, e):
        self._toggle_server()

    def _on_next(self, sender, e):
        if self._can_start_server():
            self._toggle_server()
        else:
            self.Close()

    def _on_refresh(self, sender, e):
        # A Start/Stop failure stays visible until the user asks for a fresh
        # look — from then on the live state speaks for itself.
        self._server_error = None
        self._refresh_all()

    def _refresh_auto_start(self):
        if not HAS_SERVICE:
            if self._auto_start_toggle is not None:
                self._auto_start_toggle.IsEnabled = False
            return
        self._auto_start = MCPService.auto_start_enabled()
        if self._auto_start_toggle is not None:
            self._auto_start_toggle.IsChecked = self._auto_start

    def _on_auto_start_toggle(self, sender, e):
        if not HAS_SERVICE or self._auto_start_toggle is None:
            return
        want = bool(self._auto_start_toggle.IsChecked)
        _state, err = MCPService.set_auto_start(want)
        if err:
            logger.error('Auto-start setting error: {}'.format(err))
        self._refresh_auto_start()
        self._refresh_server()

    # ── AI apps ────────────────────────────────────────────────────────────────

    def _make_configure_handler(self, key):
        """Per-client Click handler — `key` is captured for the closure."""
        def _handler(sender, e):
            self._configure_one(key)
        return _handler

    def _configure_one(self, key):
        if not HAS_SERVICE:
            return
        label = self._client_labels.get(key, key)
        ok, msg = MCPService.configure_client(key, port=self._current_port())
        if ok:
            logger.info('{} configured: {}'.format(key, msg))
        else:
            logger.error('{} configure error: {}'.format(key, msg))
        if self._configure_all_label:
            self._configure_all_label.Text = (
                '{0} connected — restart {0} so it loads T3Lab.'.format(label) if ok
                else '{} failed: {}'.format(label, msg))
        self._refresh_clients()
        self._refresh_footer()

    def _on_configure_all(self, sender, e):
        if not HAS_SERVICE:
            return
        results = MCPService.configure_all_clients(port=self._current_port())
        done   = [r['label'] for r in results if r['ok']]
        failed = [r for r in results if not r['ok']]
        for r in failed:
            logger.error('{} configure error: {}'.format(r['label'], r['message']))
        if self._configure_all_label:
            if not failed:
                self._configure_all_label.Text = (
                    'Connected {} apps — restart them so they load T3Lab.'.format(len(done)))
            else:
                self._configure_all_label.Text = (
                    'Connected {} of {} — failed: {}'.format(
                        len(done), len(results),
                        ', '.join('{} ({})'.format(r['label'], r['message']) for r in failed)))
        self._refresh_clients()
        self._refresh_footer()

    # ── Manual config snippet ──────────────────────────────────────────────────

    def _snippet_fmt(self):
        """'toml' when the Codex row is picked in the format combo, else 'json'."""
        try:
            if self._snippet_format_cb is not None:
                return 'toml' if self._snippet_format_cb.SelectedIndex == 1 else 'json'
        except Exception:
            pass
        return 'json'

    def _refresh_snippet(self):
        if not (HAS_SERVICE and self.config_box):
            return
        try:
            self.config_box.Text = MCPService.config_snippet(
                port=self._current_port(), fmt=self._snippet_fmt())
        except Exception as ex:
            logger.error('Snippet error: {}'.format(ex))

    def _on_snippet_format_changed(self, sender, e):
        self._refresh_snippet()

    def _on_port_changed(self, sender, e):
        if self._port_hint:
            text = self.port_tb.Text.strip() if self.port_tb else ''
            self._port_hint.Text = (
                self._port_hint_default if self._current_port() is not None
                else '"{}" is not a valid port — enter a number from 1024 to 65535.'.format(text))
        self._refresh_snippet()

    def _on_copy(self, sender, e):
        try:
            from System.Windows import Clipboard
            text = self.config_box.Text if self.config_box else ''
            Clipboard.SetText(text)
            msg = 'Copied — paste it into the app\'s MCP config file, then restart the app.'
        except Exception as ex:
            logger.error('Clipboard error: {}'.format(ex))
            msg = 'Copy failed: {} — select the text and press Ctrl+C instead.'.format(ex)
        if self._copy_status:
            self._copy_status.Text = msg

    # ── File watcher ───────────────────────────────────────────────────────────

    def _on_watcher_toggle(self, sender, e):
        if not HAS_SERVICE:
            return
        new_state, err = MCPService.toggle_watcher()
        if err:
            logger.error('File watcher toggle error: {}'.format(err))
        else:
            logger.info('File watcher: {}'.format(new_state))
        self._refresh_watcher()

    def _on_open_dir(self, sender, e):
        if not HAS_SERVICE:
            return
        ok, err = MCPService.open_data_dir()
        if not ok:
            logger.error('Could not open data dir: {}'.format(err))

    # ── Teaching capture ────────────────────────────────────────────────────────

    def _refresh_teaching(self):
        if not HAS_SERVICE or self._teaching_label is None:
            return
        try:
            status = MCPService.teaching_status()
        except Exception as ex:
            status = {'enabled': False, 'error': str(ex)}
        enabled = bool(status.get('enabled'))
        if self._teaching_toggle is not None:
            self._teaching_toggle.IsChecked = enabled
        self._set_dot(self._teaching_indicator,
                      'T3.Success.Accent' if enabled else 'T3.TextDisabled')
        recorded = status.get('sessions_recorded', 0)
        if status.get('error'):
            self._teaching_label.Text = 'Teaching capture unavailable'
        elif enabled:
            self._teaching_label.Text = (
                'Recording MCP sessions — {} captured'.format(recorded))
        else:
            self._teaching_label.Text = 'Teaching capture off'
        if self._sandbox_label is not None:
            sb = status.get('sandbox')
            self._sandbox_label.Text = sb or 'None — mark a scratch model'

    def _on_teaching_toggle(self, sender, e):
        if not HAS_SERVICE:
            return
        want = bool(self._teaching_toggle.IsChecked) \
            if self._teaching_toggle is not None else False
        _new, err = MCPService.set_teaching_mode(want)
        if err:
            logger.error('Teaching mode error: {}'.format(err))
        self._refresh_teaching()

    def _on_mark_sandbox(self, sender, e):
        if not HAS_SERVICE:
            return
        info, err = MCPService.mark_active_document_as_sandbox()
        if err:
            logger.error('Mark sandbox error: {}'.format(err))
        else:
            logger.info('Sandbox document set: {}'.format(
                info.get('title') if info else ''))
        self._refresh_teaching()


def show_mcp_control_dialog():
    """Show the MCP Control dialog."""
    MCPControlWindow().ShowDialog()
