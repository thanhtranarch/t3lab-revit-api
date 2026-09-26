# -*- coding: utf-8 -*-
"""
T3Lab Agent Registry

Defines all available intents (T3Lab tools + MCP Revit commands) and builds
the system prompt that tells AI models exactly which APIs are available.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "T3Lab Agent"

# ─── Intent catalogue ──────────────────────────────────────────────────────────
# Format: intent_name → (category, description, example_phrase)
# Categories: Export | Tools | Chat
#
# Revit element query/create/modify tools are intentionally NOT hand-maintained
# here as a static list. They used to be (as "Revit Query"/"Revit Create"/
# "Revit Modify" categories with names like "revit_create_wall"), but that list
# had drifted out of sync with the real MCP tool registry in core/server.py —
# e.g. "revit_create_wall" was advertised to every LLM but the actual
# registered tool is "place_wall", so the model would confidently call a name
# that always failed with "Tool not implemented". The real, authoritative tool
# list (name/description/JSON-schema params) is read live from
# core/server.py's T3LabAIServer and appended to the prompt by whoever calls
# build_system_prompt() (see script.py's do_nlp(), which appends the live
# "Local MCP Server Tools" block). _get_mcp_tools()/is_mcp_intent()/
# get_intent_info() below read that same live registry so this module can
# never advertise a Revit tool name that doesn't actually exist.

AVAILABLE_INTENTS = {
    # ── T3Lab Export ──────────────────────────────────────────────────────────
    "export_direct":            ("Export",  "Export sheets to PDF/DWG/DWF directly",    u"xuất pdf G sheet"),
    "open_batchout_configured": ("Export",  "Open BatchOut with pre-set config",        u"mở batchout G sheet pdf"),
    "open_batchout":            ("Export",  "Open BatchOut tool",                       u"mở batchout"),

    # ── T3Lab Tools ───────────────────────────────────────────────────────────
    # Only tools with a dedicated launcher are listed statically. Every other
    # open_* intent is appended live from the tool_discovery registry by
    # _registry_intents() — the very drift described in the comment above had
    # also happened here: ParaSync, Project Name, Workset, Dim Text, Upper Dim
    # Text, Reset Overrides, Grids and Load Family Cloud were advertised to
    # every LLM long after their pushbuttons were deleted (2026-07-28).
    "open_loadfamily":      ("Tools", "Open the Family Loader / Family Manager",   u"load family"),
    "check_spelling":       ("Tools", "Scan ALL Text Notes in the model and proofread their English spelling (use for any spelling/grammar check request)", u"kiểm tra lỗi tiếng anh trong dự án"),

    # ── Conversation ─────────────────────────────────────────────────────────
    "help":    ("Chat", "Answer a T3Lab help question",         u"batchout là gì?"),
    "greet":   ("Chat", "Reply to a greeting",                  u"hello / xin chào"),
    "chat":    ("Chat", "General conversation",                 u"câu hỏi chung"),
    "unknown": ("Chat", "Cannot understand — ask for clarification", u""),
}


def _registry_intents():
    """Auto-discovered tool intents as {intent: (cat, desc, example)}.

    Read live from the tool_discovery registry so this module can never
    advertise a T3Lab tool that is not installed — the same guarantee
    _get_mcp_tools() already gives for the Revit tool names.
    """
    out = {}
    try:
        from Services.tool_discovery import get_registered_tools
        for t in get_registered_tools():
            intent = t.get('intent')
            if not intent or intent in AVAILABLE_INTENTS:
                continue
            title = t.get('title') or intent
            desc  = t.get('doc') or u"Open the {} tool".format(title)
            out[intent] = ("Tools", desc, u"mở {}".format(title.lower()))
    except Exception:
        pass
    return out


def get_available_intents():
    """Static intents + everything currently installed, merged."""
    merged = dict(AVAILABLE_INTENTS)
    merged.update(_registry_intents())
    return merged

# ─── Agent descriptors (for settings UI) ─────────────────────────────────────

AGENTS = [
    {
        "id":          "export",
        "name":        "Export Agent",
        "icon":        u"↗",
        "description": "Handles PDF, DWG, DWF exports and BatchOut operations",
        "intents":     ["export_direct", "open_batchout", "open_batchout_configured"],
    },
    {
        "id":          "tools",
        "name":        "Tools Agent",
        "icon":        u"⚙",
        "description": ("Opens T3Lab tool windows — Family Loader plus every "
                        "auto-discovered pushbutton; the intent list is filled "
                        "from the live registry, not hardcoded here."),
        "intents":     ["open_loadfamily", "check_spelling"],
    },
    {
        "id":          "revit_tools",
        "name":        "Revit Tools Agent",
        "icon":        u"?",
        "description": ("Queries/creates/modifies Revit elements via the live MCP tool "
                        "registry in core/server.py — see _get_mcp_tools() below, this "
                        "list is intentionally not hardcoded here."),
        "intents":     [],
    },
    {
        "id":          "chat",
        "name":        "Chat Agent",
        "icon":        u"...",
        "description": "General conversation, help, and T3Lab knowledge base",
        "intents":     ["help", "greet", "chat"],
    },
]


# ─── Live MCP tool registry (single source of truth for Revit manipulation) ──
# See the AVAILABLE_INTENTS comment above for why this replaced a hardcoded
# "Revit Query/Create/Modify" list.

def _get_mcp_tools():
    """Return the live list of MCP tool dicts (name/description/inputSchema)
    from core/server.py's T3LabAIServer, or [] if it isn't available (e.g.
    running outside Revit)."""
    try:
        from core.server import get_t3labai_server
        srv = get_t3labai_server()
        return srv._handle_tools_list().get('tools', [])
    except Exception:
        return []


def is_mcp_intent(intent):
    """Return True if `intent` is a real, currently-registered MCP tool name."""
    return any(t.get('name') == intent for t in _get_mcp_tools())


# ─── System prompt builder ────────────────────────────────────────────────────

def build_system_prompt(revit_context=u""):
    """Return the comprehensive system prompt listing all available APIs.

    revit_context: optional Revit model summary injected into the prompt.

    Note: this only covers T3Lab UI tools (Export/Tools) and conversational
    intents (Chat). The real Revit element query/create/modify tools are NOT
    listed here — callers that also want those should append the live tool
    list from core/server.py's T3LabAIServer._handle_tools_list() (with its
    real names and JSON-schema params), as script.py's do_nlp() already does
    under "Local MCP Server Tools". Duplicating that list here as static text
    is exactly what caused the previous drift bug.
    """
    # Group intents by category
    by_cat = {}
    for intent, (cat, desc, ex) in get_available_intents().items():
        by_cat.setdefault(cat, []).append((intent, desc, ex))

    lines = []
    for cat in ["Export", "Tools", "Chat"]:
        entries = by_cat.get(cat, [])
        if not entries:
            continue
        lines.append(u"\n[{}]".format(cat.upper()))
        for intent, desc, ex in entries:
            ex_txt = u"  e.g. \"{}\"".format(ex) if ex else u""
            lines.append(u"  {:36s} # {}{}".format(intent, desc, ex_txt))

    revit_block = u""
    if revit_context:
        revit_block = u"\nRevit Model Context:\n{}\n".format(revit_context)

    prompt = (
        u"You are T3Lab Assistant — an AI integrated into Autodesk Revit via T3Lab pyRevit extension.\n"
        u"You can understand natural language and map it to the available APIs below.\n"
        + revit_block +
        u"\nAVAILABLE APIs (use these as 'intent' values):\n"
        + u"\n".join(lines) +
        u"\n\nOUTPUT FORMAT — JSON only, no other text:\n"
        u'{"intent": "<intent>", "params": {<optional>}, "message": "<short reply in English>"}\n'
        u"\nPARAMS:\n"
        u"  export_direct / open_batchout_configured: format (pdf|dwg|dwf|dgn|ifc|nwd|img), filter (sheet prefix e.g. G/A/S or '' for all), combine (bool)\n"
        u"\nRULES:\n"
        u"  - CRITICAL CONTEXT RULE: act ONLY on the user's LATEST message. Earlier conversation turns are completed history — an action already reported as successful there is DONE; NEVER repeat or re-run it unless the latest message explicitly asks again. Each new command fully replaces the previous one: after \"tô đỏ tường\" (walls red) succeeded, \"tô vàng sàn\" means color FLOORS YELLOW only — walls and red are finished, do not touch them.\n"
        u"  - Color/highlight requests (\"tô đỏ X\", \"bôi vàng Y\", \"highlight Z\", \"color X red\"): the color word is the override color to APPLY, never a parameter value to filter by (elements have no 'Color' parameter). For a WHOLE category (\"tô vàng sàn\" = all floors yellow) call revit_override_color with category + color directly — the server collects ALL matching elements itself, no ids needed, no count limit. Pass element_ids only for a specific subset or the current selection. Use color_elements ONLY for coloring BY a parameter's values, with a REAL parameter_name.\n"
        u"  - Whole-category actions (\"chọn tất cả X\", \"ẩn hết Y\", \"đổi workset mọi Z\", \"set parameter for all W\"): operate_element / select_elements / set_element_workset / bulk_set_parameter / revit_override_color / tag_elements all accept category directly — the server collects ALL matching elements itself, no ids needed, no count limit.\n"
        u"  - CRITICAL: Never confuse queries about Revit elements (e.g. columns/cột, walls/tường, doors/cửa, rooms/phòng) with sheet exporting (export_direct). If the user asks about elements, use one of the exact tool names listed under \"Local MCP Server Tools\" below (if present), or ask for clarification. Do NOT use export_direct.\n"
        u"  - 'xuất/export' + format → export_direct\n"
        u"  - 'mở batchout' + config → open_batchout_configured\n"
        u"  - 'mở batchout' alone → open_batchout\n"
        u"  - Revit element queries/creation/modification → use the EXACT tool name and parameters from the \"Local MCP Server Tools\" list below. Never invent a tool name (e.g. there is no generic 'revit_create_wall' — use the real registered name shown there).\n"
        u"  - General conversation → chat\n"
        u"  - CRITICAL: greetings, thanks, or small talk alone (e.g. 'morning', 'hello', 'ok', 'thanks', 'chào') are NEVER tool commands → greet or chat. Never return an open_* / export / tool intent for them.\n"
        u"  - CRITICAL: if the user asks whether a tool/feature EXISTS ('có tool nào để X không?', 'do you have a tool for X?'), answer ONLY from the tool lists in this prompt — name the matching tool(s) or say clearly that none exists. NEVER invent a tool name.\n"
        u"  - SAME language as user (Vietnamese → Vietnamese, English → English)\n"
        u"  - CRITICAL: \"unknown\" means you have NOTHING to say — avoid it. If the request doesn't match a listed tool/API, answer it as \"chat\" instead (explain what you can do, ask a clarifying question, or give your best helpful answer) and ALWAYS fill \"message\" with real text. Only use \"unknown\" if you truly cannot produce any response at all.\n"
        u"\nEXAMPLES:\n"
        u"  input: xuất pdf G sheet\n"
        u'  output: {"intent":"export_direct","params":{"format":"pdf","filter":"G","combine":false},"message":"Đang xuất G sheet sang PDF..."}\n'
    )
    return prompt


def get_intent_info(intent):
    """Return (category, description) for an intent or (None, None)."""
    entry = get_available_intents().get(intent)
    if entry:
        return entry[0], entry[1]
    for t in _get_mcp_tools():
        if t.get('name') == intent:
            return "Revit Tool", t.get('description', '')
    return None, None


