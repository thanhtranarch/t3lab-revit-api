# -*- coding: utf-8 -*-
"""
MCP Control Dialog

Thin WPF wrapper around MCPService. All backend logic lives in
Services/mcp_service.py and can be reused by any other tool.
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
    CLIENT_KEYS = [key for key, _label, _fmt in MCPService.clients()]
except Exception:
    CLIENT_KEYS = ['claude', 'chatgpt', 'antigravity']

logger = script.get_logger()


def _brush(hex_color):
    """Safely convert a hex color string to a WPF SolidColorBrush."""
    if not hex_color:
        return None
    try:
        from System.Windows.Media import BrushConverter
        return BrushConverter().ConvertFromString(hex_color)
    except Exception:
        pass
    try:
        from System.Windows.Media import SolidColorBrush, Color
        hex_c = hex_color.lstrip('#')
        if len(hex_c) == 6:
            r = int(hex_c[0:2], 16)
            g = int(hex_c[2:4], 16)
            b = int(hex_c[4:6], 16)
            return SolidColorBrush(Color.FromRgb(r, g, b))
        elif len(hex_c) == 8:
            a = int(hex_c[0:2], 16)
            r = int(hex_c[2:4], 16)
            g = int(hex_c[4:6], 16)
            b = int(hex_c[6:8], 16)
            return SolidColorBrush(Color.FromArgb(a, r, g, b))
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
    """
    if not status:
        status = {}
    if status.get('error'):
        color, text = '#D23B3B', 'Error: {}'.format(status['error'])
        btn_content, btn_style_key = 'Start Server', 'T3.Button.Primary'
        enabled = True
    elif status.get('running') and status.get('foreign'):
        # Live and serving, but owned by another pyRevit engine in this Revit
        # session — its Python object is not callable from here, so Stop would
        # only throw. Say who owns it and what actually releases it.
        color       = '#157038'
        text        = ('Connected — port {} (started by another pyRevit engine; '
                       'restart Revit to stop it)').format(status.get('port', 48884))
        btn_content = 'Stop Server'
        btn_style_key = 'T3.Button.Danger'
        enabled     = False
    elif status.get('running'):
        color       = '#157038'
        text        = 'Connected — port {}'.format(status.get('port', 48884))
        btn_content = 'Stop Server'
        btn_style_key = 'T3.Button.Danger'
        enabled     = True
    else:
        color, text = '#71717A', 'Disconnected'
        btn_content, btn_style_key = 'Start Server', 'T3.Button.Primary'
        enabled = True

    if indicator:
        b = _brush(color)
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
            b = _brush('#9A9AA2')
            if b is not None:
                indicator.Background = b
        if label:
            label.Text = err
        if btn:
            btn.IsEnabled = False
        return

    if status.get('running'):
        color = '#157038'
        text  = 'File watcher active — monitoring task.json'
        btn_content, btn_style_key = 'Stop Watcher', 'T3.Button.Danger'
    else:
        color = '#71717A'
        text  = 'File watcher stopped'
        btn_content, btn_style_key = 'Start Watcher', 'T3.Button.Secondary'

    if indicator:
        b = _brush(color)
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

        # MCP server controls (safely bound via FindName)
        self.toggle_btn       = self.FindName('toggle_btn')
        self.copy_btn         = self.FindName('copy_btn')
        self.port_tb          = self.FindName('port_tb')
        self.status_indicator = self.FindName('status_indicator')
        self.status_label     = self.FindName('status_label')
        self.config_box       = self.FindName('config_box')

        if self.toggle_btn:
            self.toggle_btn.Click += self._on_toggle
        if self.copy_btn:
            self.copy_btn.Click += self._on_copy
        if self.port_tb:
            self.port_tb.TextChanged += self._on_port_changed

        # Active document widgets (read-only status)
        self._doc_indicator = self.FindName('active_doc_indicator')
        self._doc_label     = self.FindName('active_doc_label')

        # File watcher widgets
        self._watcher_indicator = self.FindName('watcher_indicator')
        self._watcher_label     = self.FindName('watcher_label')
        self._watcher_btn       = self.FindName('watcher_toggle_btn')
        self._dir_label         = self.FindName('data_dir_label')
        open_dir_btn            = self.FindName('open_dir_btn')

        if self._watcher_btn:
            self._watcher_btn.Click += self._on_watcher_toggle
        if open_dir_btn:
            open_dir_btn.Click += self._on_open_dir

        # AI client auto-configure widgets — one row per client key, plus the
        # "Configure All" button. Widget names follow <key>_cfg_* so a client
        # added to MCPService.AI_CLIENTS only needs its three XAML rows.
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
                'path':      self.FindName('{}_cfg_path'.format(key)),
                'button':    btn,
            }
            if btn:
                handler = self._make_configure_handler(key)
                self._client_handlers.append(handler)
                btn.Click += handler

        # Legacy aliases — kept so existing callers/tests keep working.
        _claude = self._client_widgets.get('claude', {})
        self._claude_cfg_indicator = _claude.get('indicator')
        self._claude_cfg_label     = _claude.get('label')
        self._claude_cfg_path      = _claude.get('path')

        self._configure_all_label = self.FindName('configure_all_label')
        configure_all_btn         = self.FindName('configure_all_btn')
        if configure_all_btn:
            configure_all_btn.Click += self._on_configure_all

        # Snippet format picker (JSON for Claude/Antigravity, TOML for Codex)
        self._snippet_format_cb = self.FindName('snippet_format_cb')
        if self._snippet_format_cb:
            self._snippet_format_cb.SelectionChanged += self._on_snippet_format_changed

        # Teaching capture widgets
        self._teaching_toggle    = self.FindName('teaching_toggle')
        self._teaching_indicator = self.FindName('teaching_indicator')
        self._teaching_label     = self.FindName('teaching_status_label')
        self._sandbox_label      = self.FindName('sandbox_label')
        mark_sandbox_btn         = self.FindName('mark_sandbox_btn')

        if self._teaching_toggle:
            self._teaching_toggle.Click += self._on_teaching_toggle
        if mark_sandbox_btn:
            mark_sandbox_btn.Click += self._on_mark_sandbox

        self._init_port()
        self._refresh_all()

    # ── Window chrome ──────────────────────────────────────────────────────────

    def minimize_button_clicked(self, sender, e):
        if WindowState is not None:
            self.WindowState = WindowState.Minimized

    def close_button_clicked(self, sender, e):
        self.Close()

    # ── Init helpers ───────────────────────────────────────────────────────────

    def _init_port(self):
        port = 48884
        if HAS_SERVICE:
            try:
                port = MCPService.server_status().get('port', 48884)
            except Exception:
                pass
        if self.port_tb:
            self.port_tb.Text = str(port)

    # ── Refresh ────────────────────────────────────────────────────────────────

    def _refresh_all(self):
        try:
            self._refresh_server()
        except Exception as ex:
            logger.debug("Error refreshing server status: {}".format(ex))
        try:
            self._refresh_documents()
        except Exception as ex:
            logger.debug("Error refreshing documents: {}".format(ex))
        try:
            self._refresh_watcher()
        except Exception as ex:
            logger.debug("Error refreshing watcher: {}".format(ex))
        try:
            self._refresh_clients()
        except Exception as ex:
            logger.debug("Error refreshing AI client configs: {}".format(ex))
        try:
            self._refresh_teaching()
        except Exception as ex:
            logger.debug("Error refreshing teaching: {}".format(ex))

    def _refresh_server(self):
        if not HAS_SERVICE:
            if self.status_indicator:
                b = _brush('#94A3B8')
                if b: self.status_indicator.Background = b
            if self.status_label:
                self.status_label.Text = 'Service unavailable: ' + _SVC_ERR_MSG
            if self.toggle_btn:
                self.toggle_btn.IsEnabled = False
            return
        status = MCPService.server_status()
        apply_server_status(
            status,
            self.status_indicator,
            self.status_label,
            self.toggle_btn,
            self.Resources,
        )
        self._refresh_snippet()

    def _refresh_documents(self):
        if not self._doc_label:
            return
        if not HAS_SERVICE:
            if self._doc_indicator:
                b = _brush('#94A3B8')
                if b: self._doc_indicator.Background = b
            self._doc_label.Text = 'Service unavailable'
            return

        docs, err = MCPService.list_open_documents()
        if err:
            if self._doc_indicator:
                b = _brush('#EF4444')
                if b: self._doc_indicator.Background = b
            self._doc_label.Text = 'Error: {}'.format(err)
            return

        active_title = next((d['title'] for d in (docs or []) if d.get('is_active')), None)
        if active_title:
            if self._doc_indicator:
                b = _brush('#10B981')
                if b: self._doc_indicator.Background = b
            self._doc_label.Text = 'Active: {}'.format(active_title)
        elif docs:
            if self._doc_indicator:
                b = _brush('#F59E0B')
                if b: self._doc_indicator.Background = b
            self._doc_label.Text = '{} open — none active (click a tab in Revit)'.format(len(docs))
        else:
            if self._doc_indicator:
                b = _brush('#94A3B8')
                if b: self._doc_indicator.Background = b
            self._doc_label.Text = 'No document open'

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

    # ── Event handlers ─────────────────────────────────────────────────────────

    def _on_toggle(self, sender, e):
        if not HAS_SERVICE:
            return
        try:
            port = int(self.port_tb.Text.strip()) if self.port_tb else None
        except Exception:
            port = None
        new_state, err = MCPService.toggle_server(current_port=port)
        if err:
            logger.error('MCP server toggle error: {}'.format(err))
        else:
            logger.info('MCP server: {}'.format(new_state))
            if new_state == 'running':
                ok, ee_err = MCPService.ensure_external_event()
                if not ok:
                    logger.error('MCP ExternalEvent init error: {}'.format(ee_err))
        self._refresh_server()

    def _on_watcher_toggle(self, sender, e):
        if not HAS_SERVICE:
            return
        new_state, err = MCPService.toggle_watcher()
        if err:
            logger.error('File watcher toggle error: {}'.format(err))
        else:
            logger.info('File watcher: {}'.format(new_state))
        self._refresh_watcher()

    def _on_copy(self, sender, e):
        try:
            from System.Windows import Clipboard
            text = self.config_box.Text if self.config_box else ''
            Clipboard.SetText(text)
            logger.info('Configuration copied to clipboard.')
        except Exception as ex:
            logger.error('Clipboard error: {}'.format(ex))

    def _on_open_dir(self, sender, e):
        ok, err = MCPService.open_data_dir()
        if not ok:
            logger.error('Could not open data dir: {}'.format(err))

    def _refresh_clients(self):
        """Refresh every AI client row (Claude Desktop, ChatGPT/Codex, Antigravity)."""
        if not HAS_SERVICE:
            for widgets in self._client_widgets.values():
                if widgets.get('indicator'):
                    b = _brush('#94A3B8')
                    if b: widgets['indicator'].Background = b
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

            if status.get('error'):
                color = '#EF4444'
                text  = 'Error: {}'.format(status['error'])
            elif not status.get('file_exists'):
                color = '#F59E0B'
                text  = 'Config not found — will be created on Configure'
            elif status.get('configured'):
                color = '#10B981'
                text  = 'Configured — t3lab-revit entry present'
            else:
                color = '#EF4444'
                text  = 'Not configured — click Configure to add entry'

            if widgets.get('indicator'):
                b = _brush(color)
                if b: widgets['indicator'].Background = b
            if widgets.get('label'):
                widgets['label'].Text = text
            if widgets.get('path'):
                widgets['path'].Text = status.get('path', '')

    def _current_port(self):
        """Port typed in the box, or None to let the service decide."""
        try:
            return int(self.port_tb.Text.strip()) if self.port_tb else None
        except Exception:
            return None

    def _make_configure_handler(self, key):
        """Per-client Click handler — `key` is captured for the closure."""
        def _handler(sender, e):
            self._configure_one(key)
        return _handler

    def _configure_one(self, key):
        if not HAS_SERVICE:
            return
        ok, msg = MCPService.configure_client(key, port=self._current_port())
        if ok:
            logger.info('{} configured: {}'.format(key, msg))
        else:
            logger.error('{} configure error: {}'.format(key, msg))
        if self._configure_all_label:
            self._configure_all_label.Text = (
                'Wrote {} — restart the client to pick it up.'.format(key) if ok
                else '{} failed: {}'.format(key, msg))
        self._refresh_clients()

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
                    'Configured {} clients — restart them to pick it up.'.format(len(done)))
            else:
                self._configure_all_label.Text = (
                    'Configured {} of {} — failed: {}'.format(
                        len(done), len(results),
                        ', '.join('{} ({})'.format(r['label'], r['message']) for r in failed)))
        self._refresh_clients()

    # ── Snippet ────────────────────────────────────────────────────────────────

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
        port_text = self.port_tb.Text if self.port_tb else None
        try:
            self.config_box.Text = MCPService.config_snippet(
                port=port_text or None, fmt=self._snippet_fmt())
        except Exception as ex:
            logger.error('Snippet error: {}'.format(ex))

    def _on_snippet_format_changed(self, sender, e):
        self._refresh_snippet()

    def _on_port_changed(self, sender, e):
        self._refresh_snippet()

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
        if self._teaching_indicator is not None:
            b = _brush('#10B981' if enabled else '#94A3B8')
            if b: self._teaching_indicator.Background = b
        recorded = status.get('sessions_recorded', 0)
        if status.get('error'):
            self._teaching_label.Text = 'Teaching unavailable'
        elif enabled:
            self._teaching_label.Text = (
                'Recording MCP sessions - {} captured'.format(recorded))
        else:
            self._teaching_label.Text = 'Teaching capture off'
        if self._sandbox_label is not None:
            sb = status.get('sandbox')
            self._sandbox_label.Text = (
                'Sandbox: {}'.format(sb) if sb
                else 'Sandbox: none - mark a scratch model')

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
