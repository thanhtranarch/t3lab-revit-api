# -*- coding: utf-8 -*-
"""
Settings

Configuration settings manager for T3Lab AI integration.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""
from __future__ import unicode_literals

__author__  = "Tran Tien Thanh"
__title__   = "Settings"

import io
import os
import json


class T3LabAISettings(object):
    """Settings manager for T3LabAI"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(T3LabAISettings, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._settings_file = self._get_settings_path()
        self._load_ok       = True    # False → the file exists but we couldn't read it
        self._load_error    = None
        self._settings = self._load_settings()
        self._initialized = True

    def _get_settings_path(self):
        """Get the path to settings file"""
        app_data = os.environ.get('APPDATA', '')
        settings_dir = os.path.join(app_data, 'T3LabAI')
        if not os.path.exists(settings_dir):
            os.makedirs(settings_dir)
        return os.path.join(settings_dir, 'settings.json')

    def _load_settings(self):
        """Load settings from file, distinguishing the three failure modes.

        This used to `except Exception: pass` straight into the defaults, which
        made a truncated settings.json catastrophic: every setter reloads before
        saving, so ONE toggle in LLMs Setting rewrote the defaults over the file
        and silently destroyed every API key, model preference and knowledge dir.

        Now:
          * file absent (first run) → defaults, writes allowed
          * file parses            → parsed, writes allowed
          * file exists but is not valid JSON → quarantine it as
            settings.corrupt-<stamp>.json, then behave like "absent". The bytes
            survive, so keys can be recovered by hand, and the app stays usable.
          * file exists but cannot be read (locked by the other Revit session,
            permissions) → do NOT quarantine (the file is probably fine).
            Serve defaults for READS only and make save_settings() refuse, so a
            transient lock can never clobber good data. It self-heals on the
            next attempt because every setter reloads first.
        """
        self._load_ok    = True
        self._load_error = None

        if not os.path.exists(self._settings_file):
            return self._get_default_settings()

        try:
            with io.open(self._settings_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except ValueError as ex:
            # Malformed / truncated JSON. json.JSONDecodeError subclasses
            # ValueError on py3 and does not exist on py2 — ValueError is the
            # portable catch for both runtimes.
            self._quarantine_corrupt(ex)
            return self._get_default_settings()
        except Exception as ex:
            self._load_ok    = False
            self._load_error = u"{}".format(ex)
            return self._get_default_settings()

    def _quarantine_corrupt(self, reason):
        """Move an unparseable settings.json aside so it is never overwritten."""
        try:
            import datetime
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            dest = os.path.join(
                os.path.dirname(self._settings_file),
                'settings.corrupt-{}.json'.format(stamp))
            os.rename(self._settings_file, dest)
            self._load_error = u"settings.json was not valid JSON ({}); " \
                               u"kept a copy at {}".format(reason, dest)
        except Exception as ex:
            # Could not move it — refuse to write rather than destroy it.
            self._load_ok    = False
            self._load_error = u"settings.json is corrupt and could not be " \
                               u"quarantined: {}".format(ex)

    def is_healthy(self):
        """False when the settings file exists but could not be read."""
        return bool(self._load_ok)

    def get_load_error(self):
        """Human-readable reason the last load failed, or None."""
        return self._load_error

    def _update(self, mutator):
        """Reload from disk → apply `mutator(settings)` → save.

        The single write path for every setter. Reloading first is what stops
        two Revit sessions from overwriting each other: without it, a session
        holding a stale in-memory dict wipes whatever the other one saved.
        """
        self._settings = self._load_settings()
        mutator(self._settings)
        return self.save_settings()

    def _get_default_settings(self):
        """Get default settings"""
        return {
            'server': {
                'port': 8080,
                'host': 'localhost'
            },
            'providers': [],
            'api_keys': {},
            # Default to the local Ollama+Qwen runtime (privacy + zero API cost).
            # This is only the FIRST-RUN seed — a saved choice always wins on
            # restore, and the fallback chain still reaches Claude/OpenAI when
            # Ollama is unreachable, so the assistant works even with no model.
            'active_provider': 'ollama',
            'model_preferences': {},
            'username': 'Thạnh',
            'window_state': {
                'left':         None,
                'top':          None,
                'width':        560,
                'height':       720,
                'sidebar_open': False,
            },
            'active_project': None,
            'knowledge': {
                'dirs':               [],
                'embeddings_enabled': True,
                'embed_model':        'nomic-embed-text',
            },
            'agents': {
                'multi_agent':    True,
                'llm_classify':   True,
                'quality_mode':   False,
                'graph_mode':     True,
                'show_tool_calls': True,
                'show_thinking':   True,
                'self_study':      True,
                'parallel_tasks':  True,
                'opus_teacher':    False,
                'self_train_auto': False,
            },
            'skills': {
                'disabled': [],
            },
            'ai_mode': {
                'enabled': True,
                'fast_provider': 'auto',
                'reasoning_provider': 'auto',
                'tool_toggles': {},
            },
        }

    def get_window_state(self):
        """Return the last-saved window state dict with safe boundary clamping."""
        defaults = {'left': None, 'top': None,
                    'width': 560, 'height': 720, 'sidebar_open': False}
        saved = self._settings.get('window_state', {})
        defaults.update(saved)
        try:
            w = defaults.get('width')
            if w is not None:
                w_f = float(w)
                if w_f < 380 or w_f > 1600:
                    defaults['width'] = 560
            h = defaults.get('height')
            if h is not None:
                h_f = float(h)
                if h_f < 400 or h_f > 1400:
                    defaults['height'] = 720
        except Exception:
            defaults['width'] = 560
            defaults['height'] = 720
        return defaults

    def save_window_state(self, left, top, width, height, sidebar_open=False):
        """Persist window geometry and sidebar visibility with boundary clamping."""
        def _m(s):
            w = 560
            h = 720
            try:
                if width is not None:
                    w = max(380, min(int(float(width)), 1600))
                if height is not None:
                    h = max(400, min(int(float(height)), 1400))
            except Exception:
                w, h = 560, 720

            s['window_state'] = {
                'left':         left,
                'top':          top,
                'width':        w,
                'height':       h,
                'sidebar_open': sidebar_open,
            }
        return self._update(_m)

    def save_settings(self):
        """Save settings to file.

        Uses ensure_ascii=True + io.open(utf-8) so non-ASCII values
        (Vietnamese usernames, unicode paths) never break the dump
        under IronPython 2.7.

        Refuses when the last load failed on an existing file — writing then
        would replace real data with defaults.
        """
        if not self._load_ok:
            return False
        try:
            payload = json.dumps(self._settings, indent=2, ensure_ascii=True)
            if isinstance(payload, bytes):
                payload = payload.decode('ascii')
            with io.open(self._settings_file, 'w', encoding='utf-8') as f:
                f.write(payload)
            return True
        except Exception:
            return False

    def get_server_config(self):
        """Get server configuration"""
        return self._settings.get('server', {})

    def get_enabled_providers(self):
        """Get list of enabled providers"""
        return self._settings.get('providers', [])

    def get_api_key(self, provider_name):
        """Get API key for a provider — always reads fresh from the in-memory dict.

        The dict is kept in sync with the file by reload() / set_api_key().
        """
        return self._settings.get('api_keys', {}).get(provider_name)

    def set_api_key(self, provider_name, api_key):
        """Set API key for a provider.

        Reloads the file from disk first so that keys saved by other sessions
        (or other providers) are not accidentally overwritten by stale
        in-memory data.
        """
        def _m(s):
            s.setdefault('api_keys', {})[provider_name] = api_key
        return self._update(_m)

    def get_active_provider(self):
        """Return the name of the last-selected LLM provider ('claude', 'openai', 'ollama')."""
        return self._settings.get('active_provider', 'claude')

    def set_active_provider(self, name):
        """Persist the active provider name."""
        def _m(s):
            s['active_provider'] = name
        return self._update(_m)

    def get_provider_model(self, provider_name):
        """Return the saved model name for a provider, or None."""
        return self._settings.get('model_preferences', {}).get(provider_name)

    def set_provider_model(self, provider_name, model_name):
        """Persist the preferred model name for a provider."""
        def _m(s):
            s.setdefault('model_preferences', {})[provider_name] = model_name
        return self._update(_m)

    def get_username(self):
        """Return the saved user name, or default 'Thạnh'."""
        return self._settings.get('username', 'Thạnh')

    def set_username(self, username):
        """Persist the user name."""
        def _m(s):
            s['username'] = username
        return self._update(_m)

    # ------------------------------------------------------------------
    # Knowledge directories (RAG sources)
    # ------------------------------------------------------------------

    def get_knowledge_dirs(self):
        """Return the list of user-added knowledge directories."""
        return list(self._settings.get('knowledge', {}).get('dirs', []))

    def add_knowledge_dir(self, path):
        """Add a knowledge directory (reload-merge-save, like set_api_key)."""
        def _m(s):
            dirs = s.setdefault('knowledge', {}).setdefault('dirs', [])
            if path not in dirs:
                dirs.append(path)
        return self._update(_m)

    def remove_knowledge_dir(self, path):
        """Remove a knowledge directory."""
        def _m(s):
            dirs = s.setdefault('knowledge', {}).setdefault('dirs', [])
            if path in dirs:
                dirs.remove(path)
        return self._update(_m)

    def get_knowledge_option(self, key, default=None):
        """Read a scalar option from the knowledge block."""
        return self._settings.get('knowledge', {}).get(key, default)

    def set_knowledge_option(self, key, value):
        """Persist a scalar option in the knowledge block."""
        def _m(s):
            s.setdefault('knowledge', {})[key] = value
        return self._update(_m)

    # ------------------------------------------------------------------
    # Multi-agent switches
    # ------------------------------------------------------------------

    def is_multi_agent_enabled(self):
        """Kill switch for the specialist dispatcher (default on)."""
        return bool(self._settings.get('agents', {}).get('multi_agent', True))

    def is_llm_classify_enabled(self):
        """Whether the dispatcher may use one small LLM call to classify."""
        return bool(self._settings.get('agents', {}).get('llm_classify', True))

    def is_graph_mode_enabled(self):
        """Kill switch for the graph agent layer (default on).

        Only affects messages the PLANNER finds more than one goal in — a
        single-goal turn never reaches the graph layer at all, so turning this
        off changes nothing for the overwhelming majority of chats. It is here
        because a multi-step turn runs several agent turns back to back, and a
        user who wants one answer per message should be able to say so.
        """
        return bool(self._settings.get('agents', {}).get('graph_mode', True))

    def is_quality_mode_enabled(self):
        """Opus-parity switch (default off — trades cost/latency for depth).

        When on, the Claude provider prefers the most capable model for the
        default (Opus > Sonnet > Haiku), turns extended thinking on for agent
        turns, and raises the agentic token ceiling. Tiny utility calls
        (classification) pin a fast model via model_override and are unaffected.
        """
        return bool(self._settings.get('agents', {}).get('quality_mode', False))

    # ------------------------------------------------------------------
    # Chat verbosity
    #
    # Both default ON: the assistant showing its work live is what stops a
    # long turn from looking like a hang. OFF does NOT go silent — the cards
    # collapse into one clickable summary row and the running tool still
    # shows in the typing indicator. Hiding progress entirely would trade one
    # confusion for a worse one.
    # ------------------------------------------------------------------

    def is_show_tool_calls_enabled(self):
        """Full tool cards in the chat, vs one collapsed summary row."""
        return bool(self._settings.get('agents', {}).get(
            'show_tool_calls', True))

    def set_show_tool_calls(self, enabled):
        return self.set_agent_option('show_tool_calls', bool(enabled))

    def is_show_thinking_enabled(self):
        """Interim narration between tool calls ("Đang tạo dim cho...").

        Only the INTERIM turns are affected. The final answer of a turn is
        never suppressed — a turn that renders nothing at all is the bug this
        setting exists to avoid, not a mode it should offer.
        """
        return bool(self._settings.get('agents', {}).get(
            'show_thinking', True))

    def set_show_thinking(self, enabled):
        return self.set_agent_option('show_thinking', bool(enabled))

    def is_sync_with_central_allowed(self):
        """Whether the assistant may synchronise with central (default OFF).

        Deliberately opt-in: a sync publishes the user's work to everyone on
        the project, can run for minutes, and relinquishes worksets. Every
        other model edit is local and undoable; this one is neither, so it
        needs a decision the user made once, on purpose, outside the chat.
        """
        return bool(self._settings.get('agents', {}).get(
            'allow_sync_with_central', False))

    def set_sync_with_central_allowed(self, allowed):
        def _m(s):
            s.setdefault('agents', {})['allow_sync_with_central'] = bool(allowed)
        return self._update(_m)

    def get_action_mode(self):
        """Harness action mode for model-editing tools.

        'auto'    = agent executes edits immediately (legacy behavior).
        'confirm' = agent must reply with a plan and wait for the user's OK
                    before any model-modifying tool call.
        """
        mode = self._settings.get('agents', {}).get('action_mode', 'auto')
        return mode if mode in ('auto', 'confirm') else 'auto'

    def get_reply_language(self):
        """Language the assistant answers in.

        'auto' = follow the language the user wrote in (default), 'vi' and
        'en' pin it. Read by the Assistant for its own strings and threaded
        into the LLM system prompts so the model agrees with the UI.
        """
        lang = self._settings.get('agents', {}).get('reply_language', 'auto')
        return lang if lang in ('auto', 'vi', 'en') else 'auto'

    def set_reply_language(self, lang):
        """Persist the reply-language preference ('auto' | 'vi' | 'en')."""
        if lang not in ('auto', 'vi', 'en'):
            lang = 'auto'
        return self.set_agent_option('reply_language', lang)

    def get_agent_option(self, key, default=None):
        """Read a scalar switch from the agents block."""
        return self._settings.get('agents', {}).get(key, default)

    def set_agent_option(self, key, value):
        """Persist a switch in the agents block."""
        def _m(s):
            s.setdefault('agents', {})[key] = value
        return self._update(_m)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    def get_disabled_skills(self):
        """Return the list of skill ids the user switched off."""
        return list(self._settings.get('skills', {}).get('disabled', []))

    def set_skill_disabled(self, skill_id, disabled):
        """Toggle a skill on/off (persisted globally)."""
        def _m(s):
            items = s.setdefault('skills', {}).setdefault('disabled', [])
            if disabled and skill_id not in items:
                items.append(skill_id)
            elif not disabled and skill_id in items:
                items.remove(skill_id)
        return self._update(_m)

    # ------------------------------------------------------------------
    # Active project
    # ------------------------------------------------------------------

    def get_active_project(self):
        """Return the active project id, or None."""
        return self._settings.get('active_project')

    def set_active_project(self, project_id):
        """Persist the active project id (None = no project)."""
        def _m(s):
            s['active_project'] = project_id
        return self._update(_m)

    # ------------------------------------------------------------------
    # AI Mode (Tool Copilot & Augmented Features)
    # ------------------------------------------------------------------

    def is_ai_mode_enabled(self):
        """Return True if global AI Mode is enabled."""
        return bool(self._settings.get('ai_mode', {}).get('enabled', True))

    def set_ai_mode_enabled(self, enabled):
        """Enable or disable global AI Mode."""
        def _m(s):
            s.setdefault('ai_mode', {})['enabled'] = bool(enabled)
        return self._update(_m)

    def is_tool_ai_enabled(self, tool_name=None):
        """Return True if AI mode is enabled for a specific tool.
        If tool not explicitly configured, defaults to the global AI Mode state.
        """
        if not self.is_ai_mode_enabled():
            return False
        if not tool_name:
            return True
        toggles = self._settings.get('ai_mode', {}).get('tool_toggles', {})
        return bool(toggles.get(tool_name, True))

    def set_tool_ai_enabled(self, tool_name, enabled):
        """Set AI mode state for a specific tool."""
        def _m(s):
            s.setdefault('ai_mode', {}).setdefault('tool_toggles', {})[tool_name] = bool(enabled)
        return self._update(_m)

    def get_ai_mode_config(self):
        """Return the entire ai_mode configuration dictionary."""
        defaults = {
            'enabled': True,
            'fast_provider': 'auto',
            'reasoning_provider': 'auto',
            'tool_toggles': {},
        }
        saved = self._settings.get('ai_mode', {})
        defaults.update(saved)
        return defaults

    def log_model_usage(self, action, provider, model):
        """Log model usage/setup to a log file for audit and fast setup verification."""
        try:
            import datetime
            app_data = os.environ.get('APPDATA', '')
            settings_dir = os.path.join(app_data, 'T3LabAI')
            if not os.path.exists(settings_dir):
                os.makedirs(settings_dir)
            log_file = os.path.join(settings_dir, 'model_setup.log')
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_line = "[{}] Action: {} | Provider: {} | Model: {}\n".format(
                timestamp, action, provider, model
            )
            with open(log_file, 'a') as f:
                f.write(log_line)
        except Exception:
            pass



def get_settings():
    """Get the singleton settings instance"""
    return T3LabAISettings()
