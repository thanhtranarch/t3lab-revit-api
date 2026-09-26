# -*- coding: utf-8 -*-
"""
LLM Provider

Abstract base class and shared HTTP helper for all LLM provider adapters.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "LLM Provider"

import json
import re

# ─── Shared HTTP backend ───────────────────────────────────────────────────────
# IronPython/pyRevit: use .NET WebClient.
# CPython (testing): fall back to urllib.

_USE_NET = False
try:
    import clr
    clr.AddReference('System.Net')
    from System.Net import WebClient
    from System.Text import Encoding as _NetEncoding
    _USE_NET = True
except Exception:
    pass

_HAS_URLLIB = False
if not _USE_NET:
    try:
        from urllib.request import urlopen, Request  # Python 3 / CPython
        _HAS_URLLIB = True
    except ImportError:
        try:
            from urllib2 import urlopen, Request     # Python 2 / IronPython fallback
            _HAS_URLLIB = True
        except Exception:
            pass

HAS_HTTP = _USE_NET or _HAS_URLLIB


def _http_error_detail(ex):
    """Pull the response body out of a failed HTTP call.

    Vendors put the ACTUAL reason in the error body ("Model Not Exist",
    "Insufficient Balance", "does not support function calling", ...) —
    without this, every API rejection surfaces as a bare WebException/
    HTTPError and the UI can only say "the model didn't respond".
    """
    # .NET WebException carries the response object
    try:
        resp = getattr(ex, "Response", None)
        if resp is not None:
            from System.IO import StreamReader
            reader = StreamReader(resp.GetResponseStream(), _NetEncoding.UTF8)
            try:
                body = reader.ReadToEnd()
            finally:
                reader.Close()
            if body:
                return body[:400]
    except Exception:
        pass
    # urllib2.HTTPError is itself file-like
    try:
        read = getattr(ex, "read", None)
        if callable(read):
            body = read()
            if isinstance(body, bytes):
                body = body.decode("utf-8", "replace")
            if body:
                return body[:400]
    except Exception:
        pass
    return None


def _raise_with_detail(ex):
    """Re-raise a transport error, upgrading it to carry the API error body."""
    detail = _http_error_detail(ex)
    if detail:
        raise RuntimeError(u"API error: {}".format(detail))
    raise ex


def http_get_auth(url, headers=None, timeout_ms=8000):
    """
    Authenticated GET request with optional headers.
    Returns response text string, or None on error.
    Mirrors http_post's dual .NET / urllib backend.
    """
    if _USE_NET:
        try:
            from System.Net import WebClient
            client = WebClient()
            try:
                client.Encoding = _NetEncoding.UTF8
                if headers:
                    for k, v in headers.items():
                        client.Headers.Add(k, v)
                return client.DownloadString(url)
            finally:
                try:
                    client.Dispose()
                except Exception:
                    pass
        except Exception:
            pass

    if _HAS_URLLIB:
        try:
            req = Request(url)
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)
            timeout_sec = float(timeout_ms) / 1000.0
            resp = urlopen(req, timeout=timeout_sec)
            raw = resp.read()
            return raw.decode("utf-8") if isinstance(raw, bytes) else raw
        except Exception:
            pass

    return None


def http_post(url, payload, headers=None, timeout_ms=60000):
    """
    POST a JSON-serialisable payload and return the response string.

    Args:
        url: target URL string.
        payload: dict to serialise as JSON.
        headers: optional dict of extra request headers.
        timeout_ms: request timeout in milliseconds. Default 60000 (60s) —
            fine for cloud APIs. Local providers (Ollama/LM Studio) doing
            CPU inference on a multi-billion-parameter model routinely need
            much longer; callers there should pass a larger value.
            IMPORTANT: plain System.Net.WebClient has NO Timeout property,
            so without explicitly building the request via HttpWebRequest
            (as done below), every local-model call silently inherited
            .NET's ~100s default and failed on any slower model/machine —
            indistinguishable from "the model answered badly", when in fact
            the request never completed at all.

    Returns:
        str: response body, or raises RuntimeError on failure.
    """
    body = json.dumps(payload, ensure_ascii=False)
    if _USE_NET:
        from System.Net import HttpWebRequest
        from System.IO import StreamReader
        body_bytes = _NetEncoding.UTF8.GetBytes(body)
        req = HttpWebRequest.Create(url)
        req.Method           = "POST"
        req.ContentType      = "application/json; charset=utf-8"
        req.Timeout          = timeout_ms
        req.ReadWriteTimeout  = timeout_ms
        if headers:
            for k, v in headers.items():
                req.Headers.Add(k, v)
        req.ContentLength = body_bytes.Length
        rs = req.GetRequestStream()
        try:
            rs.Write(body_bytes, 0, body_bytes.Length)
        finally:
            rs.Close()
        try:
            resp = req.GetResponse()
        except Exception as ex:
            _raise_with_detail(ex)
        try:
            reader = StreamReader(resp.GetResponseStream(), _NetEncoding.UTF8)
            try:
                return reader.ReadToEnd()
            finally:
                reader.Close()
        finally:
            resp.Close()

    if _HAS_URLLIB:
        if isinstance(body, type(u"")):
            body_bytes = body.encode("utf-8")
        else:
            body_bytes = body
        req_headers = {"Content-Type": "application/json; charset=utf-8"}
        if headers:
            req_headers.update(headers)
        req = Request(url, body_bytes, req_headers)
        try:
            resp = urlopen(req, timeout=float(timeout_ms) / 1000.0)
        except Exception as ex:
            _raise_with_detail(ex)
        raw = resp.read()
        return raw.decode("utf-8") if isinstance(raw, bytes) else raw

    raise RuntimeError("No HTTP client available")


def http_get(url, timeout_ms=4000):
    """GET url; return response string or None. Times out after timeout_ms."""
    try:
        if _USE_NET:
            from System.Net import HttpWebRequest
            from System.IO import StreamReader
            req = HttpWebRequest.Create(url)
            req.Method  = "GET"
            req.Timeout = timeout_ms
            resp = req.GetResponse()
            try:
                reader = StreamReader(resp.GetResponseStream(), _NetEncoding.UTF8)
                try:
                    return reader.ReadToEnd()
                finally:
                    reader.Close()
            finally:
                resp.Close()
        if _HAS_URLLIB:
            resp = urlopen(url, timeout=4)
            raw = resp.read()
            return raw.decode("utf-8") if isinstance(raw, bytes) else raw
    except Exception:
        return None


# ─── Streaming (Server-Sent Events) backend ────────────────────────────────────

def http_post_stream(url, payload, headers=None, on_line=None, timeout_ms=120000):
    """
    POST a JSON payload and stream the response back line-by-line.

    Each decoded text line of the response is passed to on_line(line) as it
    arrives. Used for SSE endpoints (request payloads carry "stream": true).

    Args:
        url: target URL string.
        payload: dict to serialise as JSON.
        headers: optional dict of extra request headers.
        on_line: callable(str) invoked once per response line.
        timeout_ms: socket timeout in milliseconds.

    Returns:
        bool: True when the stream completes. Raises on transport failure so the
        caller can fall back to a blocking request.
    """
    body = json.dumps(payload, ensure_ascii=False)

    if _USE_NET:
        # .NET HttpWebRequest streams the response without buffering it whole.
        from System.Net import HttpWebRequest
        from System.IO import StreamReader

        req = HttpWebRequest.Create(url)
        req.Method          = "POST"
        req.ContentType     = "application/json; charset=utf-8"
        req.Timeout         = timeout_ms
        req.ReadWriteTimeout = timeout_ms
        if headers:
            for k, v in headers.items():
                # Content-Type is set via the property above; everything else
                # (x-api-key, Authorization, anthropic-version, …) is unrestricted.
                req.Headers.Add(k, v)

        data = _NetEncoding.UTF8.GetBytes(body)
        req.ContentLength = data.Length
        rs = req.GetRequestStream()
        try:
            rs.Write(data, 0, data.Length)
        finally:
            rs.Close()

        try:
            resp = req.GetResponse()
        except Exception as ex:
            _raise_with_detail(ex)
        try:
            reader = StreamReader(resp.GetResponseStream(), _NetEncoding.UTF8)
            try:
                while True:
                    line = reader.ReadLine()
                    if line is None:
                        break
                    if on_line is not None:
                        on_line(line)
            finally:
                reader.Close()
        finally:
            resp.Close()
        return True

    if _HAS_URLLIB:
        body_bytes = body.encode("utf-8") if isinstance(body, type(u"")) else body
        req_headers = {"Content-Type": "application/json; charset=utf-8"}
        if headers:
            req_headers.update(headers)
        req = Request(url, body_bytes, req_headers)
        try:
            resp = urlopen(req, timeout=120)
        except Exception as ex:
            _raise_with_detail(ex)
        for raw_line in resp:
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if on_line is not None:
                on_line(line.rstrip("\n"))
        return True

    raise RuntimeError("No HTTP client available")


def parse_anthropic_stream_line(line):
    """Return the text delta carried by one Anthropic SSE line, or None."""
    if not line:
        return None
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        obj = json.loads(data)
    except Exception:
        return None
    if obj.get("type") == "content_block_delta":
        delta = obj.get("delta", {}) or {}
        if delta.get("type") in ("text_delta", None):
            return delta.get("text")
    return None


def parse_openai_stream_line(line):
    """Return the text delta carried by one OpenAI-format SSE line, or None.

    Shared by OpenAI and DeepSeek (both OpenAI-compatible). Reasoning models
    stream their chain-of-thought under `reasoning_content`, which is ignored —
    only the user-facing `content` delta is surfaced.
    """
    if not line:
        return None
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        obj = json.loads(data)
    except Exception:
        return None
    try:
        choices = obj.get("choices") or []
        if not choices:
            return None
        delta = choices[0].get("delta") or {}
        return delta.get("content")
    except Exception:
        return None


# ─── Native tool calling (OpenAI wire format — shared by OpenAI/DeepSeek) ──────

# ─── Local reasoning-model sampling ────────────────────────────────────────────
# Substrings that mark a hybrid/reasoning local model (Qwen3, QwQ, DeepSeek-R1,
# Magistral, etc.). These models are trained with sampled decoding and DEGRADE
# under greedy (temperature 0): Qwen's own guidance is explicit that greedy
# decoding in thinking mode causes endless repetition and quality drops. The
# whole codebase historically pinned temperature 0.0 for determinism of tool
# JSON — correct for instruct models, actively harmful for these.
_REASONING_MODEL_HINTS = (
    "qwen3", "qwq", "deepseek-r1", "-r1", "r1-", "magistral",
    "reasoning", "thinker", "marco-o1", "openthinker", "phi-4-reasoning",
)


def is_reasoning_model(model_name):
    """True when the model name looks like a hybrid/reasoning local model."""
    if not model_name:
        return False
    low = u"{}".format(model_name).lower()
    return any(h in low for h in _REASONING_MODEL_HINTS)


def local_sampling_params(model_name):
    """Recommended sampling options for a local model, by family.

    Reasoning models (Qwen3 thinking, DeepSeek-R1, ...) get the vendor-
    recommended non-greedy profile (temp 0.6 / top_p 0.95 / top_k 20 / min_p 0)
    so thinking mode doesn't collapse into repetition. Plain instruct models
    keep the deterministic low-temperature profile that makes tool-call JSON
    stable. Returns a dict of raw option names (temperature/top_p/top_k/min_p)
    — each provider maps them onto its own payload shape.
    """
    if is_reasoning_model(model_name):
        return {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0}
    return {"temperature": 0.0, "top_p": 0.9}


def openai_chat_agent(url, headers, model, system_prompt, messages, tools,
                      max_tokens=1500, timeout_ms=180000, extra_payload=None):
    """One blocking agentic turn against an OpenAI-compatible /chat/completions.

    `messages` must be OpenAI-native (may contain assistant tool_calls and
    role:"tool" results from earlier iterations) and already end with the
    latest user / tool turn. Raises on transport failure — callers wrap.

    Returns the uniform chat_agent dict:
        {"text", "tool_calls":[{"id","name","args"}], "assistant_msg", "stop_reason"}
    """
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(list(messages or []))

    payload = {"model": model, "messages": msgs, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
    # extra_payload carries provider-specific knobs (sampling for local
    # reasoning models, tool_choice, ...) without changing OpenAI's defaults.
    if extra_payload:
        payload.update(extra_payload)

    resp_text = http_post(url, payload, headers, timeout_ms=timeout_ms)
    data = json.loads(resp_text)
    msg  = (data.get("choices") or [{}])[0].get("message", {}) or {}

    text = msg.get("content") or u""
    # Reasoning models may in-line their chain of thought — never show it.
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()

    raw_calls  = msg.get("tool_calls") or []
    tool_calls = []
    for c in raw_calls:
        fn = c.get("function", {}) or {}
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):          # some servers send an object
            args = raw_args
        else:
            try:
                args = json.loads(raw_args) if raw_args else {}
            except Exception:
                args = {}
        tool_calls.append({"id": c.get("id", ""),
                           "name": fn.get("name", ""), "args": args})

    assistant_msg = {"role": "assistant", "content": msg.get("content") or None}
    if raw_calls:
        assistant_msg["tool_calls"] = raw_calls

    return {
        "text":          text,
        "tool_calls":    tool_calls,
        "assistant_msg": assistant_msg,
        "stop_reason":   "tool_use" if tool_calls else "end_turn",
    }


def openai_chat_agent_stream(url, headers, model, system_prompt, messages, tools,
                             max_tokens=1500, timeout_ms=180000, on_delta=None,
                             extra_payload=None):
    """Streaming variant of openai_chat_agent for OpenAI-compatible servers.

    Streams visible text through on_delta as it arrives AND accumulates the
    tool_call deltas (which arrive fragmented by index, with `arguments`
    streamed as partial JSON strings) into whole calls. Returns the same
    uniform chat_agent dict. Raises on transport failure so callers can fall
    back to the blocking openai_chat_agent.
    """
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(list(messages or []))

    payload = {"model": model, "messages": msgs,
               "max_tokens": max_tokens, "stream": True}
    if tools:
        payload["tools"] = tools
    if extra_payload:
        payload.update(extra_payload)

    state = {"text": [], "tool": {}, "order": [], "finish": None}

    def _on_line(line):
        if not line:
            return
        line = line.strip()
        if not line.startswith("data:"):
            return
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except Exception:
            return
        choices = obj.get("choices") or []
        if not choices:
            return
        ch    = choices[0]
        delta = ch.get("delta") or {}

        c = delta.get("content")
        if c:
            state["text"].append(c)
            if on_delta:
                try:
                    on_delta(c)
                except Exception:
                    pass

        for tc in (delta.get("tool_calls") or []):
            idx  = tc.get("index", 0)
            slot = state["tool"].get(idx)
            if slot is None:
                slot = {"id": tc.get("id", ""), "name": u"", "args": []}
                state["tool"][idx] = slot
                state["order"].append(idx)
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"].append(fn["arguments"])

        if ch.get("finish_reason"):
            state["finish"] = ch["finish_reason"]

    http_post_stream(url, payload, headers, _on_line, timeout_ms=timeout_ms)

    raw_text = u"".join(state["text"])
    text     = re.sub(r"<think>[\s\S]*?</think>", "", raw_text).strip()

    tool_calls = []
    raw_calls  = []
    for idx in state["order"]:
        slot     = state["tool"][idx]
        args_str = u"".join(slot["args"])
        try:
            args = json.loads(args_str) if args_str.strip() else {}
        except Exception:
            args = {}
        tool_calls.append({"id": slot["id"], "name": slot["name"], "args": args})
        raw_calls.append({"id": slot["id"], "type": "function",
                          "function": {"name": slot["name"],
                                       "arguments": args_str or "{}"}})

    assistant_msg = {"role": "assistant", "content": raw_text or None}
    if raw_calls:
        assistant_msg["tool_calls"] = raw_calls

    return {
        "text":          text,
        "tool_calls":    tool_calls,
        "assistant_msg": assistant_msg,
        "stop_reason":   "tool_use" if tool_calls else (state["finish"] or "end_turn"),
    }


def openai_agent_tool_results(tool_calls, result_strs):
    """OpenAI format: one role:"tool" message per call, matched by id.

    `result_strs` are pre-serialized JSON strings (agent_loop truncates them);
    missing entries (cancelled run) are padded to keep the transcript valid.
    """
    out = []
    for i, tc in enumerate(tool_calls):
        res = result_strs[i] if i < len(result_strs) else u'{"cancelled": true}'
        out.append({"role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": res})
    return out


class StreamingJSONExtractor(object):
    """
    Incrementally surface the human-readable `message` value out of a streaming
    JSON response of the form:

        {"intent": "...", "params": {...}, "message": "<reply>"}

    The T3Lab system prompt asks models to answer in JSON, but during streaming
    the user should only ever see the `message` text — never the raw braces.
    Feed the full accumulated raw text on each delta; ``display`` returns the
    best human-readable string so far (partial messages included). If the model
    replies in plain prose instead of JSON, the prose is returned verbatim.
    """

    _ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}

    @staticmethod
    def _strip_fences(text):
        t = text.lstrip()
        if t.startswith("```"):
            nl = t.find("\n")
            if nl != -1:
                t = t[nl + 1:]
            stripped = t.rstrip()
            if stripped.endswith("```"):
                t = stripped[:-3]
        return t

    def display(self, raw):
        if not raw:
            return u""
        t = self._strip_fences(raw)
        head = t.lstrip()

        # Plain prose (model ignored the JSON instruction) → show as-is.
        if not head.startswith("{"):
            return raw.strip()

        key_idx = t.find('"message"')
        if key_idx == -1:
            # JSON object opened but the message field hasn't streamed yet.
            return u""

        colon = t.find(":", key_idx)
        if colon == -1:
            return u""
        q = t.find('"', colon)
        if q == -1:
            return u""

        out = []
        i = q + 1
        n = len(t)
        while i < n:
            c = t[i]
            if c == "\\" and i + 1 < n:
                out.append(self._ESCAPES.get(t[i + 1], t[i + 1]))
                i += 2
                continue
            if c == '"':          # unescaped closing quote → end of message
                break
            out.append(c)
            i += 1
        return u"".join(out)


# ─── Abstract base provider ────────────────────────────────────────────────────

class BaseLLMProvider(object):
    """
    Abstract base class for all LLM provider adapters.

    Subclasses must implement:
        chat(messages, system_prompt, user_content, max_tokens) → str | None
        check_health()                                          → bool
    """

    NAME         = "base"
    DISPLAY_NAME = "Base Provider"

    # True if this provider can handle image content blocks
    SUPPORTS_VISION = False

    def _record_error(self, msg):
        """Remember the most recent failure AND debug-log it.

        chat()/chat_agent() return None on any failure, which the UI can only
        render as a generic "the model didn't respond". The chat window reads
        get_last_error() to show the user the API's real reason instead.
        """
        try:
            self._last_error = u"{}".format(msg)[:300]
        except Exception:
            self._last_error = u"unknown error"
        self._debug_log(msg)

    def get_last_error(self):
        """Most recent failure message, or None. Cleared on each new call."""
        return getattr(self, "_last_error", None)

    # ── Silent-failure guards ─────────────────────────────────────────────────
    # chat()/chat_stream()/chat_agent() all bail out with a bare `return None`
    # when there is no key or no resolvable model. Those two paths are the ONLY
    # ways the providers can fail without touching the network, and because
    # they recorded nothing, the chat window's "the model didn't respond"
    # branch had no reason to show — the user got a generic failure with no
    # Details line and no way to tell "your key is missing" from "the vendor
    # timed out". Route every such bail-out through these instead.

    def _fail(self, reason):
        """Record `reason` as the last error and return None (never raises)."""
        self._record_error(reason)
        return None

    def _fail_no_key(self, where="chat"):
        return self._fail(
            u"{}: no API key saved for {} — add it in Settings → LLMs Setting"
            .format(where, self.DISPLAY_NAME))

    def _fail_no_model(self, where="chat"):
        return self._fail(
            u"{}: no usable model for {} — the live model list came back empty "
            u"(key not verified, no credit, or the /models request was blocked "
            u"by the network/proxy)".format(where, self.DISPLAY_NAME))

    def _fail_no_http(self, where="chat"):
        return self._fail(
            u"{}: HTTP transport unavailable in this engine".format(where))

    def is_configured(self):
        """True when this provider has credentials — WITHOUT any network call.

        Deliberately distinct from check_health(), which for the cloud
        providers does a live GET /models on every call. Behind a corporate
        proxy that probe fails intermittently, and callers that gated the whole
        LLM path on it silently fell back to offline canned replies: the
        assistant told the user to go connect an AI they had already connected.
        Gate on THIS, attempt the real call, and let the API's own error reach
        the user when it genuinely fails.

        Local providers (no API key) override it — for them reachability is the
        only meaningful signal.
        """
        try:
            return bool(self._get_api_key())
        except Exception:
            return False

    def _clear_error(self):
        self._last_error = None

    def _debug_log(self, msg):
        """Best-effort debug log via pyRevit's logger; never raises.

        chat()/check_health() failures here are usually swallowed and
        returned as None/False, which looks identical to "not configured" —
        use this in the except-blocks that wrap the actual network call so a
        real API error (bad key, malformed response, rate limit) leaves a
        trace instead of vanishing silently.
        """
        try:
            from pyrevit import script
            script.get_logger().debug(u"{}: {}".format(self.NAME, msg))
        except Exception:
            pass

    @staticmethod
    def _wants_json(response_format):
        """True when a caller asked for a JSON-only reply.

        Accepts every shape used across the codebase: the OpenAI-style
        {"type": "json_object"} dict that the assistant's tool loop sends, and
        the bare "json" string. Providers that constrain decoding server-side
        (Ollama's `format`) use this so JSON mode is opt-in per call instead of
        forced on paths that need prose.
        """
        if not response_format:
            return False
        if isinstance(response_format, dict):
            return u"json" in u"{}".format(
                response_format.get("type", "")).lower()
        return u"json" in u"{}".format(response_format).lower()

    def chat(self, messages, system_prompt, user_content, max_tokens=400, **kwargs):
        """
        Send a chat request and return the raw response text.

        Args:
            messages (list): prior [{role, content}] dicts — conversation history.
                             Content may be a string or a list of content blocks.
            system_prompt (str): system instruction string.
            user_content (str|list): current user input — plain string OR a list
                                     of Claude-format content blocks (text/image).
            max_tokens (int): maximum tokens in the response.

        Returns:
            str | None: raw response text, or None on failure.
        """
        raise NotImplementedError

    def chat_stream(self, messages, system_prompt, user_content,
                    on_delta=None, max_tokens=400, **kwargs):
        """
        Streaming variant of chat(). Calls on_delta(text_chunk) for each piece of
        text as it arrives and returns the full concatenated response.

        The base implementation has no real token streaming: it performs a normal
        blocking chat() and emits the whole reply as a single delta. Providers
        that support Server-Sent Events override this for true incremental output.

        Returns:
            str | None: full response text, or None on failure.
        """
        text = self.chat(messages, system_prompt, user_content, max_tokens, **kwargs)
        if text and on_delta:
            try:
                on_delta(text)
            except Exception:
                pass
        return text

    def check_health(self):
        """Return True if the provider is reachable and has credentials."""
        return False

    def supports_vision(self):
        return self.SUPPORTS_VISION

    def get_models(self):
        """Return a list of model name strings available for this provider."""
        return []

    def get_active_model(self):
        """Return the model name currently in use, or None."""
        return None

    def pick_fast_model(self):
        """Fastest/cheapest model for tiny utility calls (classification).

        Providers override this from their CACHED live model list; None means
        "no faster option known — use the active model".
        """
        return None

    def set_model(self, model_name):
        """
        Set the model to use for future requests.

        Returns:
            bool: True if the model was accepted.
        """
        return False

    # ── Shared utilities ───────────────────────────────────────────────────────


    @staticmethod
    def blocks_to_text(user_content):
        """
        Flatten a list of Claude-format content blocks to a plain text string.
        Used by providers that do not support vision.
        """
        if isinstance(user_content, list):
            parts = []
            for block in user_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(parts)
        return user_content or ""

    @staticmethod
    def has_image_blocks(user_content):
        """Return True if user_content contains at least one image block."""
        if not isinstance(user_content, list):
            return False
        for block in user_content:
            if isinstance(block, dict) and block.get("type") == "image":
                return True
        return False
