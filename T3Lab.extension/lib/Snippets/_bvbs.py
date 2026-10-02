# -*- coding: utf-8 -*-
"""
_bvbs.py
========
BVBS (Bundesvereinigung der Bausoftware) writer — the ``.abs`` file that
bending machines read. Revit has no native BVBS export; Tekla users expect
one. Pure Python: no Revit import, so ``dev/test_bvbs_writer.py`` runs it
outside Revit. (``chain_to_shape`` imports ``Snippets._rebar`` lazily; that
module is import-safe without Revit too.)

Supported: ``BF2D`` blocks (planar bars). A block is::

    BF2D@Hj<project>@r<plan>@i<index>@p<position>@l<length>@n<quantity>@e<weight>@d<diameter>@g<grade>@s<mandrel dia>@v<>@
    Gl<L1>@w<A1>@l<L2>@w<A2>@...@l<Ln>@w0@
    C<checksum>@

Every field ends with ``@``; the block is one line terminated by CR LF. The
header fields permitted for BF2D, in this order, are j r i p l n e d g s v
(then optional a / t / c) — ``m`` is a mesh-only (BFMA/BFGT/BFAU) field and
must NOT appear in a BF2D header (BVBS-Guideline 3.1, "Header block").

Checksum (CONFIRMED 2026-10-02 against the BVBS-Guideline "Data exchange of
reinforcement data" v3.1, section "Checksum block", and its worked examples):
sum the ASCII codes of every character from the start of the record up to and
**including** the ``C`` that opens the checksum block, then
``checksum = 96 - (sum % 32)``. Guideline example: ``"abcde@C"`` -> 78.
Reference line from the guideline (example 1), used by dev/test_bvbs_writer.py::

    BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@Gl400@w90@l600@w0@C72@

(excluding the ``C`` from the sum would give 75, so the rule is unambiguous).

Lengths in the geometry block are OUTER (out-to-out) dimensions, not centreline
lengths (guideline, "Basic conditions"); ``Snippets._rebar.outer_legs`` does
that conversion before records reach this module. Segments arrive as
``[(length_mm, angle_deg), ...]`` where the angle is the bend *after* that
segment (0 for the last one). Positive angle = bend to the left when
travelling along the bar; the caller fixes the sign convention.

Facts from the guideline that shape this module (2026-10-02 reading of v3.1):

* "The total length of the bended reinforcing bar is calculated from the data
  of the geometry block, the length specifications in the header ... is to be
  ignored." So ``l`` in the header is informational; the geometry decides.
* Every header field must be present, in order, even when empty (``v@``).
* Only ASCII is allowed and ``@`` must not occur in free text. Text is
  therefore sanitised BEFORE the checksum is computed - a character replaced
  afterwards (when encoding the file) would silently invalidate the checksum.
* Weight ``e`` is printed with three decimals in all five worked examples
  (``e0.710``, not ``e0.71``); this writer does the same.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"

import json
import math
import os
import re
import unicodedata

# Lengths are written in whole millimetres, angles with at most one decimal —
# that is what the machines parse. Anything finer is rounded, never truncated.

FIELD_SEP = "@"
BLOCK_2D = "BF2D"
HEADER_ORDER = "jriplnedgsv"          # BF2D header fields, in the order written

# BVBS-Guideline 3.1, BF2D example 1 — the writer must reproduce this line
# byte for byte, and verify_block() must accept it. The BVBS dialog runs this
# self-test when it opens and refuses to export when it fails.
REFERENCE_LINE_1 = ("BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@"
                    "Gl400@w90@l600@w0@C72@")

# BVBS-Guideline 3.1, BF2D example 3 (a bar with 45 degree legs and negative
# angles) — second anchor of the self-test.
REFERENCE_LINE_3 = ("BF2D@HjTestPDF@r417@ia@p1@l1224@n10@e1.087@d12@gB500A@s48@v@"
                    "Gl100@w90@l300@w45@l424@w-45@l300@w-90@l100@w0@C82@")

SELF_TEST_MESSAGE = (u"BVBS self-test failed: the writer no longer matches the reference "
                     u"line from the BVBS guideline. Do not send this file to a machine "
                     u"— report this to T3Lab.")

WEIGHT_STEEL_FORMULA = 0.006165        # kg/m per mm^2 of diameter squared

# Source labels shown in the dialog (D13). Keys are what resolve_weight returns.
WEIGHT_SOURCE_LABELS = {
    "revit": u"Revit mass (2027)",
    "T3_WeightPerMetre": u"T3_WeightPerMetre",
    "table": u"default table (TCVN/BS)",
    "formula": u"formula 0.006165 x D^2",
}

_WINDOWS_RESERVED = ("CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4",
                     "LPT1", "LPT2", "LPT3")

# Letters that do not decompose under NFKD but have an obvious ASCII twin.
_ASCII_TWINS = {u"đ": u"d", u"Đ": u"D", u"Ø": u"O", u"ø": u"o",
                u"ß": u"ss", u"æ": u"ae", u"Æ": u"AE",
                u"ł": u"l", u"Ł": u"L"}


# ── TEXT & NUMBER FORMATTING ─────────────────────────────────────────────────

def ascii_text(text):
    """Pure ASCII with no ``@`` and no control characters; length preserved
    as far as possible (accents are stripped, unknown letters become ``?``)."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    out = []
    for ch in text:
        if ch in _ASCII_TWINS:
            out.append(_ASCII_TWINS[ch])
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
        if not stripped:
            continue                       # a bare combining mark
        for c in stripped:
            code = ord(c)
            if c == FIELD_SEP or c in "\r\n\t" or code < 32 or code == 127:
                out.append(" ")
            elif code > 126:
                out.append("?")
            else:
                out.append(c)
    return "".join(out)


def _clean(text):
    """Field text: ASCII only, no ``@``, no line breaks, trimmed."""
    return ascii_text(text).strip()


def _int(value):
    """Whole millimetres, round-half-up (deterministic, unlike banker's rounding)."""
    return str(int(math.floor(float(value or 0) + 0.5)))


def _mm(value):
    """Diameter / mandrel in mm: whole when it is whole (12), one decimal otherwise (12.7).

    The guideline asks to avoid decimals for millimetres; a 12.7 mm bar must still
    not be written as 13.
    """
    number = float(value or 0)
    if abs(number - round(number)) < 0.05:
        return _int(number)
    return ("%.1f" % number).rstrip("0").rstrip(".")


def _angle(value):
    """Bend angle: at most one decimal, no trailing zero, never ``-0``."""
    text = "%.1f" % float(value or 0)
    if text.endswith(".0"):
        text = text[:-2]
    return "0" if text in ("-0", "-0.0") else text


def _num(value, digits=3):
    """Weight: fixed `digits` decimals (the guideline prints ``e0.710``)."""
    return ("%." + str(digits) + "f") % float(value or 0)


def checksum(block_upto_c):
    """BVBS checksum for the text ending with (and including) the opening ``C``."""
    total = sum(ord(ch) for ch in block_upto_c)
    return 96 - (total % 32)


# ── RECORD ───────────────────────────────────────────────────────────────────

class BarRecord(object):
    """One position (mark) to write. Lengths in mm, weight in kg per bar.

    ``group`` is not written to the file; it names the assembly the bars belong
    to so ``plan_files`` can split the output into one file per assembly.
    """

    def __init__(self, mark, diameter_mm, quantity, segments, project="", plan="",
                 index="", weight_kg=0.0, grade="", roll_diameter_mm=0.0,
                 total_length_mm=None, group=""):
        self.mark = mark
        self.diameter_mm = diameter_mm
        self.quantity = quantity
        self.segments = list(segments or [])
        self.project = project
        self.plan = plan
        self.index = index
        self.weight_kg = weight_kg
        self.grade = grade
        self.roll_diameter_mm = roll_diameter_mm
        self.total_length_mm = total_length_mm
        self.group = group

    @property
    def length_mm(self):
        if self.total_length_mm is not None:
            return self.total_length_mm
        return sum(seg[0] for seg in self.segments)

    def problems(self):
        """Why this record cannot be written; empty list when it can."""
        out = []
        if not self.segments:
            out.append("no straight segments")
        if any(seg[0] <= 0 for seg in self.segments):
            out.append("segment of zero length")
        if any(len(seg) > 1 and abs(float(seg[1])) > 180.0 for seg in self.segments):
            out.append("bend angle beyond 180 degrees")
        if not self.diameter_mm or self.diameter_mm <= 0:
            out.append("no bar diameter")
        if not self.quantity or self.quantity <= 0:
            out.append("quantity is zero")
        if not _clean(self.mark):
            out.append("no mark")
        return out


def header_fields(rec):
    return [
        ("j", _clean(rec.project)),
        ("r", _clean(rec.plan)),
        ("i", _clean(rec.index)),
        ("p", _clean(rec.mark)),
        ("l", _int(rec.length_mm)),
        ("n", _int(rec.quantity)),
        ("e", _num(rec.weight_kg)),
        ("d", _mm(rec.diameter_mm)),
        ("g", _clean(rec.grade)),
        ("s", _mm(rec.roll_diameter_mm)),
        ("v", ""),
    ]


def geometry_fields(segments):
    """``l``/``w`` pairs; the last segment carries no bend."""
    out = []
    count = len(segments)
    for i, seg in enumerate(segments):
        length, angle = seg[0], seg[1] if len(seg) > 1 else 0
        out.append(("l", _int(length)))
        out.append(("w", _angle(angle) if i < count - 1 else "0"))
    return out


def _join(prefix, fields):
    """``H`` / ``G`` opens the block and glues onto its first field: ``Hj..@r..@``."""
    text = prefix
    for key, value in fields:
        text += key + value + FIELD_SEP
    return text


def write_block(rec):
    """One complete ``BF2D`` line (without line break). Raises on bad data."""
    issues = rec.problems()
    if issues:
        raise ValueError("; ".join(issues))
    body = BLOCK_2D + FIELD_SEP
    body += _join("H", header_fields(rec))
    body += _join("G", geometry_fields(rec.segments))
    body += "C"
    return body + str(checksum(body)) + FIELD_SEP


def verify_block(line):
    """True when the line's checksum matches its content."""
    line = line.strip()
    pos = line.rfind("C")
    if pos < 0 or not line.endswith(FIELD_SEP):
        return False
    try:
        stated = int(line[pos + 1:-1])
    except ValueError:
        return False
    return stated == checksum(line[:pos + 1])


def parse_block(line):
    """Split one BF2D line back into its parts; raises ValueError when it is malformed.

    Returns ``{"header": [(key, value), ...], "segments": [(l, w), ...],
    "checksum": int}``. Checks the structure the guideline demands: header
    fields exactly in ``HEADER_ORDER`` (so a stray ``m`` is caught), an even
    ``l``/``w`` geometry sequence ending in ``w0``, one checksum block.
    """
    text = line.strip()
    if not text.startswith(BLOCK_2D + FIELD_SEP + "H"):
        raise ValueError("does not start with BF2D@H")
    if not text.endswith(FIELD_SEP):
        raise ValueError("does not end with @")
    tokens = text.split(FIELD_SEP)[1:-1]
    header, geometry, check = [], [], None
    block = None
    for token in tokens:
        if not token:
            raise ValueError("empty field")
        if token[0].isupper():
            block, token = token[0], token[1:]
            if block not in "HGC":
                raise ValueError("unexpected block %s" % block)
            if block == "C":
                if check is not None or not token.isdigit():
                    raise ValueError("bad checksum block")
                check = int(token)
                continue
            if not token:
                if block == "H":
                    raise ValueError("empty header block")
                continue
        if block is None or block == "C":
            raise ValueError("field outside a block")
        key, value = token[0], token[1:]
        (header if block == "H" else geometry).append((key, value))
    if check is None:
        raise ValueError("no checksum block")
    if "".join(k for k, _ in header) != HEADER_ORDER:
        raise ValueError("header fields %r are not %r" % ("".join(k for k, _ in header),
                                                          HEADER_ORDER))
    if not geometry or len(geometry) % 2:
        raise ValueError("geometry block is not l/w pairs")
    segments = []
    for (lk, lv), (wk, wv) in zip(geometry[0::2], geometry[1::2]):
        if lk != "l" or wk != "w":
            raise ValueError("geometry fields are not in l/w order")
        segments.append((int(lv), float(wv)))
    if segments[-1][1] != 0:
        raise ValueError("the last leg must end with w0")
    return {"header": header, "segments": segments, "checksum": check}


def lines_for(records):
    """([line, ...], [(mark, reason), ...]) for the records that can be written."""
    lines, skipped = [], []
    for rec in records:
        try:
            lines.append(write_block(rec))
        except ValueError as exc:
            skipped.append((rec.mark, str(exc)))
    return lines, skipped


def _write_lines(path, lines):
    # Every header text already went through ascii_text(), so nothing here can
    # fail to encode. "replace" is only the last line of defence: should a
    # character ever slip through, the file differs from the lines the checksum
    # was computed on and verify_file() reports it instead of an exception
    # killing the export half-way.
    with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
        for line in lines:
            fh.write(line + "\n")


def write_file(path, records):
    """Write records; returns (written, skipped[(mark, reason)])."""
    lines, skipped = lines_for(records)
    _write_lines(path, lines)
    return len(lines), skipped


def verify_file(path, records=None):
    """Read `path` back and list everything wrong with it; ``[]`` means verified.

    Checks, in order: the file exists and is ASCII; every line ends with CR LF
    (no bare LF); every line passes ``verify_block`` and ``parse_block``; and,
    when `records` are given, the line count matches and each line is
    byte-identical to what ``write_block`` produces for its record.
    """
    problems = []
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except (IOError, OSError) as exc:
        return ["cannot read the file back: %s" % exc]
    if not data:
        return ["the file is empty"]
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return ["the file contains non-ASCII bytes"]
    if not text.endswith("\r\n"):
        problems.append("the last line has no CR LF")
    if re.search(r"(?<!\r)\n", text):
        problems.append("a line break is a bare LF, not CR LF")
    lines = text.split("\r\n")
    if lines and lines[-1] == "":
        lines.pop()
    expected = None
    if records is not None:
        expected, _ = lines_for(records)
        if len(lines) != len(expected):
            problems.append("%d lines read back, %d expected" % (len(lines), len(expected)))
    for number, line in enumerate(lines, 1):
        if not verify_block(line):
            problems.append("line %d: checksum does not match" % number)
            continue
        try:
            parse_block(line)
        except ValueError as exc:
            problems.append("line %d: %s" % (number, exc))
            continue
        if expected is not None and number <= len(expected) and line != expected[number - 1]:
            problems.append("line %d: differs from the record it was written from" % number)
    return problems


def write_verified(path, records):
    """Write `records` to `path` only if the file reads back clean.

    The lines go to ``<path>.tmp`` first; the tmp file is read back with
    ``verify_file`` and only then moved over `path` (so a failed write never
    replaces an existing good file or leaves a bad ``.abs`` behind). The final
    file is read back once more. Returns ``(written, skipped, problems)``;
    `problems` is empty when the file on disk is verified.
    """
    lines, skipped = lines_for(records)
    if not lines:
        return 0, skipped, ["nothing to write"]
    good = [rec for rec in records if not rec.problems()]
    tmp = path + ".tmp"
    try:
        _write_lines(tmp, lines)
    except (IOError, OSError) as exc:
        return 0, skipped, ["cannot write %s: %s" % (tmp, exc)]
    problems = verify_file(tmp, good)
    if problems:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return 0, skipped, problems
    try:
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return 0, skipped, ["cannot move the file into place at %s: %s" % (path, exc)]
    problems = verify_file(path, good)
    return (len(lines) if not problems else 0), skipped, problems


# ── SELF-TEST ────────────────────────────────────────────────────────────────

def reference_record():
    """BarRecord for guideline example 1 (the self-test anchor)."""
    return BarRecord("1", 12, 10, [(400, 90), (600, 0)], project="TestPDF", plan="417",
                     index="a", weight_kg=0.888, grade="B500A", roll_diameter_mm=48)


def reference_record_3():
    """BarRecord for guideline example 3 (45 degree legs, negative angles)."""
    return BarRecord("1", 12, 10, [(100, 90), (300, 45), (424, -45), (300, -90), (100, 0)],
                     project="TestPDF", plan="417", index="a", weight_kg=1.087,
                     grade="B500A", roll_diameter_mm=48)


def self_test():
    """(ok, message). Run when the dialog opens; a failure blocks the export.

    Checks that the guideline's reference lines pass ``verify_block`` AND are
    reproduced byte for byte by ``write_block`` — so neither the checksum rule
    nor the field order / number formatting has drifted.
    """
    try:
        if not verify_block(REFERENCE_LINE_1) or not verify_block(REFERENCE_LINE_3):
            return False, SELF_TEST_MESSAGE
        if write_block(reference_record()) != REFERENCE_LINE_1:
            return False, SELF_TEST_MESSAGE
        if write_block(reference_record_3()) != REFERENCE_LINE_3:
            return False, SELF_TEST_MESSAGE
        if checksum("abcde@C") != 78:
            return False, SELF_TEST_MESSAGE
    except Exception:
        return False, SELF_TEST_MESSAGE
    return True, ""


# ── WEIGHT (D13) ─────────────────────────────────────────────────────────────

def default_weight_per_metre(diameter_mm, table=None):
    """kg/m from a {diameter: kg_per_m} table, else the steel formula.

    The formula is 0.006165 * d^2 (kg/m, d in mm) — the usual value used when
    a bar type carries no weight parameter. A project table wins over it.
    """
    return weight_from_table(diameter_mm, table)[0]


def weight_from_table(diameter_mm, table=None):
    """(kg/m, "table" | "formula") for a nominal diameter."""
    if table:
        key = str(int(math.floor(float(diameter_mm) + 0.5)))
        if key in table:
            return float(table[key]), "table"
        if diameter_mm in table:
            return float(table[diameter_mm]), "table"
    return WEIGHT_STEEL_FORMULA * float(diameter_mm) ** 2, "formula"


def load_weight_table(path=None):
    """{"12": 0.888, ...} from lib/data/rebar_weights.json; ``{}`` when unreadable."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "rebar_weights.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        table = data.get("kg_per_m", {})
        return {str(k): float(v) for k, v in table.items() if float(v) > 0}
    except Exception:
        return {}


def resolve_weight(diameter_mm, revit_value=None, revit_source=None, table=None):
    """D13 chain -> (kg/m, source key).

    1. `revit_value` / `revit_source` from ``_compat.bar_mass_per_metre`` (Revit
       2027 native mass, else the T3_WeightPerMetre parameter) when positive;
    2. the nominal-diameter table; 3. ``0.006165 * d^2``.
    The key indexes ``WEIGHT_SOURCE_LABELS``.
    """
    try:
        if revit_value is not None and float(revit_value) > 0 and revit_source:
            return float(revit_value), revit_source
    except (TypeError, ValueError):
        pass
    return weight_from_table(diameter_mm, table)


def bar_weight_kg(length_mm, kg_per_metre):
    """Weight of one bar of `length_mm`."""
    return float(kg_per_metre) * float(length_mm) / 1000.0


def weight_caption(counts):
    """'Weight: Revit mass (2027)' from {source key: bar count}; mixed sources are listed."""
    used = [(key, n) for key, n in counts.items() if n]
    if not used:
        return u"Weight: no bars yet"
    if len(used) == 1:
        return u"Weight: %s" % WEIGHT_SOURCE_LABELS.get(used[0][0], used[0][0])
    used.sort(key=lambda kv: -kv[1])
    return u"Weight: " + u" · ".join(
        u"%s for %d" % (WEIGHT_SOURCE_LABELS.get(k, k), n) for k, n in used)


# ── MARK, SHAPE, GROUPING ────────────────────────────────────────────────────

def build_mark(partition, number, schedule_mark):
    """``<partition>-<number>``, or the Schedule Mark when the number is empty."""
    number = (number or u"").strip()
    partition = (partition or u"").strip()
    if number:
        return u"%s-%s" % (partition, number) if partition else number
    return (schedule_mark or u"").strip()


def legs_text(segments):
    """``400/90/600``: leg, bend angle, leg ... (outer dimensions, as written)."""
    parts = []
    last = len(segments) - 1
    for i, seg in enumerate(segments):
        parts.append(_int(seg[0]))
        if i < last:
            parts.append(_angle(seg[1]))
    return "/".join(parts)


class BarShape(object):
    """Result of ``chain_to_shape``: either ``segments`` or a ``reason`` to skip.

    ``error`` carries the Revit message when the reason is an unreadable
    centre-line (set by the caller that made the API call).
    """

    def __init__(self, segments=None, reason=u"", centre_legs=None, outer_sum=0.0,
                 bends=0, error=u""):
        self.segments = list(segments or [])
        self.reason = reason
        self.centre_legs = list(centre_legs or [])
        self.outer_sum = outer_sum
        self.bends = bends
        self.error = error

    @property
    def ok(self):
        return bool(self.segments) and not self.reason

    def key(self):
        """Hashable identity of the shape as written (lengths + angles)."""
        return tuple((int(seg[0]), round(float(seg[1]), 1)) for seg in self.segments)


def chain_to_shape(curves_info, diameter_mm, planar_tol_mm=1.0):
    """D2 + D14 for one bar position: centre-line curves -> outer BVBS segments.

    ``curves_info`` is ``[('Line', p0, p1) | ('Arc', p0, p1, pm)]`` in mm, as
    ``_rebar.centerline_points`` returns it. The reason text is what follows
    ``Skipped:`` in the preview row.
    """
    from Snippets import _rebar
    if not curves_info:
        return BarShape(reason=u"no centre-line")
    kind = _rebar.classify_centerline(curves_info, planar_tol_mm)
    if kind == "non_planar":
        return BarShape(reason=u"free-form 3D")
    if kind == "planar_with_arcs":
        return BarShape(reason=u"curved leg")
    try:
        points = _rebar.curves_to_polyline(curves_info)
    except ValueError:
        return BarShape(reason=u"broken centre-line")
    chain = _rebar.centerline_to_legs(points, planar_tol_mm)
    if not chain.planar:
        return BarShape(reason=u"free-form 3D")
    outer = _rebar.outer_legs(chain.legs, diameter_mm)
    segments = _rebar.legs_to_bvbs_segments(outer)
    if not segments or any(seg[0] <= 0 for seg in segments):
        return BarShape(reason=u"zero-length leg")
    return BarShape(segments=segments, centre_legs=chain.legs,
                    outer_sum=sum(length for length, _ in outer),
                    bends=len(segments) - 1)


def group_distinct(shapes):
    """[(BarShape, count)] — equal shapes collapsed, first-seen order kept."""
    order, counts = [], {}
    for shape in shapes:
        key = shape.key()
        if key not in counts:
            counts[key] = [shape, 0]
            order.append(key)
        counts[key][1] += 1
    return [(counts[key][0], counts[key][1]) for key in order]


def sample_positions(existing, full_up_to=12):
    """Bar positions worth reading for a set: all of them for a small set, else
    first / middle / last (R11: one centre-line call per bar is too slow for
    thousands). The caller widens to every position when the samples differ."""
    existing = list(existing)
    if len(existing) <= full_up_to:
        return existing
    return [existing[0], existing[len(existing) // 2], existing[-1]]


def length_check(revit_length_mm, outer_sum_mm, bends, diameter_mm, roll_diameter_mm=0.0):
    """Plausibility of Revit's bar length against the outer legs: (ok, text).

    A cut length can never exceed the sum of the outer legs (corners only
    shorten it) and falls short by a bounded amount per bend. A length outside
    that window means the legs written to the file do not match the bar Revit
    schedules - exactly the mistake a bending machine cannot recover from.
    """
    if not revit_length_mm or revit_length_mm <= 0:
        return True, u""
    roll = roll_diameter_mm if roll_diameter_mm and roll_diameter_mm > 0 else 4.0 * diameter_mm
    slack_up = 2.0
    slack_down = bends * (roll / 2.0 + 2.0 * diameter_mm) + 5.0
    if revit_length_mm > outer_sum_mm + slack_up:
        return False, (u"Revit length %d mm is longer than the outer legs add up to (%d mm)"
                       % (int(round(revit_length_mm)), int(round(outer_sum_mm))))
    if revit_length_mm < outer_sum_mm - slack_down:
        return False, (u"Revit length %d mm is much shorter than the outer legs (%d mm)"
                       % (int(round(revit_length_mm)), int(round(outer_sum_mm))))
    return True, u""


# ── FILES ────────────────────────────────────────────────────────────────────

def safe_file_name(text, default="BVBS"):
    """A file-name stem: letters, digits, ``.``, ``_``, ``-`` only; never a Windows device name."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_text(text)).strip("._-")
    if not stem:
        stem = default
    if stem.upper() in _WINDOWS_RESERVED:
        stem += "_"
    return stem[:80]


def plan_files(records, per_assembly, plan, loose_label="unassigned"):
    """[(file name, [records])] — one file, or one per ``record.group`` (A8).

    Names are ``<plan>.abs`` or ``<plan>_<assembly mark>.abs``; bars with no
    assembly go to ``<plan>_unassigned.abs``. Equal names get ``_2``, ``_3``.
    """
    records = list(records)
    base = safe_file_name(plan, "MODEL")
    if not per_assembly:
        return [(base + ".abs", records)] if records else []
    buckets, order = {}, []
    for rec in records:
        label = (rec.group or "").strip() or loose_label
        if label not in buckets:
            buckets[label] = []
            order.append(label)
        buckets[label].append(rec)
    out, used = [], {}
    for label in order:
        name = "%s_%s" % (base, safe_file_name(label, loose_label))
        used[name] = used.get(name, 0) + 1
        if used[name] > 1:
            name = "%s_%d" % (name, used[name])
        out.append((name + ".abs", buckets[label]))
    return out
