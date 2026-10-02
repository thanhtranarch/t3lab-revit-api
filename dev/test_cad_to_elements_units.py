# -*- coding: utf-8 -*-
"""CAD to Elements — every length follows the project's unit (2026-10-02).

Owner: "the tools that deal with units must detect which units the file uses,
switch to them and show the appropriate units". For CAD to Elements that means:

- no option label carries a hard-coded "(MM)": each length label has an
  x:Name and the dialog writes "HEIGHT (MM)" / "HEIGHT (FT-IN)" from the unit
  `Snippets/_units.project_length_unit(doc)` read when the window opens;
- defaults the code keeps in mm are shown in the project unit, and the XAML no
  longer holds the mm numbers;
- every typed length is parsed by the helper (bare number = project unit,
  "1200 mm" / 3'-6" always work) and there is no 304.8 left in the dialog;
- the column size step and per-size type names use mm / inches (paper_unit),
  never m / ft.

The dialog imports WPF, so the methods under test are lifted out of the source
with `ast` and run against plain stand-in controls (same trick as
dev/test_ui_overlap.py).

Run: python3 dev/test_cad_to_elements_units.py
"""
import ast
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, "T3Lab.extension", "lib")
XAML = os.path.join(LIB, "GUI", "Tools", "CADToElements.xaml")
DIALOG = os.path.join(LIB, "GUI", "CADToElementsDialog.py")
sys.path.insert(0, LIB)

from Snippets import _cad_geometry as geo  # noqa: E402
from Snippets import _units as U  # noqa: E402

P = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
X = "{http://schemas.microsoft.com/winfx/2006/xaml}"

MM = 1.0 / 304.8
IN = 1.0 / 12.0

# TextBoxes of the options card that are NOT lengths (a name, a count, a letter).
NOT_LENGTHS = {"txt_room_name", "txt_grid_start_number", "txt_grid_start_letter"}
MEP_LENGTHS = {"txt_mep_width": "lbl_mep_width", "txt_mep_height": "lbl_mep_height",
               "txt_mep_offset": "lbl_mep_offset"}
UNITS = ("millimeters", "meters", "centimeters", "feet", "inches",
         "feetFractionalInches", "fractionalInches")


def read(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


SRC = read(DIALOG)
TREE = ast.parse(SRC)
XSRC = read(XAML)


def module_value(name):
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


LENGTH_FIELDS = module_value("LENGTH_FIELDS")
ROUNDING_DEFAULT = module_value("COLUMN_ROUNDING_DEFAULT")

METHODS = ("_apply_units", "_len", "_length", "_fill_mep")


def load_dialog():
    """The unit code of the dialog, runnable without WPF / Revit."""
    body = []
    for node in ast.parse(SRC).body:            # own copy: the class is trimmed below
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ("LENGTH_FIELDS", "COLUMN_ROUNDING_DEFAULT")
                for t in node.targets):
            body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in ("label_name", "with_unit"):
            body.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "_InputError":
            body.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "CADToElementsWindow":
            node.bases = []
            node.body = [m for m in node.body
                         if isinstance(m, ast.FunctionDef) and m.name in METHODS]
            body.append(node)
    scope = {"geo": geo}
    exec(compile(ast.Module(body=body, type_ignores=[]), DIALOG, "exec"), scope)
    return scope


SCOPE = load_dialog()


class Box(object):
    """Stand-in for a TextBox / TextBlock."""

    def __init__(self, text=""):
        self.Text = text
        self.IsEnabled = True


def window(unit_key):
    win = SCOPE["CADToElementsWindow"]()
    for name in re.findall(r'x:Name="((?:txt|lbl)_\w+)"', XSRC):
        setattr(win, name, Box())
    win._unit = U.LengthUnit(unit_key)
    win._size_unit = U.paper_unit(win._unit)
    return win


def length(win, text, label="Height", **kw):
    return win._length(Box(text), label, **kw)


# ═══════════════════════════════════════════════════════════════════════════

class XamlHasNoUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ET.fromstring(XSRC)
        cls.names = {el.get(X + "Name"): el for el in cls.root.iter() if el.get(X + "Name")}
        cls.parent = {c: p for p in cls.root.iter() for c in p}
        cls.lengths = dict((f, SCOPE["label_name"](f)) for f, _l, _d in LENGTH_FIELDS)
        cls.lengths.update(MEP_LENGTHS)
        cls.lengths["txt_column_rounding"] = "lbl_column_rounding"

    def test_no_label_carries_a_hard_coded_mm(self):
        self.assertNotIn("(MM)", XSRC)
        self.assertNotIn("(mm)", XSRC)

    def test_every_option_textbox_is_a_known_length_or_declared_not(self):
        boxes = set()
        for k in geo.MODE_KEYS:
            boxes.update(el.get(X + "Name") for el in self.names["opt_" + k].iter(P + "TextBox"))
        self.assertEqual(sorted(boxes - NOT_LENGTHS), sorted(self.lengths))

    def test_each_length_has_a_named_label_above_it_and_no_mm_default(self):
        for field, label in self.lengths.items():
            box = self.names[field]
            self.assertIsNone(box.get("Text"), "%s still has an mm default in the XAML" % field)
            stack = list(self.parent[box])
            above = stack[stack.index(box) - 1]
            self.assertEqual(above.get(X + "Name"), label, field)
            self.assertEqual(above.get("Style"), "{StaticResource T3.Label}", field)
            self.assertEqual(above.get("Text"), above.get("Text").upper(), field)

    def test_hints_that_quote_a_length_are_named(self):
        self.assertIn("txt_wall_hint", self.names)
        self.assertIn("txt_column_hint", self.names)


class LabelsAndDefaults(unittest.TestCase):
    def test_metric_project_shows_mm_and_the_old_defaults(self):
        win = window("millimeters")
        win._apply_units()
        self.assertEqual(win.lbl_wall_height.Text, "HEIGHT (MM)")
        self.assertEqual(win.lbl_beam_offset.Text, "TOP OFFSET (MM)")
        self.assertEqual(win.lbl_lines_offset.Text, "OFFSET FROM LEVEL (MM)")
        self.assertEqual((win.txt_wall_height.Text, win.txt_wall_thickness.Text,
                          win.txt_ceiling_offset.Text, win.txt_beam_offset.Text,
                          win.txt_grid_min_length.Text), ("3000", "200", "2700", "-50", "1000"))
        self.assertEqual((win.lbl_column_rounding.Text, win.txt_column_rounding.Text),
                         ("SIZE ROUNDING (MM)", "10"))
        self.assertIn("610 mm", win.txt_wall_hint.Text)
        self.assertIn("100 mm", win.txt_column_hint.Text)
        self.assertIn("3000 mm", win.txt_column_hint.Text)

    def test_imperial_project_shows_feet_inches(self):
        win = window("feetFractionalInches")
        win._apply_units()
        self.assertEqual(win.lbl_wall_height.Text, "HEIGHT (FT-IN)")
        self.assertEqual(win.lbl_mep_offset.Text, "OFFSET FROM LEVEL (FT-IN)")
        self.assertEqual(win.txt_wall_height.Text, "9' - 10 1/8\"")
        self.assertEqual(win.txt_wall_offset.Text, "0' - 0\"")
        # Size rounding is in inches (type sizes), with an inch default.
        self.assertEqual((win.lbl_column_rounding.Text, win.txt_column_rounding.Text),
                         ("SIZE ROUNDING (IN)", '1/2"'))
        self.assertIn("2' - 0\"", win.txt_wall_hint.Text)
        self.assertNotIn("mm", win.txt_wall_hint.Text + win.txt_column_hint.Text)

    def test_metres_project_keeps_type_sizes_in_mm(self):
        win = window("meters")
        win._apply_units()
        self.assertEqual((win.lbl_wall_height.Text, win.txt_wall_height.Text), ("HEIGHT (M)", "3"))
        self.assertEqual((win.lbl_column_rounding.Text, win.txt_column_rounding.Text),
                         ("SIZE ROUNDING (MM)", "10"))

    def test_every_default_reads_back_as_the_mm_value_in_every_unit(self):
        for key in UNITS:
            win = window(key)
            win._apply_units()
            unit = win._unit
            tol = (1.0 / 16) * IN if unit.style != "decimal" else \
                0.5 * 10 ** -unit.decimals * unit.ft_per_unit
            for field, _label, default_mm in LENGTH_FIELDS:
                got = win._length(getattr(win, field), field)
                self.assertLessEqual(abs(got - default_mm * MM), tol + 1e-9,
                                     (key, field, getattr(win, field).Text))
            step = win._length(win.txt_column_rounding, "Size rounding", unit=win._size_unit)
            expect = win._size_unit.to_feet(ROUNDING_DEFAULT[win._size_unit.tag])
            self.assertAlmostEqual(step, expect, places=9, msg=key)

    def test_negative_default_keeps_its_sign_in_feet_inches(self):
        # -50 mm shows as -0' - 2"; the helper alone reads that back as +2".
        win = window("feetFractionalInches")
        win._apply_units()
        self.assertTrue(win.txt_beam_offset.Text.startswith("-"))
        self.assertLess(win._length(win.txt_beam_offset, "Top offset"), 0)


class MepFollowsTheUnit(unittest.TestCase):
    def fill(self, unit_key, key):
        win = window(unit_key)
        win._busy = False
        win._fill = Mock()
        win._update_enabling = Mock()
        win._mep_types, win._mep_systems, win._mep_values = {}, {}, {}
        win.cmb_mep_line_mode = SimpleNamespace(SelectedIndex=0)
        win._fill_mep(key, restore=False)
        return win

    def test_labels_and_defaults_per_category(self):
        win = self.fill("feetFractionalInches", "duct")
        self.assertEqual(win.lbl_mep_width.Text, "WIDTH / DIAMETER (FT-IN)")
        self.assertEqual(win.txt_mep_width.Text, U.LengthUnit("feetFractionalInches").default_text(300))
        win = self.fill("millimeters", "pipe")
        self.assertEqual(win.lbl_mep_width.Text, "DIAMETER (MM)")
        self.assertEqual((win.txt_mep_width.Text, win.txt_mep_height.Text, win.txt_mep_offset.Text),
                         ("100", "", "2600"))
        win = self.fill("meters", "tray")
        self.assertEqual((win.lbl_mep_width.Text, win.txt_mep_width.Text, win.txt_mep_height.Text),
                         ("WIDTH (M)", "0.3", "0.1"))


class Parsing(unittest.TestCase):
    def test_bare_number_is_the_project_unit_and_explicit_units_win(self):
        mm, ftin, m = window("millimeters"), window("feetFractionalInches"), window("meters")
        self.assertAlmostEqual(length(mm, "2800"), 2800 * MM)
        self.assertAlmostEqual(length(m, "2.8"), 2800 * MM)
        self.assertAlmostEqual(length(ftin, "9"), 9.0)
        for win in (mm, ftin, m):
            self.assertAlmostEqual(length(win, "1200 mm"), 1200 * MM)
            self.assertAlmostEqual(length(win, "3'-6\""), 3.5)
        self.assertAlmostEqual(length(ftin, "-0' - 6\""), -0.5)

    def test_bad_text_is_an_input_error_naming_the_field(self):
        err = SCOPE["_InputError"]
        win = window("feetFractionalInches")
        with self.assertRaises(err) as ctx:
            length(win, "abc", "Base offset")
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith("Base offset: "), msg)
        self.assertIn("ft-in", msg)
        self.assertIn("options card", msg)
        with self.assertRaises(err):
            length(win, "", "Height")

    def test_ranges_are_checked_and_quoted_in_the_project_unit(self):
        err = SCOPE["_InputError"]
        mm, ftin = window("millimeters"), window("feetFractionalInches")
        with self.assertRaises(err) as ctx:
            length(mm, "0", "Height", positive=True)
        self.assertIn("more than zero", str(ctx.exception))
        with self.assertRaises(err) as ctx:
            length(ftin, "12'", "Unpaired thickness", positive=True, maximum=3000 * MM)
        self.assertIn("at most 9' - 10 1/8\"", str(ctx.exception))
        with self.assertRaises(err) as ctx:
            length(mm, "40", "Max width", minimum=50 * MM)
        self.assertIn("is 40 mm, but it must be at least 50 mm", str(ctx.exception))
        self.assertAlmostEqual(length(mm, "0", "Extend ends", minimum=0.0), 0.0)

    def test_size_rounding_reads_in_the_size_unit(self):
        win = window("feetFractionalInches")
        self.assertAlmostEqual(length(win, "1", unit=win._size_unit), 1 * IN)    # 1 inch, not 1 ft
        win = window("meters")
        self.assertAlmostEqual(length(win, "10", unit=win._size_unit), 10 * MM)  # 10 mm, not 10 m

    def test_with_unit_for_sentences(self):
        w = SCOPE["with_unit"]
        self.assertEqual(w(U.LengthUnit("millimeters"), 2800 * MM), "2800 mm")
        self.assertEqual(w(U.LengthUnit("meters"), 2800 * MM), "2.8 m")
        self.assertEqual(w(U.LengthUnit("feetFractionalInches"), 9.1875), "9' - 2 1/4\"")
        self.assertEqual(w(U.LengthUnit("fractionalInches"), 0.5 * IN), '1/2"')


class DialogSource(unittest.TestCase):
    def test_no_hand_conversion_and_no_mm_in_messages(self):
        self.assertNotIn("304.8", SRC)
        self.assertNotIn("{:g} mm", SRC)
        self.assertNotIn("to_mm(lv", SRC)

    def test_every_length_is_parsed_by_the_units_helper(self):
        fields = [f for f, _l, _d in LENGTH_FIELDS] + list(MEP_LENGTHS) + ["txt_column_rounding"]
        for field in fields:
            self.assertIn("self._length(self.%s," % field, SRC, field)
        # _num stays only for what is not a length.
        self.assertEqual(re.findall(r"self\._num\(self\.(\w+)", SRC), ["txt_grid_start_number"])

    def test_unit_is_read_when_the_window_opens_not_at_import(self):
        init = next(n for c in TREE.body if isinstance(c, ast.ClassDef)
                    and c.name == "CADToElementsWindow"
                    for n in c.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        calls = [n.func.id for n in ast.walk(init)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertIn("project_length_unit", calls)
        top = [n for n in TREE.body if not isinstance(n, (ast.ClassDef, ast.FunctionDef))]
        self.assertNotIn("project_length_unit(", "".join(ast.unparse(n) for n in top
                                                         if not isinstance(n, ast.ImportFrom)))

    def test_level_combo_shows_the_elevation_in_the_project_unit(self):
        self.assertIn('lv["label"] = u"{} ({})".format(lv["name"], self._len(lv["elevation"]))', SRC)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
