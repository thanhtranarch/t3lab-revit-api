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


P = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
X = "{http://schemas.microsoft.com/winfx/2006/xaml}"


class ManaAnnoOneFrame(unittest.TestCase):
    """2026-10-02 redesign: every rail page uses the same frame, so switching
    pages nothing jumps, and the window has one primary, in the footer."""

    def root(self):
        import xml.etree.ElementTree as ET
        return ET.parse(MANA_ANNO_XAML).getroot()

    def test_every_page_has_the_same_frame(self):
        frames = []
        for tab in self.root().iter(P + "TabItem"):
            grid = tab.find(P + "Grid")
            cols = [c.get("Width") for c in grid.find(P + "Grid.ColumnDefinitions")]
            rows = [r.get("Height") for r in grid.find(P + "Grid.RowDefinitions")]
            panels = [b for b in grid if b.tag == P + "Border"
                      and b.get("Style") == "{StaticResource T3.Panel}"]
            frames.append((grid.get("Margin"), cols, rows,
                           [(b.get("Grid.Row"), b.get("Grid.Column"), b.get("Grid.ColumnSpan"))
                            for b in panels]))
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0], frames[1])
        self.assertEqual(frames[0], frames[2])
        # left panel, right panel, one full-width settings/actions card
        self.assertEqual(frames[0][3], [("2", "0", None), ("2", "2", None), ("4", None, "3")])

    def test_one_primary_in_the_footer(self):
        root = self.root()
        primaries = [b for b in root.iter(P + "Button")
                     if b.get("Style") == "{StaticResource T3.Button.Primary}"]
        self.assertEqual([b.get(X + "Name") for b in primaries], ["btn_primary"])
        footer = next(b for b in root.iter(P + "Border")
                      if b.get("Style") == "{StaticResource T3.FooterBar}")
        self.assertIn(primaries[0], list(footer.iter(P + "Button")))
        self.assertEqual(primaries[0].get("IsDefault"), "True")

    def test_no_buttons_that_repeat_the_header_checkbox_or_the_x(self):
        labels = []
        for button in self.root().iter(P + "Button"):
            labels.append(button.get("Content") or "")
            labels.extend(t.get("Text") or "" for t in button.iter(P + "TextBlock"))
        for duplicate in ("Select All", "Select None", "Clear", "Close", "Cancel", "Done"):
            self.assertNotIn(duplicate, labels)

    def test_every_status_line_names_its_page(self):
        tree = ast.parse(_read(MANA_ANNO), filename=MANA_ANNO)
        missing = [n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "_status" and len(n.args) < 2]
        self.assertEqual(missing, [])


class ManaAnnoPageStatus(unittest.TestCase):
    """The footer shows the status of the page on screen, and the primary
    follows the page (Jump to View / Apply Overrides)."""

    METHODS = ("_status", "_paint_status", "_on_page_shown", "_sync_primary")

    def window(self):
        from types import SimpleNamespace
        tree = ast.parse(_read(MANA_ANNO), filename=MANA_ANNO)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "AnnotationManagerWindow")
        cls.bases = []
        cls.body = [m for m in cls.body if isinstance(m, ast.FunctionDef)
                    and m.name in self.METHODS]
        consts = [n for n in tree.body if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") in ("PAGE_DIM", "_DOT_KEYS")
                          or isinstance(t, ast.Tuple) for t in n.targets)]
        scope = {}
        module = ast.Module(body=consts + [cls], type_ignores=[])
        exec(compile(module, MANA_ANNO, "exec"), scope)
        win = scope["AnnotationManagerWindow"].__new__(scope["AnnotationManagerWindow"])
        win.status = SimpleNamespace(Text="", ToolTip="")
        win.status_dot = SimpleNamespace(Fill=None)
        win.FindResource = lambda key: key
        win.btn_primary = SimpleNamespace(Content="", IsEnabled=True, ToolTip="")
        win._dimtext_update_scope = lambda: None
        win._dim_submode, win._txt_submode = "instances", "notes"
        win._page = scope["PAGE_DIM"]
        win._page_status = {0: ("Ready.", "idle"), 1: ("Ready.", "idle"), 2: ("Ready.", "idle")}
        return win, scope

    def test_status_of_another_page_waits_for_that_page(self):
        win, scope = self.window()
        win._status("Loaded 7 dimension(s).", scope["PAGE_DIM"])
        win._status("Loaded 13 text note(s).", scope["PAGE_TXT"], "ok")
        self.assertEqual(win.status.Text, "Loaded 7 dimension(s).")
        win._on_page_shown(scope["PAGE_TXT"])
        self.assertEqual(win.status.Text, "Loaded 13 text note(s).")
        self.assertEqual(win.status_dot.Fill, "T3.Success.Accent")
        win._on_page_shown(scope["PAGE_DIM"])
        self.assertEqual(win.status.Text, "Loaded 7 dimension(s).")

    def test_primary_relabels_per_page_and_mode(self):
        win, scope = self.window()
        win._on_page_shown(scope["PAGE_DIM"])
        self.assertEqual((win.btn_primary.Content, win.btn_primary.IsEnabled), ("Jump to View", True))
        win._dim_submode = "types"
        win._sync_primary()
        self.assertFalse(win.btn_primary.IsEnabled)            # types have no view
        win._on_page_shown(scope["PAGE_DIMTEXT"])
        self.assertEqual((win.btn_primary.Content, win.btn_primary.IsEnabled),
                         ("Apply Overrides", True))



class ActiveDocumentPerLaunch(unittest.TestCase):
    """The module is imported once per Revit session; the document is not."""

    def _function(self, path, name):
        tree = ast.parse(_read(path))
        return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)

    def test_mana_anno_rereads_the_document_before_opening(self):
        show = self._function(MANA_ANNO, "show_dialog")
        first = show.body[0]
        self.assertIsInstance(first, ast.Expr)
        self.assertEqual(first.value.func.id, "_refresh_active_document")
        refresh = ast.unparse(self._function(MANA_ANNO, "_refresh_active_document"))
        self.assertIn("global doc, uidoc", refresh)
        self.assertIn("DimTextDialog.doc = doc", refresh)
        self.assertIn("DimTextDialog.uidoc = uidoc", refresh)

    def test_dim_text_rereads_the_document_before_opening(self):
        show = ast.unparse(self._function(os.path.join(GUI_DIR, "DimTextDialog.py"),
                                          "show_dialog"))
        self.assertIn("global doc, uidoc", show)
        self.assertLess(show.index("revit.doc"), show.index("DimTextWindow()"))

if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
