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
        Options,
        ViewDetailLevel,
        Solid,
        GeometryInstance,
        GeometryElement,
        BooleanOperationsUtils,
        BooleanOperationsType,
    )
except Exception:
    Transaction = TransactionStatus = FilteredElementCollector = BuiltInCategory = JoinGeometryUtils = None
    BoundingBoxIntersectsFilter = Outline = IFailuresPreprocessor = FailureProcessingResult = FailureSeverity = None
    Options = ViewDetailLevel = Solid = GeometryInstance = GeometryElement = None
    BooleanOperationsUtils = BooleanOperationsType = None

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


# ==================================================
# EMBEDDED ELEMENTS — keep an element that sits inside another one visible
# ==================================================
# Một join luôn có một bên CẮT và một bên BỊ CẮT. Nếu bên bị cắt nằm trọn trong
# bên cắt (dầm chìm trong sàn, cột nằm trong tường, tường ngắn trong tường dày)
# thì Revit khoét mất toàn bộ hình của nó — element vẫn còn trong model nhưng
# biến mất khỏi mọi view. Bảo vệ: element nằm trong luôn là bên CẮT, đè lên thứ
# tự của rule.

# An element counts as embedded when at least this share of its volume lies
# inside the other one — a column that pokes out by a modelling sliver still counts.
EMBEDDED_RATIO = 0.98
# Bounding-box slack for the cheap pre-check (feet, about 6 mm).
BBOX_TOL = 0.02
# Solid volume at or below this (cubic feet) means nothing is left to draw.
MIN_VOLUME = 1e-6


class EmbedCheck(object):
    """Verdict on one pair: which element (if any) lies inside the other.

    inner     — the embedded element, which must be the cutting one.
    duplicate — both lie inside each other (an exact overlap): a join would
                hide one of them whichever way round it goes.
    certain   — False when the geometry could not settle it; the caller then
                measures the cut element after the join instead.
    volumes   — pre-join volumes by element id, kept for that measurement.
    """
    def __init__(self, inner=None, duplicate=False, certain=True, volumes=None):
        self.inner = inner
        self.duplicate = duplicate
        self.certain = certain
        self.volumes = volumes or {}


def _box_inside(inner, outer, tol=BBOX_TOL):
    """True when box `inner` fits inside box `outer` ((min xyz), (max xyz)).

    A missing box means "unknown", so the expensive check still runs.
    """
    if inner is None or outer is None:
        return True
    (imin, imax), (omin, omax) = inner, outer
    return all(imin[i] >= omin[i] - tol and imax[i] <= omax[i] + tol for i in range(3))


def _verdict_from_ratios(a, b, ratio_a_in_b, ratio_b_in_a, threshold=EMBEDDED_RATIO):
    """Turn the two "share of my volume inside the other" ratios into a verdict."""
    a_inside = ratio_a_in_b >= threshold
    b_inside = ratio_b_in_a >= threshold
    if a_inside and b_inside:
        return EmbedCheck(duplicate=True)
    if a_inside:
        return EmbedCheck(inner=a)
    if b_inside:
        return EmbedCheck(inner=b)
    return EmbedCheck()


def _walk_solids(geometry, depth=0):
    """Yield every Solid in a GeometryElement, recursing into family instances."""
    if geometry is None or depth > 4:
        return
    for obj in geometry:
        try:
            if isinstance(obj, Solid):
                yield obj
            elif isinstance(obj, GeometryInstance):
                for solid in _walk_solids(obj.GetInstanceGeometry(), depth + 1):
                    yield solid
            elif isinstance(obj, GeometryElement):
                for solid in _walk_solids(obj, depth + 1):
                    yield solid
        except Exception:
            continue


def _element_solids(el):
    """Solids of el as Revit draws it now (after the joins it already has)."""
    try:
        opts = Options()
        opts.ComputeReferences = False
        opts.DetailLevel = ViewDetailLevel.Fine
        geometry = el.get_Geometry(opts)
    except Exception:
        return []
    solids = []
    for solid in _walk_solids(geometry):
        try:
            if solid.Volume > MIN_VOLUME:
                solids.append(solid)
        except Exception:
            continue
    return solids


def _solid_volume(el):
    return sum(s.Volume for s in _element_solids(el))


def _inside_ratio(inner, outer):
    """Share of inner's volume that lies inside outer (0..1). Raises if Revit's
    boolean fails, so the caller can fall back to measuring after the join."""
    inner_solids = _element_solids(inner)
    total = sum(s.Volume for s in inner_solids)
    if total <= MIN_VOLUME:
        return 0.0
    outer_solids = _element_solids(outer)
    shared = 0.0
    for a in inner_solids:
        for b in outer_solids:
            common = BooleanOperationsUtils.ExecuteBooleanOperation(
                a, b, BooleanOperationsType.Intersect)
            if common is not None:
                shared += common.Volume
    return min(shared / total, 1.0)


class EmbedState(object):
    """Per-run memory: boxes and volumes as they were when first read, and one
    verdict per pair so a pair met again by a later rule keeps the same order."""

    def __init__(self):
        self._boxes = {}
        self._volumes = {}
        self.verdicts = {}
        self.protected = set()
        self.duplicates = set()

    def box(self, el):
        key = eid_value(el.Id)
        if key not in self._boxes:
            try:
                bb = el.get_BoundingBox(None)
                self._boxes[key] = ((bb.Min.X, bb.Min.Y, bb.Min.Z),
                                    (bb.Max.X, bb.Max.Y, bb.Max.Z)) if bb else None
            except Exception:
                self._boxes[key] = None
        return self._boxes[key]

    def volume(self, el):
        key = eid_value(el.Id)
        if key not in self._volumes:
            self._volumes[key] = _solid_volume(el)
        return self._volumes[key]

    def check(self, document, el, cand, joined, pair_key):
        if pair_key not in self.verdicts:
            self.verdicts[pair_key] = _find_embedded(document, el, cand, joined, self)
        return self.verdicts[pair_key]


def _find_embedded(document, a, b, joined, state):
    """Decide whether a or b lies inside the other, before this run changes them."""
    a_in_b = _box_inside(state.box(a), state.box(b))
    b_in_a = _box_inside(state.box(b), state.box(a))

    if joined:
        # Revit has already cut one of them. A cut element with nothing left
        # to draw was swallowed whole: that is the element to bring back.
        try:
            a_cuts = JoinGeometryUtils.IsCuttingElementInJoin(document, a, b)
        except Exception:
            return EmbedCheck()
        cutter, cut = (a, b) if a_cuts else (b, a)
        if state.volume(cut) <= MIN_VOLUME:
            return EmbedCheck(inner=cut)
        # The cut element still shows, so it is not inside the cutter. Whether
        # the cutter sits inside the cut one cannot be read from a shape that
        # already has the cutter's hole in it — measure after any switch.
        cutter_in_cut = a_in_b if a_cuts else b_in_a
        if not cutter_in_cut:
            return EmbedCheck()
        return EmbedCheck(certain=False,
                          volumes={eid_value(cutter.Id): state.volume(cutter)})

    if not (a_in_b or b_in_a):
        return EmbedCheck()
    try:
        ratio_a = _inside_ratio(a, b) if a_in_b else 0.0
        ratio_b = _inside_ratio(b, a) if b_in_a else 0.0
    except Exception:
        return EmbedCheck(certain=False,
                          volumes={eid_value(a.Id): state.volume(a),
                                   eid_value(b.Id): state.volume(b)})
    return _verdict_from_ratios(a, b, ratio_a, ratio_b)


def _apply_join_order(document, el, cand, switch_order, inner):
    """Make the right element of a joined pair the cutting one.

    The embedded element wins; otherwise the rule's priority element cuts when
    switch_order is on; otherwise Revit's own order is left alone.
    Returns True when the order was switched.
    """
    cutter = inner if inner is not None else (el if switch_order else None)
    if cutter is None:
        return False
    other = cand if cutter is el else el
    if JoinGeometryUtils.IsCuttingElementInJoin(document, cutter, other):
        return False
    JoinGeometryUtils.SwitchJoinOrder(document, cutter, other)
    return True


def _rescue_swallowed(document, el, cand, volumes):
    """After a join or switch the geometry could not predict: regenerate, and if
    the element now being cut has (almost) nothing left, make it the cutter.
    Returns the rescued element, or None."""
    try:
        document.Regenerate()
    except Exception:
        return None
    cut = cand if JoinGeometryUtils.IsCuttingElementInJoin(document, el, cand) else el
    before = volumes.get(eid_value(cut.Id), 0.0)
    if before <= MIN_VOLUME:
        return None
    if _solid_volume(cut) > before * (1.0 - EMBEDDED_RATIO):
        return None
    other = el if cut is cand else cand
    JoinGeometryUtils.SwitchJoinOrder(document, cut, other)
    return cut


def _fill_stats(stats, state, saved=True):
    if stats is None:
        return
    stats["protected"] = len(state.protected) if (state and saved) else 0
    stats["duplicates"] = len(state.duplicates) if state else 0


def _commit_join(transaction):
    status = transaction.Commit()
    if status != TransactionStatus.Committed:
        raise RuntimeError("Join transaction was not committed: {}. "
                           "Resolve any Revit failure dialog before running again.".format(status))


def run_join(rules, scope="Active View", mode="Join", switch_order=False,
             progress_callback=None, cancel_check=None, doc=None, uidoc=None,
             protect_embedded=False, stats=None):
    """Execute join/unjoin operations based on rules.

    protect_embedded — an element that lies fully inside the other element of
        a pair always cuts it, whatever the rule order says, so the join never
        hides it; exact overlaps (duplicates) are not joined at all.
    stats — optional dict, filled with "protected" (pairs where an embedded
        element was kept visible) and "duplicates" (overlapping pairs left
        unjoined). Returns (joined, skipped, errors, message) as before.
    """
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
    state = EmbedState() if (protect_embedded and mode == "Join") else None

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

            rule_label = "Rule {}/{} · {} cuts {}".format(
                rule_idx + 1, total_rules, priority_name, joinwith_name)
            if progress_callback:
                progress_callback(rule_idx, total_rules, rule_label + u" …")

            priority_elements = _collect_elements(
                document, priority_bic, scope, view_id, selected_ids
            )
            n_elements = len(priority_elements)

            processed_pairs = set()

            for el_idx, el in enumerate(priority_elements):
                if cancel_check and cancel_check():
                    _commit_join(t)
                    if stats is not None:
                        _fill_stats(stats, state)
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
                            check = None
                            if state is not None:
                                check = state.check(document, el, cand, are_joined, pair_key)
                                if check.duplicate and not are_joined:
                                    # Exact overlap: a join hides one of the two.
                                    state.duplicates.add(pair_key)
                                    total_skipped += 1
                                    continue

                            if not are_joined:
                                JoinGeometryUtils.JoinGeometry(document, el, cand)
                                total_joined += 1
                            else:
                                total_skipped += 1

                            if JoinGeometryUtils.AreElementsJoined(document, el, cand):
                                inner = check.inner if check is not None else None
                                switched = _apply_join_order(
                                    document, el, cand, switch_order, inner)
                                if (check is not None and inner is None and not check.certain
                                        and (switched or not are_joined)):
                                    inner = _rescue_swallowed(document, el, cand, check.volumes)
                                    if inner is not None:
                                        state.verdicts[pair_key] = EmbedCheck(inner=inner)
                                if inner is not None:
                                    state.protected.add(pair_key)

                        elif mode == "Unjoin":
                            if are_joined:
                                JoinGeometryUtils.UnjoinGeometry(document, el, cand)
                                total_joined += 1
                            else:
                                total_skipped += 1

                    except Exception as ex:
                        logger.debug("Join operation error: {}".format(ex))
                        total_errors += 1

                if progress_callback:
                    progress_callback(
                        rule_idx + float(el_idx + 1) / n_elements, total_rules,
                        u"{} · {}/{}".format(rule_label, el_idx + 1, n_elements))

        _commit_join(t)
        if stats is not None:
            _fill_stats(stats, state)
        return (total_joined, total_skipped, total_errors, None)

    except Exception as ex:
        if t is not None and t.GetStatus() == TransactionStatus.Started:
            t.RollBack()
        if stats is not None:
            _fill_stats(stats, state, saved=False)
        # Attempted joins are not saved joins after rollback or while Pending.
        return (0, total_skipped, total_errors + 1, str(ex))
