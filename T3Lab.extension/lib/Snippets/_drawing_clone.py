# -*- coding: utf-8 -*-
"""
_drawing_clone.py
=================
Clone Drawing: copy the finished drawing of one assembly (cast unit) - its
assembly views, sheet, free annotations - to similar assemblies, and re-create
the tags and dimensions that can be matched (Tekla: clone drawing).

Three layers, each one Transaction per target inside ONE TransactionGroup
(spec D9 / A6, so Ctrl+Z undoes the whole click):

* T1  "Clone views to <mark>"          - AssemblyViewUtils.Create... per source
                                          view, same template / scale / crop;
                                          the sheet with its viewports.
* T2  "Copy annotations to <mark>"     - view-owned text, detail lines, detail
                                          items, filled regions... copied view
                                          to view, one SubTransaction per view.
* T3  "Re-create references on <mark>" - IndependentTags on matched elements and
                                          dimensions between faces of matched
                                          hosts (D17). Everything else is listed
                                          as UNMATCHED with its reason - never
                                          dropped silently.

Two halves, kept apart on purpose:

* PURE PYTHON  - view classification, mark substitution, similarity score,
  clone planning, face matching. No Revit import at module level, so
  ``dev/test_rebar_fingerprint.py`` exercises the shipped source.
* REVIT        - readers and the three layers. ``Autodesk.Revit.DB`` is
  imported inside each function. Nothing here ever deletes a view or sheet.

Spec: dev/plan/rebar-tekla-implementation-spec.md section 3.4 / 5.3.
Everything marked [NV] is unverified until dev/debug/spike_rebar_assembly.py
has run on Revit 2027 (probes G1, G2, G3, G8, G14); each such call degrades to
a ``failed`` / ``skipped`` / unmatched row with the Revit message.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"
__title__ = "Drawing Clone"

import math

from Snippets._assembly import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    SKIP_STOPPED,
    TRANSACTION_PREFIX,
    Result,
    collect_assembly_views,
    transform_between,
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
from Snippets._rebar import local_center, match_by_fingerprint


# ── CONSTANTS ────────────────────────────────────────────────────────────────

# [NV] spike G2: how ElementTransformUtils.CopyElements(view -> view) expects
# its transform for assembly views. 'delta' = transform_between(src, dst)
# (model -> model, the text lands at the same spot relative to the target);
# 'identity' = Transform.Identity. ONE constant, flipped after the spike.
T2_TRANSFORM_MODE = "delta"

# [NV] spike G1: whether instances of ONE assembly type share their views.
# "skip" = a target of the source's own type is skipped with SAME_TYPE_REASON;
# "clone" = treated like any other target.
SAME_TYPE_POLICY = "skip"
SAME_TYPE_REASON = (u"same type as the source - Revit shares views between "
                    u"instances of one type; nothing to clone")

# [NV] spike G14: Revit's View.ViewDirection points TOWARDS the viewer; the
# classification tables below are written with the LOOKING direction, which
# is its negative. Flip this if G14 shows the opposite.
VIEW_DIRECTION_TOWARDS_VIEWER = True

VIEW_KINDS = ("3d", "section", "elevation", "partlist", "takeoff",
              "single_schedule", "sheet")
SCHEDULE_KINDS = ("partlist", "takeoff", "single_schedule")

# Looking direction in the assembly's LOCAL frame -> orientation name.
# Sections cut through the centre of the assembly; elevations cut on the face
# of its bounding box on the viewer's side and look in. [NV] spike G14.
SECTION_TABLE = (
    ((0, 0, -1), "HorizontalDetail"),      # looking down
    ((0, 1, 0), "DetailSectionA"),         # looking north
    ((-1, 0, 0), "DetailSectionB"),        # looking west
)
ELEVATION_TABLE = (
    ((0, 0, -1), "ElevationTop"),
    ((0, 0, 1), "ElevationBottom"),
    ((0, 1, 0), "ElevationFront"),
    ((0, -1, 0), "ElevationBack"),
    ((1, 0, 0), "ElevationLeft"),
    ((-1, 0, 0), "ElevationRight"),
)
AXIS_DOT_MIN = 0.98            # a view more than ~11 degrees off an axis is "unknown"
SECTION_CUT_MAX = 0.5          # |cut position| below this = cut through the centre
ELEVATION_CUT_MIN = -0.5       # cut position at or beyond the near face

DEFAULT_TOL_MM = 10.0
FACE_TOL_MM = 5.0
FACE_DOT_MIN = 0.999
SIMILAR_MIN_SCORE = 70

# Spec 3.4 similarity penalties.
PENALTY_CATEGORY = 40
PENALTY_BBOX = 30
PENALTY_REBAR = 20

DEFAULT_SHEET_PATTERN = u"{SourceNumber}-{Mark}"
DEFAULT_VIEW_PATTERN = u"{SourceName}"
PATTERN_TOKENS = (u"{SourceName}", u"{SourceNumber}", u"{Mark}", u"{SourceMark}")

# T2: view-owned categories copied view to view (spec 3.4). Detail groups and
# masking regions ride along: they are free annotation of the same kind.
T2_CATEGORY_NAMES = ("OST_TextNotes", "OST_Lines", "OST_DetailComponents",
                     "OST_FilledRegion", "OST_MaskingRegion", "OST_GenericAnnotation",
                     "OST_InsulationLines", "OST_IOSDetailGroups")
# View-owned elements that belong to the view machinery, not to the drawing:
# never copied and never reported.
T2_IGNORED_CATEGORY_NAMES = ("OST_Viewers", "OST_CropBoundary", "OST_SectionBox",
                             "OST_ReferenceViewer", "OST_TitleBlocks", "OST_Viewports",
                             "OST_ScheduleGraphics", "OST_SketchLines", "OST_Elev",
                             "OST_Callouts", "OST_Sections", "OST_Matchline",
                             "OST_GuideGrid", "OST_IOSSketchGrid", "OST_Cameras",
                             "OST_RevisionCloudTags", "OST_ElevationMarks")

# Unmatched reasons (T3, D17). Shown verbatim in the log.
R_LINKED = u"tag on a linked element is not supported in V1"
R_MULTI_REF = u"tag on several elements is not supported in V1"
R_REBAR_POSITION = u"rebar set position tag is not supported in V1"
R_SUBELEMENT = u"tag on a face or sub-element is not supported in V1"
R_NOT_MEMBER = u"tagged element is not part of the source assembly"
R_NO_MATCH = u"no matching element in %s"
R_AMBIGUOUS = u"two equally close elements in %s - not guessed"
R_SPOT = u"spot elevation is not supported in V1"
R_MRA = u"multi-rebar annotation is not supported in V1"
R_DIM_SHAPE = u"%s dimension is not supported in V1"
R_DIM_REBAR = u"dimension to rebar is not supported in V1"
R_DIM_NOT_FACE = u"dimension reference is not a face of a host element"
R_FACE_NO_MATCH = u"no matching face on the target element"
R_FACE_AMBIGUOUS = u"two matching faces on the target element - not guessed"

LEVEL_OK = "ok"
LEVEL_SKIPPED = "skipped"
LEVEL_FAILED = "failed"
LEVEL_PLAIN = "plain"


# ── RECORDS (pure) ───────────────────────────────────────────────────────────

class _Bag(object):
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


class ViewportSpec(_Bag):
    """One view placed on the source sheet. ``center`` is (x, y) in sheet feet."""
    _FIELDS = (("view_id", -1), ("view_name", u""), ("center", (0.0, 0.0)),
               ("rotation", u""), ("label_offset", None), ("type_id", -1),
               ("is_schedule", False))


class ViewSpec(_Bag):
    """Everything T1 needs to re-create one assembly view (or the sheet).

    ``kind`` in VIEW_KINDS; ``orientation`` an AssemblyDetailViewOrientation
    name for sections / elevations; ``crop_local`` the crop box in the SOURCE
    assembly's local frame: (origin, basis_x, basis_y, basis_z, min, max), all
    (x, y, z) feet. ``skip_reason`` set = this view cannot be cloned (it is
    reported, never silently dropped).
    """
    _FIELDS = (("kind", u""), ("orientation", None), ("template_id", -1),
               ("scale", 0), ("detail_level", u""), ("display_style", u""),
               ("name", u""), ("crop_local", None), ("crop_active", None),
               ("crop_visible", None), ("category_id", -1),
               ("title_block_type_id", -1), ("title_block_name", u""),
               ("sheet_number", u""), ("sheet_name", u""), ("viewports", []),
               ("src_view_id", -1), ("skip_reason", u""))


class CloneOptions(_Bag):
    """What the Options tab asked for."""
    _FIELDS = (("t1", True), ("t2", True), ("t3", True), ("add_missing", False),
               ("tol_mm", DEFAULT_TOL_MM), ("allow_mirror", True),
               ("sheet_pattern", DEFAULT_SHEET_PATTERN),
               ("view_pattern", DEFAULT_VIEW_PATTERN))


class Tally(object):
    """T3 outcome of one view (or of a whole target once merged)."""

    def __init__(self):
        self.tags_ok = 0
        self.tags_unmatched = 0
        self.dims_ok = 0
        self.dims_unmatched = 0
        self.unmatched = []          # [(src_element_id, reason)]

    def tag(self, ok, src_id=None, reason=u""):
        if ok:
            self.tags_ok += 1
        else:
            self.tags_unmatched += 1
            self.unmatched.append((src_id, u"tag: " + reason))

    def dim(self, ok, src_id=None, reason=u""):
        if ok:
            self.dims_ok += 1
        else:
            self.dims_unmatched += 1
            self.unmatched.append((src_id, u"dimension: " + reason))

    def other(self, src_id, reason):
        """An annotation that is neither a tag nor a dimension (MRA...)."""
        self.unmatched.append((src_id, reason))

    def merge(self, other):
        self.tags_ok += other.tags_ok
        self.tags_unmatched += other.tags_unmatched
        self.dims_ok += other.dims_ok
        self.dims_unmatched += other.dims_unmatched
        self.unmatched.extend(other.unmatched)
        return self

    @property
    def tags_total(self):
        return self.tags_ok + self.tags_unmatched

    @property
    def dims_total(self):
        return self.dims_ok + self.dims_unmatched


class TargetOutcome(object):
    """What happened to ONE target assembly - one row of the Results tab."""

    def __init__(self, assembly_id, mark):
        self.assembly_id = assembly_id
        self.mark = mark
        self.status = STATUS_OK
        self.detail = u""
        self.views_planned = 0
        self.views_created = 0
        self.sheet_number = u""
        self.sheet_id = -1
        self.copied = 0
        self.tally = Tally()
        self.unmatched = []          # [(src_element_id, reason)] - T2 + T3
        self.failures = 0            # failed views / layers (the target may still be ok)
        self.log = []                # [(level, text)]

    def note(self, level, text):
        if level == LEVEL_FAILED:
            self.failures += 1
        self.log.append((level, text))

    @property
    def unmatched_count(self):
        return len(self.unmatched)


# ── PURE HELPERS ─────────────────────────────────────────────────────────────

def _unit3(v):
    length = math.sqrt(sum(float(c) * float(c) for c in v))
    if length < 1e-12:
        return None
    return tuple(float(c) / length for c in v)


def _dot3(a, b):
    return sum(float(a[k]) * float(b[k]) for k in range(3))


def _axis_lookup(direction, table):
    unit = _unit3(direction)
    if unit is None:
        return None
    for axis, name in table:
        if _dot3(unit, axis) >= AXIS_DOT_MIN:
            return name
    return None


def classify_view(view_type, view_dir_local, cut_plane_local, has_crop):
    """AssemblyDetailViewOrientation name of a section / elevation, or None.

    ``view_type``       ViewType name ('Section', 'Elevation', 'Detail', ...).
    ``view_dir_local``  the LOOKING direction in the source assembly's local
                        frame (the negative of View.ViewDirection - see
                        VIEW_DIRECTION_TOWARDS_VIEWER).
    ``cut_plane_local`` where the cut plane sits along that direction, scaled
                        to the assembly's half-extent: 0 = through the centre,
                        -1 = on the bounding-box face on the viewer's side.
                        None = unknown, then ``view_type`` decides.
    ``has_crop``        kept for the spike calibration log; a view without a
                        crop box still classifies.

    Through the centre: (0,0,-1) HorizontalDetail, (0,+1,0) DetailSectionA,
    (-1,0,0) DetailSectionB. On the near face: ElevationTop / Bottom / Front /
    Back / Left / Right by the axis the view looks along. Anything oblique, on
    the far face, or not a section / elevation is None - the view is then
    reported "orientation unknown" instead of being drawn wrong (R2). [NV] G14.
    """
    _ = has_crop
    kind = (u"%s" % (view_type or u"")).split(".")[-1]
    if kind not in ("Section", "Elevation", "Detail"):
        return None
    if view_dir_local is None:
        return None
    if cut_plane_local is None:
        elevation = kind == "Elevation"
    else:
        t = float(cut_plane_local)
        if abs(t) < SECTION_CUT_MAX:
            elevation = False
        elif t <= ELEVATION_CUT_MIN:
            elevation = True
        else:
            return None
    return _axis_lookup(view_dir_local, ELEVATION_TABLE if elevation else SECTION_TABLE)


def kind_of_orientation(orientation):
    """'elevation' for Elevation* names, 'section' for the rest."""
    return "elevation" if (orientation or u"").startswith("Elevation") else "section"


def substitute_mark(text, src_mark, dst_mark, fallback_suffix=True):
    """'C-01 Elevation Front' -> 'C-02 Elevation Front'.

    Every occurrence of the source mark is replaced. When the text does not
    contain it, ' (<dst_mark>)' is appended (``fallback_suffix``) so two
    assemblies never end up with the same view name.
    """
    text = text or u""
    dst = dst_mark or u"copy"
    if src_mark and src_mark in text:
        return text.replace(src_mark, dst)
    if not fallback_suffix:
        return text
    return u"%s (%s)" % (text, dst) if text else dst


def render_pattern(pattern, ctx):
    """Replace {SourceName} {SourceNumber} {Mark} {SourceMark}; unknown tokens stay."""
    out = pattern or u""
    for token in PATTERN_TOKENS:
        key = token[1:-1]
        if token in out:
            out = out.replace(token, u"%s" % (ctx.get(key) or u""))
    return out.strip()


def unique_name(name, taken):
    """`name`, or 'name (2)', 'name (3)'... - the first not in `taken` (a set)."""
    if name not in taken:
        return name
    n = 2
    while u"%s (%d)" % (name, n) in taken:
        n += 1
    return u"%s (%d)" % (name, n)


def view_name_for(spec, src_mark, dst_mark, pattern):
    """New view name: the pattern, with the source mark swapped for the target's."""
    explicit_mark = u"{Mark}" in (pattern or u"")
    source_name = substitute_mark(spec.name, src_mark, dst_mark,
                                  fallback_suffix=not explicit_mark)
    name = render_pattern(pattern or DEFAULT_VIEW_PATTERN, {
        "SourceName": source_name, "SourceNumber": spec.sheet_number,
        "Mark": dst_mark, "SourceMark": src_mark})
    return name or source_name


def sheet_number_for(spec, src_mark, dst_mark, pattern):
    number = render_pattern(pattern or DEFAULT_SHEET_PATTERN, {
        "SourceName": spec.sheet_name, "SourceNumber": spec.sheet_number,
        "Mark": dst_mark, "SourceMark": src_mark})
    return number or substitute_mark(spec.sheet_number, src_mark, dst_mark)


def _within(a, b, tol_pct):
    a, b = abs(float(a)), abs(float(b))
    return abs(a - b) <= max(a, b) * float(tol_pct) / 100.0 + 1e-9


def _fmt_box(box):
    return u"×".join(u"%d" % int(round(float(v))) for v in box)


def similarity(src, dst, tol_pct=5.0):
    """(score 0..100, [reasons]) of how much `dst` looks like `src`.

    ``src`` / ``dst``: dict(category, bbox_mm=(dx, dy, dz) in the assembly's
    local frame, rebar_count, member_count). 100 = same category, every box
    side within ``tol_pct`` %, same rebar count. Different category -40, box
    out of tolerance -30, different rebar count -20. ``member_count`` is not
    scored (it moves with the rebar count).
    """
    score = 100
    reasons = []
    if (src.get("category") or u"") != (dst.get("category") or u""):
        score -= PENALTY_CATEGORY
        reasons.append(u"category %s, not %s" % (dst.get("category") or u"?",
                                                 src.get("category") or u"?"))
    src_box = tuple(src.get("bbox_mm") or (0.0, 0.0, 0.0))
    dst_box = tuple(dst.get("bbox_mm") or (0.0, 0.0, 0.0))
    if len(src_box) != 3 or len(dst_box) != 3 or \
            not all(_within(a, b, tol_pct) for a, b in zip(src_box, dst_box)):
        score -= PENALTY_BBOX
        reasons.append(u"size %s mm, not %s" % (_fmt_box(dst_box), _fmt_box(src_box)))
    if int(src.get("rebar_count") or 0) != int(dst.get("rebar_count") or 0):
        score -= PENALTY_REBAR
        reasons.append(u"%d rebar, not %d" % (int(dst.get("rebar_count") or 0),
                                              int(src.get("rebar_count") or 0)))
    return max(0, score), reasons


def view_key(spec):
    """What makes two assembly views 'the same view' for add-missing-only."""
    if spec.kind == "sheet":
        return ("sheet",)
    if spec.kind == "single_schedule":
        return (spec.kind, spec.category_id)
    if spec.kind in ("section", "elevation"):
        return ("view", spec.orientation)
    return (spec.kind,)


def pair_existing(src_specs, dst_specs):
    """{src_view_id: dst_view_id} for source views the target already has.

    Same key (view_key) pairs in order; two source sections of one orientation
    pair with two target ones, a third stays unpaired.
    """
    pool = {}
    for spec in dst_specs or ():
        if spec.skip_reason:
            continue
        pool.setdefault(view_key(spec), []).append(spec.src_view_id)
    out = {}
    for spec in src_specs or ():
        bucket = pool.get(view_key(spec))
        if bucket:
            out[spec.src_view_id] = bucket.pop(0)
    return out


def plan_clone(src_specs, dst_existing_specs, add_missing_only):
    """(to_create, skipped) for one target. ``skipped`` is [(ViewSpec, reason)].

    * a source view that cannot be cloned keeps its own ``skip_reason``;
    * target without views: everything is created;
    * target with views, ``add_missing_only`` False: everything is skipped
      ('has drawing');
    * ``add_missing_only`` True: only views (and the sheet) of a kind the
      target does not have yet are created; the rest is skipped 'exists'.
    """
    to_create = []
    skipped = []
    has_drawing = bool(dst_existing_specs)
    paired = pair_existing(src_specs, dst_existing_specs) if add_missing_only else {}
    for spec in src_specs or ():
        if spec.skip_reason:
            skipped.append((spec, spec.skip_reason))
        elif has_drawing and not add_missing_only:
            skipped.append((spec, u"has drawing"))
        elif spec.src_view_id in paired:
            skipped.append((spec, u"exists"))
        else:
            to_create.append(spec)
    return to_create, skipped


def creation_order(specs):
    """Views, then schedules, then sheets (viewports need their views)."""
    rank = {"3d": 0, "section": 1, "elevation": 1, "partlist": 2, "takeoff": 2,
            "single_schedule": 2, "sheet": 3}
    return sorted(specs, key=lambda s: rank.get(s.kind, 4))


def _plural(n, word, many=None):
    return u"%d %s" % (n, word if n == 1 else (many or word + u"s"))


def summarize_specs(specs):
    """'3D · 2 elevations · 2 sections · Part list · Sheet S-01 (A1)'."""
    counts = {}
    sheets = []
    for spec in specs or ():
        if spec.kind == "sheet":
            label = u"Sheet %s" % (spec.sheet_number or u"?")
            if spec.title_block_name:
                label += u" (%s)" % spec.title_block_name
            sheets.append(label)
        else:
            counts[spec.kind] = counts.get(spec.kind, 0) + 1
    parts = []
    for kind, one, many in (("3d", u"3D", u"3D views"),
                            ("elevation", u"elevation", u"elevations"),
                            ("section", u"section", u"sections"),
                            ("partlist", u"Part list", u"part lists"),
                            ("takeoff", u"Material takeoff", u"material takeoffs"),
                            ("single_schedule", u"schedule", u"schedules")):
        n = counts.get(kind, 0)
        if n == 1 and kind in ("3d", "partlist", "takeoff"):
            parts.append(one)
        elif n:
            parts.append(u"%d %s" % (n, many))
    parts.extend(sheets)
    return u" · ".join(parts) if parts else u"No assembly views"


def match_face(src_face, dst_faces, tol_mm=FACE_TOL_MM, allow_mirror=True):
    """(index into dst_faces or None, reason) for one source face.

    A face is ``(normal_local (x, y, z) unit, distance_mm)`` where distance is
    the signed distance of the face plane from the element's centre along the
    normal. Exact orientation first; then, when ``allow_mirror``, the x / y
    mirrored normal (a mirrored assembly). Two candidates = ambiguous.
    """
    normal, distance = src_face
    flips = [(1, 1, 1)]
    if allow_mirror:
        flips += [(-1, 1, 1), (1, -1, 1)]
    for flip in flips:
        want = (normal[0] * flip[0], normal[1] * flip[1], normal[2] * flip[2])
        hits = [i for i, (n, d) in enumerate(dst_faces)
                if _dot3(want, n) >= FACE_DOT_MIN and abs(float(d) - float(distance)) <= tol_mm]
        if len(hits) == 1:
            return hits[0], u""
        if len(hits) > 1:
            return None, R_FACE_AMBIGUOUS
    return None, R_FACE_NO_MATCH


def target_status(is_source_type, has_drawing, add_missing):
    """(StatusText, Severity, clonable) for a target row before running."""
    if is_source_type and SAME_TYPE_POLICY == "skip":
        return u"Will skip: same type", "Warning", False
    if has_drawing and not add_missing:
        return u"Will skip: has drawing", "Warning", False
    if has_drawing:
        return u"Has drawing - add missing only", "Warning", True
    return u"Ready", "Success", True


# ── REVIT HELPERS (private) ──────────────────────────────────────────────────

def _enum_name(value):
    if value is None:
        return u""
    try:
        text = value.ToString()
    except Exception:
        text = u"%s" % value
    return text.split(".")[-1]


def _xyz(t):
    from Autodesk.Revit.DB import XYZ
    return XYZ(float(t[0]), float(t[1]), float(t[2]))


def _tup(xyz):
    return (float(xyz.X), float(xyz.Y), float(xyz.Z))


def _rollback(txn):
    try:
        if txn.HasStarted() and not txn.HasEnded():
            txn.RollBack()
    except Exception:
        pass


def _category_ints(names):
    from Autodesk.Revit.DB import BuiltInCategory
    out = set()
    for name in names:
        member = getattr(BuiltInCategory, name, None)
        if member is not None:
            try:
                out.add(int(member))
            except Exception:
                continue
    return out


def _category_int(element):
    try:
        return eid_value(element.Category.Id)
    except Exception:
        return None


def _category_label(element):
    try:
        return element.Category.Name
    except Exception:
        return type(element).__name__


def _assembly_mark(assembly):
    try:
        return assembly.AssemblyTypeName or u""
    except Exception:
        return u""


def _is_rebar_like(element):
    name = type(element).__name__
    return name in ("Rebar", "RebarInSystem", "AreaReinforcement", "PathReinforcement",
                    "FabricSheet", "FabricArea", "RebarCoupler")


def _box_corners(box):
    lo, hi = box.Min, box.Max
    from Autodesk.Revit.DB import XYZ
    out = []
    for x in (lo.X, hi.X):
        for y in (lo.Y, hi.Y):
            for z in (lo.Z, hi.Z):
                out.append(XYZ(x, y, z))
    return out


def _local_extent(elements, inverse):
    """(min, max) of the elements' bounding boxes in a local frame, feet."""
    lo = [None, None, None]
    hi = [None, None, None]
    for element in elements:
        try:
            box = element.get_BoundingBox(None)
        except Exception:
            box = None
        if box is None:
            continue
        for corner in _box_corners(box):
            p = inverse.OfPoint(corner)
            for k, v in enumerate((p.X, p.Y, p.Z)):
                lo[k] = v if lo[k] is None else min(lo[k], v)
                hi[k] = v if hi[k] is None else max(hi[k], v)
    if lo[0] is None:
        return None
    return tuple(lo), tuple(hi)


def _member_elements(doc, assembly):
    out = []
    try:
        for member_id in assembly.GetMemberIds():
            element = doc.GetElement(member_id)
            if element is not None:
                out.append(element)
    except Exception:
        pass
    return out


# ── PROFILE & SOURCE READING [REVIT] ─────────────────────────────────────────

def assembly_profile(doc, assembly):
    """dict(category, bbox_mm, rebar_count, member_count) for ``similarity``.

    The box is measured on the non-rebar members in the assembly's own frame,
    so two identical columns rotated differently in plan still compare equal.
    """
    members = _member_elements(doc, assembly)
    hosts = [m for m in members if not _is_rebar_like(m)]
    category = u""
    try:
        from Autodesk.Revit.DB import Category
        cat = Category.GetCategory(doc, assembly.NamingCategoryId)
        category = cat.Name if cat is not None else u""
    except Exception:
        pass
    bbox = (0.0, 0.0, 0.0)
    try:
        extent = _local_extent(hosts or members, assembly.GetTransform().Inverse)
        if extent is not None:
            lo, hi = extent
            bbox = tuple(round(to_mm(hi[k] - lo[k]), 1) for k in range(3))
    except Exception:
        pass
    return {"category": category, "bbox_mm": bbox,
            "rebar_count": len(members) - len(hosts), "member_count": len(members)}


def _crop_local(view, inverse):
    """Crop box of `view` in an assembly's local frame, or None."""
    try:
        box = view.CropBox
        if box is None:
            return None
        local = inverse.Multiply(box.Transform)
        return (_tup(local.Origin), _tup(local.BasisX), _tup(local.BasisY),
                _tup(local.BasisZ), _tup(box.Min), _tup(box.Max))
    except Exception:
        return None


def _cut_position(doc, view, assembly, looking_model):
    """Where the view's cut plane sits along `looking_model`, scaled to the
    assembly's half extent (0 centre, -1 near face); None when unreadable."""
    try:
        center = assembly.GetCenter()
        origin = view.Origin
        offset = origin.Subtract(center).DotProduct(looking_model)
        half = 0.0
        for element in _member_elements(doc, assembly) or [assembly]:
            box = element.get_BoundingBox(None)
            if box is None:
                continue
            for corner in _box_corners(box):
                half = max(half, abs(corner.Subtract(center).DotProduct(looking_model)))
        if half < 1e-9:
            return None
        return offset / half
    except Exception:
        return None


def _schedule_kind(view):
    """(kind, category_id) of an assembly schedule; kind None when not one."""
    try:
        definition = view.Definition
        if getattr(definition, "IsKeySchedule", False):
            return None, -1
        if getattr(definition, "IsMaterialTakeoff", False):
            return "takeoff", eid_value(definition.CategoryId)
        category_id = eid_value(definition.CategoryId)
        if category_id == -1:
            return "partlist", -1
        return "single_schedule", category_id
    except Exception:
        return None, -1


def _view_spec(doc, view, assembly, inverse):
    from Autodesk.Revit.DB import ViewSchedule
    spec = ViewSpec(src_view_id=eid_value(view.Id))
    try:
        spec.name = view.Name
    except Exception:
        spec.name = elem_name(view)
    try:
        spec.template_id = eid_value(view.ViewTemplateId)
    except Exception:
        pass

    if isinstance(view, ViewSchedule):
        kind, category_id = _schedule_kind(view)
        if kind is None:
            spec.kind = "schedule"
            spec.skip_reason = u"key or unknown schedule - not an assembly schedule kind"
        else:
            spec.kind, spec.category_id = kind, category_id
        return spec

    try:
        spec.scale = int(view.Scale)
    except Exception:
        pass
    for attr, prop in (("detail_level", "DetailLevel"), ("display_style", "DisplayStyle")):
        try:
            setattr(spec, attr, _enum_name(getattr(view, prop)))
        except Exception:
            pass
    try:
        spec.crop_active = bool(view.CropBoxActive)
        spec.crop_visible = bool(view.CropBoxVisible)
    except Exception:
        pass
    spec.crop_local = _crop_local(view, inverse)

    view_type = _enum_name(getattr(view, "ViewType", None))
    if view_type == "ThreeD":
        spec.kind = "3d"
        return spec
    if view_type not in ("Section", "Elevation", "Detail"):
        spec.kind = view_type.lower() or u"view"
        spec.skip_reason = u"%s views are not an assembly view kind Clone Drawing can create" % (
            view_type or u"this")
        return spec
    try:
        direction = view.ViewDirection
        looking = direction.Negate() if VIEW_DIRECTION_TOWARDS_VIEWER else direction
        local_dir = _tup(inverse.OfVector(looking))
    except Exception:
        looking, local_dir = None, None
    cut = _cut_position(doc, view, assembly, looking) if looking is not None else None
    orientation = classify_view(view_type, local_dir, cut, spec.crop_local is not None)
    if orientation is None:
        spec.kind = "elevation" if view_type == "Elevation" else "section"
        spec.skip_reason = u"orientation unknown - only the assembly view orientations are cloned"
        return spec
    spec.orientation = orientation
    spec.kind = kind_of_orientation(orientation)
    return spec


def _sheet_spec(doc, sheet):
    from Autodesk.Revit.DB import (BuiltInCategory, FilteredElementCollector,
                                   ScheduleSheetInstance)
    spec = ViewSpec(kind="sheet", src_view_id=eid_value(sheet.Id))
    try:
        spec.sheet_number = sheet.SheetNumber or u""
        spec.sheet_name = sheet.Name or u""
        spec.name = spec.sheet_name
    except Exception:
        pass
    try:
        with disposing(FilteredElementCollector(doc, sheet.Id)) as collector:
            block = collector.OfCategory(BuiltInCategory.OST_TitleBlocks) \
                .WhereElementIsNotElementType().FirstElement()
        if block is not None:
            spec.title_block_type_id = eid_value(block.GetTypeId())
            block_type = doc.GetElement(block.GetTypeId())
            spec.title_block_name = elem_name(block_type) if block_type is not None else u""
    except Exception:
        pass
    viewports = []
    try:
        for vp_id in sheet.GetAllViewports():
            vp = doc.GetElement(vp_id)
            if vp is None:
                continue
            row = ViewportSpec(view_id=eid_value(vp.ViewId), type_id=eid_value(vp.GetTypeId()))
            try:
                row.view_name = doc.GetElement(vp.ViewId).Name
            except Exception:
                pass
            try:
                center = vp.GetBoxCenter()
                row.center = (float(center.X), float(center.Y))
            except Exception:
                pass
            row.rotation = _enum_name(getattr(vp, "Rotation", None))
            offset = getattr(vp, "LabelOffset", None)
            if offset is not None:
                row.label_offset = _tup(offset)
            viewports.append(row)
    except Exception:
        pass
    try:
        with disposing(FilteredElementCollector(doc, sheet.Id)) as collector:
            instances = list(collector.OfClass(ScheduleSheetInstance).ToElements())
        for inst in instances:
            if getattr(inst, "IsTitleblockRevisionSchedule", False):
                continue
            row = ViewportSpec(view_id=eid_value(inst.ScheduleId), is_schedule=True)
            try:
                row.view_name = doc.GetElement(inst.ScheduleId).Name
                point = inst.Point
                row.center = (float(point.X), float(point.Y))
            except Exception:
                pass
            row.rotation = _enum_name(getattr(inst, "Rotation", None))
            viewports.append(row)
    except Exception:
        pass
    spec.viewports = viewports
    return spec


def read_source(doc, assembly, views_index=None):
    """[ViewSpec] of every view and sheet owned by `assembly` (A7).

    ``views_index`` is ``_assembly.collect_assembly_views(doc)`` when the
    caller already has it (one model scan per window). A view that cannot be
    cloned is returned with ``skip_reason`` so it is reported, not dropped.
    """
    if views_index is None:
        views_index = collect_assembly_views(doc)
    view_ids, sheet_ids = views_index.get(eid_value(assembly.Id), ([], []))
    try:
        inverse = assembly.GetTransform().Inverse
    except Exception:
        inverse = None
    specs = []
    for view_id in view_ids:
        view = doc.GetElement(make_eid(view_id))
        if view is None:
            continue
        try:
            specs.append(_view_spec(doc, view, assembly, inverse))
        except Exception as exc:
            specs.append(ViewSpec(kind="view", src_view_id=view_id, name=elem_name(view),
                                  skip_reason=u"could not be read: %s" % short_error(exc)))
    for sheet_id in sheet_ids:
        sheet = doc.GetElement(make_eid(sheet_id))
        if sheet is None:
            continue
        try:
            specs.append(_sheet_spec(doc, sheet))
        except Exception as exc:
            specs.append(ViewSpec(kind="sheet", src_view_id=sheet_id,
                                  skip_reason=u"could not be read: %s" % short_error(exc)))
    return specs


# ── T1 · VIEWS AND SHEET [REVIT] ─────────────────────────────────────────────

def _with_template(create, args, template_id, notes):
    """create(*args, template, True); falls back to create(*args) + assignment."""
    if template_id is not None and eid_value(template_id) > 0:
        try:
            return create(*(tuple(args) + (template_id, True)))
        except Exception as exc:
            notes.append(u"template not applied: %s" % short_error(exc))
    return create(*args)


def _apply_view_props(view, spec, dst_transform, notes):
    """Scale, detail level, display style and crop of `spec` onto `view`.

    A property the view template controls refuses the write; that is kept as
    the template wants it and noted, not treated as a failure.
    """
    from Autodesk.Revit.DB import (BoundingBoxXYZ, DisplayStyle, Transform,
                                   ViewDetailLevel)
    templated = eid_value(getattr(view, "ViewTemplateId", None)) > 0
    refused = []

    def attempt(label, action):
        try:
            action()
        except Exception as exc:
            refused.append(label if templated else u"%s (%s)" % (label, short_error(exc)))

    if spec.scale:
        attempt(u"scale", lambda: setattr(view, "Scale", int(spec.scale)))
    level = getattr(ViewDetailLevel, spec.detail_level or u"", None)
    if level is not None:
        attempt(u"detail level", lambda: setattr(view, "DetailLevel", level))
    style = getattr(DisplayStyle, spec.display_style or u"", None)
    if style is not None:
        attempt(u"display style", lambda: setattr(view, "DisplayStyle", style))
    if spec.crop_local is not None:
        def set_crop():
            origin, bx, by, bz, lo, hi = spec.crop_local
            local = Transform(Transform.Identity)
            local.Origin = _xyz(origin)
            local.BasisX = _xyz(bx)
            local.BasisY = _xyz(by)
            local.BasisZ = _xyz(bz)
            box = BoundingBoxXYZ()
            box.Transform = dst_transform.Multiply(local)
            box.Min = _xyz(lo)
            box.Max = _xyz(hi)
            view.CropBox = box          # [NV] G8: crop of assembly views is writable
        attempt(u"crop box", set_crop)
    if spec.crop_active is not None:
        attempt(u"crop on/off", lambda: setattr(view, "CropBoxActive", bool(spec.crop_active)))
    if spec.crop_visible is not None:
        attempt(u"crop visibility", lambda: setattr(view, "CropBoxVisible", bool(spec.crop_visible)))
    if refused:
        notes.append((u"kept from view template: " if templated else u"not copied: ")
                     + u", ".join(refused))


def _create_view(doc, dst_asm, spec, notes):
    from Autodesk.Revit.DB import AssemblyDetailViewOrientation, AssemblyViewUtils, ElementId
    asm_id = dst_asm.Id
    template = make_eid(spec.template_id) if spec.template_id > 0 else ElementId.InvalidElementId
    if spec.kind == "3d":
        return _with_template(AssemblyViewUtils.Create3DOrthographic, (doc, asm_id), template, notes)
    if spec.kind in ("section", "elevation"):
        orientation = getattr(AssemblyDetailViewOrientation, spec.orientation or u"", None)
        if orientation is None:
            raise ValueError(u"orientation %s is not available in this Revit" % spec.orientation)
        return _with_template(AssemblyViewUtils.CreateDetailSection,
                              (doc, asm_id, orientation), template, notes)
    if spec.kind == "partlist":
        return _with_template(AssemblyViewUtils.CreatePartList, (doc, asm_id), template, notes)
    if spec.kind == "takeoff":
        return _with_template(AssemblyViewUtils.CreateMaterialTakeoff, (doc, asm_id), template, notes)
    if spec.kind == "single_schedule":
        return _with_template(AssemblyViewUtils.CreateSingleCategorySchedule,
                              (doc, asm_id, make_eid(spec.category_id)), template, notes)
    raise ValueError(u"%s views cannot be created" % spec.kind)


def _place_viewports(doc, sheet, spec, view_map, notes):
    """Viewports / schedule instances of the source sheet on the new sheet."""
    from Autodesk.Revit.DB import ScheduleSheetInstance, Viewport, ViewportRotation, XYZ
    placed = 0
    for row in spec.viewports:
        new_id = view_map.get(row.view_id)
        if new_id is None:
            notes.append(u"%s not placed: its view was not cloned" % (row.view_name or row.view_id))
            continue
        point = XYZ(float(row.center[0]), float(row.center[1]), 0.0)
        try:
            if row.is_schedule:
                instance = ScheduleSheetInstance.Create(doc, sheet.Id, make_eid(new_id), point)
                rotation = getattr(ViewportRotation, row.rotation or u"", None)
                if rotation is not None and row.rotation != "None":
                    try:
                        instance.Rotation = rotation
                    except Exception:
                        pass
                placed += 1
                continue
            if not Viewport.CanAddViewToSheet(doc, sheet.Id, make_eid(new_id)):
                notes.append(u"%s not placed: Revit says it cannot go on this sheet "
                             u"(already on another sheet?)" % (row.view_name or new_id))
                continue
            viewport = Viewport.Create(doc, sheet.Id, make_eid(new_id), point)
            if row.type_id > 0:
                try:
                    viewport.ChangeTypeId(make_eid(row.type_id))
                except Exception:
                    pass
            rotation = getattr(ViewportRotation, row.rotation or u"", None)
            if rotation is not None:
                try:
                    viewport.Rotation = rotation
                except Exception:
                    pass
            try:
                viewport.SetBoxCenter(point)
            except Exception:
                pass
            if row.label_offset is not None and hasattr(viewport, "LabelOffset"):
                try:
                    viewport.LabelOffset = _xyz(row.label_offset)
                except Exception:
                    pass
            placed += 1
        except Exception as exc:
            notes.append(u"%s not placed: %s" % (row.view_name or new_id, short_error(exc)))
    return placed


def _create_sheet(doc, dst_asm, spec, view_map, ctx, notes):
    from Autodesk.Revit.DB import AssemblyViewUtils, ElementId
    block = make_eid(spec.title_block_type_id) if spec.title_block_type_id > 0 \
        else ElementId.InvalidElementId
    sheet = AssemblyViewUtils.CreateSheet(doc, dst_asm.Id, block)
    number = unique_name(sheet_number_for(spec, ctx["src_mark"], ctx["dst_mark"],
                                          ctx["sheet_pattern"]), ctx["sheet_numbers"])
    try:
        sheet.SheetNumber = number
        ctx["sheet_numbers"].add(number)
    except Exception as exc:
        notes.append(u"sheet number %s refused: %s" % (number, short_error(exc)))
    try:
        sheet.Name = substitute_mark(spec.sheet_name, ctx["src_mark"], ctx["dst_mark"])
    except Exception as exc:
        notes.append(u"sheet name not set: %s" % short_error(exc))
    placed = _place_viewports(doc, sheet, spec, view_map, notes)
    notes.insert(0, u"%d of %d viewports placed" % (placed, len(spec.viewports)))
    return sheet


def _taken_names(doc):
    """(view names, sheet numbers) already used in the model."""
    from Autodesk.Revit.DB import FilteredElementCollector, View, ViewSheet
    names = set()
    numbers = set()
    with disposing(FilteredElementCollector(doc)) as collector:
        views = list(collector.OfClass(View).ToElements())
    for view in views:
        try:
            if isinstance(view, ViewSheet):
                numbers.add(view.SheetNumber)
            else:
                names.add(view.Name)
        except Exception:
            continue
    return names, numbers


def create_views(doc, dst_asm, specs, progress=None, src_mark=u"", options=None,
                 view_map=None, taken=None):
    """T1 - Transaction "Clone views to <mark>": one SubTransaction per spec.

    Views first, then schedules, then the sheet (its viewports need the new
    views; ``view_map`` may already hold target views paired with source
    views in add-missing mode). Template, scale, detail level, display style
    and crop are copied where the template allows; names follow the view
    pattern with the source mark swapped for the target's. Returns
    ``({src_view_id: new_view_id}, [Result], new_sheet_id_or_-1)``.
    """
    from Autodesk.Revit.DB import SubTransaction, Transaction
    options = options or CloneOptions()
    dst_mark = _assembly_mark(dst_asm)
    view_map = dict(view_map or {})
    names, numbers = taken if taken is not None else _taken_names(doc)
    ctx = {"src_mark": src_mark, "dst_mark": dst_mark, "sheet_numbers": numbers,
           "sheet_pattern": options.sheet_pattern}
    try:
        dst_transform = dst_asm.GetTransform()
    except Exception:
        dst_transform = None
    results = []
    created = {}
    sheet_id = -1
    ordered = creation_order(specs)
    total = len(ordered)

    txn = Transaction(doc, TRANSACTION_PREFIX + u"Clone views to %s" % (dst_mark or u"assembly"))
    with disposing(txn) as t:
        t.Start()
        try:
            for index, spec in enumerate(ordered):
                label = spec.name or spec.sheet_number or spec.kind
                if progress is not None and progress(index + 1, total, label) is False:
                    results.extend(Result(s.name or s.kind, STATUS_SKIPPED, 0, SKIP_STOPPED)
                                   for s in ordered[index:])
                    break
                notes = []
                sub = SubTransaction(doc)
                with disposing(sub) as st:
                    st.Start()
                    try:
                        if spec.kind == "sheet":
                            sheet = _create_sheet(doc, dst_asm, spec, view_map, ctx, notes)
                            new_id = eid_value(sheet.Id)
                            sheet_id = new_id
                            name = sheet.SheetNumber
                        else:
                            view = _create_view(doc, dst_asm, spec, notes)
                            if spec.kind not in SCHEDULE_KINDS and dst_transform is not None:
                                _apply_view_props(view, spec, dst_transform, notes)
                            name = unique_name(view_name_for(spec, src_mark, dst_mark,
                                                             options.view_pattern), names)
                            try:
                                view.Name = name
                                names.add(name)
                            except Exception as exc:
                                notes.append(u"name %s refused: %s" % (name, short_error(exc)))
                                name = view.Name
                            new_id = eid_value(view.Id)
                        st.Commit()
                        created[spec.src_view_id] = new_id
                        view_map[spec.src_view_id] = new_id
                        results.append(Result(name, STATUS_OK, 1, u"; ".join(notes)))
                    except Exception as exc:
                        _rollback(st)
                        results.append(Result(label, STATUS_FAILED, 0, short_error(exc)))
            t.Commit()
        except Exception:
            _rollback(t)
            raise
    return created, results, sheet_id


# ── T2 · FREE ANNOTATIONS [REVIT] ────────────────────────────────────────────

def annotation_split(doc, src_view):
    """(ids to copy, [(id, reason)] not copied) of the view-owned elements.

    Tags and dimensions are T3's job and are left out of both lists.
    """
    from Autodesk.Revit.DB import Dimension, FilteredElementCollector, IndependentTag
    copy_cats = _category_ints(T2_CATEGORY_NAMES)
    ignore_cats = _category_ints(T2_IGNORED_CATEGORY_NAMES)
    view_int = eid_value(src_view.Id)
    with disposing(FilteredElementCollector(doc, src_view.Id)) as collector:
        elements = list(collector.WhereElementIsNotElementType().ToElements())
    copy_ids = []
    left_out = []
    for element in elements:
        try:
            if eid_value(element.OwnerViewId) != view_int:
                continue
            if isinstance(element, (IndependentTag, Dimension)):
                continue
            if type(element).__name__ == "MultiReferenceAnnotation":
                continue
            category = _category_int(element)
            if category is None or category in ignore_cats:
                continue
            if category in copy_cats:
                copy_ids.append(eid_value(element.Id))
            else:
                left_out.append((eid_value(element.Id),
                                 u"%s is not copied in V1" % _category_label(element)))
        except Exception:
            continue
    return copy_ids, left_out


def copy_annotations(doc, src_view, dst_view, transform, progress=None):
    """T2 for one view: a SubTransaction around ElementTransformUtils.CopyElements.

    Run inside the caller's Transaction "Copy annotations to <mark>". A view
    whose copy Revit refuses is rolled back alone (R1) and every element of
    it is reported. Returns ``(copied_count, Result, [(src_id, reason)])``.
    """
    from Autodesk.Revit.DB import (CopyPasteOptions, ElementId, ElementTransformUtils,
                                   SubTransaction)
    label = src_view.Name
    if progress is not None and progress(1, 1, label) is False:
        return 0, Result(label, STATUS_SKIPPED, 0, SKIP_STOPPED), []
    ids, left_out = annotation_split(doc, src_view)
    if not ids:
        return 0, Result(label, STATUS_SKIPPED, 0, u"no free annotations"), left_out
    sub = SubTransaction(doc)
    with disposing(sub) as st:
        st.Start()
        try:
            copied = ElementTransformUtils.CopyElements(
                src_view, net_list(ElementId, [make_eid(i) for i in ids]), dst_view,
                transform, CopyPasteOptions())
            st.Commit()
        except Exception as exc:
            _rollback(st)
            reason = u"copy refused by Revit: %s" % short_error(exc)
            return 0, Result(label, STATUS_SKIPPED, 0, reason), \
                left_out + [(i, reason) for i in ids]
    count = copied.Count if copied is not None else 0
    return count, Result(label, STATUS_OK, count, u"%d copied" % count), left_out


def t2_transform(src_asm, dst_asm, is_sheet):
    """Transform handed to CopyElements for one view pair (T2_TRANSFORM_MODE)."""
    from Autodesk.Revit.DB import Transform
    if is_sheet or T2_TRANSFORM_MODE != "delta":
        return Transform.Identity          # sheets are paper space
    return transform_between(src_asm, dst_asm)


# ── T3 · TAGS AND DIMENSIONS [REVIT] ─────────────────────────────────────────

class _MemberKey(object):
    """What fingerprint() reads from a record; built for hosts and rebar alike."""

    def __init__(self, element_id, kind, shape, bar_type, quantity):
        self.id = element_id
        self.kind = kind
        self.shape = shape
        self.bar_type = bar_type
        self.quantity = quantity


def _member_key(doc, element):
    element_id = eid_value(element.Id)
    type_name = u""
    try:
        type_el = doc.GetElement(element.GetTypeId())
        type_name = elem_name(type_el) if type_el is not None else u""
    except Exception:
        pass
    if _is_rebar_like(element):
        shape = u""
        try:
            shape_el = doc.GetElement(element.GetShapeId())
            shape = elem_name(shape_el) if shape_el is not None else u""
        except Exception:
            pass
        quantity = 1
        try:
            quantity = int(element.Quantity)
        except Exception:
            pass
        return _MemberKey(element_id, type(element).__name__, shape, type_name, quantity)
    return _MemberKey(element_id, u"Element:%s" % _category_label(element), u"", type_name, 1)


def _member_items(doc, assembly):
    transform = assembly.GetTransform()
    items = []
    for element in _member_elements(doc, assembly):
        center = local_center(element, transform)
        if center is None:
            continue
        items.append((_member_key(doc, element), center))
    return items


def match_members(doc, src_asm, dst_asm, tol_mm=DEFAULT_TOL_MM, allow_mirror=True):
    """({src_id: dst_id}, {src_id: reason}) between the members of two assemblies.

    Uses ``_rebar.match_by_fingerprint`` on (kind, shape, type, quantity) +
    the centre in each assembly's local frame. The assemblies themselves pair
    too, so a tag on the assembly instance is re-created on the target.
    """
    dst_mark = _assembly_mark(dst_asm) or u"the target"
    pairs, unmatched, ambiguous = match_by_fingerprint(
        _member_items(doc, src_asm), _member_items(doc, dst_asm),
        tol_mm=tol_mm, allow_mirror=allow_mirror)
    mapping = {eid_value(src_asm.Id): eid_value(dst_asm.Id)}
    for src_item, dst_item in pairs:
        mapping[src_item[0].id] = dst_item[0].id
    reasons = {}
    for item in unmatched:
        reasons[item[0].id] = R_NO_MATCH % dst_mark
    for item in ambiguous:
        reasons[item[0].id] = R_AMBIGUOUS % dst_mark
    return mapping, reasons


def _tagged_references(tag):
    getter = getattr(tag, "GetTaggedReferences", None)          # 2022+
    if getter is not None:
        try:
            return list(getter())
        except Exception:
            pass
    single = getattr(tag, "GetTaggedReference", None)
    if single is not None:
        try:
            return [single()]
        except Exception:
            pass
    return []


def _reference_is_whole_element(reference):
    return _enum_name(getattr(reference, "ElementReferenceType", None)) in (
        u"", u"REFERENCE_TYPE_NONE")


def _is_linked(reference):
    try:
        return eid_value(reference.LinkedElementId) >= 0
    except Exception:
        return False


def _tag_target(doc, tag, pairs, reasons):
    """(dst element id, None) or (None, reason) for one source tag."""
    references = _tagged_references(tag)
    if not references:
        return None, R_SUBELEMENT
    if len(references) > 1:
        return None, R_MULTI_REF
    reference = references[0]
    if _is_linked(reference):
        return None, R_LINKED
    src_id = eid_value(reference.ElementId)
    element = doc.GetElement(reference.ElementId)
    if not _reference_is_whole_element(reference):
        return None, R_REBAR_POSITION if element is not None and _is_rebar_like(element) \
            else R_SUBELEMENT
    if src_id in pairs:
        return pairs[src_id], None
    return None, reasons.get(src_id, R_NOT_MEMBER)


def _recreate_tag(doc, tag, dst_view, dst_id, transform):
    from Autodesk.Revit.DB import IndependentTag, Reference
    dst_el = doc.GetElement(make_eid(dst_id))
    head = transform.OfPoint(tag.TagHeadPosition)
    new_tag = IndependentTag.Create(doc, tag.GetTypeId(), dst_view.Id, Reference(dst_el),
                                    bool(tag.HasLeader), tag.TagOrientation, head)
    if tag.HasLeader:
        try:
            new_tag.LeaderEndCondition = tag.LeaderEndCondition
        except Exception:
            pass
        try:
            new_tag.TagHeadPosition = head
        except Exception:
            pass
    return new_tag


def _planar_faces(element):
    """[(reference, normal XYZ model, origin XYZ model)] of an element's planar faces.

    Family geometry is read from the symbol (its references are the ones a
    dimension accepts) and moved by the instance transform. [NV] G3.
    """
    from Autodesk.Revit.DB import GeometryInstance, Options, PlanarFace, Solid, Transform
    options = Options()
    options.ComputeReferences = True
    options.IncludeNonVisibleObjects = False
    out = []

    def walk(geometry, transform):
        if geometry is None:
            return
        for obj in geometry:
            if isinstance(obj, Solid):
                for face in obj.Faces:
                    if isinstance(face, PlanarFace) and face.Reference is not None:
                        out.append((face.Reference, transform.OfVector(face.FaceNormal),
                                    transform.OfPoint(face.Origin)))
            elif isinstance(obj, GeometryInstance):
                walk(obj.GetSymbolGeometry(), transform.Multiply(obj.Transform))

    walk(element.get_Geometry(options), Transform.Identity)
    return out


def _face_keys(element, faces, inverse):
    """[(normal_local, distance_mm)] for ``match_face``."""
    from Autodesk.Revit.DB import XYZ
    box = element.get_BoundingBox(None)
    center = XYZ((box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0,
                 (box.Min.Z + box.Max.Z) / 2.0)
    keys = []
    for _, normal, origin in faces:
        keys.append((_tup(inverse.OfVector(normal)),
                     to_mm(origin.Subtract(center).DotProduct(normal))))
    return keys


def _stable(doc, reference):
    try:
        return reference.ConvertToStableRepresentation(doc)
    except Exception:
        return None


def _dimension_refs(doc, dim, pairs, reasons, src_asm, dst_asm, face_cache, mark):
    """ReferenceArray for the target, or (None, reason)."""
    from Autodesk.Revit.DB import ReferenceArray
    out = ReferenceArray()
    src_inverse = src_asm.GetTransform().Inverse
    dst_inverse = dst_asm.GetTransform().Inverse
    for reference in dim.References:
        if _is_linked(reference):
            return None, R_DIM_NOT_FACE
        src_id = eid_value(reference.ElementId)
        element = doc.GetElement(reference.ElementId)
        if element is None:
            return None, R_DIM_NOT_FACE
        if _is_rebar_like(element):
            return None, R_DIM_REBAR
        if _enum_name(getattr(reference, "ElementReferenceType", None)) != "REFERENCE_TYPE_SURFACE":
            return None, R_DIM_NOT_FACE
        if src_id not in pairs:
            return None, reasons.get(src_id, R_NOT_MEMBER)
        dst_el = doc.GetElement(make_eid(pairs[src_id]))
        if dst_el is None:
            return None, R_NO_MATCH % mark
        if ("src", src_id) not in face_cache:
            faces = _planar_faces(element)
            face_cache[("src", src_id)] = (faces, _face_keys(element, faces, src_inverse))
        src_faces, src_keys = face_cache[("src", src_id)]
        wanted = _stable(doc, reference)
        index = None
        for i, (face_ref, _, _) in enumerate(src_faces):
            if wanted is not None and _stable(doc, face_ref) == wanted:
                index = i
                break
        if index is None:
            return None, R_DIM_NOT_FACE
        dst_key = ("dst", eid_value(dst_el.Id))
        if dst_key not in face_cache:
            faces = _planar_faces(dst_el)
            face_cache[dst_key] = (faces, _face_keys(dst_el, faces, dst_inverse))
        dst_faces, dst_keys = face_cache[dst_key]
        hit, reason = match_face(src_keys[index], dst_keys)
        if hit is None:
            return None, reason
        out.Append(dst_faces[hit][0])
    return out, None


def _recreate_dimension(doc, dim, dst_view, refs, transform):
    from Autodesk.Revit.DB import Line
    curve = dim.Curve
    origin = transform.OfPoint(curve.Origin)
    direction = transform.OfVector(curve.Direction)
    line = Line.CreateBound(origin, origin.Add(direction))
    return doc.Create.NewDimension(dst_view, line, refs, dim.DimensionType)


def recreate_references(doc, src_view, dst_view, pairs, src_asm, dst_asm, progress=None,
                        reasons=None):
    """T3 (D17) for one view pair. Run inside the caller's Transaction.

    Tags: each IndependentTag on ONE whole element that has a match is
    re-created on the matched element (type, leader, orientation, head moved
    by ``transform_between``). Dimensions: each linear dimension whose every
    reference is a FACE of a matched host is re-created between the faces
    matched by (local normal, distance from centre) within 5 mm. Spot
    elevations, multi-rebar annotations, dimensions to rebar, rebar set
    position tags and anything unmatched are listed in ``Tally.unmatched``
    with the reason. Every element gets its own SubTransaction.
    """
    from Autodesk.Revit.DB import (Dimension, FilteredElementCollector, IndependentTag,
                                   SpotDimension, SubTransaction)
    reasons = reasons or {}
    tally = Tally()
    mark = _assembly_mark(dst_asm) or u"the target"
    transform = transform_between(src_asm, dst_asm)
    with disposing(FilteredElementCollector(doc, src_view.Id)) as collector:
        elements = list(collector.WhereElementIsNotElementType().ToElements())

    owned_by_mra = set()
    for element in elements:
        if type(element).__name__ != "MultiReferenceAnnotation":
            continue
        tally.other(eid_value(element.Id), u"annotation: " + R_MRA)
        for attr in ("DimensionId", "TagId"):
            try:
                owned_by_mra.add(eid_value(getattr(element, attr)))
            except Exception:
                pass

    tags = [e for e in elements if isinstance(e, IndependentTag)
            and eid_value(e.Id) not in owned_by_mra]
    dims = [e for e in elements if isinstance(e, Dimension)
            and eid_value(e.Id) not in owned_by_mra]
    total = len(tags) + len(dims)
    step = [0]

    def advance(label):
        step[0] += 1
        return progress is None or progress(step[0], total, label) is not False

    def isolated(action):
        sub = SubTransaction(doc)
        with disposing(sub) as st:
            st.Start()
            try:
                action()
                st.Commit()
                return None
            except Exception as exc:
                _rollback(st)
                return short_error(exc)

    for tag in tags:
        tag_id = eid_value(tag.Id)
        if not advance(u"tag %d" % tag_id):
            tally.tag(False, tag_id, SKIP_STOPPED)
            continue
        dst_id, reason = _tag_target(doc, tag, pairs, reasons)
        if dst_id is None:
            tally.tag(False, tag_id, reason)
            continue
        error = isolated(lambda: _recreate_tag(doc, tag, dst_view, dst_id, transform))
        tally.tag(error is None, tag_id, u"Revit refused the tag: %s" % error if error else u"")

    face_cache = {}
    for dim in dims:
        dim_id = eid_value(dim.Id)
        if not advance(u"dimension %d" % dim_id):
            tally.dim(False, dim_id, SKIP_STOPPED)
            continue
        if isinstance(dim, SpotDimension):
            tally.dim(False, dim_id, R_SPOT)
            continue
        shape = _enum_name(getattr(dim, "DimensionShape", None))
        if shape and shape != "Linear":
            tally.dim(False, dim_id, R_DIM_SHAPE % shape.lower())
            continue
        try:
            refs, reason = _dimension_refs(doc, dim, pairs, reasons, src_asm, dst_asm,
                                           face_cache, mark)
        except Exception as exc:
            refs, reason = None, u"could not read its references: %s" % short_error(exc)
        if refs is None:
            tally.dim(False, dim_id, reason)
            continue
        error = isolated(lambda: _recreate_dimension(doc, dim, dst_view, refs, transform))
        tally.dim(error is None, dim_id,
                  u"Revit refused the dimension: %s" % error if error else u"")
    return tally


# ── ONE TARGET / THE WHOLE RUN [REVIT] ───────────────────────────────────────

def _view_of(doc, view_id):
    return doc.GetElement(make_eid(view_id)) if view_id is not None else None


def _is_same_type(src_asm, dst_asm):
    try:
        return eid_value(src_asm.GetTypeId()) == eid_value(dst_asm.GetTypeId())
    except Exception:
        return False


def clone_to_target(doc, src_asm, dst_asm, src_specs, options, views_index, taken):
    """T1 -> T2 -> T3 for one target, three Transactions (caller holds the group).

    T2 / T3 go to the views T1 created; with T1 off they go to the target
    views paired with the source views (the user asked for annotations only).
    Never deletes anything. Returns a TargetOutcome.
    """
    from Autodesk.Revit.DB import Transaction
    src_mark = _assembly_mark(src_asm)
    dst_mark = _assembly_mark(dst_asm)
    outcome = TargetOutcome(eid_value(dst_asm.Id), dst_mark or u"assembly %d" % eid_value(dst_asm.Id))
    label = outcome.mark

    if SAME_TYPE_POLICY == "skip" and _is_same_type(src_asm, dst_asm):
        outcome.status = STATUS_SKIPPED
        outcome.detail = SAME_TYPE_REASON
        outcome.note(LEVEL_SKIPPED, u"skipped %s: %s" % (label, SAME_TYPE_REASON))
        return outcome

    dst_specs = read_source(doc, dst_asm, views_index)
    clonable = [s for s in src_specs if not s.skip_reason]
    outcome.views_planned = len([s for s in clonable if s.kind != "sheet"])

    # Which target view receives each source view's annotations.
    if options.t1:
        to_create, skipped = plan_clone(src_specs, dst_specs, options.add_missing)
        existing_pairs = pair_existing(src_specs, dst_specs) if options.add_missing else {}
        if to_create:
            for spec, reason in skipped:
                if not spec.skip_reason:
                    outcome.note(LEVEL_SKIPPED, u"skipped %s: %s %s - %s" % (
                        label, u"sheet" if spec.kind == "sheet" else u"view",
                        spec.name or spec.sheet_number, reason))
        if not to_create:
            reasons = set(r for s, r in skipped if not s.skip_reason)
            outcome.status = STATUS_SKIPPED
            if not clonable:
                outcome.detail = u"no view of the source can be cloned"
            elif u"has drawing" in reasons:
                outcome.detail = u"has drawing"
            else:
                outcome.detail = u"nothing missing"
            outcome.note(LEVEL_SKIPPED, u"skipped %s: %s" % (label, outcome.detail))
            return outcome
        try:
            created, results, sheet_id = create_views(
                doc, dst_asm, to_create, src_mark=src_mark, options=options,
                view_map=existing_pairs, taken=taken)
        except Exception as exc:
            outcome.status = STATUS_FAILED
            outcome.detail = short_error(exc)
            outcome.note(LEVEL_FAILED, u"failed %s: views not created - %s" % (label, outcome.detail))
            return outcome
        outcome.views_created = len([v for k, v in created.items() if v != sheet_id])
        outcome.sheet_id = sheet_id
        if sheet_id > 0:
            sheet = _view_of(doc, sheet_id)
            outcome.sheet_number = getattr(sheet, "SheetNumber", u"") or u""
        for row in results:
            level = {STATUS_OK: LEVEL_OK, STATUS_SKIPPED: LEVEL_SKIPPED}.get(row.status, LEVEL_FAILED)
            text = u"%s %s: %s" % (row.status, label, row.name)
            if row.detail:
                text += u" - " + row.detail
            outcome.note(level, text)
        failed = [r for r in results if r.status == STATUS_FAILED]
        if failed and len(failed) == len(results):
            outcome.status = STATUS_FAILED
            outcome.detail = failed[0].detail
        targets = created
    else:
        targets = pair_existing(src_specs, dst_specs)
        if not targets:
            outcome.status = STATUS_SKIPPED
            outcome.detail = u"no target views to annotate (T1 is off)"
            outcome.note(LEVEL_SKIPPED, u"skipped %s: %s" % (label, outcome.detail))
            return outcome

    # The target is being worked on: source views that cannot be cloned are
    # unmatched for it (a skipped target does not count them).
    for spec in src_specs:
        if spec.skip_reason:
            outcome.unmatched.append((spec.src_view_id, u"view %s: %s" % (spec.name, spec.skip_reason)))
            outcome.note(LEVEL_SKIPPED, u"unmatched %s: view %s - %s"
                         % (label, spec.name, spec.skip_reason))

    spec_by_id = dict((s.src_view_id, s) for s in src_specs)
    view_pairs = [(src_id, dst_id) for src_id, dst_id in targets.items()
                  if spec_by_id.get(src_id) is not None
                  and spec_by_id[src_id].kind not in SCHEDULE_KINDS]

    if options.t2 and view_pairs:
        txn = Transaction(doc, TRANSACTION_PREFIX + u"Copy annotations to %s" % (dst_mark or label))
        with disposing(txn) as t:
            t.Start()
            try:
                for src_id, dst_id in view_pairs:
                    src_view, dst_view = _view_of(doc, src_id), _view_of(doc, dst_id)
                    if src_view is None or dst_view is None:
                        continue
                    is_sheet = spec_by_id[src_id].kind == "sheet"
                    try:
                        count, row, left_out = copy_annotations(
                            doc, src_view, dst_view, t2_transform(src_asm, dst_asm, is_sheet))
                    except Exception as exc:
                        count, row, left_out = 0, Result(src_view.Name, STATUS_FAILED, 0,
                                                         short_error(exc)), []
                    outcome.copied += count
                    outcome.unmatched.extend(left_out)
                    if row.status != STATUS_SKIPPED or row.detail != u"no free annotations":
                        level = {STATUS_OK: LEVEL_OK}.get(row.status, LEVEL_SKIPPED)
                        outcome.note(level, u"%s %s: annotations of %s - %s"
                                     % (row.status, label, row.name, row.detail))
                    for element_id, reason in left_out:
                        outcome.note(LEVEL_SKIPPED, u"unmatched %s: element %s - %s"
                                     % (label, element_id, reason))
                t.Commit()
            except Exception as exc:
                _rollback(t)
                outcome.note(LEVEL_FAILED, u"failed %s: annotations - %s" % (label, short_error(exc)))

    model_pairs = [(s, d) for s, d in view_pairs if spec_by_id[s].kind != "sheet"]
    if options.t3 and model_pairs:
        try:
            pairs, reasons = match_members(doc, src_asm, dst_asm, options.tol_mm,
                                           options.allow_mirror)
        except Exception as exc:
            pairs, reasons = {}, {}
            outcome.note(LEVEL_FAILED, u"failed %s: element matching - %s" % (label, short_error(exc)))
        txn = Transaction(doc, TRANSACTION_PREFIX + u"Re-create references on %s" % (dst_mark or label))
        with disposing(txn) as t:
            t.Start()
            try:
                for src_id, dst_id in model_pairs:
                    src_view, dst_view = _view_of(doc, src_id), _view_of(doc, dst_id)
                    if src_view is None or dst_view is None:
                        continue
                    try:
                        tally = recreate_references(doc, src_view, dst_view, pairs,
                                                    src_asm, dst_asm, reasons=reasons)
                    except Exception as exc:
                        outcome.note(LEVEL_FAILED, u"failed %s: tags and dimensions of %s - %s"
                                     % (label, src_view.Name, short_error(exc)))
                        continue
                    outcome.tally.merge(tally)
                    for element_id, reason in tally.unmatched:
                        outcome.unmatched.append((element_id, reason))
                        outcome.note(LEVEL_SKIPPED, u"unmatched %s: element %s in %s - %s"
                                     % (label, element_id, src_view.Name, reason))
                t.Commit()
            except Exception as exc:
                _rollback(t)
                outcome.note(LEVEL_FAILED, u"failed %s: tags and dimensions - %s"
                             % (label, short_error(exc)))

    if outcome.status == STATUS_OK:
        outcome.detail = outcome_summary(outcome)
        outcome.note(LEVEL_OK, u"ok %s: %s" % (label, outcome.detail))
    return outcome


def outcome_summary(outcome):
    """'5 views, sheet S-01-C-02 · 12 copied · tags 4 / 5 · dims 2 / 2 · 3 unmatched'."""
    parts = [_plural(outcome.views_created, u"view")]
    if outcome.sheet_number:
        parts[0] += u", sheet %s" % outcome.sheet_number
    if outcome.copied:
        parts.append(u"%d copied" % outcome.copied)
    if outcome.tally.tags_total:
        parts.append(u"tags %d / %d" % (outcome.tally.tags_ok, outcome.tally.tags_total))
    if outcome.tally.dims_total:
        parts.append(u"dims %d / %d" % (outcome.tally.dims_ok, outcome.tally.dims_total))
    if outcome.unmatched:
        parts.append(u"%d unmatched" % len(outcome.unmatched))
    return u" · ".join(parts)


def run_clone(doc, src_id, target_ids, options, progress=None):
    """Clone the drawing of assembly `src_id` to every id of `target_ids`.

    ONE TransactionGroup "T3Lab: Clone drawing" (Ctrl+Z undoes the click),
    three Transactions per target. ``progress(index, total, label)`` returning
    False stops; the remaining targets are ``skipped: stopped``. Raises only
    when the group itself fails (it is then rolled back). Returns
    [TargetOutcome] in target order.
    """
    from Autodesk.Revit.DB import TransactionGroup
    src_asm = doc.GetElement(make_eid(src_id))
    if src_asm is None:
        raise ValueError(u"Source assembly %s no longer exists." % src_id)
    views_index = collect_assembly_views(doc)
    src_specs = read_source(doc, src_asm, views_index)
    taken = _taken_names(doc)
    outcomes = []
    total = len(target_ids)

    group = TransactionGroup(doc, TRANSACTION_PREFIX + u"Clone drawing")
    with disposing(group) as g:
        g.Start()
        try:
            for index, raw in enumerate(target_ids):
                dst_asm = doc.GetElement(make_eid(raw))
                mark = _assembly_mark(dst_asm) if dst_asm is not None else u"assembly %s" % raw
                if progress is not None and progress(index + 1, total, mark) is False:
                    for rest in target_ids[index:]:
                        rest_el = doc.GetElement(make_eid(rest))
                        out = TargetOutcome(eid_value(rest), _assembly_mark(rest_el)
                                            if rest_el is not None else u"%s" % rest)
                        out.status = STATUS_SKIPPED
                        out.detail = SKIP_STOPPED
                        out.note(LEVEL_SKIPPED, u"skipped %s: %s" % (out.mark, SKIP_STOPPED))
                        outcomes.append(out)
                    break
                if dst_asm is None:
                    out = TargetOutcome(eid_value(raw), mark)
                    out.status = STATUS_SKIPPED
                    out.detail = u"assembly no longer exists"
                    out.note(LEVEL_SKIPPED, u"skipped %s: %s" % (mark, out.detail))
                    outcomes.append(out)
                    continue
                try:
                    outcome = clone_to_target(doc, src_asm, dst_asm, src_specs, options,
                                              views_index, taken)
                except Exception as exc:
                    outcome = TargetOutcome(eid_value(raw), mark)
                    outcome.status = STATUS_FAILED
                    outcome.detail = short_error(exc)
                    outcome.note(LEVEL_FAILED, u"failed %s: %s" % (mark, outcome.detail))
                outcomes.append(outcome)
            g.Assimilate()
        except Exception:
            _rollback(g)
            raise
    return outcomes


def summarize_outcomes(outcomes):
    """{'ok', 'skipped', 'failed', 'views', 'sheets', 'unmatched'} counts."""
    out = {STATUS_OK: 0, STATUS_SKIPPED: 0, STATUS_FAILED: 0,
           "views": 0, "sheets": 0, "unmatched": 0}
    for outcome in outcomes:
        out[outcome.status] = out.get(outcome.status, 0) + 1
        out["views"] += outcome.views_created
        out["sheets"] += 1 if outcome.sheet_id > 0 else 0
        out["unmatched"] += outcome.unmatched_count
    return out
