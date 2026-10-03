# -*- coding: utf-8 -*-
"""
_rebar_check.py
===============
Data checks for the Rebar Check tool (Tekla: model checks). Revit does not run
these: rebar without a valid host, rebar missing from its host's assembly,
assemblies without drawings, duplicate rebar numbers on different bars, bars
outside their host, bars without a partition and shape-driven bars whose shape
Revit cannot name.

Two halves, kept apart on purpose:

* PURE PYTHON  - the rules. They classify ``RebarRecord`` / ``HostRecord`` /
  ``AssemblyRecord`` lists (built by ``_rebar.build_rebar_index`` and
  ``_assembly.collect_assemblies``) into ``Issue`` rows, filter and summarise
  them, and plan the two fixes. No Revit import at module level, so
  ``dev/test_rebar_check_rules.py`` exercises the shipped source.
* REVIT        - host lookup, bounding boxes, bar centre-lines (for the
  duplicate-number geometry test), temporary isolate and the Sync fix. The scan
  is read-only; the only model changes are Sync (one TransactionGroup, one
  Transaction per assembly, rule A6) and Isolate (view-local).

The two fixes never re-implement their logic: Sync is ``_assembly.sync_rebar``
and Assign partition is ``_rebar.assign_partition`` (reached through Cast Unit
Manager, page "Partition by rule"). Spec: dev/plan/rebar-tekla-implementation-
spec.md section 5.4 (decision D12, risk R11). Everything marked [NV] is
unverified until dev/debug/spike_rebar_assembly.py has run on Revit 2027.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"
__title__ = "Rebar Check"

from Snippets._assembly import (
    NO_ASSEMBLY,
    SKIP_IN_GROUP,
    SKIP_IN_LINK,
    TRANSACTION_PREFIX,
    HostRecord,
    filter_assembly_candidates,
)
from Snippets._compat import (
    disposing,
    eid_value,
    elem_name,
    make_eid,
    net_list,
    short_error,
    to_mm,
)


# ── CONSTANTS ────────────────────────────────────────────────────────────────

CHECK_HOST = "host"
CHECK_MEMBER = "member"
CHECK_DRAWING = "drawing"
CHECK_DUP = "dup"
CHECK_BBOX = "bbox"
CHECK_PARTITION = "partition"
CHECK_SHAPE = "shape"

CHECK_ORDER = (CHECK_HOST, CHECK_MEMBER, CHECK_DRAWING, CHECK_DUP,
               CHECK_BBOX, CHECK_PARTITION, CHECK_SHAPE)

# Text of the "Check" column and of the filter chips.
CHECK_LABELS = {
    CHECK_HOST: u"No valid host",
    CHECK_MEMBER: u"Not in assembly",
    CHECK_DRAWING: u"No drawing",
    CHECK_DUP: u"Duplicate number",
    CHECK_BBOX: u"Outside host",
    CHECK_PARTITION: u"No partition",
    CHECK_SHAPE: u"Shape unknown",
}

# Checks that need bar geometry. They run on demand (risk R11): the bounding
# box of every bar, and the centre-line of every bar that shares a number.
DEEP_CHECKS = (CHECK_DUP, CHECK_BBOX)

FIX_SYNC = "sync"
FIX_ASSIGN = "assign"
FIX_MANUAL = "manual"
FIX_LABELS = {
    FIX_SYNC: u"Sync into assembly",
    FIX_ASSIGN: u"Assign partition",
    FIX_MANUAL: u"Manual",
}

SCOPE_ALL = "all"
SCOPE_IN_ASSEMBLY = "in"
SCOPE_LOOSE = "loose"

KIND_ASSEMBLY = u"Assembly"
KIND_LABELS = {
    "Rebar": u"Rebar",
    "RebarInSystem": u"Rebar (system)",
    "AreaReinforcement": u"Area reinforcement",
    "PathReinforcement": u"Path reinforcement",
    "FabricSheet": u"Fabric sheet",
    "FabricArea": u"Fabric area",
    "RebarCoupler": u"Coupler",
}
# Kinds that are individual bars: the only ones the number / partition / shape /
# outside-host checks apply to (area, path and fabric carry no bar shape).
BAR_KINDS = ("Rebar", "RebarInSystem")

NO_ASSEMBLY_TEXT = u"— none —"
MIN_BBOX_TOLERANCE_MM = 50.0
ISOLATE_NEEDS_MODEL_VIEW = (u"Isolate needs a model view. Open a plan, section or 3D "
                            u"view and try again.")


# ── TEXT HELPERS (pure) ──────────────────────────────────────────────────────

def fmt_count(value):
    """1286 -> '1 286' (the house style for counts in the status line)."""
    try:
        return u"{:,}".format(int(value)).replace(u",", u" ")
    except Exception:
        return u"%s" % value


def plural(count, singular, many=None):
    """'1 bar' / '2 bars' (no grouping). `many` defaults to singular + 's'."""
    word = singular if count == 1 else (many or singular + u"s")
    return u"%d %s" % (count, word)


def kind_label(kind):
    return KIND_LABELS.get(kind, kind or u"Rebar")


def _mark_of(marks, assembly_id):
    if assembly_id == NO_ASSEMBLY:
        return u""
    return marks.get(assembly_id) or (u"assembly %d" % assembly_id)


def _host_label(host):
    name = (getattr(host, "name", u"") or u"").strip()
    if name:
        return u"%s (%d)" % (name, host.id)
    return u"element %d" % host.id


# ── ISSUE ROW (pure) ─────────────────────────────────────────────────────────

class Issue(object):
    """One finding. ``element_id`` is the element to select (an assembly id for
    the drawing check); ``element_ids`` holds every element behind the row (the
    duplicate-number check reports one row per number).

    ``assembly_id`` is the assembly the row relates to: the host's assembly for
    a bar that is missing from it, the bar's own assembly otherwise, the
    assembly itself for the drawing check. The scope chips (A8) filter on it.
    """

    def __init__(self, check, element_id, detail, kind=u"", category=u"",
                 assembly_id=NO_ASSEMBLY, assembly_mark=u"", partition=u"",
                 number=u"", fix=FIX_MANUAL, fix_reason=u"", element_ids=None):
        self.check = check
        self.element_id = element_id
        self.element_ids = list(element_ids) if element_ids else [element_id]
        self.detail = detail
        self.kind = kind
        self.category = category
        self.assembly_id = assembly_id
        self.assembly_mark = assembly_mark
        self.partition = partition
        self.number = number
        self.fix = fix
        self.fix_reason = fix_reason

    @property
    def check_label(self):
        return CHECK_LABELS.get(self.check, self.check)

    @property
    def fix_label(self):
        return FIX_LABELS.get(self.fix, self.fix)

    @property
    def fixable(self):
        return self.fix != FIX_MANUAL

    def __repr__(self):
        return "<Issue %s %s fix=%s>" % (self.check, self.element_id, self.fix)


def _row_issue(check, row, detail, marks, fix=FIX_MANUAL, fix_reason=u"",
               assembly_id=None, element_ids=None):
    owner = row.assembly_id if assembly_id is None else assembly_id
    return Issue(check, row.id, detail, kind=row.kind,
                 category=kind_label(row.kind), assembly_id=owner,
                 assembly_mark=_mark_of(marks, owner), partition=row.partition,
                 number=row.number, fix=fix, fix_reason=fix_reason,
                 element_ids=element_ids)


# ── THE CHECKS (pure) ────────────────────────────────────────────────────────

def check_hosts(rows, hosts, marks=None):
    """`host`: the bar has no host, or its host is gone / cannot host rebar.

    ``hosts`` is ``{host_id: HostRecord}`` for the hosts that exist in the
    model. A coupler whose host could not be read is skipped, not flagged: that
    is a probe gap ([NV] spike G4), not evidence of a problem.
    """
    marks = marks or {}
    out = []
    for row in rows:
        if row.host_id < 0:
            if row.kind == "RebarCoupler":
                continue
            detail = (u"No host recorded — its host was deleted or cannot be read. "
                      u"Re-host the element or delete it.")
        else:
            host = hosts.get(row.host_id)
            if host is None:
                detail = (u"Host element %d no longer exists. Re-host the element "
                          u"or delete it." % row.host_id)
            elif not host.is_valid_host:
                detail = (u"Element %d can no longer host rebar. Re-host the "
                          u"element or restore the host." % row.host_id)
            else:
                continue
        out.append(_row_issue(CHECK_HOST, row, detail, marks))
    return out


def _member_manual_reason(row, reason, marks):
    if reason == SKIP_IN_GROUP:
        return u"it is inside a group — ungroup it, then sync"
    if reason == SKIP_IN_LINK:
        return u"it belongs to a linked model — assemblies cannot include it"
    return (u"it is already a member of %s — remove it there first (Edit Assembly)"
            % (_mark_of(marks, row.assembly_id) or u"another assembly"))


def check_members(rows, hosts, marks=None):
    """`member`: the host is in an assembly but the bar is not a member of it.

    Fixable by Sync when ``_assembly.filter_assembly_candidates`` (rules A3/A5)
    lets the bar join that assembly; otherwise the row is Manual and says why.
    """
    marks = marks or {}
    out = []
    for row in rows:
        host = hosts.get(row.host_id)
        if host is None or host.assembly_id == NO_ASSEMBLY:
            continue
        if row.assembly_id == host.assembly_id:
            continue
        host_mark = _mark_of(marks, host.assembly_id)
        if row.assembly_id == NO_ASSEMBLY:
            detail = u"Hosted by %s in %s but not a member" % (_host_label(host), host_mark)
        else:
            detail = (u"Hosted by %s in %s but a member of %s"
                      % (_host_label(host), host_mark, _mark_of(marks, row.assembly_id)))
        ok, skipped = filter_assembly_candidates([row], allow_assembly_id=host.assembly_id)
        if ok:
            out.append(_row_issue(CHECK_MEMBER, row, detail, marks, fix=FIX_SYNC,
                                  assembly_id=host.assembly_id))
        else:
            reason = _member_manual_reason(row, skipped[0][1], marks)
            out.append(_row_issue(CHECK_MEMBER, row, u"%s — %s" % (detail, reason),
                                  marks, fix=FIX_MANUAL, fix_reason=skipped[0][1],
                                  assembly_id=host.assembly_id))
    return out


def check_drawings(assemblies):
    """`drawing`: one row per assembly without views, or with views but no sheet.

    Views belong to an assembly instance (A7). An instance whose siblings of the
    same type do have views says so in the detail — [NV] whether the views stay
    with the type or the instance (spike G1).
    """
    by_type = {}
    for asm in assemblies:
        if asm.view_ids:
            by_type[asm.type_id] = by_type.get(asm.type_id, 0) + 1
    out = []
    for asm in assemblies:
        if not asm.view_ids:
            detail = u"No views"
            siblings = by_type.get(asm.type_id, 0)
            if siblings:
                detail += (u" (%s of this type %s)"
                           % (plural(siblings, u"other instance"),
                              u"has them" if siblings == 1 else u"have them"))
        elif not asm.sheet_ids:
            detail = u"Views but no sheet"
        else:
            continue
        out.append(Issue(CHECK_DRAWING, asm.id, detail, kind=KIND_ASSEMBLY,
                         category=asm.naming_category or KIND_ASSEMBLY,
                         assembly_id=asm.id, assembly_mark=asm.mark or (u"assembly %d" % asm.id)))
    return out


def _cheap_signature(row):
    return (row.shape, row.bar_type, row.quantity)


def _number_groups(rows):
    """{(partition, number): [row]} for bars that carry a number."""
    groups = {}
    for row in rows:
        if row.kind in BAR_KINDS and (row.number or u"").strip():
            groups.setdefault((row.partition or u"", row.number.strip()), []).append(row)
    return groups


def legs_needed(rows):
    """Ids whose centre-line decides the duplicate-number test.

    A group of bars sharing (partition, number) that already differ in shape,
    bar type or quantity is a duplicate without any geometry. Only groups that
    look identical on those need the leg chains compared, so only their bars
    are read (the slow part, risk R11).
    """
    ids = []
    for members in _number_groups(rows).values():
        if len(members) > 1 and len(set(_cheap_signature(r) for r in members)) == 1:
            ids.extend(r.id for r in members)
    return ids


def canonical_legs(legs):
    """Leg chain as a hashable key that ignores the direction the bar was drawn.

    ``legs`` is ``[(length_mm, angle_deg_after)]``; lengths are rounded to 1 mm,
    angles to 1 degree, and the smaller of the chain and its reverse is kept.
    """
    forward = tuple((int(round(length)), int(round(angle))) for length, angle in legs)
    count = len(forward)
    if count < 2:
        return forward
    lengths = [leg[0] for leg in forward]
    angles = [leg[1] for leg in forward]
    rev_lengths = lengths[::-1]
    rev_angles = [-angles[count - 2 - i] for i in range(count - 1)] + [0]
    backward = tuple(zip(rev_lengths, rev_angles))
    return min(forward, backward)


def check_duplicates(rows, legs_of=None, marks=None):
    """`dup`: bars sharing (partition, number) that are not the same bar.

    "Same bar" = same shape, bar type, quantity and leg chain (rounded to 1 mm).
    ``legs_of(id)`` returns the hashable leg chain for a bar, or None when the
    geometry was not read; without it only shape / type / quantity separate
    bars. One row per number, reporting the least common variant first.
    """
    marks = marks or {}
    out = []
    for (partition, number), members in sorted(_number_groups(rows).items()):
        variants = {}
        unread = []
        for row in members:
            legs = legs_of(row.id) if legs_of is not None else None
            if legs_of is not None and legs is None:
                unread.append(row)          # centre-line could not be read
                continue
            variants.setdefault(_cheap_signature(row) + (legs,), []).append(row)
        for row in unread:
            # An unreadable bar never makes a group look different: it joins a
            # variant with the same shape / type / quantity when there is one.
            home = next((key for key in variants if key[:3] == _cheap_signature(row)),
                        _cheap_signature(row) + (None,))
            variants.setdefault(home, []).append(row)
        if len(variants) < 2:
            continue
        ordered = sorted(variants.values(), key=lambda group: (len(group), group[0].id))
        first = ordered[0][0]
        ids = sorted(r.id for r in members)
        where = (u"in %s" % partition) if partition else u"without a partition"
        detail = (u"Number %s %s used by %d different bars (%s)"
                  % (number, where, len(variants), plural(len(members), u"element")))
        out.append(_row_issue(CHECK_DUP, first, detail, marks, element_ids=ids))
    return out


def bbox_outside_mm(box, outer):
    """Largest distance (mm) by which `box` sticks out of `outer`; 0 when inside.

    Both are ``((minx, miny, minz), (maxx, maxy, maxz))`` in mm.
    """
    worst = 0.0
    for axis in range(3):
        worst = max(worst, outer[0][axis] - box[0][axis], box[1][axis] - outer[1][axis])
    return worst


def check_bbox(rows, rebar_boxes, host_boxes, host_tol_mm=None, marks=None):
    """`bbox`: the bar's bounding box leaves its host's box grown by a tolerance.

    Tolerance per host = max(50 mm, host cover) from ``host_tol_mm``. Bars or
    hosts without a box are skipped (nothing to compare).
    """
    marks = marks or {}
    host_tol_mm = host_tol_mm or {}
    out = []
    for row in rows:
        if row.kind not in BAR_KINDS:
            continue
        box = rebar_boxes.get(row.id)
        outer = host_boxes.get(row.host_id)
        if box is None or outer is None:
            continue
        tolerance = max(MIN_BBOX_TOLERANCE_MM, host_tol_mm.get(row.host_id, 0.0))
        outside = bbox_outside_mm(box, outer)
        if outside > tolerance:
            out.append(_row_issue(
                CHECK_BBOX, row,
                u"Bar extends %d mm outside host %d (tolerance %d mm)"
                % (int(round(outside)), row.host_id, int(round(tolerance))), marks))
    return out


def check_partitions(rows, marks=None):
    """`partition`: a bar (shape-driven or free-form) with an empty Partition."""
    marks = marks or {}
    out = []
    for row in rows:
        if row.kind != "Rebar" or (row.partition or u"").strip():
            continue
        if row.is_link:
            out.append(_row_issue(
                CHECK_PARTITION, row,
                u"No partition — it belongs to a linked model; edit it in that model",
                marks, fix=FIX_MANUAL, fix_reason=SKIP_IN_LINK))
        else:
            out.append(_row_issue(
                CHECK_PARTITION, row,
                u"No partition — Revit cannot number it inside a partition",
                marks, fix=FIX_ASSIGN))
    return out


def check_shapes(rows, marks=None):
    """`shape`: a shape-driven bar whose shape Revit cannot name."""
    marks = marks or {}
    return [_row_issue(CHECK_SHAPE, row, u"Shape not recognised — edit the bar", marks)
            for row in rows
            if row.kind in BAR_KINDS and row.is_shape_driven and not (row.shape or u"").strip()]


class BoxData(object):
    """Bounding boxes read for the `bbox` check (mm). Built by ``collect_boxes``."""

    def __init__(self, rebar_boxes=None, host_boxes=None, host_tol_mm=None):
        self.rebar_boxes = rebar_boxes or {}
        self.host_boxes = host_boxes or {}
        self.host_tol_mm = host_tol_mm or {}


def run_checks(rows, hosts, assemblies, legs=None, boxes=None):
    """Every check, sorted by check order then element id.

    ``legs`` (``{bar_id: leg chain}``) and ``boxes`` (``BoxData``) are the deep
    inputs; ``None`` means the geometry was not read, so ``bbox`` finds nothing
    and ``dup`` compares shape / type / quantity only. ``hosts=None`` means the
    hosts were not read (a stopped scan): the host and member checks are skipped
    instead of flagging every bar.
    """
    marks = dict((a.id, a.mark) for a in assemblies)
    legs_of = legs.get if legs is not None else None
    issues = []
    if hosts is not None:
        issues.extend(check_hosts(rows, hosts, marks))
        issues.extend(check_members(rows, hosts, marks))
    issues.extend(check_drawings(assemblies))
    issues.extend(check_duplicates(rows, legs_of, marks))
    if boxes is not None:
        issues.extend(check_bbox(rows, boxes.rebar_boxes, boxes.host_boxes,
                                 boxes.host_tol_mm, marks))
    issues.extend(check_partitions(rows, marks))
    issues.extend(check_shapes(rows, marks))
    issues.sort(key=lambda i: (CHECK_ORDER.index(i.check) if i.check in CHECK_ORDER else 99,
                               i.element_id))
    return issues


# ── FILTER, SUMMARY, FIX PLAN (pure) ─────────────────────────────────────────

def filter_issues(issues, check=None, scope=SCOPE_ALL, text=u""):
    """Chips and search box: `check` is one id or None (all), `scope` is
    all / in assemblies / loose (A8), `text` matches every visible column."""
    needle = (text or u"").strip().lower()
    out = []
    for issue in issues:
        if check and issue.check != check:
            continue
        if scope == SCOPE_IN_ASSEMBLY and issue.assembly_id == NO_ASSEMBLY:
            continue
        if scope == SCOPE_LOOSE and issue.assembly_id != NO_ASSEMBLY:
            continue
        if needle:
            hay = u" ".join((issue.check_label, u"%d" % issue.element_id, issue.detail,
                             issue.category, issue.assembly_mark, issue.partition,
                             issue.number, issue.fix_label)).lower()
            if needle not in hay:
                continue
        out.append(issue)
    return out


def summarize(rows, issues, assemblies):
    """Numbers of the summary strip.

    ``flagged`` counts distinct reinforcement elements with at least one issue
    (assembly rows excluded); ``pass_rate`` is the share of rebar elements
    without any, 0..100 (100 for an empty model).
    """
    row_ids = set(r.id for r in rows)
    flagged = set()
    for issue in issues:
        if issue.kind == KIND_ASSEMBLY:
            continue
        flagged.update(i for i in issue.element_ids if i in row_ids)
    total = len(rows)
    rate = 100 if total == 0 else int(round(100.0 * (total - len(flagged)) / total))
    return {"total": total, "issues": len(issues), "assemblies": len(assemblies),
            "flagged": len(flagged), "pass_rate": rate}


def ready_text(summary):
    """Status line after a scan."""
    counts = (u"%s, %s" % (fmt_count(summary["total"]) + u" rebar",
                           plural(summary["assemblies"], u"assembly", u"assemblies")))
    if summary["issues"]:
        return u"Ready — %s in %s" % (plural(summary["issues"], u"issue"), counts)
    return u"Ready — no issues in %s" % counts


def empty_text(total_rebar, total_issues):
    """Empty state of the grid; the count is injected here, not in the XAML."""
    if total_issues:
        return (u"No issue matches this filter — %s in total.\n"
                u"Change the filter or clear the search box."
                % plural(total_issues, u"issue"))
    return (u"No issues found — %s rebar checked.\n"
            u"Change the filter or rescan after editing the model." % fmt_count(total_rebar))


def issue_element_ids(issues):
    """Unique element ids behind `issues`, in row order (for select / isolate)."""
    seen = set()
    out = []
    for issue in issues:
        for element_id in issue.element_ids:
            if element_id not in seen:
                seen.add(element_id)
                out.append(element_id)
    return out


class FixPlan(object):
    """What the Fix button will do for a set of ticked rows."""

    def __init__(self):
        self.sync_assembly_ids = []     # assemblies to run Sync rebar on
        self.sync_rebar_ids = []        # the ticked bars behind them
        self.assign_ids = []            # bars to hand to Cast Unit Manager
        self.manual = []                # rows nothing can fix automatically

    @property
    def fixable(self):
        return bool(self.sync_assembly_ids or self.assign_ids) and not self.manual


def plan_fix(issues):
    """Group ticked rows by fix. Any Manual row makes the whole plan not fixable
    (the Fix button stays disabled until the selection is only fixable rows)."""
    plan = FixPlan()
    for issue in issues:
        if issue.fix == FIX_SYNC:
            if issue.assembly_id not in plan.sync_assembly_ids:
                plan.sync_assembly_ids.append(issue.assembly_id)
            plan.sync_rebar_ids.extend(issue.element_ids)
        elif issue.fix == FIX_ASSIGN:
            plan.assign_ids.extend(issue.element_ids)
        else:
            plan.manual.append(issue)
    return plan


def fix_summary(results):
    """'Synced 12 rebar into 3 assemblies · 1 skipped · 0 failed' for Sync rows."""
    ok = [r for r in results if r.status == "ok"]
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")
    added = sum(r.count for r in ok)
    return (u"Synced %s into %s · %d skipped · %d failed"
            % (plural(added, u"rebar element"), plural(len(ok), u"assembly", u"assemblies"),
               skipped, failed))


# ── REVIT: SCAN [REVIT] ──────────────────────────────────────────────────────

class ScanData(object):
    """Everything one read-only scan collected. ``stopped`` means the user
    pressed Stop and the data is partial."""

    def __init__(self):
        self.index = None
        self.hosts = None       # {id: HostRecord}; None = not read (host checks are skipped)
        self.assemblies = []
        self.stopped = False

    @property
    def rows(self):
        return self.index.rows if self.index is not None else []


def _report(progress, done, total, label, every=25):
    """Call progress about every `every` items; False means stop."""
    if progress is None:
        return True
    if done % every == 1 or done == total:
        return progress(done, total, label) is not False
    return True


def _host_record(doc, element, host_id):
    record = HostRecord(id=host_id)
    try:
        record.name = elem_name(element)
    except Exception:
        pass
    try:
        record.category = element.Category.Name
    except Exception:
        pass
    try:
        record.assembly_id = eid_value(element.AssemblyInstanceId)
    except Exception:
        pass
    try:
        record.in_group = eid_value(element.GroupId) >= 0
    except Exception:
        pass
    try:
        from Snippets._assembly import _is_valid_host
        record.is_valid_host = bool(_is_valid_host(element))
    except Exception:
        record.is_valid_host = True
    return record


def collect_host_info(doc, rows, progress=None):
    """``({host_id: HostRecord}, stopped)`` for the hosts that still exist.

    A host id with no element in the document is simply absent from the dict,
    which is how ``check_hosts`` knows it is gone.
    """
    ids = sorted(set(r.host_id for r in rows if r.host_id >= 0))
    out = {}
    for index, host_id in enumerate(ids):
        if not _report(progress, index + 1, len(ids), u"Reading hosts"):
            return out, True
        try:
            element = doc.GetElement(make_eid(host_id))
            if element is not None:
                out[host_id] = _host_record(doc, element, host_id)
        except Exception:
            continue
    return out, False


def scan(doc, progress=None):
    """Read the whole model once: rebar index, assemblies and hosts. Read-only.

    ``progress(index, total, label)`` returning False stops; ``ScanData.stopped``
    is then True and the checks run on what was read.
    """
    from Snippets._assembly import collect_assemblies
    from Snippets._rebar import build_rebar_index
    data = ScanData()
    data.index = build_rebar_index(doc, progress=progress)
    if data.index.stopped:
        data.stopped = True
        return data
    data.assemblies = collect_assemblies(doc)
    hosts, data.stopped = collect_host_info(doc, data.index.rows, progress=progress)
    # A host list cut short would flag every bar as "host missing": leave it unread.
    data.hosts = None if data.stopped else hosts
    return data


# ── REVIT: DEEP INPUTS [REVIT] ───────────────────────────────────────────────

def _box_mm(element):
    """Bounding box of `element` in mm as ((min), (max)), or None."""
    try:
        box = element.get_BoundingBox(None)
        if box is None:
            return None
        return ((to_mm(box.Min.X), to_mm(box.Min.Y), to_mm(box.Min.Z)),
                (to_mm(box.Max.X), to_mm(box.Max.Y), to_mm(box.Max.Z)))
    except Exception:
        return None


def _cover_mm(host):
    """Common cover distance of a host in mm, 0 when Revit does not say.

    [NV] RebarHostData.GetCommonCoverType().CoverDistance: any failure leaves
    the plain 50 mm tolerance.
    """
    try:
        from Snippets._compat import _revit_type
        host_data = _revit_type("RebarHostData")
        data = host_data.GetRebarHostData(host)
        cover = data.GetCommonCoverType() if data is not None else None
        return to_mm(cover.CoverDistance) if cover is not None else 0.0
    except Exception:
        return 0.0


def collect_boxes(doc, rows, hosts, progress=None):
    """``(BoxData, stopped)`` for the bars that have a known host (the slow
    part of the `bbox` check, run on demand)."""
    bars = [r for r in rows if r.kind in BAR_KINDS and r.host_id in hosts]
    data = BoxData()
    for index, row in enumerate(bars):
        if not _report(progress, index + 1, len(bars), u"Reading bar boxes"):
            return data, True
        try:
            box = _box_mm(doc.GetElement(make_eid(row.id)))
            if box is not None:
                data.rebar_boxes[row.id] = box
        except Exception:
            continue
    for host_id in sorted(set(r.host_id for r in bars)):
        try:
            host = doc.GetElement(make_eid(host_id))
            box = _box_mm(host)
            if box is not None:
                data.host_boxes[host_id] = box
                data.host_tol_mm[host_id] = _cover_mm(host)
        except Exception:
            continue
    return data, False


def _chord_length(curves_info):
    total = 0.0
    for curve in curves_info:
        a, b = curve[1], curve[2]
        total += sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5
    return total


def bar_legs(rebar):
    """Hashable leg chain of one bar's first position, or None when unreadable.

    Planar straight-leg bars give ``canonical_legs``; arcs or a non-planar
    chain give ``("free", curve count, rounded chord length)`` so two such bars
    only match when they are plainly the same. [NV] centre-line reading (spike G12).
    """
    from Snippets._rebar import (centerline_points, centerline_to_legs,
                                 classify_centerline, curves_to_polyline)
    info = centerline_points(rebar, 0)
    if not info:
        return None
    if classify_centerline(info) == "planar_lines":
        chain = centerline_to_legs(curves_to_polyline(info))
        if chain.planar:
            return canonical_legs(chain.legs)
    return ("free", len(info), int(round(_chord_length(info))))


def collect_legs(doc, ids, progress=None):
    """``({bar_id: leg chain}, stopped)`` for `ids`. A bar that cannot be read is
    left out of the dict, so it never makes a group look different."""
    out = {}
    for index, bar_id in enumerate(ids):
        if not _report(progress, index + 1, len(ids), u"Reading bar shapes", every=10):
            return out, True
        try:
            legs = bar_legs(doc.GetElement(make_eid(bar_id)))
            if legs is not None:
                out[bar_id] = legs
        except Exception:
            continue
    return out, False


# ── REVIT: ACTIONS [REVIT] ───────────────────────────────────────────────────

def _rollback(txn):
    try:
        if not txn.HasEnded():
            txn.RollBack()
    except Exception:
        pass


def sync_assemblies(doc, assembly_ids, rebar_index, progress=None):
    """Fix `member` rows: ``_assembly.sync_rebar`` inside ONE TransactionGroup
    "T3Lab: Sync rebar" (one Ctrl+Z, rule A6; one Transaction per assembly
    inside it). Returns the ``[Result]`` rows; raises only when the group
    itself fails, after rolling it back.

    Sync adds every loose bar hosted by the assembly's members, not just the
    bars that were ticked — the caller says so in its confirmation.
    """
    from Autodesk.Revit.DB import TransactionGroup
    from Snippets._assembly import sync_rebar
    group = TransactionGroup(doc, TRANSACTION_PREFIX + u"Sync rebar")
    with disposing(group) as tg:
        tg.Start()
        try:
            results = sync_rebar(doc, list(assembly_ids), rebar_index, progress=progress)
            tg.Assimilate()
        except Exception:
            _rollback(tg)
            raise
    return results


def isolate_in_active_view(doc, uidoc, ids):
    """Temporarily isolate `ids` in the active view. Returns ``(count, error)``;
    `error` is None on success, else the sentence to show the user.

    View-local and undone by Revit's "Reset Temporary Hide/Isolate". Needs a
    model view (not a sheet or schedule).
    """
    from Autodesk.Revit.DB import ElementId, Transaction
    view = None
    try:
        view = uidoc.ActiveView if uidoc is not None else doc.ActiveView
    except Exception:
        view = None
    if view is None:
        return 0, ISOLATE_NEEDS_MODEL_VIEW
    try:
        if not view.CanUseTemporaryVisibilityModes():
            return 0, ISOLATE_NEEDS_MODEL_VIEW
    except Exception:
        pass            # older release or odd view: let IsolateElementsTemporary decide
    wanted = [make_eid(i) for i in ids]
    if not wanted:
        return 0, u"Nothing to isolate. Tick rows or change the filter, then try again."
    txn = Transaction(doc, TRANSACTION_PREFIX + u"Isolate in view")
    with disposing(txn) as t:
        t.Start()
        try:
            view.IsolateElementsTemporary(net_list(ElementId, wanted))
            t.Commit()
        except Exception as exc:
            _rollback(t)
            return 0, (u"Revit could not isolate the elements in this view: %s. "
                       u"Open a plan, section or 3D view and try again." % short_error(exc))
    return len(wanted), None
