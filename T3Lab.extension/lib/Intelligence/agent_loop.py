# -*- coding: utf-8 -*-
"""
Agent Loop

Provider-agnostic agentic tool-calling loop for the T3Lab Assistant.

Runs on a BACKGROUND thread. Providers that set SUPPORTS_NATIVE_TOOLS expose:

    chat_agent(system_prompt, messages, tools, on_delta=None, max_tokens=...)
        -> {"text":        unicode,          # user-visible text ("" if none)
            "tool_calls":  [{"id","name","args"}],
            "assistant_msg": <provider-native message to append to transcript>,
            "stop_reason": str}
        or None on transport/parse failure.

    agent_tool_results(tool_calls, results)
        -> list of provider-native messages carrying the tool results.

The loop itself never touches WPF or the Revit API directly:
- UI feedback flows through the `callbacks` dict (caller marshals to UI thread);
- tool execution goes through the injected `execute_tool(name, args)` callable
  (script.py passes core.server's _execute_tool, which already marshals write
  tools onto Revit's main thread via ExternalEvent).

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Agent Loop"

import json
import re
import threading
import time

from core import jsonsafe
from Intelligence.tool_schema import (LAUNCHER_TOOL_NAME, MEMORY_TOOL_NAME,
                                      is_file_output_only, is_model_modifying)


# ─── Result truncation ─────────────────────────────────────────────────────────
# Tool results are fed back to the model; huge payloads (element dumps) blow the
# context. Keep the JSON fed to the model bounded — the UI card shows the full
# result separately.
_MAX_RESULT_CHARS = 4000


def _json_safe(obj):
    """Recursively coerce byte strings to unicode. Never raises.

    A Revit/OS string that arrives as Windows code-page bytes is the one
    input json's encoder cannot handle — it decodes byte strings as UTF-8 —
    and under IronPython 2.7 the `u"{}".format(...)` fallback below fails on
    exactly the same bytes, so the guard guarded nothing.

    Delegates to core.jsonsafe so this rule has ONE implementation: a private
    second copy is how the MCP server path ended up without it, and the miss
    took down tools/list and every non-ASCII view name.
    """
    return jsonsafe.coerce(obj)


def _result_to_json(result):
    try:
        s = json.dumps(_json_safe(result), ensure_ascii=False)
    except Exception:
        try:
            s = u"{}".format(_json_safe(result))
        except Exception:
            s = u"<unserializable {}>".format(type(result).__name__)
    if len(s) > _MAX_RESULT_CHARS:
        s = s[:_MAX_RESULT_CHARS] + u"... [truncated {} chars]".format(len(s) - _MAX_RESULT_CHARS)
    return s


# ─── Parameter-error self-correction ───────────────────────────────────────────
# A small local model routinely calls a real tool with a wrong or missing
# argument, gets a terse error back, and repeats the same call. Attaching the
# tool's expected schema TO the error result gives it what it needs to fix the
# call on the next turn. Bounded per run so a pathological loop can't balloon the
# context, and each tool is hinted at most once.
_MAX_PARAM_HINTS = 3


def _param_hint(tools, name):
    """Compact 'expected parameters' line for `name` from the tool list, or u''.

    Handles both shapes in play: Anthropic (`input_schema`) and OpenAI/Ollama
    (`function.parameters`). Names each property with required/optional and any
    enum, so a model that guessed the arguments can correct them.
    """
    schema = None
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("name") == name and isinstance(t.get("input_schema"), dict):
            schema = t["input_schema"]
            break
        fn = t.get("function")
        if isinstance(fn, dict) and fn.get("name") == name \
                and isinstance(fn.get("parameters"), dict):
            schema = fn["parameters"]
            break
    if not isinstance(schema, dict):
        return u""
    props = schema.get("properties") or {}
    if not isinstance(props, dict) or not props:
        return u""
    required = set(schema.get("required") or [])
    parts = []
    for pname in list(props.keys())[:12]:
        pspec = props[pname] if isinstance(props[pname], dict) else {}
        bits = [u"{}".format(pspec.get("type") or u"any")]
        if pspec.get("enum"):
            vals = u"|".join(u"{}".format(v) for v in list(pspec["enum"])[:8])
            bits.append(u"enum: " + vals)
        parts.append(u"{} ({}, {})".format(
            pname, u"required" if pname in required else u"optional",
            u", ".join(bits)))
    return u"Expected parameters for `{}`: {}".format(name, u"; ".join(parts))


# ─── Phantom tool calls ────────────────────────────────────────────────────────
# A model that writes {"name": "...", "parameters": {...}} as chat text is
# trying to call a tool. When the name is real the loop executes it
# (_text_tool_call → known); when it isn't, the blob is a dead end that must
# never be shown as the answer — the model gets one correction per retry.
_MAX_PHANTOM_RETRIES = 2

# ─── Announced-but-not-performed turns ────────────────────────────────────────
# A model that replies "Đang liệt kê các mức trong dự án…" and calls nothing
# has not answered — it has narrated. The loop used to accept any text-only
# reply as final, so "list levels" ended on that sentence and no level ever
# reached the user. Worse than the missing answer: on a data question, prose
# produced with zero read tools is ungrounded by construction.
_MAX_ANNOUNCE_NUDGES = 1

_ANNOUNCE_PATTERNS = (
    # Vietnamese
    'dang liet ke', 'dang lay', 'dang kiem tra', 'dang tim', 'dang tai',
    'dang truy xuat', 'dang thuc hien', 'dang tien hanh', 'dang xu ly',
    'dang chay', 'toi se', 'minh se', 'de toi', 'de minh', 'se tien hanh',
    'cho chut', 'cho mot chut', 'vui long doi', 'doi mot lat',
    # English
    'let me', "i'll ", 'i will ', 'i am going to', "i'm going to",
    'i am now', "i'm now", 'going to check', 'one moment', 'hold on',
    'give me a moment', 'let us ', "let's ",
)

_ANNOUNCE_FIXUP = (
    u"You described what you were about to do but called no tool, so nothing "
    u"happened and the user received no data. Do not narrate — act. Call the "
    u"tool that answers the request NOW, then report its real result. If no "
    u"available tool can do it, say plainly that you cannot and why."
)


def _announces_work(text):
    """True when `text` promises an action instead of delivering one.

    Deliberately narrow: a question is a clarification (legitimate), and a
    long reply is an answer. Only a short, forward-looking sentence counts.
    """
    if not text:
        return False
    body = u"{}".format(text).strip()
    if len(body) > 220 or body.endswith(u'?'):
        return False
    from Intelligence.knowledge import vi_text
    folded = vi_text.fold_diacritics(body).lower()
    for p in _ANNOUNCE_PATTERNS:
        if p in folded:
            return True
    # "Đang liệt kê các mức trong dự án..." — a trailing ellipsis on a short
    # line is the same promise without any of the phrasings above.
    return body.endswith(u'...') or body.endswith(u'…')


# ── "Taught the user to do it by hand" guard ─────────────────────────────────
# _announces_work only catches the SHORT forward-looking promise ("Đang xử
# lý..."); it returns False above 220 characters. The commonest real failure is
# the opposite shape: a long, confident tutorial telling the user which Revit
# buttons to click — for "tô đỏ tường" the model produced a numbered walkthrough
# of a "Bulk Set Parameter" dialog that does not exist. To the user that is a
# refusal with extra steps: they asked the assistant to DO the work.
_HOWTO_MARKERS = (
    # Vietnamese (diacritics folded before matching)
    'huong dan', 'cac buoc', 'buoc 1', 'tung buoc', 'ban co the su dung',
    'ban hay', 'nhap chuot', 'nhan chuot', 'chon cong cu', 'thanh cong cu',
    'hop thoai', 'tren ribbon', 'menu ngu canh', 'lam theo cac buoc',
    # English
    'here are the steps', 'follow these steps', 'step 1', 'step-by-step',
    'you can use the', 'click on the', 'right-click', 'in the ribbon',
    'dialog box', 'from the menu', 'select the tool',
)

# The user ASKED for instructions — then a walkthrough is the right answer.
_ASKS_HOWTO = (
    'lam the nao', 'lam sao', 'cach ', 'huong dan toi', 'chi toi cach',
    'how do i', 'how to', 'how can i', 'what are the steps', 'explain how',
)

_HOWTO_FIXUP = (
    u"You explained how the USER could do it by hand in Revit's interface. "
    u"That is a refusal — they asked YOU to do it, and you have tools that "
    u"perform this directly on the live model. Never describe ribbon buttons, "
    u"dialogs or click sequences. Call the tool that performs the request NOW "
    u"and report the real result. If genuinely no tool can do it, say that in "
    u"one sentence and stop — do not substitute a tutorial."
)


def _asks_for_instructions(text):
    """True when the USER asked how to do something themselves."""
    if not text:
        return False
    from Intelligence.knowledge import vi_text
    folded = vi_text.fold_diacritics(u"{}".format(text)).lower()
    return any(p in folded for p in _ASKS_HOWTO)


def _instructs_instead_of_acting(text, user_text=None):
    """True when the reply is a do-it-yourself walkthrough.

    Length is NOT a disqualifier here — that is exactly what distinguishes this
    from _announces_work. Two independent signals are required so a passing
    mention ("click Reload in pyRevit") cannot trip it: a how-to marker AND
    either a numbered list or a second marker.
    """
    if not text or _asks_for_instructions(user_text):
        return False
    from Intelligence.knowledge import vi_text
    body = u"{}".format(text)
    folded = vi_text.fold_diacritics(body).lower()
    hits = sum(1 for p in _HOWTO_MARKERS if p in folded)
    if not hits:
        return False
    numbered = len(re.findall(r'(?m)^\s*\d+[\.\)]\s', body)) >= 2
    return hits >= 2 or numbered


_PHANTOM_FIXUP = (
    u"`{}` is not a tool, and JSON written as chat text is never executed. "
    u"An Active skill in your system prompt is a set of INSTRUCTIONS for YOU "
    u"to carry out — \"playbook\", \"skill\" and \"apply_playbook\" are not "
    u"tool names. Do the work NOW: use a real tool call from the tools list "
    u"for anything that reads or edits the model, and plain prose for "
    u"everything else. Do not output JSON as text again."
)


def _find_json_blob(text):
    """(obj, start, end) for the outermost {...} in `text`, else None.

    Fenced blocks are skipped on purpose: ```json ...``` is the model SHOWING
    json because the user asked to see it, not miscalling a tool.
    """
    if not text or u"{" not in text or u"```" in text:
        return None
    try:
        import re
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            return None
        return json.loads(m.group()), m.start(), m.end()
    except Exception:
        return None


def _tool_call_shape(obj):
    """(name, args) if `obj` is a tool call written as JSON, else None.

    Accepts the Anthropic ({"name","parameters"|"input"}), OpenAI
    ({"function": {"name","arguments"}}) and legacy T3Lab
    ({"intent","params"}) spellings — a model that falls out of native
    tool-calling reproduces whichever one it was trained on.
    """
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function")
    if isinstance(fn, dict):
        obj = fn
    name = obj.get("name") or obj.get("tool") or obj.get("intent")
    args = (obj.get("parameters") or obj.get("arguments")
            or obj.get("params") or obj.get("input") or {})
    if not name or not isinstance(args, dict):
        return None
    try:
        name = name.strip()
    except Exception:
        return None          # name wasn't a string
    return (name, args) if name else None


# Keys a JSON tool call may carry. Anything else in the object means the model
# is returning DATA that merely happens to have a "name" field (a level, a
# family type...), which must never be mistaken for a call.
_CALL_KEYS = frozenset((
    "name", "tool", "intent", "function", "type", "id",
    "parameters", "arguments", "params", "input",
    "message", "thought", "reasoning", "explanation",
))


def _only_call_keys(obj):
    try:
        return not (set(obj.keys()) - _CALL_KEYS)
    except Exception:
        return False


def is_bare_tool_call_text(content):
    """True when `content` is nothing but a tool call written as JSON text —
    no prose around it, e.g.

        {"name": "apply_playbook", "parameters": {"playbook_name": "lod-standard"}}

    AgentLoop suppresses these before they reach the chat now, but saved
    transcripts written before that fix still carry them. Used by the
    Assistant's history loader to prune them: replayed into the model's
    context they are a worked example of answering with a fake tool call,
    which is exactly the behaviour being fixed.
    """
    if not content:
        return False
    try:
        stripped = content.strip()
    except Exception:
        return False
    if not (stripped.startswith(u"{") and stripped.endswith(u"}")):
        return False
    found = _find_json_blob(stripped)
    if not found:
        return False
    return bool(_tool_call_shape(found[0])) and _only_call_keys(found[0])


def _call_key(name, args):
    """Canonical identity of one tool call, for the duplicate guard.

    Shared by the guard itself and by the read-batching prefetch so the two
    can never disagree about what counts as "already done".
    """
    try:
        return (name, json.dumps(_json_safe(args), sort_keys=True,
                                 ensure_ascii=False))
    except Exception:
        try:
            return (name, u"{}".format(_json_safe(args)))
        except Exception:
            return (name, u"<unkeyable>")


def _sanitize_history(history, limit=None):
    """Reduce persisted chat history to plain-text user/assistant messages.

    The window size comes from Intelligence.conversation.HISTORY_LIMIT. It was
    hardcoded to 24 here while script._add_to_history truncated to 16, so this
    limit never actually bound and the extra continuity it claimed to give
    could not happen. One constant now, and turns that fall past it are folded
    into a rolling summary rather than dropped.

    It stays affordable: Claude re-reads the transcript prefix from the prompt
    cache, and Ollama's num_ctx is sized to fit the actual payload per request.
    """
    if limit is None:
        try:
            from Intelligence.conversation import HISTORY_LIMIT
            limit = HISTORY_LIMIT
        except Exception:
            limit = 24
    out = []
    for h in (history or [])[-limit:]:
        role    = h.get("role", "")
        content = h.get("content", "")
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            content = u"\n".join(parts)
        if content:
            out.append({"role": role, "content": content})
    # Provider APIs reject a transcript that starts with an assistant turn.
    while out and out[0]["role"] == "assistant":
        out.pop(0)
    return out


# ─── Tool gate: who has to approve a call before it runs ──────────────────────
# ONE policy for every path that executes a tool on the user's behalf — the
# native AgentLoop callback and the legacy JSON-intent loop in the Assistant
# pane both ask the same ToolGate. They used to disagree: the native path
# confirmed destructive tools and forced the first purge to a dry run, while
# the legacy path ran every call straight through srv._execute_tool, and the
# "Ask before edits" switch was only a sentence in the system prompt that
# nothing in code enforced.
#
# The gate only DECIDES. Showing the approval card and blocking on it is the
# window's job (it needs WPF); this half is pure so it can be tested.

GATE_RUN         = 'run'           # execute now, nobody is asked
GATE_DESTRUCTIVE = 'destructive'   # always confirm, in every action mode
GATE_EDIT        = 'edit'          # "Ask before edits" is on: confirm first

ACTION_MODES = ('auto', 'confirm')

# Calls that only REPORT although the tool itself can write. A purge dry run
# lists what it would delete and touches nothing, so "Ask before edits" has
# nothing to ask about — and the first purge of every request is forced into
# exactly that dry run below.
_REPORT_ONLY_CALLS = {
    'purge_unused': ('dry_run', True),
}


def _is_report_only(name, args):
    rule = _REPORT_ONLY_CALLS.get(name)
    if not rule:
        return False
    key, default = rule
    try:
        return bool((args or {}).get(key, default))
    except Exception:
        return False


class ToolGate(object):
    """Pre-execution policy for ONE user request.

    action_mode:       'auto' | 'confirm' (settings agents.action_mode).
    is_destructive:    callable(name, args) -> bool — core.server's
                       T3LabAIServer.is_destructive, where the dangerous
                       tools are declared next to their definitions.
    is_modifying:      callable(name) -> bool. Defaults to
                       tool_schema.is_model_modifying (unknown names count as
                       modifying: the unclassified tool is the surprising one).
    preapproved_edits: True while an approved multi-step plan runs — the user
                       already said yes to its edits on the plan card, so
                       "Ask before edits" does not ask again per call.
                       Destructive calls still confirm one by one.

    Fails CLOSED: a classifier that raises turns the call into a
    confirmation, never into a silent run.
    """

    def __init__(self, action_mode='auto', is_destructive=None,
                 is_modifying=None, preapproved_edits=False):
        self.action_mode = (action_mode if action_mode in ACTION_MODES
                            else 'auto')
        self._is_destructive = is_destructive
        self._is_modifying = is_modifying or is_model_modifying
        self.preapproved_edits = bool(preapproved_edits)
        self._purge_seen = False

    def verdict(self, name, args=None):
        """GATE_RUN | GATE_DESTRUCTIVE | GATE_EDIT for one call. No side
        effects — safe to ask about a call that will never run."""
        if not name or name in (LAUNCHER_TOOL_NAME, MEMORY_TOOL_NAME):
            # Local pseudo-tools: the launcher opens a window AFTER the turn,
            # the memory tool writes a fact file. Neither touches the model.
            return GATE_RUN
        args = args or {}
        if self._is_destructive is not None:
            try:
                if self._is_destructive(name, args):
                    return GATE_DESTRUCTIVE
            except Exception:
                return GATE_DESTRUCTIVE
        if (self.action_mode == 'confirm' and not self.preapproved_edits
                and self.modifies(name, args)
                and not is_file_output_only(name)):
            # Exports only write a new file — "Ask before edits" is about the
            # model, so they run (tool_schema.FILE_OUTPUT_TOOL_NAMES).
            return GATE_EDIT
        return GATE_RUN

    def modifies(self, name, args=None):
        """True when this call can change the model or write a file
        (whatever the action mode says about asking first)."""
        if not name or name in (LAUNCHER_TOOL_NAME, MEMORY_TOOL_NAME):
            return False
        if _is_report_only(name, args or {}):
            return False
        try:
            return bool(self._is_modifying(name))
        except Exception:
            return True

    def prepare(self, name, args):
        """(args, verdict) for a call that is ABOUT to run.

        The first purge_unused of a request is always rewritten to a dry run
        — the model reports what would go, the user decides, and only a
        second, explicit call can delete (and that one is destructive, so it
        confirms).
        """
        args = dict(args or {})
        if name == 'purge_unused' and not self._purge_seen:
            self._purge_seen = True
            if not bool(args.get('dry_run', True)):
                args['dry_run'] = True
        return args, self.verdict(name, args)


def tool_def_name(tool):
    """Name of one provider-native tool definition (Anthropic `{"name"}`,
    OpenAI-style `{"function": {"name"}}`), or u''."""
    try:
        return (tool.get("name")
                or (tool.get("function") or {}).get("name") or u"")
    except Exception:
        return u""


def read_only_tools(tools, is_modifying=None):
    """`tools` minus every definition that can change the model or write a
    file. For background read tasks, which have no approval card to stop at:
    a tool they cannot see is a tool they cannot call."""
    is_modifying = is_modifying or is_model_modifying
    out = []
    for t in tools or []:
        name = tool_def_name(t)
        try:
            if name and not is_modifying(name):
                out.append(t)
        except Exception:
            pass
    return out


def declined_result(name):
    """The tool result handed back to the model when the user said no."""
    return {"cancelled": True,
            "note": u"User declined the '{}' action. Do not retry it; say "
                    u"what was skipped and ask what to change.".format(name)}


# ─── One answer from the UI thread to a waiting worker ────────────────────────

class PendingDecision(object):
    """A one-shot decision the UI thread hands to a blocked worker thread.

    The approval cards (plan card, confirm card) block the routing worker
    while the user reads them. FIRST decision wins: a Run click that lands a
    moment after Stop must not resurrect a request the user already
    cancelled, and a timeout must not be overturned by a late click.
    """

    STOPPED = 'stopped'
    EXPIRED = 'expired'

    def __init__(self):
        self._evt   = threading.Event()
        self._lock  = threading.Lock()
        self._value = None

    def decide(self, value):
        """Record `value` unless something was decided already. Returns True
        when THIS call made the decision."""
        with self._lock:
            if self._evt.is_set():
                return False
            self._value = value
            self._evt.set()
            return True

    @property
    def value(self):
        return self._value

    def is_decided(self):
        return self._evt.is_set()

    def wait(self, timeout_sec, should_abort=None, poll=0.25):
        """Block until decided, aborted (STOPPED) or timed out (EXPIRED).

        `should_abort` is polled between waits — the window passes its Stop
        flag, so pressing Stop releases a card that is still on screen.
        Returns the decided value.
        """
        deadline = time.time() + max(0.0, float(timeout_sec or 0))
        while not self._evt.is_set():
            if should_abort is not None:
                try:
                    if should_abort():
                        self.decide(self.STOPPED)
                        break
                except Exception:
                    pass
            remaining = deadline - time.time()
            if remaining <= 0:
                self.decide(self.EXPIRED)
                break
            self._evt.wait(min(poll, remaining))
        return self._value


class AgentLoop(object):
    """One user request = one AgentLoop.run(). Cancellable between steps."""

    def __init__(self, provider, execute_tool, tools, callbacks=None,
                 max_iterations=10, max_tokens=1500, time_budget_sec=240,
                 turn_timer=None, execute_tools_batch=None,
                 is_write_tool=None):
        self._provider       = provider
        self._execute_tool   = execute_tool
        self._tools          = tools
        # Optional read-batching seam. Both must be supplied for it to engage;
        # without them every call goes through execute_tool one at a time,
        # exactly as before.
        self._execute_batch  = execute_tools_batch
        self._is_write_tool  = is_write_tool
        self._cb             = callbacks or {}
        self._max_iterations = max_iterations
        self._max_tokens     = max_tokens
        self._time_budget    = time_budget_sec
        self._cancelled      = False
        # Optional Intelligence.telemetry.TurnTimer. None = no accounting;
        # every use below is guarded so the loop runs identically without it.
        self._timer          = turn_timer

    # ── Cancellation ──────────────────────────────────────────────────────────

    def cancel(self):
        """Request a stop. The current model turn / tool finishes, then the
        loop ends — a Transaction mid-flight cannot be safely aborted."""
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled

    def _guard_tripped(self):
        """True when the optional guard_check callback reports the request
        context is no longer valid (e.g. the user switched Revit documents
        mid-request — writing on would edit the wrong model)."""
        guard = self._cb.get("guard_check")
        if guard is None:
            return False
        try:
            return bool(guard())
        except Exception:
            return False

    # ── Callback helpers (never raise into the loop) ──────────────────────────

    def _emit(self, name, *args):
        fn = self._cb.get(name)
        if fn:
            try:
                fn(*args)
            except Exception:
                pass

    # ── Telemetry (optional, never raises into the loop) ──────────────────────

    def _note_usage(self, usage):
        """Accumulate one model call's token usage onto the turn timer."""
        if self._timer is None or not usage:
            return
        try:
            self._timer.add_usage(usage)
        except Exception:
            pass

    def _note_roundtrip(self, n_calls=1):
        """Record one crossing to Revit's main thread carrying n tool calls."""
        if self._timer is None:
            return
        try:
            self._timer.tool_roundtrip(n_calls)
        except Exception:
            pass

    # ── Read batching ─────────────────────────────────────────────────────────

    def leading_read_run(self, calls, done_calls=None):
        """Indices of the LEADING run of batchable read-only calls.

        Stops at the first call that is not a plain read — a write, the
        terminal launcher, the locally-executed memory tool, or a duplicate
        the loop will refuse anyway. Stopping there is what preserves
        read-after-write ordering: a read placed after a write must see the
        model as the write left it, so it can never be hoisted into the batch.

        A run of one is not worth a batch; the caller falls back to the
        sequential path.
        """
        if self._execute_batch is None or self._is_write_tool is None:
            return []
        done = done_calls or set()
        idxs = []
        for i, tc in enumerate(calls or []):
            name = tc.get("name", "")
            if not name or name in (LAUNCHER_TOOL_NAME, MEMORY_TOOL_NAME):
                break
            try:
                if self._is_write_tool(name):
                    break
            except Exception:
                break
            if _call_key(name, tc.get("args") or {}) in done:
                break
            idxs.append(i)
        return idxs if len(idxs) > 1 else []

    def _prefetch_reads(self, calls, done_calls):
        """Execute the leading read run in ONE Revit round-trip.

        Returns {index: result}. Results are handed back to the normal
        sequential loop below, which still emits on_tool_start/on_tool_done
        per call and still applies its guards — only the crossing is shared,
        so the UI and the transcript are indistinguishable from before.

        Any failure falls back silently to per-call execution.
        """
        idxs = self.leading_read_run(calls, done_calls)
        if not idxs:
            return {}
        batch = [(calls[i].get("name", ""), calls[i].get("args") or {})
                 for i in idxs]
        try:
            results = self._execute_batch(batch)
        except Exception:
            return {}
        if not isinstance(results, list) or len(results) != len(idxs):
            return {}
        self._note_roundtrip(len(idxs))
        return dict(zip(idxs, results))

    # ── Text tool-call rescue ─────────────────────────────────────────────────

    def _registered_tool_names(self):
        """Tool names from the provider-native schema list (Anthropic uses
        {"name": ...}, OpenAI-style wraps it in {"function": {"name": ...}})."""
        names = set()
        for t in (self._tools or []):
            try:
                n = t.get("name") or (t.get("function") or {}).get("name")
                if n:
                    names.add(n)
            except Exception:
                pass
        return names

    def _text_tool_call(self, text):
        """A tool call the model wrote as chat text instead of a native
        tool_call block. Local models drift into JSON-in-prose like
        {"name": "get_material_quantities", "parameters": {...}} after
        seeing a tool error; hosted ones do it when a /slash skill puts a
        playbook in the system prompt and they try to "call" the playbook.

        Returns {"name", "args", "prose", "known"} or None. `prose` is the
        message with the blob removed — the blob itself is never something
        the user should read, whether or not the tool turns out to be real.
        `known` says whether the name is a registered tool: True → execute
        it, False → phantom, correct the model instead.
        """
        found = _find_json_blob(text)
        if not found:
            return None
        obj, start, end = found
        shape = _tool_call_shape(obj)
        if not shape:
            return None
        name, args = shape
        known = name in self._registered_tool_names()
        # An unregistered name is weak evidence on its own, so require the
        # object to carry ONLY tool-call keys before suppressing it.
        if not known and not _only_call_keys(obj):
            return None
        return {"name": name, "args": args, "known": known,
                "prose": (text[:start] + u" " + text[end:]).strip()}

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self, history, system_prompt, user_content):
        """Execute the agentic loop. Blocking — call from a worker thread.

        Returns:
            {"status": "done"|"cancelled"|"failed"|"max_iterations"|"timeout"
                       |"doc_changed",
             "text": <last user-visible text>,
             "launch_intent": <str|None>,   # open_t3lab_tool target, terminal
             "iterations": int, "tool_runs": int}
        """
        messages = _sanitize_history(history)
        messages.append({"role": "user", "content": user_content})

        started    = time.time()
        last_text  = u""
        tool_runs  = 0
        iteration  = 0
        done_calls = set()   # (name, canonical args) that succeeded this run
        phantom_retries = 0  # JSON calls to tools that don't exist, corrected
        announce_nudges = 0  # "I'm about to…" replies that called nothing
        hinted = set()       # tools whose schema we already attached to an error

        while iteration < self._max_iterations:
            iteration += 1

            if self._cancelled:
                return self._finish("cancelled", last_text, None, iteration, tool_runs)
            if self._guard_tripped():
                return self._finish("doc_changed", last_text, None, iteration, tool_runs)
            if (time.time() - started) > self._time_budget:
                return self._finish("timeout", last_text, None, iteration, tool_runs)

            # Track whether this turn actually streamed deltas so blocking
            # providers still surface their text through on_text_delta once.
            turn = {"streamed": False}

            def _on_delta(chunk, _turn=turn):
                _turn["streamed"] = True
                self._emit("on_text_delta", chunk)

            try:
                resp = self._provider.chat_agent(
                    system_prompt, messages, self._tools,
                    on_delta=_on_delta, max_tokens=self._max_tokens)
            except Exception:
                resp = None

            if resp is None:
                # First turn failing = provider never answered; later turns
                # failing still leave earlier text/tool work worth reporting.
                status = "failed" if iteration == 1 else "done"
                return self._finish(status, last_text, None, iteration, tool_runs)

            self._note_usage(resp.get("usage"))

            text  = (resp.get("text") or u"").strip()
            calls = resp.get("tool_calls") or []

            # Model wrote the tool call as JSON text instead of a native
            # tool_call block. Either way the blob is not an answer, so it is
            # stripped from what the user sees; a registered name is then
            # executed anyway rather than ending the turn on a dead blob.
            rescued = False
            phantom  = None
            if not calls and text:
                _tc = self._text_tool_call(text)
                if _tc and _tc["known"]:
                    calls   = [{"id": "text_rescue", "name": _tc["name"],
                                "args": _tc["args"]}]
                    rescued = True
                    text    = _tc["prose"]
                elif _tc:
                    phantom = _tc
                    text    = _tc["prose"]

            # Phantom: the name isn't a tool at all (`apply_playbook` is the
            # recurring one — /slash injects a playbook and the model tries
            # to "call" it). Correct the model and let it try again. Bounded,
            # because a model that still does this after two corrections will
            # not stop on the third; falling through then leaves an empty
            # turn, which script.py already routes to the legacy JSON-intent
            # path where a visible reply is guaranteed.
            if phantom is not None and phantom_retries < _MAX_PHANTOM_RETRIES:
                phantom_retries += 1
                if text:
                    last_text = text
                # An open live bubble already streamed the blob — empty text
                # drops that bubble, real text replaces its content.
                self._emit("on_turn_text", text, False)
                messages.append(resp["assistant_msg"])
                messages.append({"role": "user",
                                 "content": _PHANTOM_FIXUP.format(phantom["name"])})
                continue

            # Narrated instead of acted: no tool call anywhere in this run and
            # a reply that only promises one. Nudge once, then let it stand —
            # a model that still narrates after a correction will not stop on
            # the third try, and its text is at least visible.
            if (not calls and tool_runs == 0
                    and announce_nudges < _MAX_ANNOUNCE_NUDGES
                    and _announces_work(text)):
                announce_nudges += 1
                last_text = text
                self._emit("on_turn_text", text, False)
                messages.append(resp["assistant_msg"])
                messages.append({"role": "user", "content": _ANNOUNCE_FIXUP})
                continue

            # Taught the user to click through Revit instead of doing the work.
            # Same budget as the announce nudge and same reasoning: correct it
            # once, then let the text stand rather than loop.
            if (not calls and tool_runs == 0
                    and announce_nudges < _MAX_ANNOUNCE_NUDGES
                    and _instructs_instead_of_acting(text, user_content)):
                announce_nudges += 1
                last_text = text
                self._emit("on_turn_text", text, False)
                messages.append(resp["assistant_msg"])
                messages.append({"role": "user", "content": _HOWTO_FIXUP})
                continue

            if text:
                last_text = text
                if not turn["streamed"]:
                    self._emit("on_text_delta", text)
                self._emit("on_turn_text", text, not calls)
            elif rescued or phantom is not None:
                # The turn was nothing BUT the blob. Drop the live bubble that
                # streamed it instead of leaving the raw JSON on screen.
                self._emit("on_turn_text", u"", False)

            messages.append(resp["assistant_msg"])

            if not calls:
                return self._finish("done", last_text, None, iteration, tool_runs)

            # ── Execute tool calls sequentially (Revit is single-threaded) ──
            # One exception: the LEADING run of read-only calls is fetched in a
            # single crossing to Revit's main thread first (each crossing costs
            # an ExternalEvent raise plus a wait for Revit to go idle, and the
            # model routinely asks for several reads at once). The loop below
            # is otherwise untouched — it still emits a card per call and still
            # applies every guard; it just consumes the prefetched result
            # instead of paying for its own round-trip.
            prefetched    = self._prefetch_reads(calls, done_calls)
            results       = []
            launch_intent = None
            doc_changed   = False
            for _idx, tc in enumerate(calls):
                name = tc.get("name", "")
                args = tc.get("args") or {}

                if self._guard_tripped():
                    doc_changed = True
                    results.append({"cancelled": True,
                                    "note": "Active document changed — request aborted."})
                    break

                if name == LAUNCHER_TOOL_NAME:
                    # Terminal: the window opens on the UI thread AFTER the
                    # loop ends (ShowDialog would otherwise block the loop).
                    launch_intent = args.get("tool_intent") or ""
                    results.append({"success": True,
                                    "note": "T3Lab tool window will open now."})
                    break

                if self._cancelled:
                    results.append({"cancelled": True,
                                    "note": "User stopped the request."})
                    break

                # Deterministic duplicate guard: identical (name, args) that
                # already succeeded in THIS run is never executed again — the
                # model gets an error result telling it to move on, and no
                # tool card ever reaches the UI for the repeat.
                call_key = _call_key(name, args)
                if call_key in done_calls:
                    results.append({
                        "error": u"Duplicate call: `{}` with identical "
                                 u"arguments already succeeded in this "
                                 u"request. Do not repeat completed calls — "
                                 u"continue with the next step or give the "
                                 u"final answer.".format(name)})
                    continue

                self._emit("on_tool_start", name, args, iteration)
                t0 = time.time()
                if _idx in prefetched:
                    # Already fetched in the shared read round-trip above.
                    res = prefetched[_idx]
                else:
                    self._note_roundtrip(1)
                    try:
                        res = self._execute_tool(name, args)
                    except Exception as ex:
                        res = {"error": u"{}".format(ex), "tool": name}
                if not isinstance(res, dict):
                    res = {"result": res}
                dt = time.time() - t0
                ok = "error" not in res
                # Hand a small model the tool's schema alongside the error so it
                # can fix a wrong/missing argument instead of repeating the call.
                if not ok and name not in hinted and len(hinted) < _MAX_PARAM_HINTS:
                    _ph = _param_hint(self._tools, name)
                    if _ph:
                        res["expected_parameters"] = _ph
                        hinted.add(name)
                tool_runs += 1
                self._emit("on_tool_done", name, res, ok, dt)
                results.append(res)
                if ok:
                    done_calls.add(call_key)

            if launch_intent:
                return self._finish("done", last_text, launch_intent,
                                    iteration, tool_runs)
            if doc_changed:
                return self._finish("doc_changed", last_text, None,
                                    iteration, tool_runs)
            if self._cancelled:
                return self._finish("cancelled", last_text, None,
                                    iteration, tool_runs)

            # Feed results back in the provider's native format — serialized
            # and truncated here so no provider ever pushes a huge element
            # dump into the model context.
            try:
                res_strs = [_result_to_json(r) for r in results]
                if rescued:
                    # A rescued call has no native tool_call block in the
                    # transcript, so provider tool-result messages would
                    # reference an unknown id — feed the result back as plain
                    # user text instead (accepted by every provider).
                    messages.append({
                        "role": "user",
                        "content": u"Tool `{}` returned: {}\nContinue the "
                                   u"task. Use REAL tool calls, never JSON "
                                   u"as text.".format(calls[0]["name"],
                                                      res_strs[0])})
                else:
                    messages.extend(
                        self._provider.agent_tool_results(calls, res_strs))
            except Exception:
                return self._finish("failed", last_text, None, iteration, tool_runs)

        return self._finish("max_iterations", last_text, None,
                            self._max_iterations, tool_runs)

    def _finish(self, status, text, launch_intent, iterations, tool_runs):
        if self._timer is not None:
            try:
                self._timer.iterations = iterations
            except Exception:
                pass
        result = {
            "status":        status,
            "text":          text,
            "launch_intent": launch_intent,
            "iterations":    iterations,
            "tool_runs":     tool_runs,
        }
        self._emit("on_finish", result)
        return result


# ─── System prompt (native tool-calling mode) ──────────────────────────────────
# Unlike the legacy JSON-intent prompt, this one carries NO tool schemas (they
# travel through the API `tools` parameter) and does NOT force JSON output.

_AGENT_PROMPT = u"""You are T3Lab Assistant, an AI agent embedded in Autodesk Revit via the T3Lab pyRevit extension. You can read and modify the live Revit model through the tools provided.

## Language & formatting
{language}Keep replies short and practical — one or two sentences between tool calls, a compact summary at the end.
Use markdown when it helps: **bold**, `code`, bullet lists, and pipe tables (| a | b |) for numeric summaries — the chat renders them natively. Do NOT use emoji.

## Units
All tool coordinates and dimensions are in METERS. Convert user input: 5000mm = 5.0, 3m = 3.0. Element ids are integers.

## You DO the work — you never teach it
This is the most important rule. The user is asking you to change their model, not to learn Revit. NEVER answer an action request with instructions, a numbered walkthrough, or the name of a ribbon button, dialog or menu. Do not write "you can use...", "click...", "select the tool...", "here are the steps". You are not a manual; you hold tools that act on the live model, and a tutorial is a refusal dressed up as help. "tô đỏ tường" means CALL revit_override_color, not explain how the user could paint walls themselves. Explain steps ONLY when the user explicitly asked how to do it by hand ("làm thế nào", "how do I"). If no tool can do what was asked, say so in one sentence and stop.

Do not invent safety rules either. Asking "are you sure?" for work that is not on the destructive list in Working rule 3 is the same refusal in a different costume. Graphic overrides (`revit_override_color`, `color_elements`, `operate_element` hide/isolate/halftone), tagging, creating views/sheets/levels/grids and setting parameters are ORDINARY edits: just do them, however many elements they touch. "toàn bộ", "tất cả", "all" describes the SCOPE the user wants — it is not a reason to stop and confirm.

## Plan, then act
For a multi-step request, decide the full tool sequence BEFORE the first call and state it in one short sentence. Chain the steps yourself — never ask the user to run intermediate steps you can do with tools. For a simple request, skip the plan and just act.

## Efficiency (each model turn is expensive — minimize turns)
- BATCH independent read-only calls into ONE turn: when you need e.g. levels + selection + view info, emit all those tool calls together instead of one per turn.
- Prefer ONE bulk tool over many single calls: `bulk_set_parameter` over repeated `set_parameter`, `ai_element_filter` over per-element inspection, `tag_all_*` over per-element tags.
- ONE `ai_element_filter` call, fully narrowed, beats a broad call plus your own sifting. It filters by `category`/`categories`, `group_name`, `workset`, `level_name`, `type_name`, `name_contains` and a parameter — all at once, all server-side. Never pull a whole category down and pick through it yourself: that is slower, costs thousands of tokens, and the list you sift may be a truncated page.
- NEVER FERRY IDS. To act on the elements a filter found, re-call it with `store_as="sel"` and pass `element_ids=["@sel"]` to the acting tool — the whole matched set, uncapped, at the cost of one word. Ids belong in the reply only when the user asked for specific ones.
- COUNTS COME FROM `group_by`, NOT FROM ROWS. "How many…", "thống kê", any breakdown: call `ai_element_filter` with `group_by` (e.g. `["name","level"]`, `["workset"]`) and read the `summary` counts. Never fetch 1000 element rows to count them.
- Trim what you do list: `fields=["id"]` returns a bare id array; add only the fields you will actually use.
- Do not re-query data that is already in this conversation (earlier tool results, the context block in the latest user message) unless the model may have changed since.
- Never call a tool "just to check" after a success result already told you the outcome; verify with a read tool only after LARGE modifications (20+ elements) or when a result looks suspicious.

## Working rules
1. Query before you modify: when element ids or names are unknown, use read tools (get_current_view_elements, ai_element_filter, list_levels, ...) first.
2. After finishing, summarize WHAT changed (counts + element ids) in English.
3. Destructive actions — `delete_element`, `purge_unused`, `edit_elements` ungroup, `manage_view_template` delete, `manage_links` delete, `manage_document` save_as / sync_with_central, and anything else removing or publishing model data: unless the user's current message already explicitly requested it, ask for confirmation in text FIRST and stop — do not call the tool in the same turn. (Revit cannot undo an ungroup by re-grouping, deleting a link takes its tags and dimensions with it, and syncing pushes your work to the whole team.)
4. If a tool returns an error, diagnose from the error text and retry ONCE with corrected arguments; if it fails again, report exactly what is missing — never loop on the same failing call.
5. If the request is ambiguous about WHICH elements to change, ask ONE precise clarifying question instead of guessing — a wrong bulk edit is worse than a question.
6. `open_t3lab_tool` opens a T3Lab window and ENDS your turn — only ever call it last, and never together with other tools. OPENING A WINDOW IS NOT DOING THE WORK: call it only when the user asked to OPEN/see a tool by name ("mở BatchOut", "open View Manager"). When they asked for a RESULT ("create 3d views for each room", "export the A sheets"), do it with the real tools and report what you produced — handing them a window to fill in themselves is a silent refusal dressed up as success. If no tool can do it, say exactly that.
7. When the user refers to the current selection ("these elements", "the selected walls", "các element này", "đang chọn"), call `revit_get_selected_elements` FIRST and operate on those element ids — never guess ids.
8. Trust tool results over assumptions: counts, names and ids come from the model, not from memory of typical projects.
9. Scope resolution when the request names no explicit target: for CHECKING / auditing / statistics / spell-check requests the default scope is the ENTIRE PROJECT — never silently limit to the current view (limit only when the user says so, or ask ONE question when unsure). For MODIFY actions, use the "Current Revit context" block in the latest user message — the user's selection first, then the active view. Never report "nothing found" from one filtered query: re-check with corrected arguments or wider scope first.
10. NUMBERS MUST COME FROM TOOLS, NEVER FROM YOU. Tool results may be truncated before you see them, so counting or summing rows/elements yourself gives wrong answers. Use the exact fields the tools compute: `total_count` (ai_element_filter), `row_count` / `column_totals` (get_schedule_data), `element_counts` (analyze_model_statistics). If a statistic you need has no tool-computed field, say so — do not estimate.
11. Color / highlight requests — "tô màu", "tô đỏ X", "bôi xanh X", "đổi màu X", "highlight X", "color X red": the color word is the OVERRIDE color to APPLY, never a parameter value to filter by (Revit elements have no "Color" or "Fill Color" parameter). For ALL elements of a category ("tô vàng sàn", "color the walls red") call `revit_override_color` with `category` + `color` directly — the server collects every matching element itself, no ids needed and NO count limit. Pass `element_ids` only for a SUBSET: the user's selection (via revit_get_selected_elements), or `["@sel"]` after `ai_element_filter(..., store_as="sel")` — never a hand-copied id list, which is capped at one page. Use `color_elements` only when the user wants elements colored BY a parameter's values (one distinct color per value, e.g. "tô màu tường theo Type") — and its parameter_name must be a REAL parameter, never empty, never a color name.
12. NEVER fabricate tool activity. Every action happens ONLY through a real tool call in this conversation — if you say you will use a tool, emit that tool call in the same turn, and only report results that came back from an actual tool result. Writing an invented "Result:" for a call you never made is a critical failure.
13. HISTORY IS NOT A TO-DO LIST: act only on the LATEST user message. Earlier turns are background context — an action already reported successful there is DONE; never re-execute it unless the user explicitly asks again. Each new command fully replaces the previous one: after "tô đỏ tường" (walls red) succeeded, "tô vàng sàn" targets FLOORS + YELLOW only — walls and red are finished business.
14. NEVER SAY A FILTER IS IMPOSSIBLE — `ai_element_filter` combines category/categories + `group_name` + `workset` + `level_name` + `type_name` + `name_contains` + parameter in ONE server-side call. "Elements inside the groups named _F1_" is `group_name="_F1_"` (the only tool that can answer it); "elements on workset X" is `workset="X"`. Do not fall back to listing everything and sifting, and do not tell the user no such filter exists.
    Then act on the result WITHOUT ids:
    - WHOLE-CATEGORY actions need no ids at all: `revit_override_color`, `operate_element`, `edit_elements`, `select_elements`, `set_element_workset`, `bulk_set_parameter` and `tag_elements` all accept `category` and collect every match themselves — one call, no count limit.
    - ANY OTHER subset: re-call `ai_element_filter` with the same filters plus `store_as="sel"`, then pass `element_ids=["@sel"]` to the acting tool. The full set goes through, uncapped, and no id enters the conversation. Pass literal ids only for a handful you already have (the user's selection, a couple picked out by name).
    Category names are the standard Revit ones (Walls, StructuralFoundations, Ducts, Areas, Rebar, ...) — if one is rejected, the error lists `did_you_mean`; retry with one of those instead of giving up or substituting another category.
15. NEVER SUBSTITUTE A DIFFERENT ACTION. Match the VERB the user asked for, not the nearest tool you happen to have.
   - `operate_element` (changes only how the ACTIVE VIEW looks): pin/ghim/khoá → "pin" (unpin/bỏ ghim → "unpin"); hide/ẩn → "hide"; isolate/cô lập → "isolate"; unhide/hiện lại → "unhide"; "bỏ isolate" / "hiện lại hết" → "reset_temporary" (needs no target); ẩn cả category trong view → "hide_category"; halftone/làm mờ → "halftone"; độ trong suốt → "transparency"; select/chọn → "select"; "chọn tương tự" → "select_similar".
   - `edit_elements` (changes the MODEL): mirror/lật/đối xứng → "mirror"; "đổi type/đổi sang loại X" → "change_type"; group/nhóm lại → "group"; ungroup/rã nhóm → "ungroup".
   - `manage_view`: đổi tỉ lệ/scale → "set_scale"; mức độ chi tiết → "set_detail_level"; discipline → "set_discipline"; crop/vùng cắt → "set_crop".
   - `manage_view_template` manages the templates themselves (list/rename/duplicate/delete); applying one to views is `apply_view_template`.
   - `manage_links` for linked models and CAD links (list/reload/unload/delete/pin); `manage_revision` for revisions (list/create/assign_to_sheets/set_issued); `manage_sheet` for duplicate/renumber/list_sets.
   - `export_model` for IFC / NWC / DWF / DGN — PDF stays `export_sheets_pdf`, DWG `export_dwg`, PNG `export_image`.
   - `check_bad_geometry` when an export crashes or geometry looks wrong; `manage_material` to read materials; `create_detail_annotation` for filled regions and detail lines; `manage_document` for save / save as / sync.
   - Only tô màu / color / highlight means an override color.
   If NO tool performs the requested action, say plainly that the assistant cannot do it yet and stop — doing a different thing to the model and reporting it as done is a critical failure, worse than doing nothing.

## Multiple open models
Several documents can be open in this Revit session; every tool operates on the ACTIVE one. To work across models: `list_open_documents` → `switch_active_document` (exact title) → read/modify there → switch again as needed, then combine everything into ONE final answer stating which model each number comes from. Element ids are only valid inside their own document — never reuse ids across models. When the user names a model that is not in `list_open_documents`, it may live in a different Revit window (separate process): say so and point them to the assistant in that window — do not guess.

## Live context
The latest user message carries a "Current Revit context" block (active view, selection, project) and, when relevant, "Reference from project knowledge" excerpts. Read them as the state of the model RIGHT NOW — they are not something the user typed.
"""


# Compact few-shot for LOCAL models on the DEFAULT (no-specialist) path.
# Cloud models follow the prose rules above reliably; small local models pick
# the right tool far more often when shown a couple of concrete traces. The
# per-specialist paths carry their own few-shot; this fills the general path.
_LOCAL_GENERAL_FEWSHOT = u"""
## Examples (follow this tool-calling style)
User: "có bao nhiêu cửa?" -> call ai_element_filter(category="Doors"); read total_count; reply "Model có 84 cửa."
User: "chọn các element đang chọn và ẩn đi" -> call revit_get_selected_elements FIRST; then operate_element(operation="hide", element_ids=[...]); reply what was hidden.
User: "tô đỏ tường" -> call revit_override_color(category="Walls", color="red") ONE time (server collects every wall, no ids, no limit); reply "Đã tô đỏ 128 tường."
User: "pin toàn bộ tường trong view" (= LOCK the walls, NOT a color) -> call operate_element(operation="pin", category="Walls") ONE time; reply "Đã pin 54 tường trong view."
User: "cô lập cột" then later "bỏ isolate đi" -> first operate_element(operation="isolate", category="Columns"); then operate_element(operation="reset_temporary") with NO category and NO ids — that is the only way back out of isolate.
User: "tạo section mới" -> call create_view(view_type="section"); never fall back to a 3D view.
User: "mở batchout" -> call open_t3lab_tool(tool_intent="open_batchout") LAST and stop.
Rules shown above still apply: query before you modify, numbers come from tool fields (total_count/...), never invent a tool or a result.
"""


# Language directives. The prompt used to hardcode "Always reply in English,
# regardless of the language the user writes in", which disagreed with the UI
# the moment the assistant's own strings went back to following the user.
_LANG_EN = (u"Always reply in English, regardless of the language the user "
            u"writes in. ")
_LANG_VI = (u"Always reply in Vietnamese, regardless of the language the user "
            u"writes in. ")
_LANG_AUTO = (u"Reply in the SAME language the user wrote in — Vietnamese in, "
              u"Vietnamese out; English in, English out. ")


def build_agent_system_prompt(revit_context=u"", local=False, lang="auto"):
    """STATIC system prompt for the native tool-calling agent path.

    Static is the point. The live Revit context used to be interpolated into
    this string, which made the whole system block change on every turn (the
    active view and selection are re-read by a 2-second timer). Since the
    system block is one prompt-cache breakpoint — and `messages` sit after it
    in the cache prefix — a system block that never repeats meant the cache
    never hit across turns and the entire prompt was re-processed each time.
    The volatile half now travels with the user turn: see
    build_context_block() and Intelligence/telemetry.py for the measurement.

    `revit_context` is accepted and ignored, so old callers keep working;
    passing it no longer has any effect on the returned prompt.

    local=True appends a compact few-shot trace — small local models select
    tools far more accurately from concrete examples than from prose alone.
    lang is 'auto' | 'vi' | 'en' and must match what the UI itself renders,
    so the model's prose and the assistant's own strings agree.
    """
    language = {'vi': _LANG_VI, 'en': _LANG_EN}.get(lang, _LANG_AUTO)
    prompt = _AGENT_PROMPT.format(language=language)
    if local:
        prompt += u"\n" + _LOCAL_GENERAL_FEWSHOT
        prompt += teacher_exemplar_block()
    return prompt


# ─── Portable teacher exemplars (few-shot distilled from teaching) ───────────────

def teacher_exemplar_block():
    """The committed teacher-exemplar few-shot block, prefixed with a blank line,
    or u"" when there are none.

    These come from lib/Intelligence/config/teacher_exemplars.json — a git-tracked
    file — so a plain local model (qwen3:14b) on ANY machine answers in the taught
    style without a re-train (the weight-level fine-tune is not portable). Static
    for the session, so the cached system prefix still hits across turns. Local
    models only, mirroring _LOCAL_GENERAL_FEWSHOT / SpecialistSpec.few_shot.
    Never raises.
    """
    try:
        from Intelligence.learning import exemplars as _ex
        block = _ex.build_exemplar_block(_ex.load_exemplars())
        return (u"\n" + block) if block else u""
    except Exception:
        return u""


# Fence around the volatile block so the model can tell live state from the
# user's own words, and so the block can be stripped back off a stored turn.
CONTEXT_FENCE_OPEN  = u"<<<T3LAB_LIVE_CONTEXT"
CONTEXT_FENCE_CLOSE = u"T3LAB_LIVE_CONTEXT>>>"


def build_context_block(revit_context=u"", knowledge_ref=u"",
                        analysis_hint=u""):
    """The VOLATILE half of the prompt, to prepend to the current user turn.

    Everything in here changes from turn to turn — the active view, the
    selection, knowledge excerpts retrieved for this specific question, and the
    language analysis of this specific message — which is exactly why it must
    not sit in the cached system block.

    `analysis_hint` is `Intelligence.language.Utterance.to_prompt_hint()`: a
    few lines naming the facts a model reads least reliably out of a
    Vietnamese sentence — whether it was a prohibition, how wide the scope is,
    which Revit categories were actually named, whether the operation needs
    confirming. It goes LAST, closest to the user's own words.

    Returns u"" when there is nothing live to report, so an ordinary turn
    carries no extra tokens at all.
    """
    parts = []
    ctx = (revit_context or u"").strip()
    if ctx:
        parts.append(u"## Current Revit context\n" + ctx)
    ref = (knowledge_ref or u"").strip()
    if ref:
        parts.append(ref)
    hint = (analysis_hint or u"").strip()
    if hint:
        parts.append(hint)
    if not parts:
        return u""
    return u"{}\n{}\n{}".format(CONTEXT_FENCE_OPEN,
                                u"\n\n".join(parts),
                                CONTEXT_FENCE_CLOSE)


def apply_context_block(user_content, context_block):
    """Prepend `context_block` to a turn's user content.

    Handles both content shapes the providers accept: a plain string, and the
    Anthropic block list used when an attachment carries images (see
    rag_processor.build_vision_content_blocks). In the list case the text goes
    into a leading text block so image blocks keep their own positions.
    """
    if not context_block:
        return user_content
    if isinstance(user_content, list):
        return [{"type": "text", "text": context_block}] + list(user_content)
    return u"{}\n\n{}".format(context_block, user_content or u"")


def strip_context_block(text):
    """Remove any live-context fence from `text`.

    History is stored from the raw user text, so this is belt-and-braces: it
    keeps a stray fenced block from being replayed as if it were still current
    if a caller ever persists the decorated content.
    """
    if not text or CONTEXT_FENCE_OPEN not in text:
        return text
    out = []
    depth = 0
    for line in text.split(u"\n"):
        stripped = line.strip()
        if stripped == CONTEXT_FENCE_OPEN:
            depth += 1
            continue
        if stripped == CONTEXT_FENCE_CLOSE:
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(line)
    return u"\n".join(out).strip()
