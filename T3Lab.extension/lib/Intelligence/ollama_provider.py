# -*- coding: utf-8 -*-
"""
Ollama Provider

Local Ollama LLM adapter for the T3Lab LLM router.
Reuses local_llm.py for model discovery and the HTTP helpers in llm_provider.py.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Ollama Provider"

import json
import os
import sys
import time

from Intelligence.llm_provider import (BaseLLMProvider, http_post, http_get,
                                       http_post_stream,
                                       local_sampling_params, is_reasoning_model)


# ─── Provider ──────────────────────────────────────────────────────────────────

class OllamaProvider(BaseLLMProvider):
    """Adapter for a locally-running Ollama LLM server."""

    NAME                  = "ollama"
    DISPLAY_NAME          = "Local LLM (Ollama)"
    SUPPORTS_VISION       = False   # most small models don't support vision
    SUPPORTS_NATIVE_TOOLS = True    # qwen2.5 / llama3.1+ support /api/chat tools;
                                    # unsupported models fail turn 1 → caller
                                    # falls back to the legacy JSON-intent path

    # Context-window bounds for options.num_ctx. Without an explicit num_ctx
    # Ollama runs the model at its DEFAULT context (2048–4096 tokens) and
    # SILENTLY truncates everything above it — our system prompt + tool
    # catalog alone exceeds that, so the model literally never saw most of
    # its instructions (cloud providers with 128k+ windows were unaffected).
    NUM_CTX_MIN = int(os.environ.get("T3LAB_OLLAMA_NUM_CTX_MIN", 8192))
    NUM_CTX_MAX = int(os.environ.get("T3LAB_OLLAMA_NUM_CTX_MAX", 32768))

    # The installed-model list changes only when the user pulls/removes a model
    # or switches host — all of which invalidate the cache explicitly. So the
    # auto-selected model is cached for this long instead of re-probing
    # /api/tags on EVERY chat turn (a network round-trip that is pure overhead,
    # and painful against a remote/busy Ollama). Mirrors the router's 30s
    # status-cache TTL.
    MODEL_CACHE_TTL = float(os.environ.get("T3LAB_OLLAMA_MODEL_TTL", 30))

    def __init__(self):
        self._model = None        # None → auto-select best installed model
        self._host  = None        # None → read from local_llm.OLLAMA_HOST
        self._active_host = None  # last host that actually responded
        self._auto_model = None   # cached auto-selected model
        self._auto_model_at = 0.0 # monotonic-ish timestamp of that cache

    # Reasoning models spend most of a turn inside <think>...</think>; a tool
    # call or final answer only appears AFTER that. num_predict caps the whole
    # generation, so the instruct-tuned budget (1.5k) truncated the reply
    # before the model ever reached its tool call. Give thinking models real
    # room to finish reasoning and still answer.
    REASONING_MIN_PREDICT = int(os.environ.get("T3LAB_OLLAMA_MIN_PREDICT", 4096))

    def _num_predict_for(self, model, max_tokens):
        """Effective num_predict — widened for reasoning models."""
        if is_reasoning_model(model):
            return max(int(max_tokens), self.REASONING_MIN_PREDICT)
        return int(max_tokens)

    def _num_ctx_for(self, payload, max_tokens):
        """Pick a num_ctx that fits this request: estimated prompt tokens
        (chars/3 deliberately over-counts) + response budget, doubled up
        from NUM_CTX_MIN and clamped at NUM_CTX_MAX (VRAM guard)."""
        try:
            need = len(json.dumps(payload)) // 3 + int(max_tokens) + 512
        except Exception:
            return self.NUM_CTX_MIN
        num = self.NUM_CTX_MIN
        while num < need and num < self.NUM_CTX_MAX:
            num *= 2
        return min(num, self.NUM_CTX_MAX)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _local_llm(self):
        """Lazy-import local_llm module."""
        try:
            from Intelligence import local_llm
            return local_llm
        except Exception:
            return None

    def _configured_host(self):
        """Host saved in settings.json, else local_llm's env-backed default.

        Mirrors LMStudioProvider._configured_host. Without this, a custom
        Ollama URL lived only in memory: set_host() set an attribute nobody
        persisted, so the setting was lost on restart and the dialog always
        redisplayed http://localhost:11434.
        """
        host = None
        try:
            from config.settings import T3LabAISettings
            host = T3LabAISettings().get_api_key("Ollama_Host")
        except Exception:
            host = None
        if not host:
            mod = self._local_llm()
            host = mod.OLLAMA_HOST if mod else "http://localhost:11434"
        return host.rstrip("/") if host else host

    def _get_host(self):
        return self._active_host or self._host or self._configured_host()

    def _candidate_hosts(self):
        """Hosts to try, in order: explicit/configured → 127.0.0.1 → localhost."""
        cfg = self._host or self._configured_host()
        out = []
        for h in (cfg, "http://127.0.0.1:11434", "http://localhost:11434"):
            if h:
                h = h.rstrip("/")
                if h not in out:
                    out.append(h)
        return out


    # ── BaseLLMProvider interface ──────────────────────────────────────────────

    def _probe_tags(self):
        """Try each candidate host; return (host, [model_names]) for the first
        reachable Ollama with installed models, else (None, [])."""
        for host in self._candidate_hosts():
            try:
                tags = http_get(host + "/api/tags", timeout_ms=800)
                if not tags:
                    continue
                data = json.loads(tags)
                names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                if names:
                    self._active_host = host   # remember for chat/generate
                    return host, names
            except Exception:
                continue
        return None, []

    def check_health(self):
        """Return True if a reachable Ollama has at least one model installed."""
        host, names = self._probe_tags()
        return bool(names)

    def is_configured(self):
        """No API key to check — reachability IS the configuration test."""
        return self.check_health()

    def get_models(self):
        host, names = self._probe_tags()
        if names:
            return names
        # Fall back to local_llm's own discovery if direct probing found nothing.
        try:
            mod = self._local_llm()
            if mod:
                return mod.list_models()
        except Exception:
            pass
        return []

    @staticmethod
    def _quality_mode():
        """Opus-parity switch (agents.quality_mode). Off by default. When on,
        the auto-pick favours the strongest installed model over the fastest."""
        try:
            from config.settings import get_settings
            return bool(get_settings().is_quality_mode_enabled())
        except Exception:
            return False

    def get_active_model(self):
        if self._model:
            return self._model
        # Auto-select: reuse the recently-resolved model instead of probing
        # /api/tags on every turn. Only the short TTL window is cached; a pull/
        # remove/host-change invalidates it via reload_credentials/set_host.
        now = time.time()
        if self._auto_model and (now - self._auto_model_at) < self.MODEL_CACHE_TTL:
            return self._auto_model
        mod = self._local_llm()
        if not mod:
            return None
        picked = None
        # Rank the models installed on the host WE are configured for.
        # local_llm.get_best_model() re-discovers via its module-level
        # OLLAMA_HOST constant, so with a remote Ollama it queried localhost,
        # found nothing, and returned None — while check_health() (which does
        # honour _candidate_hosts) reported a green "Ready" dot.
        try:
            _host, names = self._probe_tags()
            if names:
                if self._quality_mode():
                    # Quality mode: strongest installed model (reasoning + size).
                    picked = mod.pick_best(names, prefer_capable=True)
                else:
                    # Default agentic path: prefer a tool-capable Qwen tier
                    # (qwen3:14b → 8b → 4b) over the smallest-first NLU pick, so
                    # multi-tool turns land on a model that actually tool-calls.
                    try:
                        picked = mod.pick_tool_capable(names)
                    except AttributeError:
                        picked = mod.pick_best(names, prefer_capable=False)
        except AttributeError:
            pass          # older local_llm without pick_best
        except Exception:
            pass
        if not picked:
            try:
                picked = mod.get_best_model(prefer_capable=self._quality_mode())
            except TypeError:
                # Older local_llm without the prefer_capable kwarg.
                picked = mod.get_best_model()
            except Exception:
                picked = None
        if picked:
            self._auto_model = picked
            self._auto_model_at = now
        return picked

    def set_model(self, model_name):
        self._model = model_name
        self._auto_model = None        # a pinned choice retires the auto cache
        return True

    def reload_credentials(self):
        """Re-read the configured host on the next call.

        Ollama needs no API key, but it does have a saved server URL — so this
        must drop the cached live host, exactly like LMStudioProvider does.
        """
        self._active_host = None
        self._auto_model = None        # host may have changed → re-rank models

    def invalidate_models_cache(self):
        """No-op — Ollama always fetches live from /api/tags."""
        pass

    def set_host(self, host):
        """Override the Ollama server URL (e.g. 'http://192.168.1.10:11434').

        Persists to settings.json so the choice survives a Revit restart.
        Doing the write HERE (rather than in the settings dialog) means no
        caller can accidentally take an in-memory-only path.
        """
        self._host = host
        self._active_host = None   # re-probe with the new host on next check
        self._auto_model = None    # different host → different installed models
        try:
            from config.settings import T3LabAISettings
            T3LabAISettings().set_api_key("Ollama_Host", host)
        except Exception:
            pass

    def warm_up(self):
        """Preload the active model so the FIRST real message isn't a cold load.

        keep_alive keeps a model resident only AFTER a first use, so without
        this the first message after the pane opens pays the multi-second VRAM
        load. Sends a 1-token generation and swallows everything — never raises,
        never blocks the UI (the caller runs it on a background thread).
        """
        try:
            model = self.get_active_model()
            if not model:
                return False
            payload = {
                "model":      model,
                "messages":   [{"role": "user", "content": u"hi"}],
                "stream":     False,
                "keep_alive": "15m",
                "options":    {"num_predict": 1, "temperature": 0.0},
            }
            http_post(self._get_host() + "/api/chat", payload, timeout_ms=15000)
            return True
        except Exception:
            return False

    def chat(self, messages, system_prompt, user_content, max_tokens=400, **kwargs):
        """
        Send a chat request to the local Ollama server.

        Vision is not supported — image blocks are stripped and only the text
        portions of user_content are sent.
        """
        # Flatten vision/multi-modal content to plain text
        if isinstance(user_content, list):
            text = self.blocks_to_text(user_content)
        else:
            text = user_content or ""

        model = self.get_active_model()
        if not model:
            return self._fail(
                u"chat(): Ollama has no model available — is the server "
                u"running, and has a model been pulled?")

        msgs = [{"role": "system", "content": system_prompt}]
        for h in (messages or [])[-8:]:
            role    = h.get("role", "user")
            content = h.get("content", "")
            if role not in ("user", "assistant"):
                continue
            if isinstance(content, list):
                content = self.blocks_to_text(content)
            if content:
                msgs.append({"role": role, "content": content})
        msgs.append({"role": "user", "content": text})

        payload = {
            "model":      model,
            "messages":   msgs,
            "stream":     False,
            # Keep the model resident between requests — reloading a local
            # model costs multi-second latency on every cold call.
            "keep_alive": "15m",
            "options":    {
                "temperature": 0.0,
                # Widened for reasoning models, exactly like the streaming path
                # — this used to pass max_tokens straight through, and only the
                # streaming path called _num_predict_for(). On a thinking model
                # the whole budget goes into the reasoning pass, so a 120-token
                # non-streaming call (Test Connection) came back with an empty
                # `content` and reported "Provider responded but returned an
                # empty reply" for a model that was working perfectly.
                "num_predict": self._num_predict_for(model, max_tokens),
            },
        }
        # JSON grammar is OPT-IN, honouring the caller's response_format the
        # same way LMStudioProvider does. It used to be hardcoded on for EVERY
        # call, which forced JSON out of paths that need prose: Test Connection
        # showed {"reply": "Connected OK"} instead of a sentence, the docked
        # pane's quick-chat returned JSON, and spell-check — whose prompt asks
        # for a line format and whose parser is a line regex — silently matched
        # nothing and reported zero findings on Ollama.
        if self._wants_json(kwargs.get("response_format")):
            payload["format"] = "json"
        payload["options"]["num_ctx"] = self._num_ctx_for(payload, max_tokens)

        try:
            # Local CPU/GPU inference on a multi-billion-parameter model can
            # legitimately take minutes, especially with "format": "json"
            # grammar-constrained decoding — the shared http_post() default
            # (60s, tuned for cloud APIs) was silently killing every slower
            # local generation. The caller saw a plain None with no error,
            # indistinguishable from "the model answered but scored unknown".
            # A caller that is only waiting on a tiny utility answer (e.g. the
            # dispatcher's one-label classification) passes a much shorter
            # timeout_ms — three minutes of dead wait ahead of the real turn
            # is worse than falling back to the keyword decision.
            resp_text = http_post(
                self._get_host() + "/api/chat",
                payload,
                timeout_ms=int(kwargs.get("timeout_ms") or 180000),
            )
            data = json.loads(resp_text)
            return self._reply_text(data.get("message") or {})
        except Exception:
            return None

    @staticmethod
    def _reply_text(message):
        """Answer text from an Ollama chat message.

        Newer Ollama builds return reasoning in a SEPARATE `thinking` field
        rather than inline <think> tags, so `content` alone is the right answer
        — but when generation is cut short mid-reasoning, `content` is empty
        while `thinking` holds everything. Returning "" there is what made a
        healthy qwen3.6 look like a broken provider. Fall back to the tail of
        the reasoning so the caller sees the model DID respond; a real answer
        still comes from `content` whenever there is one.
        """
        text = (message.get("content") or u"").strip()
        if text:
            return text
        thinking = (message.get("thinking") or u"").strip()
        if thinking:
            return thinking[-400:]
        return u""

    # ── Agentic chat (native tool calling, blocking) ──────────────────────────

    def chat_agent(self, system_prompt, messages, tools,
                   on_delta=None, max_tokens=1500, **kwargs):
        """One agentic turn via Ollama /api/chat `tools`.

        NO "format": "json" here — the reply is free text plus structured
        tool_calls. Ollama sends tool-call arguments as an object already.
        """
        model = self.get_active_model()
        if not model:
            return self._fail(
                u"chat_agent(): Ollama has no model available — is the server "
                u"running, and has a model been pulled?")

        msgs = [{"role": "system", "content": system_prompt}]
        msgs.extend(list(messages or []))

        # Reasoning models (Qwen3, ...) need non-greedy sampling to avoid the
        # thinking-mode repetition trap, plus a wider num_predict so a tool
        # call after <think> isn't truncated. Instruct models keep low-temp
        # determinism for stable tool JSON.
        opts = {"num_predict": self._num_predict_for(model, max_tokens)}
        opts.update(local_sampling_params(model))

        payload = {
            "model":      model,
            "messages":   msgs,
            "keep_alive": "15m",
            "options":    opts,
        }
        if tools:
            payload["tools"] = tools
        # Tool schemas get rendered INTO the prompt by Ollama's chat template,
        # so they count against num_ctx — size the window after adding them.
        payload["options"]["num_ctx"] = self._num_ctx_for(payload, max_tokens)

        url = self._get_host() + "/api/chat"

        # Qwen3-class models think by default. Quality mode keeps that deep
        # reasoning; normal mode turns it OFF for reasoning models — faster
        # replies AND steadier tool-call JSON (no rambling <think> before the
        # call). Only sent for reasoning models, and ONLY on the streaming
        # payload: the blocking fallback omits it so an older Ollama that
        # rejects the `think` field still recovers.
        think_val = None
        if is_reasoning_model(model):
            think_val = bool(self._quality_mode())

        # ── Streaming attempt (NDJSON, one JSON object per line) ──────────────
        # Ollama streams message.content deltas for a live typing effect and
        # emits tool_calls in a chunk near the end. Any failure falls through
        # to the blocking call below so a stream-averse setup still works.
        try:
            stream_payload = dict(payload)
            stream_payload["stream"] = True
            if think_val is not None:
                stream_payload["think"] = think_val
            state = {"content": [], "tool_calls": []}

            def _on_line(line, _st=state):
                if not line:
                    return
                try:
                    obj = json.loads(line.strip())
                except Exception:
                    return
                msg = obj.get("message") or {}
                piece = msg.get("content") or u""
                if piece:
                    _st["content"].append(piece)
                    if on_delta:
                        try:
                            on_delta(piece)
                        except Exception:
                            pass
                if msg.get("tool_calls"):
                    _st["tool_calls"].extend(msg["tool_calls"])

            http_post_stream(url, stream_payload, on_line=_on_line,
                             timeout_ms=180000)
            if state["content"] or state["tool_calls"]:
                full_msg = {"role": "assistant",
                            "content": u"".join(state["content"]),
                            "tool_calls": state["tool_calls"]}
                return self._assemble_agent_result(full_msg)
        except Exception as ex:
            self._debug_log("chat_agent stream failed, blocking: {}".format(ex))

        # ── Blocking fallback ────────────────────────────────────────────────
        try:
            block_payload = dict(payload)
            block_payload["stream"] = False
            resp_text = http_post(url, block_payload, timeout_ms=180000)
            data = json.loads(resp_text)
            return self._assemble_agent_result(data.get("message", {}) or {})
        except Exception as ex:
            self._debug_log("chat_agent() failed: {}".format(ex))
            return None

    @staticmethod
    def _assemble_agent_result(msg):
        """Build the uniform chat_agent dict from an Ollama assistant message
        (used by both the streaming and blocking paths)."""
        import re as _re
        text = msg.get("content") or u""
        text = _re.sub(r"<think>[\s\S]*?</think>", "", text).strip()

        tool_calls = []
        for c in (msg.get("tool_calls") or []):
            fn   = c.get("function", {}) or {}
            args = fn.get("arguments")
            if not isinstance(args, dict):
                try:
                    args = json.loads(args) if args else {}
                except Exception:
                    args = {}
            tool_calls.append({"id": "", "name": fn.get("name", ""),
                               "args": args})

        return {
            "text":          text,
            "tool_calls":    tool_calls,
            "assistant_msg": msg,
            "stop_reason":   "tool_use" if tool_calls else "end_turn",
        }

    @staticmethod
    def agent_tool_results(tool_calls, result_strs):
        """Ollama format: one role:"tool" message per call (no ids)."""
        out = []
        for i in range(len(tool_calls)):
            res = result_strs[i] if i < len(result_strs) else u'{"cancelled": true}'
            out.append({"role": "tool", "content": res})
        return out
