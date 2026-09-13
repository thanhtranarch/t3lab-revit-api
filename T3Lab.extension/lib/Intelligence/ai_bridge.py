# -*- coding: utf-8 -*-
"""
AI Mode Bridge — Unified AI Integration Hub for T3Lab Tools

Enables any T3Lab tool / dialog to connect with LLM models seamlessly
when AI Mode is enabled and API keys are configured in LLMs Setting.

Features:
  - Dual-mode checking: is_ai_mode_active(tool_name)
  - Safe, graceful degradation: never crashes when offline / missing keys
  - Structured JSON extraction with schema parsing
  - Semantic classification (CAD layers, IFC subtypes, parameter matching)
  - Non-blocking async execution for responsive Revit WPF UI
  - In-memory result cache for high-frequency repetitive queries

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import os
import sys
import json
import re
import time
import threading

def _get_logger():
    try:
        from pyrevit import script
        return script.get_logger()
    except Exception:
        import logging
        return logging.getLogger("T3Lab.AIModeBridge")

logger = _get_logger()


def _extract_json_from_text(text):
    """Safely extract JSON object or array from LLM response text."""
    if not text:
        return None
    # 1. Try direct parse
    trimmed = text.strip()
    try:
        return json.loads(trimmed)
    except Exception:
        pass

    # 2. Extract from markdown code blocks: ```json ... ``` or ``` ... ```
    m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text, re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass

    # 3. Find outermost curly braces { ... }
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except Exception:
            pass

    # 4. Find outermost brackets [ ... ]
    first_bracket = text.find('[')
    last_bracket = text.rfind(']')
    if first_bracket != -1 and last_bracket > first_bracket:
        try:
            return json.loads(text[first_bracket:last_bracket + 1])
        except Exception:
            pass

    return None


class AIModeBridge(object):
    """Singleton bridge providing AI services to all T3Lab Revit Tools."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(AIModeBridge, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._initialized = True

    # ------------------------------------------------------------------
    # Settings & Availability
    # ------------------------------------------------------------------

    def get_settings(self):
        """Get T3LabAISettings singleton instance."""
        try:
            from config.settings import T3LabAISettings
            return T3LabAISettings()
        except Exception as ex:
            logger.debug("AIModeBridge: failed to load settings: {}".format(ex))
            return None

    def get_router(self):
        """Get LLMRouter singleton instance."""
        try:
            from Intelligence.llm_router import LLMRouter
            return LLMRouter()
        except Exception as ex:
            logger.debug("AIModeBridge: failed to load LLMRouter: {}".format(ex))
            return None

    def is_ai_mode_active(self, tool_name=None):
        """Check if AI Mode is active globally, for this tool, and has a provider."""
        s = self.get_settings()
        if not s or not s.is_ai_mode_enabled():
            return False

        if tool_name and not s.is_tool_ai_enabled(tool_name):
            return False

        router = self.get_router()
        if not router:
            return False

        status = router.get_status(use_cache=True)
        active_name = router.get_active_name()
        active_info = status.get(active_name, {})
        if active_info.get("available"):
            return True

        # Check if any provider in fallback chain is available
        return any(info.get("available") for info in status.values())

    def get_active_provider_info(self):
        """Return dict with provider name, model, and display status."""
        router = self.get_router()
        if not router:
            return {"available": False, "provider": "None", "model": "", "label": "AI Offline"}

        active_name = router.get_active_name()
        status = router.get_status(use_cache=True)
        active_info = status.get(active_name, {})
        is_avail = bool(active_info.get("available"))
        model = active_info.get("model") or ""
        display_name = active_info.get("display_name") or active_name.capitalize()

        display = "{} ({})".format(display_name, model) if model else display_name
        return {
            "available": is_avail,
            "provider": active_name,
            "model": model,
            "label": display if is_avail else "AI Offline ({})".format(display_name)
        }

    # ------------------------------------------------------------------
    # Prompt & Completion Execution
    # ------------------------------------------------------------------

    def ask(self, prompt, system_prompt=None, max_tokens=1000, **kwargs):
        """Execute a text prompt against the active LLM provider.

        Returns:
            str: response text, or None if failed / AI Mode inactive.
        """
        router = self.get_router()
        if not router:
            return None

        sys_prompt = system_prompt or (
            "You are T3Lab BIM AI, an expert assistant for Autodesk Revit. "
            "Respond concisely and accurately."
        )

        try:
            response = router.chat(
                messages=[],
                system_prompt=sys_prompt,
                user_content=prompt,
                max_tokens=max_tokens,
                **kwargs
            )
            return response
        except Exception as ex:
            logger.warning("AIModeBridge.ask error: {}".format(ex))
            return None

    def ask_json(self, prompt, system_prompt=None, schema_description=None, max_tokens=2000, **kwargs):
        """Execute a prompt and parse the result into a Python dict or list.

        Args:
            prompt (str): user instruction
            system_prompt (str|None): base prompt
            schema_description (str|None): guidelines on expected JSON fields
            max_tokens (int): token budget

        Returns:
            dict|list|None: parsed JSON or None if failed
        """
        sys_p = system_prompt or (
            "You are T3Lab BIM AI. You MUST respond with ONLY valid JSON. "
            "Do not include any conversational preamble, commentary, or markdown outside the JSON block."
        )
        if schema_description:
            sys_p += "\nExpected JSON format:\n" + schema_description

        raw_res = self.ask(prompt, system_prompt=sys_p, max_tokens=max_tokens, **kwargs)
        if not raw_res:
            return None

        parsed = _extract_json_from_text(raw_res)
        if parsed is None:
            logger.debug("AIModeBridge.ask_json failed to parse JSON from: {}".format(raw_res[:200]))
        return parsed

    def classify(self, input_text, candidate_categories, context=None):
        """Classify input text into one of the candidate categories.

        Uses caching for identical inputs.
        """
        if not input_text or not candidate_categories:
            return None

        cache_key = "cls:{}:{}".format(input_text.strip().lower(), ",".join(sorted(candidate_categories)))
        cached = self.get_cache(cache_key)
        if cached:
            return cached

        prompt = (
            "Classify the following BIM item into exactly ONE of the provided categories.\n"
            "Item: \"{}\"\n"
            "Context: {}\n"
            "Candidate categories:\n{}\n\n"
            "Return JSON format:\n"
            "{{\"selected_category\": \"<one of candidate categories>\", \"confidence\": 0.0-1.0, \"rationale\": \"brief reason\"}}"
        ).format(
            input_text,
            context or "Autodesk Revit Model element / layer",
            "\n".join(["- " + c for c in candidate_categories])
        )

        res = self.ask_json(prompt, max_tokens=300)
        if res and isinstance(res, dict) and "selected_category" in res:
            cat = res["selected_category"]
            if cat in candidate_categories:
                self.set_cache(cache_key, res, ttl_seconds=7200)
                return res

        return None

    # ------------------------------------------------------------------
    # Asynchronous Helper
    # ------------------------------------------------------------------

    def run_async(self, worker_func, on_done=None, on_error=None):
        """Run worker_func in a background thread and safely call on_done on finish.

        Args:
            worker_func: callable taking no arguments, returns result.
            on_done: callback taking (result).
            on_error: callback taking (exception).
        """
        def _runner():
            try:
                res = worker_func()
                if on_done:
                    on_done(res)
            except Exception as ex:
                logger.warning("AIModeBridge async worker error: {}".format(ex))
                if on_error:
                    on_error(ex)

        t = threading.Thread(target=_runner)
        t.daemon = True
        t.start()
        return t

    # ------------------------------------------------------------------
    # Result Caching
    # ------------------------------------------------------------------

    def get_cache(self, key):
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry:
                val, exp = entry
                if time.time() < exp:
                    return val
                del self._cache[key]
        return None

    def set_cache(self, key, value, ttl_seconds=3600):
        with self._cache_lock:
            self._cache[key] = (value, time.time() + ttl_seconds)


# Global singleton instance
ai_bridge = AIModeBridge()
