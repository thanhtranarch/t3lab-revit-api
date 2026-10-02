# -*- coding: utf-8 -*-
"""
_rebar.py
=========
Reinforcement helpers for the Rebar & Assembly toolkit.

* PURE PYTHON  - partition-rule rendering, bar centre-line geometry (legs,
  outer dimensions, BVBS segments) and fingerprint matching between two
  assemblies. No Revit import at module level; ``dev/test_rebar_geometry.py``
  and ``dev/test_partition_rule.py`` run the shipped source without Revit.
* REVIT        - the rebar index (host -> rebar map built once per window),
  centre-line extraction, bar type lookup and "Assign partition". ``Autodesk.Revit``
  is imported inside each function.

Spec: dev/plan/rebar-tekla-implementation-spec.md section 3.3 (decisions D2,
D14, D15). Everything marked [NV] is unverified until
dev/debug/spike_rebar_assembly.py has run on Revit 2027.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"
__title__ = "Rebar"

import math
import re

from Snippets._assembly import (
    NO_ASSEMBLY,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    SKIP_STOPPED,
    TRANSACTION_PREFIX,
    RebarRecord,
    Result,
)
from Snippets._compat import (
    _partition_parameter_raw,
    _revit_type,
    bar_nominal_diameter_mm,
    disposing,
    eid_value,
    elem_name,
    make_eid,
    numbering_partitions_by_partition_param,
    rebar_number_text,
    short_error,
    to_mm,
)


# ── CONSTANTS ────────────────────────────────────────────────────────────────

REBAR_KINDS = ("Rebar", "RebarInSystem", "AreaReinforcement", "PathReinforcement",
               "FabricSheet", "FabricArea", "RebarCoupler")
PARTITION_TOKENS = ("{AssemblyMark}", "{Level}", "{HostType}", "{HostMark}",
                    "{Workset}", "{Category}")

# Revit's partition name limit is unknown [NV]; longer values are written but
# reported so the user can shorten the rule.
PARTITION_MAX_LEN = 64
_SEPARATORS = u"-_ "

PARTITION_2027_WARNING = (
    u'This model\'s rebar numbering schema does not partition by "Partition". '
    u'Partition values were written, but numbers will not change until you add '
    u'"Partition" as a partitioning parameter in Manage › Numbering.')

_TOKEN_RE = re.compile(r"\{[A-Za-z]+\}")


# ── PARTITION RULE (pure) ────────────────────────────────────────────────────

def render_partition(rule, ctx):
    """Render a partition rule such as ``{AssemblyMark}-{Level}``.

    ``ctx`` maps AssemblyMark / Level / HostType / HostMark / Workset /
    Category to text (None counts as empty). Unknown ``{tokens}`` stay as
    typed. Empty tokens leave no stray separators ('A--B' -> 'A-B', leading
    and trailing '-', '_', ' ' are trimmed). When the rule has tokens and
    every one of them is empty the result is ''. Never truncates: see
    ``check_partition`` for the length warning.
    """
    rule = rule or u""
    ctx = ctx or {}
    known = set(token[1:-1] for token in PARTITION_TOKENS)
    state = {"known": 0, "filled": 0, "unknown": 0}

    def replace(match):
        name = match.group(0)[1:-1]
        if name not in known:
            state["unknown"] += 1
            return match.group(0)
        state["known"] += 1
        value = ctx.get(name)
        value = u"" if value is None else (u"%s" % value).strip()
        if value:
            state["filled"] += 1
        return value

    text = _TOKEN_RE.sub(replace, rule)
    if state["known"] and not state["filled"] and not state["unknown"]:
        return u""
    text = re.sub(u"([%s])\\1+" % re.escape(_SEPARATORS), u"\\1", text)
    return text.strip(_SEPARATORS)


def check_partition(value):
    """Warning text for a rendered partition value, or None when it is fine."""
    if value and len(value) > PARTITION_MAX_LEN:
        return (u"Partition value is %d characters (over %d); Revit may refuse "
                u"it. Shorten the rule." % (len(value), PARTITION_MAX_LEN))
    return None


# ── VECTOR HELPERS (pure) ────────────────────────────────────────────────────

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(v):
    return math.sqrt(_dot(v, v))


def _unit(v):
    length = _norm(v)
    if length < 1e-12:
        return None
    return (v[0] / length, v[1] / length, v[2] / length)


def _point(p):
    return (float(p[0]), float(p[1]), float(p[2]))


def _plane_normal_of_points(points, tol_mm):
    """(origin, unit normal) of the best plane through `points`; normal None when collinear."""
    origin = points[0]
    far = max(points, key=lambda p: _norm(_sub(p, origin)))
    axis = _unit(_sub(far, origin))
    if axis is None:
        return origin, None
    off = max(points, key=lambda p: _norm(_cross(axis, _sub(p, origin))))
    if _norm(_cross(axis, _sub(off, origin))) <= tol_mm:
        return origin, None
    return origin, _unit(_cross(axis, _sub(off, origin)))


def _max_plane_deviation(points, origin, normal):
    return max(abs(_dot(_sub(p, origin), normal)) for p in points)


# ── CENTRE-LINE GEOMETRY (pure) ──────────────────────────────────────────────

class LegChain(object):
    """Result of ``centerline_to_legs``.

    ``legs`` is ``[(length_mm, bend_angle_deg_after_the_leg)]`` and the last angle
    is 0. When ``planar`` is False, ``legs`` is empty and ``reason`` says why
    (the BVBS row then reads ``skipped: free-form 3D``).
    """

    def __init__(self, legs=None, planar=True, reason=u"", normal=None):
        self.legs = list(legs or [])
        self.planar = planar
        self.reason = reason
        self.normal = normal

    def __repr__(self):
        return "<LegChain planar=%r legs=%r %s>" % (self.planar, self.legs, self.reason)


def _dedupe_points(points, tol=1e-3):
    out = []
    for p in points:
        p = _point(p)
        if not out or _norm(_sub(p, out[-1])) > tol:
            out.append(p)
    return out


def _merge_collinear(points):
    """Drop points that lie straight on the way; a 180-degree reversal is kept."""
    out = [points[0]]
    for i in range(1, len(points) - 1):
        v1 = _sub(points[i], out[-1])
        v2 = _sub(points[i + 1], points[i])
        sin = _norm(_cross(v1, v2)) / (_norm(v1) * _norm(v2))
        if sin < 1e-6 and _dot(v1, v2) > 0:
            continue
        out.append(points[i])
    out.append(points[-1])
    return out


def _bend_normal(directions):
    """Normal of the bending plane: cross of the first two clearly non-parallel legs."""
    best = None
    for a, b in zip(directions, directions[1:]):
        c = _cross(a, b)
        s = _norm(c)
        if s > 0.05:
            return _unit(c)
        if best is None or s > best[0]:
            best = (s, c)
    if best is not None and best[0] > 1e-9:
        return _unit(best[1])
    # Straight, or only 180-degree reversals: any normal will do.
    d0 = directions[0]
    axis = (1.0, 0.0, 0.0) if abs(d0[0]) < 0.9 else (0.0, 1.0, 0.0)
    return _unit(_cross(d0, axis))


def centerline_to_legs(points, planar_tol_mm=1.0):
    """Leg lengths and bend angles of one bar's sharp-corner centre-line.

    ``points`` are (x, y, z) mm. Collinear points are merged; a 180-degree
    reversal is a hook (angle 180). Angles are positive when the chain turns
    left about the plane normal (the cross product of the first two legs), so
    the first bend is always positive; the last angle is 0. A chain that
    leaves its plane by more than ``planar_tol_mm`` is returned as
    ``LegChain(planar=False, reason=...)`` (decision D2: BF2D only).
    """
    cleaned = _dedupe_points(points or ())
    if len(cleaned) < 2:
        return LegChain(planar=False, reason=u"fewer than two distinct points")
    cleaned = _merge_collinear(cleaned)

    vectors = [_sub(b, a) for a, b in zip(cleaned, cleaned[1:])]
    lengths = [_norm(v) for v in vectors]
    directions = [_unit(v) for v in vectors]

    normal = _bend_normal(directions)
    deviation = _max_plane_deviation(cleaned, cleaned[0], normal)
    if deviation > planar_tol_mm:
        return LegChain(planar=False, normal=normal,
                        reason=u"bar leaves its plane by %.1f mm" % deviation)

    legs = []
    for i, length in enumerate(lengths):
        angle = 0.0
        if i < len(directions) - 1:
            a, b = directions[i], directions[i + 1]
            angle = math.degrees(math.atan2(_dot(_cross(a, b), normal), _dot(a, b)))
            if abs(abs(angle) - 180.0) < 1e-6:
                angle = 180.0
        legs.append((round(length, 6), round(angle, 6) + 0.0))
    return LegChain(legs=legs, planar=True, normal=normal)


def _bend_allowance(angle_deg, half_d):
    """How much one bend lengthens an adjacent leg when going centre-line -> outer."""
    a = abs(angle_deg)
    if a < 1e-9:
        return 0.0
    if a >= 179.999:
        return half_d                       # hook: tan(90) capped at d/2
    return half_d * math.tan(math.radians(a / 2.0))


def outer_legs(legs, bar_d_mm):
    """D14: centre-line legs -> outer (out-to-out) legs, as BVBS wants them.

    Leg i grows by ``(d/2)*tan(|theta|/2)`` for each of its (at most two)
    adjacent bends; a 180-degree hook adds ``d/2`` per hook end. Returns
    ``[(outer_length_mm, angle_deg)]`` ready for ``legs_to_bvbs_segments``.
    [NV] against Revit's shape parameters (spike G12).
    """
    half_d = float(bar_d_mm) / 2.0
    out = []
    last = len(legs) - 1
    for i, (length, angle) in enumerate(legs):
        grown = float(length)
        if i > 0:
            grown += _bend_allowance(legs[i - 1][1], half_d)
        if i < last:
            grown += _bend_allowance(angle, half_d)
        out.append((round(grown, 6), angle))
    return out


def legs_to_bvbs_segments(legs):
    """[(length, angle)] -> [(int mm, angle rounded to 0.1 deg)], last angle 0."""
    out = []
    last = len(legs) - 1
    for i, (length, angle) in enumerate(legs):
        bend = 0.0 if i == last else round(float(angle), 1) + 0.0
        out.append((int(round(length)), bend))
    return out


def classify_centerline(curves_info, planar_tol_mm=1.0):
    """'planar_lines' | 'planar_with_arcs' | 'non_planar' for a bar's curves.

    ``curves_info`` is ``[('Line', p0, p1) | ('Arc', p0, p1, pm)]`` (mm points).
    Arcs left after Revit's bend radii were suppressed are curved legs (D2).
    """
    points = []
    has_arc = False
    for curve in curves_info or ():
        if curve[0] != "Line":
            has_arc = True
        points.extend(_point(p) for p in curve[1:])
    if not points:
        return "non_planar"
    origin, normal = _plane_normal_of_points(points, planar_tol_mm)
    if normal is not None and _max_plane_deviation(points, origin, normal) > planar_tol_mm:
        return "non_planar"
    return "planar_with_arcs" if has_arc else "planar_lines"


def curves_to_polyline(curves_info, tol_mm=0.5):
    """Chain 'Line' curves into one polyline of (x, y, z) mm points.

    Curves may arrive with either end first; they are flipped to follow each
    other. Raises ValueError when the curves do not form one connected chain.
    Arcs contribute their end points only - classify the bar first.
    """
    pending = [(_point(c[1]), _point(c[2])) for c in curves_info or ()]
    if not pending:
        raise ValueError("no curves")
    start, end = pending.pop(0)
    chain = [start, end]
    while pending:
        for k, (a, b) in enumerate(pending):
            if _norm(_sub(a, chain[-1])) <= tol_mm:
                chain.append(b)
            elif _norm(_sub(b, chain[-1])) <= tol_mm:
                chain.append(a)
            elif _norm(_sub(b, chain[0])) <= tol_mm:
                chain.insert(0, a)
            elif _norm(_sub(a, chain[0])) <= tol_mm:
                chain.insert(0, b)
            else:
                continue
            pending.pop(k)
            break
        else:
            raise ValueError("centre-line curves do not form one connected chain")
    return chain


# ── FINGERPRINT MATCHING (pure) ──────────────────────────────────────────────

def _cell(value, tol):
    return int(math.floor(value / tol + 0.5))


def fingerprint(record, local_center, tol_mm=10.0):
    """(kind, shape, bar_type, quantity, cell_x, cell_y, cell_z).

    ``local_center`` is the element centre in the ASSEMBLY's local frame (mm);
    each axis is snapped to a ``tol_mm`` grid.
    """
    return (record.kind, record.shape, record.bar_type, record.quantity,
            _cell(local_center[0], tol_mm), _cell(local_center[1], tol_mm),
            _cell(local_center[2], tol_mm))


def _group_key(item):
    record = item[0]
    return (record.kind, record.shape, record.bar_type, record.quantity)


def _assign(candidates, taken_src, taken_dst):
    """Greedy nearest-first assignment of (distance, i, j) candidates.

    A source whose two best candidates are equally near is ambiguous and is
    not matched. Returns (matches {i: j}, ambiguous set of i).
    """
    by_src = {}
    for dist, i, j in candidates:
        if i not in taken_src and j not in taken_dst:
            by_src.setdefault(i, []).append((dist, j))
    ambiguous = set()
    for i, options in by_src.items():
        options.sort()
        if len(options) > 1 and options[1][0] - options[0][0] < 1e-6:
            ambiguous.add(i)
    matches = {}
    used = set()
    for dist, i, j in sorted((d, i, j) for d, i, j in candidates):
        if i in matches or i in ambiguous or i in taken_src \
                or j in used or j in taken_dst:
            continue
        matches[i] = j
        used.add(j)
    return matches, ambiguous


def match_by_fingerprint(src_items, dst_items, tol_mm=10.0, allow_mirror=True):
    """Pair rebar of a source assembly with rebar of a target assembly.

    Items are ``(record, local_center_mm)`` tuples. Returns
    ``(pairs, unmatched_src, ambiguous)``; ``pairs`` is ``[(src_item, dst_item)]``
    in source order and the other two are source items. Phases: identical
    fingerprint; then nearest within ``2*tol_mm`` among bars with the same
    (kind, shape, bar type, quantity); then, when ``allow_mirror``, the mirror
    (x -> -x or y -> -y) that matches the most remaining bars. A source with two
    equally near candidates is ``ambiguous``, never guessed.
    """
    src = list(src_items)
    dst = list(dst_items)
    matched = {}
    ambiguous = set()
    taken_dst = set()

    def distance(a, b, flip=(1, 1, 1)):
        return math.sqrt(sum((a[k] * flip[k] - b[k]) ** 2 for k in range(3)))

    def candidates(flip, need_same_cell):
        out = []
        for i, s in enumerate(src):
            if i in matched or i in ambiguous:
                continue
            fx = (s[1][0] * flip[0], s[1][1] * flip[1], s[1][2] * flip[2])
            for j, d in enumerate(dst):
                if j in taken_dst or _group_key(s) != _group_key(d):
                    continue
                dist = distance(s[1], d[1], flip)
                if need_same_cell:
                    if tuple(_cell(v, tol_mm) for v in fx) != \
                            tuple(_cell(v, tol_mm) for v in d[1]):
                        continue
                elif dist > 2.0 * tol_mm:
                    continue
                out.append((dist, i, j))
        return out

    def commit(matches, new_ambiguous):
        for i, j in matches.items():
            matched[i] = j
            taken_dst.add(j)
        ambiguous.update(i for i in new_ambiguous if i not in matched)

    for flip, same_cell in (((1, 1, 1), True), ((1, 1, 1), False)):
        commit(*_assign(candidates(flip, same_cell), set(matched) | ambiguous, taken_dst))

    if allow_mirror and len(matched) < len(src):
        best = None
        for flip in ((-1, 1, 1), (1, -1, 1)):
            result = _assign(candidates(flip, False), set(matched) | ambiguous, taken_dst)
            if result[0] and (best is None or len(result[0]) > len(best[0])):
                best = result
        if best is not None:
            commit(*best)

    pairs = [(src[i], dst[matched[i]]) for i in sorted(matched)]
    unmatched = [s for i, s in enumerate(src) if i not in matched and i not in ambiguous]
    ambiguous_items = [src[i] for i in sorted(ambiguous) if i not in matched]
    return pairs, unmatched, ambiguous_items


# ── REBAR INDEX [REVIT] ──────────────────────────────────────────────────────

class RebarIndex(object):
    """Host -> rebar map of the whole model, built once per window open.

    ``rows`` is every RebarRecord, ``by_host`` maps host id to its rows,
    ``by_id`` maps element id to its row. ``stopped`` is True when the
    progress callback cancelled the scan (the index is then partial).
    """

    def __init__(self):
        self.rows = []
        self.by_host = {}
        self.by_id = {}
        self.stopped = False

    def add(self, row):
        self.rows.append(row)
        self.by_id[row.id] = row
        self.by_host.setdefault(row.host_id, []).append(row)


def _call_first(obj, names):
    """Result of the first no-argument method / property of `names` that works."""
    for name in names:
        try:
            member = getattr(obj, name, None)
            if member is None:
                continue
            return member() if callable(member) else member
        except Exception:
            continue
    return None


def host_id_of(doc, element):
    """Host element id (int) of any reinforcement element, -1 when unknown.

    Rebar / area / path / fabric: GetHostId(). RebarInSystem falls back to the
    host of its system (SystemId). Couplers use the host of the first coupled
    reinforcement. [NV] the coupler and fabric member names (spike G4).
    """
    try:
        value = eid_value(element.GetHostId())
        if value >= 0:
            return value
    except Exception:
        pass
    try:
        system = doc.GetElement(element.SystemId)
        if system is not None:
            value = eid_value(system.GetHostId())
            if value >= 0:
                return value
    except Exception:
        pass
    value = _call_first(element, ("HostId",))
    if value is not None:
        try:
            if eid_value(value) >= 0:
                return eid_value(value)
        except Exception:
            pass
    try:
        for data in element.GetCoupledReinforcementData():
            rebar_id = _call_first(data, ("GetReinforcementId", "ReinforcementId",
                                          "GetRebarId"))
            if rebar_id is None:
                continue
            coupled = doc.GetElement(rebar_id)
            if coupled is not None:
                value = eid_value(coupled.GetHostId())
                if value >= 0:
                    return value
    except Exception:
        pass
    return -1


def _text_of(parameter):
    if parameter is None:
        return u""
    try:
        return parameter.AsString() or parameter.AsValueString() or u""
    except Exception:
        return u""


def _schedule_mark(element):
    mark = _call_first(element, ("ScheduleMark",))
    if mark:
        return u"%s" % mark
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        built_in = getattr(BuiltInParameter, "REBAR_ELEM_SCHEDULE_MARK", None)
        if built_in is not None:
            return _text_of(element.get_Parameter(built_in))
    except Exception:
        pass
    return u""


def _rebar_record(doc, element, kind, type_cache, shape_cache):
    record = RebarRecord(id=eid_value(element.Id), kind=kind)
    record.host_id = host_id_of(doc, element)
    try:
        record.assembly_id = eid_value(element.AssemblyInstanceId)
    except Exception:
        record.assembly_id = NO_ASSEMBLY
    try:
        record.in_group = eid_value(element.GroupId) >= 0
    except Exception:
        pass
    record.partition = _text_of(_partition_parameter_raw(element))
    record.number = rebar_number_text(element)
    record.mark = _schedule_mark(element)

    if kind == "RebarInSystem":
        value = _call_first(element, ("SystemId",))
        if value is not None:
            record.system_id = eid_value(value)

    if kind in ("Rebar", "RebarInSystem"):
        bar_type = bar_type_of(doc, element)
        if bar_type is not None:
            type_id = eid_value(bar_type.Id)
            if type_id not in type_cache:
                try:
                    type_cache[type_id] = (elem_name(bar_type),
                                           bar_nominal_diameter_mm(bar_type))
                except Exception:
                    type_cache[type_id] = (u"", 0.0)
            record.bar_type, record.diameter_mm = type_cache[type_id]
        quantity = _call_first(element, ("Quantity",))
        if quantity is not None:
            try:
                record.quantity = int(quantity)
            except Exception:
                pass
        shape_id = _call_first(element, ("GetShapeId",))
        if shape_id is not None:
            key = eid_value(shape_id)
            if key not in shape_cache:
                try:
                    shape = doc.GetElement(shape_id)
                    shape_cache[key] = elem_name(shape) if shape is not None else u""
                except Exception:
                    shape_cache[key] = u""
            record.shape = shape_cache[key]
        driven = _call_first(element, ("IsRebarShapeDriven",))
        record.is_shape_driven = bool(driven)
    return record


def build_rebar_index(doc, progress=None):
    """Index every reinforcement element of the model. Read-only.

    Collects Rebar, RebarInSystem, AreaReinforcement, PathReinforcement,
    FabricSheet, FabricArea and RebarCoupler (a class this release lacks is
    skipped). An element that cannot be read keeps its default fields instead
    of raising. ``progress(index, total, label)`` returning False stops the
    scan; ``index.stopped`` is then True.
    """
    from Autodesk.Revit.DB import FilteredElementCollector
    index = RebarIndex()
    batches = []
    for kind in REBAR_KINDS:
        element_type = _revit_type(kind)
        if element_type is None:
            continue
        try:
            with disposing(FilteredElementCollector(doc)) as collector:
                batches.append((kind, list(collector.OfClass(element_type).ToElements())))
        except Exception:
            continue

    total = sum(len(elements) for _, elements in batches)
    type_cache = {}
    shape_cache = {}
    done = 0
    for kind, elements in batches:
        for element in elements:
            done += 1
            if progress is not None and done % 50 == 1 \
                    and progress(done, total, u"Reading reinforcement") is False:
                index.stopped = True
                return index
            try:
                index.add(_rebar_record(doc, element, kind, type_cache, shape_cache))
            except Exception:
                continue
    return index


# ── CENTRE-LINE EXTRACTION [REVIT] ───────────────────────────────────────────

def _point_mm(xyz):
    return (to_mm(xyz.X), to_mm(xyz.Y), to_mm(xyz.Z))


def _curve_info(curve):
    p0 = _point_mm(curve.GetEndPoint(0))
    p1 = _point_mm(curve.GetEndPoint(1))
    if curve.GetType().Name == "Line":
        return ("Line", p0, p1)
    return ("Arc", p0, p1, _point_mm(curve.Evaluate(0.5, True)))


def centerline_points(rebar, position_index=0):
    """Centre-line of one bar position as ``[('Line', p0, p1) | ('Arc', p0, p1, pm)]`` (mm).

    Asks for the sharp-corner chain (bend radii suppressed, hooks kept) with
    all multiplanar curves. Rebar: GetTransformedCenterlineCurves when the
    release has it, else GetCenterlineCurves moved by the bar position
    transform; RebarInSystem: the 3-argument GetCenterlineCurves. [NV] which of
    the first two returns model coordinates for position i (spike G12).
    Raises RuntimeError (with the Revit message) when no overload works - wrap
    it per bar.
    """
    option_type = _revit_type("MultiplanarOption")
    option = getattr(option_type, "IncludeAllMultiplanarCurves", None) if option_type else None
    curves = None
    errors = []

    getter = getattr(rebar, "GetTransformedCenterlineCurves", None)
    if getter is not None and option is not None:
        try:
            curves = getter(False, False, True, option, position_index)
        except Exception as exc:
            errors.append(short_error(exc))
    if curves is None and option is not None:
        try:
            curves = rebar.GetCenterlineCurves(False, False, True, option, position_index)
            if position_index > 0:
                transform = rebar.GetShapeDrivenAccessor().GetBarPositionTransform(position_index)
                curves = [c.CreateTransformed(transform) for c in curves]
        except Exception as exc:
            curves = None
            errors.append(short_error(exc))
    if curves is None:
        try:
            curves = rebar.GetCenterlineCurves(False, False, True)
        except Exception as exc:
            errors.append(short_error(exc))
    if curves is None:
        raise RuntimeError(u"Could not read the bar centre-line: %s" % u"; ".join(errors))
    return [_curve_info(c) for c in curves]


def bar_type_of(doc, rebar):
    """RebarBarType of a Rebar / RebarInSystem, or None.

    GetTypeId() for both; RebarInSystem also falls back to the REBAR_BAR_TYPE
    parameter. [NV] the parameter for RebarInSystem (spike G12).
    """
    try:
        bar_type = doc.GetElement(rebar.GetTypeId())
        if bar_type is not None and hasattr(bar_type, "BarNominalDiameter"):
            return bar_type
    except Exception:
        pass
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        built_in = getattr(BuiltInParameter, "REBAR_BAR_TYPE", None)
        if built_in is not None:
            param = rebar.get_Parameter(built_in)
            if param is not None:
                bar_type = doc.GetElement(param.AsElementId())
                if bar_type is not None and hasattr(bar_type, "BarNominalDiameter"):
                    return bar_type
    except Exception:
        pass
    return None


def mandrel_diameter_mm(bar_type, style):
    """Inner bend (mandrel) diameter in mm: stirrup/tie bend diameter for
    StirrupTie style, else the standard bend diameter. None when unreadable.
    ``style`` is a RebarStyle member or its name."""
    try:
        if "StirrupTie" in (u"%s" % style):
            return to_mm(bar_type.StirrupTieBendDiameter)
        return to_mm(bar_type.StandardBendDiameter)
    except Exception:
        return None


def local_center(element, assembly_transform):
    """Bounding-box centre of `element` in the assembly's local frame, mm; None without a box."""
    try:
        from Autodesk.Revit.DB import XYZ
        box = element.get_BoundingBox(None)
        if box is None:
            return None
        middle = XYZ((box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0,
                     (box.Min.Z + box.Max.Z) / 2.0)
        local = assembly_transform.Inverse.OfPoint(middle)
        return _point_mm(local)
    except Exception:
        return None


# ── PARTITION [REVIT] ────────────────────────────────────────────────────────

def _rollback(txn):
    try:
        if not txn.HasEnded():
            txn.RollBack()
    except Exception:
        pass


def assign_partition(doc, element_ids, rule, context_fn, progress=None):
    """Write the rendered partition rule into each element's Partition parameter.

    ONE Transaction "Assign rebar partition" (the caller may wrap it in a
    group). ``context_fn(id)`` returns the dict ``render_partition`` expects.
    Returns [Result]: ok (count 1), skipped (read-only / no Partition parameter /
    empty value / stopped) or failed. A value over 64 characters is written
    and flagged in the detail. Never touches Rebar Number (D15).
    """
    from Autodesk.Revit.DB import Transaction
    ids = [eid_value(i) for i in element_ids]
    results = []
    total = len(ids)
    txn = Transaction(doc, TRANSACTION_PREFIX + u"Assign rebar partition")
    with disposing(txn) as t:
        t.Start()
        try:
            for index, element_id in enumerate(ids):
                label = u"%d" % element_id
                if progress is not None and progress(index + 1, total, label) is False:
                    results.extend(Result(u"%d" % rest, STATUS_SKIPPED, 0, SKIP_STOPPED)
                                   for rest in ids[index:])
                    break
                results.append(_assign_one(doc, element_id, rule, context_fn))
            t.Commit()
        except Exception:
            _rollback(t)
            raise
    return results


def _assign_one(doc, element_id, rule, context_fn):
    label = u"%d" % element_id
    try:
        element = doc.GetElement(make_eid(element_id))
        if element is None:
            return Result(label, STATUS_SKIPPED, 0, u"element no longer exists")
        value = render_partition(rule, context_fn(element_id))
        if not value:
            return Result(label, STATUS_SKIPPED, 0,
                          u"empty value: the rule resolved to nothing for this element")
        param = _partition_parameter_raw(element)
        if param is None:
            return Result(label, STATUS_SKIPPED, 0, u"no Partition parameter on this element")
        if param.IsReadOnly:
            return Result(label, STATUS_SKIPPED, 0, u"Partition is read-only on this element")
        if not param.Set(value):
            return Result(label, STATUS_FAILED, 0, u"Revit refused the value %s" % value)
        warning = check_partition(value)
        return Result(label, STATUS_OK, 1,
                      u"Partition = %s%s" % (value, u" - " + warning if warning else u""))
    except Exception as exc:
        return Result(label, STATUS_FAILED, 0, short_error(exc))


def partition_warning_2027(doc):
    """D15: the warning text when this 2027+ model's numbering schema does not
    partition by "Partition", else None (also None on <= 2026 and on any error)."""
    if numbering_partitions_by_partition_param(doc) is False:
        return PARTITION_2027_WARNING
    return None
