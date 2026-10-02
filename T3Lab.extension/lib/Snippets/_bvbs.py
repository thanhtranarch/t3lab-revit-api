# -*- coding: utf-8 -*-
"""
_bvbs.py
========
BVBS (Bundesvereinigung der Bausoftware) writer — the ``.abs`` file that
bending machines read. Revit has no native BVBS export; Tekla users expect
one. Pure Python: no Revit import, so ``dev/test_bvbs_writer.py`` runs it
outside Revit.

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
lengths (guideline, "General"); ``Snippets._rebar.outer_legs`` does that
conversion before records reach this module. Segments arrive as
``[(length_mm, angle_deg), ...]`` where the angle is the bend *after* that
segment (0 for the last one). Positive angle = bend to the left when
travelling along the bar; the caller fixes the sign convention.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"

# Lengths are written in whole millimetres, angles in whole degrees — that is
# what the machines parse. Anything finer is rounded, never truncated.

FIELD_SEP = "@"
BLOCK_2D = "BF2D"

# BVBS-Guideline 3.1, BF2D example 1 — the writer must reproduce this line
# byte for byte, and verify_block() must accept it. The BVBS dialog runs this
# self-test when it opens and refuses to export when it fails.
REFERENCE_LINE_1 = ("BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@"
                    "Gl400@w90@l600@w0@C72@")


def _clean(text):
    """Strip ``@`` and line breaks — both would corrupt the block."""
    if text is None:
        return ""
    text = str(text)
    for bad in ("@", "\r", "\n"):
        text = text.replace(bad, " ")
    return text.strip()


def _int(value):
    return str(int(round(float(value or 0))))


def _num(value, digits=3):
    """Weight-like decimals: at most `digits` decimals, no trailing zeros."""
    text = ("%." + str(digits) + "f") % float(value or 0)
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def checksum(block_upto_c):
    """BVBS checksum for the text ending with (and including) the opening ``C``."""
    total = sum(ord(ch) for ch in block_upto_c)
    return 96 - (total % 32)


class BarRecord(object):
    """One position (mark) to write. Lengths in mm, weight in kg per bar."""

    def __init__(self, mark, diameter_mm, quantity, segments, project="", plan="",
                 index="", weight_kg=0.0, grade="", roll_diameter_mm=0.0,
                 total_length_mm=None):
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
        ("d", _int(rec.diameter_mm)),
        ("g", _clean(rec.grade)),
        ("s", _int(rec.roll_diameter_mm)),
        ("v", ""),
    ]


def geometry_fields(segments):
    """``l``/``w`` pairs; the last segment carries no bend."""
    out = []
    count = len(segments)
    for i, seg in enumerate(segments):
        length, angle = seg[0], seg[1] if len(seg) > 1 else 0
        out.append(("l", _int(length)))
        out.append(("w", _int(angle) if i < count - 1 else "0"))
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


def write_file(path, records):
    """Write records; returns (written, skipped[(mark, reason)])."""
    written, skipped = 0, []
    lines = []
    for rec in records:
        try:
            lines.append(write_block(rec))
            written += 1
        except ValueError as exc:
            skipped.append((rec.mark, str(exc)))
    with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
        for line in lines:
            fh.write(line + "\n")
    return written, skipped


def default_weight_per_metre(diameter_mm, table=None):
    """kg/m from a {diameter: kg_per_m} table, else the steel formula.

    The formula is 0.006165 * d^2 (kg/m, d in mm) — the usual value used when
    a bar type carries no weight parameter. A project table wins over it.
    """
    if table:
        key = str(int(round(diameter_mm)))
        if key in table:
            return float(table[key])
        if diameter_mm in table:
            return float(table[diameter_mm])
    return 0.006165 * float(diameter_mm) ** 2
