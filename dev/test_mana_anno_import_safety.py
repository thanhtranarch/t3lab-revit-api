# -*- coding: utf-8 -*-
"""Regression tests for the ManaAnno sidebar rail after the 2026-10-02 cleanup.

The Utilities (wrench) and Settings (gear) modes were removed together with the
helper modules only they used (CopyAnnotationDialog, TagCheckerDialog,
RenumberAlongSpline, UpperAll, TagChecker.xaml). These tests keep it that way:

  - ManaAnnoDialog never imports a removed module again (a lazy import of a
    deleted file would only fail when the user clicks the button);
  - every rail ToggleButton maps to an existing TabItem, in order, so the
    headerless TabControl never selects a missing page.
"""
import ast
import os
import re
import sys
import unittest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, "T3Lab.extension", "lib")
GUI_DIR = os.path.join(LIB_DIR, "GUI")
MANA_ANNO = os.path.join(GUI_DIR, "ManaAnnoDialog.py")
MANA_ANNO_XAML = os.path.join(GUI_DIR, "Tools", "ManaAnno.xaml")
REMOVED_MODULES = {
    "CopyAnnotationDialog",
    "TagCheckerDialog",
    "UpperAll",
    "RenumberAlongSpline",
}
REMOVED_NAMES = (
    "nav_utils", "nav_settings",
    "btn_util_copy_anno", "btn_util_renumber_spline",
    "btn_util_upper_all", "btn_util_tag_checker",
    "chk_auto_select", "chk_include_groups", "chk_confirm_delete",
)


def _read(path):
    with open(path, encoding="utf-8-sig") as source:
        return source.read()


class ManaAnnoRailCleanup(unittest.TestCase):
    def test_no_imports_of_removed_modules(self):
        offenders = []
        for node in ast.walk(ast.parse(_read(MANA_ANNO), filename=MANA_ANNO)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [alias.name for alias in node.names]
            for name in names:
                if name.split(".")[-1] in REMOVED_MODULES:
                    offenders.append((node.lineno, name))
        self.assertEqual(offenders, [])

    def test_removed_modules_are_gone(self):
        leftovers = [
            path for path in (
                os.path.join(GUI_DIR, "CopyAnnotationDialog.py"),
                os.path.join(GUI_DIR, "TagCheckerDialog.py"),
                os.path.join(GUI_DIR, "Tools", "TagChecker.xaml"),
                os.path.join(LIB_DIR, "Utils", "UpperAll.py"),
                os.path.join(LIB_DIR, "Utils", "RenumberAlongSpline.py"),
            ) if os.path.exists(path)
        ]
        self.assertEqual(leftovers, [])

    def test_removed_controls_not_referenced(self):
        xaml, code = _read(MANA_ANNO_XAML), _read(MANA_ANNO)
        found = [name for name in REMOVED_NAMES if name in xaml or name in code]
        self.assertEqual(found, [])

    def test_rail_buttons_map_to_tabs_in_order(self):
        xaml, code = _read(MANA_ANNO_XAML), _read(MANA_ANNO)
        rail = re.findall(r'<ToggleButton x:Name="(nav_\w+)"[^>]*Click="(\w+)"', xaml)
        tab_count = len(re.findall(r"<TabItem[\s>]", xaml))
        self.assertEqual([name for name, _ in rail], ["nav_dim", "nav_txt", "nav_dimtext"])
        self.assertEqual(tab_count, len(rail))
        for index, (name, handler) in enumerate(rail):
            body = re.search(
                r"def {}\(self, sender, args\):(.*?)(?=\n    def |\Z)".format(handler),
                code, re.S)
            self.assertIsNotNone(body, "missing handler " + handler)
            self.assertIn("self.{})".format(name), body.group(1))
            self.assertIn("SelectedIndex = {}".format(index), body.group(1))


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
