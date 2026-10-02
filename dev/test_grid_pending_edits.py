# -*- coding: utf-8 -*-
"""
Tests for GUI/GridPendingEdits.py — the staged inline editing shared by
View Manager, Sheet Manager and Group Manager.

Also guards the regression this module exists for: View Manager and Sheet
Manager dispatched cell edits on the column's *header text*, which did not
match the header the XAML actually renders, so every inline edit was silently
discarded. The header-vs-binding test below fails if anyone reintroduces that.

Run: python dev/test_grid_pending_edits.py
"""
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(REPO, 'T3Lab.extension', 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from GUI import GridPendingEdits as pend       # noqa: E402

XNS = "{http://schemas.microsoft.com/winfx/2006/xaml}"
TOOLS = os.path.join(LIB_DIR, 'GUI', 'Tools')


def _read(path):
    """Whole file as text, with the handle closed (keeps the run warning-free)."""
    with open(path, encoding='utf-8') as handle:
        return handle.read()


class _Row(object):
    """Stands in for a grid row — a plain object, like the real ones."""


class _Path(object):
    def __init__(self, path):
        self.Path = path


class _Binding(object):
    def __init__(self, path):
        self.Path = _Path(path)


class _Column(object):
    """Stands in for a DataGridColumn; only the bits column_key reads."""

    def __init__(self, binding=None, selected=None, sort="", header=""):
        if binding is not None:
            self.Binding = _Binding(binding)
        if selected is not None:
            self.SelectedItemBinding = _Binding(selected)
        self.SortMemberPath = sort
        self.Header = header


class _TextEditor(object):
    def __init__(self, text=""):
        self.Text = text


class _ComboEditor(object):
    def __init__(self, selected=None):
        self.SelectedItem = selected
        self.Text = ""


class TestColumnKey(unittest.TestCase):
    """Which row field a column edits, resolved from its binding."""

    def test_text_column_uses_its_binding(self):
        self.assertEqual(pend.column_key(_Column(binding="sheet_name")), "sheet_name")

    def test_combo_column_uses_its_selected_item_binding(self):
        self.assertEqual(
            pend.column_key(_Column(selected="view_template")), "view_template")

    def test_falls_back_to_sort_member_path(self):
        self.assertEqual(pend.column_key(_Column(sort="scale")), "scale")

    def test_header_is_never_the_answer(self):
        """The header is display text and must not drive dispatch."""
        column = _Column(binding="name", header="VIEW NAME")
        self.assertEqual(pend.column_key(column), "name")

    def test_template_column_without_binding_yields_nothing(self):
        self.assertEqual(pend.column_key(_Column()), "")
        self.assertEqual(pend.column_key(None), "")


class TestEditorValue(unittest.TestCase):
    def test_reads_a_text_box(self):
        self.assertEqual(pend.editor_text(_TextEditor("A2-01")), "A2-01")

    def test_prefers_a_combo_selection(self):
        self.assertEqual(pend.editor_text(_ComboEditor("Fine")), "Fine")

    def test_missing_editor_is_none(self):
        self.assertIsNone(pend.editor_text(None))

    def test_revert_puts_the_old_value_back(self):
        editor = _TextEditor("typo")
        pend.revert_editor(editor, "original")
        self.assertEqual(editor.Text, "original")

    def test_revert_handles_none(self):
        editor = _TextEditor("x")
        pend.revert_editor(editor, None)
        self.assertEqual(editor.Text, u"")


class TestStaging(unittest.TestCase):
    def setUp(self):
        self.row = _Row()
        pend.init_pending(self.row, ("name", "scale"))

    def test_init_creates_every_dirty_flag(self):
        """A DataTrigger bound to a missing path never fires, so the flags
        must exist before the grid binds to them."""
        self.assertIs(self.row.dirty_name, False)
        self.assertIs(self.row.dirty_scale, False)
        self.assertFalse(pend.has_pending(self.row))

    def test_stage_marks_the_cell_and_keeps_the_value(self):
        pend.stage(self.row, "name", "Level 2 - Power")
        self.assertIs(self.row.dirty_name, True)
        self.assertEqual(pend.pending_of(self.row), {"name": "Level 2 - Power"})
        self.assertIs(self.row.dirty_scale, False)

    def test_unstage_clears_only_that_cell(self):
        pend.stage(self.row, "name", "A")
        pend.stage(self.row, "scale", "50")
        pend.unstage(self.row, "name")
        self.assertIs(self.row.dirty_name, False)
        self.assertIs(self.row.dirty_scale, True)
        self.assertEqual(pend.pending_of(self.row), {"scale": "50"})

    def test_clear_pending_wipes_the_row(self):
        pend.stage(self.row, "name", "A")
        pend.stage(self.row, "scale", "50")
        pend.clear_pending(self.row)
        self.assertIs(self.row.dirty_name, False)
        self.assertIs(self.row.dirty_scale, False)
        self.assertEqual(pend.pending_of(self.row), {})

    def test_counts_cells_and_rows(self):
        other = _Row()
        pend.init_pending(other, ("name",))
        pend.stage(self.row, "name", "A")
        pend.stage(self.row, "scale", "50")
        pend.stage(other, "name", "B")
        rows = [self.row, other]
        self.assertEqual(pend.pending_count(rows), 3)
        self.assertEqual(len(pend.pending_rows(rows)), 2)

    def test_counts_are_zero_for_clean_rows(self):
        self.assertEqual(pend.pending_count([self.row]), 0)
        self.assertEqual(pend.pending_rows([self.row]), [])
        self.assertEqual(pend.pending_count(None), 0)

    def test_stage_survives_a_row_that_was_never_initialised(self):
        bare = _Row()
        pend.stage(bare, "name", "A")
        self.assertEqual(pend.pending_of(bare), {"name": "A"})


class TestSameText(unittest.TestCase):
    """Retyping a value as it already was must not leave a cell amber."""

    def test_trims_before_comparing(self):
        self.assertTrue(pend.same_text(" A2-01 ", "A2-01"))

    def test_none_matches_empty(self):
        self.assertTrue(pend.same_text(None, ""))
        self.assertTrue(pend.same_text(None, None))

    def test_real_difference_is_reported(self):
        self.assertFalse(pend.same_text("A2-01", "A2-02"))

    def test_compares_non_strings_as_text(self):
        self.assertTrue(pend.same_text(50, "50"))


PNS = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"


def _local(tag):
    return tag.split('}')[-1]


def _column_field(column):
    """Row field a DataGrid column edits, from its Binding / SelectedItemBinding."""
    for attribute in ('Binding', 'SelectedItemBinding'):
        hit = re.match(r'\{Binding (\w+)(?:,[^}]*)?\}$', (column.get(attribute) or '').strip())
        if hit:
            return hit.group(1)
    return ''


def _bridged_flag(style, prefix):
    """Field whose `<prefix><field>` flag `style` reads through the string
    bridge — a Setter copying it into GridPendingEdits.CELL_BRIDGE_PROPERTY plus
    a property Trigger on that property for "True" — or ''."""
    prop = pend.CELL_BRIDGE_PROPERTY
    flag = ''
    for setter in style.findall(PNS + 'Setter'):          # direct children only
        if setter.get('Property') == prop:
            hit = re.match(r'\{Binding %s(\w+)\}$' % prefix, setter.get('Value') or '')
            flag = hit.group(1) if hit else ''
    if not flag:
        return ''
    fires = [t for t in style.iter(PNS + 'Trigger')
             if t.get('Property') == prop and t.get('Value') == 'True']
    return flag if fires else ''


class TestXamlWiring(unittest.TestCase):
    """Every staged field needs a working amber highlight in the XAML.

    A field the Python stages but the XAML cannot paint is an edit the user
    cannot see is pending — which is how this whole class of bug goes
    unnoticed. "Working" is the point: a `DataTrigger Binding="{Binding
    dirty_<field>}"` exists in the file yet never fires, because the Python row
    hands WPF a PyObject that never equals "True". The column that edits the
    field must read its flag through the cell string bridge instead.
    """

    CASES = (
        ('ManaViews.xaml', 'ManaViewsDialog.py', 'VIEW_EDIT_FIELDS'),
        ('ManaViews.xaml', 'ManaViewsDialog.py', 'TMPL_EDIT_FIELDS'),
        ('ManaSheets.xaml', 'ManaSheetsDialog.py', 'SHEET_EDIT_FIELDS'),
    )
    # Tools that compute the flag themselves instead of staging through
    # GridPendingEdits — same highlight, same bridge.
    OWN_FLAGS = (
        ('ManaGroup.xaml', ('NewName',)),
        ('ManaLoca.xaml', ('x_mm', 'y_mm', 'z_mm')),
    )

    @staticmethod
    def _declared_fields(dialog, constant):
        source = _read(os.path.join(LIB_DIR, 'GUI', dialog))
        match = re.search(constant + r'\s*=\s*\(([^)]*)\)', source)
        assert match, constant + " not found in " + dialog
        return [f.strip().strip('"\'') for f in match.group(1).split(',') if f.strip()]

    @staticmethod
    def _bridged_columns(xaml):
        """{field: flag} for every column whose CellStyle paints through the bridge."""
        root = ET.fromstring(_read(os.path.join(TOOLS, xaml)))
        out = {}
        for column in root.iter():
            tag = _local(column.tag)
            if not (tag.startswith('DataGrid') and tag.endswith('Column')):
                continue
            for style in column.iter(PNS + 'Style'):
                if style.get('TargetType') != 'DataGridCell':
                    continue
                flag = _bridged_flag(style, pend.DIRTY_PREFIX)
                if flag:
                    out[_column_field(column)] = flag
        return out

    def _assert_bridged(self, xaml, fields, origin):
        cells = self._bridged_columns(xaml)
        for field in fields:
            self.assertIn(
                field, cells,
                "%s stages '%s' but no column of %s bound to it paints dirty_%s "
                "through the cell bridge" % (origin, field, xaml, field))
            self.assertEqual(cells[field], field,
                             "%s: the column bound to '%s' reads dirty_%s"
                             % (xaml, field, cells[field]))

    def test_every_editable_field_has_a_working_amber_cell(self):
        for xaml, dialog, constant in self.CASES:
            self._assert_bridged(xaml, self._declared_fields(dialog, constant), dialog)

    def test_tools_with_their_own_flags_use_the_same_bridge(self):
        for xaml, fields in self.OWN_FLAGS:
            self._assert_bridged(xaml, fields, xaml)

    def test_no_trigger_binds_a_dirty_flag_directly(self):
        for xaml in ('ManaViews.xaml', 'ManaSheets.xaml', 'ManaGroup.xaml', 'ManaLoca.xaml'):
            source = _read(os.path.join(TOOLS, xaml))
            self.assertNotRegex(source, r'<DataTrigger Binding="\{Binding dirty_',
                                xaml + ": a DataTrigger on a Python flag never fires")

    def test_odd_coordinates_turn_red_through_the_bridge(self):
        """ManaLoca's red digits are an ElementStyle (TextBlock), same bridge."""
        root = ET.fromstring(_read(os.path.join(TOOLS, 'ManaLoca.xaml')))
        red = set()
        for column in root.iter(PNS + 'DataGridTextColumn'):
            for holder in column.findall(PNS + 'DataGridTextColumn.ElementStyle'):
                for style in holder.findall(PNS + 'Style'):
                    flag = _bridged_flag(style, 'odd_')
                    if flag:
                        self.assertEqual(flag, _column_field(column))
                        red.add(flag)
        self.assertEqual(red, {'x_mm', 'y_mm', 'z_mm'})

    def test_bridge_property_is_a_real_string_attached_property(self):
        """The name the XAML uses — WPF's AutomationProperties.ItemStatus is a
        string-typed attached property, so the binding converts the PyObject
        to text exactly like TextBlock.Text does."""
        self.assertEqual(pend.CELL_BRIDGE_PROPERTY, "AutomationProperties.ItemStatus")

    def test_edited_columns_are_not_read_only(self):
        """A column the tool stages edits for must actually be editable.

        Scoped per DataGrid: `name` and `scale` exist in both grids of View
        Manager, and the Templates grid is legitimately read-only for `scale`.
        """
        grids = {
            'views_grid': self._declared_fields('ManaViewsDialog.py', 'VIEW_EDIT_FIELDS'),
            'tmpl_grid': self._declared_fields('ManaViewsDialog.py', 'TMPL_EDIT_FIELDS'),
            'sheets_grid': self._declared_fields('ManaSheetsDialog.py', 'SHEET_EDIT_FIELDS'),
        }
        seen = set()
        for xaml in ('ManaViews.xaml', 'ManaSheets.xaml'):
            root = ET.fromstring(_read(os.path.join(TOOLS, xaml)))
            for grid in root.iter():
                name = grid.get(XNS + 'Name')
                if name not in grids:
                    continue
                seen.add(name)
                fields = grids[name]
                found = set()
                for element in grid.iter():
                    tag = element.tag.split('}')[-1]
                    if not (tag.startswith('DataGrid') and tag.endswith('Column')):
                        continue
                    bound = ''
                    for attribute in ('Binding', 'SelectedItemBinding'):
                        hit = re.match(r'\{Binding (\w+)\}$',
                                       (element.get(attribute) or '').strip())
                        if hit:
                            bound = hit.group(1)
                            break
                    if bound not in fields:
                        continue
                    found.add(bound)
                    self.assertNotEqual(
                        element.get('IsReadOnly'), 'True',
                        "%s/%s: column bound to '%s' is read-only but is staged"
                        % (xaml, name, bound))
                self.assertEqual(
                    sorted(found), sorted(fields),
                    "%s/%s: staged fields with no column of their own: %s"
                    % (xaml, name, sorted(set(fields) - found)))
        self.assertEqual(seen, set(grids), "grid not found in any XAML")


if __name__ == '__main__':
    unittest.main()
