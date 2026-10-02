# -*- coding: utf-8 -*-
"""
Background Scout
----------------
Collects current Revit context for the AI Agent to avoid redundant questions.

Author: Tran Tien Thanh
"""

import re
import threading
import time

from pyrevit import revit, DB


def _eid_value(element_id):
    """Return the integer value of an ElementId, version-safe.

    Revit 2024+ replaced ElementId.IntegerValue with ElementId.Value (Int64).
    Falls back to IntegerValue for older Revit versions.
    """
    try:
        return element_id.Value          # Revit 2024+
    except AttributeError:
        return element_id.IntegerValue   # Revit 2023 and earlier


# ─── Levels + grids (cached per document) ─────────────────────────────────────
# The datums every placement / filter / dimension request leans on. Without
# them in the live context the agent spent its first round-trip of almost any
# "on Level 2" / "between grids A and C" request calling list_levels. They
# change rarely, so they are read ONCE per document and kept: the cache is
# keyed by document (switching models re-reads), dropped when the assistant
# itself changes the model (invalidate_datums), and re-read after a TTL so an
# edit the user makes by hand in Revit still shows up. A turn inside the TTL
# costs nothing at all.

_FT_TO_M = 0.3048

#: Most levels / grids listed by name in the summary (the count is always
#: exact). Thirty grid names cover an ordinary building; a campus model with
#: hundreds would otherwise put a wall of names in front of every turn.
MAX_LEVELS_IN_SUMMARY = 40
MAX_GRIDS_IN_SUMMARY = 30

#: Seconds a document's digest stays fresh without an explicit invalidation.
DATUM_TTL_SEC = 300.0

#: Documents remembered at once (one entry per open model the user visits).
_MAX_CACHED_DOCS = 8

_datum_cache = {}          # doc key -> {"at", "levels", "grids", "dirty"}
_datum_lock = threading.Lock()


def _doc_identity(doc):
    """A key for one open document: its path, else its title (unsaved)."""
    try:
        path = doc.PathName or u""
    except Exception:
        path = u""
    try:
        title = doc.Title or u""
    except Exception:
        title = u""
    return u"{}|{}".format(path, title)


def _natural_key(text):
    """Sort "2" before "10" and "A" before "B" — grid names are both."""
    parts = re.split(r'(\d+)', u"{}".format(text or u""))
    return [(0, int(p), u"") if p.isdigit() else (1, 0, p.lower())
            for p in parts if p != u""]


def collect_levels_and_grids(doc):
    """([(level name, elevation in m)], [grid name]) for `doc`.

    Read-only: two collectors, no transaction. Elevations are converted from
    Revit's internal feet to METRES — the unit every assistant tool speaks —
    the same way the list_levels tool reports them.
    """
    levels = []
    for lv in DB.FilteredElementCollector(doc).OfClass(DB.Level):
        try:
            levels.append((lv.Name, round(float(lv.Elevation) * _FT_TO_M, 3)))
        except Exception:
            pass
    levels.sort(key=lambda item: (item[1], _natural_key(item[0])))
    grids = []
    for g in DB.FilteredElementCollector(doc).OfClass(DB.Grid):
        try:
            grids.append(g.Name)
        except Exception:
            pass
    grids.sort(key=_natural_key)
    return levels, grids


def _fmt_elevation(metres):
    text = u"{:.3f}".format(metres).rstrip(u"0").rstrip(u".")
    if text in (u"-0", u""):
        text = u"0"
    return text


def format_levels_and_grids(levels, grids, max_levels=None, max_grids=None):
    """Compact summary lines (no trailing newline), or u"" when the model has
    neither. The count is exact even when the name list is cut short."""
    max_levels = MAX_LEVELS_IN_SUMMARY if max_levels is None else max_levels
    max_grids = MAX_GRIDS_IN_SUMMARY if max_grids is None else max_grids
    lines = []
    if levels:
        shown = [u"{} ({} m)".format(name, _fmt_elevation(elev))
                 for name, elev in levels[:max_levels]]
        more = len(levels) - len(shown)
        lines.append(u"- Levels ({}, by elevation): {}{}".format(
            len(levels), u", ".join(shown),
            u", +{} more".format(more) if more > 0 else u""))
    if grids:
        shown = [u"{}".format(n) for n in grids[:max_grids]]
        more = len(grids) - len(shown)
        lines.append(u"- Grids ({}): {}{}".format(
            len(grids), u", ".join(shown),
            u", +{} more".format(more) if more > 0 else u""))
    return u"\n".join(lines)


def get_levels_and_grids(doc, now=None):
    """Cached collect_levels_and_grids(doc). Never raises — ([], []) when the
    document cannot be read."""
    if doc is None:
        return [], []
    key = _doc_identity(doc)
    now = time.time() if now is None else now
    with _datum_lock:
        entry = _datum_cache.get(key)
        if (entry is not None and not entry["dirty"]
                and (now - entry["at"]) < DATUM_TTL_SEC):
            return list(entry["levels"]), list(entry["grids"])
    try:
        levels, grids = collect_levels_and_grids(doc)
    except Exception:
        return [], []
    with _datum_lock:
        if key not in _datum_cache and len(_datum_cache) >= _MAX_CACHED_DOCS:
            oldest = min(_datum_cache, key=lambda k: _datum_cache[k]["at"])
            _datum_cache.pop(oldest, None)
        _datum_cache[key] = {"at": now, "levels": list(levels),
                             "grids": list(grids), "dirty": False}
    return list(levels), list(grids)


def invalidate_datums(doc=None):
    """Mark the cached digest stale — one document, or all of them. Called
    after the assistant runs a model-modifying tool (it may have just created
    a level or a grid)."""
    with _datum_lock:
        if doc is None:
            for entry in _datum_cache.values():
                entry["dirty"] = True
            return
        entry = _datum_cache.get(_doc_identity(doc))
        if entry is not None:
            entry["dirty"] = True


class ContextScout:
    """Specialized module for rapid context gathering from the active Revit session."""

    @staticmethod
    def invalidate_datums(doc=None):
        """See the module-level invalidate_datums()."""
        invalidate_datums(doc)

    @staticmethod
    def get_active_context():
        """Returns a dictionary containing the current state of the Revit document."""
        doc = revit.doc
        if not doc:
            return {"error": "No active document"}

        # 1. Project Information
        proj_info = doc.ProjectInformation
        
        # 2. View Context
        active_view = doc.ActiveView
        scale_val = "1/{}".format(active_view.Scale) if (active_view and hasattr(active_view, "Scale")) else "Unknown"
        discipline_val = str(active_view.Discipline) if (active_view and hasattr(active_view, "Discipline")) else "Unknown"
        
        # 3. Selection Context
        uidoc = revit.uidoc
        selection_ids = []
        selection_details = []
        if uidoc:
            sel_ids = uidoc.Selection.GetElementIds()
            selection_ids = [_eid_value(e) for e in sel_ids]
            for eid in sel_ids:
                if len(selection_details) >= 5:
                    break
                elem = doc.GetElement(eid)
                if elem:
                    cat_name = elem.Category.Name if elem.Category else "Unknown"
                    elem_name = elem.Name if hasattr(elem, "Name") else str(elem)
                    selection_details.append({
                        "id": _eid_value(eid),
                        "name": elem_name,
                        "category": cat_name
                    })
        
        # Heuristic for Region
        address = (proj_info.Address or "").lower()
        title = (doc.Title or "").lower()
        region = "Unknown"
        if any(kw in address or kw in title for kw in ["singapore", "sgp", "jurong", "changi"]):
            region = "Singapore"
        elif any(kw in address or kw in title for kw in ["vietnam", "vn", "hà nội", "hcm", "việt nam"]):
            region = "Vietnam"

        context = {
            "project": {
                "title": doc.Title,
                "name": proj_info.Name,
                "number": proj_info.Number,
                "region": region
            },
            "active_view": {
                "name": active_view.Name if active_view else "None",
                "type": str(active_view.ViewType) if active_view else "None",
                "id": _eid_value(active_view.Id) if active_view else 0,
                "scale": scale_val,
                "discipline": discipline_val
            },
            "selection": {
                "count": len(selection_ids),
                "ids": selection_ids[:50],  # Cap at 50 IDs to avoid massive JSON
                "details": selection_details
            },
            "revit": {
                "version": doc.Application.VersionNumber,
                "language": str(doc.Application.Language)
            }
        }

        # Levels + grids: cached per document, so this costs nothing per turn.
        levels, grids = get_levels_and_grids(doc)
        context["datums"] = {"levels": levels, "grids": grids}

        return context

    @staticmethod
    def get_context_summary_for_ai():
        """Returns a concise string summary for inclusion in AI prompts."""
        ctx = ContextScout.get_active_context()
        if "error" in ctx: return "No Revit document is currently open."
        
        summary = (
            "Current Context:\n"
            "- Project: {title} ({region})\n"
            "- Active View: {view_name} ({view_type}, Scale: {scale}, Discipline: {discipline})\n"
            "- Selected Elements: {sel_count} items\n"
        ).format(
            title=ctx["project"]["title"],
            region=ctx["project"]["region"],
            view_name=ctx["active_view"]["name"],
            view_type=ctx["active_view"]["type"],
            scale=ctx["active_view"]["scale"],
            discipline=ctx["active_view"]["discipline"],
            sel_count=ctx["selection"]["count"]
        )
        
        if ctx["selection"]["details"]:
            summary += "Selected items details:\n"
            for d in ctx["selection"]["details"]:
                summary += "  * {} ({}) [ID: {}]\n".format(d["name"], d["category"], d["id"])

        datums = ctx.get("datums") or {}
        datum_lines = format_levels_and_grids(datums.get("levels") or [],
                                              datums.get("grids") or [])
        if datum_lines:
            summary += datum_lines + "\n"

        return summary
