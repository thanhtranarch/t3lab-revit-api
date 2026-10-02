# -*- coding: utf-8 -*-
"""Project length units (Snippets/_units.py): labels, display, parsing.

Run: python3 dev/test_units.py
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'T3Lab.extension', 'lib'))

from Snippets import _units as U  # noqa: E402  (pure Python core)

FT = 1.0
IN = 1.0 / 12.0
MM = 1.0 / 304.8


class UnitDetection(unittest.TestCase):
    def test_type_ids_map_to_units(self):
        cases = {
            'autodesk.unit.unit:millimeters-1.0.1': 'mm',
            'autodesk.unit.unit:meters-1.0.1': 'm',
            'autodesk.unit.unit:centimeters-1.0.1': 'cm',
            'autodesk.unit.unit:feet-1.0.1': 'ft',
            'autodesk.unit.unit:inches-1.0.1': 'in',
            'autodesk.unit.unit:fractionalInches-1.0.0': 'in',
            'autodesk.unit.unit:feetFractionalInches-1.0.0': 'ft-in',
        }
        for type_id, tag in cases.items():
            self.assertEqual(U.unit_from_type_id(type_id).tag, tag, type_id)

    def test_unknown_or_missing_falls_back_to_mm(self):
        self.assertEqual(U.unit_from_type_id('autodesk.unit.unit:lightYears-1').tag, 'mm')
        self.assertEqual(U.unit_from_type_id(None).tag, 'mm')
        self.assertEqual(U.project_length_unit(None).tag, 'mm')

    def test_project_unit_reads_the_document(self):
        class Opt(object):
            def GetUnitTypeId(self):
                return type('T', (), {'TypeId': 'autodesk.unit.unit:feetFractionalInches-1.0.0'})()

        class Units(object):
            def GetFormatOptions(self, spec):
                return Opt()

        class Doc(object):
            def GetUnits(self):
                return Units()
        # Outside Revit the SpecTypeId import fails → mm. Inside it reads the doc.
        self.assertIn(U.project_length_unit(Doc()).tag, ('mm', 'ft-in'))


class Labels(unittest.TestCase):
    def test_label_follows_the_case_of_the_text(self):
        mm = U.LengthUnit('millimeters')
        ftin = U.LengthUnit('feetFractionalInches')
        self.assertEqual(mm.label('HEIGHT'), 'HEIGHT (MM)')
        self.assertEqual(mm.label('Height offset'), 'Height offset (mm)')
        self.assertEqual(ftin.label('WIDTH'), 'WIDTH (FT-IN)')


class Display(unittest.TestCase):
    def test_metric_text(self):
        self.assertEqual(U.LengthUnit('millimeters').text(2800 * MM), '2800')
        self.assertEqual(U.LengthUnit('meters').text(1250 * MM), '1.25')
        self.assertEqual(U.LengthUnit('meters').text(3000 * MM), '3')
        self.assertEqual(U.LengthUnit('centimeters').text(125 * MM), '12.5')

    def test_imperial_text(self):
        ftin = U.LengthUnit('feetFractionalInches')
        self.assertEqual(ftin.text(3 * FT + 6.5 * IN), '3\' - 6 1/2"')
        self.assertEqual(ftin.text(9 * FT), '9\' - 0"')
        self.assertEqual(U.LengthUnit('fractionalInches').text(6.125 * IN), '6 1/8"')
        self.assertEqual(U.LengthUnit('feet').text(2.5), '2.5')

    def test_defaults_kept_in_mm_show_in_the_project_unit(self):
        self.assertEqual(U.LengthUnit('millimeters').default_text(2800), '2800')
        self.assertEqual(U.LengthUnit('meters').default_text(2800), '2.8')
        self.assertEqual(U.LengthUnit('feetFractionalInches').default_text(2800),
                         '9\' - 2 1/4"')


class Parsing(unittest.TestCase):
    def assertFeet(self, unit, text, feet):
        self.assertAlmostEqual(unit.parse(text), feet, places=6, msg=text)

    def test_bare_numbers_use_the_project_unit(self):
        self.assertFeet(U.LengthUnit('millimeters'), '1200', 1200 * MM)
        self.assertFeet(U.LengthUnit('meters'), '1.2', 1200 * MM)
        self.assertFeet(U.LengthUnit('meters'), '2,5', 2500 * MM)
        self.assertFeet(U.LengthUnit('feetFractionalInches'), '6', 6 * FT)
        self.assertFeet(U.LengthUnit('fractionalInches'), '6 1/2', 6.5 * IN)

    def test_explicit_units_win_in_any_project(self):
        for unit in (U.LengthUnit('millimeters'), U.LengthUnit('feetFractionalInches')):
            self.assertFeet(unit, '1200 mm', 1200 * MM)
            self.assertFeet(unit, '1.2m', 1200 * MM)
            self.assertFeet(unit, '42"', 42 * IN)
            self.assertFeet(unit, "3'", 3 * FT)
            self.assertFeet(unit, '30 cm', 300 * MM)

    def test_feet_and_inches(self):
        unit = U.LengthUnit('feetFractionalInches')
        for text in ("3'-6\"", "3' 6\"", "3'6\"", "3ft 6in", "3' - 6\"", "3'-6"):
            self.assertFeet(unit, text, 3 * FT + 6 * IN)
        self.assertFeet(unit, "3'-6 1/2\"", 3 * FT + 6.5 * IN)
        self.assertFeet(unit, "-1'-6\"", -(1 * FT + 6 * IN))
        # Typographic quotes (pasted from Word / e-mail) count as ' and "
        self.assertFeet(unit, '3\u2019-6\u201d', 3 * FT + 6 * IN)
        self.assertFeet(unit, '3\u2032 6\u2033', 3 * FT + 6 * IN)

    def test_round_trip_display_then_parse(self):
        for key in ('millimeters', 'meters', 'feetFractionalInches', 'feet', 'inches'):
            unit = U.LengthUnit(key)
            for mm in (0, 12, 300, 2800, 12345):
                feet = mm * MM
                back = unit.parse(unit.text(feet))
                tolerance = (1.0 / 16) * IN if unit.style != 'decimal' else \
                    0.5 * 10 ** -unit.decimals * unit.ft_per_unit
                self.assertLessEqual(abs(back - feet), tolerance + 1e-9, (key, mm))

    def test_bad_text_says_what_is_accepted(self):
        with self.assertRaises(ValueError) as ctx:
            U.LengthUnit('millimeters').parse('abc')
        self.assertIn('mm', str(ctx.exception))
        self.assertIn("3'-6\"", str(ctx.exception))
        with self.assertRaises(ValueError):
            U.LengthUnit('millimeters').parse('  ')

    def test_parse_mm_for_code_that_still_works_in_mm(self):
        self.assertAlmostEqual(U.LengthUnit('meters').parse_mm('2.8'), 2800.0, places=6)
        self.assertAlmostEqual(U.LengthUnit('feetFractionalInches').parse_mm('1\''), 304.8,
                               places=6)



class NegativeAndSentences(unittest.TestCase):
    def test_negative_under_one_foot_keeps_its_sign(self):
        ftin = U.LengthUnit('feetFractionalInches')
        self.assertAlmostEqual(ftin.parse('-0\' - 2"'), -2 * IN, places=9)
        self.assertAlmostEqual(ftin.parse(ftin.default_text(-50)), -50 * MM, delta=IN / 16)
        self.assertAlmostEqual(ftin.parse("-1'-6\""), -(1 * FT + 6 * IN), places=9)
        self.assertAlmostEqual(ftin.parse('-0.5 m'), -500 * MM, places=9)

    def test_show_adds_the_unit_for_sentences(self):
        self.assertEqual(U.LengthUnit('millimeters').show(2800 * MM), '2800 mm')
        self.assertEqual(U.LengthUnit('meters').show(1250 * MM), '1.25 m')
        self.assertEqual(U.LengthUnit('feetFractionalInches').show(9 * FT), '9\' - 0"')
        self.assertEqual(U.LengthUnit('fractionalInches').show(6 * IN), '6"')


class PaperUnits(unittest.TestCase):
    def test_paper_sizes_follow_the_unit_system_not_the_unit(self):
        self.assertEqual(U.paper_unit(U.LengthUnit('meters')).tag, 'mm')
        self.assertEqual(U.paper_unit(U.LengthUnit('millimeters')).tag, 'mm')
        self.assertEqual(U.paper_unit(U.LengthUnit('feetFractionalInches')).tag, 'in')
        self.assertEqual(U.paper_unit(U.LengthUnit('feet')).tag, 'in')
        self.assertEqual(U.paper_unit(None).tag, 'mm')
        self.assertEqual(U.project_paper_unit(None).tag, 'mm')


class Precision(unittest.TestCase):
    def test_feet_inches_to_a_sixteenth_written_like_revit(self):
        ftin = U.LengthUnit('feetFractionalInches')
        self.assertEqual(ftin.text(0.5 * IN), '0\' - 0 1/2"')
        self.assertEqual(ftin.text(1.0 / 16 * IN), '0\' - 0 1/16"')
        self.assertEqual(ftin.text(3 * FT + 6.5 * IN), '3\' - 6 1/2"')
        self.assertAlmostEqual(ftin.parse(ftin.text(1.0 / 16 * IN)), 1.0 / 16 * IN, places=9)

    def test_paper_sizes_keep_small_text_heights(self):
        mm = U.paper_unit(U.LengthUnit('meters'))
        inch = U.paper_unit(U.LengthUnit('feetFractionalInches'))
        self.assertEqual(mm.text(2.5 * MM), '2.5')
        self.assertEqual(mm.text(70 * MM), '70')
        self.assertEqual(inch.text(3.0 / 32 * IN), '3/32"')
        self.assertEqual(inch.text(1.25 * IN), '1 1/4"')

if __name__ == '__main__':
    unittest.main()
