# -*- coding: utf-8 -*-
"""
_group_ops.py
=============
Revit API helpers for managing **groups** — model groups, detail groups and
attached detail groups:

* collect every group type with its instances, members and worksets
* rename group types in batch (find/replace, prefix, suffix, case, cleanup)
* move group instances (and optionally their members) to another workset
* audit and clean up: unused types, name problems, ungroup, purge

Kept free of any WPF/UI reference so it can be reused by other tools.

Part of T3Lab Extension.
"""

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    Element,
    ElementId,
    FilteredElementCollector,
    FilteredWorksetCollector,
    Group,
    GroupType,
    Transaction,
    WorksetKind,
)

import System


# ── KINDS ────────────────────────────────────────────────────────────────────

KIND_MODEL = "Model"
KIND_DETAIL = "Detail"
KIND_ATTACHED = "Attached"

KINDS = (KIND_MODEL, KIND_DETAIL, KIND_ATTACHED)

# Revit rejects these in any element name.
ILLEGAL_CHARS = "\\:{}[]|;<>?`~"

CASE_KEEP = "Keep as is"
CASE_UPPER = "UPPERCASE"
CASE_LOWER = "lowercase"
CASE_TITLE = "Title Case"

CASE_MODES = (CASE_KEEP, CASE_UPPER, CASE_LOWER, CASE_TITLE)


# ── VERSION SHIMS ────────────────────────────────────────────────────────────

def eid_int(element_id):
    """Integer value of an ElementId, valid on Revit 2023 - 2026+."""
    if element_id is None:
        return -1
    try:
        return element_id.Value          # Revit 2024+
    except AttributeError:
        return element_id.IntegerValue   # Revit 2023


def new_element_id(value):
    """Build an ElementId from a plain int.

    A bare Python int makes the ElementId constructor ambiguous under pythonnet
    (BuiltInParameter / BuiltInCategory / Int64 all match), so the value is
    widened explicitly: Int64 on Revit 2024+, Int32 on Revit 2023.
    """
    try:
        return ElementId(System.Int64(int(value)))
    except Exception:
        pass
    try:
        return ElementId(System.Int32(int(value)))
    except Exception:
        return ElementId(int(value))


def workset_int(workset_id):
    """Integer value of a WorksetId across Revit versions."""
    if workset_id is None:
        return -1
    try:
        return workset_id.IntegerValue
    except AttributeError:
        pass
    try:
        return workset_id.Value
    except AttributeError:
        return -1


def element_name(element):
    """Best-effort name of an element, never raising."""
    if element is None:
        return ""
    try:
        name = Element.Name.__get__(element)
        if name:
            return name
    except Exception:
        pass
    try:
        return element.Name or ""
    except Exception:
        return ""


# ── NAME RULES ───────────────────────────────────────────────────────────────

def illegal_chars_in(name):
    """The forbidden characters present in name, in order, without repeats."""
    found = []
    for char in name or "":
        if char in ILLEGAL_CHARS and char not in found:
            found.append(char)
    return found


def clean_name(name):
    """Strip forbidden characters, collapse repeated spaces, trim the ends."""
    text = name or ""
    for char in ILLEGAL_CHARS:
        text = text.replace(char, "")
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


def apply_case(name, mode):
    """Apply one of the CASE_MODES to name."""
    if not name:
        return name
    if mode == CASE_UPPER:
        return name.upper()
    if mode == CASE_LOWER:
        return name.lower()
    if mode == CASE_TITLE:
        return " ".join(w[:1].upper() + w[1:].lower() if w else w
                        for w in name.split(" "))
    return name


def build_new_name(current, find="", replace="", match_case=False,
                   prefix="", suffix="", case_mode=CASE_KEEP, cleanup=False):
    """Run the rename rules over one name and return the proposed result."""
    text = current or ""

    if find:
        if match_case:
            text = text.replace(find, replace or "")
        else:
            lowered = text.lower()
            needle = find.lower()
            out = []
            i = 0
            while True:
                hit = lowered.find(needle, i)
                if hit < 0:
                    out.append(text[i:])
                    break
                out.append(text[i:hit])
                out.append(replace or "")
                i = hit + len(needle)
            text = "".join(out)

    if prefix:
        text = prefix + text
    if suffix:
        text = text + suffix

    text = apply_case(text, case_mode)

    if cleanup:
        text = clean_name(text)
    return text


def name_problems(name):
    """Human-readable problems with a group name — empty list when it is fine."""
    issues = []
    text = name or ""
    if not text.strip():
        issues.append("Empty name")
        return issues
    bad = illegal_chars_in(text)
    if bad:
        issues.append("Illegal characters %s" % " ".join(bad))
    if text != text.strip():
        issues.append("Leading/trailing spaces")
    if "  " in text:
        issues.append("Double spaces")
    return issues


# ── RECORDS ──────────────────────────────────────────────────────────────────

class GroupTypeRecord(object):
    """One group type in the document, with everything the UI needs to show."""

    def __init__(self, group_type, kind, instances, doc):
        self.group_type = group_type
        self.type_id = eid_int(group_type.Id)
        self.name = element_name(group_type)
        self.kind = kind
        self.instances = list(instances)
        self.instance_count = len(self.instances)
        self.member_count = 0
        self.workset_names = []
        self.attached_count = 0
        self._read_details(doc)

    def _read_details(self, doc):
        first = self.instances[0] if self.instances else None
        if first is not None:
            try:
                self.member_count = len(list(first.GetMemberIds()))
            except Exception:
                self.member_count = 0
            if self.kind == KIND_MODEL:
                try:
                    self.attached_count = len(list(
                        first.GetAvailableAttachedDetailGroupTypeIds()))
                except Exception:
                    self.attached_count = 0

        seen = []
        for inst in self.instances:
            ws_name = instance_workset_name(doc, inst)
            if ws_name and ws_name not in seen:
                seen.append(ws_name)
        self.workset_names = seen

    @property
    def workset_summary(self):
        if not self.workset_names:
            return "—"
        if len(self.workset_names) == 1:
            return self.workset_names[0]
        return "%d worksets" % len(self.workset_names)

    def audit_issues(self):
        """Cleanup findings for this group type, as a list of short phrases."""
        issues = list(name_problems(self.name))
        if self.instance_count == 0:
            issues.append("Unused")
        elif self.instance_count == 1:
            issues.append("Single instance")
        if len(self.workset_names) > 1:
            issues.append("Mixed worksets")
        if self.instance_count and not self.member_count:
            issues.append("No members")
        return issues


# ── COLLECTION ───────────────────────────────────────────────────────────────

def _type_ids_of(doc, built_in_category):
    """Ids of the group types belonging to one group category."""
    ids = set()
    try:
        collector = FilteredElementCollector(doc) \
            .OfCategory(built_in_category) \
            .WhereElementIsElementType()
        for element_id in collector.ToElementIds():
            ids.add(eid_int(element_id))
    except Exception:
        pass
    return ids


def instance_workset_name(doc, element):
    """Name of the workset an element sits on, or an empty string."""
    if doc is None or element is None or not doc.IsWorkshared:
        return ""
    try:
        table = doc.GetWorksetTable()
        workset = table.GetWorkset(element.WorksetId)
        return workset.Name if workset else ""
    except Exception:
        return ""


def collect_group_types(doc):
    """Every group type in doc as GroupTypeRecord, sorted by kind then name."""
    if doc is None:
        return []

    model_ids = _type_ids_of(doc, BuiltInCategory.OST_IOSModelGroups)
    detail_ids = _type_ids_of(doc, BuiltInCategory.OST_IOSDetailGroups)
    attached_ids = _type_ids_of(doc, BuiltInCategory.OST_IOSAttachedDetailGroups)

    instances_by_type = {}
    try:
        for group in FilteredElementCollector(doc) \
                .OfClass(Group) \
                .WhereElementIsNotElementType() \
                .ToElements():
            try:
                key = eid_int(group.GroupType.Id)
            except Exception:
                continue
            instances_by_type.setdefault(key, []).append(group)
    except Exception:
        pass

    records = []
    for group_type in FilteredElementCollector(doc).OfClass(GroupType).ToElements():
        key = eid_int(group_type.Id)
        if key in detail_ids:
            kind = KIND_DETAIL
        elif key in attached_ids:
            kind = KIND_ATTACHED
        elif key in model_ids:
            kind = KIND_MODEL
        else:
            continue
        records.append(GroupTypeRecord(
            group_type, kind, instances_by_type.get(key, []), doc))

    order = {KIND_MODEL: 0, KIND_DETAIL: 1, KIND_ATTACHED: 2}
    records.sort(key=lambda r: (order.get(r.kind, 9), r.name.lower()))
    return records


def user_worksets(doc):
    """User worksets of doc as [(id_int, name)], sorted by name."""
    if doc is None or not doc.IsWorkshared:
        return []
    out = []
    try:
        for workset in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
            out.append((workset_int(workset.Id), workset.Name))
    except Exception:
        return []
    out.sort(key=lambda pair: pair[1].lower())
    return out


# ── RENAME ───────────────────────────────────────────────────────────────────

def rename_group_types(doc, pairs, progress=None):
    """Rename group types in one transaction.

    pairs      — [(GroupTypeRecord, new_name)]
    progress   — optional callable(index, total, label)
    Returns    — [(record, ok, message)]
    """
    results = []
    if doc is None or not pairs:
        return results

    total = len(pairs)
    transaction = Transaction(doc, "T3Lab — Rename Groups")
    transaction.Start()
    try:
        for index, (record, new_name) in enumerate(pairs, 1):
            if progress:
                progress(index, total, record.name)

            wanted = (new_name or "").strip()
            if not wanted:
                results.append((record, False, "Empty name"))
                continue
            if wanted == record.name:
                results.append((record, False, "Unchanged"))
                continue
            bad = illegal_chars_in(wanted)
            if bad:
                results.append((record, False, "Illegal %s" % " ".join(bad)))
                continue

            try:
                record.group_type.Name = wanted
                record.name = wanted
                results.append((record, True, "Renamed"))
            except Exception as exc:
                results.append((record, False, _short_error(exc, "Rename failed")))
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return results


# ── WORKSET ──────────────────────────────────────────────────────────────────

def _set_workset(element, workset_value):
    """Move one element to a workset. Returns (ok, message)."""
    try:
        param = element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
    except Exception:
        param = None
    if param is None:
        return False, "No workset parameter"
    if param.IsReadOnly:
        return False, "Workset is read-only"
    try:
        if param.AsInteger() == workset_value:
            return True, "Already there"
    except Exception:
        pass
    try:
        param.Set(System.Int32(int(workset_value)))
        return True, "Moved"
    except Exception as exc:
        return False, _short_error(exc, "Set failed")


def apply_workset(doc, records, workset_value, include_members=False, progress=None):
    """Move every instance of the given group types onto one workset.

    records         — [GroupTypeRecord]
    workset_value   — integer id of the target workset
    include_members — also move the elements inside each group instance
    progress        — optional callable(index, total, label)
    Returns         — [(record, moved, skipped, failed, message)]
    """
    results = []
    if doc is None or not records:
        return results

    total = len(records)
    transaction = Transaction(doc, "T3Lab — Set Group Workset")
    transaction.Start()
    try:
        for index, record in enumerate(records, 1):
            if progress:
                progress(index, total, record.name)

            moved = skipped = failed = 0
            message = ""

            if not record.instances:
                results.append((record, 0, 0, 0, "No instance"))
                continue

            targets = []
            for instance in record.instances:
                targets.append(instance)
                if include_members:
                    try:
                        for member_id in instance.GetMemberIds():
                            member = doc.GetElement(member_id)
                            if member is not None:
                                targets.append(member)
                    except Exception:
                        pass

            for element in targets:
                ok, note = _set_workset(element, workset_value)
                if ok and note == "Moved":
                    moved += 1
                elif ok:
                    skipped += 1
                else:
                    failed += 1
                    message = message or note

            if failed:
                message = "%d failed — %s" % (failed, message)
            elif moved:
                message = "Moved %d element%s" % (moved, "" if moved == 1 else "s")
            else:
                message = "Already there"
            results.append((record, moved, skipped, failed, message))
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return results


# ── CLEANUP ──────────────────────────────────────────────────────────────────

def purge_group_types(doc, records, progress=None):
    """Delete the given group types. Only unused types delete cleanly.

    Returns [(record, ok, message)].
    """
    results = []
    if doc is None or not records:
        return results

    total = len(records)
    transaction = Transaction(doc, "T3Lab — Purge Group Types")
    transaction.Start()
    try:
        for index, record in enumerate(records, 1):
            if progress:
                progress(index, total, record.name)
            if record.instance_count:
                results.append((record, False, "Still placed"))
                continue
            try:
                doc.Delete(record.group_type.Id)
                results.append((record, True, "Purged"))
            except Exception as exc:
                results.append((record, False, _short_error(exc, "Delete failed")))
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return results


def ungroup_instances(doc, records, progress=None):
    """Ungroup every instance of the given group types.

    Returns [(record, ungrouped, failed, message)].
    """
    results = []
    if doc is None or not records:
        return results

    total = len(records)
    transaction = Transaction(doc, "T3Lab — Ungroup Groups")
    transaction.Start()
    try:
        for index, record in enumerate(records, 1):
            if progress:
                progress(index, total, record.name)

            if not record.instances:
                results.append((record, 0, 0, "No instance"))
                continue

            ungrouped = failed = 0
            message = ""
            for instance in record.instances:
                try:
                    instance.UngroupMembers()
                    ungrouped += 1
                except Exception as exc:
                    failed += 1
                    message = message or _short_error(exc, "Ungroup failed")
            if failed:
                message = "%d failed — %s" % (failed, message)
            else:
                message = "Ungrouped %d" % ungrouped
            results.append((record, ungrouped, failed, message))
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return results


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _short_error(exc, fallback):
    """First line of an exception message, trimmed for a status cell."""
    try:
        text = str(exc).strip().splitlines()[0]
    except Exception:
        text = ""
    text = text.replace("Autodesk.Revit.Exceptions.", "")
    return text[:60] if text else fallback


def duplicate_names(records):
    """Names shared by more than one group type, compared case-insensitively."""
    counts = {}
    for record in records:
        key = (record.name or "").strip().lower()
        counts[key] = counts.get(key, 0) + 1
    return set(key for key, count in counts.items() if count > 1 and key)


# ── PLACEMENT: WHERE THE GROUP INSTANCES SIT ─────────────────────────────────
# Everything below feeds the Placement tab's plan view. The Revit reads are
# isolated in collect_placements(); the geometry maths underneath it is plain
# Python so it can be unit-tested outside Revit (see dev/test_group_manager.py).

class GroupPlacement(object):
    """One placed group instance, reduced to what a plan view needs."""

    __slots__ = ("instance_id", "type_id", "type_name", "kind",
                 "x", "y", "z", "level_name", "view_name", "workset_name")

    def __init__(self, instance_id, type_id, type_name, kind, x, y, z,
                 level_name="", view_name="", workset_name=""):
        self.instance_id = instance_id
        self.type_id = type_id
        self.type_name = type_name
        self.kind = kind
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.level_name = level_name or ""
        self.view_name = view_name or ""
        self.workset_name = workset_name or ""

    @property
    def location_label(self):
        """Where this instance lives, in one phrase for the filter and tooltip."""
        if self.level_name:
            return self.level_name
        if self.view_name:
            return self.view_name
        return "-"

    def __repr__(self):
        return "<GroupPlacement %s @ (%.1f, %.1f)>" % (self.type_name, self.x, self.y)


def instance_point(group):
    """(x, y, z) of a group instance in feet, or None when it has no location.

    Prefers the insertion point; falls back to the centre of the bounding box,
    which is what detail groups and some nested cases actually carry.
    """
    if group is None:
        return None
    try:
        location = group.Location
        point = getattr(location, "Point", None)
        if point is not None:
            return (point.X, point.Y, point.Z)
    except Exception:
        pass
    try:
        box = group.get_BoundingBox(None)
        if box is not None:
            return ((box.Min.X + box.Max.X) / 2.0,
                    (box.Min.Y + box.Max.Y) / 2.0,
                    (box.Min.Z + box.Max.Z) / 2.0)
    except Exception:
        pass
    return None


def instance_level_name(doc, group):
    """Name of the level a group instance sits on, or an empty string."""
    if doc is None or group is None:
        return ""
    try:
        level_id = group.LevelId
        if level_id is not None and eid_int(level_id) > 0:
            level = doc.GetElement(level_id)
            if level is not None:
                return element_name(level)
    except Exception:
        pass
    try:
        param = group.get_Parameter(BuiltInParameter.GROUP_LEVEL)
        if param is not None:
            level = doc.GetElement(param.AsElementId())
            if level is not None:
                return element_name(level)
    except Exception:
        pass
    return ""


def instance_view_name(doc, group):
    """Owner view of a view-specific group (detail groups), or an empty string."""
    if doc is None or group is None:
        return ""
    try:
        view_id = group.OwnerViewId
        if view_id is not None and eid_int(view_id) > 0:
            view = doc.GetElement(view_id)
            if view is not None:
                return element_name(view)
    except Exception:
        pass
    return ""


def collect_placements(doc, records):
    """GroupPlacement for every located instance of `records`.

    Instances Revit gives no usable point for are skipped rather than parked at
    the origin -- a marker at (0,0) reads as "this group sits on the project base
    point", which would be a lie. The caller reports the skipped count instead.
    """
    placements = []
    for record in records or []:
        for instance in getattr(record, "instances", None) or []:
            point = instance_point(instance)
            if point is None:
                continue
            try:
                instance_id = eid_int(instance.Id)
            except Exception:
                continue
            placements.append(GroupPlacement(
                instance_id=instance_id,
                type_id=record.type_id,
                type_name=record.name,
                kind=record.kind,
                x=point[0], y=point[1], z=point[2],
                level_name=instance_level_name(doc, instance),
                view_name=instance_view_name(doc, instance),
                workset_name=instance_workset_name(doc, instance)))
    return placements


def placed_instance_total(records):
    """How many instances exist in total, located or not."""
    return sum(int(getattr(r, "instance_count", 0) or 0) for r in records or [])


# ── PLAN GEOMETRY (no Revit -- unit-tested) ──────────────────────────────────

def placements_extent(placements):
    """(min_x, min_y, max_x, max_y) covering `placements`, or None if empty."""
    if not placements:
        return None
    xs = [p.x for p in placements]
    ys = [p.y for p in placements]
    return (min(xs), min(ys), max(xs), max(ys))


def level_names_of(placements):
    """Distinct level (or view) labels present, sorted, for the level filter."""
    seen = set()
    for placement in placements or []:
        label = placement.location_label
        if label and label != "-":
            seen.add(label)
    return sorted(seen, key=lambda s: s.lower())


def filter_placements(placements, kind=None, level=None, type_ids=None):
    """Subset of `placements` matching every filter that is not None."""
    out = []
    allowed = set(type_ids) if type_ids is not None else None
    for placement in placements or []:
        if kind and placement.kind != kind:
            continue
        if level and placement.location_label != level:
            continue
        if allowed is not None and placement.type_id not in allowed:
            continue
        out.append(placement)
    return out


def count_by_type(placements):
    """{type_id: number of instances} for the legend counts."""
    counts = {}
    for placement in placements or []:
        counts[placement.type_id] = counts.get(placement.type_id, 0) + 1
    return counts


class PlanTransform(object):
    """Maps model feet onto canvas pixels, keeping aspect ratio and flipping Y.

    Revit's Y grows north; WPF's Y grows down the screen. Without the flip the
    plan comes out mirrored -- plausible enough that nobody notices until they
    try to find the group on site.
    """

    __slots__ = ("scale", "offset_x", "offset_y", "extent")

    def __init__(self, scale, offset_x, offset_y, extent):
        self.scale = scale
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.extent = extent

    @classmethod
    def fit(cls, extent, width, height, padding=24.0):
        """Transform that fits `extent` inside width x height pixels.

        A degenerate extent -- one instance, or every instance on one line --
        still yields a usable transform: the span is floored so the scale stays
        finite and the content lands in the middle instead of at a corner.
        """
        width = max(1.0, float(width))
        height = max(1.0, float(height))
        padding = max(0.0, float(padding))
        usable_w = max(1.0, width - 2 * padding)
        usable_h = max(1.0, height - 2 * padding)

        if not extent:
            return cls(1.0, width / 2.0, height / 2.0, None)

        min_x, min_y, max_x, max_y = extent
        span_x = max(max_x - min_x, 1e-6)
        span_y = max(max_y - min_y, 1e-6)
        scale = min(usable_w / span_x, usable_h / span_y)

        mid_x = (min_x + max_x) / 2.0
        mid_y = (min_y + max_y) / 2.0
        offset_x = width / 2.0 - mid_x * scale
        offset_y = height / 2.0 + mid_y * scale      # plus, because Y is flipped
        return cls(scale, offset_x, offset_y, extent)

    def to_canvas(self, x, y):
        """(px, py) on the canvas for a model point in feet."""
        return (x * self.scale + self.offset_x,
                self.offset_y - y * self.scale)

    def to_model(self, px, py):
        """Model point in feet for a canvas pixel -- the inverse of to_canvas."""
        if not self.scale:
            return (0.0, 0.0)
        return ((px - self.offset_x) / self.scale,
                (self.offset_y - py) / self.scale)

    MIN_ZOOM_SCALE = 1e-4
    MAX_ZOOM_SCALE = 1e4

    def zoom_at(self, px, py, factor):
        """Zoom by `factor` keeping the model point under (px, py) still.

        Zooming around the window centre instead makes the thing you are
        pointing at slide away, so the anchor is the cursor.
        """
        factor = float(factor)
        if factor <= 0:
            return self
        new_scale = self.scale * factor
        new_scale = max(self.MIN_ZOOM_SCALE, min(self.MAX_ZOOM_SCALE, new_scale))
        if new_scale == self.scale:
            return self
        model_x, model_y = self.to_model(px, py)
        self.scale = new_scale
        self.offset_x = px - model_x * new_scale
        self.offset_y = py + model_y * new_scale
        return self

    def pan_by(self, dx, dy):
        """Slide the view by a pixel delta."""
        self.offset_x += float(dx)
        self.offset_y += float(dy)
        return self


def feet_to_metres_label(feet):
    """Feet as a short metric string -- these models are all metric."""
    metres = float(feet) * 0.3048
    if abs(metres) >= 1000:
        return "%.1f km" % (metres / 1000.0)
    return "%.1f m" % metres


def select_instances(uidoc, instance_ids):
    """Put `instance_ids` into the Revit selection. Returns how many were set.

    Selection is a UI operation, not a model edit, so there is no transaction to
    open here.
    """
    if uidoc is None or not instance_ids:
        return 0
    # Imported here, not at module scope: the Revit-free unit tests exec this
    # file with a stubbed `System`, and a top-level Generic import would need
    # the stub to fake the whole namespace.
    from System.Collections.Generic import List
    ids = List[ElementId]()
    for value in instance_ids:
        element_id = new_element_id(value)
        if element_id is not None:
            ids.Add(element_id)
    uidoc.Selection.SetElementIds(ids)
    return ids.Count


def extent_label(extent):
    """Human-readable size of a plan extent, for the info strip under the plan."""
    if not extent:
        return "no extent"
    min_x, min_y, max_x, max_y = extent
    return "%s x %s" % (feet_to_metres_label(max_x - min_x),
                        feet_to_metres_label(max_y - min_y))
