# -*- coding: utf-8 -*-
"""
Tests for Snippets/_bvbs.py: the BVBS BF2D writer, its checksum, the read-back
verification and the pure helpers behind the BVBS Export dialog.
Run: python dev/test_bvbs_writer.py

The five reference lines are the worked examples of the BVBS-Guideline "Data
exchange of reinforcement data" v3.1 (section "BF2D Examples"); a bending
machine file is only as good as these.
"""
import os
import shutil
import sys
import tempfile
import types
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


def _load_module(name):
    """Exec Snippets/<name>.py (the shipped source, not a copy).

    _bvbs is pure Python, so no Revit stub is needed; chain_to_shape imports
    Snippets._rebar lazily, which is import-safe without Revit as well.
    """
    path = os.path.join(LIB_DIR, 'Snippets', name + '.py')
    with open(path, 'r', encoding='utf-8') as handle:
        source = handle.read()
    module = types.ModuleType('t3_%s_under_test' % name)
    module.__file__ = path
    exec(compile(source, path, 'exec'), module.__dict__)
    return module


bvbs = _load_module('_bvbs')

# Guideline examples 1-5, header + geometry + checksum, without CR LF.
LINE_1 = ("BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@"
          "Gl400@w90@l600@w0@C72@")
LINE_2 = ("BF2D@HjTestPDF@r417@ia@p1@l800@n10@e0.710@d12@gB500A@s48@v@"
          "Gl100@w180@l600@w180@l100@w0@C71@")
LINE_3 = ("BF2D@HjTestPDF@r417@ia@p1@l1224@n10@e1.087@d12@gB500A@s48@v@"
          "Gl100@w90@l300@w45@l424@w-45@l300@w-90@l100@w0@C82@")
LINE_4 = ("BF2D@HjTestPDF@r417@ia@p1@l1428@n10@e1.268@d12@gB500A@s48@v@"
          "Gl400@w0@r400@w90@w0@l400@w0@C79@")
LINE_5 = ("BF2D@HjTestPDF@r417@ia@p1@l1428@n10@e1.268@d12@gB500A@s48@v@"
          "Gl400@w45@r400@w90@w45@l400@w0@C93@")


def _record(segments, length=None, weight=0.0, mark="1", **kwargs):
    fields = dict(project="TestPDF", plan="417", index="a", grade="B500A",
                  roll_diameter_mm=48, weight_kg=weight, total_length_mm=length)
    fields.update(kwargs)
    return bvbs.BarRecord(mark, 12, 10, segments, **fields)


class _TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t3_bvbs_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, name):
        return os.path.join(self.tmp, name)


class ChecksumTests(unittest.TestCase):
    def test_checksum_guideline_example(self):
        # Guideline "Checksum block": "abcde@C" -> 96 - (626 mod 32) = 78.
        self.assertEqual(bvbs.checksum("abcde@C"), 78)

    def test_checksum_includes_the_opening_C(self):
        # Without the C the sum is one ASCII code (67) shorter: 96 - 559%32 = 79.
        self.assertNotEqual(bvbs.checksum("abcde@"), bvbs.checksum("abcde@C"))
        line = LINE_1[:LINE_1.rindex("C") + 1]
        self.assertEqual(bvbs.checksum(line), 72)
        self.assertEqual(bvbs.checksum(line[:-1]), 75)       # the wrong rule would give 75

    def test_all_five_reference_lines_verify(self):
        for number, line in enumerate((LINE_1, LINE_2, LINE_3, LINE_4, LINE_5), 1):
            self.assertTrue(bvbs.verify_block(line), "fixture %d" % number)

    def test_verify_block_detects_corruption(self):
        # Every single-character change before the checksum value is caught, except
        # one whose ASCII code moves by a multiple of 32 (a case flip, a<->A): the
        # BVBS checksum is a sum mod 32, that blind spot is part of the format.
        for position in range(len(LINE_1) - 4):
            changed = LINE_1[:position] + chr(ord(LINE_1[position]) + 1) + LINE_1[position + 1:]
            self.assertFalse(bvbs.verify_block(changed), "char %d went unnoticed" % position)
        self.assertFalse(bvbs.verify_block(LINE_1.replace("C72@", "C73@")))
        self.assertFalse(bvbs.verify_block(LINE_1[:-1]))                # no closing @
        self.assertFalse(bvbs.verify_block("BF2D@Gl400@w0@"))           # no checksum block

    def test_reference_line_constants_match_the_guideline(self):
        self.assertEqual(bvbs.REFERENCE_LINE_1, LINE_1)
        self.assertEqual(bvbs.REFERENCE_LINE_3, LINE_3)


class WriterTests(unittest.TestCase):
    def test_fixture_1_to_3_roundtrip(self):
        # write_block must reproduce the guideline byte for byte.
        self.assertEqual(bvbs.write_block(_record([(400, 90), (600, 0)], weight=0.888)), LINE_1)
        self.assertEqual(bvbs.write_block(_record([(100, 180), (600, 180), (100, 0)],
                                                  weight=0.71)), LINE_2)
        self.assertEqual(bvbs.write_block(_record(
            [(100, 90), (300, 45), (424, -45), (300, -90), (100, 0)], weight=1.087)), LINE_3)

    @unittest.expectedFailure
    def test_fixture_4_arc_not_supported_yet(self):
        # Example 4 has an r400 arc leg; V1 writes straight legs only (D2).
        record = _record([(400, 0), (400, 90), (400, 0)], length=1428, weight=1.268)
        self.assertEqual(bvbs.write_block(record), LINE_4)

    @unittest.expectedFailure
    def test_fixture_5_arc_not_supported_yet(self):
        record = _record([(400, 45), (400, 90), (400, 0)], length=1428, weight=1.268)
        self.assertEqual(bvbs.write_block(record), LINE_5)

    def test_header_has_no_m_field(self):
        keys = "".join(key for key, _ in bvbs.header_fields(_record([(400, 90), (600, 0)])))
        self.assertEqual(keys, "jriplnedgsv")
        self.assertNotIn("m", keys)
        header = bvbs.write_block(_record([(400, 90), (600, 0)])).split("@G")[0]
        self.assertNotIn("@m", header)

    def test_weight_is_printed_with_three_decimals(self):
        line = bvbs.write_block(_record([(400, 90), (600, 0)], weight=0.71))
        self.assertIn("@e0.710@", line)
        line = bvbs.write_block(_record([(400, 90), (600, 0)], weight=12.5))
        self.assertIn("@e12.500@", line)

    def test_decimal_angles_are_kept_to_one_decimal(self):
        line = bvbs.write_block(_record([(100, 79.7), (200, -45.0), (300, 0)]))
        self.assertIn("@w79.7@", line)
        self.assertIn("@w-45@", line)
        self.assertTrue(bvbs.verify_block(line))

    def test_fractional_diameters_are_not_rounded_to_a_different_bar(self):
        fields = dict(bvbs.header_fields(bvbs.BarRecord("1", 12.7, 1, [(100, 0)],
                                                        roll_diameter_mm=50.8)))
        self.assertEqual((fields["d"], fields["s"]), ("12.7", "50.8"))
        fields = dict(bvbs.header_fields(bvbs.BarRecord("1", 12.0, 1, [(100, 0)],
                                                        roll_diameter_mm=48.0)))
        self.assertEqual((fields["d"], fields["s"]), ("12", "48"))

    def test_last_angle_is_always_zero(self):
        line = bvbs.write_block(_record([(400, 90), (600, 90)]))
        self.assertTrue(line.endswith("l600@w0@C%d@" % bvbs.checksum(line[:line.rindex("C") + 1])))

    def test_total_length_overrides_the_sum_of_legs(self):
        line = bvbs.write_block(_record([(400, 90), (600, 0)], length=975))
        self.assertIn("@l975@", line)

    def test_problems_lists_every_reason(self):
        bad = bvbs.BarRecord("", 0, 0, [(0, 90), (-5, 0)])
        problems = bad.problems()
        for fragment in ("segment of zero length", "no bar diameter", "quantity is zero",
                         "no mark"):
            self.assertIn(fragment, problems)
        self.assertIn("no straight segments", bvbs.BarRecord("1", 12, 1, []).problems())
        self.assertIn("bend angle beyond 180 degrees",
                      bvbs.BarRecord("1", 12, 1, [(100, 190), (100, 0)]).problems())
        self.assertEqual(_record([(400, 90), (600, 0)]).problems(), [])
        with self.assertRaises(ValueError):
            bvbs.write_block(bad)

    def test_at_sign_and_line_breaks_cannot_corrupt_a_field(self):
        line = bvbs.write_block(_record([(400, 90), (600, 0)], mark="A@1\r\nB", plan="x@y"))
        self.assertEqual(line.count("\n"), 0)
        self.assertIn("@pA 1  B@", line)
        self.assertIn("@rx y@", line)
        self.assertTrue(bvbs.verify_block(line))
        self.assertIsNotNone(bvbs.parse_block(line))


class AsciiTests(unittest.TestCase):
    def test_ascii_text_strips_accents_and_replaces_the_rest(self):
        self.assertEqual(bvbs.ascii_text(u"Dự án Đà Nẵng"), "Du an Da Nang")
        self.assertEqual(bvbs.ascii_text(u"Ø12"), "O12")
        self.assertEqual(bvbs.ascii_text(u"a中b"), "a?b")
        self.assertEqual(bvbs.ascii_text(None), "")

    def test_non_ascii_mark_keeps_the_checksum_valid(self):
        # The old writer summed the unicode text and then encoded '?' - a checksum
        # that no longer matched the bytes on disk.
        line = bvbs.write_block(_record([(400, 90), (600, 0)], mark=u"Été-中",
                                        project=u"Dự án"))
        line.encode("ascii")                                  # raises when not ASCII
        self.assertTrue(bvbs.verify_block(line))
        self.assertIn("@pEte-?@", line)
        self.assertIn("@HjDu an@", line)


class FileTests(_TmpDirCase):
    def test_write_file_crlf_and_ascii(self):
        target = self.path("out.abs")
        records = [_record([(400, 90), (600, 0)], weight=0.888, mark=u"É-1"),
                   _record([(100, 180), (600, 180), (100, 0)], weight=0.71, mark="2")]
        written, skipped = bvbs.write_file(target, records)
        self.assertEqual((written, skipped), (2, []))
        with open(target, "rb") as handle:
            data = handle.read()
        data.decode("ascii")
        self.assertTrue(data.endswith(b"\r\n"))
        self.assertEqual(data.count(b"\r\n"), 2)
        self.assertEqual(data.count(b"\n"), 2)                # no bare LF
        self.assertIn(b"@pE-1@", data)                        # non-ASCII mark replaced
        for line in data.decode("ascii").split("\r\n")[:-1]:
            self.assertTrue(bvbs.verify_block(line))

    def test_write_file_reports_unwritable_records(self):
        written, skipped = bvbs.write_file(
            self.path("out.abs"), [_record([(400, 90), (600, 0)]),
                                   bvbs.BarRecord("9", 0, 1, [(10, 0)])])
        self.assertEqual(written, 1)
        self.assertEqual(skipped, [("9", "no bar diameter")])

    def test_verify_file_accepts_what_write_file_wrote(self):
        target = self.path("out.abs")
        records = [_record([(400, 90), (600, 0)], weight=0.888)]
        bvbs.write_file(target, records)
        self.assertEqual(bvbs.verify_file(target, records), [])

    def test_verify_file_catches_every_kind_of_damage(self):
        target = self.path("out.abs")
        records = [_record([(400, 90), (600, 0)], weight=0.888)]
        bvbs.write_file(target, records)
        with open(target, "rb") as handle:
            good = handle.read()

        def damaged(data):
            with open(target, "wb") as handle:
                handle.write(data)
            return bvbs.verify_file(target, records)

        self.assertTrue(damaged(good.replace(b"l400", b"l401")))                  # content changed
        self.assertTrue(damaged(good.replace(b"\r\n", b"\n")))                    # LF only
        self.assertTrue(damaged(good.rstrip(b"\r\n")))                            # no final CR LF
        self.assertTrue(damaged(b""))                                             # empty
        self.assertTrue(damaged(good + good))                                     # extra line
        self.assertTrue(damaged(good.replace(b"@s48", b"@m1@s48")))               # BFMA-only field
        self.assertTrue(damaged(b"\xc3\xa9" + good))                              # non-ASCII byte
        os.remove(target)
        self.assertTrue(bvbs.verify_file(target, records))                        # missing file

    def test_verify_file_notices_a_line_that_differs_from_its_record(self):
        # A self-consistent line (checksum right) that is not the record we meant.
        target = self.path("out.abs")
        bvbs.write_file(target, [_record([(400, 90), (500, 0)])])
        problems = bvbs.verify_file(target, [_record([(400, 90), (600, 0)])])
        self.assertTrue(any("differs" in text for text in problems), problems)

    def test_write_verified_writes_verifies_and_leaves_no_tmp_file(self):
        target = self.path("good.abs")
        records = [_record([(400, 90), (600, 0)], weight=0.888)]
        written, skipped, problems = bvbs.write_verified(target, records)
        self.assertEqual((written, skipped, problems), (1, [], []))
        self.assertEqual(os.listdir(self.tmp), ["good.abs"])
        self.assertEqual(bvbs.verify_file(target, records), [])

    def test_write_verified_never_replaces_a_good_file_with_a_bad_one(self):
        target = self.path("keep.abs")
        records = [_record([(400, 90), (600, 0)], weight=0.888)]
        bvbs.write_verified(target, records)
        with open(target, "rb") as handle:
            before = handle.read()

        original = bvbs._write_lines

        def corrupt(path, lines):                          # a writer that flips a digit
            original(path, [lines[0].replace("l400", "l499")] + list(lines[1:]))

        bvbs._write_lines = corrupt
        try:
            written, _, problems = bvbs.write_verified(target, records)
        finally:
            bvbs._write_lines = original
        self.assertEqual(written, 0)
        self.assertTrue(problems)
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), before)        # the old file is untouched
        self.assertEqual(os.listdir(self.tmp), ["keep.abs"])   # and no .tmp is left

    def test_write_verified_with_nothing_to_write(self):
        written, skipped, problems = bvbs.write_verified(
            self.path("none.abs"), [bvbs.BarRecord("9", 0, 1, [(10, 0)])])
        self.assertEqual(written, 0)
        self.assertTrue(problems)
        self.assertEqual(os.listdir(self.tmp), [])


class ParseTests(unittest.TestCase):
    def test_parse_block_returns_header_segments_and_checksum(self):
        parsed = bvbs.parse_block(LINE_3)
        self.assertEqual(parsed["checksum"], 82)
        self.assertEqual(parsed["segments"],
                         [(100, 90.0), (300, 45.0), (424, -45.0), (300, -90.0), (100, 0.0)])
        self.assertEqual(dict(parsed["header"])["p"], "1")
        self.assertEqual(dict(parsed["header"])["g"], "B500A")

    def test_parse_block_rejects_malformed_lines(self):
        for text in ("", "garbage", LINE_1[:-1],
                     LINE_1.replace("@s48", "@m1@s48"),             # m is not a BF2D field
                     LINE_1.replace("@v@", "@"),                    # v missing
                     LINE_1.replace("@w0@C", "@w5@C"),              # last bend not 0
                     LINE_1.replace("Gl400@w90@l600@w0@", "Gl400@w90@l600@"),   # odd geometry
                     LINE_1.replace("C72@", "")):                    # no checksum block
            with self.assertRaises(ValueError, msg=text):
                bvbs.parse_block(text)


class SelfTestTests(unittest.TestCase):
    def test_self_test_passes(self):
        self.assertEqual(bvbs.self_test(), (True, ""))

    def test_self_test_fails_loudly_when_the_writer_drifts(self):
        original = bvbs.checksum
        bvbs.checksum = lambda text: original(text) + 1
        try:
            ok, message = bvbs.self_test()
        finally:
            bvbs.checksum = original
        self.assertFalse(ok)
        self.assertEqual(message, bvbs.SELF_TEST_MESSAGE)
        self.assertIn("Do not send this file to a machine", message)

    def test_self_test_fails_when_the_header_gains_a_field(self):
        original = bvbs.header_fields
        bvbs.header_fields = lambda rec: original(rec) + [("m", "x")]
        try:
            ok, _ = bvbs.self_test()
        finally:
            bvbs.header_fields = original
        self.assertFalse(ok)


class WeightTests(unittest.TestCase):
    def test_default_weight_table_then_formula(self):
        table = bvbs.load_weight_table()
        self.assertEqual(table["12"], 0.888)
        self.assertEqual(bvbs.default_weight_per_metre(12, table), 0.888)       # table wins
        self.assertAlmostEqual(bvbs.default_weight_per_metre(12), 0.006165 * 144)  # formula
        self.assertAlmostEqual(bvbs.default_weight_per_metre(13, table), 0.006165 * 169)
        self.assertEqual(bvbs.default_weight_per_metre(12.0, {"12": 1.0}), 1.0)

    def test_the_table_agrees_with_the_formula(self):
        for key, value in bvbs.load_weight_table().items():
            self.assertAlmostEqual(value, 0.006165 * int(key) ** 2, delta=0.006, msg=key)

    def test_load_weight_table_survives_a_missing_file(self):
        self.assertEqual(bvbs.load_weight_table("/no/such/file.json"), {})

    def test_resolve_weight_follows_the_d13_chain(self):
        table = {"12": 0.888}
        self.assertEqual(bvbs.resolve_weight(12, 0.9, "revit", table), (0.9, "revit"))
        self.assertEqual(bvbs.resolve_weight(12, 0.95, "T3_WeightPerMetre", table),
                         (0.95, "T3_WeightPerMetre"))
        self.assertEqual(bvbs.resolve_weight(12, None, None, table), (0.888, "table"))
        self.assertEqual(bvbs.resolve_weight(12, 0.0, "revit", table), (0.888, "table"))
        value, source = bvbs.resolve_weight(13, None, None, table)
        self.assertEqual(source, "formula")
        self.assertAlmostEqual(value, 0.006165 * 169)

    def test_bar_weight_matches_the_guideline_example(self):
        self.assertAlmostEqual(bvbs.bar_weight_kg(1000, 0.888), 0.888)

    def test_weight_caption_names_the_source(self):
        self.assertEqual(bvbs.weight_caption({"revit": 5, "table": 0}), u"Weight: Revit mass (2027)")
        self.assertEqual(bvbs.weight_caption({"table": 3}), u"Weight: default table (TCVN/BS)")
        self.assertEqual(bvbs.weight_caption({"T3_WeightPerMetre": 1}), u"Weight: T3_WeightPerMetre")
        mixed = bvbs.weight_caption({"revit": 4, "table": 9})
        self.assertIn("default table (TCVN/BS) for 9", mixed)
        self.assertIn("Revit mass (2027) for 4", mixed)
        self.assertEqual(bvbs.weight_caption({}), u"Weight: no bars yet")


class ShapeTests(unittest.TestCase):
    def test_a_bar_from_the_guideline_round_trips_through_the_geometry_pipeline(self):
        # Centre-line 394 x 594 with a 12 mm bar -> outer 400 x 600 = guideline example 1.
        curves = [("Line", (0, 0, 0), (394, 0, 0)), ("Line", (394, 0, 0), (394, 594, 0))]
        shape = bvbs.chain_to_shape(curves, 12)
        self.assertTrue(shape.ok, shape.reason)
        self.assertEqual(shape.segments, [(400, 90.0), (600, 0.0)])
        self.assertEqual(bvbs.legs_text(shape.segments), "400/90/600")
        record = _record(shape.segments, weight=0.888)
        self.assertEqual(bvbs.write_block(record), LINE_1)

    def test_curves_may_arrive_reversed_and_in_any_direction(self):
        curves = [("Line", (394, 594, 0), (394, 0, 0)), ("Line", (394, 0, 0), (0, 0, 0))]
        shape = bvbs.chain_to_shape(curves, 12)
        self.assertTrue(shape.ok, shape.reason)
        self.assertEqual([int(length) for length, _ in shape.segments], [600, 400])

    def test_z_bar_turns_both_ways(self):
        curves = [("Line", (0, 0, 0), (100, 0, 0)), ("Line", (100, 0, 0), (100, 100, 0)),
                  ("Line", (100, 100, 0), (200, 100, 0))]
        shape = bvbs.chain_to_shape(curves, 10)
        self.assertTrue(shape.ok, shape.reason)
        self.assertEqual([angle for _, angle in shape.segments], [90.0, -90.0, 0.0])
        self.assertEqual(shape.bends, 2)

    def test_straight_bar_is_one_leg(self):
        shape = bvbs.chain_to_shape([("Line", (0, 0, 0), (3000, 0, 0))], 20)
        self.assertEqual(shape.segments, [(3000, 0.0)])
        self.assertEqual(bvbs.legs_text(shape.segments), "3000")

    def test_hooked_stirrup_keeps_its_135_and_90_degree_bends(self):
        # tail - 135 - side - 90 - side - 90 - side - 90 - side - 135 - tail, all turning one way.
        import math
        legs = [(60, 135), (200, 90), (400, 90), (200, 90), (400, 135), (60, 0)]
        x, y, heading = 0.0, 0.0, 0.0
        points = [(x, y, 0.0)]
        for length, turn in legs:
            x += length * math.cos(math.radians(heading))
            y += length * math.sin(math.radians(heading))
            points.append((x, y, 0.0))
            heading += turn
        curves = [("Line", a, b) for a, b in zip(points, points[1:])]
        shape = bvbs.chain_to_shape(curves, 8)
        self.assertTrue(shape.ok, shape.reason)
        self.assertEqual([angle for _, angle in shape.segments], [135.0, 90.0, 90.0, 90.0, 135.0, 0.0])
        self.assertEqual(len(shape.segments), 6)
        self.assertGreater(shape.segments[0][0], 60)       # outer leg > centre-line leg
        self.assertEqual(shape.bends, 5)

    def test_skip_reasons(self):
        arc = [("Line", (0, 0, 0), (100, 0, 0)), ("Arc", (100, 0, 0), (200, 100, 0), (170, 30, 0))]
        self.assertEqual(bvbs.chain_to_shape(arc, 12).reason, u"curved leg")
        spatial = [("Line", (0, 0, 0), (100, 0, 0)), ("Line", (100, 0, 0), (100, 100, 0)),
                   ("Line", (100, 100, 0), (100, 100, 100))]
        self.assertEqual(bvbs.chain_to_shape(spatial, 12).reason, u"free-form 3D")
        gap = [("Line", (0, 0, 0), (100, 0, 0)), ("Line", (500, 0, 0), (500, 100, 0))]
        self.assertEqual(bvbs.chain_to_shape(gap, 12).reason, u"broken centre-line")
        self.assertEqual(bvbs.chain_to_shape([], 12).reason, u"no centre-line")
        self.assertFalse(bvbs.chain_to_shape(spatial, 12).ok)

    def test_group_distinct_counts_equal_shapes(self):
        a = bvbs.BarShape(segments=[(400, 90.0), (600, 0.0)])
        b = bvbs.BarShape(segments=[(400, 90.0), (601, 0.0)])
        groups = bvbs.group_distinct([a, a, b, a])
        self.assertEqual([(g.key(), n) for g, n in groups], [(a.key(), 3), (b.key(), 1)])
        self.assertEqual(bvbs.group_distinct([]), [])

    def test_sample_positions_reads_all_of_a_small_set_and_three_of_a_big_one(self):
        self.assertEqual(bvbs.sample_positions(range(5)), [0, 1, 2, 3, 4])
        self.assertEqual(bvbs.sample_positions(range(12)), list(range(12)))
        self.assertEqual(bvbs.sample_positions(range(100)), [0, 50, 99])
        self.assertEqual(bvbs.sample_positions([3, 4, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]),
                         [3, 13, 19])
        self.assertEqual(bvbs.sample_positions([]), [])

    def test_legs_text_formats_lengths_and_angles(self):
        self.assertEqual(bvbs.legs_text([(100, 90.0), (300, 45.0), (424, -45.0), (100, 0.0)]),
                         "100/90/300/45/424/-45/100")
        self.assertEqual(bvbs.legs_text([(100, 79.7), (200, 0.0)]), "100/79.7/200")


class LengthCheckTests(unittest.TestCase):
    def test_a_cut_length_slightly_under_the_outer_legs_is_fine(self):
        # 400 + 600 outer, one 90 degree bend over a 48 mm mandrel: cut length ~ 975.
        self.assertEqual(bvbs.length_check(975, 1000, 1, 12, 48), (True, u""))
        self.assertEqual(bvbs.length_check(1000, 1000, 1, 12, 48), (True, u""))

    def test_a_length_longer_than_the_outer_legs_is_suspicious(self):
        ok, text = bvbs.length_check(1060, 1000, 1, 12, 48)
        self.assertFalse(ok)
        self.assertIn("longer", text)

    def test_a_length_far_shorter_than_the_outer_legs_is_suspicious(self):
        ok, text = bvbs.length_check(700, 1000, 1, 12, 48)
        self.assertFalse(ok)
        self.assertIn("shorter", text)

    def test_no_revit_length_means_nothing_to_compare(self):
        self.assertEqual(bvbs.length_check(None, 1000, 1, 12), (True, u""))
        self.assertEqual(bvbs.length_check(0, 1000, 1, 12), (True, u""))

    def test_mandrel_defaults_to_four_diameters(self):
        self.assertEqual(bvbs.length_check(975, 1000, 1, 12), (True, u""))


class MarkAndFileTests(unittest.TestCase):
    def test_build_mark(self):
        self.assertEqual(bvbs.build_mark(u"C-01", u"5", u"X"), u"C-01-5")
        self.assertEqual(bvbs.build_mark(u"", u"5", u"X"), u"5")
        self.assertEqual(bvbs.build_mark(u"C-01", u"", u"7"), u"7")           # number empty
        self.assertEqual(bvbs.build_mark(u"C-01", u"  ", u" 7 "), u"7")
        self.assertEqual(bvbs.build_mark(u"", u"", u""), u"")
        self.assertEqual(bvbs.build_mark(None, None, None), u"")

    def test_safe_file_name(self):
        self.assertEqual(bvbs.safe_file_name("MODEL"), "MODEL")
        self.assertEqual(bvbs.safe_file_name(u"C-01 / L2:*?"), "C-01_L2")
        self.assertEqual(bvbs.safe_file_name(u"Đợt 1"), "Dot_1")
        self.assertEqual(bvbs.safe_file_name(""), "BVBS")
        self.assertEqual(bvbs.safe_file_name("..."), "BVBS")
        self.assertEqual(bvbs.safe_file_name("con"), "con_")
        self.assertEqual(bvbs.safe_file_name("NUL"), "NUL_")
        self.assertLessEqual(len(bvbs.safe_file_name("x" * 300)), 80)

    def test_plan_files_one_file(self):
        records = [_record([(100, 0)], group="A"), _record([(200, 0)], group="B")]
        files = bvbs.plan_files(records, False, "MODEL")
        self.assertEqual([name for name, _ in files], ["MODEL.abs"])
        self.assertEqual(len(files[0][1]), 2)
        self.assertEqual(bvbs.plan_files([], False, "MODEL"), [])

    def test_plan_files_one_per_assembly(self):
        records = [_record([(100, 0)], mark="1", group="C-01"),
                   _record([(200, 0)], mark="2", group="C-02"),
                   _record([(300, 0)], mark="3", group="C-01"),
                   _record([(400, 0)], mark="4", group="")]
        files = bvbs.plan_files(records, True, "417")
        self.assertEqual([name for name, _ in files],
                         ["417_C-01.abs", "417_C-02.abs", "417_unassigned.abs"])
        self.assertEqual([r.mark for r in files[0][1]], ["1", "3"])

    def test_plan_files_gives_equal_names_a_suffix(self):
        records = [_record([(100, 0)], group="C/1"), _record([(200, 0)], group="C:1")]
        files = bvbs.plan_files(records, True, "P")
        self.assertEqual(len({name for name, _ in files}), 2)
        self.assertEqual(files[0][0], "P_C_1.abs")
        self.assertEqual(files[1][0], "P_C_1_2.abs")



# ── DIALOG LOGIC (Revit and WPF faked) ───────────────────────────────────────
#
# BVBSExportDialog is Revit + WPF glue, but the decisions in it (sampling, merging,
# skip reasons, length check, file split, write + verify) are what put numbers on
# a machine. This loads the shipped dialog source with the Revit/WPF modules
# stubbed and drives its real methods against fake rebar.

class _Control(object):
    """Any named XAML control: remembers whatever the dialog sets on it."""

    def __init__(self):
        self.Text = ""
        self.IsEnabled = True
        self.IsChecked = False
        self.SelectedIndex = -1
        self.SelectedItem = None
        self.ItemsSource = None
        self.Visibility = None
        self.Fill = None
        self.Content = None

    def __iadd__(self, handler):
        return self

    def AddHandler(self, *args):
        pass


class _FakeWindow(object):
    """Stand-in for T3WPFWindow: lazy controls + the progress / grid helpers."""

    def __init__(self, xaml=None):
        self._controls = {}
        self.stop_after = None          # stop the Nth step_progress call
        self._steps = 0
        self.progress_log = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        controls = self.__dict__.setdefault("_controls", {})
        if name not in controls:
            controls[name] = _Control()
        return controls[name]

    def set_items_source(self, control, items):
        control.ItemsSource = list(items)

    def begin_progress(self, maximum=100, disable=None):
        self.progress_log.append(("begin", maximum))

    def _update_progress(self, value, maximum=None):
        pass

    def step_progress(self, value, message=None):
        self._steps += 1
        self.progress_log.append(("step", value, message))
        return not (self.stop_after and self._steps >= self.stop_after)

    def end_progress(self):
        self.progress_log.append(("end",))

    def toggle_all_rows(self, grid, prop, is_checked):
        for row in grid.ItemsSource:
            setattr(row, prop, bool(is_checked))

    def FindResource(self, key):
        return key

    def Close(self):
        self.closed = True


def _load_dialog():
    """Exec GUI/BVBSExportDialog.py with clr / WPF / GUI helpers stubbed."""
    messages = []

    def stub(name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        module.__path__ = []
        return module

    visibility = types.SimpleNamespace(Visible="Visible", Collapsed="Collapsed")
    stubs = {
        "clr": stub("clr", AddReference=lambda name: None),
        "System": stub("System", Uri=object, UriKind=types.SimpleNamespace(Absolute=1)),
        "System.Windows": stub("System.Windows", Visibility=visibility,
                               RoutedEventHandler=lambda fn: fn),
        "System.Windows.Controls": stub("System.Windows.Controls",
                                        CheckBox=types.SimpleNamespace(ClickEvent="Click")),
        "System.Windows.Media": stub("System.Windows.Media"),
        "System.Windows.Media.Imaging": stub(
            "System.Windows.Media.Imaging", BitmapImage=object,
            BitmapCacheOption=types.SimpleNamespace(OnLoad=1)),
        "GUI.WPF_Base": stub("GUI.WPF_Base", T3WPFWindow=_FakeWindow),
        "GUI.T3Dialog": stub(
            "GUI.T3Dialog",
            confirm=lambda *a, **k: messages.append(("confirm", a, k)) or True,
            show_error=lambda *a, **k: messages.append(("error", a, k)),
            show_info=lambda *a, **k: messages.append(("info", a, k)),
            show_warning=lambda *a, **k: messages.append(("warning", a, k))),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        path = os.path.join(LIB_DIR, "GUI", "BVBSExportDialog.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        module = types.ModuleType("t3_bvbs_dialog_under_test")
        module.__file__ = path
        exec(compile(source, path, "exec"), module.__dict__)
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    module._messages = messages
    module.make_eid = lambda value: value
    module.eid_value = lambda value: int(value)
    module.bar_mass_per_metre = lambda bar_type: (None, None)
    return module


def _line_curves(*points):
    return [("Line", a, b) for a, b in zip(points, points[1:])]


L_BAR = _line_curves((0, 0, 0), (394, 0, 0), (394, 594, 0))          # outer 400 / 600 at d=12
L_BAR_LONG = _line_curves((0, 0, 0), (394, 0, 0), (394, 794, 0))     # outer 400 / 800
BAR_3D = _line_curves((0, 0, 0), (100, 0, 0), (100, 100, 0), (100, 100, 100))


class _Style(object):
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name


class _FakeBarType(object):
    Id = 900
    StandardBendDiameter = 48 / 304.8
    StirrupTieBendDiameter = 32 / 304.8


class _FakeRebar(object):
    """A rebar set: curves per bar position."""

    def __init__(self, element_id, positions, length_mm=None, style="Standard"):
        self.Id = element_id
        self.positions = positions                      # {position: curves | Exception}
        self.NumberOfBarPositions = max(positions) + 1 if positions else 0
        self.length_mm = length_mm
        self.Style = _Style(style)

    def DoesBarExistAtPosition(self, index):
        return index in self.positions


class _FakeDoc(object):
    def __init__(self, elements):
        self.elements = {el.Id: el for el in elements}
        self.PathName = ""
        self.ProjectInformation = types.SimpleNamespace(Number=u"P-100")

    def GetElement(self, element_id):
        return self.elements.get(element_id)


class _Selection(object):
    def __init__(self, ids):
        self.ids = ids

    def GetElementIds(self):
        return list(self.ids)


class DialogLogicTests(_TmpDirCase):
    def setUp(self):
        super(DialogLogicTests, self).setUp()
        self.dlg = _load_dialog()                    # fresh module: no patch leaks between tests
        self.Rebar = sys.modules["Snippets._assembly"].RebarRecord
        appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self.tmp                        # settings file lands here
        self.addCleanup(lambda: os.environ.pop("APPDATA") if appdata is None
                        else os.environ.__setitem__("APPDATA", appdata))
        dlg = self.dlg
        self._saved = (dlg._rebar.centerline_points, dlg._rebar.bar_type_of,
                       dlg.BVBSExportDialog._revit_length_mm, dlg.resolve_uidoc)
        dlg._rebar.centerline_points = self._centerline_points
        dlg._rebar.bar_type_of = lambda doc, element: _FakeBarType()
        dlg.BVBSExportDialog._revit_length_mm = staticmethod(lambda element: element.length_mm)
        self.addCleanup(self._restore)
        del dlg._messages[:]

    def _restore(self):
        dlg = self.dlg
        (dlg._rebar.centerline_points, dlg._rebar.bar_type_of,
         dlg.BVBSExportDialog._revit_length_mm, dlg.resolve_uidoc) = self._saved

    @staticmethod
    def _centerline_points(element, position=0):
        found = element.positions[position]
        if isinstance(found, Exception):
            raise found
        return found

    def record(self, element_id, number="5", partition="C1", mark="", quantity=1,
               diameter=12.0, assembly=-1, host=-1, driven=True, kind="Rebar"):
        return self.Rebar(id=element_id, kind=kind, host_id=host, assembly_id=assembly,
                          partition=partition, number=number, mark=mark, diameter_mm=diameter,
                          quantity=quantity, is_shape_driven=driven)

    def make_dialog(self, records, elements, assemblies=()):
        """A real BVBSExportDialog (its __init__ runs) over fake rebar."""
        dialog = self.dlg.BVBSExportDialog(_FakeDoc(elements))
        index = types.SimpleNamespace(rows=list(records), stopped=False, by_id={}, by_host={})
        for row in records:
            index.by_id[row.id] = row
            index.by_host.setdefault(row.host_id, []).append(row)
        dialog._index = index
        dialog._assemblies = list(assemblies)
        dialog._build_maps()
        dialog._fill_scope_combos()
        return dialog

    def uniform(self, element_id, bars, curves=L_BAR, **kwargs):
        return _FakeRebar(element_id, {i: curves for i in range(bars)}, **kwargs)

    # -- the happy path --------------------------------------------------------

    def test_a_uniform_set_is_one_ready_row_read_from_three_positions(self):
        rebar = self.uniform(1, 40)
        reads = []
        original = self.dlg._rebar.centerline_points
        self.dlg._rebar.centerline_points = lambda el, pos=0: reads.append(pos) or original(el, pos)
        dialog = self.make_dialog([self.record(1, quantity=40)], [rebar])
        dialog._rebuild_preview()
        self.assertEqual(reads, [0, 20, 39])                    # R11: first / middle / last
        row, = dialog._rows
        self.assertEqual((row.mark, row.qty_text, row.legs_text, row.StatusText),
                         ("C1-5", "40", "400/90/600", "Ready"))
        self.assertTrue(row.is_selected)
        self.assertTrue(row.exportable)
        self.assertEqual(row.record.quantity, 40)
        self.assertEqual(row.record.segments, [(400, 90.0), (600, 0.0)])
        self.assertEqual(row.record.roll_diameter_mm, 48)       # bar type StandardBendDiameter
        self.assertIn("read 3 of 40 bars", row.detail)
        self.assertEqual(dialog.btn_export_label.Text, "Export 40 bars")
        self.assertTrue(dialog.btn_export.IsEnabled)
        self.assertEqual(dialog.txt_weight_source.Text, u"Weight: default table (TCVN/BS)")

    def test_a_small_set_is_read_bar_by_bar(self):
        reads = []
        original = self.dlg._rebar.centerline_points
        self.dlg._rebar.centerline_points = lambda el, pos=0: reads.append(pos) or original(el, pos)
        dialog = self.make_dialog([self.record(1, quantity=5)], [self.uniform(1, 5)])
        dialog._rebuild_preview()
        self.assertEqual(reads, [0, 1, 2, 3, 4])
        self.assertEqual(dialog._rows[0].qty_text, "5")

    def test_weight_follows_the_chain_and_length_is_the_outer_legs_without_revit_length(self):
        dialog = self.make_dialog([self.record(1, quantity=2)], [self.uniform(1, 2)])
        dialog._rebuild_preview()
        record = dialog._rows[0].record
        self.assertEqual(record.length_mm, 1000)                # 400 + 600
        self.assertAlmostEqual(record.weight_kg, 0.888, places=3)

    def test_revit_mass_wins_when_the_bar_type_has_it(self):
        self.dlg.bar_mass_per_metre = lambda bar_type: (1.0, "revit")
        dialog = self.make_dialog([self.record(1, quantity=2)], [self.uniform(1, 2)])
        dialog._rebuild_preview()
        self.assertAlmostEqual(dialog._rows[0].record.weight_kg, 1.0, places=3)
        self.assertEqual(dialog.txt_weight_source.Text, u"Weight: Revit mass (2027)")

    def test_revit_length_is_used_when_it_agrees_with_the_legs(self):
        dialog = self.make_dialog([self.record(1, quantity=2)],
                                  [self.uniform(1, 2, length_mm=975)])
        dialog._rebuild_preview()
        row = dialog._rows[0]
        self.assertEqual(row.StatusText, "Ready")
        self.assertEqual(row.record.length_mm, 975)
        self.assertIn("Revit bar length", row.detail)

    def test_a_revit_length_that_contradicts_the_legs_is_flagged_and_unticked(self):
        dialog = self.make_dialog([self.record(1, quantity=2)],
                                  [self.uniform(1, 2, length_mm=1100)])
        dialog._rebuild_preview()
        row = dialog._rows[0]
        self.assertEqual((row.StatusText, row.Severity), ("Check legs", "Warning"))
        self.assertFalse(row.is_selected)
        self.assertTrue(row.exportable)
        self.assertEqual(row.record.length_mm, 1000)            # falls back to the outer legs
        self.assertIn("longer than the outer legs", row.detail)
        self.assertFalse(dialog.btn_export.IsEnabled)           # nothing ticked

    def test_stirrup_style_uses_the_stirrup_bend_diameter(self):
        dialog = self.make_dialog([self.record(1, quantity=1)],
                                  [self.uniform(1, 1, style="StirrupTie")])
        dialog._rebuild_preview()
        self.assertEqual(dialog._rows[0].record.roll_diameter_mm, 32)

    # -- merging, varying sets -------------------------------------------------

    def test_sets_with_the_same_mark_and_shape_add_their_quantities(self):
        records = [self.record(1, quantity=10), self.record(2, quantity=6)]
        dialog = self.make_dialog(records, [self.uniform(1, 10), self.uniform(2, 6)])
        dialog._rebuild_preview()
        row, = dialog._rows
        self.assertEqual(row.qty_text, "16")
        self.assertEqual(row.rebar_ids, [1, 2])

    def test_a_set_with_varying_bars_becomes_one_row_per_distinct_shape(self):
        positions = {i: (L_BAR if i < 15 else L_BAR_LONG) for i in range(20)}
        rebar = _FakeRebar(1, positions)
        dialog = self.make_dialog([self.record(1, quantity=20)], [rebar])
        dialog._rebuild_preview()
        rows = {r.legs_text: r for r in dialog._rows}
        self.assertEqual(set(rows), {"400/90/600", "400/90/800"})
        self.assertEqual(rows["400/90/600"].qty_text, "15")
        self.assertEqual(rows["400/90/800"].qty_text, "5")
        for row in rows.values():                               # same mark, two shapes: check first
            self.assertEqual(row.StatusText, "Same mark, other shape")
            self.assertFalse(row.is_selected)

    def test_the_same_mark_in_two_assemblies_stays_two_rows(self):
        asm_a = types.SimpleNamespace(id=70, mark="C-01", members=[], type_id=1)
        asm_b = types.SimpleNamespace(id=71, mark="C-02", members=[], type_id=2)
        records = [self.record(1, quantity=3, assembly=70), self.record(2, quantity=4, assembly=71)]
        dialog = self.make_dialog(records, [self.uniform(1, 3), self.uniform(2, 4)], [asm_a, asm_b])
        dialog._rebuild_preview()
        self.assertEqual(sorted((r.group, r.qty_text) for r in dialog._rows),
                         [("C-01", "3"), ("C-02", "4")])
        self.assertTrue(all(r.StatusText == "Ready" for r in dialog._rows))   # not a conflict

    # -- skipped rows ----------------------------------------------------------

    def test_bars_that_cannot_be_written_are_listed_as_skipped_with_the_reason(self):
        records = [self.record(1, number="1", quantity=2), self.record(2, number="2", quantity=3),
                   self.record(3, number="", mark="", quantity=1),
                   self.record(4, number="4", diameter=0.0), self.record(5, number="5")]
        elements = [self.uniform(1, 2, curves=BAR_3D), self.uniform(2, 3, curves=_line_curves(
                        (0, 0, 0), (100, 0, 0)) + [("Arc", (100, 0, 0), (200, 100, 0),
                                                    (170, 30, 0))]),
                    self.uniform(3, 1), self.uniform(4, 1),
                    _FakeRebar(5, {0: RuntimeError("Could not read the bar centre-line: boom")})]
        dialog = self.make_dialog(records, elements)
        dialog._rebuild_preview()
        by_mark = {r.mark: r for r in dialog._rows}
        self.assertEqual(by_mark["C1-1"].StatusText, "Skipped: free-form 3D")
        self.assertEqual(by_mark["C1-2"].StatusText, "Skipped: curved leg")
        self.assertEqual(by_mark["(id 3)"].StatusText, "Skipped: no mark")
        self.assertEqual(by_mark["C1-4"].StatusText, "Skipped: no diameter")
        self.assertEqual(by_mark["C1-5"].StatusText, "Skipped: centre-line unreadable")
        self.assertIn("boom", by_mark["C1-5"].detail)
        for row in dialog._rows:
            self.assertEqual(row.Severity, "Danger")
            self.assertFalse(row.exportable)
            self.assertFalse(row.is_selected)
            self.assertIsNone(row.record)
        self.assertEqual(by_mark["C1-1"].qty_text, "2")
        self.assertFalse(dialog.btn_export.IsEnabled)
        self.assertIn("skipped", dialog.txt_preview_count.Text)

    def test_a_set_that_is_partly_3d_exports_the_good_bars_and_reports_the_rest(self):
        positions = {i: (L_BAR if i < 18 else BAR_3D) for i in range(20)}
        dialog = self.make_dialog([self.record(1, quantity=20)], [_FakeRebar(1, positions)])
        dialog._rebuild_preview()
        good = [r for r in dialog._rows if r.exportable]
        bad = [r for r in dialog._rows if not r.exportable]
        self.assertEqual([(r.qty_text, r.legs_text) for r in good], [("18", "400/90/600")])
        self.assertEqual([(r.qty_text, r.StatusText) for r in bad],
                         [("2", "Skipped: free-form 3D")])
        self.assertIn("2 of 20 bars", bad[0].detail)

    def test_one_broken_bar_never_stops_the_others(self):
        records = [self.record(1, number="1"), self.record(2, number="2")]
        elements = [self.uniform(1, 1), self.uniform(2, 1)]
        elements[0].NumberOfBarPositions = "not a number"       # makes the position scan fail
        dialog = self.make_dialog(records, elements)
        dialog.doc.elements[1] = None                           # and element 1 vanishes
        dialog._rebuild_preview()
        self.assertEqual(sorted(r.StatusText for r in dialog._rows),
                         ["Ready", "Skipped: element missing"])

    def test_filters_and_other_reinforcement_kinds_are_counted_in_a_note(self):
        records = [self.record(1, number="1"), self.record(2, number="2", driven=False),
                   self.record(3, number="3", kind="AreaReinforcement")]
        dialog = self.make_dialog(records, [self.uniform(1, 1), self.uniform(2, 1)])
        dialog._rebuild_preview()
        self.assertEqual([r.mark for r in dialog._rows], ["C1-1"])
        self.assertIn("1 element", dialog.txt_note.Text)
        self.assertIn("1 free-form set", dialog.txt_note.Text)
        dialog.chk_only_shape_driven.IsChecked = False
        dialog._rebuild_preview()
        self.assertEqual(sorted(r.mark for r in dialog._rows), ["C1-1", "C1-2"])

    # -- scope ------------------------------------------------------------------

    def scope_fixture(self):
        asm = types.SimpleNamespace(id=70, mark="C-01", members=[10], type_id=1)
        records = [self.record(1, number="1", partition="P1", assembly=70, host=10),
                   self.record(2, number="2", partition="P2", host=10),      # hosted, not yet a member
                   self.record(3, number="3", partition="P2", host=11)]
        elements = [self.uniform(i, 1) for i in (1, 2, 3)]
        return self.make_dialog(records, elements, [asm]), asm

    def test_assembly_scope_includes_rebar_hosted_by_its_members(self):
        dialog, asm = self.scope_fixture()
        dialog.rb_scope_assembly.IsChecked = True
        dialog.cb_assembly.SelectedIndex = 0
        dialog._rebuild_preview()
        self.assertEqual(sorted(r.mark for r in dialog._rows), ["P1-1", "P2-2"])
        self.assertEqual({r.group for r in dialog._rows}, {"C-01"})   # unsynced rebar named too

    def test_partition_scope_and_model_scope(self):
        dialog, _ = self.scope_fixture()
        dialog.rb_scope_partition.IsChecked = True
        dialog.cb_partition.SelectedIndex = dialog._partition_choices.index("P2")
        dialog._rebuild_preview()
        self.assertEqual(sorted(r.mark for r in dialog._rows), ["P2-2", "P2-3"])
        dialog.rb_scope_partition.IsChecked = False
        dialog._rebuild_preview()
        self.assertEqual(len(dialog._rows), 3)

    def test_selection_scope_expands_hosts_and_assemblies(self):
        dialog, asm = self.scope_fixture()
        self.dlg.resolve_uidoc = lambda: types.SimpleNamespace(Selection=_Selection([70]))
        self.assertEqual(sorted(r.id for r in dialog._selection_rows()), [1, 2])
        self.dlg.resolve_uidoc = lambda: types.SimpleNamespace(Selection=_Selection([11, 1]))
        self.assertEqual([r.id for r in dialog._selection_rows()], [3, 1])
        self.dlg.resolve_uidoc = lambda: types.SimpleNamespace(Selection=_Selection([999]))
        self.assertEqual(dialog._selection_rows(), [])

    # -- stopping ---------------------------------------------------------------

    def test_stopping_a_scan_blocks_the_export_until_the_preview_is_refreshed(self):
        records = [self.record(i, number=str(i)) for i in range(1, 12)]
        elements = [self.uniform(i, 1) for i in range(1, 12)]
        dialog = self.make_dialog(records, elements)
        dialog.stop_after = 2                                   # stop at the 2nd progress step
        dialog._rebuild_preview()
        self.assertTrue(dialog._partial)
        self.assertFalse(dialog.btn_export.IsEnabled)
        self.assertIn("stopped", dialog.txt_note.Text)
        dialog.stop_after = None
        dialog._rebuild_preview()
        self.assertFalse(dialog._partial)
        self.assertEqual(len(dialog._rows), 11)
        self.assertTrue(dialog.btn_export.IsEnabled)

    # -- grid -------------------------------------------------------------------

    def test_a_skipped_row_can_never_stay_ticked(self):
        records = [self.record(1, number="1"), self.record(2, number="2")]
        dialog = self.make_dialog(records, [self.uniform(1, 1), self.uniform(2, 1, curves=BAR_3D)])
        dialog._rebuild_preview()
        dialog.select_all_grid_preview_clicked(dialog.chk_all_grid_preview, None)
        dialog.chk_all_grid_preview.IsChecked = True
        dialog.select_all_grid_preview_clicked(dialog.chk_all_grid_preview, None)
        self.assertEqual([(r.mark, r.is_selected) for r in dialog._rows],
                         [("C1-1", True), ("C1-2", False)])
        self.assertEqual(dialog.chk_all_grid_preview.IsChecked, True)    # every writable row is ticked

    # -- export: write, read back, verify ---------------------------------------

    def ready_dialog(self, per_assembly=False):
        asm_a = types.SimpleNamespace(id=70, mark="C-01", members=[], type_id=1)
        asm_b = types.SimpleNamespace(id=71, mark="C-02", members=[], type_id=2)
        records = [self.record(1, number="1", quantity=10, assembly=70),
                   self.record(2, number="2", quantity=4, assembly=71),
                   self.record(3, number="3", quantity=2)]
        elements = [self.uniform(1, 10), self.uniform(2, 4, curves=L_BAR_LONG),
                    self.uniform(3, 2, curves=BAR_3D)]
        dialog = self.make_dialog(records, elements, [asm_a, asm_b])
        dialog._rebuild_preview()
        dialog.tb_folder.Text = self.tmp
        dialog.tb_plan.Text = "417"
        dialog.tb_project.Text = "TestPDF"
        dialog.tb_revision.Text = "a"
        dialog.tb_grade.Text = "B500A"
        dialog.chk_per_assembly.IsChecked = per_assembly
        return dialog

    def abs_files(self):
        return sorted(name for name in os.listdir(self.tmp) if name.endswith(".abs"))

    def test_export_writes_one_verified_file_with_the_guideline_header(self):
        dialog = self.ready_dialog()
        dialog.export_clicked(None, None)
        self.assertEqual(self.abs_files(), ["417.abs"])
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".tmp")], [])
        with open(os.path.join(self.tmp, "417.abs"), "rb") as handle:
            lines = handle.read().decode("ascii").split("\r\n")[:-1]
        self.assertEqual(len(lines), 2)                              # the 3D set is not written
        for line in lines:
            self.assertTrue(bvbs.verify_block(line))
            self.assertTrue(line.startswith("BF2D@HjTestPDF@r417@ia@p"))
        self.assertIn("@pC1-1@l1000@n10@e0.888@d12@gB500A@s48@v@Gl400@w90@l600@w0@", lines[0])
        self.assertIn("@pC1-2@l1200@n4@", lines[1])
        status = dialog.status_text.Text
        self.assertIn("Wrote 14 bars to 1 file", status)
        self.assertIn("2 skipped (free-form 3D)", status)
        self.assertTrue(status.endswith("verified"))
        kinds = [m[0] for m in self.dlg._messages]
        self.assertEqual(kinds, ["info"])                           # no overwrite prompt the 1st time
        self.assertIn("417.abs", self.dlg._messages[0][2]["details"])

    def test_export_one_file_per_assembly(self):
        dialog = self.ready_dialog(per_assembly=True)
        dialog.export_clicked(None, None)
        self.assertEqual(self.abs_files(), ["417_C-01.abs", "417_C-02.abs"])
        self.assertIn("to 2 files", dialog.status_text.Text)

    def test_export_only_writes_ticked_rows(self):
        dialog = self.ready_dialog()
        dialog._rows[1].is_selected = False
        dialog.grid_preview_checkbox_clicked(None, None)
        self.assertEqual(dialog.btn_export_label.Text, "Export 10 bars")
        dialog.export_clicked(None, None)
        with open(os.path.join(self.tmp, "417.abs"), "rb") as handle:
            self.assertEqual(handle.read().count(b"\r\n"), 1)

    def test_export_asks_before_overwriting_and_respects_no(self):
        dialog = self.ready_dialog()
        path = os.path.join(self.tmp, "417.abs")
        with open(path, "wb") as handle:
            handle.write(b"old")
        self.dlg.t3_confirm = lambda *a, **k: self.dlg._messages.append(("confirm", a, k)) or False
        dialog.export_clicked(None, None)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"old")                  # declined: untouched
        self.assertEqual(self.dlg._messages[0][0], "confirm")
        self.assertIn("Overwrite 1 existing file", self.dlg._messages[0][1][0])
        self.assertTrue(self.dlg._messages[0][2]["danger"])
        self.dlg.t3_confirm = lambda *a, **k: True
        dialog.export_clicked(None, None)
        self.assertGreater(os.path.getsize(path), 3)

    def test_export_reports_a_failed_read_back_and_writes_nothing(self):
        dialog = self.ready_dialog()
        original = self.dlg._bvbs._write_lines

        def corrupt(path, lines):
            original(path, [lines[0].replace("l400", "l499")] + list(lines[1:]))

        self.dlg._bvbs._write_lines = corrupt
        try:
            dialog.export_clicked(None, None)
        finally:
            self.dlg._bvbs._write_lines = original
        self.assertEqual(self.abs_files(), [])
        self.assertEqual(self.dlg._messages[-1][0], "error")
        self.assertIn("Do not send these files to a machine", self.dlg._messages[-1][1][0])
        self.assertIn("failed read-back verification", dialog.status_text.Text)
        self.assertNotIn("verified.", dialog.status_text.Text.replace("verification", ""))

    def test_export_refuses_a_missing_folder_and_an_empty_selection(self):
        dialog = self.ready_dialog()
        dialog.tb_folder.Text = os.path.join(self.tmp, "nope")
        dialog.export_clicked(None, None)
        self.assertEqual(self.dlg._messages[-1][0], "warning")
        self.assertIn("does not exist", self.dlg._messages[-1][1][0])
        self.assertEqual(self.abs_files(), [])
        dialog.tb_folder.Text = self.tmp
        for row in dialog._rows:
            row.is_selected = False
        dialog.export_clicked(None, None)
        self.assertIn("Nothing to export", self.dlg._messages[-1][1][0])
        self.assertEqual(self.abs_files(), [])

    def test_export_is_blocked_after_a_stopped_scan(self):
        dialog = self.ready_dialog()
        dialog._partial = True
        dialog.export_clicked(None, None)
        self.assertEqual(self.abs_files(), [])
        self.assertIn("Refresh the preview", self.dlg._messages[-1][1][0])

    def test_a_failed_self_test_disables_the_export(self):
        original = self.dlg._bvbs.self_test
        self.dlg._bvbs.self_test = lambda: (False, self.dlg._bvbs.SELF_TEST_MESSAGE)
        try:
            dialog = self.make_dialog([self.record(1)], [self.uniform(1, 1)])
        finally:
            self.dlg._bvbs.self_test = original
        self.assertFalse(dialog.btn_export.IsEnabled)
        self.assertEqual(dialog.selftest_box.Visibility, "Visible")
        self.assertIn("Do not send this file to a machine", dialog.txt_selftest.Text)
        dialog._rebuild_preview()
        self.assertEqual(dialog._rows, [])                           # never even reads bars
        dialog.export_clicked(None, None)
        self.assertEqual(self.abs_files(), [])

    def test_settings_are_remembered_after_a_successful_export(self):
        dialog = self.ready_dialog()
        dialog.tb_grade.Text = "B500C"
        dialog.export_clicked(None, None)
        again = self.make_dialog([], [])
        self.assertEqual(again.tb_grade.Text, "B500C")
        self.assertEqual(again.tb_folder.Text, self.tmp)

    def test_defaults_come_from_the_project(self):
        dialog = self.make_dialog([], [])
        self.assertEqual(dialog.tb_project.Text, u"P-100")
        self.assertEqual(dialog.tb_plan.Text, "MODEL")
        self.assertEqual(dialog.tb_revision.Text, "a")
        self.assertEqual(dialog.tb_grade.Text, "B500B")

if __name__ == '__main__':
    unittest.main()
