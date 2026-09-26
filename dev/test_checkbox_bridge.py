# -*- coding: utf-8 -*-
"""Row checkboxes must show their Python row, and clicks must reach it.

pythonnet hands WPF a PyObject for every Python attribute. WPF converts it to
string (text columns render) but never to bool?, so a CheckBox bound straight
to a row attribute always reads unchecked: the tick vanishes on
Items.Refresh(), select-all ticks nothing (BatchOut, confirmed in Revit
2026-09-26). Every row checkbox now reads through a hidden string and
T3WPFWindow writes clicks back (WPF_Base.py, "Checkbox bridge").

Run: python dev/test_checkbox_bridge.py
"""
import ast
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
TOOLS = os.path.join(LIB, 'GUI', 'Tools')
WPF_BASE = os.path.join(LIB, 'GUI', 'WPF_Base.py')
sys.path.insert(0, LIB)

from GUI import WPF_Base  # noqa: E402  (imports cleanly without .NET)

X = '{http://schemas.microsoft.com/winfx/2006/xaml}'
P = '{http://schemas.microsoft.com/winfx/2006/xaml/presentation}'
BRIDGE = re.compile(r'^\{Binding Text, ElementName=(\w+), Mode=OneWay\}$')

# Files whose rows are DataRowView (typed bool columns) or UI-frozen.
EXEMPT = {'ManaAnno.xaml', 'DWGManagement.xaml'}


# ── fakes for the .NET binding API ───────────────────────────────────────────

def binding(path, element_name=None, relative=None, source=None):
    return SimpleNamespace(Path=SimpleNamespace(Path=path), ElementName=element_name,
                           RelativeSource=relative, Source=source)


class Ops(object):
    """BindingOperations double: bindings keyed by (element id, dp)."""

    def __init__(self):
        self.table = {}

    def set(self, element, dp, b):
        self.table[(id(element), dp)] = b

    def GetBinding(self, element, dp):
        return self.table.get((id(element), dp))


class Element(object):
    def __init__(self, names=None, **kw):
        self._names = names or {}
        self.__dict__.update(kw)

    def FindName(self, name):
        return self._names.get(name)


class Row(object):
    def __init__(self):
        self.writes = 0
        self._sel = False

    @property
    def is_selected(self):
        return self._sel

    @is_selected.setter
    def is_selected(self, value):
        self.writes += 1
        self._sel = value


def bridged(ops, text_binding, row, checked):
    bridge = Element()
    box = Element(names={'row_is_selected_text': bridge}, DataContext=row, IsChecked=checked)
    ops.set(box, 'IsChecked', binding('Text', 'row_is_selected_text'))
    if text_binding is not None:
        ops.set(bridge, 'Text', text_binding)
    return box


class WriteBack(unittest.TestCase):
    def prop(self, ops, box):
        return WPF_Base.bridged_row_property(box, ops, 'IsChecked', 'Text')[0]

    def test_click_reaches_the_row(self):
        ops, row = Ops(), Row()
        box = bridged(ops, binding('is_selected'), row, True)
        self.assertEqual(self.prop(ops, box), 'is_selected')
        self.assertTrue(WPF_Base.write_bridged_toggle(box, 'is_selected'))
        self.assertTrue(row.is_selected)

    def test_refresh_from_the_row_writes_nothing(self):
        ops, row = Ops(), Row()
        row._sel = True
        box = bridged(ops, binding('is_selected'), row, True)
        self.assertFalse(WPF_Base.write_bridged_toggle(box, 'is_selected'))
        self.assertEqual(row.writes, 0)

    def test_only_bridged_checkboxes_are_touched(self):
        ops = Ops()
        plain = Element(DataContext=Row(), IsChecked=True)        # no binding at all
        self.assertIsNone(self.prop(ops, plain))
        direct = Element(DataContext=Row(), IsChecked=True)
        ops.set(direct, 'IsChecked', binding('is_selected'))     # old direct binding
        self.assertIsNone(self.prop(ops, direct))
        for text_binding in (None,
                             binding('Name', element_name='other'),
                             binding('IsSelected', relative=object()),
                             binding('Row.is_selected'),
                             binding('[0]')):
            box = bridged(ops, text_binding, Row(), True)
            self.assertIsNone(self.prop(ops, box), text_binding)

    def test_rows_without_the_attribute_are_skipped(self):
        # DataRowView / DisconnectedItem: nothing to set, never raises
        box = Element(DataContext=SimpleNamespace(), IsChecked=True)
        self.assertFalse(WPF_Base.write_bridged_toggle(box, 'is_selected'))
        box = Element(DataContext=None, IsChecked=True)
        self.assertFalse(WPF_Base.write_bridged_toggle(box, 'is_selected'))


class WindowWiring(unittest.TestCase):
    def setUp(self):
        with open(WPF_BASE, encoding='utf-8') as f:
            self.src = f.read()

    def test_installed_before_templated_clicks(self):
        # Checked/Unchecked fire inside OnToggle, before Click; installing the
        # writer first also keeps it ahead on the window's handler list.
        body = self.src[self.src.index('def _load_via_xaml_reader'):]
        self.assertLess(body.index('self._install_checkbox_bridge()'),
                        body.index('self._wire_templated_clicks(unresolved)'))

    def test_bridge_is_reread_after_every_toggle(self):
        # Recycled containers: a bridge left at the pre-click "False" never
        # fires again on the next "False" row, which then shows a stale tick.
        body = self.src[self.src.index('def _on_bridged_toggle'):
                        self.src.index('def _wire_templated_clicks')]
        self.assertIn('GetBindingExpression(bridge, TextBlock.TextProperty)', body)
        self.assertIn('expr.UpdateTarget()', body)

    def test_listens_to_checked_and_unchecked_including_handled(self):
        body = self.src[self.src.index('def _install_checkbox_bridge'):
                        self.src.index('def _on_bridged_toggle')]
        self.assertIn('ToggleButton.CheckedEvent, self._t3_bridge_handler, True', body)
        self.assertIn('ToggleButton.UncheckedEvent, self._t3_bridge_handler, True', body)


def _xaml_files():
    return sorted(f for f in os.listdir(TOOLS) if f.endswith('.xaml'))


class ShippedXaml(unittest.TestCase):
    """Every row checkbox in every tool reads through a bridge."""

    def test_no_row_checkbox_binds_a_python_attribute_directly(self):
        bad = []
        for fname in _xaml_files():
            if fname in EXEMPT:
                continue
            root = ET.parse(os.path.join(TOOLS, fname)).getroot()
            parent = {c: p for p in root.iter() for c in p}

            def in_template(el):
                while el in parent:
                    el = parent[el]
                    if el.tag in (P + 'DataTemplate', P + 'HierarchicalDataTemplate'):
                        return True
                return False

            for el in root.iter():
                if el.tag == P + 'DataGridCheckBoxColumn' and el.get('Binding'):
                    bad.append('%s DataGridCheckBoxColumn %s' % (fname, el.get('Binding')))
                b = el.get('IsChecked') or ''
                if el.tag == P + 'CheckBox' and b.startswith('{Binding') and \
                        'ElementName' not in b and 'RelativeSource' not in b and in_template(el):
                    bad.append('%s CheckBox %s' % (fname, ' '.join(b.split())))
        self.assertEqual(bad, [])

    def test_every_bridge_points_at_a_hidden_sibling_textblock(self):
        count = 0
        for fname in _xaml_files():
            root = ET.parse(os.path.join(TOOLS, fname)).getroot()
            parent = {c: p for p in root.iter() for c in p}
            for box in root.iter(P + 'CheckBox'):
                m = BRIDGE.match(box.get('IsChecked') or '')
                if not m:
                    continue
                count += 1
                siblings = {s.get(X + 'Name'): s for s in parent[box]}
                bridge = siblings.get(m.group(1))
                self.assertIsNotNone(bridge, '%s: %s is not a sibling' % (fname, m.group(1)))
                self.assertEqual(bridge.tag, P + 'TextBlock', fname)
                self.assertEqual(bridge.get('Visibility'), 'Collapsed', fname)
                self.assertRegex(bridge.get('Text') or '', r'^\{Binding \w+\}$', fname)
        # 25 template checkboxes + 13 former DataGridCheckBoxColumns + BatchOut
        self.assertEqual(count, 39)

    def test_read_only_workset_state_is_display_only(self):
        root = ET.parse(os.path.join(TOOLS, 'ManaWorkset.xaml')).getroot()
        cols = {c.get('Header'): c for c in root.iter(P + 'DataGridTemplateColumn')}
        for header in ('ACTIVE', 'OPEN', 'EDITABLE'):
            box = next(cols[header].iter(P + 'CheckBox'))
            self.assertEqual(box.get('IsHitTestVisible'), 'False', header)


class CheckColumnAlignment(unittest.TestCase):
    """Header select-all box sits exactly over the row boxes: header and cell
    share one geometry (no padding, box centred)."""

    GRIDS = {'ManaViews.xaml': ('views_grid', 'tmpl_grid'),
             'ManaSheets.xaml': ('sheets_grid', 'renum_grid'),
             'ManaPara.xaml': ('dg_loader_params',),
             'ManaSched.xaml': ('xl_dg_schedules', 'dup_dg_schedules'),
             'ManaStyles.xaml': ('grid_style', 'grid_pattern', 'grid_fill'),
             'ModelAuditor.xaml': ('dg_smart_purge',),
             'SheetGen.xaml': ('room_datagrid',),
             'UIStandardShowcase.xaml': ('sample_grid',),
             'PDFImport.xaml': ('grid_views',),
             'ModelAuditorDetail.xaml': ('dg_detail_elements',)}

    def test_check_columns_use_the_shared_geometry(self):
        for fname, grids in self.GRIDS.items():
            root = ET.parse(os.path.join(TOOLS, fname)).getroot()
            for grid in root.iter(P + 'DataGrid'):
                if grid.get(X + 'Name') not in grids:
                    continue
                col = next(grid.iter(P + 'DataGridTemplateColumn'))
                where = '%s %s' % (fname, grid.get(X + 'Name'))
                self.assertEqual(col.get('HeaderStyle'),
                                 '{StaticResource T3.DataGridColumnHeader.Check}', where)
                self.assertEqual(col.get('CellStyle'),
                                 '{StaticResource T3.DataGridCell.Check}', where)
                boxes = list(col.iter(P + 'CheckBox'))
                self.assertEqual(len(boxes), 2, where)          # header + row
                for box in boxes:
                    self.assertEqual(box.get('Style'), '{StaticResource T3.CheckBox.Cell}', where)
                    for geometry in ('Padding', 'Margin', 'HorizontalAlignment'):
                        self.assertIsNone(box.get(geometry), where)

    def test_renumber_tab_has_the_same_metrics_frame_as_the_sheets_tab(self):
        root = ET.parse(os.path.join(TOOLS, 'ManaSheets.xaml')).getroot()
        tabs = {t.get(X + 'Name'): t for t in root.iter(P + 'TabItem')}
        widths = []
        for tab in ('tab_sheets_item', 'tab_renumber_item'):
            captions = [t.get('Text') for t in tabs[tab].iter(P + 'TextBlock')
                        if t.get('Style') == '{StaticResource T3.Caption}']
            self.assertEqual(captions[:2], ['TOTAL SHEETS', 'SELECTED'], tab)
            widths.append([c.get('Width') for c in tabs[tab].iter(P + 'ColumnDefinition')][:1])
        self.assertEqual(widths[0], widths[1])                 # strip does not jump


class PdfImportGrid(unittest.TestCase):
    """PAGE is a plain centred number and every header sits centred."""

    def grid(self):
        root = ET.parse(os.path.join(TOOLS, 'PDFImport.xaml')).getroot()
        return next(g for g in root.iter(P + 'DataGrid') if g.get(X + 'Name') == 'grid_views')

    def test_page_number_has_no_box(self):
        page = next(c for c in self.grid().iter(P + 'DataGridTemplateColumn')
                    if c.get('Header') == 'PAGE')
        template = page.find(P + 'DataGridTemplateColumn.CellTemplate')
        self.assertEqual(list(template.iter(P + 'Border')), [])
        cell = next(template.iter(P + 'TextBlock'))
        self.assertEqual(cell.get('Text'), '{Binding PageDisplay}')
        self.assertEqual(cell.get('Style'), '{StaticResource T3.Cell.Center}')

    def test_headers_are_centred(self):
        cols = list(self.grid().find(P + 'DataGrid.Columns'))
        self.assertEqual([c.get('Header') for c in cols[1:]],
                         ['PAGE', 'VIEW / SHEET NAME', 'TYPE'])
        for col in cols[1:]:
            self.assertEqual(col.get('HeaderStyle'),
                             '{StaticResource T3.DataGridColumnHeader.Center}', col.get('Header'))


class MetricDetailGrid(unittest.TestCase):
    def test_id_starts_where_its_header_starts(self):
        # T3.Cell.Number pushed the ids to the right edge, far from "ID".
        root = ET.parse(os.path.join(TOOLS, 'ModelAuditorDetail.xaml')).getroot()
        col = next(c for c in root.iter(P + 'DataGridTextColumn') if c.get('Header') == 'ID')
        self.assertEqual(col.get('ElementStyle'), '{StaticResource T3.Mono}')

    def test_header_box_follows_the_footer_buttons(self):
        src = open(os.path.join(LIB, 'GUI', 'ModelAuditorDialog.py'), encoding='utf-8').read()
        for handler in ('def on_check_all', 'def on_uncheck_all'):
            body = src[src.index(handler):]
            body = body[:body.index('\n    def ', 1)]
            self.assertIn('self.sync_header_checkbox(self.chk_all_dg_detail_elements', body)


class CustomFilenameCell(unittest.TestCase):
    def test_cell_takes_the_row_colour(self):
        root = ET.parse(os.path.join(TOOLS, 'ExportManager.xaml')).getroot()
        box = next(t for t in root.iter(P + 'TextBox')
                   if 'CustomFilename' in (t.get('Text') or ''))
        self.assertEqual(box.get('Background'), 'Transparent')
        self.assertEqual(box.get('BorderBrush'), 'Transparent')


if __name__ == '__main__':
    unittest.main()
