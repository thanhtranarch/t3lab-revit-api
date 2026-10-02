# -*- coding: utf-8 -*-
"""CAD to Elements — the one-shell layout contract (`CADToElements.xaml`).

The user's complaint was that switching modes made the UI jump. These tests
pin the structure that prevents it:

- one rail tile `btn_mode_<key>` and one options grid `opt_<key>` per mode in
  `Snippets/_cad_geometry.MODES`, in that order;
- every options grid has the SAME column and row skeleton (fixed heights), and
  all of them sit in the same cell of one options card;
- row 0 of every options grid is the CREATE AS / NAMING choice and row 1 the
  type (or view / style) selector, so those never change place;
- exactly one layer list, toolbar, tally and footer, shared by every mode;
- the window is the L size class and never sizes to content;
- every x:Name the dialog reads and every XAML event handler exist.

Run: python3 dev/test_cad_to_elements_layout.py
"""
import ast
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, "T3Lab.extension", "lib")
XAML = os.path.join(LIB, "GUI", "Tools", "CADToElements.xaml")
DIALOG = os.path.join(LIB, "GUI", "CADToElementsDialog.py")
sys.path.insert(0, LIB)

from Snippets import _cad_geometry as geo  # noqa: E402

P = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
X = "{http://schemas.microsoft.com/winfx/2006/xaml}"


def read(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def body_root():
    src = read(XAML)
    return ET.fromstring(src)


def by_name(root):
    return {el.get(X + "Name"): el for el in root.iter() if el.get(X + "Name")}


def skeleton(grid):
    cols = [c.get("Width") for c in grid.find(P + "Grid.ColumnDefinitions")]
    rows = [r.get("Height") for r in grid.find(P + "Grid.RowDefinitions")]
    return cols, rows


class OneShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = body_root()
        cls.names = by_name(cls.root)
        cls.parent = {c: p for p in cls.root.iter() for c in p}

    def test_source_row_lines_up_with_the_cards_below(self):
        # CAD SOURCE | LEVEL share the column split of OPTIONS | LAYERS, so the
        # two combos end exactly where the two cards end (owner, 2026-10-02).
        cols = lambda g: [c.get("Width") for c in g.find(P + "Grid.ColumnDefinitions")]
        source = self.parent[self.parent[self.names["cmb_cad_files"]]]
        body = self.parent[source]
        cards = [g for g in body if g.tag == P + "Grid" and g.get("Grid.Row") == "4"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cols(source), cols(cards[0]))

    def test_window_is_size_class_l_and_not_sized_to_content(self):
        self.assertEqual((self.root.get("Width"), self.root.get("Height")), ("1000", "620"))
        self.assertIsNone(self.root.get("SizeToContent"))

    def test_rail_tiles_follow_the_mode_table(self):
        tiles = [el.get(X + "Name") for el in self.root.iter(P + "ToggleButton")
                 if (el.get(X + "Name") or "").startswith("btn_mode_")]
        self.assertEqual(tiles, ["btn_mode_" + k for k in geo.MODE_KEYS])
        for k in geo.MODE_KEYS:
            tile = self.names["btn_mode_" + k]
            self.assertEqual(tile.get("Tag"), k)
            self.assertEqual(tile.get("Click"), "mode_tile_clicked")
            self.assertTrue(tile.get("ToolTip"), k)

    def test_every_mode_shares_one_options_skeleton_in_one_cell(self):
        grids = [self.names["opt_" + k] for k in geo.MODE_KEYS]
        first = skeleton(grids[0])
        self.assertTrue(all(h != "Auto" for h in first[1][:-1]),
                        "option rows must have fixed heights, not Auto")
        for k, grid in zip(geo.MODE_KEYS, grids):
            self.assertEqual(skeleton(grid), first, "opt_%s skeleton differs" % k)
            self.assertIs(self.parent[grid], self.parent[grids[0]], "opt_%s in another cell" % k)
            self.assertIsNone(grid.get("Grid.Row"))
        visible = [k for k, g in zip(geo.MODE_KEYS, grids) if g.get("Visibility") != "Collapsed"]
        self.assertEqual(visible, ["wall"])

    def test_create_as_row_and_type_row_are_in_the_same_slot(self):
        for k in geo.MODE_KEYS:
            grid = self.names["opt_" + k]
            row0 = [c for c in grid if c.get("Grid.Row") == "0"]
            row1 = [c for c in grid if c.get("Grid.Row") == "1"]
            self.assertEqual(len(row0), 1, k)
            self.assertTrue(any(el.tag == P + "RadioButton" for el in row0[0].iter()), k)
            self.assertEqual(len(row1), 1, k)
            self.assertTrue(any(el.tag == P + "ComboBox" for el in row1[0].iter()), k)

    def test_options_never_collapse_inside_a_mode(self):
        for k in geo.MODE_KEYS:
            for el in self.names["opt_" + k].iter():
                if el is self.names["opt_" + k]:
                    continue
                self.assertNotEqual(el.get("Visibility"), "Collapsed",
                                    "opt_%s hides %s — disable it instead" % (k, el.tag))

    def test_one_shared_layer_list_toolbar_and_footer(self):
        src = read(XAML)
        for name in ("grid_layers", "txt_layer_search", "btn_ai_select",
                     "txt_layer_tally", "txt_layers_empty",
                     "cmb_cad_files", "cmb_levels", "btn_run", "btn_refresh"):
            self.assertEqual(src.count('x:Name="%s"' % name), 1, name)
        # The header checkbox selects / clears every layer: no separate buttons.
        for name in ("btn_layers_all", "btn_layers_clear"):
            self.assertNotIn('x:Name="%s"' % name, src)
        # The title-bar X is the only close control (2026-10-02): no footer Close.
        self.assertNotIn('x:Name="btn_close_bar"', src)
        self.assertEqual(len(list(self.root.iter(P + "DataGrid"))), 1)
        self.assertNotIn("SizeToContent", src)


class XamlAndPythonAgree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read(XAML)
        cls.py = read(DIALOG)
        cls.xnames = set(re.findall(r'x:Name="([^"]+)"', cls.src))
        tree = ast.parse(cls.py)
        cls.methods = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def test_every_self_attribute_read_exists(self):
        used = set(re.findall(r"self\.((?:cmb|txt|chk|rb|btn|lbl|opt|grid|cb|dot|pb)_\w+)", self.py))
        missing = sorted(n for n in used if n not in self.xnames and n not in self.methods)
        self.assertEqual(missing, [])

    def test_every_combo_filled_by_name_exists(self):
        filled = set(re.findall(r'_(?:fill|pick|need|category)\("(\w+)"', self.py))
        self.assertEqual(sorted(n for n in filled if n not in self.xnames), [])

    def test_every_xaml_handler_is_a_method(self):
        handlers = set(re.findall(r'\s(?:Click|Checked|SelectionChanged|TextChanged)="(\w+)"', self.src))
        self.assertEqual(sorted(h for h in handlers if h not in self.methods), [])

    def test_ai_mode_single_button(self):
        self.assertEqual(self.src.count('Text="AI Select"'), 1)
        self.assertIn('AI_TOOL = "CADToElements"', self.py)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
