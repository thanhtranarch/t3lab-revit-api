# -*- coding: utf-8 -*-
"""
_drawing_clone.py
=================
Clone Drawing V2 (Tekla: clone drawing): copy the finished drawing of one
assembly (cast unit) - its assembly views, sheet, free annotations - to similar
assemblies, and re-create tags, dimensions and spot elevations on the matched
target elements. Design: dev/plan/clone-drawing-v2-design.md (this module
follows it; where they differ the design wins).

Layers, each one Transaction per target inside ONE TransactionGroup (one undo
step for the click):

* T0  "Replace annotations on <mark>"  - only with the explicit, confirmed
                                          ``existing = replace_annotations``
                                          policy: deletes annotation elements of
                                          the target's assembly views, never a
                                          view, sheet, viewport or model element.
* T1  "Clone views to <mark>"          - AssemblyViewUtils.Create... per source
                                          view (template / scale / crop / title),
                                          manual sections and callouts, the sheet
                                          with viewports and sheet parameters.
* T2  "Copy annotations to <mark>"     - view-owned text, symbols, detail lines,
                                          regions, detail items, imports, images,
                                          revision clouds copied view to view;
                                          graphic overrides re-applied.
* T3  "Re-create references on <mark>" - IndependentTags on matched elements
                                          (plus "create for unmatched" marks),
                                          dimensions between matched host faces,
                                          spot elevations on matched faces. Every
                                          thing else is UNMATCHED with a reason.

Two halves, kept apart on purpose:

* PURE PYTHON  - settings + presets, view classification, mark substitution,
  similarity, planning per existing-drawing policy, face matching, dedupe keys,
  report text. No Revit import at module level: ``dev/test_rebar_fingerprint.py``
  and ``dev/test_clone_drawing_v2.py`` exercise the shipped source.
* REVIT        - readers and the four layers. ``Autodesk.Revit.DB`` is imported
  inside each function.

Everything marked [NV] is unverified until the spike (design doc section 9)
has run; each such call degrades to a ``failed`` / ``skipped`` / unmatched row
with the Revit message - nothing is dropped silently.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"
__title__ = "Drawing Clone"

import json
import math
import os
import time

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

# [NV] G2: how ElementTransformUtils.CopyElements(view -> view) expects its
# transform for assembly views. 'delta' = transform_between(src, dst)
# (model -> model, the text lands at the same spot relative to the target);
# 'identity' = Transform.Identity. ONE constant, flipped after the spike.
T2_TRANSFORM_MODE = "delta"

# [NV] G1: instances of ONE assembly type share their views (one instance owns
# them, AssemblyViewUtils.AcquireAssemblyViews moves ownership between siblings
# of the same type - rvtdocs 2025). "skip" = a target of the source's own type
# is reported with SAME_TYPE_REASON; "clone" = treated like any other target.
SAME_TYPE_POLICY = "skip"
SAME_TYPE_REASON = (u"same type as the source - Revit shares one set of views "
                    u"between instances of one assembly type; nothing to clone")
# Tekla K4: cloned drawings need the same main part type (naming category here).
CATEGORY_REASON = u"different naming category (Tekla: main part type) - %s, not %s"

# [NV] G14: Revit's View.ViewDirection points TOWARDS the viewer; the
# classification tables below are written with the LOOKING direction, which
# is its negative. Flip this if G14 shows the opposite.
VIEW_DIRECTION_TOWARDS_VIEWER = True

VIEW_KINDS = ("3d", "section", "elevation", "manual_section", "manual_callout",
              "partlist", "takeoff", "single_schedule", "sheet")
SCHEDULE_KINDS = ("partlist", "takeoff", "single_schedule")
MANUAL_KINDS = ("manual_section", "manual_callout")
MODEL_VIEW_KINDS = ("3d", "section", "elevation", "manual_section", "manual_callout")

# Looking direction in the assembly's LOCAL frame -> orientation name.
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

# Similarity penalties (design 3.2).
PENALTY_CATEGORY = 40
PENALTY_BBOX = 30
PENALTY_REBAR = 20

DEFAULT_SHEET_PATTERN = u"{SourceNumber}-{Mark}"
DEFAULT_VIEW_PATTERN = u"{SourceName}"
PATTERN_TOKENS = (u"{SourceName}", u"{SourceNumber}", u"{Mark}", u"{SourceMark}")

# ── Clone settings (design section 4) ───────────────────────────────────────

CLONE = "clone"
SKIP = "skip"
CLONE_CREATE = "clone_create"
CROP_SOURCE = "source"
CROP_FIT = "fit"
EXISTING_SKIP = "skip"
EXISTING_ADD = "add_missing"
EXISTING_NEW = "new_set"
EXISTING_REPLACE = "replace_annotations"
EXISTING_CHOICES = (EXISTING_SKIP, EXISTING_ADD, EXISTING_NEW, EXISTING_REPLACE)

# (field, label, choices, default, hint) - one row per Tekla object type; the
# order is the order of the Clone settings page. Rows with ONE choice are
# shown disabled with the hint as the reason (Tekla has them, Revit cannot).
SETTINGS_ROWS = (
    ("views", u"Views and sheet", (CLONE, SKIP), CLONE,
     u"Assembly views (3D, elevations, sections, schedules) and the sheet with its viewports"),
    ("crop", u"Crop box", (CROP_SOURCE, CROP_FIT), CROP_SOURCE,
     u"Source: the source crop moved into the target. Fit: Revit's own crop around the target"),
    ("manual_views", u"Manual sections and callouts", (CLONE, SKIP), CLONE,
     u"Sections and callouts drawn inside the source views, re-created from the parent view"),
    ("dimensions", u"Dimensions", (CLONE, SKIP), CLONE,
     u"Re-created between matched host faces. Create (auto-dimension) is impossible in Revit"),
    ("spot", u"Spot elevations (level marks)", (CLONE, SKIP), CLONE,
     u"Re-created on matched host faces; spot coordinates and slopes are reported"),
    ("tags", u"Tags (marks)", (CLONE, CLONE_CREATE, SKIP), CLONE_CREATE,
     u"Clone: tags on matched elements. Clone + create: also a new tag of the same type on every unmatched target member"),
    ("mra", u"Multi-rebar annotations", (SKIP,), SKIP,
     u"Not supported - the MultiReferenceAnnotation options API is unverified; listed as unmatched"),
    ("texts", u"Text notes", (CLONE, SKIP), CLONE,
     u"Copied view to view at the same place relative to the assembly"),
    ("symbols", u"Symbols (generic annotations)", (CLONE, SKIP), CLONE,
     u"Annotation symbol families placed in the view"),
    ("shapes", u"Detail lines, regions, detail items, groups", (CLONE, SKIP), CLONE,
     u"Detail lines, filled and masking regions, detail components, insulation, detail groups"),
    ("imports", u"Imported DWG/DXF (view-specific)", (CLONE, SKIP), CLONE,
     u"CAD imported into the view; linked CAD is model-wide and needs nothing"),
    ("images", u"Images", (CLONE, SKIP), CLONE,
     u"Raster images placed in the view"),
    ("revisions", u"Revision clouds", (CLONE, SKIP), SKIP,
     u"Off by default: a copied cloud adds its revision to the new sheet"),
    ("overrides", u"Graphic overrides", (CLONE, SKIP), CLONE,
     u"Per-element overrides of the source view re-applied to copied and matched elements"),
    ("sheet_params", u"Sheet parameters", (CLONE, SKIP), CLONE,
     u"Drawn By, Checked By and every other text parameter of the sheet"),
)
SETTINGS_FIELDS = tuple(row[0] for row in SETTINGS_ROWS)

CHOICE_LABELS = {CLONE: u"Clone", SKIP: u"Skip", CLONE_CREATE: u"Clone + create for unmatched",
                 CROP_SOURCE: u"Copy from source", CROP_FIT: u"Fit to target"}
EXISTING_LABELS = {
    EXISTING_SKIP: u"Skip assemblies that already have views",
    EXISTING_ADD: u"Add missing views and annotations (nothing deleted)",
    EXISTING_NEW: u"Create a new set of views next to the existing ones",
    EXISTING_REPLACE: u"Replace annotations (deletes them first - confirmed)",
}

PRESET_FILE_NAME = "clone_drawing_presets.json"
PRESET_VERSION = 2
PRESET_TEKLA = u"Tekla default"
PRESET_VIEWS = u"Views only"
PRESET_ANNOTATIONS = u"Annotations only"
BUILTIN_PRESET_NAMES = (PRESET_TEKLA, PRESET_VIEWS, PRESET_ANNOTATIONS)

# T2: view-owned categories copied view to view, by settings row.
T2_ROW_CATEGORIES = (
    ("texts", ("OST_TextNotes",)),
    ("symbols", ("OST_GenericAnnotation",)),
    ("shapes", ("OST_Lines", "OST_DetailComponents", "OST_FilledRegion", "OST_MaskingRegion",
                "OST_InsulationLines", "OST_IOSDetailGroups")),
    ("imports", ("OST_ImportObjectStyles",)),       # view-specific ImportInstance [NV] G20
    ("images", ("OST_RasterImages",)),
    ("revisions", ("OST_RevisionClouds",)),
)
T2_ALL_CATEGORY_NAMES = tuple(name for _, names in T2_ROW_CATEGORIES for name in names)
# View-owned elements that belong to the view machinery, not to the drawing:
# never copied, never deleted, never reported.
T2_IGNORED_CATEGORY_NAMES = ("OST_Viewers", "OST_CropBoundary", "OST_SectionBox",
                             "OST_ReferenceViewer", "OST_TitleBlocks", "OST_Viewports",
                             "OST_ScheduleGraphics", "OST_SketchLines", "OST_Elev",
                             "OST_Callouts", "OST_Sections", "OST_Matchline",
                             "OST_GuideGrid", "OST_IOSSketchGrid", "OST_Cameras",
                             "OST_ElevationMarks", "OST_CalloutHeads", "OST_SectionHeads",
                             "OST_ViewportLabel", "OST_SketchOptions")
# Categories the Replace-annotations policy may delete (design section 5 / G25):
# only annotation kinds this tool itself creates or copies.
DELETE_CATEGORY_NAMES = T2_ALL_CATEGORY_NAMES + (
    "OST_Dimensions", "OST_SpotElevations", "OST_SpotCoordinates", "OST_SpotSlopes",
    "OST_MultiReferenceAnnotations", "OST_RevisionCloudTags")
DELETE_CLASS_NAMES = ("IndependentTag", "Dimension", "SpotDimension",
                      "MultiReferenceAnnotation", "TextNote", "DetailCurve", "FilledRegion",
                      "RevisionCloud")
# Spot dimension categories: only elevations are re-created (design 2 / K7).
SPOT_ELEVATION_CATEGORY = "OST_SpotElevations"

# Sheet parameters never copied (identity of the sheet).
SHEET_IDENTITY_PARAMS = ("SHEET_NUMBER", "SHEET_NAME", "SHEET_CURRENT_REVISION",
                         "SHEET_CURRENT_REVISION_DATE", "SHEET_CURRENT_REVISION_DESCRIPTION",
                         "SHEET_CURRENT_REVISION_ISSUED", "SHEET_CURRENT_REVISION_ISSUED_BY",
                         "SHEET_CURRENT_REVISION_ISSUED_TO")
STAMP_TEXT = u"T3Lab cloned from %s on %s"

# Unmatched reasons (T3). Shown verbatim in the log.
R_LINKED = u"tag on a linked element is not supported"
R_MULTI_REF = u"tag on several elements is not supported"
R_REBAR_POSITION = u"rebar set position tag is not supported (tags one bar of a set)"
R_SUBELEMENT = u"tag on a face or sub-element is not supported"
R_NOT_MEMBER = u"tagged element is not part of the source assembly"
R_NO_MATCH = u"no matching element in %s"
R_AMBIGUOUS = u"two equally close elements in %s - not guessed"
R_SPOT_KIND = u"%s is not a spot elevation - not re-created"
R_MRA = u"multi-rebar annotation is not supported (API unverified)"
R_DIM_SHAPE = u"%s dimension is not supported (linear only)"
R_DIM_REBAR = u"dimension to rebar is not supported"
R_DIM_NOT_FACE = u"dimension reference is not a face of a host element"
R_FACE_NO_MATCH = u"no matching face on the target element"
R_FACE_AMBIGUOUS = u"two matching faces on the target element - not guessed"
R_EXISTS = u"already exists on the target view"
R_SKIPPED_BY_SETTING = u"skipped by the clone settings"
R_VIEW_DIMS_OFF = u"dimensions off for this view (Clone dimensions unticked)"
R_WELDS = u"weld marks have no Revit equivalent in assembly views"

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
    (x, y, z) feet. Manual views carry ``parent_view_id`` (callouts) and
    ``view_family_type_id``. ``clone_dims`` is the per-view dimension method
    (Tekla K8). ``skip_reason`` set = this view cannot be cloned (it is
    reported, never silently dropped).
    """
    _FIELDS = (("kind", u""), ("orientation", None), ("template_id", -1),
               ("scale", 0), ("detail_level", u""), ("display_style", u""),
               ("name", u""), ("title", u""), ("crop_local", None), ("crop_active", None),
               ("crop_visible", None), ("category_id", -1),
               ("title_block_type_id", -1), ("title_block_name", u""),
               ("sheet_number", u""), ("sheet_name", u""), ("viewports", []),
               ("sheet_params", []), ("src_view_id", -1), ("parent_view_id", -1),
               ("view_family_type_id", -1), ("view_type", u""), ("clone_dims", True),
               ("skip_reason", u""))


class CloneSettings(_Bag):
    """The Clone settings page (design section 4). Every choice is a string."""
    _FIELDS = tuple((row[0], row[3]) for row in SETTINGS_ROWS) + (
        ("existing", EXISTING_SKIP), ("tol_mm", DEFAULT_TOL_MM), ("allow_mirror", True),
        ("sheet_pattern", DEFAULT_SHEET_PATTERN), ("view_pattern", DEFAULT_VIEW_PATTERN))

    def to_dict(self):
        return dict((name, getattr(self, name)) for name, _ in self._FIELDS)

    def wants(self, field):
        """True when the row is set to clone (any non-skip choice)."""
        return getattr(self, field, SKIP) != SKIP

    @property
    def any_annotation(self):
        return any(self.wants(f) for f in ("dimensions", "spot", "tags", "texts", "symbols",
                                            "shapes", "imports", "images", "revisions"))

    @property
    def any_layer(self):
        return self.wants("views") or self.any_annotation


def _row_choices():
    return dict((row[0], row[2]) for row in SETTINGS_ROWS)


def normalize_settings(data):
    """(CloneSettings, [invalid field names]) from any dict; bad values -> defaults."""
    data = dict(data or {})
    settings = CloneSettings()
    invalid = []
    choices = _row_choices()
    for field in SETTINGS_FIELDS:
        if field not in data:
            continue
        value = u"%s" % data.get(field)
        if value in choices[field]:
            setattr(settings, field, value)
        else:
            invalid.append(field)
    if "existing" in data:
        if data["existing"] in EXISTING_CHOICES:
            settings.existing = data["existing"]
        else:
            invalid.append("existing")
    if "tol_mm" in data:
        try:
            tol = float(data["tol_mm"])
        except (TypeError, ValueError):
            tol = -1.0
        if tol > 0:
            settings.tol_mm = tol
        else:
            invalid.append("tol_mm")
    if "allow_mirror" in data:
        value = data["allow_mirror"]
        if isinstance(value, bool):
            settings.allow_mirror = value
        elif u"%s" % value in (u"True", u"False", u"true", u"false", u"1", u"0"):
            settings.allow_mirror = u"%s" % value in (u"True", u"true", u"1")
        else:
            invalid.append("allow_mirror")
    for field, default in (("sheet_pattern", DEFAULT_SHEET_PATTERN),
                           ("view_pattern", DEFAULT_VIEW_PATTERN)):
        if field in data:
            text = (u"%s" % (data[field] or u"")).strip()
            setattr(settings, field, text or default)
    return settings, invalid


def builtin_presets():
    """{name: CloneSettings} - Tekla default, Views only, Annotations only."""
    tekla = CloneSettings()
    views = CloneSettings(manual_views=CLONE, dimensions=SKIP, spot=SKIP, tags=SKIP,
                          texts=SKIP, symbols=SKIP, shapes=SKIP, imports=SKIP, images=SKIP,
                          revisions=SKIP, overrides=SKIP)
    annotations = CloneSettings(views=SKIP, manual_views=SKIP, sheet_params=SKIP,
                                existing=EXISTING_ADD)
    return {PRESET_TEKLA: tekla, PRESET_VIEWS: views, PRESET_ANNOTATIONS: annotations}


def load_presets(path):
    """({name: CloneSettings} user presets, last used name, [warnings]) from a JSON file."""
    presets = {}
    warnings = []
    last = PRESET_TEKLA
    if not path or not os.path.isfile(path):
        return presets, last, warnings
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError) as exc:
        warnings.append(u"Preset file could not be read (%s) - built-in presets only."
                        % short_error(exc))
        return presets, last, warnings
    if not isinstance(data, dict):
        warnings.append(u"Preset file is not a JSON object - built-in presets only.")
        return presets, last, warnings
    for name, raw in (data.get("presets") or {}).items():
        if not isinstance(raw, dict) or not name or name in BUILTIN_PRESET_NAMES:
            continue
        settings, invalid = normalize_settings(raw)
        presets[u"%s" % name] = settings
        if invalid:
            warnings.append(u'Preset "%s" had invalid values - defaults used for %s.'
                            % (name, u", ".join(sorted(invalid))))
    last = u"%s" % (data.get("last") or PRESET_TEKLA)
    return presets, last, warnings


def save_presets(path, presets, last):
    """Write user presets (builtins are never written). Returns an error text or None."""
    payload = {"version": PRESET_VERSION, "last": last,
               "presets": dict((name, s.to_dict()) for name, s in presets.items()
                               if name not in BUILTIN_PRESET_NAMES)}
    try:
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
    except (IOError, OSError, TypeError, ValueError) as exc:
        return short_error(exc)
    return None


def preset_path():
    """Per-user preset file under %APPDATA%\\T3LabAI (core.paths), never in the extension."""
    try:
        from core.paths import user_data_path
        return user_data_path(PRESET_FILE_NAME)
    except Exception:
        return os.path.join(os.path.expanduser("~"), PRESET_FILE_NAME)


class Tally(object):
    """T3 outcome of one view (or of a whole target once merged)."""

    def __init__(self):
        self.tags_ok = 0
        self.tags_unmatched = 0
        self.tags_created = 0          # "create for unmatched" marks
        self.dims_ok = 0
        self.dims_unmatched = 0
        self.spots_ok = 0
        self.spots_unmatched = 0
        self.exists = 0                # dedupe hits (add missing / re-clone)
        self.unmatched = []            # [(src_element_id, reason)]

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

    def spot(self, ok, src_id=None, reason=u""):
        if ok:
            self.spots_ok += 1
        else:
            self.spots_unmatched += 1
            self.unmatched.append((src_id, u"spot elevation: " + reason))

    def other(self, src_id, reason):
        """An annotation that is neither a tag nor a dimension (MRA...)."""
        self.unmatched.append((src_id, reason))

    def merge(self, other):
        for name in ("tags_ok", "tags_unmatched", "tags_created", "dims_ok", "dims_unmatched",
                     "spots_ok", "spots_unmatched", "exists"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.unmatched.extend(other.unmatched)
        return self

    @property
    def tags_total(self):
        return self.tags_ok + self.tags_unmatched

    @property
    def dims_total(self):
        return self.dims_ok + self.dims_unmatched

    @property
    def spots_total(self):
        return self.spots_ok + self.spots_unmatched


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
        self.deleted = 0
        self.extra_members = 0       # target members the source has no counterpart for
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
    ``has_crop``        kept for the spike calibration log.

    Through the centre: (0,0,-1) HorizontalDetail, (0,+1,0) DetailSectionA,
    (-1,0,0) DetailSectionB. On the near face: ElevationTop / Bottom / Front /
    Back / Left / Right by the axis the view looks along. Anything oblique, on
    the far face, or not a section / elevation is None - the view is then a
    MANUAL view (re-created as a section box / callout) or reported. [NV] G14.
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


def view_key(spec, src_mark=u"", dst_mark=u""):
    """What makes two assembly views 'the same view' for add-missing-only.

    Manual views pair by name with the marks swapped so 'C-01 Detail 1' on
    the source pairs with 'C-02 Detail 1' on the target.
    """
    if spec.kind == "sheet":
        return ("sheet",)
    if spec.kind == "single_schedule":
        return (spec.kind, spec.category_id)
    if spec.kind in ("section", "elevation"):
        return ("view", spec.orientation)
    if spec.kind in MANUAL_KINDS:
        name = spec.name or u""
        if src_mark and src_mark in name:
            name = name.replace(src_mark, u"{Mark}")
        elif dst_mark and dst_mark in name:
            name = name.replace(dst_mark, u"{Mark}")
        return ("manual", name)
    return (spec.kind,)


def pair_existing(src_specs, dst_specs, src_mark=u"", dst_mark=u""):
    """{src_view_id: dst_view_id} for source views the target already has.

    Same key (view_key) pairs in order; two source sections of one orientation
    pair with two target ones, a third stays unpaired.
    """
    pool = {}
    for spec in dst_specs or ():
        if spec.skip_reason and spec.kind not in MANUAL_KINDS:
            continue
        pool.setdefault(view_key(spec, src_mark, dst_mark), []).append(spec.src_view_id)
    out = {}
    for spec in src_specs or ():
        bucket = pool.get(view_key(spec, src_mark, dst_mark))
        if bucket:
            out[spec.src_view_id] = bucket.pop(0)
    return out


class TargetPlan(object):
    """What T1 does for one target under one existing-drawing policy.

    ``to_create``   [ViewSpec] created on the target;
    ``paired``      {src_view_id: existing dst_view_id} that receive annotations;
    ``skipped``     [(ViewSpec, reason)] per view (reported, never dropped);
    ``skip_reason`` set = the whole target is skipped (reported once).
    """

    def __init__(self):
        self.to_create = []
        self.paired = {}
        self.skipped = []
        self.skip_reason = u""


def plan_for_target(src_specs, dst_specs, settings, src_mark=u"", dst_mark=u""):
    """TargetPlan for one target (design section 5).

    * source views that cannot be cloned keep their own ``skip_reason``;
    * ``manual_views = skip`` turns manual views into skipped rows;
    * target without views: everything is created;
    * EXISTING_SKIP with views on the target: the target is skipped ('has drawing');
    * EXISTING_ADD / EXISTING_REPLACE: paired views are reused, the rest created;
    * EXISTING_NEW: everything is created next to the existing views.
    With ``views = skip`` nothing is created; annotations go to paired views.
    """
    plan = TargetPlan()
    has_drawing = bool(dst_specs)
    policy = settings.existing
    manual_on = settings.wants("manual_views")
    create_views = settings.wants("views")
    if has_drawing and policy == EXISTING_SKIP and create_views:
        plan.skip_reason = u"has drawing"
        return plan
    paired = {}
    if (has_drawing and policy in (EXISTING_ADD, EXISTING_REPLACE)) or not create_views:
        paired = pair_existing(src_specs, dst_specs, src_mark, dst_mark)
    for spec in src_specs or ():
        if spec.skip_reason:
            plan.skipped.append((spec, spec.skip_reason))
        elif spec.kind in MANUAL_KINDS and not manual_on:
            plan.skipped.append((spec, R_SKIPPED_BY_SETTING))
        elif spec.src_view_id in paired:
            plan.paired[spec.src_view_id] = paired[spec.src_view_id]
            plan.skipped.append((spec, u"exists"))
        elif not create_views:
            plan.skipped.append((spec, u"views are not created (Views and sheet = Skip)"))
        else:
            plan.to_create.append(spec)
    if not plan.to_create and not plan.paired:
        if not [s for s in src_specs or () if not s.skip_reason]:
            plan.skip_reason = u"no view of the source can be cloned"
        elif not create_views:
            plan.skip_reason = u"no target views to annotate (Views and sheet = Skip)"
        else:
            plan.skip_reason = u"nothing missing"
    return plan


def creation_order(specs):
    """Views, then manual views (need their parent), then schedules, then sheets."""
    rank = {"3d": 0, "section": 1, "elevation": 1, "manual_section": 2, "manual_callout": 2,
            "partlist": 3, "takeoff": 3, "single_schedule": 3, "sheet": 4}
    return sorted(specs, key=lambda s: rank.get(s.kind, 5))


def _plural(n, word, many=None):
    return u"%d %s" % (n, word if n == 1 else (many or word + u"s"))


def summarize_specs(specs):
    """'3D · 2 elevations · 2 sections · 1 callout · Part list · Sheet S-01 (A1)'."""
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
                            ("manual_section", u"manual section", u"manual sections"),
                            ("manual_callout", u"callout", u"callouts"),
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


def kind_label(spec):
    """Short column text for a source view row."""
    labels = {"3d": u"3D", "section": u"Section", "elevation": u"Elevation",
              "manual_section": u"Manual section", "manual_callout": u"Callout",
              "partlist": u"Part list", "takeoff": u"Takeoff",
              "single_schedule": u"Schedule", "sheet": u"Sheet"}
    text = labels.get(spec.kind, spec.kind or u"view")
    if spec.orientation:
        text += u" · " + spec.orientation
    return text


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


def target_status(is_source_type, same_category, has_drawing, policy,
                  src_category=u"", dst_category=u""):
    """(StatusText, Severity, clonable) for a target row before running."""
    if is_source_type and SAME_TYPE_POLICY == "skip":
        return u"Will skip: same type", "Warning", False
    if not same_category:
        return u"Will skip: different category", "Warning", False
    if has_drawing:
        if policy == EXISTING_SKIP:
            return u"Will skip: has drawing", "Warning", False
        if policy == EXISTING_ADD:
            return u"Has drawing - add missing", "Warning", True
        if policy == EXISTING_NEW:
            return u"Has drawing - new set", "Warning", True
        return u"Has drawing - replace annotations", "Danger", True
    return u"Ready", "Success", True


def near_key(category, type_id, local_mm, tol_mm=DEFAULT_TOL_MM):
    """Dedupe key of a free annotation: (category, type, position cell)."""
    tol = float(tol_mm) if tol_mm else DEFAULT_TOL_MM
    return (category, type_id, tuple(int(math.floor(float(v) / tol + 0.5)) for v in local_mm))


def mean_offset(offsets):
    """Mean of (x, y, z) tuples, or None when empty - tag head offsets per category."""
    offsets = [o for o in offsets or () if o is not None]
    if not offsets:
        return None
    n = float(len(offsets))
    return tuple(sum(float(o[k]) for o in offsets) / n for k in range(3))


def deletable_categories():
    """Category names Replace-annotations may delete; never a view-machinery one."""
    return tuple(name for name in DELETE_CATEGORY_NAMES if name not in T2_IGNORED_CATEGORY_NAMES)


def t2_categories(settings):
    """Category names T2 copies under these settings, and the rows that are off."""
    on = []
    off = []
    for field, names in T2_ROW_CATEGORIES:
        if settings.wants(field):
            on.extend(names)
        else:
            off.extend(names)
    return tuple(on), tuple(off)


def confirm_text(src_mark, specs, targets, settings, delete_count=0):
    """(message, details, ok_text) for the P5 confirmation."""
    clonable = [s for s in specs if not s.skip_reason]
    n_views = len([s for s in clonable if s.kind != "sheet"]) * targets
    n_sheets = len([s for s in clonable if s.kind == "sheet"]) * targets
    target_text = _plural(targets, u"assembly", u"assemblies")
    head = u"Clone the drawing of %s to %s?" % (src_mark, target_text)
    if settings.wants("views"):
        body = u"This creates %s%s and %s." % (
            u"up to " if settings.existing != EXISTING_NEW else u"",
            _plural(n_views, u"view"), _plural(n_sheets, u"sheet"))
    else:
        body = u"Views are not created; annotations go onto the views the targets already have."
    rows = [row[1] for row in SETTINGS_ROWS if settings.wants(row[0]) and row[0] != "crop"]
    details = u"Cloned: %s.\nExisting drawings: %s." % (
        u", ".join(rows) if rows else u"nothing", EXISTING_LABELS.get(settings.existing, u""))
    if settings.existing == EXISTING_REPLACE:
        details += (u"\n%s on the target views will be DELETED before cloning. "
                    u"Views, sheets and model elements are never deleted."
                    % _plural(delete_count, u"annotation element"))
    else:
        details += u"\nNothing is deleted."
    details += u" The whole run is one undo step (Ctrl+Z)."
    return head + u" " + body, details, u"Clone to %s" % target_text


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


def _box_center(element, view=None):
    from Autodesk.Revit.DB import XYZ
    box = element.get_BoundingBox(view)
    if box is None and view is not None:
        box = element.get_BoundingBox(None)
    if box is None:
        return None
    return XYZ((box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0,
               (box.Min.Z + box.Max.Z) / 2.0)


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


def _view_owned(doc, view):
    """Non-type elements owned by `view` (OwnerViewId == view.Id)."""
    from Autodesk.Revit.DB import FilteredElementCollector
    view_int = eid_value(view.Id)
    with disposing(FilteredElementCollector(doc, view.Id)) as collector:
        elements = list(collector.WhereElementIsNotElementType().ToElements())
    out = []
    for element in elements:
        try:
            if eid_value(element.OwnerViewId) == view_int:
                out.append(element)
        except Exception:
            continue
    return out


def _location_point(element):
    """A representative model point of an annotation element, or None."""
    try:
        location = element.Location
        point = getattr(location, "Point", None)
        if point is not None:
            return point
        curve = getattr(location, "Curve", None)
        if curve is not None:
            return curve.Evaluate(0.5, True)
    except Exception:
        pass
    for attr in ("Coord",):
        try:
            value = getattr(element, attr)
            if value is not None:
                return value
        except Exception:
            continue
    try:
        return _box_center(element)
    except Exception:
        return None


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


def _callout_parent(view):
    """Parent view id of a callout, or -1. [NV] G17: View.GetCalloutParentId (2016+)."""
    getter = getattr(view, "GetCalloutParentId", None)
    if getter is None:
        return -1
    try:
        return eid_value(getter())
    except Exception:
        return -1


def _view_title(view):
    """'Title on Sheet' (VIEW_DESCRIPTION) or ''."""
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        parameter = view.get_Parameter(BuiltInParameter.VIEW_DESCRIPTION)
        return parameter.AsString() or u"" if parameter is not None else u""
    except Exception:
        return u""


def _view_spec(doc, view, assembly, inverse, src_mark=u""):
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
    try:
        spec.view_family_type_id = eid_value(view.GetTypeId())
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
    spec.title = _view_title(view)

    view_type = _enum_name(getattr(view, "ViewType", None))
    spec.view_type = view_type
    if view_type == "ThreeD":
        spec.kind = "3d"
        return spec
    if view_type not in ("Section", "Elevation", "Detail"):
        spec.kind = view_type.lower() or u"view"
        spec.skip_reason = u"%s views are not an assembly view kind Clone Drawing can create" % (
            view_type or u"this")
        return spec
    parent = _callout_parent(view)
    if parent > 0:
        spec.kind = "manual_callout"
        spec.parent_view_id = parent
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
        spec.kind = "manual_section"
        if spec.crop_local is None:
            spec.skip_reason = (u"orientation unknown and no crop box - cannot be re-created "
                                u"as a section box")
        return spec
    spec.orientation = orientation
    spec.kind = kind_of_orientation(orientation)
    return spec


def _sheet_params(sheet):
    """[(name, value)] of user-modifiable text parameters (Tekla UDAs); identity excluded."""
    out = []
    try:
        from Autodesk.Revit.DB import StorageType
        for parameter in sheet.Parameters:
            try:
                if parameter.StorageType != StorageType.String or parameter.IsReadOnly:
                    continue
                if not getattr(parameter, "UserModifiable", True):
                    continue
                definition = parameter.Definition
                built_in = _enum_name(getattr(definition, "BuiltInParameter", None))
                if built_in in SHEET_IDENTITY_PARAMS:
                    continue
                value = parameter.AsString()
                if value:
                    out.append((definition.Name, value))
            except Exception:
                continue
    except Exception:
        pass
    return out


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
    spec.sheet_params = _sheet_params(sheet)
    return spec


def read_source(doc, assembly, views_index=None):
    """[ViewSpec] of every view and sheet owned by `assembly`.

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
    mark = _assembly_mark(assembly)
    specs = []
    for view_id in view_ids:
        view = doc.GetElement(make_eid(view_id))
        if view is None:
            continue
        try:
            specs.append(_view_spec(doc, view, assembly, inverse, mark))
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


# ── SELECTION HELPERS [REVIT] ────────────────────────────────────────────────

_ASSEMBLY_FILTER = None


def assembly_selection_filter():
    """ISelectionFilter that accepts AssemblyInstances only (Pick in model, D8).

    Static namespace is safe here: lib/ modules import once per engine session
    and the class is built lazily, so tests never touch .NET (S15).
    """
    global _ASSEMBLY_FILTER
    if _ASSEMBLY_FILTER is None:
        from Autodesk.Revit.DB import AssemblyInstance
        from Autodesk.Revit.UI.Selection import ISelectionFilter

        class AssemblyFilter(ISelectionFilter):
            __namespace__ = "T3Lab.CloneDrawing"

            def AllowElement(self, element):
                return isinstance(element, AssemblyInstance)

            def AllowReference(self, reference, position):
                return False

        _ASSEMBLY_FILTER = AssemblyFilter
    return _ASSEMBLY_FILTER()


def selected_assembly_ids(doc, uidoc):
    """Assembly ids in the current Revit selection (members resolve to their assembly)."""
    from Autodesk.Revit.DB import AssemblyInstance
    out = []
    seen = set()
    try:
        ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        return out
    for element_id in ids:
        element = doc.GetElement(element_id)
        if element is None:
            continue
        if isinstance(element, AssemblyInstance):
            value = eid_value(element.Id)
        else:
            try:
                value = eid_value(element.AssemblyInstanceId)
            except Exception:
                value = -1
        if value > 0 and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def pick_assembly_ids(doc, uidoc):
    """(ids, error) from uidoc.Selection.PickObjects with the assembly filter. [NV] G24."""
    from Autodesk.Revit.UI.Selection import ObjectType
    try:
        references = uidoc.Selection.PickObjects(ObjectType.Element, assembly_selection_filter(),
                                                 "Select target assemblies, then Finish")
    except Exception as exc:
        name = type(exc).__name__
        if "Cancel" in name:
            return [], None
        return [], short_error(exc)
    out = []
    for reference in references or ():
        value = eid_value(reference.ElementId)
        if value > 0 and value not in out:
            out.append(value)
    return out, None


# ── T0 · REPLACE ANNOTATIONS [REVIT] ─────────────────────────────────────────

def replaceable_ids(doc, assembly, views_index=None):
    """Annotation element ids on the assembly's views that Replace would delete.

    Only the categories / classes this tool creates or copies, owned by a
    non-sheet assembly view. Never a view, sheet, viewport, title block or
    model element (design 5 / [NV] G25). Returns [(view_id, [ids])].
    """
    if views_index is None:
        views_index = collect_assembly_views(doc)
    view_ids, _ = views_index.get(eid_value(assembly.Id), ([], []))
    allowed = _category_ints(deletable_categories())
    ignored = _category_ints(T2_IGNORED_CATEGORY_NAMES)
    tag_cats = _tag_category_ints()
    out = []
    for view_id in view_ids:
        view = doc.GetElement(make_eid(view_id))
        if view is None:
            continue
        ids = []
        try:
            for element in _view_owned(doc, view):
                category = _category_int(element)
                if category is None or category in ignored:
                    continue
                class_name = type(element).__name__
                if category in allowed or category in tag_cats or class_name in DELETE_CLASS_NAMES:
                    ids.append(eid_value(element.Id))
        except Exception:
            continue
        if ids:
            out.append((view_id, ids))
    return out


def _tag_category_ints():
    """Ids of every *Tags category (tags are deletable annotation)."""
    from Autodesk.Revit.DB import BuiltInCategory
    out = set()
    for name in dir(BuiltInCategory):
        if name.startswith("OST_") and name.endswith("Tags"):
            try:
                out.add(int(getattr(BuiltInCategory, name)))
            except Exception:
                continue
    return out


def delete_annotations(doc, dst_asm, plan_ids):
    """T0 - Transaction "Replace annotations on <mark>": deletes `plan_ids`.

    ``plan_ids`` is ``replaceable_ids(...)``. Returns (deleted_count, [notes]).
    """
    from Autodesk.Revit.DB import ElementId, Transaction
    mark = _assembly_mark(dst_asm) or u"assembly"
    notes = []
    deleted = 0
    txn = Transaction(doc, TRANSACTION_PREFIX + u"Replace annotations on %s" % mark)
    with disposing(txn) as t:
        t.Start()
        try:
            for view_id, ids in plan_ids:
                try:
                    removed = doc.Delete(net_list(ElementId, [make_eid(i) for i in ids]))
                    deleted += removed.Count if removed is not None else len(ids)
                except Exception as exc:
                    notes.append(u"view %s: %d annotations not deleted - %s"
                                 % (view_id, len(ids), short_error(exc)))
            t.Commit()
        except Exception:
            _rollback(t)
            raise
    return deleted, notes


# ── T1 · VIEWS AND SHEET [REVIT] ─────────────────────────────────────────────

def _with_template(create, args, template_id, notes):
    """create(*args, template, True); falls back to create(*args) + assignment."""
    if template_id is not None and eid_value(template_id) > 0:
        try:
            return create(*(tuple(args) + (template_id, True)))
        except Exception as exc:
            notes.append(u"template not applied: %s" % short_error(exc))
    return create(*args)


def _crop_box_in(spec, dst_transform):
    from Autodesk.Revit.DB import BoundingBoxXYZ, Transform
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
    return box


def _apply_view_props(view, spec, dst_transform, notes, copy_crop=True):
    """Scale, detail level, display style, crop and title of `spec` onto `view`.

    A property the view template controls refuses the write; that is kept as
    the template wants it and noted, not treated as a failure.
    """
    from Autodesk.Revit.DB import DisplayStyle, ViewDetailLevel
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
    if copy_crop and spec.crop_local is not None and dst_transform is not None:
        def set_crop():
            view.CropBox = _crop_box_in(spec, dst_transform)      # [NV] G8
        attempt(u"crop box", set_crop)
    if spec.crop_active is not None:
        attempt(u"crop on/off", lambda: setattr(view, "CropBoxActive", bool(spec.crop_active)))
    if spec.crop_visible is not None:
        attempt(u"crop visibility", lambda: setattr(view, "CropBoxVisible", bool(spec.crop_visible)))
    if refused:
        notes.append((u"kept from view template: " if templated else u"not copied: ")
                     + u", ".join(refused))


def _set_view_title(view, spec, src_mark, dst_mark, notes):
    if not spec.title:
        return
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        parameter = view.get_Parameter(BuiltInParameter.VIEW_DESCRIPTION)
        if parameter is not None and not parameter.IsReadOnly:
            parameter.Set(substitute_mark(spec.title, src_mark, dst_mark, fallback_suffix=False))
    except Exception as exc:
        notes.append(u"title on sheet not copied: %s" % short_error(exc))


def _stamp_view(view, src_mark):
    """'T3Lab cloned from <mark> on <date>' in the view's Comments. [NV] G23."""
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        parameter = view.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if parameter is not None and not parameter.IsReadOnly:
            parameter.Set(STAMP_TEXT % (src_mark or u"assembly", time.strftime("%Y-%m-%d")))
            return True
    except Exception:
        pass
    return False


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


def _create_manual_view(doc, dst_asm, spec, view_map, src_transform, dst_transform, notes):
    """Manual section (box) or callout (of a cloned parent). [NV] G17.

    The crop box of the source view, expressed in the source frame, is moved
    into the target frame; a callout needs its parent view already cloned.
    """
    from Autodesk.Revit.DB import ElementId, ViewSection
    if spec.crop_local is None:
        raise ValueError(u"no crop box to re-create the view from")
    type_id = make_eid(spec.view_family_type_id) if spec.view_family_type_id > 0 \
        else ElementId.InvalidElementId
    if spec.kind == "manual_callout":
        parent_new = view_map.get(spec.parent_view_id)
        if parent_new is None:
            raise ValueError(u"its parent view was not cloned")
        box = _crop_box_in(spec, dst_transform)
        corners = [box.Transform.OfPoint(_xyz(c)) for c in (spec.crop_local[4], spec.crop_local[5])]
        view = ViewSection.CreateCallout(doc, make_eid(parent_new), type_id, corners[0], corners[1])
    else:
        view = ViewSection.CreateSection(doc, type_id, _crop_box_in(spec, dst_transform))
    if spec.template_id > 0:
        try:
            view.ViewTemplateId = make_eid(spec.template_id)
        except Exception as exc:
            notes.append(u"template not applied: %s" % short_error(exc))
    try:
        owner = eid_value(view.AssociatedAssemblyInstanceId)
        if owner != eid_value(dst_asm.Id):
            notes.append(u"created as an ordinary view (not owned by the assembly)")
    except Exception:
        notes.append(u"ownership by the assembly could not be checked")
    _ = src_transform
    return view


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


def _copy_sheet_params(sheet, spec, src_mark, dst_mark, notes):
    """Tekla UDAs: text parameters of the sheet. [NV] G22."""
    copied = 0
    for name, value in spec.sheet_params:
        try:
            parameter = sheet.LookupParameter(name)
            if parameter is None or parameter.IsReadOnly:
                continue
            parameter.Set(substitute_mark(value, src_mark, dst_mark, fallback_suffix=False))
            copied += 1
        except Exception as exc:
            notes.append(u"sheet parameter %s not copied: %s" % (name, short_error(exc)))
    return copied


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
    if ctx.get("sheet_params"):
        copied = _copy_sheet_params(sheet, spec, ctx["src_mark"], ctx["dst_mark"], notes)
        if copied:
            notes.append(u"%s copied" % _plural(copied, u"sheet parameter"))
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


def create_views(doc, dst_asm, specs, progress=None, src_mark=u"", settings=None,
                 view_map=None, taken=None, src_asm=None):
    """T1 - Transaction "Clone views to <mark>": one SubTransaction per spec.

    Views first, then manual views, then schedules, then the sheet (its
    viewports need the new views; ``view_map`` may already hold target views
    paired with source views). Template, scale, detail level, display style,
    crop (per the crop setting) and title are copied where the template
    allows; names follow the view pattern with the source mark swapped for the
    target's. Returns ``({src_view_id: new_view_id}, [Result], new_sheet_id_or_-1)``.
    """
    from Autodesk.Revit.DB import SubTransaction, Transaction
    settings = settings or CloneSettings()
    dst_mark = _assembly_mark(dst_asm)
    view_map = dict(view_map or {})
    names, numbers = taken if taken is not None else _taken_names(doc)
    ctx = {"src_mark": src_mark, "dst_mark": dst_mark, "sheet_numbers": numbers,
           "sheet_pattern": settings.sheet_pattern,
           "sheet_params": settings.wants("sheet_params")}
    try:
        dst_transform = dst_asm.GetTransform()
    except Exception:
        dst_transform = None
    try:
        src_transform = src_asm.GetTransform() if src_asm is not None else None
    except Exception:
        src_transform = None
    results = []
    created = {}
    sheet_id = -1
    ordered = creation_order(specs)
    total = len(ordered)
    stamp_failed = [False]

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
                            if spec.kind in MANUAL_KINDS:
                                view = _create_manual_view(doc, dst_asm, spec, view_map,
                                                           src_transform, dst_transform, notes)
                            else:
                                view = _create_view(doc, dst_asm, spec, notes)
                            if spec.kind not in SCHEDULE_KINDS:
                                _apply_view_props(view, spec, dst_transform, notes,
                                                  copy_crop=settings.crop == CROP_SOURCE)
                                _set_view_title(view, spec, src_mark, dst_mark, notes)
                            if not _stamp_view(view, src_mark):
                                stamp_failed[0] = True
                            name = unique_name(view_name_for(spec, src_mark, dst_mark,
                                                             settings.view_pattern), names)
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
                    except ValueError as exc:
                        # a planning gap (parent not cloned, no crop box): reported, not a failure
                        _rollback(st)
                        results.append(Result(label, STATUS_SKIPPED, 0, short_error(exc)))
                    except Exception as exc:
                        _rollback(st)
                        results.append(Result(label, STATUS_FAILED, 0, short_error(exc)))
            t.Commit()
        except Exception:
            _rollback(t)
            raise
    if stamp_failed[0] and results:
        results.append(Result(u"cloned-from stamp", STATUS_SKIPPED, 0,
                              u"Comments of the new views could not be written"))
    return created, results, sheet_id


# ── T2 · FREE ANNOTATIONS [REVIT] ────────────────────────────────────────────

def annotation_split(doc, src_view, settings=None):
    """(ids to copy, [(id, reason)] not copied) of the view-owned elements.

    Tags, dimensions and spot dimensions are T3's job and are left out of
    both lists; rows switched off in the settings are listed with the reason.
    """
    from Autodesk.Revit.DB import Dimension, IndependentTag
    settings = settings or CloneSettings()
    on_names, off_names = t2_categories(settings)
    copy_cats = _category_ints(on_names)
    off_cats = _category_ints(off_names)
    ignore_cats = _category_ints(T2_IGNORED_CATEGORY_NAMES)
    copy_ids = []
    left_out = []
    for element in _view_owned(doc, src_view):
        try:
            if isinstance(element, (IndependentTag, Dimension)):
                continue
            if type(element).__name__ == "MultiReferenceAnnotation":
                continue
            category = _category_int(element)
            if category is None or category in ignore_cats:
                continue
            if category in copy_cats:
                copy_ids.append(eid_value(element.Id))
            elif category in off_cats:
                left_out.append((eid_value(element.Id),
                                 u"%s: %s" % (_category_label(element), R_SKIPPED_BY_SETTING)))
            elif "Weld" in _category_label(element):
                left_out.append((eid_value(element.Id), R_WELDS))
            else:
                left_out.append((eid_value(element.Id),
                                 u"%s is not an annotation kind Clone Drawing copies"
                                 % _category_label(element)))
        except Exception:
            continue
    return copy_ids, left_out


def _existing_keys(doc, dst_view, inverse, tol_mm):
    """Dedupe keys of the free annotations already on the target view."""
    keys = set()
    for element in _view_owned(doc, dst_view):
        try:
            point = _location_point(element)
            if point is None:
                continue
            local = inverse.OfPoint(point)
            keys.add(near_key(_category_int(element), eid_value(element.GetTypeId()),
                              (to_mm(local.X), to_mm(local.Y), to_mm(local.Z)), tol_mm))
        except Exception:
            continue
    return keys


def _drop_existing(doc, ids, src_inverse, existing_keys, tol_mm):
    """Split `ids` into (to copy, [(id, reason)] that already exist on the target)."""
    keep = []
    exists = []
    for element_id in ids:
        element = doc.GetElement(make_eid(element_id))
        try:
            point = _location_point(element)
            local = src_inverse.OfPoint(point)
            key = near_key(_category_int(element), eid_value(element.GetTypeId()),
                           (to_mm(local.X), to_mm(local.Y), to_mm(local.Z)), tol_mm)
        except Exception:
            key = None
        if key is not None and key in existing_keys:
            exists.append((element_id, u"%s: %s" % (_category_label(element), R_EXISTS)))
        else:
            keep.append(element_id)
    return keep, exists


def _copy_overrides(src_view, dst_view, src_ids, new_ids, notes):
    """Per-element graphic overrides onto the copies (ids pair in order). [NV] G21."""
    applied = 0
    for src_id, new_id in zip(src_ids, list(new_ids)):
        try:
            overrides = src_view.GetElementOverrides(make_eid(src_id))
            dst_view.SetElementOverrides(new_id, overrides)
            applied += 1
        except Exception as exc:
            notes.append(u"overrides not applied: %s" % short_error(exc))
            break
    return applied


def copy_annotations(doc, src_view, dst_view, transform, settings=None, progress=None,
                     src_inverse=None, dst_inverse=None, dedupe=False):
    """T2 for one view: a SubTransaction around ElementTransformUtils.CopyElements.

    Run inside the caller's Transaction "Copy annotations to <mark>". A view
    whose copy Revit refuses is rolled back alone and every element of it is
    reported. With ``dedupe`` (add missing / re-clone) elements that already
    sit on the target are skipped as 'exists'. Returns
    ``(copied_count, Result, [(src_id, reason)], exists_count)``.
    """
    from Autodesk.Revit.DB import (CopyPasteOptions, ElementId, ElementTransformUtils,
                                   SubTransaction)
    settings = settings or CloneSettings()
    label = src_view.Name
    if progress is not None and progress(1, 1, label) is False:
        return 0, Result(label, STATUS_SKIPPED, 0, SKIP_STOPPED), [], 0
    ids, left_out = annotation_split(doc, src_view, settings)
    exists = []
    if dedupe and ids and src_inverse is not None and dst_inverse is not None:
        try:
            existing = _existing_keys(doc, dst_view, dst_inverse, settings.tol_mm)
            ids, exists = _drop_existing(doc, ids, src_inverse, existing, settings.tol_mm)
        except Exception as exc:
            left_out.append((-1, u"dedupe failed, nothing copied twice on purpose: %s"
                             % short_error(exc)))
            ids = []
    if not ids:
        detail = u"no free annotations" if not exists else u"%d already exist" % len(exists)
        return 0, Result(label, STATUS_SKIPPED, 0, detail), left_out + exists, len(exists)
    notes = []
    sub = SubTransaction(doc)
    with disposing(sub) as st:
        st.Start()
        try:
            copied = ElementTransformUtils.CopyElements(
                src_view, net_list(ElementId, [make_eid(i) for i in ids]), dst_view,
                transform, CopyPasteOptions())
            if settings.wants("overrides") and copied is not None:
                _copy_overrides(src_view, dst_view, ids, copied, notes)
            st.Commit()
        except Exception as exc:
            _rollback(st)
            reason = u"copy refused by Revit: %s" % short_error(exc)
            return 0, Result(label, STATUS_SKIPPED, 0, reason), \
                left_out + exists + [(i, reason) for i in ids], len(exists)
    count = copied.Count if copied is not None else 0
    detail = u"%d copied" % count
    if exists:
        detail += u", %d already existed" % len(exists)
    if notes:
        detail += u"; " + u"; ".join(sorted(set(notes)))
    return count, Result(label, STATUS_OK, count, detail), left_out + exists, len(exists)


def t2_transform(src_asm, dst_asm, is_sheet):
    """Transform handed to CopyElements for one view pair (T2_TRANSFORM_MODE)."""
    from Autodesk.Revit.DB import Transform
    if is_sheet or T2_TRANSFORM_MODE != "delta":
        return Transform.Identity          # sheets are paper space
    return transform_between(src_asm, dst_asm)


def copy_member_overrides(src_view, dst_view, pairs, notes):
    """Graphic overrides of matched MEMBERS from the source view to the target view."""
    applied = 0
    for src_id, dst_id in pairs.items():
        try:
            overrides = src_view.GetElementOverrides(make_eid(src_id))
            dst_view.SetElementOverrides(make_eid(dst_id), overrides)
            applied += 1
        except Exception as exc:
            notes.append(u"member overrides not applied: %s" % short_error(exc))
            break
    return applied


# ── T3 · TAGS, DIMENSIONS, SPOT ELEVATIONS [REVIT] ───────────────────────────

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


class MemberMatch(object):
    """Outcome of member pairing between two assemblies.

    ``pairs`` {src_id: dst_id}; ``reasons`` {src_id: reason} for unmatched or
    ambiguous source members; ``extra`` [dst_id] target members without a
    source counterpart (Tekla: "parts that cannot be mapped" - they get a
    created mark with ``tags = clone_create`` and are listed in the report).
    """

    def __init__(self):
        self.pairs = {}
        self.reasons = {}
        self.extra = []


def match_members(doc, src_asm, dst_asm, tol_mm=DEFAULT_TOL_MM, allow_mirror=True):
    """MemberMatch between the members of two assemblies.

    Uses ``_rebar.match_by_fingerprint`` on (kind, shape, type, quantity) +
    the centre in each assembly's local frame. The assemblies themselves pair
    too, so a tag on the assembly instance is re-created on the target.
    """
    dst_mark = _assembly_mark(dst_asm) or u"the target"
    src_items = _member_items(doc, src_asm)
    dst_items = _member_items(doc, dst_asm)
    pairs, unmatched, ambiguous = match_by_fingerprint(src_items, dst_items, tol_mm=tol_mm,
                                                       allow_mirror=allow_mirror)
    match = MemberMatch()
    match.pairs[eid_value(src_asm.Id)] = eid_value(dst_asm.Id)
    used = set()
    for src_item, dst_item in pairs:
        match.pairs[src_item[0].id] = dst_item[0].id
        used.add(dst_item[0].id)
    for item in unmatched:
        match.reasons[item[0].id] = R_NO_MATCH % dst_mark
    for item in ambiguous:
        match.reasons[item[0].id] = R_AMBIGUOUS % dst_mark
    match.extra = [item[0].id for item in dst_items if item[0].id not in used]
    return match


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
    """(dst element id, src element id, None) or (None, src id, reason) for one tag."""
    references = _tagged_references(tag)
    if not references:
        return None, -1, R_SUBELEMENT
    if len(references) > 1:
        return None, -1, R_MULTI_REF
    reference = references[0]
    if _is_linked(reference):
        return None, -1, R_LINKED
    src_id = eid_value(reference.ElementId)
    element = doc.GetElement(reference.ElementId)
    if not _reference_is_whole_element(reference):
        return None, src_id, R_REBAR_POSITION if element is not None and _is_rebar_like(element) \
            else R_SUBELEMENT
    if src_id in pairs:
        return pairs[src_id], src_id, None
    return None, src_id, reasons.get(src_id, R_NOT_MEMBER)


def _existing_tags(doc, dst_view):
    """{(tag type id, tagged element id)} already on the target view."""
    from Autodesk.Revit.DB import IndependentTag
    out = set()
    for element in _view_owned(doc, dst_view):
        if not isinstance(element, IndependentTag):
            continue
        try:
            type_id = eid_value(element.GetTypeId())
            for reference in _tagged_references(element):
                out.add((type_id, eid_value(reference.ElementId)))
        except Exception:
            continue
    return out


def _existing_dimension_keys(doc, dst_view):
    """{frozenset(stable reference strings)} of dimensions already on the target view."""
    from Autodesk.Revit.DB import Dimension
    out = set()
    for element in _view_owned(doc, dst_view):
        if not isinstance(element, Dimension):
            continue
        try:
            reps = [_stable(doc, r) for r in element.References]
            if reps and all(reps):
                out.add(frozenset(reps))
        except Exception:
            continue
    return out


def _recreate_tag(doc, tag, dst_view, dst_id, transform):
    """[NV] G19: IndependentTag.Create with Reference(element) for every whole-element tag."""
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


def _create_tag(doc, type_id, dst_view, dst_el, head, has_leader, orientation):
    from Autodesk.Revit.DB import IndependentTag, Reference
    return IndependentTag.Create(doc, make_eid(type_id), dst_view.Id, Reference(dst_el),
                                 bool(has_leader), orientation, head)


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
    center = _box_center(element)
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


class _FaceMatcher(object):
    """Face pairing shared by dimensions and spot elevations (one cache per view)."""

    def __init__(self, doc, src_asm, dst_asm, pairs, reasons, mark, allow_mirror):
        self.doc = doc
        self.pairs = pairs
        self.reasons = reasons
        self.mark = mark
        self.allow_mirror = allow_mirror
        self.src_inverse = src_asm.GetTransform().Inverse
        self.dst_inverse = dst_asm.GetTransform().Inverse
        self.cache = {}

    def _faces(self, key, element, inverse):
        if key not in self.cache:
            faces = _planar_faces(element)
            self.cache[key] = (faces, _face_keys(element, faces, inverse))
        return self.cache[key]

    def resolve(self, reference):
        """(dst Reference, None) or (None, reason) for one source face reference."""
        doc = self.doc
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
        if src_id not in self.pairs:
            return None, self.reasons.get(src_id, R_NOT_MEMBER)
        dst_el = doc.GetElement(make_eid(self.pairs[src_id]))
        if dst_el is None:
            return None, R_NO_MATCH % self.mark
        src_faces, src_keys = self._faces(("src", src_id), element, self.src_inverse)
        wanted = _stable(doc, reference)
        index = None
        for i, (face_ref, _, _) in enumerate(src_faces):
            if wanted is not None and _stable(doc, face_ref) == wanted:
                index = i
                break
        if index is None:
            return None, R_DIM_NOT_FACE
        dst_faces, dst_keys = self._faces(("dst", eid_value(dst_el.Id)), dst_el, self.dst_inverse)
        hit, reason = match_face(src_keys[index], dst_keys, allow_mirror=self.allow_mirror)
        if hit is None:
            return None, reason
        return dst_faces[hit][0], None


def _dimension_refs(doc, dim, matcher):
    """ReferenceArray for the target, or (None, reason)."""
    from Autodesk.Revit.DB import ReferenceArray
    out = ReferenceArray()
    for reference in dim.References:
        target, reason = matcher.resolve(reference)
        if target is None:
            return None, reason
        out.Append(target)
    return out, None


def _recreate_dimension(doc, dim, dst_view, refs, transform):
    from Autodesk.Revit.DB import Line
    curve = dim.Curve
    origin = transform.OfPoint(curve.Origin)
    direction = transform.OfVector(curve.Direction)
    line = Line.CreateBound(origin, origin.Add(direction))
    return doc.Create.NewDimension(dst_view, line, refs, dim.DimensionType)


def _spot_kind(spot):
    """'OST_SpotElevations' / 'OST_SpotCoordinates' / 'OST_SpotSlopes' or the label."""
    from Autodesk.Revit.DB import BuiltInCategory
    category = _category_int(spot)
    for name in ("OST_SpotElevations", "OST_SpotCoordinates", "OST_SpotSlopes"):
        member = getattr(BuiltInCategory, name, None)
        try:
            if member is not None and int(member) == category:
                return name
        except Exception:
            continue
    return _category_label(spot)


def _recreate_spot(doc, spot, dst_view, reference, transform):
    """[NV] G18: doc.Create.NewSpotElevation on a matched host face."""
    origin = transform.OfPoint(spot.Origin)
    has_leader = bool(getattr(spot, "HasLeader", False))
    end = origin
    bend = origin
    if has_leader:
        try:
            end = transform.OfPoint(spot.LeaderEndPosition)
        except Exception:
            end = origin
        bend = end
        try:
            if getattr(spot, "LeaderHasShoulder", False):
                bend = transform.OfPoint(spot.LeaderShoulderPosition)
        except Exception:
            bend = end
    new_spot = doc.Create.NewSpotElevation(dst_view, reference, origin, bend, end, origin,
                                           has_leader)
    try:
        new_spot.SpotDimensionType = spot.SpotDimensionType
    except Exception:
        pass
    return new_spot


def _tag_profile(doc, tags, pairs, src_inverse):
    """{category int: (type id, has_leader, orientation, mean head offset local)}.

    Learned from the source tags that tag whole members, used by
    'create for unmatched' to mark extra target members like the source did.
    """
    buckets = {}
    for tag in tags:
        references = _tagged_references(tag)
        if len(references) != 1 or not _reference_is_whole_element(references[0]):
            continue
        element = doc.GetElement(references[0].ElementId)
        if element is None or eid_value(element.Id) not in pairs:
            continue
        category = _category_int(element)
        if category is None:
            continue
        try:
            center = _box_center(element)
            offset = _tup(src_inverse.OfVector(tag.TagHeadPosition.Subtract(center)))
        except Exception:
            offset = None
        bucket = buckets.setdefault(category, {"types": {}, "leader": 0, "n": 0,
                                               "orientation": None, "offsets": []})
        type_id = eid_value(tag.GetTypeId())
        bucket["types"][type_id] = bucket["types"].get(type_id, 0) + 1
        bucket["n"] += 1
        bucket["leader"] += 1 if bool(getattr(tag, "HasLeader", False)) else 0
        if bucket["orientation"] is None:
            bucket["orientation"] = tag.TagOrientation
        bucket["offsets"].append(offset)
    profile = {}
    for category, bucket in buckets.items():
        type_id = sorted(bucket["types"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        profile[category] = (type_id, bucket["leader"] * 2 >= bucket["n"],
                             bucket["orientation"], mean_offset(bucket["offsets"]))
    return profile


def recreate_references(doc, src_view, dst_view, match, src_asm, dst_asm, settings=None,
                        progress=None, clone_dims=True):
    """T3 for one view pair. Run inside the caller's Transaction.

    Tags: each IndependentTag on ONE whole element that has a match is
    re-created on the matched element (type, leader, orientation, head moved
    by ``transform_between``); with ``tags = clone_create`` every extra target
    member of a category the source tagged gets a new tag of the same type.
    Dimensions: each linear dimension whose every reference is a FACE of a
    matched host is re-created between the faces matched by (local normal,
    distance from centre) within 5 mm. Spot elevations on matched faces are
    re-created. Multi-rebar annotations, dimensions to rebar, rebar set
    position tags and anything unmatched are listed in ``Tally.unmatched``
    with the reason. Already-existing tags / dimensions are 'exists'. Every
    element gets its own SubTransaction.
    """
    from Autodesk.Revit.DB import Dimension, IndependentTag, SpotDimension, SubTransaction
    settings = settings or CloneSettings()
    pairs, reasons = match.pairs, match.reasons
    tally = Tally()
    mark = _assembly_mark(dst_asm) or u"the target"
    transform = transform_between(src_asm, dst_asm)
    elements = _view_owned(doc, src_view)

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
    spots = [e for e in elements if isinstance(e, SpotDimension)]
    dims = [e for e in elements if isinstance(e, Dimension) and not isinstance(e, SpotDimension)
            and eid_value(e.Id) not in owned_by_mra]
    total = len(tags) + len(dims) + len(spots)
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

    # ── tags ────────────────────────────────────────────────────────────────
    existing_tags = _existing_tags(doc, dst_view) if tags or match.extra else set()
    tagged_dst = set()
    if settings.wants("tags"):
        for tag in tags:
            tag_id = eid_value(tag.Id)
            if not advance(u"tag %d" % tag_id):
                tally.tag(False, tag_id, SKIP_STOPPED)
                continue
            dst_id, _, reason = _tag_target(doc, tag, pairs, reasons)
            if dst_id is None:
                tally.tag(False, tag_id, reason)
                continue
            key = (eid_value(tag.GetTypeId()), dst_id)
            if key in existing_tags:
                tally.exists += 1
                tally.tags_ok += 1
                tagged_dst.add(dst_id)
                continue
            error = isolated(lambda: _recreate_tag(doc, tag, dst_view, dst_id, transform))
            tally.tag(error is None, tag_id, u"Revit refused the tag: %s" % error if error else u"")
            if error is None:
                tagged_dst.add(dst_id)
                existing_tags.add(key)
    else:
        for tag in tags:
            tally.tag(False, eid_value(tag.Id), R_SKIPPED_BY_SETTING)

    if settings.tags == CLONE_CREATE and match.extra and tags:
        try:
            profile = _tag_profile(doc, tags, pairs, src_asm.GetTransform().Inverse)
            dst_transform = dst_asm.GetTransform()
        except Exception as exc:
            profile, dst_transform = {}, None
            tally.other(-1, u"create for unmatched: tag profile not read - %s" % short_error(exc))
        for dst_id in match.extra:
            dst_el = doc.GetElement(make_eid(dst_id))
            if dst_el is None:
                continue
            spec = profile.get(_category_int(dst_el))
            if spec is None or dst_transform is None:
                continue
            type_id, has_leader, orientation, offset = spec
            if (type_id, dst_id) in existing_tags:
                tally.exists += 1
                continue
            try:
                center = _box_center(dst_el, dst_view) or _box_center(dst_el)
                head = center.Add(dst_transform.OfVector(_xyz(offset))) if offset else center
            except Exception:
                continue
            error = isolated(lambda: _create_tag(doc, type_id, dst_view, dst_el, head,
                                                 has_leader, orientation))
            if error is None:
                tally.tags_created += 1
                existing_tags.add((type_id, dst_id))
            else:
                tally.other(dst_id, u"created tag refused by Revit on target element %d: %s"
                            % (dst_id, error))

    # ── dimensions & spot elevations ────────────────────────────────────────
    matcher = None
    if (dims and settings.wants("dimensions") and clone_dims) or (spots and settings.wants("spot")):
        try:
            matcher = _FaceMatcher(doc, src_asm, dst_asm, pairs, reasons, mark,
                                   settings.allow_mirror)
        except Exception as exc:
            tally.other(-1, u"face matching unavailable: %s" % short_error(exc))
    existing_dims = _existing_dimension_keys(doc, dst_view) if dims and matcher else set()

    for dim in dims:
        dim_id = eid_value(dim.Id)
        if not advance(u"dimension %d" % dim_id):
            tally.dim(False, dim_id, SKIP_STOPPED)
            continue
        if not settings.wants("dimensions"):
            tally.dim(False, dim_id, R_SKIPPED_BY_SETTING)
            continue
        if not clone_dims:
            tally.dim(False, dim_id, R_VIEW_DIMS_OFF)
            continue
        if matcher is None:
            tally.dim(False, dim_id, u"face matching unavailable")
            continue
        shape = _enum_name(getattr(dim, "DimensionShape", None))
        if shape and shape != "Linear":
            tally.dim(False, dim_id, R_DIM_SHAPE % shape.lower())
            continue
        try:
            refs, reason = _dimension_refs(doc, dim, matcher)
        except Exception as exc:
            refs, reason = None, u"could not read its references: %s" % short_error(exc)
        if refs is None:
            tally.dim(False, dim_id, reason)
            continue
        try:
            key = frozenset(_stable(doc, refs.get_Item(i)) for i in range(refs.Size))
        except Exception:
            key = None
        if key is not None and key in existing_dims:
            tally.exists += 1
            tally.dims_ok += 1
            continue
        error = isolated(lambda: _recreate_dimension(doc, dim, dst_view, refs, transform))
        tally.dim(error is None, dim_id,
                  u"Revit refused the dimension: %s" % error if error else u"")
        if error is None and key is not None:
            existing_dims.add(key)

    for spot in spots:
        spot_id = eid_value(spot.Id)
        if not advance(u"spot %d" % spot_id):
            tally.spot(False, spot_id, SKIP_STOPPED)
            continue
        if not settings.wants("spot"):
            tally.spot(False, spot_id, R_SKIPPED_BY_SETTING)
            continue
        kind = _spot_kind(spot)
        if kind != SPOT_ELEVATION_CATEGORY:
            tally.spot(False, spot_id, R_SPOT_KIND % kind.replace("OST_", "").lower())
            continue
        if matcher is None:
            tally.spot(False, spot_id, u"face matching unavailable")
            continue
        try:
            references = list(spot.References)
        except Exception:
            references = []
        if len(references) != 1:
            tally.spot(False, spot_id, R_DIM_NOT_FACE)
            continue
        target, reason = matcher.resolve(references[0])
        if target is None:
            tally.spot(False, spot_id, reason)
            continue
        error = isolated(lambda: _recreate_spot(doc, spot, dst_view, target, transform))
        tally.spot(error is None, spot_id,
                   u"Revit refused the spot elevation: %s" % error if error else u"")

    _ = tagged_dst
    return tally


# ── ONE TARGET / THE WHOLE RUN [REVIT] ───────────────────────────────────────

def _view_of(doc, view_id):
    return doc.GetElement(make_eid(view_id)) if view_id is not None else None


def _is_same_type(src_asm, dst_asm):
    try:
        return eid_value(src_asm.GetTypeId()) == eid_value(dst_asm.GetTypeId())
    except Exception:
        return False


def _naming_category(doc, assembly):
    try:
        from Autodesk.Revit.DB import Category
        category = Category.GetCategory(doc, assembly.NamingCategoryId)
        return category.Name if category is not None else u""
    except Exception:
        return u""


def _results_to_log(outcome, label, results):
    for row in results:
        level = {STATUS_OK: LEVEL_OK, STATUS_SKIPPED: LEVEL_SKIPPED}.get(row.status, LEVEL_FAILED)
        text = u"%s %s: %s" % (row.status, label, row.name)
        if row.detail:
            text += u" - " + row.detail
        outcome.note(level, text)


def clone_to_target(doc, src_asm, dst_asm, src_specs, settings, views_index, taken,
                    replace_ids=None):
    """T0 -> T1 -> T2 -> T3 for one target (caller holds the group).

    T2 / T3 go to the views T1 created and to the views paired with existing
    ones (add missing / replace / views off). Never deletes anything except
    under the explicit replace policy. Returns a TargetOutcome.
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
    src_category, dst_category = _naming_category(doc, src_asm), _naming_category(doc, dst_asm)
    if src_category != dst_category:
        outcome.status = STATUS_SKIPPED
        outcome.detail = CATEGORY_REASON % (dst_category or u"?", src_category or u"?")
        outcome.note(LEVEL_SKIPPED, u"skipped %s: %s" % (label, outcome.detail))
        return outcome

    dst_specs = read_source(doc, dst_asm, views_index)
    clonable = [s for s in src_specs if not s.skip_reason]
    outcome.views_planned = len([s for s in clonable if s.kind != "sheet"])
    plan = plan_for_target(src_specs, dst_specs, settings, src_mark, dst_mark)
    if plan.skip_reason:
        outcome.status = STATUS_SKIPPED
        outcome.detail = plan.skip_reason
        outcome.note(LEVEL_SKIPPED, u"skipped %s: %s" % (label, plan.skip_reason))
        return outcome
    for spec, reason in plan.skipped:
        if spec.skip_reason:
            outcome.unmatched.append((spec.src_view_id, u"view %s: %s" % (spec.name, reason)))
            outcome.note(LEVEL_SKIPPED, u"unmatched %s: view %s - %s" % (label, spec.name, reason))
        else:
            outcome.note(LEVEL_SKIPPED, u"skipped %s: %s %s - %s" % (
                label, u"sheet" if spec.kind == "sheet" else u"view",
                spec.name or spec.sheet_number, reason))

    # T0 - replace annotations (explicit, confirmed policy only)
    dedupe = settings.existing in (EXISTING_ADD, EXISTING_REPLACE) or not settings.wants("views")
    if settings.existing == EXISTING_REPLACE and dst_specs:
        plan_ids = replace_ids if replace_ids is not None else replaceable_ids(doc, dst_asm, views_index)
        if plan_ids:
            try:
                outcome.deleted, notes = delete_annotations(doc, dst_asm, plan_ids)
                outcome.note(LEVEL_OK, u"ok %s: %s deleted before cloning"
                             % (label, _plural(outcome.deleted, u"annotation element")))
                for note in notes:
                    outcome.note(LEVEL_FAILED, u"failed %s: %s" % (label, note))
            except Exception as exc:
                outcome.note(LEVEL_FAILED, u"failed %s: replace annotations - %s"
                             % (label, short_error(exc)))

    # T1 - views
    targets = dict(plan.paired)
    if plan.to_create:
        try:
            created, results, sheet_id = create_views(
                doc, dst_asm, plan.to_create, src_mark=src_mark, settings=settings,
                view_map=plan.paired, taken=taken, src_asm=src_asm)
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
        _results_to_log(outcome, label, results)
        failed = [r for r in results if r.status == STATUS_FAILED]
        if failed and len(failed) == len([r for r in results if r.name != u"cloned-from stamp"]):
            outcome.status = STATUS_FAILED
            outcome.detail = failed[0].detail
        targets.update(created)
    elif not targets:
        outcome.status = STATUS_SKIPPED
        outcome.detail = u"no target views to annotate"
        outcome.note(LEVEL_SKIPPED, u"skipped %s: %s" % (label, outcome.detail))
        return outcome

    spec_by_id = dict((s.src_view_id, s) for s in src_specs)
    view_pairs = [(src_id, dst_id) for src_id, dst_id in targets.items()
                  if spec_by_id.get(src_id) is not None
                  and spec_by_id[src_id].kind not in SCHEDULE_KINDS]
    model_pairs = [(s, d) for s, d in view_pairs if spec_by_id[s].kind != "sheet"]

    # T3 member matching is needed by T2 (member overrides) too.
    match = None
    needs_match = model_pairs and (settings.wants("tags") or settings.wants("dimensions")
                                   or settings.wants("spot") or settings.wants("overrides"))
    if needs_match:
        try:
            match = match_members(doc, src_asm, dst_asm, settings.tol_mm, settings.allow_mirror)
            outcome.extra_members = len(match.extra)
            for src_id, reason in sorted(match.reasons.items()):
                outcome.note(LEVEL_SKIPPED, u"unmatched %s: member %d - %s" % (label, src_id, reason))
            if match.extra:
                outcome.note(LEVEL_SKIPPED, u"unmatched %s: %s not in the source - %s"
                             % (label, _plural(len(match.extra), u"target member"),
                                u"tagged by 'create for unmatched'" if settings.tags == CLONE_CREATE
                                else u"add their marks and dimensions by hand"))
        except Exception as exc:
            match = MemberMatch()
            outcome.note(LEVEL_FAILED, u"failed %s: element matching - %s" % (label, short_error(exc)))

    # T2 - free annotations
    t2_on = settings.wants("texts") or settings.wants("symbols") or settings.wants("shapes") \
        or settings.wants("imports") or settings.wants("images") or settings.wants("revisions") \
        or settings.wants("overrides")
    if t2_on and view_pairs:
        try:
            src_inverse = src_asm.GetTransform().Inverse
            dst_inverse = dst_asm.GetTransform().Inverse
        except Exception:
            src_inverse = dst_inverse = None
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
                        count, row, left_out, exists = copy_annotations(
                            doc, src_view, dst_view, t2_transform(src_asm, dst_asm, is_sheet),
                            settings, src_inverse=src_inverse, dst_inverse=dst_inverse,
                            dedupe=dedupe)
                    except Exception as exc:
                        count, row, left_out, exists = 0, Result(src_view.Name, STATUS_FAILED, 0,
                                                                 short_error(exc)), [], 0
                    outcome.copied += count
                    outcome.tally.exists += exists
                    outcome.unmatched.extend([(i, r) for i, r in left_out if R_EXISTS not in r])
                    if row.status != STATUS_SKIPPED or row.detail != u"no free annotations":
                        level = {STATUS_OK: LEVEL_OK}.get(row.status, LEVEL_SKIPPED)
                        outcome.note(level, u"%s %s: annotations of %s - %s"
                                     % (row.status, label, row.name, row.detail))
                    for element_id, reason in left_out:
                        if R_EXISTS in reason:
                            continue
                        outcome.note(LEVEL_SKIPPED, u"unmatched %s: element %s - %s"
                                     % (label, element_id, reason))
                    if settings.wants("overrides") and not is_sheet and match is not None:
                        notes = []
                        copy_member_overrides(src_view, dst_view, match.pairs, notes)
                        for note in notes:
                            outcome.note(LEVEL_SKIPPED, u"skipped %s: %s - %s"
                                         % (label, src_view.Name, note))
                t.Commit()
            except Exception as exc:
                _rollback(t)
                outcome.note(LEVEL_FAILED, u"failed %s: annotations - %s" % (label, short_error(exc)))

    # T3 - tags, dimensions, spot elevations
    t3_on = settings.wants("tags") or settings.wants("dimensions") or settings.wants("spot")
    if t3_on and model_pairs and match is not None:
        txn = Transaction(doc, TRANSACTION_PREFIX + u"Re-create references on %s" % (dst_mark or label))
        with disposing(txn) as t:
            t.Start()
            try:
                for src_id, dst_id in model_pairs:
                    src_view, dst_view = _view_of(doc, src_id), _view_of(doc, dst_id)
                    if src_view is None or dst_view is None:
                        continue
                    try:
                        tally = recreate_references(doc, src_view, dst_view, match, src_asm, dst_asm,
                                                    settings, clone_dims=spec_by_id[src_id].clone_dims)
                    except Exception as exc:
                        outcome.note(LEVEL_FAILED, u"failed %s: tags and dimensions of %s - %s"
                                     % (label, src_view.Name, short_error(exc)))
                        continue
                    outcome.tally.merge(tally)
                    for element_id, reason in tally.unmatched:
                        outcome.unmatched.append((element_id, reason))
                        outcome.note(LEVEL_SKIPPED, u"unmatched %s: element %s in %s - %s"
                                     % (label, element_id, src_view.Name, reason))
                    if tally.tags_created:
                        outcome.note(LEVEL_OK, u"ok %s: %s created for unmatched members in %s"
                                     % (label, _plural(tally.tags_created, u"tag"), src_view.Name))
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
    """'5 views, sheet S-01-C-02 · 12 copied · tags 4 / 5 (+2 created) · dims 2 / 2 · 3 unmatched'."""
    parts = [_plural(outcome.views_created, u"view")]
    if outcome.sheet_number:
        parts[0] += u", sheet %s" % outcome.sheet_number
    if outcome.deleted:
        parts.append(u"%d deleted" % outcome.deleted)
    if outcome.copied:
        parts.append(u"%d copied" % outcome.copied)
    tally = outcome.tally
    if tally.tags_total or tally.tags_created:
        text = u"tags %d / %d" % (tally.tags_ok, tally.tags_total)
        if tally.tags_created:
            text += u" (+%d created)" % tally.tags_created
        parts.append(text)
    if tally.dims_total:
        parts.append(u"dims %d / %d" % (tally.dims_ok, tally.dims_total))
    if tally.spots_total:
        parts.append(u"spots %d / %d" % (tally.spots_ok, tally.spots_total))
    if tally.exists:
        parts.append(u"%d already existed" % tally.exists)
    if outcome.unmatched:
        parts.append(u"%d unmatched" % len(outcome.unmatched))
    return u" · ".join(parts)


def run_clone(doc, src_id, target_ids, settings, progress=None, replace_plan=None):
    """Clone the drawing of assembly `src_id` to every id of `target_ids`.

    ONE TransactionGroup "T3Lab: Clone drawing" (Ctrl+Z undoes the click),
    up to four Transactions per target. ``progress(index, total, label)``
    returning False stops; the remaining targets are ``skipped: stopped``.
    ``replace_plan`` = {target_id: replaceable_ids(...)} counted by the dialog
    for the confirmation. Raises only when the group itself fails (it is then
    rolled back). Returns [TargetOutcome] in target order.
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
    replace_plan = replace_plan or {}

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
                    outcome = clone_to_target(doc, src_asm, dst_asm, src_specs, settings,
                                              views_index, taken, replace_plan.get(eid_value(raw)))
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
    """{'ok', 'skipped', 'failed', 'views', 'sheets', 'unmatched', 'deleted', 'created_tags'}."""
    out = {STATUS_OK: 0, STATUS_SKIPPED: 0, STATUS_FAILED: 0,
           "views": 0, "sheets": 0, "unmatched": 0, "deleted": 0, "created_tags": 0}
    for outcome in outcomes:
        out[outcome.status] = out.get(outcome.status, 0) + 1
        out["views"] += outcome.views_created
        out["sheets"] += 1 if outcome.sheet_id > 0 else 0
        out["unmatched"] += outcome.unmatched_count
        out["deleted"] += outcome.deleted
        out["created_tags"] += outcome.tally.tags_created
    return out


def tally_text(outcomes):
    """Footer strip of the Results page."""
    totals = summarize_outcomes(outcomes)
    tags_ok = sum(o.tally.tags_ok for o in outcomes)
    tags_all = sum(o.tally.tags_total for o in outcomes)
    dims_ok = sum(o.tally.dims_ok for o in outcomes)
    dims_all = sum(o.tally.dims_total for o in outcomes)
    spots_ok = sum(o.tally.spots_ok for o in outcomes)
    spots_all = sum(o.tally.spots_total for o in outcomes)
    text = (u"%d ok · %d skipped · %d failed · %s · %s · tags %d / %d"
            % (totals[STATUS_OK], totals[STATUS_SKIPPED], totals[STATUS_FAILED],
               _plural(totals["views"], u"view"), _plural(totals["sheets"], u"sheet"),
               tags_ok, tags_all))
    if totals["created_tags"]:
        text += u" (+%d created)" % totals["created_tags"]
    text += u" · dims %d / %d" % (dims_ok, dims_all)
    if spots_all:
        text += u" · spots %d / %d" % (spots_ok, spots_all)
    if totals["deleted"]:
        text += u" · %d deleted" % totals["deleted"]
    text += u" · %d unmatched" % totals["unmatched"]
    return text
