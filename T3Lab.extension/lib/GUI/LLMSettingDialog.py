# -*- coding: utf-8 -*-
"""
LLMs Setting Dialog — the T3Lab Assistant settings hub

Tabbed settings window: General (profile, action mode, data), Models
(provider/model/API key/connection), Projects (workspaces), Knowledge
(RAG index) and Skills. All state lives in the shared LLMRouter /
T3LabAISettings / UserProfile / ProjectStore singletons, so anything
changed here is immediately visible to the T3Lab Assistant (and any
other AI-powered tool) too.
"""

from __future__ import unicode_literals

import os

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

import System
from System import Action
from System.Threading import Thread, ThreadStart, ApartmentState
from System.Windows import Visibility, WindowState
from System.Windows.Media import SolidColorBrush, Color

from pyrevit import forms, script
from GUI.WPF_Base import T3WPFWindow

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'LLMSetting.xaml')

logger = script.get_logger()

try:
    from Intelligence.knowledge.knowledge_store import (get_active_store,
                                                        default_knowledge_dir)
    HAS_KNOWLEDGE = True
except Exception:
    HAS_KNOWLEDGE = False

    def get_active_store():
        return None

    def default_knowledge_dir():
        return ''


# Shared with the chat window — one implementation, so the .NET 8 shell
# fallback can never be present in one surface and missing in the other.
from GUI.AssistantShared import (open_in_explorer as _open_in_explorer,
                                 PROVIDER_COLORS as _SHARED_PROVIDER_COLORS)


def _show_dialog_owned(window, dlg):
    """Show a WinForms common dialog (folder/file picker) owned by `window`.

    Without an explicit owner, FolderBrowserDialog/OpenFileDialog parent
    themselves on whatever Win32 reports as the active window. Inside Revit
    that resolves to Revit's main window, NOT this modal WPF dialog — so the
    picker opens BEHIND the settings window while ShowDialog() keeps pumping
    messages on the UI thread. The result looks exactly like a dead button:
    nothing appears and the window stops responding.

    NativeWindow is the ready-made IWin32Window wrapper around an HWND, which
    avoids having to implement the interface from IronPython.
    """
    clr.AddReference('System.Windows.Forms')
    from System.Windows.Forms import NativeWindow
    from System.Windows.Interop import WindowInteropHelper
    try:
        hwnd = WindowInteropHelper(window).Handle
    except Exception:
        hwnd = System.IntPtr.Zero
    if hwnd == System.IntPtr.Zero:
        return dlg.ShowDialog()
    owner = NativeWindow()
    owner.AssignHandle(hwnd)
    try:
        return dlg.ShowDialog(owner)
    finally:
        try:
            owner.ReleaseHandle()
        except Exception:
            pass


def _brush(r, g, b):
    return SolidColorBrush(Color.FromRgb(r, g, b))


_READY  = _brush(16, 185, 129)
_GRAY   = _brush(230, 230, 234)
_MUTED  = _brush(161, 161, 170)
_GREEN  = _brush(16, 185, 129)
_RED    = _brush(239, 68, 68)

# Segoe MDL2 Assets "Cancel" (U+E711) — the remove/forget glyph on project
# rows. Named because it renders BLANK in a terminal, which has already led
# to it being mistaken for an empty string and "fixed" away.
_GLYPH_CANCEL = u""


class LLMSettingWindow(T3WPFWindow):
    """Standalone dialog for LLM provider / model / API key / connection setup."""

    _BRAND_COLORS = _SHARED_PROVIDER_COLORS
    _PROV_INDEX = {"claude": 0, "openai": 1, "deepseek": 2, "ollama": 3, "lmstudio": 4}

    _KEY_PROVIDERS = ("claude", "openai", "deepseek")
    _KEY_NAME_MAP  = {"claude": "Claude", "openai": "OpenAI", "deepseek": "DeepSeek"}
    _KEY_LABELS = {
        "claude":   u"Anthropic API Key (sk-ant-...)",
        "openai":   u"OpenAI API Key (sk-...)",
        "deepseek": u"DeepSeek API Key (sk-...)",
        "ollama":   u"No key needed (local)",
        "lmstudio": u"No key needed — start LM Studio first",
    }
    _API_KEY_URLS = {
        "claude":   "https://console.anthropic.com/settings/keys",
        "openai":   "https://platform.openai.com/api-keys",
        "deepseek": "https://platform.deepseek.com/api_keys",
    }
    _HOST_DEFAULTS = {
        "ollama":   "http://localhost:11434",
        "lmstudio": "http://localhost:1234",
    }
    _HOST_LABELS = {
        "ollama":   u"Ollama base URL",
        "lmstudio": u"LM Studio base URL",
    }
    # settings.json key each local provider's server URL is stored under.
    _HOST_KEY_MAP = {
        "ollama":   "Ollama_Host",
        "lmstudio": "LMStudio_Host",
    }

    def __init__(self):
        self._ui_ready     = False   # guards tab_changed during XAML load
        self._ai_mode_guard = False  # guards ai_mode_toggled re-entry
        self._action_guard  = False  # guards action_mode_toggled re-entry
        self._think_guard   = False  # guards extended_thinking_toggled re-entry
        self._quality_guard = False  # guards quality_mode_toggled re-entry
        self._verbosity_guard = False  # guards the two chat-detail toggles
        self._embed_guard   = False  # guards knowledge_embed_toggled re-entry
        self._prov_guard    = False  # guards provider_changed re-entry
        self._kn_scan_busy = False
        self._ctx_busy = False   # guards concurrent context-digest rebuilds

        # Tabs are filled on first visit, not at construction: the projects,
        # knowledge and skills loaders all walk disk (and, for a project with a
        # linked network share, block on SMB) — doing that for five tabs before
        # the window is even shown is what made this dialog slow to open.
        self._tabs_loaded = set()
        # Per-project CONTEXT block cache + a generation counter so a slow
        # worker cannot repaint a project the user has already switched away
        # from.
        self._ctx_cache = {}
        self._ctx_gen = 0

        # Dirty-tracking for the two editable text fields. A background probe
        # calls _apply_provider_chrome, which used to overwrite whatever the
        # user was typing — paste a key, wait ~5s, watch it turn back into the
        # old masked value. Focus alone is not enough (the user can click the
        # MODEL combo mid-edit), so the edit flag is the real guard.
        # An int counter, not a bool, so nested programmatic writes nest.
        self._suppress_text_dirty = 0
        self._key_dirty  = False
        self._host_dirty = False
        self._chrome_provider = None   # provider the fields currently show

        T3WPFWindow.__init__(self, _XAML)
        self._models_cache = {}
        self._probing = False
        self._probe_pending = None   # at most one queued switch (last wins)

        try:
            self.api_key_box.TextChanged += self._key_box_changed
            self.host_box.TextChanged += self._host_box_changed
        except Exception as ex:
            logger.debug("dirty-tracking wiring failed: {}".format(ex))

        self._update_instant()
        # General is cheap (settings reads) and Models is the tab that opens
        # first, so those two are eager; the rest load on first click.
        self._load_general_tab()
        self._tabs_loaded.update(('general', 'provider'))
        self._ui_ready = True
        self._probe_all_async()

    # ─── Threading / text-field helpers ─────────────────────────────────────

    def _ui_invoke(self, fn):
        """Marshal fn to the UI thread, tolerating a shut-down dispatcher.

        Closing this window while "Checking connection…" or Test Connection is
        in flight used to throw unhandled on a .NET background thread.
        """
        try:
            self.Dispatcher.Invoke(Action(fn))
        except Exception as ex:
            logger.debug("_ui_invoke skipped: {}".format(ex))

    def _start_worker(self, fn):
        """Start an STA background thread. Returns False if it could not start,
        so callers can un-latch whatever busy flag they already raised."""
        try:
            t = Thread(ThreadStart(fn))
            t.IsBackground = True
            t.SetApartmentState(ApartmentState.STA)
            t.Start()
            return True
        except Exception as ex:
            logger.debug("_start_worker failed: {}".format(ex))
            return False

    def _key_box_changed(self, sender, e):
        if not self._suppress_text_dirty:
            self._key_dirty = True

    def _host_box_changed(self, sender, e):
        if not self._suppress_text_dirty:
            self._host_dirty = True

    def _set_text_quiet(self, box, text):
        """Write to a text box without marking it user-edited."""
        self._suppress_text_dirty += 1
        try:
            box.Text = text
        finally:
            self._suppress_text_dirty -= 1

    @staticmethod
    def _is_editing(box, dirty):
        """True when repainting `box` would destroy user input."""
        if dirty:
            return True
        try:
            return bool(box.IsKeyboardFocusWithin)
        except Exception:
            return False

    # ─── Tabs ───────────────────────────────────────────────────────────────

    def tab_changed(self, sender, e):
        """Swap the visible settings panel when a tab pill is checked."""
        if not getattr(self, '_ui_ready', False):
            return
        try:
            tag = sender.Tag
            panels = {
                'general':   self.panel_general,
                'provider':  self.panel_provider,
                'projects':  self.panel_projects,
                'knowledge': self.panel_knowledge,
                'skills':    self.panel_skills,
            }
            for key in panels:
                panels[key].Visibility = (
                    Visibility.Visible if key == tag else Visibility.Collapsed)
            self._ensure_tab_loaded(tag)
        except Exception as ex:
            logger.debug("tab_changed error: {}".format(ex))

    def _ensure_tab_loaded(self, tag):
        """Fill a tab the first time it is shown (see __init__ for why)."""
        if tag in self._tabs_loaded:
            return
        self._tabs_loaded.add(tag)
        loader = {
            'projects':  self._load_projects_tab,
            'knowledge': self._load_knowledge_tab,
            'skills':    self._load_skills_tab,
        }.get(tag)
        if loader is None:
            return
        try:
            loader()
        except Exception as ex:
            # Never leave a tab permanently marked as loaded after a failure.
            self._tabs_loaded.discard(tag)
            logger.debug("load tab {} error: {}".format(tag, ex))

    # ─── Chrome ─────────────────────────────────────────────────────────────

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def close_button_clicked(self, sender, e):
        self.Close()

    # ─── Provider / model ───────────────────────────────────────────────────

    def provider_changed(self, sender, e):
        if self._prov_guard:
            return          # programmatic repaint, not a user choice
        try:
            item = self.provider_combo.SelectedItem
            if item is None:
                return
            tag = item.Tag
            if not tag:
                return
            self._populate_model_combo(tag)
            self._apply_host_panel(tag)
            self._switch_provider(tag)
        except Exception as ex:
            logger.debug("provider_changed error: {}".format(ex))

    def _switch_provider(self, name):
        try:
            from Intelligence.llm_router import LLMRouter
            router = LLMRouter()
            if not router.switch_provider(name):
                # Provider failed to load — the router is still on the previous
                # one, so repaint the combo to match reality. Otherwise the
                # dialog shows "DeepSeek" while every later action (Save Key,
                # Test) keys off router.get_active_name() and would write the
                # DeepSeek key into the Claude slot.
                self._apply_provider_chrome(router.get_active_name(),
                                            force_fields=True)
                self.model_saved_hint.Foreground = _RED
                self.model_saved_hint.Text = u"That provider failed to load"
                return
            self._apply_provider_chrome(name, force_fields=True)
            self._start_probe(name)
        except Exception as ex:
            logger.debug("_switch_provider error: {}".format(ex))

    def _start_probe(self, name):
        """Probe one provider off-thread, queueing if another probe is running.

        The old code simply returned when self._probing was set, which the
        startup probe holds for several seconds — so switching provider right
        after opening the window silently never loaded the new provider's
        models and the MODEL combo stayed disabled with a valid saved key.
        _probe_pending holds at most one name (last wins), so the chain always
        terminates.
        """
        if self._probing:
            self._probe_pending = name
            return
        self._probing = True

        def _bg():
            try:
                from Intelligence.llm_router import LLMRouter
                router = LLMRouter()
                router.probe_provider(name)
                # get_provider(name), NOT get_active_provider(): if the user
                # switched again mid-probe, the latter would store the NEW
                # provider's models under the OLD provider's cache key.
                provider = router.get_provider(name)
                if provider:
                    try:
                        self._models_cache[name] = provider.get_models()
                    except Exception:
                        pass
                self._ui_invoke(self._update_probed)
            except Exception as ex:
                logger.debug("_start_probe error: {}".format(ex))
            finally:
                def _next():
                    self._probing = False
                    pending, self._probe_pending = self._probe_pending, None
                    if pending:
                        self._start_probe(pending)
                self._ui_invoke(_next)

        if not self._start_worker(_bg):
            self._probing = False

    def model_changed(self, sender, e):
        try:
            from Intelligence.llm_router import LLMRouter
            item = self.model_combo.SelectedItem
            if item is None:
                return
            router = LLMRouter()
            router.set_model(router.get_active_name(), item.ToString())
        except Exception as ex:
            logger.debug("model_changed error: {}".format(ex))

    def save_model_clicked(self, sender, e):
        try:
            from Intelligence.llm_router import LLMRouter
            item = self.model_combo.SelectedItem
            if item is None:
                self.model_saved_hint.Text = u"Select a model first"
                self._flash_saved_hint()
                return
            router = LLMRouter()
            router.set_model(router.get_active_name(), item.ToString())
            self.model_saved_hint.Foreground = _GREEN
            self.model_saved_hint.Text = u"✓ Saved"
            self._flash_saved_hint()
        except Exception as ex:
            logger.debug("save_model_clicked error: {}".format(ex))

    def _flash_saved_hint(self):
        try:
            from System.Windows.Threading import DispatcherTimer
            from System import TimeSpan
            timer = DispatcherTimer()
            timer.Interval = TimeSpan.FromSeconds(2.0)

            def _clear(s, ev):
                try:
                    self.model_saved_hint.Text = u""
                finally:
                    timer.Stop()

            timer.Tick += _clear
            timer.Start()
        except Exception:
            pass

    def _populate_model_combo(self, active):
        """Fill MODEL combo for `active`. Empty+disabled until a live model list exists."""
        try:
            try:
                self.model_combo.SelectionChanged -= self.model_changed
            except Exception:
                pass

            self.model_combo.Items.Clear()
            models  = list(self._models_cache.get(active, []))
            enabled = bool(models)
            hint    = u""

            if enabled:
                saved = None
                try:
                    from config.settings import T3LabAISettings
                    saved = T3LabAISettings().get_provider_model(active)
                except Exception:
                    pass
                for m in models:
                    self.model_combo.Items.Add(m)
                if saved and saved in models:
                    self.model_combo.SelectedItem = saved
                else:
                    self.model_combo.SelectedIndex = 0
            else:
                if active in self._KEY_PROVIDERS:
                    hint = (u"Enter an API key first" if not self._has_saved_key(active)
                            else u"Not connected — press Save to test")
                else:
                    hint = u"Start the server & press Test Connection"

            self.model_combo.IsEnabled = enabled
            try:
                self.save_model_btn.IsEnabled = enabled
            except Exception:
                pass
            self.model_saved_hint.Text = hint
            self.model_saved_hint.Foreground = _MUTED
        except Exception as ex:
            logger.debug("_populate_model_combo error: {}".format(ex))
        finally:
            try:
                self.model_combo.SelectionChanged += self.model_changed
            except Exception:
                pass

    def _has_saved_key(self, provider):
        try:
            from config.settings import T3LabAISettings
            return bool(T3LabAISettings().get_api_key(self._KEY_NAME_MAP.get(provider, "")))
        except Exception:
            return False

    # ─── API key ────────────────────────────────────────────────────────────

    def get_api_key_clicked(self, sender, e):
        try:
            from Intelligence.llm_router import LLMRouter
            name = LLMRouter().get_active_name()
            url = self._API_KEY_URLS.get(name)
            if not url:
                return
            # Same .NET 8 trap as _open_in_explorer: a bare Process.Start(url)
            # throws under Revit 2025+ because UseShellExecute defaults to
            # False there, leaving this button dead. Ask the shell explicitly.
            import System.Diagnostics as _diag
            psi = _diag.ProcessStartInfo(url)
            psi.UseShellExecute = True
            _diag.Process.Start(psi)
        except Exception as ex:
            logger.debug("get_api_key_clicked error: {}".format(ex))

    def save_key_clicked(self, sender, e):
        """Save API key → verify connection → fetch models → enable MODEL combo."""
        try:
            from config.settings import T3LabAISettings
            from Intelligence.llm_router import LLMRouter

            key = (self.api_key_box.Text or u"").strip()
            router = LLMRouter()
            name = router.get_active_name()
            settings_key = self._KEY_NAME_MAP.get(name)
            if not settings_key:
                return

            if not key:
                self.model_saved_hint.Foreground = _RED
                self.model_saved_hint.Text = u"Enter an API key first"
                self._flash_saved_hint()
                return

            # The box shows a MASK of the stored key until the user types.
            # This used to `return` silently on the mask, so pressing Save Key
            # without retyping looked like a dead button. Re-validating the
            # stored key is the useful thing to do — and re-saving the mask
            # would have destroyed the real key.
            if not self._key_dirty and key == getattr(self, '_key_mask_shown', None):
                self.model_saved_hint.Foreground = _MUTED
                self.model_saved_hint.Text = u"Using the saved key — checking connection…"
            else:
                T3LabAISettings().set_api_key(settings_key, key)
                self._key_dirty = False
                self.model_saved_hint.Foreground = _MUTED
                self.model_saved_hint.Text = u"Checking connection…"

            provider = router.get_active_provider()
            if provider and hasattr(provider, "reload_credentials"):
                provider.reload_credentials()
            elif provider and hasattr(provider, "invalidate_models_cache"):
                provider.invalidate_models_cache()
            self._models_cache.pop(name, None)

            self.model_combo.IsEnabled = False
            self.save_model_btn.IsEnabled = False
            self.save_key_btn.IsEnabled = False

            def _validate():
                ok = False
                models = []
                try:
                    if provider and provider.check_health():
                        models = provider.get_models() or []
                        ok = len(models) > 0
                except Exception:
                    ok = False

                def _apply():
                    try:
                        self.save_key_btn.IsEnabled = True
                    except Exception:
                        pass
                    if ok:
                        self._models_cache[name] = models
                        self._populate_model_combo(name)
                        self.model_saved_hint.Foreground = _GREEN
                        self.model_saved_hint.Text = u"✓ Connected ({} models)".format(len(models))
                    else:
                        self._models_cache.pop(name, None)
                        self._populate_model_combo(name)
                        self.model_saved_hint.Foreground = _RED
                        self.model_saved_hint.Text = u"✗ Invalid key or connection failed"
                    self._set_status_dot(name, ok)

                self._ui_invoke(_apply)

            if not self._start_worker(_validate):
                # Never leave the button latched off because a thread failed.
                self.save_key_btn.IsEnabled = True
                self.model_saved_hint.Foreground = _RED
                self.model_saved_hint.Text = u"Could not start the check — try again"
        except Exception as ex:
            logger.debug("save_key_clicked error: {}".format(ex))

    # ─── Local server host (Ollama / LM Studio) ────────────────────────────

    def save_host_clicked(self, sender, e):
        try:
            from config.settings import T3LabAISettings
            from Intelligence.llm_router import LLMRouter
            host = self.host_box.Text.strip()
            if not host:
                return

            router = LLMRouter()
            name = router.get_active_name()

            # Both local providers persist their URL the same way now. Ollama
            # used to take an in-memory-only path (provider.set_host), so the
            # value was lost on restart and the field always redisplayed
            # localhost — even immediately after a "successful" Save.
            key = self._HOST_KEY_MAP.get(name)
            if key:
                T3LabAISettings().set_api_key(key, host)
                provider = router.get_active_provider()
                if provider and hasattr(provider, "reload_credentials"):
                    provider.reload_credentials()

            self._host_dirty = False
            self._models_cache.pop(name, None)
            self._update_instant()
            self._flash_hint(self.model_saved_hint, u"✓ Server URL saved")

            def _probe():
                try:
                    router.get_status(use_cache=False)
                    provider = router.get_active_provider()
                    live_models = provider.get_models() if provider else []
                    if live_models:
                        self._models_cache[name] = live_models
                    self._ui_invoke(self._update_probed)
                except Exception as pex:
                    logger.debug("host probe error: {}".format(pex))

            self._start_worker(_probe)
        except Exception as ex:
            logger.debug("save_host_clicked error: {}".format(ex))

    def _apply_host_panel(self, name, force_fields=False):
        try:
            if name in self._HOST_DEFAULTS:
                self.host_panel.Visibility = Visibility.Visible
                self.host_label.Text = self._HOST_LABELS.get(name, u"Server URL")
                if not (force_fields or not self._is_editing(
                        self.host_box, self._host_dirty)):
                    return
                if force_fields:
                    self._host_dirty = False
                current = u""
                try:
                    # Read back BOTH local providers. Only LMStudio_Host was
                    # read before, so a saved Ollama URL was invisible and the
                    # field snapped back to localhost the moment you pressed
                    # Save (save_host_clicked calls _update_instant).
                    from config.settings import T3LabAISettings
                    key = self._HOST_KEY_MAP.get(name)
                    if key:
                        current = T3LabAISettings().get_api_key(key) or u""
                except Exception:
                    pass
                self._set_text_quiet(
                    self.host_box, current or self._HOST_DEFAULTS[name])
            else:
                self.host_panel.Visibility = Visibility.Collapsed
        except Exception as ex:
            logger.debug("_apply_host_panel error: {}".format(ex))

    # ─── Test connection ────────────────────────────────────────────────────

    def test_clicked(self, sender, e):
        self.test_result_border.Visibility = Visibility.Visible
        self.test_result_border.Background = _brush(249, 250, 251)
        self.test_result_border.BorderBrush = _brush(229, 231, 235)
        self.test_label.Text = u"Testing…"
        self.test_label.Foreground = _brush(107, 114, 128)
        self.test_result.Text = u""
        self.test_btn.IsEnabled = False
        self.status_text.Text = u"Testing connection…"

        def _do_test():
            ok, label, msg = False, u"Result", u""
            try:
                from Intelligence.llm_router import LLMRouter
                router = LLMRouter()
                name = router.get_active_name()
                provider = router.get_active_provider()

                if provider is None:
                    msg = u"Provider '{}' not loaded.".format(name)
                elif not provider.check_health():
                    if name == "ollama":
                        # Recommend the tool-capable sweet spot (qwen3:14b), not
                        # a tiny NLU-only model — the assistant's agentic path
                        # needs a model that reliably tool-calls.
                        rec = u"qwen3:14b"
                        try:
                            from Intelligence import local_llm as _ll
                            rec = _ll.recommended_tool_model()
                        except Exception:
                            pass
                        msg = (u"Ollama not available or no models installed.\n"
                               u"1. Make sure Ollama is running.\n"
                               u"2. Run: ollama pull {}".format(rec))
                    elif name == "lmstudio":
                        msg = (u"LM Studio not available or no model loaded.\n"
                               u"1. Open LM Studio.\n"
                               u"2. Load a model in LM Studio first.")
                    else:
                        msg = u"Provider not reachable.\nCheck API key or service status."
                else:
                    active_model = None
                    try:
                        active_model = provider.get_active_model()
                    except Exception:
                        pass
                    if not active_model:
                        msg = u"No model selected. Choose a model from the Model dropdown."
                    else:
                        resp = provider.chat(
                            [],
                            u"You are a concise assistant. Do not think. Reply in one short sentence only.",
                            u"Reply with exactly this sentence: 'Connected OK'",
                            max_tokens=120,
                        )
                        if resp and resp.strip():
                            ok, label, msg = True, u"Connected", resp.strip()[:120]
                        else:
                            msg = u"Provider responded but returned an empty reply."
            except Exception as ex:
                msg = u"Error: {}".format(str(ex)[:100])

            _ok, _label, _msg = ok, label, msg

            def _update():
                if _ok:
                    self.test_result_border.Background = _brush(240, 253, 244)
                    self.test_result_border.BorderBrush = _brush(187, 247, 208)
                    self.test_label.Foreground = _brush(21, 128, 61)
                else:
                    self.test_result_border.Background = _brush(254, 242, 242)
                    self.test_result_border.BorderBrush = _brush(254, 202, 202)
                    self.test_label.Foreground = _brush(185, 28, 28)
                self.test_label.Text = _label
                self.test_result.Text = _msg
                self.test_btn.IsEnabled = True
                self.status_text.Text = u"Ready"

            self._ui_invoke(_update)

        if not self._start_worker(_do_test):
            self.test_btn.IsEnabled = True
            self.status_text.Text = u"Ready"
            self.test_label.Text = u"Could not start the test — try again"

    # ─── Status dots ────────────────────────────────────────────────────────

    def _set_status_dot(self, name, available, probed=True):
        mapping = {
            "claude":   (self.status_dot_claude,   self.status_text_claude),
            "openai":   (self.status_dot_openai,   self.status_text_openai),
            "deepseek": (self.status_dot_deepseek, self.status_text_deepseek),
            "ollama":   (self.status_dot_ollama,   self.status_text_ollama),
            "lmstudio": (self.status_dot_lmstudio, self.status_text_lmstudio),
        }
        pair = mapping.get(name)
        if not pair:
            return
        dot, txt = pair
        if available:
            dot.Fill = _READY
            txt.Text = u"Ready"
            txt.Foreground = _READY
        elif not probed:
            # Not health-checked yet — say so rather than asserting
            # "Not set up", which contradicted the live check moments later.
            dot.Fill = _GRAY
            txt.Text = u"Checking…"
            txt.Foreground = _MUTED
        else:
            dot.Fill = _GRAY
            txt.Text = u"Not set up"
            txt.Foreground = _MUTED

    # ─── Full render passes ─────────────────────────────────────────────────

    def _apply_provider_chrome(self, active, force_fields=False):
        """Provider combo selection + brand dot + API-key section, no HTTP.

        force_fields=True repaints the text fields even when the user has been
        typing — correct for a genuine provider CHANGE (a half-typed key for
        the previous provider is meaningless), wrong for a background probe.
        """
        # A re-entrancy guard, matching _action_guard / _think_guard / etc.
        # This used to detach and re-attach the XAML-declared handler with a
        # bare -= / +=: if the -= silently no-ops, every repaint stacks another
        # provider_changed subscription and one selection spawns N threads.
        self._prov_guard = True
        try:
            self.provider_combo.SelectedIndex = self._PROV_INDEX.get(active, 0)
        finally:
            self._prov_guard = False

        rgb = self._BRAND_COLORS.get(active, (161, 161, 170))
        self.provider_brand_dot.Fill = _brush(*rgb)

        self.key_label.Text = self._KEY_LABELS.get(active, u"API Key")
        needs_key = active in self._KEY_PROVIDERS
        self.api_key_box.IsEnabled = needs_key
        self.save_key_btn.IsEnabled = needs_key
        self.get_api_key_link.Visibility = (
            Visibility.Visible if needs_key else Visibility.Collapsed)

        # Always repaint when the provider itself changed, otherwise the fields
        # would describe a provider that is no longer selected.
        switched = (active != self._chrome_provider)
        may_write = force_fields or switched or not self._is_editing(
            self.api_key_box, self._key_dirty)

        if may_write:
            if switched or force_fields:
                self._key_dirty = False
            if needs_key:
                try:
                    from config.settings import T3LabAISettings
                    saved = T3LabAISettings().get_api_key(
                        self._KEY_NAME_MAP.get(active, "")) or ""
                    self._key_mask_shown = (
                        (saved[:8] + u"...") if len(saved) > 8 else saved)
                    self._set_text_quiet(self.api_key_box, self._key_mask_shown)
                except Exception:
                    pass
            else:
                self._key_mask_shown = u""
                self._set_text_quiet(self.api_key_box, u"")

        self._chrome_provider = active
        self._apply_host_panel(active, force_fields=force_fields or switched)

    def _update_instant(self):
        """Phase 1 — zero HTTP, cached/local data only."""
        try:
            from Intelligence.llm_router import LLMRouter
            router = LLMRouter()
            active = router.get_active_name()
            self._apply_provider_chrome(active)
            self._populate_model_combo(active)
        except Exception as ex:
            logger.debug("_update_instant error: {}".format(ex))

    def _update_probed(self):
        """Phase 2 — called on the UI thread after a background probe completes."""
        try:
            from Intelligence.llm_router import LLMRouter
            router = LLMRouter()
            status = router.get_status_instant()
            active = router.get_active_name()

            self._apply_provider_chrome(active)
            self._populate_model_combo(active)

            for name in ("claude", "openai", "deepseek", "ollama", "lmstudio"):
                info = status.get(name, {})
                self._set_status_dot(name, info.get("available", False),
                                     probed=info.get("probed", True))
        except Exception as ex:
            logger.debug("_update_probed error: {}".format(ex))

    def _probe_all_async(self):
        if self._probing:
            return
        self._probing = True

        def _bg():
            try:
                from Intelligence.llm_router import LLMRouter
                router = LLMRouter()
                active = router.get_active_name()

                router.probe_provider(active)
                provider = router.get_provider(active)
                if provider:
                    try:
                        self._models_cache[active] = provider.get_models()
                    except Exception:
                        pass
                self._ui_invoke(self._update_probed)

                router.get_status(use_cache=False)
                self._ui_invoke(self._update_probed)
            except Exception as ex:
                logger.debug("_probe_all_async error: {}".format(ex))
            finally:
                def _next():
                    self._probing = False
                    pending, self._probe_pending = self._probe_pending, None
                    if pending:
                        self._start_probe(pending)
                self._ui_invoke(_next)

        if not self._start_worker(_bg):
            self._probing = False

    # ─── Generic hint flash ─────────────────────────────────────────────────

    def _flash_hint(self, tb, text=u"✓ Saved", seconds=2.0):
        """Show a short confirmation next to a field, then clear it."""
        try:
            tb.Foreground = _GREEN
            tb.Text = text
            from System.Windows.Threading import DispatcherTimer
            from System import TimeSpan
            timer = DispatcherTimer()
            timer.Interval = TimeSpan.FromSeconds(seconds)

            def _clear(s, ev):
                try:
                    tb.Text = u""
                finally:
                    timer.Stop()

            timer.Tick += _clear
            timer.Start()
        except Exception:
            pass

    # ─── TAB: General ───────────────────────────────────────────────────────

    def _load_general_tab(self):
        """Fill display name + action-mode + reasoning toggles from settings."""
        try:
            from config.user_profile import UserProfile
            self.username_box.Text = UserProfile().get_name() or u""
        except Exception:
            pass
        try:
            from config.settings import get_settings
            self._ai_mode_guard = True
            self.ai_mode_toggle.IsChecked = bool(
                get_settings().is_ai_mode_enabled())
        except Exception:
            pass
        finally:
            self._ai_mode_guard = False
        try:
            from config.settings import get_settings
            self._action_guard = True
            self.action_mode_toggle.IsChecked = (
                get_settings().get_action_mode() == 'confirm')
        except Exception:
            pass
        finally:
            self._action_guard = False
        try:
            from config.settings import get_settings
            self._think_guard = True
            self.extended_thinking_toggle.IsChecked = bool(
                get_settings().get_agent_option('extended_thinking', False))
        except Exception:
            pass
        finally:
            self._think_guard = False
        try:
            from config.settings import get_settings
            self._quality_guard = True
            self.quality_mode_toggle.IsChecked = bool(
                get_settings().is_quality_mode_enabled())
        except Exception:
            pass
        finally:
            self._quality_guard = False
        try:
            from config.settings import get_settings
            self._verbosity_guard = True
            _s = get_settings()
            self.show_tool_calls_toggle.IsChecked = bool(
                _s.is_show_tool_calls_enabled())
            self.show_thinking_toggle.IsChecked = bool(
                _s.is_show_thinking_enabled())
        except Exception:
            pass
        finally:
            self._verbosity_guard = False

    def save_username_clicked(self, sender, e):
        name = (self.username_box.Text or u"").strip()
        if not name:
            return
        try:
            from config.user_profile import UserProfile
            UserProfile().set_name(name)
            self._flash_hint(self.username_saved_hint)
        except Exception as ex:
            logger.debug("save_username_clicked error: {}".format(ex))

    def ai_mode_toggled(self, sender, e):
        """Persist the global AI Mode state for tools."""
        if getattr(self, '_ai_mode_guard', False):
            return
        try:
            from config.settings import get_settings
            get_settings().set_ai_mode_enabled(bool(self.ai_mode_toggle.IsChecked))
        except Exception as ex:
            logger.debug("ai_mode_toggled error: {}".format(ex))

    def action_mode_toggled(self, sender, e):
        """Persist 'ask before model edits' (confirm) vs 'auto'."""
        if getattr(self, '_action_guard', False):
            return
        try:
            from config.settings import get_settings
            mode = 'confirm' if self.action_mode_toggle.IsChecked else 'auto'
            get_settings().set_agent_option('action_mode', mode)
        except Exception as ex:
            logger.debug("action_mode_toggled error: {}".format(ex))

    def extended_thinking_toggled(self, sender, e):
        """Persist the Claude extended-thinking (deep reasoning) switch."""
        if getattr(self, '_think_guard', False):
            return
        try:
            from config.settings import get_settings
            get_settings().set_agent_option(
                'extended_thinking',
                bool(self.extended_thinking_toggle.IsChecked))
        except Exception as ex:
            logger.debug("extended_thinking_toggled error: {}".format(ex))

    def quality_mode_toggled(self, sender, e):
        """Persist the Opus-parity (maximum quality) switch.

        When on, the Claude provider defaults to the most capable model,
        forces deep reasoning on agent turns, and widens the token ceiling.
        """
        if getattr(self, '_quality_guard', False):
            return
        try:
            from config.settings import get_settings
            get_settings().set_agent_option(
                'quality_mode',
                bool(self.quality_mode_toggle.IsChecked))
        except Exception as ex:
            logger.debug("quality_mode_toggled error: {}".format(ex))

    def show_tool_calls_toggled(self, sender, e):
        """Full tool cards vs one collapsed summary row per reply."""
        if getattr(self, '_verbosity_guard', False):
            return
        try:
            from config.settings import get_settings
            get_settings().set_show_tool_calls(
                bool(self.show_tool_calls_toggle.IsChecked))
        except Exception as ex:
            logger.debug("show_tool_calls_toggled error: {}".format(ex))

    def show_thinking_toggled(self, sender, e):
        """Interim step narration on/off (the final answer always shows)."""
        if getattr(self, '_verbosity_guard', False):
            return
        try:
            from config.settings import get_settings
            get_settings().set_show_thinking(
                bool(self.show_thinking_toggle.IsChecked))
        except Exception as ex:
            logger.debug("show_thinking_toggled error: {}".format(ex))

    def open_data_dir_clicked(self, sender, e):
        """Open %APPDATA%/T3LabAI in Explorer."""
        try:
            d = os.path.join(os.environ.get('APPDATA', ''), 'T3LabAI')
            _open_in_explorer(d)
        except Exception as ex:
            logger.debug("open_data_dir_clicked error: {}".format(ex))

    # ─── TAB: Projects ──────────────────────────────────────────────────────

    def _selected_project_id(self):
        try:
            item = self.project_combo.SelectedItem
            return getattr(item, 'Tag', None) if item is not None else None
        except Exception:
            return None

    def _load_projects_tab(self, select_pid=None):
        """Fill the project combo (edit scope only — activation happens in
        the chat composer's project chip, never here)."""
        # Callers outside _ensure_tab_loaded (the chat window preselects a
        # project with select_pid before showing the dialog) must also latch
        # this, or the first click on the Projects pill reloads the tab and
        # throws the preselection away.
        try:
            self._tabs_loaded.add('projects')
        except Exception:
            pass
        try:
            from System.Windows.Controls import ComboBoxItem
            from config.project_store import ProjectStore
            ps = ProjectStore()
            combo = self.project_combo
            try:
                combo.SelectionChanged -= self.project_changed
            except Exception:
                pass
            combo.Items.Clear()
            projects = ps.list_projects()
            want = select_pid or ps.get_active_project_id()
            sel = -1
            for i, meta in enumerate(projects):
                item = ComboBoxItem()
                item.Content = meta['name']
                item.Tag = meta['id']
                combo.Items.Add(item)
                if meta['id'] == want:
                    sel = i
            if projects:
                combo.SelectedIndex = sel if sel >= 0 else 0
            try:
                combo.SelectionChanged += self.project_changed
            except Exception:
                pass
            self._fill_project_edit(self._selected_project_id())
        except Exception as ex:
            logger.debug("_load_projects_tab error: {}".format(ex))

    def _fill_project_edit(self, pid):
        try:
            from config.project_store import ProjectStore
            meta = ProjectStore().get_project(pid) if pid else None
            if meta:
                self.project_edit_panel.Visibility = Visibility.Visible
                self.project_name_box.Text = meta.get('name', u'')
                self.project_instructions_box.Text = meta.get('instructions', u'')
                # Default AI override (applied on activation)
                try:
                    want = meta.get('provider') or ''
                    combo = self.project_provider_combo
                    idx = 0
                    for i in range(combo.Items.Count):
                        if str(combo.Items[i].Tag or '') == want:
                            idx = i
                            break
                    combo.SelectedIndex = idx
                    self.project_model_box.Text = meta.get('model') or u''
                except Exception:
                    pass
                # `created` has been stored since projects existed but was
                # never shown anywhere — put it on the name field's tooltip.
                try:
                    _c = meta.get('created') or u''
                    self.project_name_box.ToolTip = (
                        u"Created {}".format(_c.replace(u'T', u' '))
                        if _c else None)
                except Exception:
                    pass
                # Cheap, local-JSON renders stay inline; the CONTEXT block
                # (files walk + linked shares) refreshes off-thread.
                self._render_project_memory(pid)
                self._render_project_sched(pid)
                self._refresh_project_context(pid)
            else:
                self.project_edit_panel.Visibility = Visibility.Collapsed
        except Exception as ex:
            logger.debug("_fill_project_edit error: {}".format(ex))

    def project_changed(self, sender, e):
        self._fill_project_edit(self._selected_project_id())

    def new_project_clicked(self, sender, e):
        try:
            from config.project_store import ProjectStore
            ps = ProjectStore()
            n = len(ps.list_projects()) + 1
            meta = ps.create_project(u"Project {}".format(n))
            self._load_projects_tab(select_pid=meta['id'])
            try:
                self.project_name_box.Focus()
                self.project_name_box.SelectAll()
            except Exception:
                pass
        except Exception as ex:
            logger.debug("new_project_clicked error: {}".format(ex))

    def project_save_clicked(self, sender, e):
        try:
            pid = self._selected_project_id()
            if not pid:
                return
            prov = None
            try:
                item = self.project_provider_combo.SelectedItem
                if item is not None and item.Tag:
                    prov = str(item.Tag)
            except Exception:
                prov = None
            model = (self.project_model_box.Text or u'').strip() or None
            from config.project_store import ProjectStore
            ProjectStore().update_project(pid, {
                'name': self.project_name_box.Text.strip() or u"Project",
                'instructions': self.project_instructions_box.Text.strip(),
                'provider': prov,
                'model': model,
            })
            self._load_projects_tab(select_pid=pid)
            self._flash_hint(self.project_saved_hint)
        except Exception as ex:
            logger.debug("project_save_clicked error: {}".format(ex))

    def project_delete_clicked(self, sender, e):
        try:
            pid = self._selected_project_id()
            if not pid:
                return
            from config.project_store import ProjectStore
            ps = ProjectStore()
            meta = ps.get_project(pid) or {}
            from System.Windows import MessageBox, MessageBoxButton, MessageBoxResult
            res = MessageBox.Show(
                u"Delete project '{}' (including its index + chat history)?".format(
                    meta.get('name', pid)),
                u"LLMs Setting", MessageBoxButton.YesNo)
            if res != MessageBoxResult.Yes:
                return
            ps.delete_project(pid)
            self._ctx_cache.pop(pid, None)
            self._load_projects_tab()
        except Exception as ex:
            logger.debug("project_delete_clicked error: {}".format(ex))

    # ── Projects: context files ──────────────────────────────────────────────

    def _set_project_status(self, text):
        """Write one line to the CONTEXT status label. UI THREAD, never raises."""
        try:
            self.project_files_status.Text = text
        except Exception:
            pass

    def _project_files_dir(self, pid):
        """projects/<pid>/files — created on demand."""
        from config.project_store import ProjectStore
        d = os.path.join(ProjectStore().project_dir(pid), 'files')
        if not os.path.isdir(d):
            try:
                os.makedirs(d)
            except Exception:
                pass
        return d

    # ── Projects: CONTEXT block (counter line + linked-folder rows) ─────────
    #
    # Everything that touches disk for this block runs on a worker: the files/
    # walk, and — the expensive one — os.path.isdir + the digest sidecar read
    # of every linked folder. Those folders are routinely network shares, and a
    # share that is down makes isdir() block for the SMB timeout, which used to
    # freeze the whole dialog just for picking another project in the combo.

    def _refresh_project_context(self, pid, force=False, keep_status=False):
        """Repaint the CONTEXT counter + linked-folder rows for `pid`.

        Serves the per-project cache instantly when there is one (switching
        back and forth in the combo is then free) and refreshes from disk in
        the background. `force` drops the cache after an edit; `keep_status`
        leaves the status line alone when the caller has already written a
        more useful message there (e.g. the rescan summary).
        """
        if not pid:
            return
        self._ctx_gen += 1
        gen = self._ctx_gen
        if force:
            self._ctx_cache.pop(pid, None)

        cached = self._ctx_cache.get(pid)
        if cached is not None:
            # Instant repaint from the last read; the worker below still runs
            # and silently corrects it if anything changed on disk.
            self._paint_project_context(cached, keep_status=keep_status)
        else:
            # Placeholder from cached metadata only, so the panel is never
            # blank or stale while the worker reads the shares.
            try:
                from config.project_store import ProjectStore
                dirs = ProjectStore().get_knowledge_dirs(pid)
            except Exception:
                dirs = []
            self._paint_project_context(
                {'summary': u"Reading knowledge folder…",
                 'rows': [{'path': p, 'missing': False, 'stats': None}
                          for p in dirs],
                 'pid': pid},
                keep_status=keep_status)

        def _apply(_info):
            # Cache even when the user has moved on — the read was valid for
            # this pid, so coming back to it stays instant.
            prev = self._ctx_cache.get(pid)
            self._ctx_cache[pid] = _info
            if gen != self._ctx_gen:
                return    # another project is on screen now; don't repaint
            if _info == prev:
                return    # nothing changed — don't rebuild the rows for free
            self._paint_project_context(_info, keep_status=keep_status)

        def _work():
            _info = self._collect_project_context(pid)

            def _ui():
                _apply(_info)
            try:
                self.Dispatcher.BeginInvoke(Action(_ui))
            except Exception:
                pass

        if not self._start_worker(_work) and cached is None:
            # Better a brief freeze than a panel stuck on "Reading…".
            _apply(self._collect_project_context(pid))

    def _collect_project_context(self, pid):
        """Read the counter + one digest-stats dict per linked folder.

        BACKGROUND THREAD — must not touch any WPF element.
        """
        info = {'pid': pid, 'summary': u'', 'rows': []}
        ps = None
        dirs = []
        try:
            from config.project_store import ProjectStore
            ps = ProjectStore()
            info['summary'] = ps.describe_documents(pid, cap=99)
            dirs = ps.get_knowledge_dirs(pid)
        except Exception as ex:
            logger.debug("_collect_project_context error: {}".format(ex))
        for path in dirs:
            row = {'path': path, 'stats': None,
                   'missing': not os.path.isdir(path)}
            # linked_dir_stats is the same TTL-cached read describe_documents
            # just did, so the rows cost no extra share round trips.
            if ps is not None and not row['missing']:
                try:
                    row['stats'] = ps.linked_dir_stats(path)
                except Exception:
                    pass
            info['rows'].append(row)
        return info

    def project_add_files_clicked(self, sender, e):
        """Copy picked documents into the project's knowledge folder."""
        try:
            pid = self._selected_project_id()
            if not pid:
                self._set_project_status(
                    u"Select (or create) a project first, then add files.")
                return
            clr.AddReference('System.Windows.Forms')
            from System.Windows.Forms import OpenFileDialog, DialogResult
            dlg = OpenFileDialog()
            dlg.Multiselect = True
            dlg.Title = "Add documents to this project"
            dlg.Filter = ("Documents|*.pdf;*.docx;*.txt;*.md;*.csv;*.xlsx|"
                          "All files|*.*")
            if _show_dialog_owned(self, dlg) != DialogResult.OK:
                return
            import shutil
            dst = self._project_files_dir(pid)
            for f in dlg.FileNames:
                try:
                    shutil.copy2(f, os.path.join(dst, os.path.basename(f)))
                except Exception:
                    pass
            self._refresh_project_context(pid, force=True)
            # Editing the ACTIVE project → refresh its RAG index now;
            # other projects get indexed on activation.
            try:
                from config.project_store import ProjectStore
                if ProjectStore().get_active_project_id() == pid:
                    self._kick_knowledge_scan()
            except Exception:
                pass
        except Exception as ex:
            logger.debug("project_add_files_clicked error: {}".format(ex))

    def project_open_folder_clicked(self, sender, e):
        try:
            pid = self._selected_project_id()
            if not pid:
                try:
                    self.project_files_status.Text = (
                        u"Select a project first.")
                except Exception:
                    pass
                return
            folder = self._project_files_dir(pid)
            if not _open_in_explorer(folder):
                # never fail silently — that is what made this look dead
                try:
                    self.project_files_status.Text = (
                        u"Could not open: {}".format(folder))
                except Exception:
                    pass
        except Exception as ex:
            logger.debug("project_open_folder_clicked error: {}".format(ex))

    # ── Projects: linked external knowledge folders ─────────────────────────

    def _paint_project_context(self, info, keep_status=False):
        """Draw the CONTEXT block from an already-collected `info` dict.

        UI THREAD, but pure rendering — no disk access at all, which is what
        keeps combo switching instant.
        """
        try:
            from System.Windows.Controls import (Border, Button, Grid,
                                                 ColumnDefinition, TextBlock)
            from System.Windows import Thickness, CornerRadius, GridLength

            pid = info.get('pid')
            if not keep_status:
                try:
                    self.project_files_status.Text = info.get('summary') or u''
                except Exception:
                    pass

            panel = self.project_dirs_panel
            panel.Children.Clear()
            rows = info.get('rows') or []
            if not rows:
                return

            for _row in rows:
                path = _row.get('path') or u''
                row = Border()
                row.Background = SolidColorBrush(Color.FromRgb(255, 255, 255))
                row.BorderBrush = SolidColorBrush(Color.FromRgb(230, 230, 234))
                row.BorderThickness = Thickness(1)
                row.CornerRadius = CornerRadius(4)
                row.Padding = Thickness(10, 6, 8, 6)
                row.Margin = Thickness(0, 0, 0, 4)

                grid = Grid()
                c0 = ColumnDefinition()
                c0.Width = GridLength(1, System.Windows.GridUnitType.Star)
                c1 = ColumnDefinition()
                c1.Width = GridLength.Auto
                c2 = ColumnDefinition()
                c2.Width = GridLength.Auto
                grid.ColumnDefinitions.Add(c0)
                grid.ColumnDefinitions.Add(c1)
                grid.ColumnDefinitions.Add(c2)

                missing = bool(_row.get('missing'))
                # exact counts come from the folder's own context/ digest,
                # read on the worker in _collect_project_context
                st = _row.get('stats')
                pending = st is None
                if pending:
                    st = {'exists': False, 'files': 0, 'updated': '', 'llm': 0}

                if missing:
                    note = u"  (not found)"
                elif pending:
                    note = u"  · reading…"
                elif not st.get('exists'):
                    note = u"  · not scanned yet"
                else:
                    note = u"  · {} doc{}".format(
                        st.get('files') or 0,
                        u"" if (st.get('files') or 0) == 1 else u"s")
                    if st.get('pages'):
                        note += u" · {} pages".format(st['pages'])
                    if st.get('llm'):
                        note += u" (LLM {})".format(st['llm'])
                    if st.get('skipped'):
                        note += u" · {} unread".format(st['skipped'])
                    if st.get('updated'):
                        note += u" · {}".format(st['updated'][:16])

                tb = TextBlock()
                tb.Text = (u"🔗 " + (os.path.basename(path.rstrip(u'\\/'))
                                     or path) + note)
                tb.ToolTip = (path
                              + (u"\n\nDigest: " + st.get('path', u'')
                                 if st.get('exists') else u'')
                              + u"\n\nClick to open this folder")
                tb.FontSize = 11.5
                tb.FontFamily = System.Windows.Media.FontFamily("Segoe UI")
                tb.Foreground = SolidColorBrush(
                    Color.FromRgb(239, 68, 68) if missing
                    else Color.FromRgb(82, 82, 91))
                tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
                tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
                # the row itself opens the LINKED folder (not the project's
                # own files dir, which is what the button below opens)
                if not missing:
                    tb.Cursor = System.Windows.Input.Cursors.Hand

                    def _make_open(_p):
                        def _open(s, ev):
                            if not _open_in_explorer(_p):
                                try:
                                    self.project_files_status.Text = (
                                        u"Could not open: {}".format(_p))
                                except Exception:
                                    pass
                        return _open
                    tb.MouseLeftButtonUp += _make_open(path)
                Grid.SetColumn(tb, 0)
                grid.Children.Add(tb)

                rb = Button()
                rb.Content = u"↻"
                rb.FontSize = 11
                rb.Width = 20
                rb.Height = 20
                rb.Cursor = System.Windows.Input.Cursors.Hand
                rb.Background = SolidColorBrush(Color.FromArgb(0, 0, 0, 0))
                rb.BorderThickness = Thickness(0)
                rb.Foreground = SolidColorBrush(Color.FromRgb(59, 130, 246))
                rb.IsEnabled = not missing
                rb.ToolTip = u"Rescan this folder and rebuild its CONTEXT.md"

                def _make_rescan(_p, _pid):
                    def _rescan(s, ev):
                        self._build_context_digest([_p], _pid)
                    return _rescan
                rb.Click += _make_rescan(path, pid)
                Grid.SetColumn(rb, 1)
                grid.Children.Add(rb)

                btn = Button()
                btn.Content = u"✕"
                btn.FontSize = 10
                btn.Width = 20
                btn.Height = 20
                btn.Cursor = System.Windows.Input.Cursors.Hand
                btn.Background = SolidColorBrush(Color.FromArgb(0, 0, 0, 0))
                btn.BorderThickness = Thickness(0)
                btn.Foreground = SolidColorBrush(Color.FromRgb(161, 161, 170))
                btn.ToolTip = u"Unlink this folder (files are NOT deleted)"

                def _make_unlink(_p, _pid):
                    def _unlink(s, ev):
                        try:
                            from config.project_store import ProjectStore
                            ps = ProjectStore()
                            ps.remove_knowledge_dir(_pid, _p)
                            self._refresh_project_context(_pid, force=True)
                            if ps.get_active_project_id() == _pid:
                                self._kick_knowledge_scan()
                        except Exception as uex:
                            logger.debug("unlink dir error: {}".format(uex))
                    return _unlink
                btn.Click += _make_unlink(path, pid)
                Grid.SetColumn(btn, 2)
                grid.Children.Add(btn)

                row.Child = grid
                panel.Children.Add(row)
        except Exception as ex:
            logger.debug("_paint_project_context error: {}".format(ex))

    def project_link_folder_clicked(self, sender, e):
        """Link an external folder as project knowledge, write its CONTEXT.md
        digest, then re-index. UI THREAD (digest+scan run in background).

        Every early exit says why on the status line: this used to `return`
        silently on no-project and on a failed save, which is indistinguishable
        from a dead button.
        """
        try:
            pid = self._selected_project_id()
            if not pid:
                self._set_project_status(
                    u"Select (or create) a project first, then link a folder.")
                return
            clr.AddReference('System.Windows.Forms')
            from System.Windows.Forms import FolderBrowserDialog, DialogResult
            dlg = FolderBrowserDialog()
            dlg.Description = ("Chon thu muc BEP / tieu chuan / tai lieu de "
                               "lien ket vao project")
            if _show_dialog_owned(self, dlg) != DialogResult.OK \
                    or not dlg.SelectedPath:
                return
            from config.project_store import ProjectStore
            ps = ProjectStore()
            if ps.add_knowledge_dir(pid, dlg.SelectedPath) is None:
                self._set_project_status(
                    u"Could not save the link — the project file may be "
                    u"read-only or in use.")
                return
            self._refresh_project_context(pid, force=True)
            self._build_context_digest([dlg.SelectedPath], pid)
        except Exception as ex:
            logger.debug("project_link_folder_clicked error: {}".format(ex))
            self._set_project_status(
                u"Link folder failed: {}".format(ex))

    def _build_context_digest(self, folders, pid=None, project_ctx=True):
        """Rebuild <folder>/context/CONTEXT.md for each folder on a background
        thread, then re-index. Accepts one path or a list. Never blocks the UI.

        Re-runnable by design: the digest is regenerated from whatever the
        folder holds RIGHT NOW, so this doubles as the rescan/reload path
        after a BEP or standard is updated on the share.
        """
        if isinstance(folders, (list, tuple)):
            todo = [f for f in folders if f]
        else:
            todo = [folders] if folders else []
        if not todo:
            return
        if self._ctx_busy:
            # Used to return silently, so the per-row ↻ button (which is not
            # visibly disabled, unlike project_rescan_btn) looked broken.
            try:
                self.project_files_status.Text = (
                    u"A rescan is already running — please wait for it to finish.")
            except Exception:
                pass
            return
        self._ctx_busy = True
        try:
            self.project_rescan_btn.IsEnabled = False
        except Exception:
            pass

        def _set_status(text):
            def _ui(_t=text):
                try:
                    self.project_files_status.Text = _t
                except Exception:
                    pass
            try:
                self.Dispatcher.BeginInvoke(Action(_ui))
            except Exception:
                pass

        def _work():
            results = []
            ctx_info = []
            try:
                from Intelligence.knowledge import context_digest
                for i, folder in enumerate(todo, start=1):
                    if not os.path.isdir(folder):
                        continue
                    label = os.path.basename(folder.rstrip(u'\\/')) or folder
                    prefix = (u"[{}/{}] ".format(i, len(todo))
                              if len(todo) > 1 else u"")

                    def _prog(name, _p=prefix, _l=label):
                        _set_status(u"{}{}: {}".format(_p, _l[:18], name[:26]))
                    _set_status(u"{}Reading {}…".format(prefix, label[:24]))
                    try:
                        res = context_digest.build_context_file(
                            folder, progress_cb=_prog)
                    except Exception as bex:
                        logger.debug("digest {} error: {}".format(folder, bex))
                        res = None
                    if res:
                        results.append(res)
            except Exception as wex:
                logger.debug("context digest error: {}".format(wex))

            # ── project-wide BIM context (steps 3+4 of the RAG cycle) ──────
            # Per-folder digests summarise ONE file at a time; this pass asks
            # the indexed corpus a fixed set of BIM-standard questions and
            # answers each from the best passages across ALL linked folders,
            # with file+page citations.
            if pid and project_ctx:
                try:
                    _set_status(u"Indexing for project context…")
                    from config.project_store import ProjectStore as _PS
                    from Intelligence.knowledge import project_context as _pc
                    ps = _PS()
                    store = ps.knowledge_store_for(pid)
                    if store is not None:
                        store.scan()
                        embedder = None
                        try:
                            from Intelligence.knowledge.embeddings import (
                                get_default_embedder)
                            emb = get_default_embedder()
                            if emb is not None and emb.is_available():
                                store.embed_pending(emb, budget_sec=120)
                                embedder = emb
                        except Exception:
                            embedder = None
                        chat = None
                        try:
                            from Intelligence.knowledge.context_digest import (
                                _default_chat_fn)
                            chat = _default_chat_fn()
                        except Exception:
                            chat = None

                        def _tprog(topic):
                            _set_status(u"Context: {}".format(topic[:34]))
                        ctx = _pc.build_project_context(
                            store, chat, embedder=embedder,
                            progress_cb=_tprog)
                        out_dir = os.path.join(ps.project_dir(pid), 'files')
                        ctx_path = _pc.write_project_context(out_dir, ctx)
                        if ctx_path:
                            ctx_info.append(ctx)
                except Exception as pex:
                    logger.debug("project context error: {}".format(pex))

            def _done():
                self._ctx_busy = False
                try:
                    self.project_rescan_btn.IsEnabled = True
                except Exception:
                    pass
                try:
                    if results:
                        docs = sum(r.get('files') or 0 for r in results)
                        n_llm = sum(r.get('llm') or 0 for r in results)
                        n_pg = sum(r.get('pages') or 0 for r in results)
                        n_bad = sum(r.get('skipped') or 0 for r in results)
                        n_re = sum(r.get('reused') or 0 for r in results)
                        msg = (u"Context rebuilt: {} doc(s), {} page(s) read "
                               u"in {} folder(s){}"
                               .format(docs, n_pg, len(results),
                                       u" · LLM {}".format(n_llm) if n_llm
                                       else u" · excerpt only (no LLM)"))
                        if n_re:
                            msg += u" · {} unchanged (reused)".format(n_re)
                        if n_bad:
                            msg += u" · {} unreadable (see CONTEXT.md)".format(
                                n_bad)
                        if ctx_info:
                            c = ctx_info[0]
                            msg += u" · PROJECT_CONTEXT {}/{} topics".format(
                                c.get('topics_found') or 0,
                                c.get('topics_total') or 0)
                        self.project_files_status.Text = msg
                    elif todo:
                        self.project_files_status.Text = (
                            u"Nothing indexable found in the linked folder(s).")
                    if pid:
                        from config.project_store import ProjectStore
                        # the digests were just rewritten — drop the TTL cache
                        # so the rows show the new counts immediately
                        ProjectStore().invalidate_dir_stats()
                        if ProjectStore().get_active_project_id() == pid:
                            self._kick_knowledge_scan()
                        # keep_status: the summary written just above is more
                        # useful than the plain file counter.
                        self._refresh_project_context(pid, force=True,
                                                      keep_status=True)
                except Exception as dex:
                    logger.debug("digest done error: {}".format(dex))
            try:
                self.Dispatcher.BeginInvoke(Action(_done))
            except Exception:
                self._ctx_busy = False

        if not self._start_worker(_work):
            # Un-latch, or the rescan buttons stay dead for the window's life.
            self._ctx_busy = False
            try:
                self.project_rescan_btn.IsEnabled = True
                self.project_files_status.Text = (
                    u"Could not start the rescan — try again.")
            except Exception:
                pass

    def project_rescan_clicked(self, sender, e):
        """Re-read every linked folder of the selected project and rebuild
        their CONTEXT.md digests + the search index."""
        try:
            pid = self._selected_project_id()
            if not pid:
                return
            from config.project_store import ProjectStore
            dirs = ProjectStore().get_knowledge_dirs(pid)
            if not dirs:
                # nothing linked — still refresh the index of the project's
                # own files so the button is never a dead end
                self.project_files_status.Text = (
                    u"No linked folder — refreshing index…")
                if ProjectStore().get_active_project_id() == pid:
                    self._kick_knowledge_scan()
                else:
                    self._refresh_project_context(pid, force=True)
                return
            self._build_context_digest(dirs, pid)
        except Exception as ex:
            logger.debug("project_rescan_clicked error: {}".format(ex))

    # ── Projects: scheduled daily prompts ────────────────────────────────────

    def _render_project_memory(self, pid):
        """Remembered facts for this project, with a per-row forget button.

        The chat window's project panel used to be the ONLY place these could
        be managed; it is read-only now, so this is their home.
        """
        try:
            from System.Windows.Controls import (Grid, ColumnDefinition,
                                                 TextBlock)
            from System.Windows import Thickness, GridLength, TextWrapping
            from System.Windows.Input import Cursors
            from Intelligence import assistant_memory as _am

            panel = self.project_memory_panel
            panel.Children.Clear()
            facts = _am.list_facts(pid)
            proj = [(i + 1, f) for i, (s, f) in enumerate(facts)
                    if s == _am.PROJECT_SCOPE]
            n_global = len(facts) - len(proj)

            if not proj:
                t = TextBlock()
                t.Text = u"No facts remembered for this project yet."
                t.FontSize = 11
                t.Foreground = _MUTED
                t.Margin = Thickness(1, 0, 0, 4)
                panel.Children.Add(t)
            for number, f in proj:
                g = Grid()
                g.Margin = Thickness(1, 2, 0, 2)
                g.ColumnDefinitions.Add(ColumnDefinition())
                c1 = ColumnDefinition()
                c1.Width = GridLength.Auto
                g.ColumnDefinitions.Add(c1)

                lbl = TextBlock()
                lbl.Text = f.get('text') or u''
                lbl.FontSize = 12
                lbl.Foreground = _brush(82, 82, 91)
                lbl.TextWrapping = TextWrapping.Wrap
                g.Children.Add(lbl)

                x = TextBlock()
                x.Text = _GLYPH_CANCEL
                try:
                    x.Style = self.FindResource("T3.Icon.Muted")
                except Exception:
                    x.FontFamily = System.Windows.Media.FontFamily(
                        "Segoe MDL2 Assets")
                    x.FontSize = 11
                    x.Foreground = _MUTED
                x.Cursor = Cursors.Hand
                x.Margin = Thickness(10, 2, 2, 0)
                x.ToolTip = u"Forget this fact"

                def _forget(s, ev, _n=number, _pid=pid):
                    try:
                        from Intelligence import assistant_memory as _m
                        _m.remove_fact(_n, _pid)
                    except Exception as fex:
                        logger.debug("forget fact error: {}".format(fex))
                    self._render_project_memory(_pid)

                x.MouseLeftButtonUp += _forget
                Grid.SetColumn(x, 1)
                g.Children.Add(x)
                panel.Children.Add(g)

            if n_global:
                t = TextBlock()
                t.Text = (u"+ {} global fact(s) apply to every project."
                          .format(n_global))
                t.FontSize = 10.5
                t.Foreground = _MUTED
                t.Margin = Thickness(1, 6, 0, 0)
                panel.Children.Add(t)
        except Exception as ex:
            logger.debug("_render_project_memory error: {}".format(ex))

    def _render_project_sched(self, pid):
        """Rebuild the scheduled-prompt rows for the selected project."""
        try:
            from config.project_store import ProjectStore
            from System.Windows.Controls import (Grid, ColumnDefinition,
                                                 TextBlock)
            from System.Windows import Thickness, GridLength, TextWrapping
            from System.Windows.Input import Cursors

            panel = self.project_sched_panel
            panel.Children.Clear()
            items = (ProjectStore().get_project(pid) or {}) \
                .get('scheduled') or []
            if not items:
                t = TextBlock()
                t.Text = u"No scheduled prompts yet."
                t.FontSize = 11
                t.Foreground = _MUTED
                t.Margin = Thickness(1, 0, 0, 4)
                panel.Children.Add(t)
                return
            # Scheduled prompts only ever run for the ACTIVE project — the
            # tick lives in the chat window and reads only the active id. This
            # tab edits any project, so say so rather than let the user believe
            # an inactive project's schedule will fire.
            try:
                if ProjectStore().get_active_project_id() != pid:
                    note = TextBlock()
                    note.Text = (u"These run only while this project is the "
                                 u"active one, with the assistant open.")
                    note.FontSize = 10.5
                    note.Foreground = _brush(217, 119, 87)
                    note.TextWrapping = TextWrapping.Wrap
                    note.Margin = Thickness(1, 0, 0, 6)
                    panel.Children.Add(note)
            except Exception:
                pass

            from System.Windows.Controls import CheckBox
            for it in items:
                g = Grid()
                g.Margin = Thickness(1, 2, 0, 2)
                c_on = ColumnDefinition()
                c_on.Width = GridLength.Auto
                g.ColumnDefinitions.Add(c_on)
                g.ColumnDefinitions.Add(ColumnDefinition())
                c1 = ColumnDefinition()
                c1.Width = GridLength.Auto
                g.ColumnDefinitions.Add(c1)

                _on = bool(it.get('enabled', True))

                # `enabled` was read by the schedule tick but nothing could
                # ever write it — this toggle is what makes the field real.
                cb = CheckBox()
                cb.IsChecked = _on
                cb.VerticalAlignment = System.Windows.VerticalAlignment.Center
                cb.Margin = Thickness(0, 0, 8, 0)
                cb.ToolTip = u"Enable / pause this scheduled prompt"
                try:
                    cb.Style = self.FindResource("T3ToggleSwitch")
                    cb.LayoutTransform = System.Windows.Media.ScaleTransform(
                        0.6, 0.6)
                except Exception:
                    pass

                def _toggle(s, ev, _tid=it.get('id'), _pid=pid):
                    try:
                        from config.project_store import ProjectStore as _PS
                        _PS().set_schedule_enabled(_pid, _tid,
                                                   bool(s.IsChecked))
                    except Exception as tex:
                        logger.debug("sched toggle error: {}".format(tex))
                    self._render_project_sched(_pid)
                cb.Checked += _toggle
                cb.Unchecked += _toggle
                Grid.SetColumn(cb, 0)
                g.Children.Add(cb)

                _pv = u" ".join((it.get('prompt') or u'').split())
                if len(_pv) > 60:
                    _pv = _pv[:59] + u"\u2026"
                _last = it.get('last_run') or u''
                lbl = TextBlock()
                lbl.Text = u"{} \u2014 {}{}".format(
                    it.get('time') or u'?', _pv,
                    u"   (last run {})".format(_last) if _last else u"")
                lbl.FontSize = 12
                lbl.Foreground = _brush(82, 82, 91) if _on else _MUTED
                lbl.TextWrapping = TextWrapping.Wrap
                lbl.VerticalAlignment = System.Windows.VerticalAlignment.Center
                Grid.SetColumn(lbl, 1)
                g.Children.Add(lbl)

                x = TextBlock()
                x.Text = _GLYPH_CANCEL
                try:
                    x.Style = self.FindResource("T3.Icon.Muted")
                except Exception:
                    x.FontFamily = System.Windows.Media.FontFamily(
                        "Segoe MDL2 Assets")
                    x.FontSize = 11
                    x.Foreground = _MUTED
                x.Cursor = Cursors.Hand
                x.Margin = Thickness(10, 2, 2, 0)
                x.ToolTip = u"Remove"

                def _rm(s, ev, _tid=it.get('id'), _pid=pid):
                    try:
                        from config.project_store import ProjectStore as _PS
                        _PS().remove_schedule(_pid, _tid)
                    except Exception as rex:
                        logger.debug("sched remove error: {}".format(rex))
                    self._render_project_sched(_pid)

                x.MouseLeftButtonUp += _rm
                Grid.SetColumn(x, 2)
                g.Children.Add(x)
                panel.Children.Add(g)
        except Exception as ex:
            logger.debug("_render_project_sched error: {}".format(ex))

    def project_sched_add_clicked(self, sender, e):
        """Validate + append one scheduled daily prompt."""
        try:
            pid = self._selected_project_id()
            if not pid:
                return
            from config.project_store import ProjectStore
            ps = ProjectStore()
            prompt = (self.project_sched_prompt_box.Text or u'').strip()
            time_txt = (self.project_sched_time_box.Text or u'').strip()
            # Validation + record shape live in the store, so the chat window
            # and this tab can never drift apart on what a schedule looks like.
            hhmm = ps.validate_schedule_time(time_txt)
            if not prompt or not hhmm:
                (self.project_sched_time_box if prompt
                 else self.project_sched_prompt_box).BorderBrush = _RED
                self.project_files_status.Text = (
                    u"Enter a prompt and a time as HH:MM (00:00–23:59)."
                    if prompt else u"Enter the prompt to run.")
                return
            self.project_sched_prompt_box.BorderBrush = _brush(203, 213, 225)
            self.project_sched_time_box.BorderBrush = _brush(203, 213, 225)
            if ps.add_schedule(pid, prompt, hhmm) is None:
                self.project_files_status.Text = (
                    u"Could not save the scheduled prompt.")
                return
            self.project_sched_prompt_box.Text = u''
            self._render_project_sched(pid)
        except Exception as ex:
            logger.debug("project_sched_add_clicked error: {}".format(ex))

    # ─── TAB: Knowledge ─────────────────────────────────────────────────────

    def _load_knowledge_tab(self):
        """Refresh status label, dir rows and the embeddings toggle."""
        if not HAS_KNOWLEDGE:
            try:
                self.knowledge_index_status.Text = u"Knowledge module not available"
            except Exception:
                pass
            return
        try:
            store = get_active_store()
            if store is not None:
                st = store.stats()
                self.knowledge_index_status.Text = u"{} files · {} chunks".format(
                    st['files'], st['chunks'])
            self._refresh_knowledge_dirs_panel()
            try:
                from config.settings import get_settings
                want = bool(get_settings().get_knowledge_option(
                    'embeddings_enabled', True))
                self._embed_guard = True
                self.knowledge_embed_toggle.IsChecked = want
            except Exception:
                pass
            finally:
                self._embed_guard = False
        except Exception as ex:
            logger.debug("_load_knowledge_tab error: {}".format(ex))

    def _refresh_knowledge_dirs_panel(self):
        """Rebuild the knowledge directory rows. UI THREAD."""
        try:
            from System.Windows.Controls import (Border, TextBlock, Grid,
                                                 ColumnDefinition, Button)
            from System.Windows import Thickness, CornerRadius, GridLength

            panel = self.knowledge_dirs_panel
            panel.Children.Clear()

            rows = [(default_knowledge_dir(), False)]
            try:
                from config.settings import get_settings
                for d in get_settings().get_knowledge_dirs():
                    rows.append((d, True))
            except Exception:
                pass

            for path, removable in rows:
                row = Border()
                row.Background = SolidColorBrush(Color.FromRgb(255, 255, 255))
                row.BorderBrush = SolidColorBrush(Color.FromRgb(230, 230, 234))
                row.BorderThickness = Thickness(1)
                row.CornerRadius = CornerRadius(4)
                row.Padding = Thickness(10, 6, 8, 6)
                row.Margin = Thickness(0, 0, 0, 4)

                grid = Grid()
                col_txt = ColumnDefinition()
                col_txt.Width = GridLength(1, System.Windows.GridUnitType.Star)
                col_btn = ColumnDefinition()
                col_btn.Width = GridLength.Auto
                grid.ColumnDefinitions.Add(col_txt)
                grid.ColumnDefinitions.Add(col_btn)

                tb = TextBlock()
                tb.Text = os.path.basename(path.rstrip(u'\\/')) or path
                tb.ToolTip = path
                tb.FontSize = 11.5
                tb.FontFamily = System.Windows.Media.FontFamily("Segoe UI")
                tb.Foreground = SolidColorBrush(Color.FromRgb(82, 82, 91))
                tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
                tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
                Grid.SetColumn(tb, 0)
                grid.Children.Add(tb)

                if removable:
                    btn = Button()
                    btn.Content = u"✕"
                    btn.FontSize = 10
                    btn.Width = 20
                    btn.Height = 20
                    btn.Cursor = System.Windows.Input.Cursors.Hand
                    btn.Background = SolidColorBrush(Color.FromArgb(0, 0, 0, 0))
                    btn.BorderThickness = Thickness(0)
                    btn.Foreground = SolidColorBrush(Color.FromRgb(161, 161, 170))
                    btn.ToolTip = u"Remove folder from index"

                    def _make_remove(p):
                        def _remove(sender, e):
                            try:
                                from config.settings import get_settings
                                get_settings().remove_knowledge_dir(p)
                                self._refresh_knowledge_dirs_panel()
                                self._kick_knowledge_scan()
                            except Exception as rex:
                                logger.debug("remove dir error: {}".format(rex))
                        return _remove
                    btn.Click += _make_remove(path)
                    Grid.SetColumn(btn, 1)
                    grid.Children.Add(btn)

                row.Child = grid
                panel.Children.Add(row)
        except Exception as ex:
            logger.debug("_refresh_knowledge_dirs_panel error: {}".format(ex))

    def _kick_knowledge_scan(self):
        """(Re)scan the active knowledge store on a background thread."""
        if not HAS_KNOWLEDGE or self._kn_scan_busy:
            return
        self._kn_scan_busy = True
        try:
            self.knowledge_index_status.Text = u"Scanning..."
        except Exception:
            pass

        def _scan():
            try:
                store = get_active_store()
                if store is not None:
                    def _prog(name):
                        def _ui(_n=name):
                            try:
                                self.knowledge_index_status.Text = \
                                    u"Index: " + _n[:24]
                            except Exception:
                                pass
                        try:
                            self.Dispatcher.BeginInvoke(Action(_ui))
                        except Exception:
                            pass
                    store.scan(progress_cb=_prog)
                    try:
                        from Intelligence.knowledge.embeddings import (
                            get_default_embedder)
                        emb = get_default_embedder()
                        if emb is not None and emb.is_available():
                            store.embed_pending(emb, budget_sec=120)
                    except Exception:
                        pass
            except Exception as ex:
                logger.debug("knowledge scan error: {}".format(ex))
            finally:
                self._kn_scan_busy = False
                self._ui_invoke(self._load_knowledge_tab)
        if not self._start_worker(_scan):
            self._kn_scan_busy = False
            try:
                self.knowledge_index_status.Text = (
                    u"Could not start the scan — try again.")
            except Exception:
                pass

    def add_knowledge_dir_clicked(self, sender, e):
        """Pick a folder to add to the knowledge index. UI THREAD."""
        try:
            clr.AddReference('System.Windows.Forms')
            from System.Windows.Forms import FolderBrowserDialog, DialogResult
            dlg = FolderBrowserDialog()
            dlg.Description = "Chon thu muc tai lieu (PDF/TXT/MD) de index"
            if _show_dialog_owned(self, dlg) == DialogResult.OK \
                    and dlg.SelectedPath:
                from config.settings import get_settings
                get_settings().add_knowledge_dir(dlg.SelectedPath)
                self._refresh_knowledge_dirs_panel()
                self._kick_knowledge_scan()
        except Exception as ex:
            logger.debug("add_knowledge_dir_clicked error: {}".format(ex))
            try:
                self.knowledge_index_status.Text = (
                    u"Add folder failed: {}".format(ex))
            except Exception:
                pass

    def reindex_clicked(self, sender, e):
        # A raw WPF click handler must never let an exception escape.
        try:
            self._kick_knowledge_scan()
        except Exception as ex:
            logger.debug("reindex_clicked error: {}".format(ex))

    def knowledge_embed_toggled(self, sender, e):
        """Persist the semantic-search switch; pull the embed model when
        first enabled (background — ~270 MB)."""
        if getattr(self, '_embed_guard', False):
            return
        try:
            on = bool(self.knowledge_embed_toggle.IsChecked)
            from config.settings import get_settings
            get_settings().set_knowledge_option('embeddings_enabled', on)
            if not on:
                return

            def _ensure():
                try:
                    from Intelligence.knowledge.embeddings import (
                        get_default_embedder)
                    emb = get_default_embedder()
                    if emb is None:
                        return
                    if not emb.is_available():
                        if not emb.ensure_model():
                            def _fail():
                                try:
                                    self.knowledge_index_status.Text = (
                                        u"Embed model unavailable — check Ollama")
                                except Exception:
                                    pass
                            self._ui_invoke(_fail)
                            return
                    store = get_active_store()
                    if store is not None:
                        store.embed_pending(emb, budget_sec=300)
                    self._ui_invoke(self._load_knowledge_tab)
                except Exception as ex2:
                    logger.debug("embed enable error: {}".format(ex2))
            self._start_worker(_ensure)
        except Exception as ex:
            logger.debug("knowledge_embed_toggled error: {}".format(ex))

    # ─── TAB: Skills ────────────────────────────────────────────────────────

    def _load_skills_tab(self):
        """Rebuild the skills rows (name + enable toggle). UI THREAD."""
        try:
            from System.Windows.Controls import (Border, TextBlock, Grid,
                                                 ColumnDefinition, CheckBox)
            from System.Windows import Thickness, CornerRadius, GridLength
            from Intelligence.skills_engine import get_skills_engine

            panel = self.skills_list_panel
            panel.Children.Clear()
            skills = get_skills_engine().all_skills()
            if not skills:
                tb = TextBlock()
                tb.Text = u"No skills yet."
                tb.FontSize = 11.5
                tb.Foreground = SolidColorBrush(Color.FromRgb(161, 161, 170))
                tb.FontFamily = System.Windows.Media.FontFamily("Segoe UI")
                panel.Children.Add(tb)
                return

            try:
                from Intelligence.skill_installer import list_installed
                installed_ids = set(i.get('skill_id')
                                    for i in list_installed())
            except Exception:
                installed_ids = set()

            for meta in skills:
                row = Border()
                row.Background = SolidColorBrush(Color.FromRgb(255, 255, 255))
                row.BorderBrush = SolidColorBrush(Color.FromRgb(230, 230, 234))
                row.BorderThickness = Thickness(1)
                row.CornerRadius = CornerRadius(4)
                row.Padding = Thickness(10, 6, 10, 6)
                row.Margin = Thickness(0, 0, 0, 4)
                row.ToolTip = meta.get('description', '')

                grid = Grid()
                col_txt = ColumnDefinition()
                col_txt.Width = GridLength(1, System.Windows.GridUnitType.Star)
                col_tgl = ColumnDefinition()
                col_tgl.Width = GridLength.Auto
                grid.ColumnDefinitions.Add(col_txt)
                grid.ColumnDefinitions.Add(col_tgl)

                tb = TextBlock()
                label = meta.get('name', meta['id'])
                # Where it came from matters once repos are in play: a skill
                # pulled from GitHub is the one "Update" will overwrite.
                if meta['id'] in installed_ids:
                    label += u"   ·  GitHub"
                elif meta.get('source') == 'builtin':
                    label += u"   ·  built-in"
                tb.Text = label
                tb.FontSize = 11.5
                tb.FontFamily = System.Windows.Media.FontFamily("Segoe UI")
                tb.Foreground = SolidColorBrush(Color.FromRgb(82, 82, 91))
                tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
                tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
                Grid.SetColumn(tb, 0)
                grid.Children.Add(tb)

                cb = CheckBox()
                cb.IsChecked = bool(meta.get('enabled', True))
                cb.VerticalAlignment = System.Windows.VerticalAlignment.Center
                try:
                    cb.Style = self.FindResource("T3ToggleSwitch")
                    cb.LayoutTransform = System.Windows.Media.ScaleTransform(0.7, 0.7)
                except Exception:
                    pass

                def _make_toggle(sid, box):
                    def _toggled(sender, e):
                        try:
                            from Intelligence.skills_engine import get_skills_engine
                            get_skills_engine().set_enabled(
                                sid, bool(box.IsChecked))
                        except Exception as tex:
                            logger.debug("skill toggle error: {}".format(tex))
                    return _toggled
                handler = _make_toggle(meta['id'], cb)
                cb.Checked += handler
                cb.Unchecked += handler
                Grid.SetColumn(cb, 1)
                grid.Children.Add(cb)

                row.Child = grid
                panel.Children.Add(row)
        except Exception as ex:
            logger.debug("_load_skills_tab error: {}".format(ex))

    def refresh_skills_clicked(self, sender, e):
        """Rescan skill folders and rebuild the list."""
        try:
            from Intelligence.skills_engine import get_skills_engine
            get_skills_engine().scan()
            self._load_skills_tab()
        except Exception as ex:
            logger.debug("refresh_skills_clicked error: {}".format(ex))

    def open_skills_dir_clicked(self, sender, e):
        """Open the user skills folder in Explorer."""
        try:
            from Intelligence.skills_engine import _user_skills_dir
            _open_in_explorer(_user_skills_dir())
        except Exception as ex:
            logger.debug("open_skills_dir_clicked error: {}".format(ex))

    def install_skills_clicked(self, sender, e):
        """Ask for a GitHub repo link and install the Claude skills in it.

        Same installer the chat uses, so a skill added here behaves exactly
        like one added by asking the assistant for it.
        """
        try:
            from Intelligence import skill_installer as installer
            url = forms.ask_for_string(
                prompt="Paste a GitHub repo link (a /tree/<branch>/<folder> "
                       "link installs just that folder):",
                default="https://github.com/",
                title="Install skills from GitHub")
            if not url or not url.strip():
                return
            source = installer.parse_repo_url(url)
            if not source:
                forms.alert("That is not a GitHub repo link.\n\n"
                            "Expected something like "
                            "https://github.com/owner/repo",
                            title="Install skills")
                return
            report = installer.install_from_github(source)
            self.refresh_skills_clicked(None, None)
            forms.alert(_plain_report(report), title="Install skills")
        except Exception as ex:
            logger.debug("install_skills_clicked error: {}".format(ex))
            forms.alert("Install failed: {}".format(ex),
                        title="Install skills")

    def update_skills_clicked(self, sender, e):
        """Re-pull every skill that was installed from a repo."""
        try:
            from Intelligence import skill_installer as installer
            installed = installer.list_installed()
            if not installed:
                forms.alert("No skills were installed from GitHub yet.\n\n"
                            "Use 'Install from GitHub' first.",
                            title="Update skills")
                return
            report = installer.update_all()
            self.refresh_skills_clicked(None, None)
            forms.alert(_plain_report(report), title="Update skills")
        except Exception as ex:
            logger.debug("update_skills_clicked error: {}".format(ex))
            forms.alert("Update failed: {}".format(ex),
                        title="Update skills")


def _plain_report(report):
    """Installer report as plain text for a pyRevit alert (no markdown)."""
    report = report or {}
    lines = []
    for entry in (report.get('installed') or []):
        lines.append(u"installed  /{}".format(entry['id']))
    for entry in (report.get('updated') or []):
        lines.append(u"updated    /{}".format(entry['id']))
    for sid, why in (report.get('skipped') or [])[:6]:
        lines.append(u"skipped    {} ({})".format(sid, why))
    for err in (report.get('errors') or [])[:4]:
        lines.append(u"error      {}".format(err))
    if not lines:
        lines.append(u"Nothing changed.")
    return u"\n".join(lines)


def show_llm_setting_dialog():
    """Show the LLMs Setting dialog."""
    LLMSettingWindow().ShowDialog()
