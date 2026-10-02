# -*- coding: utf-8 -*-
"""Style Manager (ManaStyles) inline rename — tested without Revit/WPF.

Covers:
  * Services/style_naming.py  — the pure name checks used by BOTH the inline
    editor and the footer Rename buttons;
  * Services/style_rename.py  — one Transaction per rename, rollback on error
    (run against a simulated Revit API);
  * GUI/inline_rename.py      — double-click / F2 / Enter / Esc / focus-loss
    flow of the controller (run against fake WPF objects);
  * ManaStyles.xaml + ManaStylesDialog.py wiring for the three grids.

Run: python3 dev/test_style_naming.py
"""
import ast
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / 'T3Lab.extension' / 'lib'
XAML = LIB / 'GUI' / 'Tools' / 'ManaStyles.xaml'
DIALOG = LIB / 'GUI' / 'ManaStylesDialog.py'
sys.path.insert(0, str(LIB))

from Services.style_naming import (  # noqa: E402
    FILL_PATTERN, INVALID_NAME_CHARS, LINE_PATTERN, LINE_STYLE, RENAME_HINT,
    check_renamable, rename_tooltip, renamed_status, sorted_position,
    validate_style_rename)

X = '{http://schemas.microsoft.com/winfx/2006/xaml}'
P = '{http://schemas.microsoft.com/winfx/2006/xaml/presentation}'


# ═════════════════════════════════════════════════════════════════════════
# 1 · pure name checks
# ═════════════════════════════════════════════════════════════════════════

class ValidateTests(unittest.TestCase):
    def v(self, new, old='A', existing=('A', 'B', 'Hidden'), kind=LINE_PATTERN, system=False):
        return validate_style_rename(kind, old, new, list(existing), system)

    def test_trims_and_accepts(self):
        self.assertEqual(self.v('  New name  '), (True, 'New name', ''))

    def test_empty_refused_with_what_where_next(self):
        ok, _, msg = self.v('   ')
        self.assertFalse(ok)
        self.assertIn("Line pattern 'A'", msg)
        self.assertIn('cannot be empty', msg)
        self.assertIn('Esc', msg)

    def test_unchanged_is_silent_noop(self):
        self.assertEqual(self.v('A'), (False, 'A', ''))
        self.assertEqual(self.v(' A '), (False, 'A', ''))

    def test_every_revit_forbidden_char_refused(self):
        for ch in INVALID_NAME_CHARS:
            ok, _, msg = self.v('X{}Y'.format(ch))
            self.assertFalse(ok, ch)
            self.assertIn(ch, msg)
            self.assertIn('does not allow', msg)

    def test_control_chars_refused(self):
        ok, _, msg = self.v('A\tB')
        self.assertFalse(ok)
        self.assertIn('\\x09', msg)

    def test_imperial_names_allowed(self):
        # Revit's own imperial templates use / and " in pattern names.
        self.assertTrue(self.v('Dash 1/16"')[0])
        self.assertTrue(self.v('Diagonal crosshatch 3/32"', kind=FILL_PATTERN)[0])

    def test_duplicate_case_insensitive(self):
        ok, _, msg = self.v('hidden')
        self.assertFalse(ok)
        self.assertIn("named 'Hidden' already exists", msg)

    def test_own_name_is_not_a_duplicate(self):
        # Case-only change of a pattern: renamed in place, allowed.
        self.assertEqual(self.v('a'), (True, 'a', ''))

    def test_case_only_line_style_refused(self):
        ok, _, msg = self.v('a', kind=LINE_STYLE)
        self.assertFalse(ok)
        self.assertIn('letter case', msg)
        self.assertIn("Line style 'A'", msg)

    def test_system_refused_before_anything_else(self):
        ok, _, msg = self.v('Other', old='<Thin Lines>', kind=LINE_STYLE, system=True)
        self.assertFalse(ok)
        self.assertIn("Line style '<Thin Lines>'", msg)
        self.assertIn('cannot be renamed', msg)

    def test_check_renamable(self):
        self.assertEqual(check_renamable(FILL_PATTERN, 'Custom', False), (True, ''))
        ok, msg = check_renamable(FILL_PATTERN, '<Solid fill>', True)
        self.assertFalse(ok)
        self.assertIn("Fill pattern '<Solid fill>'", msg)
        self.assertIn('Duplicate it', msg)

    def test_tooltips(self):
        self.assertEqual(rename_tooltip(LINE_STYLE, False), RENAME_HINT)
        self.assertEqual(RENAME_HINT, 'Double-click or press F2 to rename')
        self.assertIn('cannot be renamed', rename_tooltip(LINE_PATTERN, True))

    def test_status(self):
        self.assertEqual(renamed_status(LINE_PATTERN, 'A', 'B'),
                         "Renamed line pattern 'A' to 'B'")
        self.assertEqual(renamed_status(LINE_STYLE, 'A', 'B', '(3 line(s) moved to it)'),
                         "Renamed line style 'A' to 'B' (3 line(s) moved to it)")


class SortedPositionTests(unittest.TestCase):
    def check(self, before, index, new_name):
        names = list(before)
        names[index] = new_name
        target = sorted_position(names, new_name, index)
        moved = list(names)
        moved.insert(target, moved.pop(index))
        self.assertEqual(moved, sorted(names))

    def test_moves(self):
        base = ['Alpha', 'Bravo', 'Charlie', 'Delta']
        self.check(base, 0, 'Zulu')
        self.check(base, 3, 'Aaa')
        self.check(base, 1, 'Cat')
        self.check(base, 2, 'Charlie2')
        self.check(['Only'], 0, 'Other')


# ═════════════════════════════════════════════════════════════════════════
# 2 · Revit operations: one transaction, rollback on failure
# ═════════════════════════════════════════════════════════════════════════

class FakeTransaction(object):
    log = []

    def __init__(self, doc, name):
        self.name = name
        self.started = self.ended = False
        self.rolled_back = self.disposed = False
        FakeTransaction.log.append(self)

    def Start(self):
        self.started = True

    def Commit(self):
        self.ended = True

    def RollBack(self):
        self.rolled_back = self.ended = True

    def HasStarted(self):
        return self.started

    def HasEnded(self):
        return self.ended

    def Dispose(self):
        self.disposed = True


class FakeCollector(object):
    curves = []

    def __init__(self, doc):
        pass

    def OfClass(self, cls):
        return self

    def __iter__(self):
        return iter(FakeCollector.curves)

    def Dispose(self):
        pass


def _eid(v):
    return SimpleNamespace(Value=v)


def load_rename_service():
    """Exec Services/style_rename.py with fake Autodesk/_compat modules."""
    db = types.ModuleType('Autodesk.Revit.DB')
    db.Transaction = FakeTransaction
    db.FilteredElementCollector = FakeCollector
    db.CurveElement = object
    db.GraphicsStyleType = SimpleNamespace(Projection='Projection')
    db.BuiltInCategory = SimpleNamespace(OST_Lines='OST_Lines')
    saved = {k: sys.modules.get(k) for k in ('Autodesk', 'Autodesk.Revit', 'Autodesk.Revit.DB')}
    sys.modules['Autodesk'] = types.ModuleType('Autodesk')
    sys.modules['Autodesk.Revit'] = types.ModuleType('Autodesk.Revit')
    sys.modules['Autodesk.Revit.DB'] = db
    try:
        src = (LIB / 'Services' / 'style_rename.py').read_text(encoding='utf-8')
        scope = {'__name__': 'style_rename_under_test'}
        exec(compile(src, 'style_rename.py', 'exec'), scope)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return SimpleNamespace(**scope)


class RenameServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = load_rename_service()

    def setUp(self):
        FakeTransaction.log = []
        FakeCollector.curves = []

    def test_line_pattern_one_transaction(self):
        el = SimpleNamespace(Name='Old')
        self.svc.rename_line_pattern(None, el, 'New')
        self.assertEqual(el.Name, 'New')
        self.assertEqual(len(FakeTransaction.log), 1)
        t = FakeTransaction.log[0]
        self.assertTrue(t.started and t.ended and t.disposed and not t.rolled_back)

    def test_line_pattern_failure_rolls_back(self):
        class Bad(object):
            @property
            def Name(self):
                return 'Old'

            @Name.setter
            def Name(self, v):
                raise ValueError('name in use')
        with self.assertRaises(ValueError):
            self.svc.rename_line_pattern(None, Bad(), 'New')
        t = FakeTransaction.log[0]
        self.assertTrue(t.rolled_back and t.disposed)

    def test_fill_pattern_falls_back_to_set_fill_pattern(self):
        pattern = SimpleNamespace(Name='Old')
        written = []

        class El(object):
            @property
            def Name(self):
                return 'Old'

            @Name.setter
            def Name(self, v):
                raise RuntimeError('read-only here')

            def GetFillPattern(self):
                return pattern

            def SetFillPattern(self, p):
                written.append(p.Name)
        self.svc.rename_fill_pattern(None, El(), 'New')
        self.assertEqual(written, ['New'])
        self.assertEqual(len(FakeTransaction.log), 1)
        self.assertFalse(FakeTransaction.log[0].rolled_back)

    def _line_style_doc(self):
        created = []
        deleted = []
        new_style = object()

        class NewSub(object):
            def __init__(self, name):
                self.Name = name
                self.LineColor = None
                self.weight = self.pattern = None

            def SetLineWeight(self, w, k):
                self.weight = w

            def SetLinePatternId(self, pid, k):
                self.pattern = pid

            def GetGraphicsStyle(self, k):
                return new_style

        def new_subcategory(parent, name):
            sub = NewSub(name)
            created.append(sub)
            return sub
        cats = SimpleNamespace(get_Item=lambda bic: 'LINES', NewSubcategory=new_subcategory)
        doc = SimpleNamespace(Settings=SimpleNamespace(Categories=cats),
                              Delete=lambda eid: deleted.append(eid.Value))
        old = SimpleNamespace(Id=_eid(500), LineColor='RED',
                              GetLineWeight=lambda k: 3,
                              GetLinePatternId=lambda k: _eid(77))
        return doc, old, created, deleted, new_style

    def test_line_style_single_transaction_moves_lines(self):
        doc, old, created, deleted, new_style = self._line_style_doc()
        mine = SimpleNamespace(LineStyle=SimpleNamespace(GraphicsStyleCategory=SimpleNamespace(Id=_eid(500))))
        other = SimpleNamespace(LineStyle=SimpleNamespace(GraphicsStyleCategory=SimpleNamespace(Id=_eid(9))))
        FakeCollector.curves = [mine, other]
        sub, moved = self.svc.rename_line_style(doc, old, 'Renamed')
        self.assertEqual(sub.Name, 'Renamed')
        self.assertEqual((sub.LineColor, sub.weight, sub.pattern.Value), ('RED', 3, 77))
        self.assertEqual(moved, 1)
        self.assertIs(mine.LineStyle, new_style)
        self.assertIsNot(other.LineStyle, new_style)
        self.assertEqual(deleted, [500])
        self.assertEqual(len(FakeTransaction.log), 1)
        self.assertFalse(FakeTransaction.log[0].rolled_back)

    def test_line_style_unmovable_line_rolls_back_everything(self):
        doc, old, created, deleted, _ = self._line_style_doc()

        class Locked(object):
            @property
            def LineStyle(self):
                return SimpleNamespace(GraphicsStyleCategory=SimpleNamespace(Id=_eid(500)))

            @LineStyle.setter
            def LineStyle(self, v):
                raise RuntimeError('in group')
        FakeCollector.curves = [Locked()]
        with self.assertRaises(RuntimeError) as ctx:
            self.svc.rename_line_style(doc, old, 'Renamed')
        self.assertIn('Nothing was renamed', str(ctx.exception))
        self.assertEqual(deleted, [])
        t = FakeTransaction.log[0]
        self.assertTrue(t.rolled_back and t.disposed)


# ═════════════════════════════════════════════════════════════════════════
# 3 · inline editor controller against fake WPF objects
# ═════════════════════════════════════════════════════════════════════════

class Event(object):
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def __isub__(self, h):
        self.handlers.remove(h)
        return self


class FakeVisual(object):
    def __init__(self, name='', children=(), parent=None):
        self.Name = name
        self.children = list(children)
        self.parent = parent
        for c in self.children:
            c.parent = self


class FakeCell(FakeVisual):
    pass


class FakeContextMenu(FakeVisual):
    pass


class FakeDispatcher(object):
    def __init__(self):
        self.queued = []

    def BeginInvoke(self, priority, fn):
        self.queued.append((priority, fn))

    def run(self):
        while self.queued:
            self.queued.pop(0)[1]()


DISPATCHER = FakeDispatcher()


class FakeEditor(FakeVisual):
    def __init__(self):
        FakeVisual.__init__(self, 'row_name_editor')
        self.Text = ''
        self.Visibility = 'Collapsed'
        self.LostKeyboardFocus = Event()
        self.IsKeyboardFocused = True
        self.Dispatcher = DISPATCHER
        self.selected_all = 0

    def Focus(self):
        return True

    def SelectAll(self):
        self.selected_all += 1


class FakeItems(list):
    @property
    def Count(self):
        return len(self)


def load_controller_module():
    names = {
        'System': dict(Action=lambda f: f),
        'System.Windows': dict(Visibility=SimpleNamespace(
            Visible='Visible', Collapsed='Collapsed', Hidden='Hidden')),
        'System.Windows.Controls': dict(DataGridCell=FakeCell, ContextMenu=FakeContextMenu,
                                        MenuItem=FakeContextMenu),
        'System.Windows.Input': dict(
            Key=SimpleNamespace(Return='Return', Escape='Escape', F2='F2', A='A'),
            Keyboard=SimpleNamespace(Modifiers='NoMod', Focus=lambda el: None),
            ModifierKeys=SimpleNamespace(**{'None': 'NoMod'}),
            MouseButton=SimpleNamespace(Left='Left', Right='Right'),
            KeyboardFocusChangedEventHandler=lambda f: f),
        'System.Windows.Media': dict(
            Visual=FakeVisual,
            VisualTreeHelper=SimpleNamespace(
                GetParent=lambda n: n.parent,
                GetChildrenCount=lambda n: len(n.children),
                GetChild=lambda n, i: n.children[i])),
        'System.Windows.Threading': dict(DispatcherPriority=SimpleNamespace(
            Input='Input', Send='Send')),
    }
    saved = {k: sys.modules.get(k) for k in names}
    for mod, attrs in names.items():
        m = types.ModuleType(mod)
        m.__dict__.update(attrs)
        sys.modules[mod] = m
    try:
        src = (LIB / 'GUI' / 'inline_rename.py').read_text(encoding='utf-8')
        scope = {'__name__': 'inline_rename_under_test'}
        exec(compile(src, 'inline_rename.py', 'exec'), scope)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return SimpleNamespace(**scope)


class Row(object):
    def __init__(self, name, is_system=False):
        self.name = name
        self.is_system = is_system


class Harness(object):
    def __init__(self, mod, rows):
        self.rows = rows
        self.cells = []
        self.row_visuals = []
        self.editors = []
        for row in rows:
            editor = FakeEditor()
            display = FakeVisual('row_name_display')
            display.Visibility = 'Visible'
            cell = FakeCell(children=[FakeVisual(children=[display, editor])])
            cell.Column = SimpleNamespace(SortMemberPath='name')
            cell.DataContext = row
            id_cell = FakeCell(children=[FakeVisual('row_id_text')])
            self.row_visuals.append(FakeVisual('row', children=[id_cell, cell]))
            self.cells.append(cell)
            self.editors.append(editor)
        name_col = SimpleNamespace(SortMemberPath='name')
        harness = self
        self.grid = SimpleNamespace(
            Columns=[SimpleNamespace(SortMemberPath=None), name_col],
            Items=FakeItems(rows),
            ItemContainerGenerator=SimpleNamespace(
                ContainerFromIndex=lambda i: harness.row_visuals[i]),
            MouseDoubleClick=Event(), PreviewKeyDown=Event(), PreviewMouseWheel=Event(),
            CurrentItem=None, SelectedItem=None, SelectedIndex=-1,
            Dispatcher=DISPATCHER,
            ScrollIntoView=lambda *a: None, UpdateLayout=lambda: None)
        self.reports = []
        self.applied = []
        self.ctl = mod.InlineRenameController(
            self.grid,
            can_begin=lambda item: ((False, 'refused ' + item.name) if item.is_system
                                    else (True, '')),
            validate=lambda item, text: validate_style_rename(
                LINE_PATTERN, item.name, text, [r.name for r in rows], item.is_system),
            apply=self._apply,
            report=self.reports.append)

    def _apply(self, item, clean):
        self.applied.append((item.name, clean))
        item.name = clean
        return item

    def key(self, k):
        e = SimpleNamespace(Key=k, Handled=False)
        for h in self.grid.PreviewKeyDown.handlers:
            h(self.grid, e)
        return e

    def double_click(self, source, button='Left'):
        e = SimpleNamespace(ChangedButton=button, OriginalSource=source, Handled=False)
        for h in self.grid.MouseDoubleClick.handlers:
            h(self.grid, e)
        return e

    def lose_focus(self, editor, new_focus):
        for h in list(editor.LostKeyboardFocus.handlers):
            h(editor, SimpleNamespace(NewFocus=new_focus))


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_controller_module()

    def setUp(self):
        DISPATCHER.queued = []
        self.h = Harness(self.mod, [Row('Alpha'), Row('Bravo'), Row('<Solid>', True)])

    def test_double_click_on_name_cell_starts_edit_with_text(self):
        h = self.h
        display = h.cells[0].children[0].children[0]
        e = h.double_click(display)
        self.assertTrue(e.Handled)
        self.assertTrue(h.ctl.is_editing)
        ed = h.editors[0]
        self.assertEqual((ed.Visibility, ed.Text), ('Visible', 'Alpha'))
        self.assertEqual(display.Visibility, 'Hidden')
        self.assertGreaterEqual(ed.selected_all, 1)

    def test_double_click_other_column_or_right_button_ignored(self):
        h = self.h
        other = FakeCell()
        other.Column = SimpleNamespace(SortMemberPath='id')
        other.DataContext = h.rows[0]
        h.double_click(other)
        h.double_click(h.cells[0], button='Right')
        self.assertFalse(h.ctl.is_editing)

    def test_system_row_refused_with_message(self):
        h = self.h
        h.double_click(h.cells[2])
        self.assertFalse(h.ctl.is_editing)
        self.assertEqual(h.reports[-1], 'refused <Solid>')
        self.assertEqual(h.editors[2].Visibility, 'Collapsed')

    def test_f2_and_enter_start_edit_on_selected_row(self):
        h = self.h
        h.grid.SelectedItem = h.rows[1]
        e = h.key('F2')
        self.assertTrue(e.Handled and h.ctl.is_editing)
        self.assertEqual(h.editors[1].Text, 'Bravo')
        h.ctl.cancel()
        h.grid.CurrentItem = h.rows[0]
        h.key('Return')
        self.assertEqual(h.editors[0].Visibility, 'Visible')

    def test_enter_commits_valid_name(self):
        h = self.h
        h.double_click(h.cells[0])
        h.editors[0].Text = '  Zulu '
        e = h.key('Return')
        self.assertTrue(e.Handled)
        self.assertEqual(h.applied, [('Alpha', 'Zulu')])
        self.assertFalse(h.ctl.is_editing)
        self.assertEqual(h.editors[0].Visibility, 'Collapsed')
        self.assertEqual(h.editors[0].LostKeyboardFocus.handlers, [])

    def test_enter_with_bad_name_keeps_editing(self):
        h = self.h
        h.double_click(h.cells[0])
        h.editors[0].Text = 'bravo'
        h.key('Return')
        self.assertTrue(h.ctl.is_editing)
        self.assertEqual(h.applied, [])
        self.assertIn('already exists', h.reports[-1])

    def test_escape_cancels_without_apply(self):
        h = self.h
        h.double_click(h.cells[0])
        h.editors[0].Text = 'Zulu'
        self.assertTrue(h.ctl.handle_escape())
        self.assertFalse(h.ctl.is_editing)
        self.assertEqual(h.applied, [])
        self.assertEqual(h.rows[0].name, 'Alpha')
        self.assertFalse(h.ctl.handle_escape())     # nothing left to cancel

    def test_focus_loss_commits_after_event(self):
        h = self.h
        h.double_click(h.cells[0])
        h.editors[0].Text = 'Zulu'
        h.lose_focus(h.editors[0], FakeVisual('btn'))
        self.assertFalse(h.ctl.is_editing)
        self.assertEqual(h.applied, [])                 # deferred ...
        self.assertEqual(DISPATCHER.queued[0][0], 'Send')
        DISPATCHER.run()
        self.assertEqual(h.applied, [('Alpha', 'Zulu')])  # ... then applied

    def test_focus_loss_to_nothing_or_context_menu_keeps_editing(self):
        h = self.h
        h.double_click(h.cells[0])
        h.lose_focus(h.editors[0], None)
        h.lose_focus(h.editors[0], FakeContextMenu())
        self.assertTrue(h.ctl.is_editing)

    def test_focus_loss_with_bad_name_reverts(self):
        h = self.h
        h.double_click(h.cells[0])
        h.editors[0].Text = 'a{b'
        h.lose_focus(h.editors[0], FakeVisual('btn'))
        DISPATCHER.run()
        self.assertFalse(h.ctl.is_editing)
        self.assertEqual(h.applied, [])
        self.assertIn('does not allow', h.reports[-1])

    def test_enter_commit_refocuses_name_cell(self):
        h = self.h
        focused = []
        h.cells[1].Focus = lambda: focused.append('name-cell')
        h.grid.SelectedItem = h.rows[1]
        h.key('F2')
        h.editors[1].Text = 'Charlie'
        h.key('Return')
        self.assertEqual(h.applied, [('Bravo', 'Charlie')])
        self.assertEqual(h.grid.SelectedIndex, 1)
        self.assertEqual(focused, ['name-cell'])

    def test_unchanged_name_leaves_quietly(self):
        h = self.h
        h.double_click(h.cells[0])
        h.key('Return')
        self.assertFalse(h.ctl.is_editing)
        self.assertEqual(h.applied, [])


# ═════════════════════════════════════════════════════════════════════════
# 4 · XAML + dialog wiring
# ═════════════════════════════════════════════════════════════════════════

def _find_grid(root, name):
    for el in root.iter(P + 'DataGrid'):
        if el.get(X + 'Name') == name:
            return el
    return None


class XamlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ET.parse(str(XAML)).getroot()

    def test_three_grids_have_inline_name_column(self):
        for grid_name in ('grid_style', 'grid_pattern', 'grid_fill'):
            grid = _find_grid(self.root, grid_name)
            self.assertIsNotNone(grid, grid_name)
            cols = [c for c in grid.iter(P + 'DataGridTemplateColumn')
                    if c.get('SortMemberPath') == 'name']
            self.assertEqual(len(cols), 1, grid_name)
            col = cols[0]
            self.assertEqual(col.get('IsReadOnly'), 'True')
            header = col.find(P + 'DataGridTemplateColumn.Header/' + P + 'TextBlock')
            self.assertEqual(header.get('Text'), 'NAME')
            self.assertEqual(header.get('ToolTip'), RENAME_HINT)
            named = {e.get(X + 'Name'): e for e in col.iter() if e.get(X + 'Name')}
            editor = named['row_name_editor']
            self.assertEqual(editor.tag, P + 'TextBox')
            self.assertEqual(editor.get('Style'), '{StaticResource T3.TextBox}')
            self.assertEqual(editor.get('Visibility'), 'Collapsed')
            self.assertEqual(named['row_name_display'].get('Text'), '{Binding name}')
            self.assertEqual(named['row_name_tip'].get('Text'), '{Binding rename_tip}')
            # No other NAME text column left behind.
            for tc in grid.iter(P + 'DataGridTextColumn'):
                self.assertNotEqual(tc.get('Header'), 'NAME', grid_name)

    def test_checkbox_columns_untouched(self):
        for grid_name in ('grid_style', 'grid_pattern', 'grid_fill'):
            grid = _find_grid(self.root, grid_name)
            header_cb = [c for c in grid.iter(P + 'CheckBox')
                         if c.get(X + 'Name') == 'chk_all_' + grid_name]
            self.assertEqual(len(header_cb), 1)


class DialogWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = DIALOG.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.src)
        cls.methods = {}
        for node in ast.walk(cls.tree):
            if isinstance(node, ast.ClassDef) and node.name == 'ManaStylesWindow':
                for f in node.body:
                    if isinstance(f, ast.FunctionDef):
                        cls.methods[f.name] = ast.get_source_segment(cls.src, f)

    def test_three_grids_get_a_renamer(self):
        init = self.methods['__init__']
        for kind, grid in (('LINE_STYLE', 'grid_style'), ('LINE_PATTERN', 'grid_pattern'),
                           ('FILL_PATTERN', 'grid_fill')):
            self.assertIn('self._make_inline_renamer({}, self.{})'.format(kind, grid), init)

    def test_buttons_and_inline_share_one_path(self):
        for handler, kind in (('_on_style_rename', 'LINE_STYLE'),
                              ('_on_pattern_rename', 'LINE_PATTERN'),
                              ('_on_fill_rename', 'FILL_PATTERN')):
            self.assertIn('self._rename_ticked({}'.format(kind), self.methods[handler])
        self.assertIn('self._apply_rename(kind, item, clean)', self.methods['_rename_ticked'])
        self.assertIn('self._apply_rename(kind, item, clean)', self.methods['_make_inline_renamer'])
        apply_src = self.methods['_apply_rename']
        for fn in ('rename_line_style', 'rename_line_pattern', 'rename_fill_pattern'):
            self.assertIn(fn + '(doc', apply_src)
        # No Revit transaction code left inside the dialog's rename methods.
        for name in ('_apply_rename', '_rename_ticked', '_on_style_rename',
                     '_on_pattern_rename', '_on_fill_rename'):
            self.assertNotIn('Transaction(', self.methods[name])

    def test_escape_routed_to_editor_first(self):
        esc = self.methods['_handle_esc_key']
        self.assertIn('handle_escape()', esc)
        self.assertIn('T3WPFWindow._handle_esc_key(self, sender, args)', esc)

    def test_rows_expose_rename_tip(self):
        self.assertEqual(self.src.count('def rename_tip(self)'), 3)


if __name__ == '__main__':
    unittest.main(verbosity=1)
