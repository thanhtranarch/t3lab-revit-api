# -*- coding: utf-8 -*-
"""
_rebar_wizard.py
================
Rebar Wizard — reinforce rectangular straight beams, rectangular columns and
pad-footing bottom meshes from a preset (Tekla system components 63 / 83 / 77).

Two halves, kept apart on purpose:

* PURE PYTHON  - sections, wizard inputs, every piece of layout math (bar
  counts, spacings, stirrup zones, cover offsets), the English summary line and
  the preset file. No Revit import at module level, so
  ``dev/test_rebar_wizard_layout.py`` exercises the shipped source without Revit.
* REVIT        - ``read_section`` (geometry -> Section + local frame),
  ``resolve_types`` and ``create_plans`` (one Transaction per host, inside the
  caller's TransactionGroup). ``Autodesk.Revit.DB`` is imported inside each
  function; nothing raises per host - the caller gets ``Result`` rows.

Local frames (all lengths in mm):

* beam    - origin = centre of the section at the start of the concrete, X along
            the axis, Y across the width b, Z up (depth h).
* column  - origin = centre of the section at the bottom of the concrete, X up
            (the member axis), Y = HandOrientation (width b), Z = X x Y (depth h).
            The beam math therefore applies unchanged: verticals are the
            "longitudinal" bars, ties are the "stirrups".
* footing - origin = plan centre at the underside, X = HandOrientation,
            Y = Z x X, Z up. ``Section.length`` = X extent, ``Section.b`` = Y
            extent, ``Section.h`` = thickness.

Spec: dev/plan/rebar-tekla-implementation-spec.md section 3.6 / 5.6.
Everything that touches Revit runtime is [NV] until the Revit 2027 spike and the
QA list of section 5.6 have run; each such call degrades to a ``failed`` row
with the Revit message instead of an exception.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"
__title__ = "Rebar Wizard"

import json
import math
import os

from Snippets._compat import (
    create_rebar_from_curves,
    add_to_assembly_of,
    disposing,
    eid_value,
    elem_name,
    make_eid,
    short_error,
    to_feet,
    to_mm,
    _revit_type,
)
from Snippets._assembly import Result, STATUS_OK, STATUS_SKIPPED, STATUS_FAILED


# ── CONSTANTS ────────────────────────────────────────────────────────────────

KIND_BEAM = "beam"
KIND_COLUMN = "column"
KIND_FOOTING = "footing"
KINDS = (KIND_BEAM, KIND_COLUMN, KIND_FOOTING)
KIND_LABELS = {KIND_BEAM: ("beam", "beams"),
               KIND_COLUMN: ("column", "columns"),
               KIND_FOOTING: ("pad footing", "pad footings")}

STYLE_STANDARD = "Standard"
STYLE_STIRRUP = "StirrupTie"

# Rectangular test: |solid volume - b*h*L| must stay within this share of b*h*L.
RECT_TOLERANCE = 0.02
# A beam whose axis leaves the horizontal by more than this is "sloped".
SLOPE_TOLERANCE_DEG = 1.0
# Shortest middle stirrup zone worth its own rebar set.
MIN_MID_ZONE_MM = 200.0
# Minimum clear gap between parallel main bars: max(bar diameter, this).
MIN_CLEAR_GAP_MM = 20.0
# Footing bars with "90 deg end hooks": the bent-up leg is this many diameters
# long, capped at the top cover.
FOOTING_LEG_DIAMETERS = 12.0

# Hook orientation of a closed stirrup drawn clockwise when looking against its
# normal: the hooks have to turn into the section, i.e. to the right of the
# direction of travel. [NV] checked on Revit 2027 / 2026 QA (spec 5.6).
STIRRUP_HOOK_ORIENTATION = "Right"

# Hook combo entry meaning "no hook type at the ends" ('' means "model default").
NO_HOOK = u"No hook"

PRESET_FILE = "rebar_wizard_presets.json"
DEFAULT_PRESET = "Default"

# Out-of-scope reasons (spec 5.6). Shown as "Out of scope: <reason>".
OOS_NOT_HOST = u"not a beam, column or isolated footing"
OOS_WALL_SLAB = u"wall or slab - use Area Reinforcement (Revit)"
OOS_FOOTING_NOT_FI = (u"footing is not a family instance (wall foundation or "
                      u"foundation slab) - use Area Reinforcement (Revit)")
OOS_CURVED = u"curved beam"
OOS_SLOPED = u"sloped beam"
OOS_ROTATED = u"beam section is rotated"
OOS_NOT_RECT = u"section is not rectangular"
OOS_COLUMN_SLANTED = u"tapered or non-vertical column"
OOS_LINK = u"element from link"
OOS_GROUP = u"element in group"
OOS_NOT_VALID_HOST = u"not a valid rebar host (the structural material must be concrete)"
OOS_NO_GEOMETRY = u"no solid geometry to measure"


# ── RECORDS (pure) ───────────────────────────────────────────────────────────

class LayoutError(ValueError):
    """The bars of a preset do not fit the section. Message is user-facing English."""


class Frame(object):
    """Local frame of a host: origin in feet (model), x/y/z unit vectors (model)."""

    def __init__(self, origin, x, y, z):
        self.origin = tuple(origin)
        self.x = tuple(x)
        self.y = tuple(y)
        self.z = tuple(z)

    def to_model(self, point_mm):
        """(x, y, z) mm in the local frame -> (x, y, z) feet in the model."""
        lx, ly, lz = (to_feet(v) for v in point_mm)
        return tuple(self.origin[i] + lx * self.x[i] + ly * self.y[i] + lz * self.z[i]
                     for i in range(3))

    def vector_to_model(self, vec):
        """Direction (x, y, z) in the local frame -> model direction."""
        return tuple(vec[0] * self.x[i] + vec[1] * self.y[i] + vec[2] * self.z[i]
                     for i in range(3))


class Section(object):
    """What the wizard knows about one host.

    b, h, length in mm (see the module docstring for the footing meaning);
    ``rectangular`` False means out of scope and ``reason`` says why.
    """

    def __init__(self, b=0.0, h=0.0, length=0.0, kind=None, rectangular=True,
                 reason=u"", frame=None):
        self.b = float(b)
        self.h = float(h)
        self.length = float(length)
        self.kind = kind
        self.rectangular = bool(rectangular)
        self.reason = reason or u""
        self.frame = frame

    @property
    def in_scope(self):
        return self.rectangular and not self.reason and self.kind in KINDS

    def label(self):
        """'300×600' for beams/columns, '1800×1800×600' for footings."""
        if self.kind == KIND_FOOTING:
            return u"%s×%s×%s" % (fmt_mm(self.length), fmt_mm(self.b), fmt_mm(self.h))
        if self.b <= 0 or self.h <= 0:
            return u"—"
        return u"%s×%s" % (fmt_mm(self.b), fmt_mm(self.h))

    def __repr__(self):
        return "<Section %s b=%g h=%g L=%g %s>" % (
            self.kind, self.b, self.h, self.length, self.reason or "ok")


def out_of_scope(reason, kind=None):
    """A Section that only carries the reason it cannot be reinforced."""
    return Section(kind=kind, rectangular=False, reason=reason)


class BarPlan(object):
    """One rebar set to create: centreline in the host's local frame (mm).

    layout: ("single",) | ("fixed", n, array_len) | ("max_spacing", s, array_len)
    ``include_first`` / ``include_last`` go to SetLayoutAs...; the normal points
    along the distribution direction (``bars_on_normal_side``).
    ``role`` drives the summary: bottom · top · stirrup · vertical · tie ·
    cross_tie · mesh_x · mesh_y.
    """

    def __init__(self, label, style, diameter, points, normal, layout,
                 role, zone=u"", hook_start=None, hook_end=None,
                 orient_start="Left", orient_end="Left",
                 bars_on_normal_side=True, include_first=True, include_last=True,
                 bar_type_name=u"", group=None):
        self.label = label
        self.group = group or label
        self.style = style
        self.diameter = float(diameter)
        self.bar_type_name = bar_type_name
        self.hook_start = hook_start
        self.hook_end = hook_end
        self.orient_start = orient_start
        self.orient_end = orient_end
        self.points = [tuple(float(c) for c in p) for p in points]
        self.normal = tuple(float(c) for c in normal)
        self.layout = tuple(layout)
        self.bars_on_normal_side = bool(bars_on_normal_side)
        self.include_first = bool(include_first)
        self.include_last = bool(include_last)
        self.role = role
        self.zone = zone

    @property
    def bar_count(self):
        """Bars Revit will place for this set (positions minus excluded ends)."""
        kind = self.layout[0]
        if kind == "single":
            return 1
        if kind == "fixed":
            positions = int(self.layout[1])
        else:
            positions = max_spacing_positions(self.layout[1], self.layout[2])
        if positions <= 1:
            return 1 if (self.include_first or self.include_last) else 0
        return positions - (0 if self.include_first else 1) - (0 if self.include_last else 1)

    def __repr__(self):
        return "<BarPlan %s %s Ø%g %r>" % (self.label, self.style, self.diameter, self.layout)


# ── WIZARD INPUT (pure) ──────────────────────────────────────────────────────

# (attribute, type, default) per tab. Diameters are nominal mm; the dialog maps
# them to the model's RebarBarType of that diameter.
FIELDS = {
    KIND_BEAM: (
        ("bot_dia", float, 20.0), ("bot_n", int, 4),
        ("top_dia", float, 16.0), ("top_n", int, 2),
        ("stir_dia", float, 8.0), ("s_end", float, 100.0),
        ("l_end", float, 600.0), ("s_mid", float, 150.0),
        ("cover", float, 25.0), ("hook", str, u""),
        ("add_assembly", bool, True),
    ),
    KIND_COLUMN: (
        ("vert_dia", float, 20.0), ("side_n", int, 1),
        ("tie_dia", float, 8.0), ("s_dense", float, 100.0),
        ("l_dense", float, 600.0), ("s_mid", float, 200.0),
        ("cover", float, 40.0), ("hook", str, u""),
        ("cross_tie", bool, True), ("add_assembly", bool, True),
    ),
    KIND_FOOTING: (
        ("x_dia", float, 16.0), ("x_s", float, 150.0),
        ("y_dia", float, 16.0), ("y_s", float, 150.0),
        ("cover", float, 50.0), ("hooks", bool, False),
        ("add_assembly", bool, True),
    ),
}

# Human names of the fields, for validation messages.
FIELD_NAMES = {
    "bot_dia": "Bottom bar diameter", "bot_n": "Bottom bar count",
    "top_dia": "Top bar diameter", "top_n": "Top bar count",
    "stir_dia": "Stirrup diameter", "s_end": "End zone spacing",
    "l_end": "End zone length", "s_mid": "Middle zone spacing",
    "cover": "Cover", "vert_dia": "Vertical bar diameter",
    "side_n": "Extra bars per face", "tie_dia": "Tie diameter",
    "s_dense": "Dense zone spacing", "l_dense": "Dense zone length",
    "x_dia": "X bar diameter", "x_s": "X bar spacing",
    "y_dia": "Y bar diameter", "y_s": "Y bar spacing",
}

# Fields that must be > 0, may be 0, or are counts with a minimum.
_POSITIVE = ("bot_dia", "top_dia", "stir_dia", "s_end", "s_mid", "cover",
             "vert_dia", "tie_dia", "s_dense", "x_dia", "x_s", "y_dia", "y_s")
_NON_NEGATIVE = ("l_end", "l_dense", "side_n")
_COUNT_MIN = {"bot_n": 1, "top_n": 1}


class WizardInput(object):
    """Form values of one tab, as plain attributes (spec 5.6)."""

    def __init__(self, kind, **values):
        if kind not in FIELDS:
            raise ValueError("unknown wizard kind %r" % (kind,))
        self.kind = kind
        self.raw = {}           # field -> text the user typed when it did not parse
        for name, cast, default in FIELDS[kind]:
            setattr(self, name, default)
        for name, value in values.items():
            self.set(name, value)

    @classmethod
    def field_names(cls, kind):
        return [name for name, _, _ in FIELDS[kind]]

    def set(self, name, value):
        """Set one field from a Python value or the text of a TextBox."""
        spec = dict((n, (c, d)) for n, c, d in FIELDS[self.kind])
        if name not in spec:
            raise KeyError("%s has no field %r" % (self.kind, name))
        cast, _default = spec[name]
        self.raw.pop(name, None)
        if cast is bool:
            setattr(self, name, bool(value))
            return
        if cast is str:
            setattr(self, name, u"" if value is None else u"%s" % value)
            return
        try:
            text = (u"%s" % value).strip().replace(",", ".")
            number = float(text)
            if cast is int:
                if number != int(number):
                    raise ValueError(text)
                number = int(number)
            setattr(self, name, number)
        except (TypeError, ValueError):
            self.raw[name] = u"%s" % (value,)
            setattr(self, name, None)

    def validate(self):
        """English messages, one per bad field; [] when every field is usable."""
        problems = []
        for name, cast, _default in FIELDS[self.kind]:
            if cast in (bool, str):
                continue
            label = FIELD_NAMES.get(name, name)
            value = getattr(self, name)
            if value is None:
                problems.append(u"%s must be a number (got \"%s\")."
                                % (label, self.raw.get(name, u"")))
                continue
            if name in _POSITIVE and value <= 0:
                problems.append(u"%s must be greater than 0." % label)
            elif name in _NON_NEGATIVE and value < 0:
                problems.append(u"%s cannot be negative." % label)
            elif name in _COUNT_MIN and value < _COUNT_MIN[name]:
                problems.append(u"%s must be at least %d." % (label, _COUNT_MIN[name]))
        return problems

    def to_dict(self):
        return dict((name, getattr(self, name)) for name in self.field_names(self.kind))

    @classmethod
    def from_dict(cls, kind, data):
        """Unknown keys are ignored, missing keys keep the default."""
        inp = cls(kind)
        for name in cls.field_names(kind):
            if isinstance(data, dict) and name in data:
                inp.set(name, data[name])
        return inp

    def __repr__(self):
        return "<WizardInput %s %r>" % (self.kind, self.to_dict())


# ── LAYOUT MATH (pure) ───────────────────────────────────────────────────────

def fmt_mm(value):
    """600.0 -> '600', 12.5 -> '12.5'."""
    value = float(value)
    if abs(value - round(value)) < 1e-6:
        return u"%d" % int(round(value))
    return (u"%.1f" % value).rstrip("0").rstrip(".")


def max_spacing_positions(spacing, length):
    """Bar positions of SetLayoutAsMaximumSpacing: ceil(L / s) + 1 (>= 1)."""
    if length <= 1e-6:
        return 1
    return int(math.ceil(length / float(spacing) - 1e-9)) + 1


def zone_lengths(total, l_end, min_mid=MIN_MID_ZONE_MM):
    """Split a stirrup run of `total` mm into (end1, mid, end2).

    * l_end <= 0                     -> (0, total, 0): one zone at the middle spacing
    * total - 2*l_end < min_mid      -> (total, 0, 0): one dense zone (short member)
    * otherwise                      -> (l_end, total - 2*l_end, l_end)
    """
    total = float(total)
    l_end = float(l_end)
    if total <= 0:
        return (0.0, 0.0, 0.0)
    if l_end <= 0:
        return (0.0, total, 0.0)
    if total - 2.0 * l_end < min_mid:
        return (total, 0.0, 0.0)
    return (l_end, total - 2.0 * l_end, l_end)


def spread(count, span):
    """Offsets of `count` bars spread evenly over `span`, centred on 0."""
    if count <= 1:
        return [0.0]
    step = span / float(count - 1)
    return [-span / 2.0 + i * step for i in range(count)]


def stirrup_points(b, h, cover, d_stirrup):
    """Closed rectangular stirrup centreline in the (y, z) plane at x = 0.

    Corners at y = ±(b/2 - cover - d/2), z = ±(h/2 - cover - d/2). Starts at the
    top-left corner (the hook corner) and runs clockwise as seen from +X
    (Y right, Z up), so the 135° hooks from the RebarHookType turn into the section
    (STIRRUP_HOOK_ORIENTATION). 5 points, 4 legs, last point = first point.
    """
    yc = b / 2.0 - cover - d_stirrup / 2.0
    zc = h / 2.0 - cover - d_stirrup / 2.0
    if yc <= 0 or zc <= 0:
        raise LayoutError(
            u"A Ø%s stirrup with %s mm cover does not fit a %s×%s section. "
            u"Reduce the cover or the stirrup diameter."
            % (fmt_mm(d_stirrup), fmt_mm(cover), fmt_mm(b), fmt_mm(h)))
    return [(0.0, -yc, zc), (0.0, yc, zc), (0.0, yc, -zc), (0.0, -yc, -zc), (0.0, -yc, zc)]


def _check_gap(count, span, dia, where, section_label):
    """LayoutError when `count` bars of Ø`dia` over `span` leave too small a gap."""
    if span < 0:
        raise LayoutError(
            u"Ø%s %s bars do not fit inside the cover of a %s section. "
            u"Reduce the cover or the bar diameters."
            % (fmt_mm(dia), where, section_label))
    if count <= 1:
        return
    gap = span / float(count - 1) - dia
    need = max(dia, MIN_CLEAR_GAP_MM)
    if gap < need - 1e-6:
        raise LayoutError(
            u"%d × Ø%s %s bars do not fit a %s section (clear gap %s mm, needs %s mm). "
            u"Use fewer or thinner bars."
            % (count, fmt_mm(dia), where, section_label, fmt_mm(round(gap, 1)), fmt_mm(need)))


def _tie_sets(prefix, role, length, cover, d_tie, l_end, s_end, s_mid, points,
              hook, dense_word):
    """Stirrup / tie sets along X over [cover, length - cover] in up to 3 zones.

    End zones keep both end bars; the middle zone drops both so no bar is
    placed twice where two zones meet.
    """
    run = length - 2.0 * cover
    if run <= 0:
        raise LayoutError(u"The member is shorter than twice the cover (%s mm)."
                          % fmt_mm(cover))
    end1, mid, end2 = zone_lengths(run, l_end)
    zones = []
    x = cover
    if end1 > 0:
        zones.append((x, end1, s_end, True, True, u"%s 1" % dense_word))
        x += end1
    if mid > 0:
        first_last = not zones           # alone: keep the end bars
        zones.append((x, mid, s_mid, first_last, first_last, u"middle"))
        x += mid
    if end2 > 0:
        zones.append((x, end2, s_end, True, True, u"%s 2" % dense_word))
    hook_name = hook if hook and hook != NO_HOOK else None
    plans = []
    for start, zlen, spacing, inc_first, inc_last, zone in zones:
        moved = [(start + p[0], p[1], p[2]) for p in points]
        plans.append(BarPlan(
            u"%s %s" % (prefix, zone), STYLE_STIRRUP, d_tie, moved, (1.0, 0.0, 0.0),
            ("max_spacing", float(spacing), float(zlen)), role, zone=zone,
            hook_start=hook_name, hook_end=hook_name,
            orient_start=STIRRUP_HOOK_ORIENTATION, orient_end=STIRRUP_HOOK_ORIENTATION,
            include_first=inc_first, include_last=inc_last, group=prefix))
    return plans


def beam_plan(section, inp):
    """Bottom bars, top bars and three-zone stirrups for a rectangular beam.

    Raises LayoutError (English) when the preset does not fit the section.
    """
    b, h, length = section.b, section.h, section.length
    cover, d_s = inp.cover, inp.stir_dia
    label = section.label()
    x0, x1 = cover, length - cover
    if x1 <= x0:
        raise LayoutError(u"The beam (%s mm) is shorter than twice the cover." % fmt_mm(length))
    plans = []
    for role, n, dia, sign in (("bottom", inp.bot_n, inp.bot_dia, -1.0),
                               ("top", inp.top_n, inp.top_dia, 1.0)):
        span = b - 2.0 * (cover + d_s) - dia
        _check_gap(n, span, dia, role, label)
        z = sign * (h / 2.0 - cover - d_s - dia / 2.0)
        if n <= 1:
            plans.append(BarPlan(u"%s bars" % role.capitalize(), STYLE_STANDARD, dia,
                                 [(x0, 0.0, z), (x1, 0.0, z)], (0.0, 1.0, 0.0),
                                 ("single",), role))
        else:
            y = -span / 2.0
            plans.append(BarPlan(u"%s bars" % role.capitalize(), STYLE_STANDARD, dia,
                                 [(x0, y, z), (x1, y, z)], (0.0, 1.0, 0.0),
                                 ("fixed", int(n), span), role))
    if h - 2.0 * (cover + d_s) - inp.bot_dia - inp.top_dia < 0:
        raise LayoutError(u"Top and bottom bars overlap in a %s beam. Reduce the cover "
                          u"or the bar diameters." % label)
    ring = stirrup_points(b, h, cover, d_s)
    plans.extend(_tie_sets(u"Stirrups", "stirrup", length, cover, d_s, inp.l_end,
                           inp.s_end, inp.s_mid, ring, inp.hook, u"end"))
    return plans


def column_plan(section, inp):
    """Corner + side verticals, three-zone ties and optional cross ties.

    Column frame: X up, Y = b, Z = h (see module docstring).
    """
    b, h, height = section.b, section.h, section.length
    cover, d_t, d_v = inp.cover, inp.tie_dia, inp.vert_dia
    label = section.label()
    n_side = int(inp.side_n)
    x0, x1 = cover, height - cover
    if x1 <= x0:
        raise LayoutError(u"The column (%s mm) is shorter than twice the cover." % fmt_mm(height))
    span_y = b - 2.0 * (cover + d_t) - d_v
    span_z = h - 2.0 * (cover + d_t) - d_v
    _check_gap(n_side + 2, span_y, d_v, "vertical", label)
    _check_gap(n_side + 2, span_z, d_v, "vertical", label)
    yv, zv = span_y / 2.0, span_z / 2.0
    positions = n_side + 2
    plans = []
    # Faces y = ±yv carry the corners plus their side bars (distributed along +Z).
    for side, y in ((u"left", -yv), (u"right", yv)):
        plans.append(BarPlan(u"Verticals %s face" % side, STYLE_STANDARD, d_v,
                             [(x0, y, -zv), (x1, y, -zv)], (0.0, 0.0, 1.0),
                             ("fixed", positions, span_z), "vertical"))
    # Faces z = ±zv carry only the side bars between the corners (along +Y).
    if n_side > 0:
        for side, z in ((u"front", -zv), (u"back", zv)):
            plans.append(BarPlan(u"Verticals %s face" % side, STYLE_STANDARD, d_v,
                                 [(x0, -yv, z), (x1, -yv, z)], (0.0, 1.0, 0.0),
                                 ("fixed", positions, span_y), "vertical",
                                 include_first=False, include_last=False))
    ring = stirrup_points(b, h, cover, d_t)
    plans.extend(_tie_sets(u"Ties", "tie", height, cover, d_t, inp.l_dense,
                           inp.s_dense, inp.s_mid, ring, inp.hook, u"dense"))
    if inp.cross_tie and n_side > 0:
        yc = b / 2.0 - cover - d_t / 2.0
        zc = h / 2.0 - cover - d_t / 2.0
        crossing = []
        # Ties along Z through the inner bars of the front/back faces, and along Y
        # through the inner bars of the left/right faces.
        for y in spread(positions, span_y)[1:-1]:
            crossing.append((u"Cross tie Y=%s" % fmt_mm(round(y)), [(0.0, y, -zc), (0.0, y, zc)]))
        for z in spread(positions, span_z)[1:-1]:
            crossing.append((u"Cross tie Z=%s" % fmt_mm(round(z)), [(0.0, -yc, z), (0.0, yc, z)]))
        for name, pts in crossing:
            plans.extend(_tie_sets(name, "cross_tie", height, cover, d_t, inp.l_dense,
                                   inp.s_dense, inp.s_mid, pts, inp.hook, u"dense"))
    return plans


def _footing_bar(x_axis, along, across_offset, z, half_len, leg_top, dia, hooks):
    """Points of one footing bar, optionally bent up at both ends."""
    def pt(a, c, zz):
        return (a, c, zz) if x_axis else (c, a, zz)
    start, end = pt(-half_len, across_offset, z), pt(half_len, across_offset, z)
    if not hooks or leg_top <= z + 1e-6:
        return [start, end]
    return [pt(-half_len, across_offset, leg_top), start, end, pt(half_len, across_offset, leg_top)]


def footing_plan(section, inp):
    """Two-layer bottom mesh of a pad footing.

    Y bars (along Y) are the bottom layer at z = cover + d_y/2; X bars (along X)
    sit on them at z = cover + d_y + d_x/2. Each layer is spread over the plan
    extent minus 2·cover minus one bar diameter, at most `s` apart. With
    ``hooks`` both ends bend up 90° (leg FOOTING_LEG_DIAMETERS·d, capped at the
    top cover).
    """
    lx, ly, th = section.length, section.b, section.h
    cover, d_x, d_y = inp.cover, inp.x_dia, inp.y_dia
    label = section.label()
    if th - 2.0 * cover - d_x - d_y < 0:
        raise LayoutError(u"Two bar layers do not fit a %s mm thick footing with %s mm cover."
                          % (fmt_mm(th), fmt_mm(cover)))
    plans = []
    z_y = cover + d_y / 2.0
    z_x = cover + d_y + d_x / 2.0
    for role, along, across, dia, s, z, x_axis in (
            ("mesh_x", lx, ly, d_x, inp.x_s, z_x, True),
            ("mesh_y", ly, lx, d_y, inp.y_s, z_y, False)):
        half = along / 2.0 - cover - dia / 2.0
        span = across - 2.0 * cover - dia
        if half <= 0 or span < 0:
            raise LayoutError(u"Ø%s bars do not fit a %s footing with %s mm cover."
                              % (fmt_mm(dia), label, fmt_mm(cover)))
        if s < dia + MIN_CLEAR_GAP_MM:
            raise LayoutError(u"Ø%s bars at %s mm leave less than %s mm between them. "
                              u"Increase the spacing." % (fmt_mm(dia), fmt_mm(s),
                                                          fmt_mm(MIN_CLEAR_GAP_MM)))
        leg_top = min(th - cover - dia / 2.0, z + FOOTING_LEG_DIAMETERS * dia)
        points = _footing_bar(x_axis, along, -span / 2.0, z, half, leg_top, dia, inp.hooks)
        normal = (0.0, 1.0, 0.0) if x_axis else (1.0, 0.0, 0.0)
        name = u"X bars" if x_axis else u"Y bars"
        plans.append(BarPlan(name, STYLE_STANDARD, dia, points, normal,
                             ("max_spacing", float(s), span), role))
    return plans


PLANNERS = {KIND_BEAM: beam_plan, KIND_COLUMN: column_plan, KIND_FOOTING: footing_plan}


def plan_for(section, inp):
    """Plans for a section of the input's kind; LayoutError when it does not fit."""
    if section.kind != inp.kind:
        raise LayoutError(u"This host is a %s, not a %s."
                          % (KIND_LABELS.get(section.kind, ("element",))[0],
                             KIND_LABELS[inp.kind][0]))
    return PLANNERS[inp.kind](section, inp)


def _zone_text(sets):
    """'@100 (2×600) / @150' for the zone sets of one stirrup/tie group."""
    ends = [p for p in sets if p.zone != u"middle"]
    mids = [p for p in sets if p.zone == u"middle"]
    parts = []
    if ends:
        lengths = sorted(set(fmt_mm(round(p.layout[2])) for p in ends))
        if mids:
            parts.append(u"@%s (%d×%s)" % (fmt_mm(ends[0].layout[1]), len(ends),
                                           u"/".join(lengths)))
        else:
            parts.append(u"@%s" % fmt_mm(ends[0].layout[1]))
    if mids:
        parts.append(u"@%s" % fmt_mm(mids[0].layout[1]))
    return u" / ".join(parts)


def summarize(plans):
    """One English line, e.g. '4 × Ø20 bottom · 2 × Ø16 top · stirrups Ø8 @100 (2×600) / @150 — 3 sets'."""
    if not plans:
        return u"No bars."
    parts = []
    main = []           # (role, dia) in first-seen order
    counts = {}
    for p in plans:
        if p.role in ("bottom", "top", "vertical", "mesh_x", "mesh_y"):
            key = (p.role, p.diameter)
            if key not in counts:
                main.append(key)
                counts[key] = 0
            counts[key] += p.bar_count
    words = {"bottom": u"bottom", "top": u"top", "vertical": u"verticals",
             "mesh_x": u"X bars", "mesh_y": u"Y bars"}
    for role, dia in main:
        if role in ("mesh_x", "mesh_y"):
            spacing = [p.layout[1] for p in plans if p.role == role][0]
            parts.append(u"%s Ø%s @%s (%d)" % (words[role], fmt_mm(dia), fmt_mm(spacing),
                                               counts[(role, dia)]))
        else:
            parts.append(u"%d × Ø%s %s" % (counts[(role, dia)], fmt_mm(dia), words[role]))
    for role, word in (("stirrup", u"stirrups"), ("tie", u"ties")):
        sets = [p for p in plans if p.role == role]
        if sets:
            parts.append(u"%s Ø%s %s" % (word, fmt_mm(sets[0].diameter), _zone_text(sets)))
    cross = [p for p in plans if p.role == "cross_tie"]
    if cross:
        lines = len(set(p.group for p in cross))
        parts.append(u"%d cross tie line%s" % (lines, u"" if lines == 1 else u"s"))
    n = len(plans)
    return u"%s — %d set%s" % (u" · ".join(parts), n, u"" if n == 1 else u"s")


def total_bars(plans):
    return sum(p.bar_count for p in plans)


# ── PRESETS (pure; the default path lives under %APPDATA%\T3LabAI) ───────────

def default_preset():
    """{kind: field dict} with the factory values of every tab."""
    return dict((kind, WizardInput(kind).to_dict()) for kind in KINDS)


def preset_path():
    """%APPDATA%\\T3LabAI\\rebar_wizard_presets.json (never inside the extension)."""
    from core.paths import user_data_path
    return user_data_path(PRESET_FILE)


def load_presets(path):
    """{name: {kind: field dict}}; a missing or unreadable file gives {}.

    Unknown kinds / fields are dropped and missing ones take the defaults, so an
    old preset file never breaks the window.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    presets = {}
    for name, body in data.items():
        if not isinstance(body, dict):
            continue
        presets[u"%s" % name] = dict(
            (kind, WizardInput.from_dict(kind, body.get(kind, {})).to_dict())
            for kind in KINDS)
    return presets


def save_presets(path, presets):
    """Write {name: {kind: field dict}} atomically. Returns None or an error text."""
    tmp = path + u".tmp"
    try:
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(presets, handle, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(tmp, path)
        return None
    except (IOError, OSError, TypeError, ValueError) as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return short_error(exc)


def preset_io(path):
    """Spec name: (load, save) bound to `path`."""
    return (lambda: load_presets(path), lambda presets: save_presets(path, presets))


# ── REVIT HELPERS (private) ──────────────────────────────────────────────────

def _bic(name):
    """int of BuiltInCategory.<name>, None when this release lacks it."""
    from Autodesk.Revit.DB import BuiltInCategory
    member = getattr(BuiltInCategory, name, None)
    return int(member) if member is not None else None


def _category_int(element):
    try:
        return eid_value(element.Category.Id)
    except Exception:
        return None


def _kind_of_category(cat):
    if cat is None:
        return None
    if cat == _bic("OST_StructuralFraming"):
        return KIND_BEAM
    if cat == _bic("OST_StructuralColumns"):
        return KIND_COLUMN
    if cat == _bic("OST_StructuralFoundation"):
        return KIND_FOOTING
    return None


def _unit(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < 1e-12:
        return None
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _xyz(v):
    return (float(v.X), float(v.Y), float(v.Z))


def _solids(element):
    """Every solid with volume of an element (instance geometry, Fine)."""
    from Autodesk.Revit.DB import Options, ViewDetailLevel, Solid, GeometryInstance
    options = Options()
    options.DetailLevel = ViewDetailLevel.Fine
    options.ComputeReferences = False
    out = []

    def walk(geometry):
        if geometry is None:
            return
        for obj in geometry:
            if isinstance(obj, Solid):
                try:
                    if obj.Volume > 1e-9:
                        out.append(obj)
                except Exception:
                    pass
            elif isinstance(obj, GeometryInstance):
                walk(obj.GetInstanceGeometry())

    walk(element.get_Geometry(options))
    return out


def _extents(solids, base, axes):
    """min/max of every tessellated edge point of `solids` along the 3 axes (feet)."""
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for solid in solids:
        for edge in solid.Edges:
            for p in edge.Tessellate():
                d = (p.X - base[0], p.Y - base[1], p.Z - base[2])
                for i, axis in enumerate(axes):
                    v = _dot(d, axis)
                    if v < lo[i]:
                        lo[i] = v
                    if v > hi[i]:
                        hi[i] = v
    if lo[0] == float("inf"):
        return None
    return lo, hi


def _measure(element, base, axes, kind, origin_rule):
    """Section from the solids of `element` measured in the frame (base, axes).

    origin_rule: "start_centre" (beam/column: centre of the section at the low
    end of axis 0) or "plan_centre_bottom" (footing).
    """
    solids = _solids(element)
    if not solids:
        return out_of_scope(OOS_NO_GEOMETRY, kind)
    ext = _extents(solids, base, axes)
    if ext is None:
        return out_of_scope(OOS_NO_GEOMETRY, kind)
    lo, hi = ext
    size = [to_mm(hi[i] - lo[i]) for i in range(3)]
    volume_mm3 = sum(s.Volume for s in solids) * (304.8 ** 3)
    box = size[0] * size[1] * size[2]
    if box <= 0 or abs(volume_mm3 - box) > RECT_TOLERANCE * box:
        reason = OOS_NOT_RECT
        if kind == KIND_COLUMN:
            reason = u"section is not rectangular (round or tapered column)"
        return out_of_scope(reason, kind)
    if origin_rule == "plan_centre_bottom":
        local = ((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, lo[2])
    else:
        local = (lo[0], (lo[1] + hi[1]) / 2.0, (lo[2] + hi[2]) / 2.0)
    origin = tuple(base[i] + local[0] * axes[0][i] + local[1] * axes[1][i]
                   + local[2] * axes[2][i] for i in range(3))
    frame = Frame(origin, axes[0], axes[1], axes[2])
    # axis 0 = length (beam axis / column height / footing X), 1 = b, 2 = h.
    return Section(b=size[1], h=size[2], length=size[0], kind=kind, frame=frame)


def _horizontal(vec):
    return _unit((vec[0], vec[1], 0.0))


# ── REVIT: READ ──────────────────────────────────────────────────────────────

def read_section(doc, element):
    """Section of one host, or an out-of-scope Section whose reason says why.

    Never raises: an unexpected error becomes the reason text.
    """
    try:
        return _read_section(doc, element)
    except Exception as exc:
        return out_of_scope(u"could not read the geometry: %s" % short_error(exc))


def _read_section(doc, element):
    from Autodesk.Revit.DB import FamilyInstance, LocationCurve, LocationPoint, Line
    if element is None:
        return out_of_scope(OOS_NOT_HOST)
    try:
        if element.Document.IsLinked:
            return out_of_scope(OOS_LINK)
    except Exception:
        pass
    cat = _category_int(element)
    kind = _kind_of_category(cat)
    if cat in (_bic("OST_Walls"), _bic("OST_Floors")):
        return out_of_scope(OOS_WALL_SLAB)
    try:
        if eid_value(element.GroupId) >= 0:
            return out_of_scope(OOS_GROUP, kind)
    except Exception:
        pass
    if kind == KIND_FOOTING and not isinstance(element, FamilyInstance):
        return out_of_scope(OOS_FOOTING_NOT_FI, kind)
    if kind is None or not isinstance(element, FamilyInstance):
        return out_of_scope(OOS_NOT_HOST)
    host_data = _revit_type("RebarHostData")
    try:
        if host_data is not None and not host_data.IsValidHost(element):
            return out_of_scope(OOS_NOT_VALID_HOST, kind)
    except Exception:
        pass

    up = (0.0, 0.0, 1.0)
    location = element.Location
    if kind == KIND_BEAM:
        if not isinstance(location, LocationCurve):
            return out_of_scope(OOS_NOT_HOST, kind)
        curve = location.Curve
        if not isinstance(curve, Line):
            return out_of_scope(OOS_CURVED, kind)
        direction = _xyz(curve.Direction)
        if abs(direction[2]) > math.sin(math.radians(SLOPE_TOLERANCE_DEG)):
            return out_of_scope(OOS_SLOPED, kind)
        if abs(_param_double(element, "STRUCTURAL_BEND_DIR_ANGLE")) > 1e-6:
            return out_of_scope(OOS_ROTATED, kind)
        x = _horizontal(direction)
        y = _cross(up, x)
        return _measure(element, _xyz(curve.GetEndPoint(0)), (x, y, up), kind,
                        "start_centre")

    if kind == KIND_COLUMN:
        if getattr(element, "IsSlantedColumn", False) or not isinstance(location, LocationPoint):
            return out_of_scope(OOS_COLUMN_SLANTED, kind)
        hand = _horizontal(_xyz(element.HandOrientation)) or (1.0, 0.0, 0.0)
        z = _cross(up, hand)
        return _measure(element, _xyz(location.Point), (up, hand, z), kind, "start_centre")

    # footing
    hand = _horizontal(_xyz(element.HandOrientation)) or (1.0, 0.0, 0.0)
    base = _xyz(location.Point) if isinstance(location, LocationPoint) else (0.0, 0.0, 0.0)
    return _measure(element, base, (hand, _cross(up, hand), up), kind, "plan_centre_bottom")


def _param_double(element, built_in_name):
    """AsDouble of a built-in parameter, 0.0 when absent."""
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        built_in = getattr(BuiltInParameter, built_in_name, None)
        if built_in is None:
            return 0.0
        param = element.get_Parameter(built_in)
        if param is None or not param.HasValue:
            return 0.0
        return float(param.AsDouble())
    except Exception:
        return 0.0


HOST_CATEGORY_NAMES = ("OST_StructuralFraming", "OST_StructuralColumns",
                       "OST_StructuralFoundation", "OST_Floors", "OST_Walls")
REBAR_CATEGORY_NAMES = ("OST_Rebar", "OST_AreaReinforcement", "OST_PathReinforcement",
                        "OST_FabricAreas", "OST_FabricReinforcement", "OST_Coupler")


def hosts_from_ids(doc, element_ids):
    """Rule A1: expand a selection into candidate host elements.

    AssemblyInstance -> its members; rebar / area / path / fabric / coupler ->
    its host; anything else is kept when its category can host rebar (beams,
    columns, foundations, floors, walls - the last two become out-of-scope
    rows). Returns (elements in selection order, unique; ignored_count).
    """
    from Autodesk.Revit.DB import AssemblyInstance
    from Snippets._rebar import host_id_of
    host_cats = set(c for c in (_bic(n) for n in HOST_CATEGORY_NAMES) if c is not None)
    rebar_cats = set(c for c in (_bic(n) for n in REBAR_CATEGORY_NAMES) if c is not None)
    out, seen = [], set()
    ignored = [0]

    def add(element):
        if element is None:
            return
        if _category_int(element) not in host_cats:
            ignored[0] += 1
            return
        key = eid_value(element.Id)
        if key not in seen:
            seen.add(key)
            out.append(element)

    for raw in element_ids or ():
        element = doc.GetElement(make_eid(eid_value(raw)))
        if element is None:
            continue
        try:
            if isinstance(element, AssemblyInstance):
                for member_id in element.GetMemberIds():
                    member = doc.GetElement(member_id)
                    if _category_int(member) not in rebar_cats:
                        add(member)
            elif _category_int(element) in rebar_cats:
                host_id = host_id_of(doc, element)
                if host_id >= 0:
                    add(doc.GetElement(make_eid(host_id)))
            else:
                add(element)
        except Exception:
            ignored[0] += 1
    return out, ignored[0]


def assembly_mark(doc, element):
    """AssemblyTypeName of the host's assembly, '' when it has none."""
    try:
        assembly_id = eid_value(element.AssemblyInstanceId)
        if assembly_id < 0:
            return u""
        assembly = doc.GetElement(make_eid(assembly_id))
        return (assembly.AssemblyTypeName or u"") if assembly is not None else u""
    except Exception:
        return u""


def resolve_types(doc):
    """({diameter_mm: RebarBarType}, {name: RebarHookType}).

    One bar type per nominal diameter (rounded to 0.1 mm), the first by name.
    Empty dicts when the model has none (the dialog warns).
    """
    from Autodesk.Revit.DB import FilteredElementCollector
    bars, hooks = {}, {}
    bar_cls = _revit_type("RebarBarType")
    hook_cls = _revit_type("RebarHookType")
    if bar_cls is not None:
        found = []
        with disposing(FilteredElementCollector(doc)) as collector:
            for bar_type in collector.OfClass(bar_cls):
                try:
                    dia = round(to_mm(bar_type.BarNominalDiameter), 1)
                    found.append((dia, elem_name(bar_type), bar_type))
                except Exception:
                    continue
        for dia, _name, bar_type in sorted(found, key=lambda t: (t[0], t[1])):
            bars.setdefault(dia, bar_type)
    if hook_cls is not None:
        with disposing(FilteredElementCollector(doc)) as collector:
            for hook_type in collector.OfClass(hook_cls):
                try:
                    hooks[elem_name(hook_type)] = hook_type
                except Exception:
                    continue
    return bars, hooks


def default_hook_name(hook_names):
    """The first hook type whose name says 135 (stirrup/tie seismic), else ''."""
    names = sorted(hook_names)
    for name in names:
        if u"135" in name and (u"tirrup" in name or u"ie" in name):
            return name
    for name in names:
        if u"135" in name:
            return name
    return u""


_HANDLERS = None


def _warning_swallower():
    """IFailuresPreprocessor that dismisses warnings and counts them (lazy, once)."""
    global _HANDLERS
    if _HANDLERS is not None:
        return _HANDLERS
    from Autodesk.Revit.DB import (IFailuresPreprocessor, FailureProcessingResult,
                                   FailureSeverity)

    class SwallowRebarWarnings(IFailuresPreprocessor):
        # Static namespace is safe: lib/ modules are imported once per engine
        # session, and the class is built lazily so tests never touch .NET.
        __namespace__ = "T3Lab.RebarWizard"

        def __init__(self):
            self.dismissed = 0

        def PreprocessFailures(self, accessor):
            for message in accessor.GetFailureMessages():
                if message.GetSeverity() == FailureSeverity.Warning:
                    accessor.DeleteWarning(message)
                    self.dismissed += 1
            return FailureProcessingResult.Continue

    _HANDLERS = SwallowRebarWarnings
    return _HANDLERS


_FILTER = None


def host_filter():
    """ISelectionFilter for Pick in model: beams, columns and foundations only."""
    global _FILTER
    if _FILTER is None:
        from Autodesk.Revit.UI.Selection import ISelectionFilter
        allowed = set(c for c in (_bic("OST_StructuralFraming"), _bic("OST_StructuralColumns"),
                                  _bic("OST_StructuralFoundation")) if c is not None)

        class HostFilter(ISelectionFilter):
            __namespace__ = "T3Lab.RebarWizard"

            def AllowElement(self, element):
                return _category_int(element) in allowed

            def AllowReference(self, reference, position):
                return False

        _FILTER = HostFilter
    return _FILTER()


# ── REVIT: CREATE ────────────────────────────────────────────────────────────

def _model_curves(frame, points):
    from Autodesk.Revit.DB import Line, XYZ
    curves = []
    for a, b in zip(points[:-1], points[1:]):
        pa, pb = frame.to_model(a), frame.to_model(b)
        curves.append(Line.CreateBound(XYZ(*pa), XYZ(*pb)))
    return curves


def _apply_layout(rebar, plan):
    accessor = rebar.GetShapeDrivenAccessor()
    kind = plan.layout[0]
    if kind == "single":
        accessor.SetLayoutAsSingle()
    elif kind == "fixed":
        accessor.SetLayoutAsFixedNumber(int(plan.layout[1]), to_feet(plan.layout[2]),
                                        plan.bars_on_normal_side, plan.include_first,
                                        plan.include_last)
    else:
        accessor.SetLayoutAsMaximumSpacing(to_feet(plan.layout[1]), to_feet(plan.layout[2]),
                                           plan.bars_on_normal_side, plan.include_first,
                                           plan.include_last)


def _create_one(doc, host, frame, plan, types):
    """Create one rebar set; returns the new rebar. Raises with a readable message."""
    from Autodesk.Revit.DB import XYZ
    bars, hooks = types
    bar_type = bars.get(round(plan.diameter, 1))
    if bar_type is None:
        raise RuntimeError(u"no Rebar Bar Type with a %s mm nominal diameter in this model"
                           % fmt_mm(plan.diameter))
    style_enum = _revit_type("RebarStyle")
    style = getattr(style_enum, plan.style)
    hook_start = hooks.get(plan.hook_start) if plan.hook_start else None
    hook_end = hooks.get(plan.hook_end) if plan.hook_end else None
    normal = XYZ(*frame.vector_to_model(plan.normal))
    rebar = create_rebar_from_curves(
        doc, style, bar_type, host, normal, _model_curves(frame, plan.points),
        hook_start=hook_start, hook_end=hook_end,
        orient_start=plan.orient_start, orient_end=plan.orient_end,
        use_existing_shape=True, create_new_shape=True)
    if rebar is None:
        raise RuntimeError(u"Revit returned no rebar")
    _apply_layout(rebar, plan)
    return rebar


def create_plans(doc, host, section, plans, types, add_to_assembly=True,
                 created=None, host_label=None):
    """Create every BarPlan of one host in ONE Transaction (rule A6, spec D9).

    Run inside the caller's TransactionGroup. On the first failing plan the
    host's Transaction is rolled back and every row of the host reports why -
    other hosts are unaffected. With ``add_to_assembly`` the new bars join the
    host's assembly in the same Transaction (rule A2).

    Returns [Result]: one per plan (count = bars) plus an "Assembly" row when
    the host is in an assembly. ``created`` (optional list) receives the new ids.
    """
    from Autodesk.Revit.DB import Transaction
    label = host_label or elem_name(host)
    results = []
    new_ids = []
    frame = section.frame
    if frame is None:
        return [Result(label, STATUS_FAILED, 0, u"host geometry was not measured")]
    txn = Transaction(doc, u"Rebar Wizard: %s" % label)
    try:
        with disposing(txn) as t:
            t.Start()
            swallow = None
            try:
                swallow = _warning_swallower()()
                options = t.GetFailureHandlingOptions()
                options.SetFailuresPreprocessor(swallow)
                t.SetFailureHandlingOptions(options)
            except Exception:
                swallow = None          # Revit shows its own warning dialog instead
            for index, plan in enumerate(plans):
                try:
                    rebar = _create_one(doc, host, frame, plan, types)
                except Exception as exc:
                    t.RollBack()
                    rows = [Result(p.label, STATUS_FAILED, 0, u"rolled back with the host")
                            for p in plans[:index]]
                    rows.append(Result(plan.label, STATUS_FAILED, 0, short_error(exc)))
                    rows.extend(Result(p.label, STATUS_SKIPPED, 0, u"not created (host rolled back)")
                                for p in plans[index + 1:])
                    return rows
                new_ids.append(eid_value(rebar.Id))
                results.append(Result(plan.label, STATUS_OK, plan.bar_count,
                                      u"rebar %d" % new_ids[-1]))
            if add_to_assembly:
                added, assembly_id, error = add_to_assembly_of(doc, host, new_ids)
                if error:
                    results.append(Result(u"Assembly", STATUS_FAILED, 0,
                                          u"bars created but not added to assembly: %s" % error))
                elif assembly_id is not None:
                    results.append(Result(u"Assembly", STATUS_OK, added,
                                          u"%d rebar sets added to assembly %s"
                                          % (added, assembly_mark(doc, host) or assembly_id)))
            t.Commit()
            if swallow is not None and swallow.dismissed:
                results.append(Result(u"Warnings", STATUS_SKIPPED, swallow.dismissed,
                                      u"%d Revit warning(s) dismissed" % swallow.dismissed))
    except Exception as exc:
        return [Result(label, STATUS_FAILED, 0, short_error(exc))]
    if created is not None:
        created.extend(new_ids)
    return results
