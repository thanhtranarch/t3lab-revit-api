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
from types import SimpleNamespace


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
        win.btn_primary = SimpleNamespace(Content="", IsEnabled=True, ToolTip="", IsDefault=True)
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

    def test_enter_never_applies_dim_text_overrides(self):
        """Owner decision 2026-10-02: on Dim Text the primary writes to the
        model and the page is TextBoxes, so Enter must not press it. It stays
        the default button (Enter) where it only jumps to a view."""
        win, scope = self.window()
        win._on_page_shown(scope["PAGE_DIMTEXT"])
        self.assertIs(win.btn_primary.IsDefault, False)
        win._on_page_shown(scope["PAGE_DIM"])
        self.assertIs(win.btn_primary.IsDefault, True)
        win._on_page_shown(scope["PAGE_DIMTEXT"])
        win._on_page_shown(scope["PAGE_TXT"])
        self.assertIs(win.btn_primary.IsDefault, True)
        win._txt_submode = "types"                     # disabled, still not Apply
        win._sync_primary()
        self.assertIs(win.btn_primary.IsDefault, True)

    def test_is_default_is_set_per_page_in_sync_primary_only(self):
        tree = ast.parse(_read(MANA_ANNO), filename=MANA_ANNO)
        writes = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute) and target.attr == "IsDefault":
                            writes.append((fn.name, ast.unparse(node.value)))
        self.assertEqual(sorted(writes), [("_sync_primary", "False"), ("_sync_primary", "True")])
        sync = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_sync_primary")
        dimtext = next(n for n in sync.body if isinstance(n, ast.If)
                       and "PAGE_DIMTEXT" in ast.unparse(n.test))
        self.assertIn("btn.IsDefault = False", ast.unparse(dimtext))
        self.assertNotIn("IsDefault = True", ast.unparse(dimtext))
        # The XAML keeps IsDefault="True" (rule 9) - test_one_primary_in_the_footer.



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


# ── Staged NAME / TEXT edits (owner decision 2026-10-02) ──────────────────
# Editing a type name (or a note's text) only STAGES it: yellow cell, Apply
# Changes writes every staged edit in one transaction. The window code is
# exec'd from the source with fakes in place of WPF / Revit, like the page tests.

STAGING_CONSTS = {
    "PAGE_DIM", "PAGE_TXT", "PAGE_DIMTEXT", "_DOT_KEYS", "_PAGE_NAMES",
    "_TYPE_ONLY_COLUMNS", "_INSTANCE_ONLY_COLUMNS",
    "ANNO_EDIT_FIELDS", "EDIT_FIELD", "ORIG_PREFIX", "DIRTY_PREFIX", "TIP_PREFIX",
    "STAGE_CLEAN", "STAGE_PENDING", "STAGE_FAILED", "_TYPE_KINDS", "_EDITABLE_KINDS",
}
STAGING_FUNCS = {
    "_one_line", "_stage_edit", "_cell_flags", "_apply_label", "_failure_reason",
    "_write_staged", "_settle_staged", "_apply_summary", "_discard_prompt",
}
STAGING_METHODS = {
    "_status", "_paint_status", "_sync_primary", "_set_mode_columns", "_grid",
    "_nav_button", "_flush_edit", "_commit_row_later", "_paint_staged", "_sync_apply",
    "_stage_cell_edit", "_discard_staged", "_confirm_discard", "_discard_or_keep",
    "_leave_page", "_on_closing", "_prune_staged", "_staged_element", "_apply_staged",
    "_settle_before_rename_all", "_update_nav_states", "_dt_add",
    "dim_cell_edit_ending", "txt_cell_edit_ending", "dim_apply", "txt_apply",
    "dim_submode", "txt_submode",
}


def _assign_names(node):
    names = set()
    for target in node.targets:
        for leaf in ast.walk(target):
            if isinstance(leaf, ast.Name):
                names.add(leaf.id)
    return names


def _staging_scope(extra=None, methods=STAGING_METHODS):
    """Module constants + pure helpers + the window class (chosen methods only),
    exec'd from ManaAnnoDialog.py with `extra` fakes for WPF / Revit names."""
    sys.path.insert(0, LIB_DIR)
    try:
        from GUI.GridPendingEdits import column_key, editor_text, revert_editor
        from Snippets._compat import disposing
    finally:
        sys.path.remove(LIB_DIR)
    tree = ast.parse(_read(MANA_ANNO), filename=MANA_ANNO)
    body = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and _assign_names(node) & STAGING_CONSTS:
            body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in STAGING_FUNCS:
            body.append(node)
        elif (isinstance(node, ast.ClassDef) and node.name == "AnnotationManagerWindow"
              and methods):
            node.bases = []
            node.body = [m for m in node.body
                         if isinstance(m, ast.FunctionDef) and m.name in methods]
            body.append(node)
    scope = {
        "column_key": column_key, "editor_text": editor_text,
        "revert_editor": revert_editor, "disposing": disposing,
        "Action": lambda fn: fn,
        "DispatcherPriority": SimpleNamespace(Background="Background"),
        "DataGridEditingUnit": SimpleNamespace(Cell="Cell", Row="Row"),
        "Visibility": SimpleNamespace(Visible="Visible", Collapsed="Collapsed"),
        "doc": object(),
    }
    scope.update(extra or {})
    exec(compile(ast.Module(body=body, type_ignores=[]), MANA_ANNO, "exec"), scope)
    return scope


class _Row(dict):
    """A DataRowView stand-in: rows are read and written by column name."""


class _Toggle(object):
    """Rail button / chip stand-in; compared by identity like a WPF control."""

    def __init__(self, checked=False):
        self.IsChecked = checked


class _Grid(object):
    def __init__(self):
        self.commits = []
        self.Columns = []

    def CommitEdit(self, unit, exit_editing):
        self.commits.append(unit)
        return True


class _Dialogs(object):
    """T3Dialog stand-in: confirm answers are queued, every call is recorded."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.confirms, self.warnings = [], []

    def confirm(self, message, **kw):
        self.confirms.append((message, kw))
        return self.answers.pop(0) if self.answers else False

    def show_warning(self, message, **kw):
        self.warnings.append((message, kw))


_STATUS = SimpleNamespace(Started="Started", Committed="Committed", RolledBack="RolledBack")


class _Transaction(object):
    """Revit Transaction stand-in that records what happened to it."""
    made = []
    commit_result = "Committed"

    def __init__(self, doc, name):
        self.name, self.events = name, []
        self.started = self.ended = False
        _Transaction.made.append(self)

    def Start(self):
        self.started = True
        self.events.append("start")
        return _STATUS.Started

    def GetFailureHandlingOptions(self):
        return SimpleNamespace(SetForcedModalHandling=lambda value: None)

    def SetFailureHandlingOptions(self, options):
        pass

    def Commit(self):
        self.ended = True
        self.events.append("commit")
        return _Transaction.commit_result

    def RollBack(self):
        self.ended = True
        self.events.append("rollback")

    def HasStarted(self):
        return self.started

    def HasEnded(self):
        return self.ended

    def Dispose(self):
        self.events.append("dispose")


class _NoTransaction(object):
    def __init__(self, *args):
        raise AssertionError("a cell edit opened a Revit transaction")


class _Type(object):
    """ElementType stand-in: Revit refuses a name another type already has."""

    def __init__(self, name, taken):
        self._name, self._taken = name, taken

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        if value in self._taken:
            raise ValueError("The name '{}' is already in use.\nParameter name: name".format(value))
        self._taken.discard(self._name)
        self._taken.add(value)
        self._name = value


def _type_row(elem_id, name, cat="DimType"):
    return _Row({"_id": elem_id, "_cat": cat, "Name": name, "Status": "Active",
                 "orig_Name": name, "dirty_Name": "False", "tip_Name": ""})


def _edit(row, typed, field="Name", action="Commit"):
    """DataGridCellEditEndingEventArgs stand-in."""
    column = SimpleNamespace(Binding=SimpleNamespace(Path=SimpleNamespace(Path=field)),
                             SortMemberPath=field, Header="SOMETHING ELSE")
    return SimpleNamespace(Column=column, EditAction=action, Row=SimpleNamespace(Item=row),
                           EditingElement=SimpleNamespace(Text=typed), Cancel=False)


class ManaAnnoStagingHelpers(unittest.TestCase):
    """The pure staging rules (no WPF, no Revit)."""

    def setUp(self):
        self.s = _staging_scope(methods=set())

    def test_stage_then_type_back_unstages(self):
        store = {}
        self.assertTrue(self.s["_stage_edit"](store, "7", "DimType", "Old", "New"))
        self.assertEqual(store["7"]["value"], "New")
        self.assertFalse(self.s["_stage_edit"](store, "7", "DimType", "Old", "Old"))
        self.assertEqual(store, {})

    def test_restaging_clears_a_refused_reason(self):
        store = {}
        self.s["_stage_edit"](store, "7", "DimType", "Old", "Dup")
        store["7"]["error"] = "name already in use"
        self.s["_stage_edit"](store, "7", "DimType", "Old", "Fresh")
        self.assertEqual(store["7"]["error"], "")

    def test_cell_flags_per_state(self):
        flags = self.s["_cell_flags"]
        self.assertEqual(flags(None), ("False", ""))
        flag, tip = flags({"original": "Old", "value": "New", "error": ""})
        self.assertEqual(flag, "True")
        self.assertIn("'Old'", tip)
        flag, tip = flags({"original": "Old", "value": "Dup", "error": "name already in use"})
        self.assertEqual(flag, "Failed")
        self.assertIn("name already in use", tip)

    def test_flag_values_match_the_xaml_triggers(self):
        xaml = _read(MANA_ANNO_XAML)
        for value in (self.s["STAGE_PENDING"], self.s["STAGE_FAILED"]):
            self.assertEqual(xaml.count(
                '<Trigger Property="AutomationProperties.ItemStatus" Value="%s">' % value), 2)

    def test_apply_label_carries_the_count(self):
        self.assertEqual(self.s["_apply_label"](0), "Apply Changes")
        self.assertEqual(self.s["_apply_label"](3), "Apply Changes (3)")

    def test_one_refused_write_fails_only_its_row(self):
        def write(key, entry):
            if key == "2":
                raise ValueError("The name is already in use.\nParameter name: name")
        items = [("1", {}), ("2", {}), ("3", {})]
        done, failed = self.s["_write_staged"](items, write)
        self.assertEqual(done, ["1", "3"])
        self.assertEqual(failed, [("2", "The name is already in use.")])

    def test_settle_keeps_refused_edits_staged_with_reason(self):
        store = {"1": {"value": "A", "error": ""}, "2": {"value": "B", "error": ""}}
        self.s["_settle_staged"](store, ["1"], [("2", "duplicate")])
        self.assertEqual(list(store), ["2"])
        self.assertEqual(store["2"]["error"], "duplicate")

    def test_summary_counts_and_names_the_failure(self):
        summary = self.s["_apply_summary"]
        self.assertEqual(summary(3, []), ("Renamed 3 type(s).", "ok"))
        self.assertEqual(summary(2, [], notes=True), ("Updated 2 text note(s).", "ok"))
        msg, kind = summary(1, [("ARC_DIM", "already in use"), ("X", "bad")])
        self.assertTrue(msg.startswith("Renamed 1 type(s). 2 could not be renamed: "
                                       "'ARC_DIM' - already in use (+1 more)"))
        self.assertEqual(kind, "warning")
        self.assertEqual(summary(0, [("X", "bad")])[1], "error")

    def test_discard_prompt_names_the_count(self):
        message, ok = self.s["_discard_prompt"](2, "close Annotation Manager")
        self.assertEqual(ok, "Discard 2 change(s)")
        self.assertIn("2 unapplied change(s)", message)


class ManaAnnoStagedEdits(unittest.TestCase):
    """Window behaviour around staged edits, with WPF / Revit faked."""

    def make(self, *answers, transaction=_NoTransaction):
        self.dialogs = _Dialogs(*answers)
        _Transaction.made = []
        _Transaction.commit_result = "Committed"
        s = _staging_scope({"T3Dialog": self.dialogs, "Transaction": transaction,
                            "DB": SimpleNamespace(TransactionStatus=_STATUS)})
        win = s["AnnotationManagerWindow"].__new__(s["AnnotationManagerWindow"])
        win.status = SimpleNamespace(Text="", ToolTip="")
        win.status_dot = SimpleNamespace(Fill=None)
        win.FindResource = lambda key: key
        win.btn_primary = SimpleNamespace(Content="", IsEnabled=True, ToolTip="", IsDefault=True)
        for name in ("btn_dim_apply", "btn_txt_apply"):
            setattr(win, name, SimpleNamespace(Content="Apply Changes", IsEnabled=False,
                                               ToolTip="", Visibility="Visible"))
        win.dg_dim, win.dg_txt = _Grid(), _Grid()
        win.Dispatcher = SimpleNamespace(BeginInvoke=lambda priority, action: action())
        for name in ("nav_dim", "nav_txt", "nav_dimtext", "rb_dim_inst", "rb_dim_type",
                     "rb_notes", "rb_types"):
            setattr(win, name, _Toggle())
        win.nav_dim.IsChecked = True
        win._dim_dt = win._txt_dt = object()           # the handlers' "init done" guard
        win._dim_search_timer = SimpleNamespace(Stop=lambda: None)
        win._txt_search_timer = SimpleNamespace(Stop=lambda: None)
        win._page = s["PAGE_DIM"]
        win._page_status = {0: ("Ready.", "idle"), 1: ("Ready.", "idle"), 2: ("Ready.", "idle")}
        win._staged = {s["PAGE_DIM"]: {}, s["PAGE_TXT"]: {}}
        win._dim_submode, win._txt_submode = "types", "notes"
        self.calls = []
        for name in ("_fill_dims", "_fill_txts", "_refresh_dim_cache", "_refresh_txt_cache",
                     "_load_sidebar_lists", "_apply_dim_mode", "_apply_txt_mode",
                     "_dimtext_update_scope"):
            setattr(win, name, (lambda n: lambda *a, **k: self.calls.append(n))(name))
        self.taken = {"Old A", "Old B", "Taken"}
        win._dim_type_by_id = {"1": _Type("Old A", self.taken), "2": _Type("Old B", self.taken)}
        win._txt_type_by_id, win._txt_record_by_id = {}, {}
        self.s, self.win = s, win
        return win

    def stage(self, row, typed, page=None):
        page = self.s["PAGE_DIM"] if page is None else page
        grid = self.win._grid(page)
        handler = self.win.dim_cell_edit_ending if page == self.s["PAGE_DIM"] \
            else self.win.txt_cell_edit_ending
        args = _edit(row, typed)
        handler(grid, args)
        return args

    def test_typing_a_name_stages_it_and_writes_nothing(self):
        win = self.make()                                  # Transaction() would raise
        row = _type_row("1", "Old A")
        self.stage(row, "New A")
        self.assertEqual(win._staged[0]["1"]["value"], "New A")
        self.assertEqual(row["dirty_Name"], "True")
        self.assertIn("'Old A'", row["tip_Name"])
        self.assertEqual(win._dim_type_by_id["1"].Name, "Old A")
        self.assertEqual((win.btn_dim_apply.Content, win.btn_dim_apply.IsEnabled),
                         ("Apply Changes (1)", True))
        self.assertEqual(win.dg_dim.commits, ["Row"])      # row committed after the cell

    def test_typed_back_to_the_model_value_is_unstaged(self):
        win = self.make()
        row = _type_row("1", "Old A")
        self.stage(row, "New A")
        row["Name"] = "New A"                              # what the binding wrote
        self.stage(row, "Old A")
        self.assertEqual(win._staged[0], {})
        self.assertEqual(row["dirty_Name"], "False")
        self.assertEqual((win.btn_dim_apply.Content, win.btn_dim_apply.IsEnabled),
                         ("Apply Changes", False))

    def test_type_name_is_trimmed_and_empty_is_refused(self):
        win = self.make()
        row = _type_row("1", "Old A")
        args = self.stage(row, "  New A  ")
        self.assertEqual(args.EditingElement.Text, "New A")   # the cell commits it trimmed
        self.assertEqual(win._staged[0]["1"]["value"], "New A")
        other = _type_row("2", "Old B")
        args = self.stage(other, "   ")
        self.assertEqual(args.EditingElement.Text, "Old B")
        self.assertNotIn("2", win._staged[0])

    def test_note_text_keeps_its_spaces(self):
        win = self.make()
        row = _type_row("9", "Note", cat="TxtInst")
        self.stage(row, " Note 2 ", page=self.s["PAGE_TXT"])
        self.assertEqual(win._staged[1]["9"]["value"], " Note 2 ")

    def test_only_the_name_column_on_commit_is_staged(self):
        win = self.make()
        row = _type_row("1", "Old A")
        win.dim_cell_edit_ending(win.dg_dim, _edit(row, "x", field="Status"))
        win.dim_cell_edit_ending(win.dg_dim, _edit(row, "x", action="Cancel"))
        self.assertEqual(win._staged[0], {})

    def test_dimension_instances_are_not_staged(self):
        win = self.make()
        row = _type_row("5", "Some Type", cat="DimInst")
        args = self.stage(row, "Renamed")
        self.assertEqual(args.EditingElement.Text, "Some Type")
        self.assertEqual(win._staged[0], {})

    def test_rebuilt_row_reads_its_staged_edit_back(self):
        """The table is rebuilt on every filter keystroke; the store is not."""
        win = self.make()
        win._staged[0]["1"] = {"kind": "DimType", "original": "Old A", "value": "New A",
                               "error": "", "status": "AI Fix"}
        rows = []
        table = SimpleNamespace(NewRow=_Row, Rows=SimpleNamespace(Add=rows.append))
        win._dt_add(table, "1", "DimType", "Old A", "Dimension Type", page=0)
        win._dt_add(table, "2", "DimType", "Old B", "Dimension Type", page=0)
        self.assertEqual((rows[0]["Name"], rows[0]["orig_Name"], rows[0]["dirty_Name"],
                          rows[0]["Status"]), ("New A", "Old A", "True", "AI Fix"))
        self.assertEqual((rows[1]["Name"], rows[1]["dirty_Name"]), ("Old B", "False"))

    def test_apply_writes_all_in_one_transaction_and_keeps_refused_rows(self):
        win = self.make(transaction=_Transaction)
        self.stage(_type_row("1", "Old A"), "New A")
        self.stage(_type_row("2", "Old B"), "Taken")      # duplicate name
        win.dim_apply(None, None)
        self.assertEqual(len(_Transaction.made), 1)
        self.assertEqual(_Transaction.made[0].events, ["start", "commit", "dispose"])
        self.assertEqual(win._dim_type_by_id["1"].Name, "New A")
        self.assertEqual(list(win._staged[0]), ["2"])
        self.assertIn("already in use", win._staged[0]["2"]["error"])
        self.assertTrue(win.status.Text.startswith("Renamed 1 type(s). 1 could not be renamed"))
        self.assertEqual(len(self.dialogs.warnings), 1)
        self.assertIn("'Old B' → 'Taken'", self.dialogs.warnings[0][1]["details"])
        self.assertEqual(win.btn_dim_apply.Content, "Apply Changes (1)")
        self.assertIn("_fill_dims", self.calls)          # rebuilt: yellow cleared, red kept

    def test_apply_with_every_row_refused_rolls_back(self):
        win = self.make(transaction=_Transaction)
        self.stage(_type_row("2", "Old B"), "Taken")
        win.dim_apply(None, None)
        self.assertEqual(_Transaction.made[0].events, ["start", "rollback", "dispose"])
        self.assertEqual(win._staged[0]["2"]["value"], "Taken")

    def test_commit_refused_by_revit_keeps_everything_staged(self):
        win = self.make(transaction=_Transaction)
        _Transaction.commit_result = "RolledBack"
        self.stage(_type_row("1", "Old A"), "New A")
        win.dim_apply(None, None)
        self.assertEqual(win._staged[0]["1"]["error"], "")
        self.assertIn("Apply failed; nothing was written", win.status.Text)
        self.assertNotIn("_fill_dims", self.calls)

    def test_mode_switch_keep_editing_puts_the_chip_back(self):
        win = self.make(False)
        self.stage(_type_row("1", "Old A"), "New A")
        win.rb_dim_inst.IsChecked, win.rb_dim_type.IsChecked = True, False
        win.dim_submode(None, None)
        self.assertEqual(win._dim_submode, "types")
        self.assertIs(win.rb_dim_type.IsChecked, True)
        self.assertEqual(len(win._staged[0]), 1)
        message, kw = self.dialogs.confirms[0]
        self.assertEqual(kw["ok_text"], "Discard 1 change(s)")
        self.assertEqual((kw["danger"], kw["cancel_text"]), (True, "Keep editing"))

    def test_mode_switch_discard_drops_the_edits(self):
        win = self.make(True)
        self.stage(_type_row("1", "Old A"), "New A")
        win.rb_dim_inst.IsChecked, win.rb_dim_type.IsChecked = True, False
        win.dim_submode(None, None)
        self.assertEqual((win._dim_submode, win._staged[0]), ("instances", {}))

    def test_leaving_the_page_keep_editing_stays(self):
        win = self.make(False)
        self.stage(_type_row("1", "Old A"), "New A")
        win.nav_dim.IsChecked, win.nav_txt.IsChecked = False, True   # the click
        self.assertFalse(win._leave_page(self.s["PAGE_TXT"]))
        self.assertEqual((win.nav_dim.IsChecked, win.nav_txt.IsChecked), (True, False))
        self.assertTrue(win._leave_page(self.s["PAGE_DIM"]))      # same page: no question
        self.assertEqual(len(self.dialogs.confirms), 1)

    def test_close_with_pending_edits_asks(self):
        win = self.make(False, True)
        self.stage(_type_row("1", "Old A"), "New A")
        args = SimpleNamespace(Cancel=False)
        win._on_closing(None, args)
        self.assertIs(args.Cancel, True)
        args = SimpleNamespace(Cancel=False)
        win._on_closing(None, args)
        self.assertIs(args.Cancel, False)
        self.assertEqual(self.dialogs.confirms[0][1]["ok_text"], "Discard 1 change(s)")

    def test_deleted_element_drops_its_staged_edit(self):
        win = self.make()
        self.stage(_type_row("1", "Old A"), "New A")
        self.stage(_type_row("2", "Old B"), "New B")
        del win._dim_type_by_id["2"]                       # Delete Selected removed it
        win._prune_staged(self.s["PAGE_DIM"])
        self.assertEqual(list(win._staged[0]), ["1"])
        self.assertEqual(win.btn_dim_apply.Content, "Apply Changes (1)")

    def test_close_without_pending_edits_does_not_ask(self):
        win = self.make()
        args = SimpleNamespace(Cancel=False)
        win._on_closing(None, args)
        self.assertEqual((args.Cancel, self.dialogs.confirms), (False, []))

    def test_rename_all_applies_or_discards_pending_renames_first(self):
        win = self.make(True, transaction=_Transaction)            # Apply first
        self.stage(_type_row("1", "Old A"), "New A")
        self.assertTrue(win._settle_before_rename_all(0))
        self.assertEqual((win._staged[0], win._dim_type_by_id["1"].Name), ({}, "New A"))

        win = self.make(False, True)                               # Don't apply → Discard
        self.stage(_type_row("1", "Old A"), "New A")
        self.assertTrue(win._settle_before_rename_all(0))
        self.assertEqual(win._staged[0], {})

        win = self.make(False, False)                              # Don't apply → Keep editing
        self.stage(_type_row("1", "Old A"), "New A")
        self.assertFalse(win._settle_before_rename_all(0))
        self.assertEqual(len(win._staged[0]), 1)


class ManaAnnoStagingSource(unittest.TestCase):
    """Source checks that keep the model writes where they belong."""

    def setUp(self):
        self.tree = ast.parse(_read(MANA_ANNO), filename=MANA_ANNO)
        cls = next(n for n in self.tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "AnnotationManagerWindow")
        self.methods = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}

    def test_cell_edit_handlers_never_write_to_the_model(self):
        for name in ("dim_cell_edit_ending", "txt_cell_edit_ending", "_stage_cell_edit"):
            fn = self.methods[name]
            names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
            self.assertFalse(names & {"Transaction", "disposing", "_run_transaction",
                                      "doc", "setattr"}, name)
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        self.assertFalse(isinstance(target, ast.Attribute)
                                         and target.attr in ("Name", "Text"),
                                         "%s assigns %s" % (name, ast.unparse(target)))

    def test_apply_uses_one_disposing_transaction_checked_for_commit(self):
        fn = self.methods["_apply_staged"]
        source = ast.unparse(fn)
        made = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "Transaction"]
        self.assertEqual(len(made), 1)
        withs = [n for n in ast.walk(fn) if isinstance(n, ast.With)]
        self.assertEqual(len(withs), 1)
        context = withs[0].items[0].context_expr
        self.assertEqual(getattr(context.func, "id", ""), "disposing")
        self.assertIs(context.args[0], made[0])
        self.assertIn("transaction.Commit() != DB.TransactionStatus.Committed", source)
        self.assertIn("transaction.Start() != DB.TransactionStatus.Started", source)
        self.assertIn("_write_staged(items, write)", source)
        self.assertNotIn("_run_transaction", source)
        for loop in (n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.While))):
            self.assertNotIn("Transaction(", ast.unparse(loop))

    def test_apply_buttons_and_rename_all_go_through_the_store(self):
        for name in ("dim_apply", "txt_apply"):
            self.assertIn("self._apply_staged(", ast.unparse(self.methods[name]))
        for name in ("dim_rename_all", "txt_rename_all"):
            first = ast.unparse(self.methods[name].body[0])
            self.assertIn("self._settle_before_rename_all(", first, name)

    def test_every_way_out_asks_before_dropping_edits(self):
        init = ast.unparse(self.methods["__init__"])
        self.assertIn("self.Closing += self._on_closing", init)
        self.assertLess(init.index("self._staged = "), init.index("self._fill_dims"
                                                                   if "self._fill_dims" in init
                                                                   else "self._load_all_dims"))
        for name in ("nav_dimensions_checked", "nav_textnotes_checked", "nav_dimtext_checked"):
            self.assertIn("self._leave_page(", ast.unparse(self.methods[name].body[0]), name)
        for name, fill in (("dim_submode", "self._fill_dims()"), ("txt_submode", "self._fill_txts()")):
            source = ast.unparse(self.methods[name])
            self.assertLess(source.index("self._discard_or_keep("), source.index(fill), name)

    def test_rebuilds_commit_an_open_cell_first(self):
        source = ast.unparse(self.methods["_replace_table"])
        self.assertLess(source.index("self._flush_edit(grid)"), source.index("ItemsSource = None"))

    def test_ai_spellcheck_stages_through_the_store(self):
        source = ast.unparse(self.methods["on_txt_ai_qa_clicked"])
        self.assertIn("_stage_edit(store", source)
        self.assertIn("self._sync_apply(PAGE_TXT)", source)


# ── Project units (2026-10-02) ────────────────────────────────────────────
# Lengths follow the project: segment lengths on the Dim Text page are typed
# and labelled in the project's length unit; text sizes are measured on paper,
# so they are mm in a metric project and inches in an imperial one
# (Snippets._units.paper_unit). Everything goes through Snippets/_units.py.

DIM_TEXT = os.path.join(GUI_DIR, "DimTextDialog.py")


def _units_module():
    sys.path.insert(0, LIB_DIR)
    try:
        from Snippets import _units
        from GUI.GridPendingEdits import column_key
    finally:
        sys.path.remove(LIB_DIR)
    return _units, column_key


U, _COLUMN_KEY = _units_module()
MM = U.LengthUnit("millimeters")
METERS = U.LengthUnit("meters")
FTIN = U.LengthUnit("feetFractionalInches")
IN = 1.0 / 12.0                                     # one inch in internal feet


def _exec_from(path, names=(), funcs=(), cls=None, methods=(), extra=None):
    """Chosen module assignments / functions / class methods of `path`,
    exec'd with `extra` fakes for the WPF / Revit names they use."""
    tree = ast.parse(_read(path), filename=path)
    body = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and _assign_names(node) & set(names):
            body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in funcs:
            body.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == cls:
            node.bases = []
            node.body = [m for m in node.body
                         if isinstance(m, ast.FunctionDef) and m.name in methods]
            body.append(node)
    scope = {"paper_unit": U.paper_unit, "project_length_unit": U.project_length_unit,
             "MM_PER_FT": U.MM_PER_FT, "column_key": _COLUMN_KEY}
    scope.update(extra or {})
    exec(compile(ast.Module(body=body, type_ignores=[]), path, "exec"), scope)
    return scope


class _BIP(object):
    """BuiltInParameter stand-in: every member is its own name."""

    def __getattr__(self, name):
        return name


class _Param(object):
    def __init__(self, double=0.0, string="", integer=0, value_string=""):
        self._d, self._s, self._i, self._v = double, string, integer, value_string

    def AsDouble(self):
        return self._d

    def AsString(self):
        return self._s

    def AsInteger(self):
        return self._i

    def AsValueString(self):
        return self._v


class _AnnoType(object):
    """DimensionType / TextNoteType stand-in (no GetUnitsFormatOptions)."""

    def __init__(self, params):
        self._params = params

    def get_Parameter(self, bip):
        return self._params.get(bip)


def _legacy_size(feet):
    """The size text Rename All wrote before units were read (metric names)."""
    return "{:.2f}mm".format(round(feet * 304.8, 2))


NAMING_NAMES = ("NAMING_TEMPLATES", "_DIM_COLORS", "_TXT_COLORS", "_INCH_GRID",
                "_FIELD_TOKENS", "_TYPE_INDICATORS", "_RENAME_NOUNS",
                "_EXAMPLE_TEXT_SIZE", "_PAPER_SIZE_COLUMNS")
NAMING_FUNCS = ("_rgb", "_sanitize", "_param_text", "_paper_size", "_size_name",
                "_size_cell", "_rename_tooltip", "_dim_name", "_txt_name")


class ManaAnnoTextSizeUnits(unittest.TestCase):
    """Text sizes (SIZE column, Rename All names) are in the paper unit."""

    def setUp(self):
        import math
        self.s = _exec_from(MANA_ANNO, NAMING_NAMES, NAMING_FUNCS,
                            extra={"math": math, "re": re, "BuiltInParameter": _BIP(),
                                   "ElementId": SimpleNamespace(InvalidElementId=None)})

    def test_metric_names_are_exactly_what_rename_all_always_wrote(self):
        """A metric project keeps its names: no type is renamed differently,
        including sizes on a half-hundredth (1.875 mm) where a reciprocal
        conversion would land one bit off."""
        size = self.s["_paper_size"]
        sizes_mm = [i / 1000.0 for i in range(1, 20001)]
        sizes_in = [n / float(d) for d in (8, 16, 32, 64) for n in range(1, 2 * d)]
        feet = [mm / 304.8 for mm in sizes_mm] + [inch * IN for inch in sizes_in]
        for paper in (None, MM, METERS, U.paper_unit(METERS)):
            for value in feet[::7] + [1.875 / 304.8, 2.495 / 304.8]:
                self.assertEqual("".join(size(value, paper)), _legacy_size(value))

    def test_imperial_sizes_are_fractional_inches(self):
        size = self.s["_size_name"]
        cases = {3.0 / 32: '3/32"', 1.0 / 8: '1/8"', 3.0 / 16: '3/16"', 1.0 / 4: '1/4"',
                 5.0 / 64: '5/64"', 1.0: '1"', 1.25: '1 1/4"'}
        for inches, text in cases.items():
            self.assertEqual(size(_Param(inches * IN), FTIN), text)
            self.assertEqual(size(_Param(inches * IN), U.paper_unit(FTIN)), text)
        # A metric size in an imperial project is not passed off as 3/32"
        self.assertEqual(size(_Param(2.5 / 304.8), FTIN), '0.098"')

    def test_size_column_holds_the_number_and_the_header_the_unit(self):
        cell = self.s["_size_cell"]
        self.assertEqual(cell(_Param(2.5 / 304.8), MM), "2.50")
        self.assertEqual(cell(_Param(3.0 / 32 * IN), FTIN), "3/32")

    def text_type(self, size_feet):
        return _AnnoType({"TEXT_SIZE": _Param(size_feet), "TEXT_FONT": _Param(string="Arial"),
                          "TEXT_BACKGROUND": _Param(integer=1),
                          "TEXT_WIDTH_SCALE": _Param(1.0), "LINE_COLOR": _Param(integer=0)})

    def dim_type(self, size_feet):
        return _AnnoType({"TEXT_SIZE": _Param(size_feet), "TEXT_FONT": _Param(string="Arial"),
                          "TEXT_WIDTH_SCALE": _Param(0.8),
                          "DIM_TEXT_BACKGROUND": _Param(value_string="Opaque"),
                          "LINE_COLOR": _Param(integer=0)})

    def test_type_names_per_unit_system(self):
        txt, dim = self.s["_txt_name"], self.s["_dim_name"]
        metric = 2.5 / 304.8
        imperial = 3.0 / 32 * IN
        self.assertEqual(txt(self.text_type(metric), "Old", MM), "ARC_TXT_2.50mm_Arial_1_Transparent")
        self.assertEqual(txt(self.text_type(metric), "Old"), "ARC_TXT_2.50mm_Arial_1_Transparent")
        self.assertEqual(txt(self.text_type(imperial), "STR old", U.paper_unit(FTIN)),
                         'STR_TXT_3/32"_Arial_1_Transparent')
        self.assertEqual(dim(self.dim_type(metric), "Old", MM), "ARC_DIM_2.50mm_Arial_0.8_Opaque")
        self.assertEqual(dim(self.dim_type(imperial), "Old", FTIN),
                         'ARC_DIM_3/32"_Arial_0.8_Opaque')

    def test_size_name_has_no_character_revit_refuses_in_a_name(self):
        for paper in (MM, FTIN):
            for inches in (3.0 / 32, 1.25, 0.0984):
                name = self.s["_size_name"](_Param(inches * IN), paper)
                self.assertFalse(set(name) & set('\\:{}[]|;<>?`~'), name)

    def test_rename_tooltip_follows_the_template_and_the_unit(self):
        tip = self.s["_rename_tooltip"]
        for kind, indicator in (("Dimension", "_DIM_"), ("TextNote", "_TXT_")):
            metric, imperial = tip(kind, MM), tip(kind, U.paper_unit(FTIN))
            self.assertIn(indicator, metric)
            self.assertIn("text size in mm, e.g. 2.50mm", metric)
            self.assertIn('text size in inches, e.g. 3/32"', imperial)
            fields = self.s["NAMING_TEMPLATES"][kind]["Fields"]
            self.assertEqual(metric.split(": ", 1)[1].split(". ")[0].count("_"), len(fields) - 1)

    def test_xaml_tooltip_is_the_metric_runtime_text(self):
        """The XAML default (shown before Python runs) is the metric tooltip."""
        xaml = _read(MANA_ANNO_XAML)
        for name, kind in (("btn_dim_rename_all", "Dimension"), ("btn_txt_rename_all", "TextNote")):
            tag = re.search(r'<Button x:Name="%s"[^>]*>' % name, xaml).group(0)
            self.assertIn('ToolTip="%s"' % self.s["_rename_tooltip"](kind, MM), tag)


class _Columns(object):
    def __init__(self, *paths):
        self.Columns = [SimpleNamespace(Binding=SimpleNamespace(Path=SimpleNamespace(Path=p)),
                                        SortMemberPath=p, Header=p.upper()) for p in paths]


class ManaAnnoUnitLabels(unittest.TestCase):
    """Every label that names a unit is written from the unit of the launch."""

    def window(self, length_unit):
        s = _exec_from(MANA_ANNO, NAMING_NAMES, NAMING_FUNCS, "AnnotationManagerWindow",
                       ("_apply_units",), extra={"math": __import__("math")})
        win = s["AnnotationManagerWindow"].__new__(s["AnnotationManagerWindow"])
        win._unit, win._paper = length_unit, U.paper_unit(length_unit)
        win.dg_dim, win.dg_txt = _Columns("Name", "Size", "Font"), _Columns("Name", "Size")
        win.btn_dim_rename_all = SimpleNamespace(ToolTip="")
        win.btn_txt_rename_all = SimpleNamespace(ToolTip="")
        win.lbl_dimtext_filter = SimpleNamespace(Text="")
        win._apply_units()
        return win

    def test_metric_project(self):
        win = self.window(MM)
        self.assertEqual([c.Header for c in win.dg_dim.Columns], ["NAME", "SIZE (MM)", "FONT"])
        self.assertEqual(win.dg_txt.Columns[1].Header, "SIZE (MM)")
        self.assertEqual(win.lbl_dimtext_filter.Text, "FILTER BY SEGMENT LENGTH (MM)")
        self.assertIn("e.g. 2.50mm", win.btn_txt_rename_all.ToolTip)

    def test_meters_project_keeps_text_sizes_in_mm(self):
        win = self.window(METERS)
        self.assertEqual(win.dg_dim.Columns[1].Header, "SIZE (MM)")
        self.assertEqual(win.lbl_dimtext_filter.Text, "FILTER BY SEGMENT LENGTH (M)")

    def test_imperial_project(self):
        win = self.window(FTIN)
        self.assertEqual(win.dg_dim.Columns[1].Header, "SIZE (IN)")
        self.assertEqual(win.dg_txt.Columns[1].Header, "SIZE (IN)")
        self.assertEqual(win.lbl_dimtext_filter.Text, "FILTER BY SEGMENT LENGTH (FT-IN)")
        self.assertIn('e.g. 3/32"', win.btn_dim_rename_all.ToolTip)

    def test_runtime_labels_have_names_in_the_xaml(self):
        names = set(re.findall(r'x:Name="([^"]+)"', _read(MANA_ANNO_XAML)))
        for name in ("btn_dim_rename_all", "btn_txt_rename_all", "lbl_dimtext_filter"):
            self.assertIn(name, names)
        source = ast.unparse(ast.parse(_read(MANA_ANNO)))
        for name in ("btn_dim_rename_all", "btn_txt_rename_all", "lbl_dimtext_filter"):
            self.assertIn("'%s'" % name, source)

    def test_size_column_is_found_by_binding_path(self):
        source = _read(MANA_ANNO)
        self.assertIn('_PAPER_SIZE_COLUMNS = {"Size": "SIZE"}', source)
        body = ast.unparse(next(n for n in ast.walk(ast.parse(source))
                                if isinstance(n, ast.FunctionDef) and n.name == "_apply_units"))
        self.assertIn("column_key(col)", body)


class ManaAnnoUnitPerLaunch(unittest.TestCase):
    """The unit belongs to the document, so it is re-read on every launch."""

    def test_refresh_reads_the_unit_of_each_launchs_document(self):
        docs = iter([SimpleNamespace(unit=MM), SimpleNamespace(unit=FTIN)])
        revit = SimpleNamespace()
        dim_text = SimpleNamespace()
        s = _exec_from(MANA_ANNO, ("unit",), ("_refresh_active_document",), extra={
            "doc": None, "uidoc": None,
            "revit": revit, "DimTextDialog": dim_text,
            "resolve_doc": lambda candidate: SimpleNamespace(doc=next(docs)),
            "resolve_uidoc": lambda candidate: object(),
            "project_length_unit": lambda d: d.unit if d is not None else MM,
        })
        s["_refresh_active_document"]()
        self.assertEqual((s["unit"].tag, dim_text.unit.tag), ("mm", "mm"))
        s["_refresh_active_document"]()                 # another project, same session
        self.assertEqual((s["unit"].tag, dim_text.unit.tag), ("ft-in", "ft-in"))
        self.assertIs(dim_text.doc, s["doc"])

    def test_window_takes_the_unit_before_anything_is_shown(self):
        tree = ast.parse(_read(MANA_ANNO))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "AnnotationManagerWindow")
        init = ast.unparse(next(m for m in cls.body if isinstance(m, ast.FunctionDef)
                                and m.name == "__init__"))
        first_fill = min(init.index(x) for x in ("self._apply_units()", "self._load_all_dims",
                                                 "self._refresh_dim_cache"))
        self.assertLess(init.index("self._unit = unit"), first_fill)
        self.assertLess(init.index("self._paper = paper_unit(unit)"), first_fill)
        self.assertLess(init.index("self._apply_units()"), init.index("self._load_all_dims"))

    def test_dim_text_window_rereads_the_unit_with_the_document(self):
        show = ast.unparse(next(n for n in ast.parse(_read(DIM_TEXT)).body
                                if isinstance(n, ast.FunctionDef) and n.name == "show_dialog"))
        self.assertIn("global doc, uidoc, unit", show)
        self.assertLess(show.index("revit.doc"), show.index("unit = project_length_unit(doc)"))
        self.assertLess(show.index("unit = project_length_unit(doc)"), show.index("DimTextWindow()"))

    def test_no_hand_conversion_left_in_the_ui_code(self):
        """Displayed and typed lengths go through Snippets/_units.py."""
        for path in (MANA_ANNO, DIM_TEXT):
            literals = [n.lineno for n in ast.walk(ast.parse(_read(path)))
                        if isinstance(n, ast.Constant) and n.value in (304.8, 25.4, 0.3048)]
            self.assertEqual(literals, [], path)


class _Event(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self


class _Children(list):
    def Add(self, item):
        self.append(item)

    def Remove(self, item):
        self.remove(item)


class _Control(object):
    """Any WPF control the rule row builds (StackPanel, ComboBox, TextBox ...)."""

    def __init__(self):
        self.Children, self.Items = _Children(), _Children()
        self.SelectionChanged, self.Click = _Event(), _Event()
        self.SelectedIndex, self.Text, self.ToolTip = -1, "", None
        self.Visibility = "Visible"

    @property
    def SelectedItem(self):
        return self.Items[self.SelectedIndex] if 0 <= self.SelectedIndex < len(self.Items) else None


def _dim_text_scope(project_unit=MM):
    wpf = {name: _Control for name in ("StackPanel", "ComboBox", "ComboBoxItem", "TextBox",
                                       "Button", "TextBlock")}
    wpf.update({"Thickness": lambda *a: a,
                "WPFOrientation": SimpleNamespace(Horizontal="Horizontal"),
                "Visibility": SimpleNamespace(Visible="Visible", Collapsed="Collapsed"),
                "VerticalAlignment": SimpleNamespace(Center="Center"),
                "unit": project_unit})
    return _exec_from(DIM_TEXT, ("_OPERATORS", "_NO_VALUE_OPS", "_TWO_VALUE_OPS"),
                      ("_length_unit", "_value_tooltip", "_t3_style", "create_rule_row",
                       "_rule_length", "build_filter_fn", "_length_of", "_set_dim_text"),
                      extra=wpf)


def _rule(s, op, first="", second="", length_unit=None):
    rd = s["create_rule_row"](SimpleNamespace(TryFindResource=lambda key: key),
                              lambda rd: None, length_unit)
    rd["combo"].SelectedIndex = s["_OPERATORS"].index(op)
    rd["txt1"].Text, rd["txt2"].Text = first, second
    return rd


class DimTextLengthRules(unittest.TestCase):
    """Segment-length rules are typed in the project unit and parsed by the helper."""

    def test_rule_row_labels_carry_the_project_unit(self):
        s = _dim_text_scope()
        for length_unit, tag in ((MM, "mm"), (METERS, "m"), (FTIN, "ft-in")):
            rd = _rule(s, "between", length_unit=length_unit)
            self.assertEqual((rd["lbl_unit"].Text, rd["lbl_unit2"].Text), (tag, tag))
            self.assertIn("Length in {}.".format(tag), rd["txt1"].ToolTip)
        # no unit passed → the unit of this launch (DimTextDialog.unit)
        self.assertEqual(_rule(_dim_text_scope(FTIN), "equals")["lbl_unit"].Text, "ft-in")

    def test_imperial_values_are_feet_and_inches(self):
        s = _dim_text_scope()
        keep = s["build_filter_fn"]([_rule(s, "equals", "3'-6\"")], True, FTIN)
        self.assertTrue(keep(3.5))
        self.assertTrue(keep(3.5 + 1.0 / 32 * IN))       # still reads 3' - 6"
        self.assertFalse(keep(3.5 + 0.25 * IN))
        bare = s["build_filter_fn"]([_rule(s, "is greater than", "6")], True, FTIN)
        self.assertTrue(bare(6.1))                        # a bare 6 is 6 ft here
        self.assertFalse(bare(5.9))

    def test_metric_keeps_half_a_millimetre_for_equals(self):
        s = _dim_text_scope()
        keep = s["build_filter_fn"]([_rule(s, "equals", "2800")], True, MM)
        self.assertTrue(keep(2800.4 / 304.8))
        self.assertFalse(keep(2800.6 / 304.8))
        meters = s["build_filter_fn"]([_rule(s, "is less than", "1,2")], True, METERS)
        self.assertTrue(meters(1199.0 / 304.8))
        self.assertFalse(meters(1201.0 / 304.8))

    def test_a_typed_unit_wins_in_any_project(self):
        s = _dim_text_scope()
        rule = s["build_filter_fn"]([_rule(s, "between", "1200 mm", "4'")], True, FTIN)
        self.assertTrue(rule(1210.0 / 304.8))            # 3' - 11 5/8"
        self.assertFalse(rule(1190.0 / 304.8))
        self.assertFalse(rule(4.2))

    def test_not_a_length_names_the_rule_and_the_unit(self):
        s = _dim_text_scope()
        rules = [_rule(s, "has a value"), _rule(s, "equals", "abc")]
        with self.assertRaises(ValueError) as ctx:
            s["build_filter_fn"](rules, True, FTIN)
        self.assertIn("Length rule 2", str(ctx.exception))
        self.assertIn("ft-in", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:           # blank used to count as 0
            s["build_filter_fn"]([_rule(s, "between", "100", "")], True, MM)
        self.assertIn("Length rule 1: Enter a length.", str(ctx.exception))

    def test_segments_are_filtered_on_revits_own_length(self):
        s = _dim_text_scope()
        keep = s["build_filter_fn"]([_rule(s, "equals", "3'-6\"")], True, FTIN)
        short = SimpleNamespace(Value=3.5, Prefix="")
        other = SimpleNamespace(Value=2.0, Prefix="")
        dim = SimpleNamespace(HasOneSegment=lambda: False, Segments=[short, other])
        s["_set_dim_text"](dim, "P", "", "", "", "", keep)
        self.assertEqual((short.Prefix, other.Prefix), ("P", ""))

    def test_bad_value_shows_on_the_dim_text_status_and_writes_nothing(self):
        dims = _dim_text_scope()
        applied = []
        s = _staging_scope({"DimTextDialog": SimpleNamespace(
            build_filter_fn=dims["build_filter_fn"],
            apply_dim_text=lambda *a: applied.append(a))},
            methods={"dimtext_apply", "_dimtext_build_filter_fn", "_status", "_paint_status"})
        win = s["AnnotationManagerWindow"].__new__(s["AnnotationManagerWindow"])
        win.status = SimpleNamespace(Text="", ToolTip="")
        win.status_dot = SimpleNamespace(Fill=None)
        win.FindResource = lambda key: key
        win._page = s["PAGE_DIMTEXT"]
        win._page_status = {}
        win._unit = FTIN
        for name in ("txt_prefix", "txt_suffix", "txt_above", "txt_below", "txt_override"):
            setattr(win, name, SimpleNamespace(Text="X"))
        win.chk_leader = win.chk_filter_enable = SimpleNamespace(IsChecked=True)
        win.combo_combine = SimpleNamespace(SelectedIndex=0)
        win._dimtext_rules = [_rule(dims, "equals", "3 meters")]
        win._dimtext_scope = lambda: ([object()], "selected")
        win.dimtext_apply(None, None)
        self.assertEqual(applied, [])
        self.assertTrue(win.status.Text.startswith("Length rule 1:"), win.status.Text)
        self.assertTrue(win.status.Text.endswith("Nothing was changed."))
        self.assertEqual(win.status_dot.Fill, "T3.Danger.Accent")
        win._dimtext_rules = [_rule(dims, "equals", "3 m")]   # fixed → it runs
        win.dimtext_apply(None, None)
        self.assertEqual(len(applied), 1)

if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
