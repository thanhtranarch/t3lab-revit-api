# -*- coding: utf-8 -*-
"""
Tekla Bridge - the data and launch helpers behind the "Tekla Bridge" pushbutton.

One JSON file (``lib/data/tekla_bridge.json``) maps a Tekla Structures command
name to the Revit command or T3Lab tool that does the same job, with a one-line
note on what is different. The same file feeds ``docs/tekla-to-revit-2027.md``
and ``lib/data/KeyboardShortcuts_Tekla.xml`` (``dev/build_tekla_docs.py``), so
the tool and the documentation cannot drift apart.

The module is split in two halves:

* PURE  - loading, validating, searching and describing rows. No Revit import,
          so ``dev/test_tekla_bridge.py`` runs it anywhere.
* REVIT - ``post_revit_command`` and ``run_tool``. Revit is imported inside the
          function that needs it, never at module level. Only
          ``UIApplication.CanPostCommand`` / ``PostCommand`` are used - no
          transaction, no document (the tool must open with no model loaded).

Author: Tran Tien Thanh
"""

__author__ = "Tran Tien Thanh"
__title__ = "Tekla Bridge"

import importlib
import json
import os
import re

# lib/Snippets/_tekla_bridge.py -> lib
LIB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(LIB_DIR, "data", "tekla_bridge.json")
# lib -> T3Lab.extension -> repository root (docs/ ships next to the extension)
GUIDE_FILE = os.path.join(os.path.dirname(os.path.dirname(LIB_DIR)),
                          "docs", "tekla-to-revit-2027.md")

GROUPS = [("model", "Model & cast unit"),
          ("rebar", "Rebar"),
          ("numbering", "Numbering"),
          ("drawing", "Drawings"),
          ("report", "Reports & export"),
          ("env", "Environment & habits")]
GROUP_IDS = [gid for gid, _label in GROUPS]
GROUP_LABELS = dict(GROUPS)

TOOL_ENTRY = {"CastUnit": ("GUI.CastUnitDialog", "show_cast_unit"),
              "CloneDrawing": ("GUI.CloneDrawingDialog", "show_clone_drawing"),
              "RebarCheck": ("GUI.RebarCheckDialog", "show_rebar_check"),
              "BVBSExport": ("GUI.BVBSExportDialog", "show_bvbs_export"),
              "RebarWizard": ("GUI.RebarWizardDialog", "show_rebar_wizard")}

TOOL_NAMES = {"CastUnit": "Cast Unit Manager",
              "CloneDrawing": "Clone Drawing",
              "RebarCheck": "Rebar Check",
              "BVBSExport": "BVBS Export",
              "RebarWizard": "Rebar Wizard"}

LAYER_LABELS = {1: "Revit", 2: "T3Lab", 3: "Revit does it differently"}

# Layer 2 means "a T3Lab tool fills the gap", so a row must name that tool -
# except the row that describes the Bridge itself.
LAYER2_NO_TOOL = ("env.find",)

# Tekla default keys that D4 allows in the shortcut template. Source: Tekla
# Structures 2026 "Default keyboard shortcuts"
# (https://support.tekla.com/doc/tekla-structures/2026/gen_keyboard_shortcuts).
# (key, Tekla command, Revit equivalent). Nothing outside this table may be
# written to a row's ``tekla_shortcut`` - a made-up key is worse than none.
TEKLA_DEFAULT_SHORTCUTS = (
    ("Ctrl+H", "Open Phase manager", "Phases"),
    ("Ctrl+B", "Create report", "Schedule/Quantities"),
    ("Ctrl+G", "Selection filters", "Filters"),
    ("Shift+I", "Inquire object", "Properties palette (no postable command)"),
    ("Ctrl+Q", "Quick Launch", "none - Tekla Bridge itself"),
    ("Ctrl+Shift+C", "Keyboard shortcuts dialog", "Keyboard Shortcuts"),
    ("Ctrl+J", "AutoConnections", "none"),
    ("Alt+Q/W/E", "Rebar selection switches", "none - Revit uses selection filters"),
)
CITED_SHORTCUT_KEYS = tuple(item[0] for item in TEKLA_DEFAULT_SHORTCUTS)

ROW_KEYS = ("id", "group", "order", "tekla", "revit", "layer", "postable", "tool",
            "ribbon_2026", "ribbon_2027", "tip_en", "tip_vi", "doc_en", "doc_vi",
            "keywords", "tekla_shortcut", "revit_command_id")
_TEXT_KEYS = ("tekla", "revit", "tip_en", "tip_vi", "doc_en", "doc_vi")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

ACTION_POST = "post"
ACTION_TOOL = "tool"
ACTION_RIBBON = "ribbon"


# ── PURE: load / validate ────────────────────────────────────────────────

def validate_rows(rows):
    """Problems found in ``rows`` against the schema, as a list of strings.

    An empty list means the data is valid. Checks: every key present, unique
    ids, group and layer in range, order unique inside its group, ``postable``
    a list of identifiers, ``tool`` known, layer 2 names a tool, shortcut keys
    only from the cited Tekla defaults.
    """
    problems = []
    if not isinstance(rows, list) or not rows:
        return ["tekla_bridge.json must hold a non-empty list of rows."]

    seen_ids = set()
    seen_order = set()
    for index, row in enumerate(rows):
        label = "row %d" % index
        if not isinstance(row, dict):
            problems.append("%s is not an object." % label)
            continue
        rid = row.get("id")
        if isinstance(rid, str) and rid:
            label = "%s (%s)" % (label, rid)
        missing = [k for k in ROW_KEYS if k not in row]
        if missing:
            problems.append("%s misses keys: %s." % (label, ", ".join(missing)))
            continue
        extra = [k for k in row if k not in ROW_KEYS]
        if extra:
            problems.append("%s has unknown keys: %s." % (label, ", ".join(sorted(extra))))

        if not isinstance(rid, str) or not rid.strip():
            problems.append("%s has an empty id." % label)
        elif rid in seen_ids:
            problems.append("%s repeats an id." % label)
        else:
            seen_ids.add(rid)

        group = row["group"]
        if group not in GROUP_IDS:
            problems.append("%s has group %r - expected one of %s." % (label, group, GROUP_IDS))
        order = row["order"]
        if isinstance(order, bool) or not isinstance(order, int):
            problems.append("%s has a non-integer order." % label)
        else:
            if (group, order) in seen_order:
                problems.append("%s repeats order %d inside group %s." % (label, order, group))
            seen_order.add((group, order))

        for key in _TEXT_KEYS:
            value = row[key]
            if not isinstance(value, str) or not value.strip():
                problems.append("%s has an empty %s." % (label, key))

        layer = row["layer"]
        if isinstance(layer, bool) or layer not in (1, 2, 3):
            problems.append("%s has layer %r - expected 1, 2 or 3." % (label, layer))

        postable = row["postable"]
        if not isinstance(postable, list) or not all(
                isinstance(n, str) and _IDENTIFIER_RE.match(n) for n in postable):
            problems.append("%s: postable must be a list of PostableCommand names." % label)

        tool = row["tool"]
        if tool is not None and tool not in TOOL_ENTRY:
            problems.append("%s names unknown tool %r - expected one of %s."
                            % (label, tool, sorted(TOOL_ENTRY)))
        if layer == 2 and not tool and rid not in LAYER2_NO_TOOL:
            problems.append("%s is layer 2 but names no tool." % label)

        for key in ("ribbon_2026", "ribbon_2027", "revit_command_id"):
            value = row[key]
            if value is not None and (not isinstance(value, str) or not value.strip()):
                problems.append("%s: %s must be null or a non-empty string." % (label, key))

        keywords = row["keywords"]
        if not isinstance(keywords, list) or not keywords or not all(
                isinstance(k, str) and k.strip() for k in keywords):
            problems.append("%s: keywords must be a non-empty list of strings." % label)

        shortcut = row["tekla_shortcut"]
        if shortcut is not None and shortcut not in CITED_SHORTCUT_KEYS:
            problems.append("%s: tekla_shortcut %r is not a cited Tekla default (%s)."
                            % (label, shortcut, ", ".join(CITED_SHORTCUT_KEYS)))
    return problems


def load_rows(path=None):
    """The validated rows of ``tekla_bridge.json``. Raises ValueError listing bad rows."""
    path = path or DATA_FILE
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get("rows")
    problems = validate_rows(data)
    if problems:
        raise ValueError("%s has %d problem(s):\n- %s"
                         % (os.path.basename(path), len(problems), "\n- ".join(problems)))
    return data


# ── PURE: search / describe ──────────────────────────────────────────────

def ordered(rows):
    """Rows in Bridge order: group order of GROUPS, then ``order``, then file order."""
    position = dict((gid, i) for i, gid in enumerate(GROUP_IDS))
    keyed = [(position.get(r["group"], len(GROUP_IDS)), r["order"], i, r)
             for i, r in enumerate(rows)]
    keyed.sort(key=lambda item: item[:3])
    return [item[3] for item in keyed]


def search(rows, text, group=None):
    """Rows matching ``text`` (case-insensitive), optionally limited to one group.

    Every word of ``text`` must occur in the row's Tekla name, Revit name or
    keywords. Empty text matches all. The order is stable: group, then ``order``.
    """
    words = [w for w in re.split(r"\s+", (text or "").strip().lower()) if w]
    only_group = group if group and group != "all" else None
    found = []
    for row in ordered(rows):
        if only_group and row["group"] != only_group:
            continue
        haystack = " ".join([row["tekla"], row["revit"]] + list(row["keywords"])).lower()
        if all(w in haystack for w in words):
            found.append(row)
    return found


def ribbon_path_for(row, year):
    """Ribbon path to show: the 2027 one on Revit 2027+ when the row has one."""
    try:
        newer = int(year) >= 2027
    except (TypeError, ValueError):
        newer = False
    if newer and row.get("ribbon_2027"):
        return row["ribbon_2027"]
    return row.get("ribbon_2026")


def layer_label(row):
    """Short text for the layer pill: 'Revit' / 'T3Lab' / 'Revit does it differently'."""
    return LAYER_LABELS.get(row.get("layer"), "")


def group_label(group_id):
    return GROUP_LABELS.get(group_id, group_id)


def tool_name(row):
    """Display name of the T3Lab tool a row links to, or ''."""
    return TOOL_NAMES.get(row.get("tool"), "")


def primary_action(row):
    """(kind, label) of the main button for a row. kind is None when nothing opens.

    * a postable command exists on some release -> "post"
    * layer 2 (Revit has nothing)               -> "tool"
    * otherwise a ribbon path to read           -> "ribbon"
    * otherwise the tool, if the row has one    -> "tool"
    """
    has_ribbon = bool(row.get("ribbon_2026") or row.get("ribbon_2027"))
    if row.get("postable"):
        return (ACTION_POST, "Open in Revit")
    if row.get("tool") and row.get("layer") == 2:
        return (ACTION_TOOL, "Open T3Lab tool")
    if has_ribbon:
        return (ACTION_RIBBON, "Show in ribbon")
    if row.get("tool"):
        return (ACTION_TOOL, "Open T3Lab tool")
    return (None, "Nothing to open")


def secondary_action(row):
    """(kind, label) of the second button, or None. Only ever the T3Lab tool."""
    kind, _label = primary_action(row)
    if row.get("tool") and kind != ACTION_TOOL:
        return (ACTION_TOOL, "Open T3Lab tool")
    return None


def summary_text(shown, total, shown_groups=None):
    """Status line: '36 commands · 6 groups' or '12 of 36 commands · 3 groups'."""
    if shown_groups is None:
        shown_groups = len(GROUPS)
    plural = "" if shown_groups == 1 else "s"
    if shown == total:
        return "%d command%s · %d group%s" % (shown, "" if shown == 1 else "s",
                                                   shown_groups, plural)
    return "%d of %d commands · %d group%s" % (shown, total, shown_groups, plural)


def guide_path():
    """Absolute path of the bilingual guide that ships with the repository."""
    return GUIDE_FILE


def ribbon_message(row, year):
    """Text for the 'Show in ribbon' action."""
    path = ribbon_path_for(row, year)
    if path:
        return "Open it from the ribbon: %s." % path
    return ("Revit has no ribbon entry for \"%s\". Check the note in the Bridge window for how Revit handles it."
            % row.get("tekla", ""))


def post_failure_message(row, status, payload, year):
    """Text shown when ``post_revit_command`` did not post (what - where - next)."""
    if status == "no_command":
        path = payload or ribbon_path_for(row, year)
        where = ("Open it from the ribbon: %s." % path if path
                 else "No ribbon path is recorded for it; look it up in Revit's own ribbon.")
        version = "Revit %s" % year if year else "Revit"
        return "%s has no postable command for \"%s\". %s" % (version, row.get("tekla", ""), where)
    if status == "cannot_post":
        return ("Revit could not start \"%s\": %s Try again after selecting the elements it needs "
                "or opening a suitable view." % (row.get("tekla", ""), payload or ""))
    return ""


# ── PURE: remembered tips ────────────────────────────────────────────────

def seen_tips_path():
    """%APPDATA%\\T3LabAI\\tekla_bridge.json - which tips were shown, and the tip-once setting."""
    from core.paths import user_data_path
    return user_data_path("tekla_bridge.json")


def _read_state(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(path, state):
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return True
    except OSError:
        return False


def load_seen(path):
    """Set of row ids whose tip was already shown before opening."""
    seen = _read_state(path).get("seen_tips")
    return set(s for s in seen if isinstance(s, str)) if isinstance(seen, list) else set()


def mark_seen(path, row_id):
    """Remember that the tip of ``row_id`` was shown. Returns False when the file cannot be written."""
    state = _read_state(path)
    seen = load_seen(path)
    seen.add(row_id)
    state["seen_tips"] = sorted(seen)
    return _write_state(path, state)


def load_tip_once(path):
    """The 'show this tip before opening, first time only' setting (default off)."""
    return bool(_read_state(path).get("tip_once", False))


def save_tip_once(path, enabled):
    state = _read_state(path)
    state["tip_once"] = bool(enabled)
    return _write_state(path, state)


# ── REVIT ────────────────────────────────────────────────────────────────

def _short_error(exc):
    """First line of an exception message (delegates to _compat once WP1 is in)."""
    try:
        from Snippets import _compat
        fn = getattr(_compat, "short_error", None)
        if fn:
            return fn(exc)
    except Exception:
        pass
    text = ""
    try:
        text = str(getattr(exc, "Message", None) or exc or "")
    except Exception:
        text = ""
    lines = text.strip().splitlines()
    return lines[0] if lines else exc.__class__.__name__


def _lookup_postable(names):
    """RevitCommandId of the first PostableCommand in ``names`` this release has, or None."""
    try:
        from Snippets import _compat
        fn = getattr(_compat, "postable_command_id", None)
    except Exception:
        fn = None
    if fn:
        try:
            return fn(list(names))
        except Exception:
            return None
    # WP1 not merged yet: same lookup, local copy.
    try:
        from Autodesk.Revit.UI import PostableCommand, RevitCommandId
    except Exception:
        return None
    for name in names or ():
        try:
            member = getattr(PostableCommand, name, None)
            if member is None:
                continue
            command_id = RevitCommandId.LookupPostableCommandId(member)
            if command_id is not None:
                return command_id
        except Exception:
            continue
    return None


def _lookup_by_id_name(command_id_name):
    """RevitCommandId from a captured RevitCommandId.Name string (spike G10), or None."""
    if not command_id_name:
        return None
    try:
        from Autodesk.Revit.UI import RevitCommandId
        return RevitCommandId.LookupCommandId(command_id_name)
    except Exception:
        return None


def revit_year(uiapp=None):
    """Revit release year as an int; never raises."""
    try:
        return int(uiapp.Application.VersionNumber)
    except Exception:
        pass
    try:
        from Snippets._host import get_revit_version
        # default=0: an unknown release must read as "unknown", not as the 2023 fallback
        return int(get_revit_version(default=0))
    except Exception:
        return 0


def post_revit_command(uiapp, row, year=None):
    """Ask Revit to run the command behind ``row``.

    Returns ``("posted", name)`` | ``("no_command", ribbon_path)`` |
    ``("cannot_post", reason)``. Revit runs a posted command only after the
    current pyRevit command returns, so call this AFTER the Bridge window has
    closed.
    """
    year = year or revit_year(uiapp)
    ribbon = ribbon_path_for(row, year)
    if uiapp is None:
        return ("cannot_post", "The Revit application is not available to this script.")

    command_id = _lookup_postable(row.get("postable") or [])
    if command_id is None:
        command_id = _lookup_by_id_name(row.get("revit_command_id"))
    if command_id is None:
        return ("no_command", ribbon)

    try:
        if not uiapp.CanPostCommand(command_id):
            return ("cannot_post", "Revit cannot run it in the current state.")
        uiapp.PostCommand(command_id)
    except Exception as exc:
        return ("cannot_post", _short_error(exc))
    return ("posted", getattr(command_id, "Name", None) or row.get("tekla", ""))


def run_tool(row, doc):
    """Open the T3Lab tool of ``row``. Returns user-facing error text, or None on success."""
    key = row.get("tool")
    entry = TOOL_ENTRY.get(key)
    if not entry:
        return ("No T3Lab tool is linked to \"%s\". Use the ribbon path shown in the Bridge window."
                % row.get("tekla", ""))
    name = TOOL_NAMES[key]
    if doc is None:
        return "Open a Revit project before running %s." % name
    if getattr(doc, "IsFamilyDocument", False):
        return "%s works on projects, not on family documents." % name

    module_name, function_name = entry
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return ("%s is not available in this install (%s could not be imported). "
                "Update the T3Lab extension and restart Revit." % (name, module_name))
    function = getattr(module, function_name, None)
    if not callable(function):
        return ("%s has no entry point %s in %s. Update the T3Lab extension and restart Revit."
                % (name, function_name, module_name))
    try:
        function(doc)
    except Exception as exc:
        return ("%s stopped with an error: %s. Check the latest Revit journal for the stack trace, then try again."
                % (name, _short_error(exc)))
    return None
