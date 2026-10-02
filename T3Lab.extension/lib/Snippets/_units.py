# -*- coding: utf-8 -*-
"""
_units.py
=========
Project length units for T3Lab tools.

Tools used to hard-code millimetres: "HEIGHT (MM)", defaults like 2800, and
`value / 304.8` before calling the API. In a project set up in feet-inches or
metres that is wrong twice — the label lies, and a typed "9" becomes 9 mm.
This module reads the length unit the PROJECT displays (Manage › Project
Units › Length) and gives every tool the same three things:

* a label suffix   — ``unit.tag`` ("mm", "m", "ft", "ft-in", …) / ``unit.TAG``
* display text     — ``unit.text(feet)`` for a TextBox, rounded the way that
                     unit is normally written (3' - 6 1/2", 1.250 m, 2800)
* parsing          — ``unit.parse(text)`` → internal feet. A bare number is in
                     the project unit; an explicit unit always wins, so
                     "1200 mm", "1.2m", "3'-6\"", "42\"" and "3ft 6in" all work
                     in any project. A comma decimal ("2,5") is accepted.

Defaults written in mm (the way the code thinks) are shown in the project
unit with ``unit.default_text(2800)``.

The core is pure Python (tested by dev/test_units.py). Only
``project_length_unit(doc)`` touches the Revit API (ForgeTypeId, Revit
2021+, so every supported release 2022–2027).

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

import re

MM_PER_FT = 304.8

# ForgeTypeId unit name (between "unit:" and the version) →
# (tag shown in labels, feet per unit, decimals in text boxes, style)
# style: "decimal", "fraction" (inches with 1/8"), "ftin" (feet + fractional inches)
_UNITS = {
    "millimeters":          ("mm", 1.0 / 304.8, 0, "decimal"),
    "centimeters":          ("cm", 1.0 / 30.48, 1, "decimal"),
    "decimeters":           ("dm", 1.0 / 3.048, 2, "decimal"),
    "meters":               ("m", 1.0 / 0.3048, 3, "decimal"),
    "metersCentimeters":    ("m", 1.0 / 0.3048, 3, "decimal"),
    "feet":                 ("ft", 1.0, 2, "decimal"),
    "usSurveyFeet":         ("ft", 1200.0 / 3937.0 / 0.3048, 2, "decimal"),
    "inches":               ("in", 1.0 / 12.0, 2, "decimal"),
    "fractionalInches":     ("in", 1.0 / 12.0, 0, "fraction"),
    "feetFractionalInches": ("ft-in", 1.0, 0, "ftin"),
}

# Explicit units a user may type after a number (any project).
_SUFFIX_FT = {
    "mm": 1.0 / 304.8, "cm": 1.0 / 30.48, "dm": 1.0 / 3.048, "m": 1.0 / 0.3048,
    "ft": 1.0, "feet": 1.0, "foot": 1.0, "'": 1.0,
    "in": 1.0 / 12.0, "inch": 1.0 / 12.0, "inches": 1.0 / 12.0, '"': 1.0 / 12.0,
}

_NUM = r"[-+]?\d+(?:[.,]\d+)?(?:\s+\d+/\d+)?|[-+]?\d+/\d+"


def _number(token):
    """'2,5' → 2.5, '6 1/2' → 6.5, '1/4' → 0.25."""
    token = token.strip()
    sign = -1.0 if token.startswith("-") else 1.0
    token = token.lstrip("+-").strip()
    whole, frac = token, None
    m = re.match(r"^(\d+(?:[.,]\d+)?)\s+(\d+)/(\d+)$", token)
    if m:
        whole, frac = m.group(1), (float(m.group(2)) / float(m.group(3)))
    else:
        m = re.match(r"^(\d+)/(\d+)$", token)
        if m:
            return sign * float(m.group(1)) / float(m.group(2))
    value = float(whole.replace(",", "."))
    if frac:
        value += frac
    return sign * value


def _fraction_text(inches, denominator=8):
    """6.53 → '6 1/2', 0.125 → '1/8', 7.0 → '7' (nearest 1/denominator)."""
    sign = "-" if inches < 0 else ""
    eighths = int(round(abs(inches) * denominator))
    whole, rest = divmod(eighths, denominator)
    if rest:
        num, den = rest, denominator
        while num % 2 == 0 and den % 2 == 0:
            num //= 2
            den //= 2
        frac = "{}/{}".format(num, den)
        return sign + ("{} {}".format(whole, frac) if whole else frac)
    return sign + str(whole)


class LengthUnit(object):
    """One project length unit: label tag, conversion, display and parsing."""

    def __init__(self, key="millimeters"):
        if key not in _UNITS:
            key = "millimeters"
        self.key = key
        self.tag, self.ft_per_unit, self.decimals, self.style = _UNITS[key]

    # ── labels ───────────────────────────────────────────────────────────
    @property
    def TAG(self):
        return self.tag.upper()

    @property
    def is_metric(self):
        return self.key in ("millimeters", "centimeters", "decimeters",
                            "meters", "metersCentimeters")

    def label(self, text, upper=None):
        """'Height' → 'Height (mm)'; an all-caps text gets an all-caps tag."""
        if upper is None:
            upper = text == text.upper()
        return "{} ({})".format(text, self.TAG if upper else self.tag)

    # ── conversion ───────────────────────────────────────────────────────
    def to_feet(self, value):
        return float(value) * self.ft_per_unit

    def from_feet(self, feet):
        return float(feet) / self.ft_per_unit

    # ── display ──────────────────────────────────────────────────────────
    def text(self, feet):
        """Internal feet → text for a TextBox, in this unit, nicely rounded."""
        feet = float(feet)
        if self.style == "ftin":
            sign = "-" if feet < 0 else ""
            total_in = round(abs(feet) * 12.0 * 8) / 8.0
            ft, inch = divmod(total_in, 12.0)
            return "{}{}' - {}\"".format(sign, int(ft), _fraction_text(inch))
        if self.style == "fraction":
            return _fraction_text(feet * 12.0) + '"'
        value = self.from_feet(feet)
        if self.decimals == 0:
            return str(int(round(value)))
        text = "{:.{}f}".format(value, self.decimals)
        return text.rstrip("0").rstrip(".") if "." in text else text

    def show(self, feet):
        """Text for a sentence, with the unit: '2800 mm', '1.25 m',
        '9\' - 2 1/4"' (feet-inches and inches already carry ' and ")."""
        text = self.text(feet)
        if self.style == "decimal":
            return "{} {}".format(text, self.tag)
        return text

    def default_text(self, mm):
        """A default the code keeps in mm, shown in this unit."""
        return self.text(float(mm) / MM_PER_FT)

    # ── parsing ──────────────────────────────────────────────────────────
    def parse(self, text):
        """Text typed by the user → internal feet. Raises ValueError with a
        message that says what is accepted."""
        raw = (text or "").strip()
        if not raw:
            raise ValueError("Enter a length.")
        s = raw.replace("’", "'").replace("′", "'") \
               .replace("”", '"').replace("″", '"').lower()
        s = re.sub(r"\s+", " ", s)

        # feet + inches: 3'-6", 3' 6 1/2", 3ft 6in, 3'6"
        m = re.match(r"^({n})\s*(?:'|ft|feet|foot)\s*-?\s*({n})\s*(?:\"|in|inch|inches)?$"
                     .format(n=_NUM), s)
        if m:
            # The sign comes from the TEXT: "-0' - 2\"" has ft == -0.0, which is
            # not < 0, and used to come back as +2" (a beam offset 2" above the
            # level instead of below it).
            sign = -1.0 if m.group(1).strip().startswith("-") else 1.0
            ft = abs(_number(m.group(1)))
            inch = abs(_number(m.group(2)))
            return sign * (ft + inch / 12.0)

        # one number with an explicit unit
        m = re.match(r"^({n})\s*(mm|cm|dm|m|ft|feet|foot|'|in|inch|inches|\")$"
                     .format(n=_NUM), s)
        if m:
            return _number(m.group(1)) * _SUFFIX_FT[m.group(2)]

        # a bare number is in the project unit
        m = re.match(r"^({n})$".format(n=_NUM), s)
        if m:
            value = _number(m.group(1))
            if self.style == "fraction":
                return value / 12.0
            return value * self.ft_per_unit

        raise ValueError(
            "“{}” is not a length. Type a number in {} or add a unit, "
            "e.g. 1200 mm, 1.2 m, 3'-6\" or 42\".".format(raw, self.tag))

    def parse_mm(self, text):
        """Typed text → millimetres (for code that still works in mm)."""
        return self.parse(text) * MM_PER_FT

    def __repr__(self):
        return "LengthUnit({!r})".format(self.key)


def unit_from_type_id(type_id):
    """'autodesk.unit.unit:feetFractionalInches-1.0.1' → LengthUnit."""
    name = (type_id or "")
    if ":" in name:
        name = name.split(":", 1)[1]
    name = name.split("-", 1)[0]
    return LengthUnit(name if name in _UNITS else "millimeters")


MILLIMETERS = LengthUnit("millimeters")


def project_length_unit(doc):
    """The length unit `doc` displays (Project Units › Length); mm when the
    document or the API is not available."""
    if doc is None:
        return MILLIMETERS
    try:
        from Autodesk.Revit.DB import SpecTypeId
        options = doc.GetUnits().GetFormatOptions(SpecTypeId.Length)
        return unit_from_type_id(options.GetUnitTypeId().TypeId)
    except Exception:
        return MILLIMETERS


FRACTIONAL_INCHES = LengthUnit("fractionalInches")


def paper_unit(model_unit):
    """Unit for sizes measured ON PAPER (sheet text and headers, print
    margins, drafting fill-pattern spacing): they never switch to m or ft —
    mm in a metric project, inches in an imperial one."""
    if model_unit is None or model_unit.is_metric:
        return MILLIMETERS
    return FRACTIONAL_INCHES


def project_paper_unit(doc):
    return paper_unit(project_length_unit(doc))
