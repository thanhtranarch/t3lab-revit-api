# -*- coding: utf-8 -*-
"""
T3Lab Assistant

Main AI assistant implementation for T3Lab Revit automation.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

from __future__ import unicode_literals

__author__  = "Tran Tien Thanh"
__title__   = "T3Lab Assistant"

import io
import json
import re
import os
import sys
import unicodedata

try:
    import clr
    clr.AddReference('System.Net')
    from System.Net import WebClient, WebException
    from System.Text import Encoding
    HAS_HTTP = True
except Exception:
    HAS_HTTP = False


# ─── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are T3Lab Assistant, a friendly AI helper built into Autodesk Revit's T3Lab plugin.
You help users both with T3Lab tool commands AND with casual conversation.

AVAILABLE TOOL INTENTS:

  ── Export (smart) ────────────────────────────────────────────────────────────
  export_direct           – export sheets directly WITHOUT opening any UI.
    params: {
      "format": "pdf"|"dwg"|"dwf"|"dgn"|"ifc"|"nwd"|"img",
      "filter": "<UPPERCASE sheet prefix e.g. G, A, S — or empty for all>",
      "combine": false
    }

  open_batchout_configured – open BatchOut pre-configured (sheets selected, format set, Create tab).
    params: { "format": "pdf"|"dwg"|..., "filter": "<prefix or empty>" }

  open_batchout           – open BatchOut with no pre-configuration. params: {}

  open_loadfamily         – open the Family Loader / Family Manager. params: {}

  ── Other tools ───────────────────────────────────────────────────────────────
  Every other tool intent is appended below under "Auto-discovered tools",
  generated from the live tool registry. NEVER use an open_* intent that is
  not listed there or above — a tool that is not in the list is not installed.

  ── Conversation ──────────────────────────────────────────────────────────────
  help   – answer a question about T3Lab tools.
    params: {"answer": "<concise answer>"}

  greet  – respond to a greeting (chào, hello, hi, xin chào, hey...).
    params: {}
    message: a warm, short greeting

  chat   – respond naturally to anything that is NOT a tool command
    (questions about Revit, small talk, thanks, follow-ups, etc.)
    params: {}
    message: a helpful, conversational reply in the SAME language as user input

EXPORT RULES:
- "xuất/export + format + filter + sheet" WITHOUT "mở/open" → export_direct
- "mở batchout + filter/format" → open_batchout_configured
- "mở batchout" alone → open_batchout
- Extract sheet prefix: "G sheet"→G, "tờ G"→G, "A sheet"→A etc.
- "toàn bộ"/"tất cả"/"all" → filter = "" (all sheets), default format = pdf

CONVERSATION RULES:
- Use conversation history to understand follow-up questions.
  e.g., user asks "batchout là gì?" then "nó xuất được những gì?" → use context.
- Be concise, friendly, professional. Reply in the SAME language as the user
  (Vietnamese in → Vietnamese out, English in → English out). This prompt
  contradicted itself before: it demanded English while every example below
  returned a Vietnamese "message".
- If unsure between tool and chat → prefer tool if there is a clear keyword.
- CRITICAL: greetings, thanks, or small talk alone ("morning", "hello", "ok",
  "thanks", "chào"...) are NEVER tool commands. Always answer them with
  greet/chat — never return an open_* or export intent for them.
- CRITICAL: if the user asks whether a tool/feature EXISTS ("có tool nào để X
  không?", "do you have a tool for X?"), answer ONLY from the tool lists in
  this prompt: name the matching tool(s), or say clearly that none exists.
  NEVER invent a tool name.

RESPONSE FORMAT (JSON only, no markdown, no extra text):
{
  "intent": "<intent_name>",
  "params": { ... },
  "message": "<friendly short message in English>"
}

EXAMPLES:
  "chào bạn"  → {"intent":"greet","params":{},"message":"Xin chào! Tôi là T3Lab Assistant. Cần giúp gì không?"}
  "hello"     → {"intent":"greet","params":{},"message":"Hello! I'm T3Lab Assistant. How can I help?"}
  "cảm ơn"    → {"intent":"chat","params":{},"message":"Không có gì! Nếu cần gì cứ hỏi nhé."}
  "batchout làm gì?" → {"intent":"help","params":{"answer":"BatchOut giúp xuất hàng loạt sheets sang PDF, DWG, DWF... với nhiều tùy chọn nâng cao."},"message":"BatchOut là công cụ xuất sheets hàng loạt."}
  "xuất pdf toàn bộ G sheet" → {"intent":"export_direct","params":{"format":"pdf","filter":"G","combine":false},"message":"Đang xuất tất cả G sheet sang PDF..."}
  "mở batchout G sheet pdf"  → {"intent":"open_batchout_configured","params":{"format":"pdf","filter":"G"},"message":"Mở BatchOut với G sheet đã chọn..."}
  "mở batchout"              → {"intent":"open_batchout","params":{},"message":"Đang mở BatchOut..."}
  "load family"              → {"intent":"open_loadfamily","params":{},"message":"Đang mở Family Loader..."}
"""

# ─── RAG system prompt prefix ─────────────────────────────────────────────────

_RAG_SYSTEM_PREFIX = """\
The user has attached one or more documents (PDF or images).
When document content is provided in the conversation:
1. Analyse the content carefully before responding.
2. Answer questions about the document in the SAME language as the user.
3. If the user asks to open a T3Lab tool, follow the normal tool intent rules.
4. If the request is purely about document analysis, respond as a "chat" intent
   with a detailed, helpful answer as the "message" field.
5. For image attachments, describe or analyse ONLY what you can actually
   see. If the image content is not available to you (text-only model, no
   vision), say so plainly and ask for key dimensions or a text PDF —
   NEVER invent a generic description of the building.
6. Building image / drawing analysis for modeling requests ("dung model",
   "build this", "model this building"): produce a STRUCTURED summary the
   build step can consume:
   - Levels: count + estimated floor-to-floor height (m)
   - Structural grid: bay count and spacing if visible (m)
   - Footprint: overall width x depth (m) and shape
   - Walls / openings / materials per facade
   Mark every number as MEASURED (a dimension or scale is visible) or
   ESTIMATED, show the table, and ask the user to confirm or correct it
   BEFORE building anything. The build itself follows the image-to-model
   workflow (levels -> grids -> walls -> floors/roof -> openings).
7. Keep answers concise unless the user asks for detail.

"""


# ─── Dynamic system prompt (injected with auto-discovered tools) ───────────────

_EXTRA_TOOLS_SECTION = ''   # set by inject_discovered_tools()


def inject_discovered_tools(tools):
    """
    Called by the Assistant window after tool discovery.
    Stores extra intent lines so parse_command() includes them in the prompt.

    Args:
        tools: list of tool dicts from tool_discovery.get_registered_tools()
    """
    global _EXTRA_TOOLS_SECTION
    if not tools:
        _EXTRA_TOOLS_SECTION = ''
        return
    lines = ['  ── Auto-discovered tools (do not ignore these) ──────────────────────────────']
    for t in tools:
        lines.append('  {}   params: {{}}   (opens "{}")'.format(t['intent'], t['title']))
    _EXTRA_TOOLS_SECTION = '\n'.join(lines)


def _build_system_prompt(revit_context=u""):
    """Return the comprehensive agent-aware system prompt, with auto-discovered tools appended."""
    try:
        from Intelligence.t3lab_agent import build_system_prompt
        base = build_system_prompt(revit_context=revit_context)
    except Exception:
        # Fallback to static prompt if t3lab_agent is unavailable
        base = SYSTEM_PROMPT
    if _EXTRA_TOOLS_SECTION:
        base = base + u'\n' + _EXTRA_TOOLS_SECTION
    # Persistent memory — remembered preferences/conventions steer this
    # path too. No tool hint: the JSON-intent path has no remember_fact
    # tool, mentioning it here would leak into the "message" field.
    try:
        from Intelligence import assistant_memory
        _pid = None
        try:
            from config.project_store import ProjectStore
            _pid = ProjectStore().get_active_project_id()
        except Exception:
            _pid = None
        _mem = assistant_memory.build_memory_block(
            _pid, include_tool_hint=False)
        if _mem:
            base = base + u'\n\n' + _mem
    except Exception:
        pass
    return base


# ─── Learned patterns ─────────────────────────────────────────────────────────

# The user's own phrasings and what they routed to: per-user data, kept in
# %APPDATA%/T3LabAI/assistant — never in the extension folder (a shared,
# public git clone; this file used to be committed with the owner's usage).
# The old in-extension file is copied over once.
_LEGACY_PATTERNS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'config', 'learned_patterns.json')


def _patterns_file():
    from core.paths import user_data_path
    return user_data_path('assistant', 'learned_patterns.json',
                          legacy=[_LEGACY_PATTERNS_FILE])


def load_learned_patterns():
    """Load learned patterns from disk. Returns dict {key: data}."""
    try:
        path = _patterns_file()
        if os.path.exists(path):
            with open(path, 'r') as f:
                data = json.load(f)
            return data.get('patterns', {})
    except Exception:
        pass
    return {}


def save_learned_patterns(patterns):
    """Persist learned patterns to disk."""
    try:
        path = _patterns_file()
        # Serialize to an ASCII string FIRST, then write in one shot —
        # json.dump(..., ensure_ascii=False) on a bytes-mode file dies with
        # UnicodeEncodeError mid-write under IronPython 2.7 as soon as a
        # pattern carries Vietnamese, truncating the file to 0 bytes
        # (same failure class as the chat-history/tool_registry bug).
        data = json.dumps({'patterns': patterns}, ensure_ascii=True, indent=2)
        if isinstance(data, bytes):
            data = data.decode('ascii')
        with io.open(path, 'w', encoding='utf-8') as f:
            f.write(data)
    except Exception:
        pass


# A learned pattern is a shorthand for a COMMAND ("mở batchout",
# "/english-spellcheck") — every legitimate entry on disk is 1–2 words. A long
# descriptive sentence must never become one: the key is an unordered word set
# matched at Jaccard ≥ 0.8, so on a 9-word key a single differing word still
# scores 0.9 and the mapping replays for requests it was never about. That is
# how one LLM guess on "create 3d views for each rooms in project with margin =
# 100mm" became a permanent deterministic route to Create Plan Views.
_LEARN_MAX_WORDS = 5


def _learned_contradicts(raw, intent):
    """True when `intent`'s tool NAMES something the request rules out.

    Same mutually-exclusive axes the catalog resolver uses (3d vs plan, wall vs
    floor, pdf vs dwg) — a learned mapping is a memory of a phrasing, and must
    not survive being replayed onto a tool the words contradict.
    """
    try:
        from Intelligence.nlu_engine import (_contradicts, _expand, _norm,
                                             _singularise, _tool_catalog)
        qset = set(_singularise(w) for w in
                   re.sub(r'[^a-z0-9\s]', ' ', _expand(_norm(raw))).split())
        for tool in _tool_catalog():
            if tool.get('intent') == intent:
                if _contradicts(qset, tool['words']):
                    return True
    except Exception:
        pass
    return False


def learn_pattern(raw, intent, params, message=''):
    """Record a successful command→intent mapping.

    Only learns tool intents (not greet/chat/help), only short command-like
    phrasings, and never a tool the request itself contradicts.
    """
    _skip = {'help', 'chat', 'greet', 'unknown', None}
    if intent in _skip:
        return
    if _learned_contradicts(raw, intent):
        return
    # Never learn small talk as a tool command. Without this gate, a single
    # LLM hallucination (e.g. "morning" → open_cadtoelements) gets recorded
    # and then replayed forever with top priority by find_learned_match().
    try:
        from Intelligence.nlu_engine import is_conversational
        if is_conversational(raw):
            return
    except Exception:
        pass
    try:
        key = _normalize_key(raw)
        if not key or len(key.split()) < 1:
            return
        if len(key.split()) > _LEARN_MAX_WORDS:
            return
        patterns = load_learned_patterns()
        if key in patterns:
            patterns[key]['hits'] = patterns[key].get('hits', 0) + 1
            patterns[key]['last_raw'] = raw
        else:
            patterns[key] = {
                'intent': intent,
                'params': params or {},
                'hits': 1,
                'last_raw': raw,
                'last_message': message,
            }
        # Keep only top 200 patterns (prune least-used)
        if len(patterns) > 200:
            sorted_keys = sorted(patterns.keys(), key=lambda k: patterns[k].get('hits', 0))
            for k in sorted_keys[:50]:
                del patterns[k]
        save_learned_patterns(patterns)
    except Exception:
        pass


def find_learned_match(raw):
    """Check learned patterns for a fuzzy match.

    Returns result dict {intent, params, message} or None.
    Uses Jaccard similarity on normalized word sets (threshold 0.8), plus a
    minimum-2-shared-words requirement once the stored pattern has 3+ words.
    The old 0.65 threshold was too permissive for the short 2-4 word
    commands typical here: a single shared word was often enough to clear
    it (e.g. "open material" vs a stored "material select" pattern scores
    1/3 ≈ 0.67 — a false positive that silently launches the wrong tool
    with high confidence, which is worse than falling through to the LLM).
    """
    try:
        # Conversational input (greetings, thanks, "ok"...) must never be
        # answered from learned tool patterns — even if an old poisoned
        # entry is still on disk, it is ignored here.
        from Intelligence.nlu_engine import is_conversational
        if is_conversational(raw):
            return None
        patterns = load_learned_patterns()
        if not patterns:
            return None
        key = _normalize_key(raw)
        key_words = set(key.split()) if key else set()
        if not key_words:
            return None

        best_score = 0.0
        best_data  = None

        for stored_key, data in patterns.items():
            stored_words = set(stored_key.split()) if stored_key else set()
            if not stored_words:
                continue
            # Long keys are not command shorthands (see _LEARN_MAX_WORDS) and
            # are no longer written; skipping them here retires the ones
            # already on disk instead of replaying them.
            if len(stored_words) > _LEARN_MAX_WORDS:
                continue
            inter = key_words & stored_words
            if len(stored_words) >= 3 and len(inter) < 2:
                continue
            union = key_words | stored_words
            score = len(inter) / len(union) if union else 0.0
            if score > best_score and not _learned_contradicts(
                    raw, data.get('intent')):
                best_score = score
                best_data  = data

        if best_score >= 0.8 and best_data:
            return {
                'intent':  best_data['intent'],
                'params':  best_data.get('params', {}),
                'message': best_data.get('last_message', ''),
                '_learned': True,
            }
    except Exception:
        pass
    return None


def _normalize_key(text):
    """Normalize to a lookup key: lowercase, no diacritics, meaningful words sorted."""
    try:
        nfd = unicodedata.normalize('NFD', text)
        ascii_text = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
        # NFD does not decompose đ/Đ — fold explicitly (see nlu_engine._strip_diacritics)
        ascii_text = ascii_text.replace(u'đ', 'd').replace(u'Đ', 'D')
    except Exception:
        ascii_text = text
    # Lowercase, keep alphanumeric
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', ' ', ascii_text.lower())
    # Keep words longer than 2 chars, sort for order-independence.
    # Short tokens carrying a DIGIT are kept regardless: "3d", "2d", "v2" are
    # exactly the words that distinguish two otherwise identical requests, and
    # dropping them made "create 3d views for each room" and "create plan views
    # for each room" the same key.
    words = sorted(w for w in cleaned.split()
                   if len(w) > 2 or any(c.isdigit() for c in w))
    return ' '.join(words)


# ─── API helpers ──────────────────────────────────────────────────────────────

def _get_api_key():
    """Retrieve Claude API key from T3LabAI settings."""
    try:
        lib_dir = os.path.dirname(os.path.abspath(__file__))
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        from config.settings import T3LabAISettings
        return T3LabAISettings().get_api_key("Claude")
    except Exception:
        return None


def _extract_json(text):
    """Extract the first JSON object from text, tolerating surrounding noise."""
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


def parse_command(user_input, history=None, attached_files=None, rag_context=None):
    """Parse a natural-language command via the active LLM provider.

    Routes through LLMRouter (Claude / OpenAI / Ollama) with automatic
    fallback.  Vision and RAG attachments are forwarded when the active
    provider supports them; text-only extraction is used otherwise.

    Args:
        user_input: The raw text the user typed.
        history: optional list of previous {"role", "content"} dicts.
        attached_files: optional list of local file paths to attach.
        rag_context: optional string of pre-extracted document text.

    Returns:
        dict with keys {intent, params, message} on success, or None on failure.
    """
    # ── Build content blocks for vision / RAG ──────────────────────────────────
    content_blocks = None
    if attached_files or rag_context:
        try:
            from Intelligence.rag_processor import build_vision_content_blocks
            files = attached_files or []
            content_blocks = build_vision_content_blocks(user_input, files)
            if rag_context and not any(
                    'PDF' in b.get('text', '') for b in content_blocks
                    if b.get('type') == 'text'):
                content_blocks.insert(0, {"type": "text", "text": rag_context})
        except Exception:
            content_blocks = None

    user_content = content_blocks if content_blocks else user_input
    max_tokens   = 1200 if (attached_files or rag_context) else 400

    system_prompt = _build_system_prompt()
    if attached_files or rag_context:
        system_prompt = _RAG_SYSTEM_PREFIX + system_prompt

    # Keep up to 16 history turns for context
    messages = list(history[-16:]) if history else []

    # ── Delegate to router ─────────────────────────────────────────────────────
    try:
        from Intelligence.llm_router import LLMRouter
        router = LLMRouter()
        raw_text = router.chat(messages, system_prompt, user_content, max_tokens)
    except Exception:
        raw_text = None

    if raw_text is None:
        return None

    result = _extract_json(raw_text)

    # Free-text response from a RAG/vision request → wrap as chat intent
    if result is None and (attached_files or rag_context):
        result = {
            "intent":  "chat",
            "params":  {},
            "message": raw_text,
        }

    return result


def has_api_key():
    """Return True if the active LLM provider is usable for a real request.

    "Usable" means CONFIGURED, not "answered a probe two seconds ago". This
    used to be `provider.check_health()`, which for the cloud providers is a
    live GET /models on every turn: behind a corporate proxy (or on any slow
    link — the probe has an 8s timeout and no retry) it returns False, the
    caller skips the whole LLM path, and the assistant answers from its
    offline canned replies while the model chip still shows the provider. The
    user sees "connect an AI in Settings" for an AI they already connected.

    A configured-but-actually-dead provider now fails at the real call, where
    the API's own error message reaches the user (see the llm_call_failed
    branch in the chat window). Local providers keep the reachability test —
    they have no key, so it is the only signal they have.
    """
    try:
        from Intelligence.llm_router import LLMRouter
        provider = LLMRouter().get_active_provider()
        return provider is not None and provider.is_configured()
    except Exception:
        return bool(_get_api_key())   # legacy fallback: check Claude key directly


def get_active_provider_name():
    """Return the name of the currently active provider ('claude', 'openai', 'ollama')."""
    try:
        from Intelligence.llm_router import LLMRouter
        return LLMRouter().get_active_name()
    except Exception:
        return "claude"


def get_provider_display_label():
    """Return a short UI badge label for the active provider (e.g. 'GPT-4o mini')."""
    try:
        from Intelligence.llm_router import LLMRouter
        return LLMRouter().get_display_label()
    except Exception:
        return "AI"


# ─── No-provider setup guidance ────────────────────────────────────────────────
# Shown instead of the generic "didn't understand" reply whenever NO LLM
# provider is configured at all (offline NLU/keyword matching still handles
# tool commands fine — this only fires for open-ended chat that genuinely
# needs a model). Also shown once as a proactive nudge on a fresh chat.

def get_setup_guidance_message(viet=True):
    """Return a detailed, step-by-step setup guide covering every provider option."""
    if viet:
        return (
            u"Hiện chưa có AI nào được kết nối, nên tôi chỉ hiểu được lệnh cụ thể "
            u"(vd: 'mở batchout', 'xuất pdf G sheet'). Để trò chuyện tự nhiên hơn, "
            u"nhấn nút Cài đặt ở góc trên rồi chọn 1 trong 2 cách:\n\n"
            u"1) Dùng AI trên mây (Claude / OpenAI / DeepSeek) — trả lời nhanh, "
            u"không cần máy mạnh:\n"
            u"   • Mở phần Cài đặt → chọn Provider → dán API Key vào ô tương ứng.\n"
            u"   • Lấy API Key tại: Claude → console.anthropic.com | "
            u"OpenAI → platform.openai.com | DeepSeek → platform.deepseek.com\n\n"
            u"2) Dùng AI cục bộ (Ollama) — miễn phí, chạy ngay trên máy, không cần internet:\n"
            u"   • Cài đặt tại ollama.ai, sau đó mở terminal chạy:\n"
            u"     ollama pull qwen3:14b   (khuyến nghị — đủ mạnh để gọi tool)\n"
            u"     • Máy yếu VRAM: dùng qwen3:8b hoặc qwen3:4b thay thế.\n"
            u"   • Mở lại T3Lab Assistant — hệ thống tự nhận diện model đã cài.\n\n"
            u"Trong lúc chưa kết nối, tôi vẫn mở được các tool T3Lab và xuất sheet "
            u"theo lệnh cụ thể như bình thường."
        )
    return (
        u"No AI provider is connected yet, so I can only understand specific "
        u"commands (e.g. 'open batchout', 'export pdf G sheet'). For more natural "
        u"conversation, click the Settings button at the top and pick one of two options:\n\n"
        u"1) Cloud AI (Claude / OpenAI / DeepSeek) — fast, no local hardware needed:\n"
        u"   • Open Settings → pick a Provider → paste your API key.\n"
        u"   • Get a key at: Claude → console.anthropic.com | "
        u"OpenAI → platform.openai.com | DeepSeek → platform.deepseek.com\n\n"
        u"2) Local AI (Ollama) — free, runs on your machine, no internet needed:\n"
        u"   • Install from ollama.ai, then in a terminal run:\n"
        u"     ollama pull qwen3:14b   (recommended — strong enough to call tools)\n"
        u"     • Low-VRAM machine: use qwen3:8b or qwen3:4b instead.\n"
        u"   • Reopen T3Lab Assistant — it will auto-detect the installed model.\n\n"
        u"Meanwhile I can still open T3Lab tools and export sheets from specific commands."
    )


# ─── Keyword fallback ─────────────────────────────────────────────────────────

def keyword_parse(raw):
    """Keyword-based fallback parser.

    Checks learned patterns first (priority), then hardcoded keywords.
    Returns dict {intent, params, message} or None.
    """
    cmd = raw.lower().strip()
    viet = _is_viet(cmd)

    # ── Learned patterns (highest priority) ──────────────────────────────────
    learned = find_learned_match(raw)
    if learned:
        # Generate a fresh message so it sounds natural
        intent = learned['intent']
        if intent in _TOOL_LABELS:
            label = _TOOL_LABELS[intent]
            learned['message'] = (u"Đang mở {}...".format(label) if viet
                                  else u"Opening {}...".format(label))
        return learned

    # ── Greetings ─────────────────────────────────────────────────────────────
    greet_kws = ['chao', 'chào', 'hello', 'hi ', 'hey ', 'xin chao', 'good morning',
                 'good afternoon', 'howdy']
    if any(k in cmd for k in greet_kws) or cmd.strip() in (
            'hi', 'hello', 'hey', 'morning', 'afternoon', 'evening', 'yo'):
        if viet:
            msg = u"Xin chào! Tôi là T3Lab Assistant. Cần giúp gì không?"
        else:
            msg = u"Hello! I'm T3Lab Assistant. How can I help?"
        return {"intent": "greet", "params": {}, "message": msg}

    # ── Thanks ────────────────────────────────────────────────────────────────
    if any(k in cmd for k in ['cam on', 'cảm ơn', 'thank', 'thanks', 'cảm ơn bạn']):
        msg = (u"Không có gì! Cần gì cứ hỏi nhé." if viet
               else u"You're welcome! Let me know if you need anything.")
        return {"intent": "chat", "params": {}, "message": msg}

    # ── Export commands ───────────────────────────────────────────────────────
    export_kws = ['xuat', 'xuất', 'export', 'in ra', 'print']
    is_export = any(k in cmd for k in export_kws)
    is_open   = any(k in cmd for k in ['mo ', 'mở ', 'open ', 'launch '])

    if is_export and not is_open:
        params = _parse_export_params(raw, cmd)
        filt  = params['filter']
        fmt   = params['format'].upper()
        filt_label = u" {} sheet".format(filt) if filt else u" tất cả sheet"
        msg = (u"Đang xuất{} sang {}...".format(filt_label, fmt) if viet
               else u"Exporting{} to {}...".format(filt_label, fmt))
        return {"intent": "export_direct", "params": params, "message": msg}

    if is_open and "batchout" in cmd:
        params = _parse_export_params(raw, cmd)
        if params.get('filter') or params.get('format', 'pdf') != 'pdf':
            filt  = params['filter']
            label = u" {} sheet".format(filt) if filt else u" tất cả sheet"
            msg = (u"Mở BatchOut với{} đã chọn...".format(label) if viet
                   else u"Opening BatchOut pre-configured{}...".format(label))
            return {"intent": "open_batchout_configured", "params": params, "message": msg}

    # ── Tool keywords ─────────────────────────────────────────────────────────
    if any(k in cmd for k in ["batchout", "batch out"]):
        return {"intent": "open_batchout", "params": {},
                "message": u"Đang mở BatchOut..." if viet else "Opening BatchOut..."}

    if any(k in cmd for k in ["load family", "tai family", "tải family",
                              "nap family", "family loader", "manafami"]):
        return {"intent": "open_loadfamily", "params": {},
                "message": u"Đang mở Family Loader..." if viet else "Opening Family Loader..."}

    # NOTE — 2026-07-28: the hardcoded branches for ParaSync, Load Family
    # (Cloud), Project Name, Workset, Upper Dim Text, Dim Text, Reset Overrides
    # and Grids were removed. Each returned an intent whose pushbutton no longer
    # exists, and because they matched BEFORE resolve_tool() they also shadowed
    # the real tools that replaced them — "workset" answered "Opening Workset..."
    # and then failed, instead of resolving to ManaWorkset. Tools other than
    # BatchOut / Family Loader must go through the ranked resolver below.

    # ── Auto-discovered tools — ranked resolver, never first-substring ────────
    # The old loop returned whichever registry entry had ANY generic keyword
    # ("manager", "elements", "auto"...) appear first — wrong tool half the
    # time. resolve_tool() scores the whole catalog and only answers when one
    # tool clearly wins.
    try:
        from Intelligence.nlu_engine import resolve_tool
        match, _cands = resolve_tool(raw)
        if match:
            label = match.get('title', match['intent'])
            return {
                'intent':  match['intent'],
                'params':  {},
                'message': (u"Đang mở {}...".format(label) if viet
                            else u"Opening {}...".format(label)),
            }
    except Exception:
        pass

    return None


# ─── Tool labels for auto-generated messages ─────────────────────────────────

# Labels for the two tools with a dedicated launcher. Everything else is
# labelled from the live registry by get_tool_label() — hardcoding the rest is
# how this map came to advertise eight tools that no longer shipped.
_SPECIAL_TOOL_LABELS = {
    'open_batchout':   'BatchOut',
    'open_loadfamily': 'Family Loader',
}


def get_tool_label(intent):
    """Display label for a tool intent, from the live registry when possible."""
    if intent in _SPECIAL_TOOL_LABELS:
        return _SPECIAL_TOOL_LABELS[intent]
    try:
        from Services.tool_discovery import get_registered_tools
        for t in get_registered_tools():
            if t.get('intent') == intent:
                return t.get('title') or intent
    except Exception:
        pass
    return (intent or '').replace('open_', '').replace('_', ' ').strip().title()


# Backwards-compatible alias: existing call sites index this like a dict.
_TOOL_LABELS = _SPECIAL_TOOL_LABELS


# ─── Export param extraction ──────────────────────────────────────────────────

def _parse_export_params(raw, cmd=None):
    """Extract format and filter from a raw export command string."""
    if cmd is None:
        cmd = raw.lower()

    fmt = 'pdf'
    for f in ['dwg', 'dwf', 'dgn', 'ifc', 'nwd', 'img', 'image', 'pdf']:
        if f in cmd:
            fmt = f
            break

    m = re.search(r'\b([A-Z])\s*[-–]?\s*(?:sheet|tờ|bản\s*vẽ)', raw, re.IGNORECASE)
    if m:
        return {'format': fmt, 'filter': m.group(1).upper(), 'combine': False}

    m = re.search(r'(?:sheet|tờ)\s+([A-Z])\b', raw, re.IGNORECASE)
    if m:
        return {'format': fmt, 'filter': m.group(1).upper(), 'combine': False}

    _ignore = {'PDF', 'DWG', 'DWF', 'DGN', 'IFC', 'NWD', 'IMG'}
    for token in raw.split():
        if re.match(r'^[A-Z]$', token) and token not in _ignore:
            return {'format': fmt, 'filter': token, 'combine': False}

    return {'format': fmt, 'filter': '', 'combine': False}


# ─── Built-in NLU engine ──────────────────────────────────────────────────────

_nlu = None

def _get_nlu():
    """Lazy-import nlu_engine (always bundled with the tool)."""
    global _nlu
    if _nlu is not None:
        return _nlu
    try:
        from Intelligence import nlu_engine as _mod
        _nlu = _mod
    except Exception:
        pass
    return _nlu


def parse_command_nlu(user_input, history=None):
    """Parse user_input using the built-in NLU engine (no external service).

    Always available offline.  Returns dict {intent, params, message} or None.
    """
    mod = _get_nlu()
    if not mod:
        return None
    try:
        return mod.classify(user_input, history=history)
    except Exception:
        return None


# ─── Local LLM (Ollama) integration ──────────────────────────────────────────

_local_llm = None
_HAS_LOCAL_LLM = False

def _get_local_llm():
    """Lazy-import local_llm module."""
    global _local_llm, _HAS_LOCAL_LLM
    if _local_llm is not None:
        return _local_llm
    try:
        from Intelligence import local_llm as _mod
        _local_llm = _mod
        _HAS_LOCAL_LLM = True
    except Exception:
        _HAS_LOCAL_LLM = False
    return _local_llm


def has_local_llm():
    """Return True if a local provider (Ollama or LM Studio) is usable.

    Delegates to LLMRouter's provider adapters — the SAME check that drives
    the "Ready" status shown in Settings — instead of the old standalone
    local_llm.py probe. That probe only ever tried OLLAMA_HOST verbatim
    (default "http://localhost:11434") with no fallback, while
    OllamaProvider tries "http://127.0.0.1:11434" too. On setups where
    "localhost" doesn't resolve cleanly (common enough on Windows), the two
    checks disagreed: Settings showed Ollama "Ready" while this gate
    returned False, silently skipping the LLM path for EVERY message and
    making a correctly-connected assistant look broken.
    """
    try:
        from Intelligence.llm_router import LLMRouter
        router = LLMRouter()
        for name in router.get_local_provider_names():   # ["ollama", "lmstudio"]
            provider = router.get_provider(name)
            if provider and provider.check_health():
                return True
        return False
    except Exception:
        pass
    # Fallback to the legacy probe only if the router itself is unavailable.
    mod = _get_local_llm()
    if not mod:
        return False
    try:
        return bool(mod.get_best_model()) and mod.is_running()
    except Exception:
        return False


def get_local_model_name():
    """Return the active local (Ollama/LM Studio) model name, or None."""
    try:
        from Intelligence.llm_router import LLMRouter
        router = LLMRouter()
        for name in router.get_local_provider_names():
            provider = router.get_provider(name)
            if provider and provider.check_health():
                return provider.get_active_model()
        return None
    except Exception:
        pass
    mod = _get_local_llm()
    if not mod:
        return None
    try:
        return mod.get_best_model()
    except Exception:
        return None


def parse_command_local(user_input, history=None):
    """Parse user_input using local Ollama LLM.

    Returns dict {intent, params, message} or None.
    """
    mod = _get_local_llm()
    if not mod:
        return None
    try:
        return mod.parse_command(user_input, history=history)
    except Exception:
        return None


# ─── Language heuristic ───────────────────────────────────────────────────────

def _is_viet(text):
    viet_chars = (u"àáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợ"
                  u"ụủứừửữựỳỵỷỹ")
    for c in text.lower():
        if c in viet_chars:
            return True
    return False
