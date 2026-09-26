# -*- coding: utf-8 -*-
"""
User Profile

Dedicated store for the T3Lab Assistant user's information (name, email, role)
together with their chosen model setup (provider + model). Lives in its own
file, separate from the low-level settings.json, and drives the first-run
onboarding experience for fresh installs.

File: %APPDATA%/T3LabAI/user_profile.json

The profile is the friendly, user-facing record. For backward compatibility it
keeps the legacy `settings.json` values (`username`, `active_provider`,
`model_preferences`) in sync, so the LLM router keeps working unchanged.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "User Profile"

import io
import os
import json
import datetime

_DEFAULT_NAME = u"Thạnh"


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class UserProfile(object):
    """Singleton manager for the user profile file."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UserProfile, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._path = self._get_path()
        self._data = self._load()
        self._initialized = True

    # ── Storage ────────────────────────────────────────────────────────────────

    def _get_path(self):
        app_data = os.environ.get('APPDATA', '') or os.path.expanduser('~')
        d = os.path.join(app_data, 'T3LabAI')
        if not os.path.exists(d):
            try:
                os.makedirs(d)
            except Exception:
                pass
        return os.path.join(d, 'user_profile.json')

    @staticmethod
    def _default():
        return {
            "user":  {"name": u"", "email": u"", "role": u""},
            "model": {"provider": u"", "model": u""},
            "setup_completed": False,
            "created_at": None,
            "updated_at": None,
        }

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, 'r') as f:
                    raw = json.load(f)
                data = self._default()
                data.update(raw or {})
                # Re-merge nested dicts so partial/older files don't drop keys.
                for key in ("user", "model"):
                    merged = self._default()[key]
                    merged.update(data.get(key) or {})
                    data[key] = merged
                return data
            except Exception:
                pass
        return self._default()

    def save(self):
        """Persist the profile to disk. Returns True on success."""
        try:
            self._data["updated_at"] = _now()
            if not self._data.get("created_at"):
                self._data["created_at"] = self._data["updated_at"]
            # Serialize to an ASCII string FIRST, then write in one shot —
            # json.dump(..., ensure_ascii=False) on a bytes-mode file dies
            # with UnicodeEncodeError under IronPython 2.7 (the default name
            # "Thạnh" alone triggers it), truncating the profile to 0 bytes.
            data = json.dumps(self._data, indent=2, ensure_ascii=True)
            if isinstance(data, bytes):
                data = data.decode('ascii')
            with io.open(self._path, 'w', encoding='utf-8') as f:
                f.write(data)
            return True
        except Exception:
            return False

    def exists(self):
        """True if a profile file is present on disk."""
        return os.path.exists(self._path)

    # ── First-run detection ────────────────────────────────────────────────────

    def is_first_run(self):
        """
        True only for a genuinely fresh install: setup never completed, no name
        saved, no legacy custom username, and no API key configured. Existing
        users are never shown onboarding.
        """
        try:
            if self._data.get("setup_completed"):
                return False
            if (self._data.get("user", {}).get("name") or u"").strip():
                return False
            if self._legacy_username():
                return False
            if self._has_any_api_key():
                return False
            return True
        except Exception:
            # On any doubt, don't nag the user with onboarding.
            return False

    def mark_setup_completed(self):
        self._data["setup_completed"] = True
        return self.save()

    # ── User info ──────────────────────────────────────────────────────────────

    def get_name(self, fallback=True):
        name = (self._data.get("user", {}).get("name") or u"").strip()
        if name:
            return name
        if fallback:
            return self._legacy_username() or _DEFAULT_NAME
        return u""

    def set_name(self, name):
        self._data.setdefault("user", {})["name"] = (name or u"").strip()
        self.save()
        # Keep legacy settings.username in sync for any code still reading it.
        try:
            from config.settings import T3LabAISettings
            T3LabAISettings().set_username((name or u"").strip())
        except Exception:
            pass


    # ── Model setup ────────────────────────────────────────────────────────────


    def set_model_setup(self, provider, model=None):
        """Record the chosen provider/model and sync it into settings.json."""
        m = self._data.setdefault("model", {})
        if provider:
            m["provider"] = provider
        if model is not None:
            m["model"] = model
        self.save()
        # Sync to settings so LLMRouter picks up the same choice on next load.
        try:
            from config.settings import T3LabAISettings
            s = T3LabAISettings()
            if provider:
                s.set_active_provider(provider)
            if model:
                s.set_provider_model(provider, model)
        except Exception:
            pass

    # ── Migration / detection helpers ──────────────────────────────────────────

    @staticmethod
    def _legacy_username():
        """Return a custom username saved in old settings.json, else ''.

        The settings default is _DEFAULT_NAME, which we treat as 'not set'.
        """
        try:
            from config.settings import T3LabAISettings
            u = T3LabAISettings().get_username()
            if u and u != _DEFAULT_NAME:
                return u
        except Exception:
            pass
        return u""

    @staticmethod
    def _has_any_api_key():
        try:
            from config.settings import T3LabAISettings
            s = T3LabAISettings()
            for key in ("Claude", "OpenAI", "DeepSeek"):
                if s.get_api_key(key):
                    return True
        except Exception:
            pass
        return False


# ─── Module-level singleton accessor ──────────────────────────────────────────

def get_profile():
    """Return the global UserProfile singleton."""
    return UserProfile()
