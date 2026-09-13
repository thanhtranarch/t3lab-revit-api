# -*- coding: utf-8 -*-
"""JoinService — Headless service for rule-based element joining, unjoining, and join-order management."""

import os
import sys
import json

from pyrevit import revit, script
from Snippets._compat import eid_value

try:
    import clr
    for _r in ('PresentationFramework', 'PresentationCore', 'WindowsBase', 'System'):
        try:
            clr.AddReference(_r)
        except Exception:
            pass
except Exception:
    clr = None

try:
    from Autodesk.Revit.DB import (
        Transaction,
        TransactionStatus,
        FilteredElementCollector,
        BuiltInCategory,
        JoinGeometryUtils,
        BoundingBoxIntersectsFilter,
        Outline,
        IFailuresPreprocessor,
        FailureProcessingResult,
        FailureSeverity,
    )
except Exception:
    Transaction = TransactionStatus = FilteredElementCollector = BuiltInCategory = JoinGeometryUtils = None
    BoundingBoxIntersectsFilter = Outline = IFailuresPreprocessor = FailureProcessingResult = FailureSeverity = None

logger = script.get_logger()
JOIN_CANCELLED_MESSAGE = (
    "Cancelled by user; completed changes were committed. Use Undo to revert this run."
)

# ==================================================
# CATEGORY DEFINITIONS
# ==================================================

JOINABLE_CATEGORIES = {
    "Walls":                  BuiltInCategory.OST_Walls,
    "Floors":                 BuiltInCategory.OST_Floors,
    "Structural Columns":     BuiltInCategory.OST_StructuralColumns,
    "Columns":                BuiltInCategory.OST_Columns,
    "Structural Framing":     BuiltInCategory.OST_StructuralFraming,
    "Structural Foundations": BuiltInCategory.OST_StructuralFoundation,
    "Roofs":                  BuiltInCategory.OST_Roofs,
    "Ceilings":               BuiltInCategory.OST_Ceilings,
    "Generic Models":         BuiltInCategory.OST_GenericModel,
}

BIC_INT_TO_NAME = {int(bic): name for name, bic in JOINABLE_CATEGORIES.items()}
CATEGORY_NAMES = sorted(JOINABLE_CATEGORIES.keys())

DEFAULT_RULES = [
    {"priority": "Walls",               "join_with": "Walls"},
    {"priority": "Floors",              "join_with": "Walls"},
    {"priority": "Structural Columns",  "join_with": "Walls"},
    {"priority": "Structural Columns",  "join_with": "Floors"},
    {"priority": "Structural Framing",  "join_with": "Walls"},
    {"priority": "Structural Framing",  "join_with": "Floors"},
    {"priority": "Columns",             "join_with": "Walls"},
]


class JoinFailuresPreprocessor(IFailuresPreprocessor):
    __namespace__ = "T3Lab.JoinServiceFailures"

    def PreprocessFailures(self, failuresAccessor):
        for f in failuresAccessor.GetFailureMessages():
            sev = f.GetSeverity()
            if sev == FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(f)
            elif sev == FailureSeverity.Error and f.HasResolutions():
                try:
                    failuresAccessor.ResolveFailure(f)
                except Exception:
                    pass
        return FailureProcessingResult.Continue


def _design_option_id(el):
    """Return the eid_value of el's Design Option, or None."""
    try:
        do = el.DesignOption
    except Exception:
        do = None
    return eid_value(do.Id) if do else None


def _same_design_option(el, cand):
    """Elements can only join if they belong to the same Design Option or main model."""
    return _design_option_id(el) == _design_option_id(cand)


def _collect_elements(doc, bic, scope, view_id=None, selected_ids=None):
    """Collect elements of a BuiltInCategory based on scope."""
    if scope == "Selected Elements" and selected_ids:
        all_els = FilteredElementCollector(doc)\
            .OfCategory(bic)\
            .WhereElementIsNotElementType()\
            .ToElements()
        id_set = set(eid_value(eid) for eid in selected_ids)
        return [el for el in all_els if eid_value(el.Id) in id_set]
    elif scope == "Active View" and view_id:
        return list(
            FilteredElementCollector(doc, view_id)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    else:
        return list(
            FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .ToElements()
        )


def _get_intersecting_elements(doc, el, target_bic, scope, view_id=None):
    """Find elements of target_bic whose bounding boxes intersect el."""
    bb = el.get_BoundingBox(None)
    if not bb:
        return []

    try:
        outline = Outline(bb.Min, bb.Max)
        bb_filter = BoundingBoxIntersectsFilter(outline)
    except Exception:
        return []

    try:
        if scope == "Active View" and view_id:
            collector = FilteredElementCollector(doc, view_id)
        else:
            collector = FilteredElementCollector(doc)

        candidates = collector\
            .OfCategory(target_bic)\
            .WherePasses(bb_filter)\
            .WhereElementIsNotElementType()\
            .ToElements()
        return [c for c in candidates if eid_value(c.Id) != eid_value(el.Id)]
    except Exception:
        return []


def _commit_join(transaction):
    status = transaction.Commit()
    if status != TransactionStatus.Committed:
        raise RuntimeError("Join transaction was not committed: {}. "
                           "Resolve any Revit failure dialog before running again.".format(status))


def run_join(rules, scope="Active View", mode="Join", switch_order=False,
             progress_callback=None, cancel_check=None, doc=None, uidoc=None):
    """Execute join/unjoin operations based on rules."""
    document = doc or revit.doc
    ui_doc = uidoc
    if ui_doc is None:
        try:
            ui_doc = revit.uidoc
        except Exception:
            ui_doc = None

    if not document:
        return (0, 0, 0, "No active Revit document found.")

    view_id = document.ActiveView.Id if scope == "Active View" and document.ActiveView else None
    selected_ids = None
    if scope == "Selected Elements":
        if not ui_doc:
            return (0, 0, 0, "No active UI document.")
        sel = ui_doc.Selection.GetElementIds()
        if not sel or sel.Count == 0:
            return (0, 0, 0, "No elements selected.")
        selected_ids = list(sel)

    total_joined  = 0
    total_skipped = 0
    total_errors  = 0
    total_rules = len(rules)

    t = None
    try:
        t = Transaction(document, "T3Lab: Auto {} Elements".format(mode))
        t.Start()
        fho = t.GetFailureHandlingOptions()
        fho.SetFailuresPreprocessor(JoinFailuresPreprocessor())
        # This service returns a final count synchronously. Revit must finish
        # failure processing before that count can describe saved changes.
        fho.SetForcedModalHandling(True)
        t.SetFailureHandlingOptions(fho)
        for rule_idx, rule in enumerate(rules):
            priority_name = rule.get("priority", "")
            joinwith_name = rule.get("join_with", "")

            priority_bic = JOINABLE_CATEGORIES.get(priority_name)
            joinwith_bic = JOINABLE_CATEGORIES.get(joinwith_name)

            if not priority_bic or not joinwith_bic:
                total_errors += 1
                continue

            if progress_callback:
                progress_callback(
                    rule_idx, total_rules,
                    "Processing: {} → {} ...".format(priority_name, joinwith_name)
                )

            priority_elements = _collect_elements(
                document, priority_bic, scope, view_id, selected_ids
            )

            processed_pairs = set()

            for el in priority_elements:
                if cancel_check and cancel_check():
                    _commit_join(t)
                    return (total_joined, total_skipped, total_errors,
                            JOIN_CANCELLED_MESSAGE)

                candidates = _get_intersecting_elements(
                    document, el, joinwith_bic, scope, view_id
                )

                for cand in candidates:
                    if not _same_design_option(el, cand):
                        total_skipped += 1
                        continue

                    pair_key = (
                        min(eid_value(el.Id), eid_value(cand.Id)),
                        max(eid_value(el.Id), eid_value(cand.Id)),
                    )
                    if pair_key in processed_pairs:
                        continue
                    processed_pairs.add(pair_key)

                    try:
                        are_joined = JoinGeometryUtils.AreElementsJoined(document, el, cand)

                        if mode == "Join":
                            if not are_joined:
                                JoinGeometryUtils.JoinGeometry(document, el, cand)
                                total_joined += 1
                            else:
                                total_skipped += 1

                            if switch_order and JoinGeometryUtils.AreElementsJoined(document, el, cand):
                                if not JoinGeometryUtils.IsCuttingElementInJoin(document, el, cand):
                                    JoinGeometryUtils.SwitchJoinOrder(document, el, cand)

                        elif mode == "Unjoin":
                            if are_joined:
                                JoinGeometryUtils.UnjoinGeometry(document, el, cand)
                                total_joined += 1
                            else:
                                total_skipped += 1

                    except Exception as ex:
                        logger.debug("Join operation error: {}".format(ex))
                        total_errors += 1

        _commit_join(t)
        return (total_joined, total_skipped, total_errors, None)

    except Exception as ex:
        if t is not None and t.GetStatus() == TransactionStatus.Started:
            t.RollBack()
        # Attempted joins are not saved joins after rollback or while Pending.
        return (0, total_skipped, total_errors + 1, str(ex))
