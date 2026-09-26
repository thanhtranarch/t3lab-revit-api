# -*- coding: utf-8 -*-
"""
Tool Schema

Converts the T3Lab MCP tool registry (core/server.py) into the native
function-calling formats of each LLM provider, so tool schemas are sent
through the API's `tools` parameter instead of being dumped as text into
the system prompt on every turn.

Formats:
    Anthropic:  {"name", "description", "input_schema"}
    OpenAI:     {"type": "function", "function": {"name", "description", "parameters"}}
    Ollama:     same wire shape as OpenAI (/api/chat `tools`)

Results are cached per session — the registry only changes on pyRevit reload.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__  = "Tool Schema"

import copy


# Synthetic tool exposed ONLY to the in-chat agent (not the MCP HTTP surface):
# lets the model open a T3Lab pushbutton window. The agent loop treats it as
# terminal — the launch happens on the UI thread after the loop finishes.
LAUNCHER_TOOL_NAME = "open_t3lab_tool"

# Synthetic tool for persistent assistant memory (assistant_memory.py).
# Executed locally by script.py's _exec_tool wrapper — never reaches the
# Revit server, so it needs no ExternalEvent and is always non-terminal.
MEMORY_TOOL_NAME = "remember_fact"


def make_memory_tool():
    """Build the remember_fact schema in server-registry shape."""
    return {
        "name": MEMORY_TOOL_NAME,
        "description": (
            "Manage the assistant's persistent memory so future chats start "
            "knowing durable facts. action 'save' (default) adds ONE fact when "
            "the user states a lasting preference or convention ('from now "
            "on...', 'our sheet prefix is...'). action 'update' supersedes a "
            "fact the user has CHANGED (set `replaces` to the old fact's gist "
            "and `fact` to the new statement) so the stale one does not linger. "
            "action 'forget' drops a fact the user CANCELS (set `replaces` to "
            "its gist). Never store one-off request details, secrets/API keys, "
            "or element ids. The user can review everything with /memory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": ("The fact as one short English sentence "
                                    "(for save/update the new statement; for "
                                    "forget, the gist of what to drop)."),
                },
                "scope": {
                    "type": "string",
                    "enum": ["project", "global"],
                    "description": ("project = a convention of THIS Revit "
                                    "project; global = a user preference "
                                    "that applies to every project."),
                },
                "action": {
                    "type": "string",
                    "enum": ["save", "update", "forget"],
                    "description": ("save = add a new fact (default); update = "
                                    "replace a changed fact; forget = remove a "
                                    "cancelled fact."),
                },
                "replaces": {
                    "type": "string",
                    "description": ("For update/forget: the gist or text of the "
                                    "prior fact this supersedes."),
                },
            },
            "required": ["fact"],
        },
    }


def make_launcher_tool(intents):
    """Build the open_t3lab_tool schema in server-registry shape.

    Args:
        intents: list of launcher intent strings (e.g. ["open_batchout", ...]).
    """
    return {
        "name": LAUNCHER_TOOL_NAME,
        "description": (
            "Open a T3Lab tool window inside Revit by its intent name. "
            "This ends the conversation turn — call it as your FINAL action, "
            "never followed by other tool calls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_intent": {
                    "type": "string",
                    "description": "Which T3Lab tool to open.",
                    "enum": sorted(intents or []),
                },
            },
            "required": ["tool_intent"],
        },
    }


# ─── Essential subset for small local models ──────────────────────────────────
# The full registry (~110 tools) is fine for cloud providers, but local chat
# templates render every schema INTO the prompt (≈15–25k tokens) and small
# models also pick tools far less accurately from a huge catalog. Local
# providers therefore get this curated subset covering the common asks;
# cloud providers keep the full list.

ESSENTIAL_TOOL_NAMES = frozenset([
    # Read / context
    "get_revit_context", "revit_get_project_info", "revit_get_active_view",
    "get_current_view_info", "get_current_view_elements",
    "revit_get_selected_elements", "revit_get_element_info",
    "get_elements_by_level", "list_levels", "list_worksets",
    "revit_list_views", "revit_list_sheets", "ai_element_filter",
    "get_parameter", "get_all_parameters", "get_model_warnings",
    "get_model_health", "analyze_model_statistics", "get_schedule_data",
    "get_available_family_types", "get_element_bounding_box",
    "get_material_quantities",
    "list_open_documents", "switch_active_document",
    "open_document", "close_document", "list_recent_documents",
    # Modify
    "set_parameter", "bulk_set_parameter", "rename_element",
    "select_elements", "color_elements", "revit_override_color",
    "operate_element",   # hide / isolate / pin / halftone / select_similar / ...
    "edit_elements",     # mirror / change_type / group / ungroup
    "manage_view",       # scale / detail level / discipline / crop
    "set_active_view", "create_text_note", "tag_elements",
    "tag_all_rooms", "tag_all_walls", "move_elements", "delete_element",
    "create_sheet", "add_view_to_sheet", "duplicate_view",
    "manage_links", "manage_sheet", "manage_revision", "manage_material",
    "check_bad_geometry", "create_detail_annotation", "manage_document",
    # Create geometry. A catalog with create_text_note but no create_grid
    # does not make the model decline a build request — it makes it answer
    # with the nearest tool it CAN see: "dựng hệ grid 5x5" came back as a
    # text note reading "Grid 5x5". A local turn that lands on the `general`
    # specialist (no subset of its own) sees only this list, so the geometry
    # constructors have to be in it.
    "create_grid", "create_level", "create_room", "place_wall",
    "create_point_based_element", "create_line_based_element",
    "create_surface_based_element", "create_dimension", "load_family",
    "join_geometry", "split_element", "split_curve", "rotate_element",
    "copy_elements",
    # Views, schedules, filters, worksets, project parameters. Same reason:
    # `general` is where every request the keyword stage could not place
    # ends up, so this list has to answer for all of them.
    "create_view", "create_schedule", "create_view_filter",
    "apply_view_template", "place_views_on_sheets",
    "create_workset", "set_element_workset", "create_project_parameter",
    # Export
    "export_sheets_pdf", "export_dwg", "export_image", "export_model",
    "export_room_data",
])


# The other side of the same decision: tools deliberately kept OUT of every
# local catalog. Declared explicitly so the split is a choice rather than an
# oversight — dev/test_assistant_routing.py fails when a registry tool is
# neither in a specialist subset, nor in ESSENTIAL_TOOL_NAMES, nor here.
# That is the guard against the create_grid class of bug: a tool nobody
# wired up is invisible to the model, and an invisible tool is not answered
# with "I can't" — it is answered with the nearest visible tool.
LOCAL_HIDDEN_TOOLS = frozenset([
    "send_code_to_revit",   # arbitrary IronPython — never for a small model
    "say_hello",            # MCP connectivity probe
    "file_watcher_status",  # diagnostic
    "show_assistant_pane",  # the assistant's own UI
    "store_project_data", "store_room_data", "query_stored_data",
])


# Tools that leave NO trace: they neither change the document nor write a
# file. Everything else in the registry is "consequential" — it edits the
# model, overrides view graphics, or drops files on disk.
#
# This is NOT core.server._WRITE_TOOLS. That set answers a different
# question ("must this run on Revit's main thread?") and therefore contains
# read-only-but-heavy tools (check_bad_geometry, say_hello,
# switch_active_document). Using it as a proxy for "will this change my
# model?" mislabels both directions.
#
# Selection and view/document switching count as side-effect free: they
# change what the user is LOOKING at, never what the file contains, and both
# are one click to undo. Exports do NOT — a bare "/export-standard" must not
# spray PDFs into a folder nobody chose.
#
# Consumer: skills_engine.SkillsEngine.modifies_model(), which decides
# whether a bare "/skill" invocation may act immediately or has to present a
# plan first. dev/test_tool_registry.py checks every name here is real and
# that the registry is fully partitioned by it.
READ_ONLY_TOOL_NAMES = frozenset([
    # Model / project reads
    "ai_element_filter", "analyze_model_statistics", "audit_model",
    "check_bad_geometry", "get_all_parameters", "get_available_family_types",
    "get_current_view_elements", "get_current_view_info",
    "get_element_bounding_box", "get_elements_by_level",
    "get_material_quantities", "get_model_health", "get_model_warnings",
    "get_parameter", "get_revit_context", "get_schedule_data",
    "list_levels", "list_open_documents", "list_recent_documents",
    "list_worksets", "query_stored_data", "revit_get_active_view",
    "revit_get_element_info", "revit_get_project_info",
    "revit_get_selected_elements", "revit_list_sheets", "revit_list_views",
    # Navigation / highlighting — changes the view, never the file
    "select_elements", "set_active_view", "switch_active_document",
    # Diagnostics and the assistant's own UI
    "file_watcher_status", "say_hello", "show_assistant_pane",
])


def is_model_modifying(name):
    """True when `name` edits the document or writes a file.

    Unknown names are treated as modifying: a tool nobody classified is the
    one most likely to surprise the user.
    """
    return bool(name) and name not in READ_ONLY_TOOL_NAMES


# ─── Registry access ───────────────────────────────────────────────────────────

_cache = {}   # keys: "raw", "anthropic[_ess]", "openai[_ess]"


def get_server_tools():
    """Return the raw tool list from the local MCP server registry.

    Each item: {'name', 'description', 'inputSchema'}. Returns [] on failure
    (server module unavailable) so callers can fall back gracefully.
    """
    if "raw" in _cache:
        return _cache["raw"]
    try:
        from core.server import get_t3labai_server
        srv   = get_t3labai_server()
        tools = srv._handle_tools_list().get("tools", []) or []
        # Keep only well-formed entries; never let one bad schema kill the list.
        # Drop the external-only teaching/training control tools: they exist for
        # the EXTERNAL MCP teacher (Claude Desktop) to frame trajectories and
        # drive the fine-tune, and must never enter the in-app assistant's own
        # tool catalog — the local model should not toggle its own safety guard
        # or launch training. The external bridge reads the server registry
        # directly, so Claude Desktop still sees them.
        clean = [t for t in tools
                 if isinstance(t, dict) and t.get("name") and t.get("inputSchema")
                 and t["name"] not in _EXTERNAL_ONLY_TOOLS]
        _cache["raw"] = clean
        return clean
    except Exception:
        return []


# Teacher-/trainer-only pseudo-tools (see core/server.py); hidden from the in-app
# agent, exposed to the external MCP client (Claude Desktop) only.
_EXTERNAL_ONLY_TOOLS = frozenset([
    't3lab_begin_teaching', 't3lab_end_teaching',
    't3lab_set_teaching_mode', 't3lab_mark_sandbox',
    't3lab_training_status', 't3lab_train_model',
    't3lab_build_exemplars',
])


# ─── Converters ────────────────────────────────────────────────────────────────

def _registry_tools(essential_only):
    """Raw registry, optionally filtered to ESSENTIAL_TOOL_NAMES.

    An empty filter result (name drift after a registry rebuild) falls back
    to the full list — a big prompt beats a mute agent.
    """
    tools = get_server_tools()
    if not essential_only:
        return tools
    subset = [t for t in tools if t["name"] in ESSENTIAL_TOOL_NAMES]
    return subset or tools


def to_anthropic_tools(extra_tools=None, essential_only=False):
    """Convert registry (+ optional extra tools in registry shape) to Anthropic format."""
    key = ("anthropic_ess" if essential_only else "anthropic") if not extra_tools else None
    if key and key in _cache:
        return _cache[key]
    out = []
    for t in _registry_tools(essential_only) + list(extra_tools or []):
        out.append({
            "name":         t["name"],
            "description":  t.get("description", "") or t["name"],
            "input_schema": copy.deepcopy(t["inputSchema"]),
        })
    if key:
        _cache[key] = out
    return out


def to_openai_tools(extra_tools=None, essential_only=False):
    """Convert registry (+ optional extras) to OpenAI/Ollama function format."""
    key = ("openai_ess" if essential_only else "openai") if not extra_tools else None
    if key and key in _cache:
        return _cache[key]
    out = []
    for t in _registry_tools(essential_only) + list(extra_tools or []):
        out.append({
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t.get("description", "") or t["name"],
                "parameters":  copy.deepcopy(t["inputSchema"]),
            },
        })
    if key:
        _cache[key] = out
    return out


def get_tools_for_provider(provider_name, extra_tools=None, essential_only=False):
    """Return the tool list in the right wire format for a provider name."""
    if provider_name == "claude":
        return to_anthropic_tools(extra_tools, essential_only=essential_only)
    # openai / deepseek / ollama / lmstudio all use the OpenAI function shape
    return to_openai_tools(extra_tools, essential_only=essential_only)


def _convert(tools, provider_name):
    """Convert a list of registry-shaped tools to a provider's wire format."""
    out = []
    for t in tools:
        if provider_name == "claude":
            out.append({
                "name":         t["name"],
                "description":  t.get("description", "") or t["name"],
                "input_schema": copy.deepcopy(t["inputSchema"]),
            })
        else:
            out.append({
                "type": "function",
                "function": {
                    "name":        t["name"],
                    "description": t.get("description", "") or t["name"],
                    "parameters":  copy.deepcopy(t["inputSchema"]),
                },
            })
    return out


def get_tools_by_names(provider_name, names, extra_tools=None):
    """Registry filtered to `names`, in the provider's wire format.

    An empty filter result (name drift after a registry rebuild) falls back
    to the full provider list — same defensive rule as _registry_tools.
    """
    wanted = frozenset(names or [])
    if not wanted:
        return get_tools_for_provider(provider_name, extra_tools)
    subset = [t for t in get_server_tools() if t["name"] in wanted]
    if not subset:
        return get_tools_for_provider(provider_name, extra_tools)
    return _convert(subset + list(extra_tools or []), provider_name)


