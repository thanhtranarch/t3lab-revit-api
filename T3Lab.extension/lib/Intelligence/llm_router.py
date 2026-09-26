# -*- coding: utf-8 -*-
"""
LLM Router

Singleton that manages LLM provider selection, hot-swap, and automatic fallback.

Usage:
    from Intelligence.llm_router import LLMRouter

    router = LLMRouter()
    router.switch_provider("openai")              # hot-swap
    router.set_model("openai", "gpt-4o")          # change model
    text = router.chat(history, sys_prompt, user_content)

    status = router.get_status()
    # {"claude": {"available": True, "model": "claude-haiku-...", ...}, ...}

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "LLM Router"

import os
import sys


def _debug_log(msg):
    """Best-effort debug log via pyRevit's logger; never raises.

    Several except-blocks in this module used to swallow failures with zero
    trace (e.g. a provider silently failing to import looked identical to
    "not installed"). Lazy-imported so this module stays importable outside
    a pyRevit/Revit process.
    """
    try:
        from pyrevit import script
        script.get_logger().debug(msg)
    except Exception:
        pass


# ─── Router ────────────────────────────────────────────────────────────────────

class LLMRouter(object):
    """
    Singleton router for multi-provider LLM access.

    Provider priority / fallback order:
        claude → openai → ollama

    Switching is hot (no session restart required).
    The active provider and per-provider model are persisted in settings.json.
    """

    _instance = None

    # Canonical fallback order — also used as the display order in UI
    FALLBACK_CHAIN = ["claude", "openai", "deepseek", "ollama", "lmstudio"]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LLMRouter, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        import threading
        self._providers     = {}
        self._active_name   = "claude"
        self._fallback_on   = True
        self._status_cache  = None   # cached result of get_status()
        self._status_ts     = 0.0    # epoch time of last cache fill
        # Guards reads/writes of _status_cache/_status_ts. script.py fires
        # get_status()/probe_provider() from several independent background
        # threads (startup probe, sidebar open, provider switch) that can
        # overlap; without a lock, one thread's cache write can race another
        # thread's cache read/iteration over the same dict.
        self._status_lock   = threading.Lock()
        self._initialized   = True

        self._load_providers()
        self._restore_settings()

    # ── Initialisation helpers ─────────────────────────────────────────────────

    def _load_providers(self):
        """Instantiate all provider adapters (failures are silenced)."""
        adapters = [
            ("claude",    "Intelligence.claude_provider",    "ClaudeProvider"),
            ("openai",    "Intelligence.openai_provider",    "OpenAIProvider"),
            ("deepseek",  "Intelligence.deepseek_provider",  "DeepSeekProvider"),
            ("ollama",    "Intelligence.ollama_provider",    "OllamaProvider"),
            ("lmstudio",  "Intelligence.lmstudio_provider",  "LMStudioProvider"),
        ]
        for name, module_path, class_name in adapters:
            try:
                parts  = module_path.split(".")
                mod    = __import__(module_path, fromlist=[parts[-1]])
                cls_   = getattr(mod, class_name)
                self._providers[name] = cls_()
            except Exception as ex:
                # A real bug here (typo, missing dependency) looks identical
                # to "provider not installed" without this — log it so the
                # difference is visible when debugging why a provider never
                # shows up.
                _debug_log("LLMRouter: failed to load provider '{}': {}".format(name, ex))

    def _restore_settings(self):
        """Load the last-used provider and per-provider model from settings."""
        try:
            _ensure_lib_in_path()
            from config.settings import T3LabAISettings
            s = T3LabAISettings()

            saved_provider = s.get_active_provider()
            if saved_provider and saved_provider in self._providers:
                self._active_name = saved_provider

            for name, provider in self._providers.items():
                saved_model = s.get_provider_model(name)
                if saved_model:
                    try:
                        provider.set_model(saved_model)
                    except Exception:
                        pass

            # Log restored startup configuration
            active_provider = self.get_active_provider()
            active_model = active_provider.get_active_model() if active_provider else None
            s.log_model_usage("STARTUP_RESTORE", self._active_name, active_model)
        except Exception as ex:
            _debug_log("LLMRouter: failed to restore settings: {}".format(ex))

    # ── Provider management ────────────────────────────────────────────────────

    def get_active_name(self):
        """Return the name of the currently active provider."""
        return self._active_name

    def get_active_provider(self):
        """Return the active provider instance, or None."""
        return self._providers.get(self._active_name)

    def switch_provider(self, name, model=None, persist=True):
        """
        Hot-swap to a different provider.

        Args:
            name (str): provider name — "claude", "openai", or "ollama".
            model (str|None): optional model to activate on the new provider.
            persist (bool): write the choice to settings.json as the user's
                global default. Pass False for a SCOPED switch (e.g. a project
                workspace's provider override) — otherwise activating a project
                permanently rewrites the user's default and deactivating it
                never restores what they had.

        Returns:
            bool: True on success, False if provider is unknown.
        """
        if name not in self._providers:
            return False

        self._active_name = name
        # Note: do NOT invalidate the status cache here. The only thing a switch
        # changes is the "active" flag, which get_status() applies live to the
        # cached snapshot. Invalidating would force a slow full re-probe of every
        # provider on the next status call (e.g. opening the badge menu).

        from config.settings import T3LabAISettings
        s = T3LabAISettings()

        if model is None:
            model = s.get_provider_model(name)

        if model:
            self._providers[name].set_model(model)
        else:
            try:
                model = self._providers[name].get_active_model()
            except Exception:
                pass

        # Persist provider selection (skipped for scoped switches)
        if persist:
            try:
                s.set_active_provider(name)
            except Exception:
                pass

        # Log provider hot-swap action
        try:
            s.log_model_usage("SWITCH_PROVIDER", name, model)
        except Exception:
            pass

        # A switch changes which provider is "active"; the cached snapshot
        # tracks that live, but the model may have changed too.
        with self._status_lock:
            if self._status_cache and name in self._status_cache and model:
                self._status_cache[name]["model"] = model

        return True

    def set_model(self, provider_name, model_name):
        """
        Set the model for a specific provider and persist the choice.

        Returns:
            bool: True if the provider exists and accepted the model.
        """
        provider = self._providers.get(provider_name)
        if not provider:
            return False

        ok = provider.set_model(model_name)
        if ok:
            try:
                _ensure_lib_in_path()
                from config.settings import T3LabAISettings
                s = T3LabAISettings()
                s.set_provider_model(provider_name, model_name)
                s.log_model_usage("SET_MODEL", provider_name, model_name)
            except Exception:
                pass
            # Keep the cached snapshot honest. get_status_instant() /
            # get_status_cached() are what the assistant's composer chip and the
            # settings dialog render from, and nothing re-probes on a timer — so
            # without this the UI keeps showing the PREVIOUS model indefinitely.
            with self._status_lock:
                if self._status_cache and provider_name in self._status_cache:
                    self._status_cache[provider_name]["model"] = model_name

        return ok


    def get_provider(self, name):
        """Return a provider instance by name, or None."""
        return self._providers.get(name)

    def get_provider_names(self):
        """Return list of loaded provider names in FALLBACK_CHAIN order."""
        return [n for n in self.FALLBACK_CHAIN if n in self._providers]


    # ── Chat ──────────────────────────────────────────────────────────────────

    def chat(self, messages, system_prompt, user_content, max_tokens=400, **kwargs):
        """
        Route a chat request to the active provider and return raw response text.

        If the active provider fails and fallback is enabled, the router tries
        each provider in FALLBACK_CHAIN order until one succeeds.

        Args:
            messages (list): conversation history [{role, content}, ...].
            system_prompt (str): system instruction.
            user_content (str|list): current user input (string or content blocks).
            max_tokens (int): maximum response tokens.

        Returns:
            str | None: raw response text, or None if all providers fail.
        """
        active = self._providers.get(self._active_name)

        # ── Active provider ────────────────────────────────────────────────────
        if active:
            try:
                result = active.chat(messages, system_prompt, user_content, max_tokens, **kwargs)
                if result is not None:
                    return result
            except Exception:
                pass

        if not self._fallback_on:
            return None

        # ── Fallback chain ─────────────────────────────────────────────────────
        for name in self.FALLBACK_CHAIN:
            if name == self._active_name:
                continue
            provider = self._providers.get(name)
            if not provider:
                continue
            try:
                if not provider.check_health():
                    continue
                result = provider.chat(messages, system_prompt, user_content, max_tokens, **kwargs)
                if result is not None:
                    return result
            except Exception:
                continue

        return None

    def chat_stream(self, messages, system_prompt, user_content,
                    on_delta=None, max_tokens=400, **kwargs):
        """
        Route a streaming chat request to the active provider, with fallback.

        on_delta(text_chunk) is invoked for each streamed piece of text. Mirrors
        chat()'s active-then-fallback strategy. Returns the full response text,
        or None if every provider fails.
        """
        active = self._providers.get(self._active_name)

        if active:
            try:
                result = active.chat_stream(
                    messages, system_prompt, user_content, on_delta, max_tokens, **kwargs)
                if result is not None:
                    return result
            except Exception:
                pass

        if not self._fallback_on:
            return None

        for name in self.FALLBACK_CHAIN:
            if name == self._active_name:
                continue
            provider = self._providers.get(name)
            if not provider:
                continue
            try:
                if not provider.check_health():
                    continue
                result = provider.chat_stream(
                    messages, system_prompt, user_content, on_delta, max_tokens, **kwargs)
                if result is not None:
                    return result
            except Exception:
                continue

        return None

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self, use_cache=True):
        """
        Return a health snapshot of every loaded provider.

        Results are cached for 30 seconds so repeated calls (e.g. sidebar
        re-renders) are instant. Pass use_cache=False to force a fresh probe.

        Returns:
            dict: {
                "claude": {"available": bool, "model": str|None,
                           "display_name": str, "active": bool,
                           "supports_vision": bool},
                ...
            }
        """
        import time
        now = time.time()
        with self._status_lock:
            if use_cache and self._status_cache is not None and (now - self._status_ts) < 30:
                # Return cached snapshot but update the "active" flag live
                snap = {}
                for name, info in self._status_cache.items():
                    snap[name] = dict(info)
                    snap[name]["active"] = (name == self._active_name)
                return snap

        import threading
        status = {}
        threads = []
        # Local lock, deliberately NOT self._status_lock: a worker calling
        # check_health() must never contend with (or re-enter) the router-level
        # lock the block below acquires. IronPython has no GIL, so five threads
        # inserting into a plain dict can collide during a resize.
        status_lock = threading.Lock()

        def probe_worker(name):
            provider = self._providers[name]
            try:
                # Every provider gets a REAL health check. This used to report a
                # non-active remote provider "available" from mere API-key
                # presence, while probe_provider() used check_health() — both
                # wrote the same cache, so the settings dialog could show a
                # revoked key as red and then flip it green two seconds later.
                # The workers run in parallel, so wall-clock is bounded by the
                # slowest provider, not the sum.
                available = provider.check_health()
            except Exception:
                available = False
            try:
                model = provider.get_active_model()
            except Exception:
                model = None
            info = {
                "available":       available,
                "model":           model,
                "display_name":    provider.DISPLAY_NAME,
                "active":          (name == self._active_name),
                "supports_vision": provider.SUPPORTS_VISION,
                "probed":          True,
            }
            with status_lock:
                status[name] = info

        probe_order = self.get_local_provider_names() + self.get_remote_provider_names()
        for name in probe_order:
            t = threading.Thread(target=probe_worker, args=(name,))
            t.daemon = True
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        with self._status_lock:
            self._status_cache = status
            self._status_ts    = now
        return status

    def get_status_cached(self):
        """
        Return the cached status snapshot WITHOUT ever probing — instant, safe
        to call on the UI thread. Returns None when nothing has been probed yet.

        get_status(use_cache=True) still re-probes when the 30s TTL lapsed,
        which is a multi-second, network-bound operation — UI render paths
        (sidebar refresh, badge menu) must use this instead and leave probing
        to background threads.
        """
        with self._status_lock:
            if not self._status_cache:
                return None
            snap = {}
            for name, info in self._status_cache.items():
                snap[name] = dict(info)
                snap[name]["active"] = (name == self._active_name)
            return snap

    def get_status_instant(self):
        """
        Zero-HTTP status snapshot for instant UI: cached data when present,
        else availability approximated by key-presence (remote) / False
        (local). Never blocks on the network.
        """
        snap = self.get_status_cached() or {}
        for name, provider in self._providers.items():
            if name in snap:
                continue
            try:
                if name in ("ollama", "lmstudio"):
                    available = False          # needs a port probe → unknown yet
                elif hasattr(provider, "_get_api_key"):
                    available = bool(provider._get_api_key())
                else:
                    available = False
            except Exception:
                available = False
            # NEVER call provider.get_active_model() here. For Ollama/LM Studio
            # it does real HTTP (tag probes, and local_llm's .NET GET used to
            # inherit a ~100s timeout), and this function is called from the UI
            # thread — that combination froze Revit while opening LLMs Setting.
            # The saved preference is the only zero-HTTP source of truth.
            try:
                _ensure_lib_in_path()
                from config.settings import T3LabAISettings
                model = T3LabAISettings().get_provider_model(name)
            except Exception:
                model = None
            snap[name] = {
                "available":       available,
                "model":           model,
                "display_name":    provider.DISPLAY_NAME,
                "active":          (name == self._active_name),
                "supports_vision": provider.SUPPORTS_VISION,
                "probed":          False,   # guessed, not health-checked
            }
        return snap

    def probe_provider(self, name):
        """
        Probe a SINGLE provider and merge its result into the status cache.

        Much faster than get_status() when you only need to refresh the active
        provider (e.g. right after a hot-swap). Returns the provider's status
        dict or None if the provider is unknown.
        """
        provider = self._providers.get(name)
        if not provider:
            return None
        try:
            available = provider.check_health()
        except Exception:
            available = False
        try:
            model = provider.get_active_model()
        except Exception:
            model = None
        info = {
            "available":       available,
            "model":           model,
            "display_name":    provider.DISPLAY_NAME,
            "active":          (name == self._active_name),
            "supports_vision": provider.SUPPORTS_VISION,
            "probed":          True,
        }
        # Merge into cache so subsequent get_status(use_cache=True) sees it.
        # Arm the 30s TTL only once the cache covers EVERY loaded provider —
        # arming it after a single-provider probe would serve a partial
        # snapshot for 30s and blank the other providers' status dots.
        with self._status_lock:
            if self._status_cache is None:
                self._status_cache = {}
            self._status_cache[name] = dict(info)
            if set(self._status_cache) >= set(self._providers):
                import time as _t
                self._status_ts = _t.time()
        return info

    def get_local_provider_names(self):
        """Return loaded local (no-API-key) provider names — probed first for speed."""
        return [n for n in ("ollama", "lmstudio") if n in self._providers]

    def get_remote_provider_names(self):
        """Return loaded remote (API-key) provider names."""
        return [n for n in self.FALLBACK_CHAIN
                if n in self._providers and n not in ("ollama", "lmstudio")]


    def get_display_label(self):
        """
        Return a short label for the UI badge (e.g. "GPT-4o-mini", "LOCAL").
        Mirrors the badge logic that was previously in script.py.
        """
        provider = self._providers.get(self._active_name)
        if not provider:
            return "OFFLINE"

        name = self._active_name
        try:
            model = provider.get_active_model() or ""
        except Exception:
            model = ""

        if name == "ollama":
            return "LOCAL"
        if name == "lmstudio":
            short = model.split("/")[-1] if "/" in model else model
            return short[:14] if short else "LM Studio"
        if name == "claude":
            # Shorten the model name for display
            if "haiku" in model:
                return "Haiku"
            if "sonnet" in model:
                return "Sonnet"
            if "opus" in model:
                return "Opus"
            return "Claude"
        if name == "openai":
            if "mini" in model:
                return "GPT-4o mini"
            if "4o" in model:
                return "GPT-4o"
            if "turbo" in model:
                return "GPT-4 Turbo"
            return "GPT"
        if name == "deepseek":
            if "reasoner" in model:
                return "DS Reasoner"
            return "DeepSeek"

        return name.upper()


# ─── Module-level singleton accessor ──────────────────────────────────────────

def get_router():
    """Return the global LLMRouter singleton."""
    return LLMRouter()


# ─── Pane chat helpers ──────────────────────────────────────────────────────────
# Used by AssistantPaneControl (DockablePane) for lightweight chat without the
# full JSON-intent pipeline that parse_command() uses.

_PANE_SYSTEM_PROMPT = (
    "You are T3Lab Assistant, an AI helper integrated directly into Autodesk Revit. "
    "You help architects, engineers, and BIM managers with Revit workflows, BIM coordination, "
    "and T3Lab tool usage. "
    "Be concise and practical — aim for under 120 words unless the user asks for detail. "
    "Use plain text (no Markdown). "
    "Respond in the SAME language the user wrote in."
)


# ─── Path helper ───────────────────────────────────────────────────────────────

def _ensure_lib_in_path():
    here    = os.path.dirname(os.path.abspath(__file__))
    lib_dir = os.path.dirname(here)
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
