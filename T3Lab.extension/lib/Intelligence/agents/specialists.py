# -*- coding: utf-8 -*-
"""
SpecialistSpec registry — per-specialist prompt, tool subset and budget.

The multi-agent layer is NOT separate processes: a specialist is just a
parameterization of the existing native agent loop (script.py's
_run_native_agent) — smaller focused prompt + smaller tool catalog +
tighter iteration budget. That is precisely what makes small local
models answer better.

Tool-subset policy:
    revit_data    read-only subset for ALL providers (queries never
                  need writes; a misroute degrades to "I can't modify").
    revit_action  curated subset for LOCAL providers only — cloud keeps
                  the full registry so no capability is lost there.
    general       provider default (exactly today's behavior).

Pure Python, CPython-3 testable.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

# ─── Tool subsets (validated against tool_schema.ESSENTIAL_TOOL_NAMES) ───────

READ_TOOLS = frozenset([
    "check_bad_geometry", "manage_material",
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
    # Scan-to-BIM reads: list / sample / detect — nothing is created.
    "list_point_clouds", "analyze_point_cloud", "detect_point_cloud_elements",
])

MODIFY_TOOLS = frozenset([
    "set_parameter", "bulk_set_parameter", "rename_element",
    "select_elements", "color_elements", "revit_override_color",
    # Multi-operation tools. Leaving operate_element out of this subset left the
    # ACTION specialist with no way to pin or hide anything, and the model
    # answered "pin all walls" with the nearest tool it COULD see
    # (revit_override_color) — a silent wrong action. Any tool with an
    # `operation` enum belongs here; dev/test_tool_registry.py enforces it.
    "operate_element", "edit_elements", "manage_view", "manage_view_template",
    "manage_links", "manage_revision", "manage_sheet",
    "manage_material", "create_detail_annotation", "manage_document",
    "set_active_view", "create_text_note", "tag_elements",
    "tag_all_rooms", "tag_all_walls", "move_elements", "delete_element",
    "create_sheet", "add_view_to_sheet", "duplicate_view",
    # Everything the dispatcher's action keywords can send here must be
    # HERE. A tool this specialist is asked for but cannot see does not
    # produce "I can't do that" — it produces the nearest visible tool
    # applied to the model and reported as success (see create_grid →
    # create_text_note). dev/test_assistant_routing.py enforces the match.
    "copy_elements", "rotate_element",              # siblings of move_elements
    "create_view", "create_schedule", "create_view_filter",
    "apply_view_template", "place_views_on_sheets",  # view/sheet production
    "create_dimension",                              # sibling of create_text_note
    "split_element", "split_curve",                  # siblings of edit_elements
    "create_workset", "set_element_workset",
    "create_project_parameter",                      # project setup
])

EXPORT_TOOLS = frozenset([
    "export_sheets_pdf", "export_dwg", "export_image", "export_model",
    # "xuất dữ liệu phòng ra file" is an export in the user's words, and
    # reads like one to the keyword stage too.
    "export_room_data",
])

ACTION_TOOLS = READ_TOOLS | MODIFY_TOOLS | EXPORT_TOOLS

# Multi-document sessions: several models open in ONE Revit process. All
# tools operate on the ACTIVE document; these switch/inspect across them.
MULTIDOC_TOOLS = READ_TOOLS | frozenset([
    "open_document", "close_document", "list_recent_documents",
    "export_room_data", "manage_document",
])

# Geometry creation (image-to-model / text-to-model workflows).
MODELING_TOOLS = READ_TOOLS | frozenset([
    "create_level", "create_grid", "place_wall",
    "create_line_based_element", "create_point_based_element",
    "create_surface_based_element", "create_room", "room_to_floor",
    "create_dimension", "create_text_note", "load_family",
    "join_geometry", "move_elements", "copy_elements", "rotate_element",
    "delete_element", "select_elements",
    # A beam grid is the one build the dispatcher already routed here while
    # the subset could not perform it.
    "create_structural_framing_system",
    "split_element", "split_curve",
    # Loadable families modelled by the AI: contract -> reviewed proposal ->
    # saved .rfa (FamiGen, schema v2 with materials).
    "famigen_get_schema", "famigen_propose_family", "famigen_create_family",
])

# Model QA / audit: read everything, highlight problems visually.
QA_TOOLS = READ_TOOLS | frozenset([
    "select_elements", "color_elements", "revit_override_color",
    "audit_model", "purge_unused", "create_view_filter",
    # Degenerate geometry is the top cause of export crashes in this codebase,
    # so a QA turn must be able to look for it.
    "check_bad_geometry", "manage_links", "manage_material",
])


class SpecialistSpec(object):

    def __init__(self, name, description, tool_names=None,
                 restrict_cloud=False, prompt_intro='', few_shot='',
                 max_iterations=10, max_tokens=1500, allows_writes=True,
                 use_launcher=True):
        self.name = name
        self.description = description
        self.tool_names = tool_names          # None = provider default
        self.restrict_cloud = restrict_cloud  # subset applies to cloud too
        self.prompt_intro = prompt_intro
        self.few_shot = few_shot              # text block, local models only
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.allows_writes = allows_writes
        self.use_launcher = use_launcher

    def tools_for(self, local):
        """The tool-name subset to apply, or None for provider default."""
        if self.tool_names is None:
            return None
        if not local and not self.restrict_cloud:
            return None
        return self.tool_names


SPECIALISTS = {
    "general": SpecialistSpec(
        "general",
        "Everything else — full capability, today's exact behavior.",
    ),

    "revit_data": SpecialistSpec(
        "revit_data",
        "Read-only model questions: counts, lists, schedules, health.",
        tool_names=READ_TOOLS,
        restrict_cloud=True,
        allows_writes=False,
        use_launcher=False,
        max_iterations=6,
        max_tokens=1600,
        prompt_intro=(
            "## Role: DATA specialist (read-only)\n"
            "This request is a QUERY about the model. Answer it with read "
            "tools only — you have no modify tools in this turn. Get the "
            "numbers, then answer compactly. A plain 'có bao nhiêu X?' gets a "
            "one-line total. A 'thống kê / thong ke / statistics / breakdown' "
            "request gets a markdown PIPE TABLE broken down by Type × Level "
            "(group the tool's element list by name × level) with a final "
            "**Tổng** row = the tool's total_count — never just a single "
            "number. If the user actually wants a modification, say so and stop."
        ),
        few_shot=(
            "## Examples\n"
            "User: \"có bao nhiêu bức tường?\" (simple count) -> ai_element_filter "
            "(category Walls), reply \"Model có 128 bức tường.\"\n"
            "User: \"thống kê cửa\" / \"thong ke door\" (breakdown) -> "
            "ai_element_filter (category Doors, limit 1000). When "
            "truncated=false, group elements by (name, level) and reply with a "
            "pipe table:\n"
            "| Loại cửa | Tầng | Số lượng |\n"
            "| --- | --- | --- |\n"
            "| 36\" x 84\" | Level 1 | 40 |\n"
            "| **Tổng** |  | **142** |\n"
            "(the Tổng cell = total_count). If truncated=true, page with offset "
            "until offset+count reaches total_count before tabulating.\n"
            "User: \"liệt kê các sheet A\" -> revit_list_sheets, reply with a "
            "compact table of matching numbers + names."
        ),
    ),

    "revit_action": SpecialistSpec(
        "revit_action",
        "Model modifications: rename, set parameters, tag, move, export.",
        tool_names=ACTION_TOOLS,
        restrict_cloud=False,     # cloud keeps the full registry
        max_iterations=10,
        max_tokens=1500,
        prompt_intro=(
            "## Role: ACTION specialist\n"
            "This request modifies the model. Work in this order: (1) read "
            "tools to resolve the exact target element ids, (2) apply the "
            "change, (3) report WHAT changed — counts + element ids. Never "
            "guess ids; if the target is ambiguous, ask ONE clarifying "
            "question instead of acting. Perform the EXACT action asked: "
            "pin/ghim/khoá and hide/isolate/select are `operate_element` "
            "operations, only tô màu/color/highlight is an override color. "
            "If no tool does what was asked, say so — never do a different "
            "thing to the model and report it as done."
        ),
        few_shot=(
            "## Examples\n"
            "User: \"đổi tên sheet A-101 thành A-102\" -> revit_list_sheets "
            "to find the id -> rename_element -> reply \"Đã đổi tên sheet "
            "A-101 → A-102 (id 12345).\"\n"
            "User: \"tô đỏ tường\" (= color the walls red — the color is "
            "what to APPLY, not a filter value) -> revit_override_color "
            "(category \"Walls\", color \"red\" — ONE call, the server "
            "collects ALL walls itself, no limit) -> reply \"Đã tô đỏ "
            "128 tường trong view hiện tại.\"\n"
            "User: \"pin toàn bộ tường trong view\" (= LOCK the walls — a "
            "pin is NOT a color) -> operate_element (operation \"pin\", "
            "category \"Walls\" — ONE call, no ids) -> reply \"Đã pin 54 "
            "tường trong view.\""
        ),
    ),

    "multi_doc": SpecialistSpec(
        "multi_doc",
        "Work across several open models: list, switch, compare documents.",
        tool_names=MULTIDOC_TOOLS,
        restrict_cloud=False,
        use_launcher=False,
        max_iterations=8,
        max_tokens=1500,
        prompt_intro=(
            "## Role: MULTI-DOCUMENT specialist\n"
            "This request spans MORE THAN ONE open model. Workflow: "
            "(1) `list_open_documents` to see what is open, (2) "
            "`switch_active_document` (exact title) to target a model, "
            "(3) run the reads there, (4) switch to the next model and "
            "repeat, (5) combine the results in ONE final answer (a pipe "
            "table with one column per model works well for comparisons). "
            "Element ids are only valid INSIDE their own document — never "
            "mix ids across models. Say clearly which model each number "
            "comes from. Documents open in a DIFFERENT Revit window "
            "(separate process) are not reachable from this chat — tell "
            "the user to open the assistant in that window instead."
        ),
        few_shot=(
            "## Examples\n"
            "User: \"so sanh so luong tuong giua 2 model dang mo\" -> "
            "list_open_documents -> switch_active_document(A) -> "
            "ai_element_filter(Walls) -> switch_active_document(B) -> "
            "ai_element_filter(Walls) -> reply with a 2-column table."
        ),
    ),

    "modeling": SpecialistSpec(
        "modeling",
        "Build geometry: levels, grids, walls, floors (image/text to model).",
        tool_names=MODELING_TOOLS,
        restrict_cloud=False,
        max_iterations=14,
        max_tokens=1600,
        prompt_intro=(
            "## Role: MODELING specialist\n"
            "This request BUILDS geometry. Units are METERS. Build order: "
            "levels -> grids -> walls -> floors/roof -> openings/furniture. "
            "Confirm key dimensions BEFORE creating anything (one compact "
            "table); after confirmation act immediately with tool calls — "
            "batch per storey and report created element ids per step. "
            "Never 'model' with text notes; missing families are listed "
            "for the user, never guessed."
        ),
    ),

    "qa_check": SpecialistSpec(
        "qa_check",
        "Model QA: warnings, health, audits, naming, standards compliance.",
        tool_names=QA_TOOLS,
        restrict_cloud=True,
        use_launcher=False,
        max_iterations=8,
        max_tokens=1500,
        prompt_intro=(
            "## Role: QA specialist\n"
            "This request AUDITS the model. Default scope is the ENTIRE "
            "project. Gather the evidence with read tools (warnings, "
            "health, statistics, naming), then report findings grouped by "
            "severity with element ids. You may select or color elements "
            "to highlight problems, but never fix anything without the "
            "user's explicit go-ahead (purge_unused: dry_run first, "
            "always)."
        ),
    ),

    "export": SpecialistSpec(
        "export",
        "Sheet/model exports: PDF, DWG, images — direct or via BatchOut.",
        tool_names=READ_TOOLS | EXPORT_TOOLS,
        restrict_cloud=False,
        max_iterations=6,
        max_tokens=1200,
        prompt_intro=(
            "## Role: EXPORT specialist\n"
            "This request EXPORTS sheets or views. Resolve the sheet set "
            "first (`revit_list_sheets`, filter by the user's prefix), "
            "state count + destination, then export. For complex batch "
            "jobs (naming rules, many formats) `open_t3lab_tool` with "
            "BatchOut is the better answer than many manual calls."
        ),
    ),

    # Pipelines — no AgentLoop tools; listed for dispatcher completeness.
    "knowledge": SpecialistSpec(
        "knowledge",
        "Questions answered from indexed documents (RAG with citations).",
        tool_names=frozenset(),
        use_launcher=False,
        allows_writes=False,
    ),
    "comment": SpecialistSpec(
        "comment",
        "PDF markup-comment resolution workflow.",
        tool_names=frozenset(),
        use_launcher=False,
    ),
}


def get_spec(name):
    """Spec by name; unknown names return the general spec."""
    return SPECIALISTS.get(name) or SPECIALISTS["general"]


def build_specialist_prompt(spec, revit_context='', project_instructions='',
                            skills_block='', local=False, lang='auto'):
    """Base agent prompt + specialist role + (local-only) few-shot +
    project instructions + active-skill block.

    Everything assembled here is STATIC for the session, which is what lets
    the whole block sit behind one prompt-cache breakpoint. `revit_context` is
    accepted (old callers pass it positionally) but no longer used: live state
    travels with the user turn via agent_loop.build_context_block().

    The per-message language analysis does NOT belong here for the same
    reason: it changes every turn, so putting it in this block would break the
    cache for the whole static prefix. It rides with the user turn instead —
    see `build_context_block(analysis_hint=...)` in agent_loop.

    lang ('auto' | 'vi' | 'en') is passed straight through to the base prompt
    so a specialist turn answers in the same language as the rest of the UI.
    """
    from Intelligence.agent_loop import (build_agent_system_prompt,
                                         teacher_exemplar_block)
    # local= is deliberately NOT forwarded: a specialist turn uses its own
    # few-shot (appended below), not the general-path one.
    parts = [build_agent_system_prompt(lang=lang)]
    if spec is not None and spec.prompt_intro:
        parts.append(spec.prompt_intro)
    if local and spec is not None and spec.few_shot:
        parts.append(spec.few_shot)
    if local:
        # Portable teacher exemplars (git-tracked). build_agent_system_prompt was
        # called WITHOUT local= above, so they are added here for specialist turns
        # too — with no double-add on the general path.
        ex_block = teacher_exemplar_block().strip()
        if ex_block:
            parts.append(ex_block)
    if project_instructions:
        parts.append("## Project instructions\n" + project_instructions)
    if skills_block:
        parts.append(skills_block)
    return "\n\n".join(parts)
