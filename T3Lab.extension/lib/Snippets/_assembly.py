# -*- coding: utf-8 -*-
"""
_assembly.py
============
Assembly (cast unit) helpers for the Rebar & Assembly toolkit.

Two halves, kept apart on purpose:

* PURE PYTHON  - records, the skip rules (A1/A3/A4/A5), batch planning and
  series naming. No Revit import at module level, so ``dev/test_assembly_rules.py``
  exercises the shipped source without Revit.
* REVIT        - collectors and mutations. ``Autodesk.Revit.DB`` is imported
  inside each function. Mutations never raise per item: they return
  ``Result`` rows (``ok`` / ``skipped`` / ``failed``) and run inside the
  CALLER's TransactionGroup, one Transaction per assembly (rule A6).

Spec: dev/plan/rebar-tekla-implementation-spec.md section 3.2.
Everything marked [NV] is unverified until dev/debug/spike_rebar_assembly.py
has run on Revit 2027; each such call degrades to a ``failed`` / ``skipped``
row with the Revit message instead of an exception.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"
__title__ = "Assembly"

from collections import namedtuple

from Snippets._compat import (
    disposing,
    eid_value,
    elem_name,
    make_eid,
    net_list,
    short_error,
)


# ── CONSTANTS ────────────────────────────────────────────────────────────────

SKIP_IN_GROUP = u"in group"
SKIP_IN_LINK = u"from link"
SKIP_IN_ASSEMBLY = u"already in assembly %s"
SKIP_NOT_VALID = u"not valid for assembly"
SKIP_NO_HOST = u"host missing"
SKIP_STOPPED = u"stopped"

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUSES = (STATUS_OK, STATUS_SKIPPED, STATUS_FAILED)

NO_ASSEMBLY = -1

TRANSACTION_PREFIX = u"T3Lab: "

# Valid rebar hosts the tools look at (Revit 2022-2027 BuiltInCategory names).
HOST_CATEGORY_NAMES = ("OST_StructuralFraming", "OST_StructuralColumns",
                       "OST_StructuralFoundation", "OST_Floors", "OST_Walls")
# Categories of reinforcement elements; used to tell rebar members from hosts.
REBAR_CATEGORY_NAMES = ("OST_Rebar", "OST_AreaReinforcement", "OST_PathReinforcement",
                        "OST_FabricAreas", "OST_FabricReinforcement", "OST_Coupler")


# ── RECORDS (pure) ───────────────────────────────────────────────────────────

class _Record(object):
    """Plain attribute bag; every field has a default so tests can build rows."""

    _FIELDS = ()

    def __init__(self, **kwargs):
        for name, default in self._FIELDS:
            value = kwargs.pop(name, default)
            if isinstance(default, list) and value is default:
                value = list(default)
            setattr(self, name, value)
        if kwargs:
            raise TypeError("%s got unknown field(s): %s"
                            % (type(self).__name__, ", ".join(sorted(kwargs))))

    def __repr__(self):
        parts = ["%s=%r" % (name, getattr(self, name)) for name, _ in self._FIELDS[:4]]
        return "<%s %s>" % (type(self).__name__, " ".join(parts))


class HostRecord(_Record):
    """One rebar host (beam, column, foundation, floor, wall)."""
    _FIELDS = (("id", -1), ("name", u""), ("category", u""), ("level", u""),
               ("workset", u""), ("type_name", u""), ("assembly_id", NO_ASSEMBLY),
               ("in_group", False), ("is_link", False), ("is_valid_host", True))


class RebarRecord(_Record):
    """One reinforcement element (bar set, system, area/path, fabric, coupler).

    ``in_group`` / ``is_link`` are additions to the spec's field list: rule A5
    applies to rebar rows too (they are reported "Manual" by Rebar Check).
    """
    _FIELDS = (("id", -1), ("kind", u"Rebar"), ("host_id", -1),
               ("assembly_id", NO_ASSEMBLY), ("partition", u""), ("number", u""),
               ("mark", u""), ("bar_type", u""), ("diameter_mm", 0.0),
               ("quantity", 1), ("shape", u""), ("is_shape_driven", False),
               ("system_id", -1), ("in_group", False), ("is_link", False))


class AssemblyRecord(_Record):
    """One AssemblyInstance. ``center`` is (x, y, z) in feet."""
    _FIELDS = (("id", -1), ("type_id", -1), ("mark", u""), ("naming_category", u""),
               ("instances", 1), ("members", []), ("rebar_ids", []),
               ("view_ids", []), ("sheet_ids", []), ("level", u""),
               ("center", (0.0, 0.0, 0.0)))


class Result(namedtuple("Result", "name status count detail")):
    """One row of a batch outcome (same shape as family_transfer.Result).

    status is always ``ok`` | ``skipped`` | ``failed`` - anything else is a bug
    and raises immediately instead of reaching the status column.
    """
    __slots__ = ()

    def __new__(cls, name, status, count=0, detail=u""):
        if status not in STATUSES:
            raise ValueError("Result status must be one of %s, got %r"
                             % (", ".join(STATUSES), status))
        return super(Result, cls).__new__(cls, name, status, count, detail)


def summarize_results(results):
    """{'ok': n, 'skipped': n, 'failed': n, 'count': n} - count sums the ok rows."""
    out = {STATUS_OK: 0, STATUS_SKIPPED: 0, STATUS_FAILED: 0, "count": 0}
    for row in results:
        out[row.status] = out.get(row.status, 0) + 1
        if row.status == STATUS_OK:
            out["count"] += row.count
    return out


class BatchPlan(object):
    """What ONE AssemblyInstance.Create call will receive.

    ``host_ids`` empty means "nothing to create": the plan only carries the
    skipped host so its row still shows up with a reason.
    """

    def __init__(self, host_ids, rebar_ids, naming_host_id, skips=None, label=u""):
        self.host_ids = list(host_ids)
        self.rebar_ids = list(rebar_ids)
        self.naming_host_id = naming_host_id
        self.skips = list(skips or [])
        self.label = label

    def __repr__(self):
        return "<BatchPlan hosts=%r rebar=%d skips=%d>" % (
            self.host_ids, len(self.rebar_ids), len(self.skips))


# ── PURE RULES ───────────────────────────────────────────────────────────────

def _skip_reason(record, allow_assembly_id):
    """Why `record` may not become / stay an assembly member, or None (A5, A3)."""
    if getattr(record, "in_group", False):
        return SKIP_IN_GROUP
    if getattr(record, "is_link", False):
        return SKIP_IN_LINK
    owner = getattr(record, "assembly_id", NO_ASSEMBLY)
    if owner not in (NO_ASSEMBLY, allow_assembly_id):
        return SKIP_IN_ASSEMBLY % owner
    return None


def filter_assembly_candidates(records, allow_assembly_id=NO_ASSEMBLY):
    """A5 / A3: split records into ``(ok, [(record, reason)])``.

    Reasons, in priority order: in a group, from a link, member of another
    assembly (``allow_assembly_id`` is the one assembly it may already belong
    to - Sync rebar passes the target assembly).
    """
    ok = []
    skipped = []
    for record in records:
        reason = _skip_reason(record, allow_assembly_id)
        if reason is None:
            ok.append(record)
        else:
            skipped.append((record, reason))
    return ok, skipped


def _unique_by_id(records):
    seen = set()
    out = []
    for record in records:
        if record.id in seen:
            continue
        seen.add(record.id)
        out.append(record)
    return out


def _rebar_ids_for(host_id, rebar_by_host, skips, seen):
    """Loose rebar ids of one host; rows that cannot join are added to `skips`."""
    ids = []
    ok, skipped = filter_assembly_candidates(rebar_by_host.get(host_id, ()))
    for record, reason in skipped:
        skips.append((record.id, reason))
    for record in ok:
        if record.id not in seen:
            seen.add(record.id)
            ids.append(record.id)
    return ids


def plan_batch_create(hosts, rebar_by_host, one_per_host=True, include_rebar=True):
    """Plan the AssemblyInstance.Create calls for a batch of hosts.

    ``one_per_host`` gives one BatchPlan per host (a skipped host gets an empty
    plan carrying its reason, in input order); otherwise a single plan holds
    every creatable host and the first one names the assembly. Hosts in a
    group, from a link or already in an assembly are skipped (A5, A3). Loose
    rebar of the hosts is added unless ``include_rebar`` is False.
    """
    rebar_by_host = rebar_by_host or {}
    hosts = _unique_by_id(hosts)
    seen_rebar = set()

    if one_per_host:
        plans = []
        for host in hosts:
            reason = _skip_reason(host, NO_ASSEMBLY)
            if reason is not None:
                plans.append(BatchPlan([], [], host.id, [(host.id, reason)],
                                       getattr(host, "name", u"")))
                continue
            skips = []
            rebar = (_rebar_ids_for(host.id, rebar_by_host, skips, seen_rebar)
                     if include_rebar else [])
            plans.append(BatchPlan([host.id], rebar, host.id, skips,
                                   getattr(host, "name", u"")))
        return plans

    if not hosts:
        return []
    ok, skipped = filter_assembly_candidates(hosts)
    skips = [(record.id, reason) for record, reason in skipped]
    if not ok:
        return [BatchPlan([], [], hosts[0].id, skips, getattr(hosts[0], "name", u""))]
    rebar = []
    if include_rebar:
        for host in ok:
            rebar.extend(_rebar_ids_for(host.id, rebar_by_host, skips, seen_rebar))
    return [BatchPlan([h.id for h in ok], rebar, ok[0].id, skips,
                      getattr(ok[0], "name", u""))]


def _series_name(prefix, number, digits):
    if digits and digits > 0:
        return u"%s%0*d" % (prefix, digits, number)
    return u"%s%d" % (prefix, number)


def series_names(count, prefix, start, step, digits):
    """['C-001', 'C-002', ...]; digits=0 means no zero padding."""
    return [_series_name(prefix, start + i * step, digits)
            for i in range(max(0, int(count)))]


def diff_type_split(before, after):
    """A4 report from ``{assembly_id: type_id}`` before and after a rename.

    Returns ``(split_count, merged_count, changed_ids)``:
    * split  - assembly types that exist now and did not before (Revit made a
      new type for an instance that no longer matched its siblings);
    * merged - assembly types that vanished (their instances joined another);
    * changed_ids - assemblies present in both whose type changed, sorted.
    """
    before_types = set(before.values())
    after_types = set(after.values())
    changed = sorted(i for i in before if i in after and before[i] != after[i])
    return len(after_types - before_types), len(before_types - after_types), changed


def expand_selection(records_by_id, selected_ids, assembly_members):
    """A1: host ids for a selection that may contain AssemblyInstances.

    An assembly id expands to its member ids; only ids that are hosts
    (present in ``records_by_id``) survive. Order is selection order, unique.
    """
    out = []
    seen = set()

    def take(identifier):
        if identifier in records_by_id and identifier not in seen:
            seen.add(identifier)
            out.append(identifier)

    for identifier in selected_ids:
        if identifier in assembly_members:
            for member in assembly_members[identifier]:
                take(member)
        else:
            take(identifier)
    return out


# ── REVIT HELPERS (private) ──────────────────────────────────────────────────

def _int(value):
    """int for a BuiltInCategory member / ElementId / int."""
    try:
        return int(value)
    except Exception:
        return eid_value(value)


def _category_ints(names):
    """BuiltInCategory int values for `names`, skipping names this release lacks."""
    from Autodesk.Revit.DB import BuiltInCategory
    out = []
    for name in names:
        member = getattr(BuiltInCategory, name, None)
        if member is not None:
            out.append(int(member))
    return out


def _rollback(txn):
    try:
        if not txn.HasEnded():
            txn.RollBack()
    except Exception:
        pass


def _is_valid_host(element):
    """RebarHostData.IsValidHost, False on any error."""
    if element is None:
        return False
    try:
        from Snippets._compat import _revit_type
        host_data = _revit_type("RebarHostData")
        return bool(host_data.IsValidHost(element))
    except Exception:
        return False


def _is_rebar_like(element, rebar_cats):
    try:
        return eid_value(element.Category.Id) in rebar_cats
    except Exception:
        return False


def _assembly_mark(assembly):
    try:
        return assembly.AssemblyTypeName or u""
    except Exception:
        return u""


def _label_of(plan):
    return plan.label or (u"host %s" % plan.naming_host_id)


def _stopped_rows(items, label_fn):
    return [Result(label_fn(item), STATUS_SKIPPED, 0, SKIP_STOPPED) for item in items]


def _category_name(doc, category_id):
    try:
        from Autodesk.Revit.DB import Category
        category = Category.GetCategory(doc, category_id)
        return category.Name if category is not None else u""
    except Exception:
        return u""


def _first_level_param(element, doc):
    """Level named by one of the level parameters (Reference / Base / Schedule), or ''."""
    from Autodesk.Revit.DB import BuiltInParameter
    for name in ("LEVEL_PARAM", "INSTANCE_REFERENCE_LEVEL_PARAM", "FAMILY_BASE_LEVEL_PARAM",
                 "INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM",
                 "WALL_BASE_CONSTRAINT", "FAMILY_LEVEL_PARAM"):
        built_in = getattr(BuiltInParameter, name, None)
        if built_in is None:
            continue
        try:
            param = element.get_Parameter(built_in)
            if param is None or not param.HasValue:
                continue
            level = doc.GetElement(param.AsElementId())
            if level is not None:
                return elem_name(level)
        except Exception:
            continue
    return u""


# ── COLLECTORS [REVIT] ───────────────────────────────────────────────────────

def level_name_of(doc, element):
    """Level / Reference Level / Base Level / Schedule Level name, '' when none."""
    if element is None:
        return u""
    try:
        level_id = element.LevelId
        if eid_value(level_id) >= 0:
            level = doc.GetElement(level_id)
            if level is not None:
                return elem_name(level)
    except Exception:
        pass
    try:
        return _first_level_param(element, doc)
    except Exception:
        return u""


def workset_name_of(doc, element):
    """Workset name of an element, '' in a non-workshared model."""
    try:
        if not doc.IsWorkshared:
            return u""
        return doc.GetWorksetTable().GetWorkset(element.WorksetId).Name
    except Exception:
        return u""


def _workset_int(workset_id):
    """int of a WorksetId (IntegerValue) or of a plain int; None when unreadable."""
    if workset_id is None:
        return None
    for member in ("IntegerValue", "Value"):
        value = getattr(workset_id, member, None)
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    try:
        return int(workset_id)
    except Exception:
        return None


def _host_record(doc, element, type_names, valid_host=None):
    """HostRecord for one element; a field that cannot be read stays at its default."""
    record = HostRecord(id=eid_value(element.Id))
    try:
        record.name = elem_name(element)
    except Exception:
        pass
    try:
        record.category = element.Category.Name
    except Exception:
        pass
    record.level = level_name_of(doc, element)
    record.workset = workset_name_of(doc, element)
    try:
        type_id = eid_value(element.GetTypeId())
        if type_id not in type_names:
            type_el = doc.GetElement(element.GetTypeId())
            type_names[type_id] = elem_name(type_el) if type_el is not None else u""
        record.type_name = type_names[type_id]
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
    record.is_valid_host = _is_valid_host(element) if valid_host is None else valid_host
    return record


def collect_hosts(doc, category_ids=None, level_id=None, workset_id=None, type_ids=None):
    """HostRecords of valid rebar hosts in the model.

    Default categories: structural framing, columns, foundation, floors, walls.
    ``category_ids`` (BuiltInCategory values / ids) narrows them; ``level_id``,
    ``workset_id`` and ``type_ids`` filter on the element.
    """
    from Autodesk.Revit.DB import (BuiltInCategory, ElementMulticategoryFilter,
                                   FilteredElementCollector)
    if category_ids:
        wanted = [_int(c) for c in category_ids]
    else:
        wanted = _category_ints(HOST_CATEGORY_NAMES)
    members = [BuiltInCategory(value) for value in wanted]
    if not members:
        return []

    with disposing(FilteredElementCollector(doc)) as collector:
        elements = list(collector.WherePasses(
            ElementMulticategoryFilter(net_list(BuiltInCategory, members)))
            .WhereElementIsNotElementType().ToElements())

    level_wanted = eid_value(level_id) if level_id is not None else None
    workset_wanted = _workset_int(workset_id)
    type_wanted = set(eid_value(t) for t in type_ids) if type_ids else None

    type_names = {}
    out = []
    for element in elements:
        try:
            if not _is_valid_host(element):
                continue
            if type_wanted is not None and eid_value(element.GetTypeId()) not in type_wanted:
                continue
            if workset_wanted is not None and _workset_int(element.WorksetId) != workset_wanted:
                continue
            if level_wanted is not None:
                level_here = -1
                try:
                    level_here = eid_value(element.LevelId)
                except Exception:
                    pass
                if level_here != level_wanted:
                    continue
            out.append(_host_record(doc, element, type_names, valid_host=True))
        except Exception:
            continue
    return out


def collect_assembly_views(doc):
    """{assembly_id: ([view ids], [sheet ids])} from View.AssociatedAssemblyInstanceId (A7).

    Views and schedules count as views, sheets are listed apart; view templates
    are ignored. [NV] AssociatedAssemblyInstanceId is read per view; a view
    that does not expose it is simply not listed.
    """
    from Autodesk.Revit.DB import FilteredElementCollector, View, ViewSheet
    out = {}
    with disposing(FilteredElementCollector(doc)) as collector:
        views = list(collector.OfClass(View).ToElements())
    for view in views:
        try:
            if view.IsTemplate:
                continue
            owner = eid_value(view.AssociatedAssemblyInstanceId)
            if owner < 0:
                continue
            bucket = out.setdefault(owner, ([], []))
            (bucket[1] if isinstance(view, ViewSheet) else bucket[0]).append(
                eid_value(view.Id))
        except Exception:
            continue
    return out


def assembly_type_map(doc):
    """{assembly_id: assembly type id} for every AssemblyInstance (input of diff_type_split)."""
    from Autodesk.Revit.DB import AssemblyInstance, FilteredElementCollector
    with disposing(FilteredElementCollector(doc)) as collector:
        instances = list(collector.OfClass(AssemblyInstance).ToElements())
    out = {}
    for instance in instances:
        try:
            out[eid_value(instance.Id)] = eid_value(instance.GetTypeId())
        except Exception:
            continue
    return out


def collect_assemblies(doc):
    """One AssemblyRecord per AssemblyInstance; ``instances`` counts its type."""
    from Autodesk.Revit.DB import AssemblyInstance, FilteredElementCollector
    with disposing(FilteredElementCollector(doc)) as collector:
        instances = list(collector.OfClass(AssemblyInstance).ToElements())
    views = collect_assembly_views(doc)
    rebar_cats = set(_category_ints(REBAR_CATEGORY_NAMES))

    records = []
    per_type = {}
    for instance in instances:
        record = AssemblyRecord(id=eid_value(instance.Id))
        try:
            record.type_id = eid_value(instance.GetTypeId())
            record.mark = _assembly_mark(instance)
            record.naming_category = _category_name(doc, instance.NamingCategoryId)
            center = instance.GetCenter()
            record.center = (float(center.X), float(center.Y), float(center.Z))
        except Exception:
            pass
        try:
            first_host = None
            for member_id in instance.GetMemberIds():
                member = eid_value(member_id)
                record.members.append(member)
                element = doc.GetElement(member_id)
                if _is_rebar_like(element, rebar_cats):
                    record.rebar_ids.append(member)
                elif first_host is None:
                    first_host = element
            if first_host is not None:
                record.level = level_name_of(doc, first_host)
        except Exception:
            pass
        view_ids, sheet_ids = views.get(record.id, ([], []))
        record.view_ids = list(view_ids)
        record.sheet_ids = list(sheet_ids)
        per_type[record.type_id] = per_type.get(record.type_id, 0) + 1
        records.append(record)
    for record in records:
        record.instances = per_type.get(record.type_id, 1)
    return records


def host_records_from_selection(doc, uidoc):
    """A1: HostRecords for the current selection.

    The selection may hold hosts, rebar (-> their host) and AssemblyInstances
    (-> every member that is a valid host). Order is selection order, unique.
    """
    from Autodesk.Revit.DB import AssemblyInstance
    from Snippets._rebar import host_id_of
    rebar_cats = set(_category_ints(REBAR_CATEGORY_NAMES))
    type_names = {}
    out = []
    seen = set()

    def add(element):
        if element is None or not _is_valid_host(element):
            return
        key = eid_value(element.Id)
        if key in seen:
            return
        seen.add(key)
        out.append(_host_record(doc, element, type_names, valid_host=True))

    for raw in uidoc.Selection.GetElementIds():
        element = doc.GetElement(raw)
        if element is None:
            continue
        try:
            if isinstance(element, AssemblyInstance):
                for member_id in element.GetMemberIds():
                    add(doc.GetElement(member_id))
            elif _is_rebar_like(element, rebar_cats):
                host_id = host_id_of(doc, element)
                if host_id >= 0:
                    add(doc.GetElement(make_eid(host_id)))
            else:
                add(element)
        except Exception:
            continue
    return out


# ── MUTATIONS [REVIT] ────────────────────────────────────────────────────────

def _create_assembly(doc, plan, prefix, number, digits):
    """Create one assembly for `plan`; returns (Result, new_assembly_id_or_None)."""
    from Autodesk.Revit.DB import AssemblyInstance, ElementId, Transaction
    label = _label_of(plan)
    host = doc.GetElement(make_eid(plan.naming_host_id))
    if host is None:
        return Result(label, STATUS_SKIPPED, 0, SKIP_NO_HOST), None

    try:
        naming_category = host.Category.Id
        wanted = [make_eid(i) for i in plan.host_ids + plan.rebar_ids]
        ids = net_list(ElementId, wanted)
        note = u""
        if not AssemblyInstance.AreElementsValidForAssembly(doc, ids, ElementId.InvalidElementId):
            if plan.rebar_ids:
                # Retry with the hosts alone: one bad bar must not block the host.
                ids = net_list(ElementId, [make_eid(i) for i in plan.host_ids])
                note = u" (rebar left out: %d element(s) not valid for assembly)" % len(plan.rebar_ids)
            if not plan.rebar_ids or not AssemblyInstance.AreElementsValidForAssembly(
                    doc, ids, ElementId.InvalidElementId):
                return Result(label, STATUS_SKIPPED, 0, SKIP_NOT_VALID), None
        member_count = ids.Count

        txn = Transaction(doc, TRANSACTION_PREFIX + u"Create assembly")
        with disposing(txn) as t:
            t.Start()
            try:
                instance = AssemblyInstance.Create(doc, ids, naming_category)
                new_id = eid_value(instance.Id)
                t.Commit()
            except Exception:
                _rollback(t)
                raise
    except Exception as exc:
        return Result(label, STATUS_FAILED, 0, short_error(exc)), None

    detail = u"assembly %d%s" % (new_id, note)
    if prefix:
        name = _series_name(prefix, number, digits)
        # Revit needs the creating transaction committed before the name is set.
        try:
            with disposing(Transaction(doc, TRANSACTION_PREFIX + u"Name assembly")) as t:
                t.Start()
                try:
                    doc.GetElement(make_eid(new_id)).AssemblyTypeName = name
                    t.Commit()
                except Exception:
                    _rollback(t)
                    raise
            detail = u"assembly %d named %s%s" % (new_id, name, note)
        except Exception as exc:
            detail = u"assembly %d created; could not name it %s: %s%s" % (
                new_id, name, short_error(exc), note)
    return Result(label, STATUS_OK, member_count, detail), new_id


def batch_create(doc, plans, progress=None, prefix="", start=1, step=1, digits=3,
                 created=None):
    """Create one assembly per BatchPlan. Run inside the caller's TransactionGroup.

    Per plan: Transaction "Create assembly" -> AssemblyInstance.Create ->
    Commit, then (when ``prefix``) Transaction "Name assembly" sets the series
    name. Type split / merge is Revit's call: the caller reports it with
    ``diff_type_split(assembly_type_map(before), assembly_type_map(after))``.

    ``progress(index, total, label)`` returning False stops; pending plans are
    ``skipped: stopped``. ``created`` (optional list) receives the new assembly
    ids so the caller can select them. Returns [Result] (count = members).
    """
    results = []
    total = len(plans)
    made = 0
    for index, plan in enumerate(plans):
        if progress is not None and progress(index + 1, total, _label_of(plan)) is False:
            results.extend(_stopped_rows(plans[index:], _label_of))
            break
        if not plan.host_ids:
            reason = plan.skips[0][1] if plan.skips else SKIP_NO_HOST
            results.append(Result(_label_of(plan), STATUS_SKIPPED, 0, reason))
            continue
        row, new_id = _create_assembly(doc, plan, prefix, start + made * step, digits)
        results.append(row)
        if new_id is not None:
            made += 1
            if created is not None:
                created.append(new_id)
    return results


def _rebar_by_host(rebar_index):
    """{host_id: [RebarRecord]} from a RebarIndex (or anything with .by_host / .rows)."""
    by_host = getattr(rebar_index, "by_host", None)
    if by_host is not None:
        return by_host
    out = {}
    for row in getattr(rebar_index, "rows", ()):
        out.setdefault(row.host_id, []).append(row)
    return out


def sync_rebar(doc, assembly_ids, rebar_index, progress=None):
    """Add loose rebar of each assembly's hosts to that assembly (rule A2).

    Per assembly: hosts = members that are valid rebar hosts; candidates =
    index rows of those hosts that are in no assembly and in no group / link;
    ``AreElementsValidForAssembly`` is the gate (rows it rejects are counted in
    the detail, the rest still go in); Transaction "Sync rebar into <mark>" ->
    AddMemberIds. Run inside the caller's TransactionGroup. Returns [Result]
    (count = rebar added). The index rows are updated so a second Sync does
    not offer them again.
    """
    from Autodesk.Revit.DB import AssemblyInstance, ElementId, Transaction
    by_host = _rebar_by_host(rebar_index)
    results = []
    total = len(assembly_ids)

    for index, raw in enumerate(assembly_ids):
        assembly_id = eid_value(raw)
        label = u"assembly %d" % assembly_id
        if progress is not None and progress(index + 1, total, label) is False:
            results.extend(_stopped_rows(
                assembly_ids[index:], lambda a: u"assembly %d" % eid_value(a)))
            break
        try:
            assembly = doc.GetElement(make_eid(assembly_id))
            if assembly is None or not isinstance(assembly, AssemblyInstance):
                results.append(Result(label, STATUS_SKIPPED, 0, u"assembly missing"))
                continue
            mark = _assembly_mark(assembly) or label
            label = mark

            host_ids = [eid_value(m) for m in assembly.GetMemberIds()
                        if _is_valid_host(doc.GetElement(m))]
            candidates = []
            for host_id in host_ids:
                rows, _ = filter_assembly_candidates(by_host.get(host_id, ()))
                candidates.extend(rows)
            candidates = _unique_by_id(candidates)
            if not candidates:
                results.append(Result(label, STATUS_SKIPPED, 0,
                                      u"no loose rebar on its hosts"))
                continue

            ids = [make_eid(r.id) for r in candidates]
            keep = candidates
            rejected = 0
            if not AssemblyInstance.AreElementsValidForAssembly(
                    doc, net_list(ElementId, ids), assembly.Id):
                keep = []
                for record in candidates:
                    if AssemblyInstance.AreElementsValidForAssembly(
                            doc, net_list(ElementId, [make_eid(record.id)]), assembly.Id):
                        keep.append(record)
                rejected = len(candidates) - len(keep)
                if not keep:
                    results.append(Result(label, STATUS_SKIPPED, 0, SKIP_NOT_VALID))
                    continue

            txn = Transaction(doc, TRANSACTION_PREFIX + u"Sync rebar into %s" % mark)
            with disposing(txn) as t:
                t.Start()
                try:
                    assembly.AddMemberIds(net_list(ElementId, [make_eid(r.id) for r in keep]))
                    t.Commit()
                except Exception:
                    _rollback(t)
                    raise
            for record in keep:
                record.assembly_id = assembly_id
            detail = u"added %d rebar element(s)" % len(keep)
            if rejected:
                detail += u"; %d not valid for assembly" % rejected
            results.append(Result(label, STATUS_OK, len(keep), detail))
        except Exception as exc:
            results.append(Result(label, STATUS_FAILED, 0, short_error(exc)))
    return results


def _first_instance_per_type(doc):
    from Autodesk.Revit.DB import AssemblyInstance, FilteredElementCollector
    with disposing(FilteredElementCollector(doc)) as collector:
        instances = list(collector.OfClass(AssemblyInstance).ToElements())
    first = {}
    for instance in instances:
        try:
            first.setdefault(eid_value(instance.GetTypeId()), instance)
        except Exception:
            continue
    return first


def rename_series(doc, type_ids_in_order, prefix, start, step, digits):
    """Rename assembly TYPES to a series in ONE Transaction "Rename assembly series".

    ``AssemblyTypeName`` is set once per type, on the first instance of that
    type (when that is refused the type element itself is renamed). Run inside
    the caller's TransactionGroup. Returns ``(results, (split, merged,
    changed_ids))`` - the second item is ``diff_type_split`` over the whole
    model, never silent (A4).
    """
    from Autodesk.Revit.DB import Transaction
    type_ids = [eid_value(t) for t in type_ids_in_order]
    names = series_names(len(type_ids), prefix, start, step, digits)
    first = _first_instance_per_type(doc)
    before = assembly_type_map(doc)
    results = []

    txn = Transaction(doc, TRANSACTION_PREFIX + u"Rename assembly series")
    with disposing(txn) as t:
        t.Start()
        try:
            for type_id, name in zip(type_ids, names):
                instance = first.get(type_id)
                if instance is None:
                    results.append(Result(name, STATUS_SKIPPED, 0,
                                          u"no assembly of this type left"))
                    continue
                old = _assembly_mark(instance)
                if old == name:
                    results.append(Result(name, STATUS_SKIPPED, 0, u"already named so"))
                    continue
                try:
                    instance.AssemblyTypeName = name
                    results.append(Result(name, STATUS_OK, 1, u"%s -> %s" % (old, name)))
                    continue
                except Exception as exc:
                    first_error = short_error(exc)
                try:
                    doc.GetElement(instance.GetTypeId()).Name = name
                    results.append(Result(name, STATUS_OK, 1, u"%s -> %s (type renamed)" % (old, name)))
                except Exception:
                    results.append(Result(name, STATUS_FAILED, 0, first_error))
            after = assembly_type_map(doc)
            t.Commit()
        except Exception:
            _rollback(t)
            raise
    return results, diff_type_split(before, after)


def select_in_revit(uidoc, ids):
    """Select `ids` in the Revit UI; returns how many were selected (0 on failure)."""
    try:
        from Autodesk.Revit.DB import ElementId
        wanted = [make_eid(eid_value(i)) for i in ids]
        uidoc.Selection.SetElementIds(net_list(ElementId, wanted))
        return len(wanted)
    except Exception:
        return 0


def transform_between(src_asm, dst_asm):
    """Transform taking model coordinates of `src_asm` to the same LOCAL spot of `dst_asm`.

    = dst.GetTransform() * src.GetTransform().Inverse (model -> model).
    """
    return dst_asm.GetTransform().Multiply(src_asm.GetTransform().Inverse)
