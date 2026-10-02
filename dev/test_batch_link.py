# -*- coding: utf-8 -*-
"""
Tests for BatchLink logic: file scanning, backup detection, item formatting,
and the Revit-free helpers in Snippets/_links.py (workset name matching,
display-mode mapping).
Run: python dev/test_batch_link.py
"""
import os
import sys
import re
import unittest
import tempfile
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

BACKUP_REGEX = re.compile(r'\.\d{3,4}\.rvt$', re.IGNORECASE)


def _load_links_module():
    """Exec Snippets/_links.py with the Revit and .NET imports stubbed out.

    The module is only importable inside Revit, so the Revit-free helpers are
    exercised against the real shipped source rather than a copy.
    """
    import types

    class _AnyMeta(type):
        """Any attribute lookup yields another permissive placeholder type."""
        def __getattr__(cls, item):
            return _make_any(item)

    def _make_any(name):
        return _AnyMeta(str(name), (), {})

    def _stub(name):
        mod = types.ModuleType(name)
        mod.__getattr__ = _make_any
        return mod

    saved = {}
    stubs = {
        'Autodesk': _stub('Autodesk'),
        'Autodesk.Revit': _stub('Autodesk.Revit'),
        'Autodesk.Revit.DB': _stub('Autodesk.Revit.DB'),
        'System': _stub('System'),
        'System.Collections': _stub('System.Collections'),
        'System.Collections.Generic': _stub('System.Collections.Generic'),
    }
    stubs['System.Collections.Generic'].List = {}
    # wire the dotted attribute chain so `import a.b.c as x` resolves
    stubs['Autodesk'].Revit = stubs['Autodesk.Revit']
    stubs['Autodesk.Revit'].DB = stubs['Autodesk.Revit.DB']
    stubs['System'].Collections = stubs['System.Collections']
    stubs['System.Collections'].Generic = stubs['System.Collections.Generic']
    for name, mod in stubs.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    try:
        source_path = os.path.join(LIB_DIR, 'Snippets', '_links.py')
        with open(source_path, 'r', encoding='utf-8') as handle:
            source = handle.read()
        module = types.ModuleType('t3_links_under_test')
        module.__file__ = source_path
        exec(compile(source, source_path, 'exec'), module.__dict__)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class _FakeWorkset(object):
    def __init__(self, workset_id, name, is_open=True):
        self.workset_id = workset_id
        self.name = name
        self.is_open = is_open


class _FakeLinkRecord(object):
    """Stands in for Snippets._links.LinkRecord in the instance-fan-out tests."""

    def __init__(self, instance_ids, instance_id=None):
        self.instance_ids = list(instance_ids)
        self.instance_id = (instance_id if instance_id is not None
                            else (instance_ids[0] if instance_ids else None))


class TestLinkHelpers(unittest.TestCase):
    """Revit-free helpers from Snippets/_links.py."""

    @classmethod
    def setUpClass(cls):
        cls.links = _load_links_module()

    def test_split_workset_ids_matches_by_name(self):
        worksets = [_FakeWorkset(1, 'Shared Levels'),
                    _FakeWorkset(2, 'Link Architecture'),
                    _FakeWorkset(3, 'Furniture')]
        open_ids, close_ids = self.links.split_workset_ids(
            worksets, {'Shared Levels', 'Link Architecture'}, {'Furniture'})
        self.assertEqual(open_ids, [1, 2])
        self.assertEqual(close_ids, [3])

    def test_split_workset_ids_leaves_unlisted_untouched(self):
        """A workset named in neither set must not be opened or closed."""
        worksets = [_FakeWorkset(7, 'Only In This Link')]
        open_ids, close_ids = self.links.split_workset_ids(
            worksets, {'Shared Levels'}, {'Furniture'})
        self.assertEqual(open_ids, [])
        self.assertEqual(close_ids, [])

    def test_split_workset_ids_handles_empty(self):
        self.assertEqual(self.links.split_workset_ids(None, set(), set()), ([], []))
        self.assertEqual(self.links.split_workset_ids([], {'x'}, {'y'}), ([], []))

    def test_workset_config_ids_covers_every_workset(self):
        """Worksets the grid never listed keep their own state, not "open"."""
        worksets = [_FakeWorkset(1, 'Shared Levels', is_open=True),
                    _FakeWorkset(2, 'Furniture', is_open=True),
                    _FakeWorkset(3, 'Only In This Link', is_open=False),
                    _FakeWorkset(4, 'Also Only Here', is_open=True)]
        open_ids, close_ids = self.links.workset_config_ids(
            worksets, {'Shared Levels'}, {'Furniture'})
        # 1 ticked open, 4 unlisted but currently open -> open.
        self.assertEqual(sorted(open_ids), [1, 4])
        # 2 ticked closed, 3 unlisted and currently closed -> closed.
        self.assertEqual(sorted(close_ids), [2, 3])

    def test_workset_config_ids_handles_empty(self):
        self.assertEqual(self.links.workset_config_ids(None, set(), set()), ([], []))
        self.assertEqual(self.links.workset_config_ids([], {'x'}, {'y'}), ([], []))

    def test_placed_instance_ids_returns_every_instance(self):
        record = _FakeLinkRecord([11, 12, 13])
        self.assertEqual(self.links._placed_instance_ids(record), [11, 12, 13])

    def test_placed_instance_ids_falls_back_to_single(self):
        record = _FakeLinkRecord([], instance_id=7)
        self.assertEqual(self.links._placed_instance_ids(record), [7])

    def test_placed_instance_ids_of_unplaced_link_is_empty(self):
        self.assertEqual(self.links._placed_instance_ids(None), [])
        self.assertEqual(self.links._placed_instance_ids(_FakeLinkRecord([])), [])

    def test_display_modes_order(self):
        self.assertEqual(self.links.DISPLAY_MODES,
                         ("By Host View", "By Linked View", "Custom"))

    def test_mode_index_round_trip(self):
        self.assertEqual(self.links._mode_index('ByHostView'), 0)
        self.assertEqual(self.links._mode_index('ByLinkView'), 1)
        self.assertEqual(self.links._mode_index('Custom'), 2)
        self.assertEqual(self.links._mode_index('SomethingElse'), 0)

    def test_only_view_filters_accepts_custom(self):
        """Revit rejects LinkVisibility.Custom on every aspect but View Filters."""
        allowing = [key for key, _label, _kind, custom in self.links.CUSTOM_ASPECTS if custom]
        self.assertEqual(allowing, ['ViewFilterType'])
        self.assertEqual(len(self.links.aspect_modes(True)), 3)
        self.assertEqual(len(self.links.aspect_modes(False)), 2)

    def test_every_aspect_has_a_known_kind(self):
        for _key, _label, kind, _custom in self.links.CUSTOM_ASPECTS:
            self.assertIn(kind, ('prop', 'discipline', 'detail'))

class TestBatchLinkLogic(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="t3_batch_link_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_backup_regex(self):
        """Test detection of Revit numeric backup files."""
        self.assertTrue(BACKUP_REGEX.search("Project1.0001.rvt"))
        self.assertTrue(BACKUP_REGEX.search("Tower_A.0123.rvt"))
        self.assertTrue(BACKUP_REGEX.search("Hospital.001.rvt"))
        self.assertFalse(BACKUP_REGEX.search("Project1.rvt"))
        self.assertFalse(BACKUP_REGEX.search("Tower_A_0001.rvt"))
        self.assertFalse(BACKUP_REGEX.search("Project.rvt.txt"))

    def test_item_formatting(self):
        """Test RevitModelItem properties and size formatting."""
        # Mock item
        class MockItem(object):
            def __init__(self, size):
                self.file_size = size
            @property
            def FileSize(self):
                if self.file_size < 1024 * 1024:
                    return "{:.1f} KB".format(self.file_size / 1024.0)
                return "{:.1f} MB".format(self.file_size / (1024.0 * 1024.0))

        item_small = MockItem(512 * 1024)
        self.assertEqual(item_small.FileSize, "512.0 KB")

        item_large = MockItem(15 * 1024 * 1024)
        self.assertEqual(item_large.FileSize, "15.0 MB")

    def test_folder_scanning(self):
        """Test scanning folder for rvt files excluding temp and backup files."""
        # Create test files
        open(os.path.join(self.test_dir, "Model_A.rvt"), "w").close()
        open(os.path.join(self.test_dir, "Model_A.0001.rvt"), "w").close()
        open(os.path.join(self.test_dir, "Model_B.rvt"), "w").close()
        open(os.path.join(self.test_dir, "something.dwg"), "w").close()
        open(os.path.join(self.test_dir, "~$Model_C.rvt"), "w").close()

        # Scan
        found = []
        for f in os.listdir(self.test_dir):
            if not f.lower().endswith(".rvt"):
                continue
            if f.startswith("~$"):
                continue
            if BACKUP_REGEX.search(f):
                continue
            found.append(f)

        found.sort()
        self.assertEqual(found, ["Model_A.rvt", "Model_B.rvt"])


# ── LINK WORKSET TAB: pending / applied / failed state ───────────────────────

from GUI import BatchLinkWorksets as WS   # pure Python, no clr / Revit


class _Rec(object):
    def __init__(self, name, instance_count=1):
        self.name = name
        self.instance_count = instance_count


def _row(name="A.rvt", current=("ARC_Walls", 10), can_move=True,
         note="Ready", severity="Success", lock_reason=""):
    return WS.LinkWorksetRow(_Rec(name), current[0], current[1], can_move,
                             note, severity, lock_reason=lock_reason)


class TestWorksetRowState(unittest.TestCase):

    def test_fresh_row_is_clean(self):
        row = _row()
        self.assertEqual(row.row_state, "")
        self.assertEqual(row.WorksetName, "ARC_Walls")
        self.assertEqual(row.WorksetEditable, "yes")
        self.assertEqual((row.StatusText, row.Severity), ("Ready", "Success"))

    def test_stage_marks_pending_and_shows_target(self):
        row = _row()
        self.assertTrue(row.stage(20, "Links"))
        self.assertEqual(row.row_state, "pending")
        self.assertEqual(row.WorksetName, "Links")
        self.assertEqual((row.StatusText, row.Severity), ("Pending", "Warning"))
        self.assertIn("ARC_Walls", row.WorksetTip)
        self.assertIn("Links", row.WorksetTip)

    def test_same_pick_twice_is_no_change(self):
        row = _row()
        row.stage(20, "Links")
        self.assertFalse(row.stage(20, "Links"))

    def test_picking_current_workset_unstages(self):
        row = _row()
        row.stage(20, "Links")
        self.assertTrue(row.stage(10, "ARC_Walls"))
        self.assertEqual(row.row_state, "")
        self.assertIsNone(row.pending_id)
        self.assertEqual(row.StatusText, "Ready")

    def test_regeneration_does_not_wipe_green(self):
        """Grid refresh re-selects the shown value: an applied row stays green."""
        row = _row()
        row.stage(20, "Links")
        row.mark_applied("Moved")
        self.assertFalse(row.stage(20, "Links"))
        self.assertEqual(row.row_state, "applied")

    def test_regeneration_does_not_wipe_failed(self):
        row = _row()
        row.stage(20, "Links")
        row.mark_failed("Owned by bob")
        self.assertFalse(row.stage(20, "Links"))
        self.assertEqual(row.row_state, "failed")

    def test_locked_row_ignores_stage_and_explains(self):
        row = _row(can_move=False, note="Not placed", severity="Warning",
                   lock_reason="This link is not placed in the model.")
        self.assertFalse(row.stage(20, "Links"))
        self.assertEqual(row.WorksetEditable, "no")
        self.assertEqual(row.WorksetTip, "This link is not placed in the model.")
        self.assertEqual(WS.pending_count([row]), 0)

    def test_mark_applied_moves_current(self):
        row = _row()
        row.stage(20, "Links")
        row.mark_applied("Moved")
        self.assertEqual((row.current_id, row.current_label), (20, "Links"))
        self.assertIsNone(row.pending_id)
        self.assertEqual((row.row_state, row.StatusText, row.Severity),
                         ("applied", "Moved", "Success"))

    def test_mark_failed_keeps_pending_for_retry(self):
        row = _row()
        row.stage(20, "Links")
        row.mark_failed("Owned by bob\nmore detail")
        self.assertEqual(row.row_state, "failed")
        self.assertEqual(row.pending_id, 20)
        self.assertEqual(row.current_id, 10)
        self.assertEqual(row.Severity, "Danger")
        self.assertTrue(row.StatusText.startswith("Failed: Owned by bob"))
        self.assertNotIn("\n", row.StatusText)
        self.assertLessEqual(len(row.StatusText), WS.STATUS_MAX)
        self.assertIn("Owned by bob", row.WorksetTip)
        self.assertEqual(WS.pending_count([row]), 1)

    def test_long_failure_is_shortened_in_cell_not_tooltip(self):
        row = _row()
        row.stage(20, "Links")
        long = "The element is borrowed by another user on a different machine"
        row.mark_failed(long)
        self.assertLessEqual(len(row.StatusText), WS.STATUS_MAX)
        self.assertIn(long, row.WorksetTip)

    def test_clear_result_only_drops_green(self):
        a, b, c = _row("a"), _row("b"), _row("c")
        a.stage(20, "Links"); a.mark_applied()
        b.stage(20, "Links"); b.mark_failed("x")
        c.stage(20, "Links")
        self.assertEqual(WS.clear_results([a, b, c]), 1)
        self.assertEqual([r.row_state for r in (a, b, c)], ["", "failed", "pending"])

    def test_set_current_drops_pending_already_there(self):
        row = _row()
        row.stage(20, "Links")
        row.set_current("Links", 20)
        self.assertIsNone(row.pending_id)
        self.assertEqual(row.row_state, "")


class TestWorksetBulkAndApply(unittest.TestCase):

    def _rows(self):
        rows = [_row("a"), _row("b"), _row("c", current=("Links", 20)),
                _row("d", can_move=False, note="Not placed", severity="Warning")]
        for r in rows:
            r.IsSelected = True
        rows[1].IsSelected = False
        return rows

    def test_stage_checked_only_touches_checked_movable_rows(self):
        rows = self._rows()
        staged, unchanged = WS.stage_checked(rows, 20, "Links")
        self.assertEqual((staged, unchanged), (1, 1))     # a staged, c already there
        self.assertEqual([r.row_state for r in rows], ["pending", "", "", ""])
        self.assertEqual(WS.pending_count(rows), 1)

    def test_primary_label(self):
        self.assertEqual(WS.primary_label(0), "Apply")
        self.assertEqual(WS.primary_label(3), "Apply (3)")

    def test_tally_text_counts_pending(self):
        rows = self._rows()
        self.assertEqual(WS.tally_text(rows),
                         u"4 links · 3 can move · 2 checked")
        rows[0].stage(30, "MEP")
        rows[1].stage(30, "MEP")
        self.assertEqual(WS.tally_text(rows),
                         u"4 links · 3 can move · 2 checked · 2 pending")

    def test_apply_pending_records_each_row(self):
        rows = [_row("ok"), _row("same"), _row("bad"), _row("boom"), _row("idle")]
        for r in rows[:4]:
            r.stage(20, "Links")
        seen = []

        def move_one(row):
            seen.append(row.LinkName)
            if row.LinkName == "bad":
                return False, "Owned by bob"
            if row.LinkName == "boom":
                raise RuntimeError("API exploded\nstack")
            if row.LinkName == "same":
                return True, "Already there"
            return True, "Moved"

        steps = []
        result = WS.apply_pending(rows, move_one,
                                  step=lambda r, i, n: steps.append((i, n)))
        self.assertEqual(result, (1, 1, 2))
        self.assertEqual(seen, ["ok", "same", "bad", "boom"])   # idle untouched
        self.assertEqual(steps, [(1, 4), (2, 4), (3, 4), (4, 4)])
        self.assertEqual([r.row_state for r in rows],
                         ["applied", "applied", "failed", "failed", ""])
        self.assertEqual(rows[1].StatusText, "Already there")
        self.assertEqual(rows[3].result_message, "API exploded")
        # failed rows keep their target, so Apply (2) retries them
        self.assertEqual(WS.pending_count(rows), 2)

    def test_rollback_all_reverts_applied_rows_only(self):
        old_green = _row("old")
        old_green.stage(30, "MEP"); old_green.mark_applied()
        a, b = _row("a"), _row("b")
        a.stage(20, "Links"); b.stage(20, "Links")
        rows = [old_green, a, b]
        WS.snapshot_before(rows)
        WS.apply_pending(rows, lambda r: (True, "Moved") if r is a else (False, "Owned"))
        WS.rollback_all(rows, "Rolled back: commit failed")
        self.assertEqual((a.row_state, a.current_id, a.pending_id), ("failed", 10, 20))
        self.assertEqual(a.result_message, "Rolled back: commit failed")
        self.assertEqual(b.result_message, "Owned")                   # own reason kept
        self.assertEqual((old_green.row_state, old_green.current_id), ("applied", 30))

    def test_summary_text(self):
        self.assertEqual(WS.summary_text(1, 0, 0), u"1 link moved · 0 failed")
        self.assertEqual(WS.summary_text(3, 2, 1),
                         u"3 links moved · 2 already there · 1 failed")


# ── LINK WORKSET TAB: XAML / dialog contract ─────────────────────────────────

XAML_PATH = os.path.join(LIB_DIR, 'GUI', 'Tools', 'BatchLink.xaml')
DIALOG_PATH = os.path.join(LIB_DIR, 'GUI', 'BatchLinkDialog.py')


def _read(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return handle.read()


class TestWorksetTabContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        xaml = _read(XAML_PATH)
        start = xaml.index('x:Name="grid_ws_links"')
        cls.grid = xaml[start:xaml.index('</DataGrid>', start)]
        cls.tab = xaml[xaml.index('x:Name="tab_item_worksets"'):
                       xaml.index('x:Name="tab_item_display"')]
        cls.dialog = _read(DIALOG_PATH)

    def test_single_editable_workset_column(self):
        self.assertIn('Header="WORKSET"', self.grid)
        self.assertNotIn('CURRENT WORKSET', self.grid)
        self.assertNotIn('NEW WORKSET', self.grid)
        self.assertIn('<ComboBox ItemsSource="{Binding Tag, RelativeSource='
                      '{RelativeSource AncestorType=DataGrid}}"', self.grid)

    def test_combo_reads_through_string_bridge(self):
        self.assertIn('x:Name="row_ws_name_text" Text="{Binding WorksetName}"', self.grid)
        self.assertIn('SelectedValue="{Binding Text, ElementName=row_ws_name_text, '
                      'Mode=OneWay}"', self.grid)

    def test_locked_rows_show_text_not_combo(self):
        """WorksetEditable reaches WPF as a PyObject: the triggers must read it
        through the hidden TextBlock, never {Binding WorksetEditable} itself."""
        self.assertIn('x:Name="row_ws_editable_text" Text="{Binding WorksetEditable}" '
                      'Visibility="Collapsed"', self.grid)
        self.assertEqual(self.grid.count(
            '<DataTrigger Binding="{Binding Text, ElementName=row_ws_editable_text}" '
            'Value="no">'), 2)
        self.assertNotIn('<DataTrigger Binding="{Binding WorksetEditable}"', self.grid)

    def test_states_painted_from_t3_tokens(self):
        # Cell: row_state copied into the cell's own string property (no template
        # to hold a TextBlock bridge). Combo inside the template: hidden TextBlock.
        self.assertIn('<Setter Property="AutomationProperties.ItemStatus" '
                      'Value="{Binding row_state}"/>', self.grid)
        self.assertIn('x:Name="row_ws_state_text" Text="{Binding row_state}" '
                      'Visibility="Collapsed"', self.grid)
        for state, token in (("pending", "Warning"), ("applied", "Success"),
                             ("failed", "Danger")):
            self.assertIn('<Trigger Property="AutomationProperties.ItemStatus" Value="%s">'
                          % state, self.grid)
            self.assertIn('<DataTrigger Binding="{Binding Text, ElementName=row_ws_state_text}" '
                          'Value="%s">' % state, self.grid)
            self.assertIn('{StaticResource T3.%s.Fill}' % token, self.grid)
        self.assertNotIn('<DataTrigger Binding="{Binding row_state}"', self.grid)
        self.assertNotRegex(self.grid, r'"#[0-9A-Fa-f]{3,8}"')

    def test_no_templated_selection_changed(self):
        """SelectionChanged= in a DataTemplate is never wired; Python adds it."""
        self.assertNotIn('SelectionChanged=', self.grid)
        self.assertIn('Selector.SelectionChangedEvent', self.dialog)

    def test_bulk_editor_stages_only(self):
        self.assertIn('x:Name="btn_ws_stage_checked"', self.tab)
        self.assertIn('Click="ws_stage_checked_clicked"', self.tab)
        self.assertIn('def ws_stage_checked_clicked', self.dialog)
        self.assertNotIn('ws_target_changed', self.tab + self.dialog)
        self.assertIn('nothing is written until you press Apply', self.tab)

    def test_apply_is_one_transaction_with_rollback(self):
        body = self.dialog[self.dialog.index('def _apply_worksets'):
                           self.dialog.index('def _confirm_discard_ws')]
        self.assertEqual(body.count('disposing(Transaction('), 1)
        self.assertIn('disposing(SubTransaction(', body)
        self.assertIn('TransactionStatus.Committed', body)
        self.assertIn('rollback_all', body)
        self.assertNotIn('Transaction(doc', body.replace('disposing(Transaction(doc', '')
                         .replace('disposing(SubTransaction(doc', ''))

    def test_primary_label_is_apply(self):
        self.assertIn('TAB_WORKSETS: "Apply"', self.dialog)
        self.assertNotIn('Move to Workset', self.dialog)

    def test_refresh_and_close_confirm_discard(self):
        reload_body = self.dialog[self.dialog.index('def reload_links_clicked'):]
        reload_body = reload_body[:reload_body.index('\n    def ')]
        self.assertIn('_confirm_discard_ws', reload_body)
        self.assertIn('self.Closing += self._on_closing', self.dialog)
        closing = self.dialog[self.dialog.index('def _on_closing'):]
        closing = closing[:closing.index('\n    def ')]
        self.assertIn('_confirm_discard_ws', closing)



class TestManageLinks(unittest.TestCase):
    """Manage Links tab: status/pin labels, workset naming, tally, tab contract."""

    @classmethod
    def setUpClass(cls):
        cls.links = _load_links_module()
        cls.xaml = _read(os.path.join(LIB_DIR, 'GUI', 'Tools', 'BatchLink.xaml'))
        cls.dialog = _read(os.path.join(LIB_DIR, 'GUI', 'BatchLinkDialog.py'))

    def test_status_labels_and_severity(self):
        self.assertEqual(self.links.status_label("Loaded"), ("Loaded", "Success"))
        self.assertEqual(self.links.status_label("NotFound"), ("Not found", "Danger"))
        self.assertEqual(self.links.status_label("LocallyUnloaded"),
                         ("Unloaded for me", "Warning"))
        self.assertEqual(self.links.status_label("Weird"), ("Weird", "Warning"))

    def test_pinned_label(self):
        self.assertEqual(self.links.pinned_label(0, 0), u"\u2014")
        self.assertEqual(self.links.pinned_label(2, 2), "Yes")
        self.assertEqual(self.links.pinned_label(0, 3), "No")
        self.assertEqual(self.links.pinned_label(1, 3), "1 of 3")

    def test_link_workset_name(self):
        self.assertEqual(self.links.link_workset_name("Link_", r"C:\x\ARC Model.rvt"),
                         "Link_ARC Model")
        self.assertEqual(self.links.link_workset_name("", "STR.rvt"), "STR")

    def test_manage_tally(self):
        import ast
        tree = ast.parse(self.dialog)
        tree.body = [n for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name == 'manage_tally']
        scope = {}
        exec(compile(tree, 'BatchLinkDialog.py', 'exec'), scope)
        row = lambda loaded, checked=False: type('R', (), {
            'record': type('Rec', (), {'is_loaded': loaded})(), 'IsSelected': checked})()
        self.assertEqual(scope['manage_tally']([row(True), row(False, True)]),
                         "2 links · 1 loaded · 1 not loaded · 1 checked")
        self.assertEqual(scope['manage_tally']([row(True)]), "1 link · 1 loaded")

    def test_tabs_follow_the_daily_flow_and_match_the_constants(self):
        order = [self.xaml.index('x:Name="tab_item_{}"'.format(n))
                 for n in ('manage', 'link', 'worksets', 'display')]
        self.assertEqual(order, sorted(order))
        for name, tag in (('manage', 0), ('link', 1), ('worksets', 2), ('display', 3)):
            chip = self.xaml[self.xaml.index('x:Name="chip_tab_{}"'.format(name)):]
            self.assertIn('Tag="{}"'.format(tag), chip[:300])
        for const, value in (('TAB_MANAGE', 0), ('TAB_LINK', 1),
                             ('TAB_WORKSETS', 2), ('TAB_DISPLAY', 3)):
            self.assertIn('{} = {}'.format(const, value), self.dialog)

    def test_no_close_or_select_all_duplicates(self):
        # The title-bar X closes; each table's header checkbox selects all/none.
        for name in ('btn_cancel', 'btn_select_all', 'btn_select_none',
                     'btn_ws_select_all', 'btn_ws_select_none'):
            self.assertNotIn('x:Name="{}"'.format(name), self.xaml)
        for handler in ('cancel_button_clicked', 'def select_all_clicked',
                        'def ws_select_all_clicked'):
            self.assertNotIn(handler, self.dialog)

    def test_every_tab_has_the_same_toolbar_and_count_strip(self):
        for invert, refresh, count in (
                ('btn_mng_invert', 'btn_mng_refresh', 'lbl_mng_count'),
                ('btn_invert', 'btn_rescan', 'lbl_file_count'),
                ('btn_ws_invert', 'btn_ws_reload_links', 'lbl_ws_link_count'),
                ('btn_disp_invert', 'btn_disp_refresh', 'lbl_disp_count')):
            for name in (invert, refresh, count):
                self.assertIn('x:Name="{}"'.format(name), self.xaml)
        self.assertEqual(self.xaml.count('Style="{StaticResource T3.Search}"'), 4)
        self.assertEqual(self.xaml.count('Tag="Search'), 4)

    def test_reload_and_unload_never_run_inside_a_transaction(self):
        body = self.dialog[self.dialog.index('def _mng_run'):
                           self.dialog.index('def _mng_reload')]
        self.assertIn('if transaction_name:', body)
        for handler in ('def _mng_reload', 'def mng_unload_clicked',
                        'def mng_reload_from_clicked'):
            start = self.dialog.index(handler)
            end = self.dialog.index('\n    def ', start + 10)
            self.assertNotIn('transaction_name=', self.dialog[start:end])

if __name__ == '__main__':
    unittest.main()