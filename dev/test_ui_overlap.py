# -*- coding: utf-8 -*-
"""Elements that must never sit on top of each other (2026-09-26).

- A bool bound straight to Visibility has no converter in a tool XAML, so the
  binding fails and Visibility stays Visible: PropertyLine's "No parcels found"
  sat on top of its hint text, and seven tools kept their empty-state text over
  the rows of a filled grid.
- A Python row attribute ("Visible"/"Collapsed") reaches WPF as a PyObject,
  which does not convert to Visibility either (ModelAuditor "Detail" buttons).
- BatchLink's tab bar follows the BGTheme strip.
- A button taller than the fixed bar it sits in loses its bottom stroke
  (Ribbon Names "Save Map": 44px row - 8+8 padding - 1px rule = 27px for a
  28px button). Same in ManaStyles and ManaWorkset.

Run: python dev/test_ui_overlap.py
"""
import ast
import os
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI = os.path.join(REPO, 'T3Lab.extension', 'lib', 'GUI')
TOOLS = os.path.join(GUI, 'Tools')
X = '{http://schemas.microsoft.com/winfx/2006/xaml}'
P = '{http://schemas.microsoft.com/winfx/2006/xaml/presentation}'

EMPTY_STATES = {'AutoDimension.xaml': 'lst_views', 'DoorThreshold.xaml': 'door_datagrid',
                'PointCloud.xaml': 'results_grid', 'QuickElement.xaml': 'dataGrid',
                'RoomToFloor.xaml': 'room_datagrid', 'TextToElement.xaml': 'dg_preview',
                'TileLayout.xaml': 'floors_listview'}


def parse(name):
    return ET.parse(os.path.join(TOOLS, name)).getroot()


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


class NoBoolIntoVisibility(unittest.TestCase):
    def test_no_null_converter_anywhere(self):
        bad = [f for f in os.listdir(TOOLS) if f.endswith('.xaml') and
               'Converter={x:Null}' in read(os.path.join(TOOLS, f))]
        self.assertEqual(bad, [])

    def test_empty_states_show_only_while_the_list_is_empty(self):
        for fname, list_name in EMPTY_STATES.items():
            root = parse(fname)
            parent = {c: p for p in root.iter() for c in p}
            empties = [t for t in root.iter(P + 'TextBlock')
                       if t.get('Style') == '{StaticResource T3.Empty}'
                       and t.get('Visibility') is None
                       and parent[t].tag == P + 'Grid'
                       and parent[t].find(P + 'Grid.Style') is not None]
            self.assertEqual(len(empties), 1, fname)
            style = parent[empties[0]].find(P + 'Grid.Style')[0]
            default = next(s for s in style.iter(P + 'Setter') if s.get('Property') == 'Visibility')
            self.assertEqual(default.get('Value'), 'Collapsed', fname)
            trigger = next(style.iter(P + 'DataTrigger'))
            self.assertEqual(trigger.get('Binding'),
                             '{Binding HasItems, ElementName=%s}' % list_name, fname)
            self.assertEqual(trigger.get('Value'), 'False', fname)

    def test_model_auditor_detail_buttons_read_visibility_through_a_string(self):
        root = parse('ModelAuditor.xaml')
        buttons = [b for b in root.iter(P + 'Button') if b.get('Content') == 'Detail']
        self.assertEqual(len(buttons), 2)
        for b in buttons:
            self.assertRegex(b.get('Visibility'), r'^\{Binding Text, ElementName=\w+\}$')


class PropertyLineResults(unittest.TestCase):
    """One empty state for the parcel list, its text set per search phase."""

    def test_one_empty_state_beside_the_list(self):
        root = parse('PropertyLine.xaml')
        parent = {c: p for p in root.iter() for c in p}
        lv = next(e for e in root.iter() if e.get(X + 'Name') == 'lv_parcels')
        cell = parent[lv]
        texts = [t for t in cell.iter(P + 'TextBlock')
                 if t.get('Style') == '{StaticResource T3.Empty}']
        self.assertEqual([t.get(X + 'Name') for t in texts], ['txt_no_results'])
        self.assertEqual([c.get(X + 'Name') for c in cell], ['border_no_results', 'lv_parcels'])

    def window(self):
        src = read(os.path.join(GUI, 'PropertyLineDialog.py'))
        tree = ast.parse(src)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and any(isinstance(m, ast.FunctionDef) and m.name == '_on_search_complete'
                           for m in n.body))
        cls.bases = []
        cls.body = [m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name in
                    ('_show_results_message', '_on_search_complete', '_on_search_more',
                     '_on_search_error', '_is_current', '_hide_address_warning')]
        vis = SimpleNamespace(Visible='Visible', Collapsed='Collapsed')
        scope = dict(Visibility=vis, logger=Mock(), ParcelItem=lambda p: p)
        exec(compile(ast.Module(body=[cls], type_ignores=[]), 'PropertyLineDialog.py', 'exec'), scope)
        win = scope[cls.name].__new__(scope[cls.name])
        win._search_seq = 1
        win.btn_search = SimpleNamespace(IsEnabled=False)
        win.txt_address_warning = SimpleNamespace(Visibility='Visible', Text='')
        win.lv_parcels = SimpleNamespace(Visibility='Visible', Items=[])
        win.lv_parcels.Items = SimpleNamespace(Clear=Mock(), Add=Mock())
        win.border_no_results = SimpleNamespace(Visibility='Collapsed')
        win.txt_no_results = SimpleNamespace(Text='')
        win._set_status = Mock()
        return win

    def assert_message(self, win, fragment):
        self.assertEqual(win.lv_parcels.Visibility, 'Collapsed')
        self.assertEqual(win.border_no_results.Visibility, 'Visible')
        self.assertIn(fragment, win.txt_no_results.Text)

    def test_no_result_error_and_found_states(self):
        win = self.window()
        win._on_search_complete([], False, 1)
        self.assert_message(win, 'No property boundary found')
        win._on_search_complete([], True, 1)
        self.assert_message(win, 'Looking for mapped boundaries')
        win._on_search_error('connection refused', 1)
        self.assert_message(win, 'Could not reach the map data service')
        win._on_search_complete([{'display_address': 'x'}], False, 1)
        self.assertEqual(win.lv_parcels.Visibility, 'Visible')
        self.assertEqual(win.border_no_results.Visibility, 'Collapsed')


class TabStripFollowsBGTheme(unittest.TestCase):
    """Tab chips sit on BGTheme's full-width strip, not in a floating pill."""

    def strip(self, fname, chip_name):
        root = parse(fname)
        parent = {c: p for p in root.iter() for c in p}
        el = next(e for e in root.iter() if e.get(X + 'Name') == chip_name)
        while el.tag != P + 'Border':
            el = parent[el]
        return {k: el.get(k) for k in ('Background', 'BorderThickness', 'Padding',
                                         'Style', 'Margin')}

    def test_every_tab_chip_group_sits_on_the_bgtheme_strip(self):
        reference = self.strip('BGTheme.xaml', 'TabModel')
        seen = set()
        for fname in sorted(os.listdir(TOOLS)):
            if not fname.endswith('.xaml'):
                continue
            root = parse(fname)
            for rb in root.iter(P + 'RadioButton'):
                group = rb.get('GroupName') or ''
                if 'T3.Chip' in (rb.get('Style') or '') and group.lower().endswith('tabs') \
                        and (fname, group) not in seen:
                    seen.add((fname, group))
                    self.assertEqual(self.strip(fname, rb.get(X + 'Name')), reference,
                                     '%s %s' % (fname, group))
        self.assertTrue({('BatchLink.xaml', 'bl_tabs'), ('ManaGroup.xaml', 'mg_tabs'),
                         ('SplitElements.xaml', 'se_tabs'),
                         ('LLMSetting.xaml', 'LLMSettingsTabs')} <= seen)

    def test_no_tab_control_shows_default_wpf_headers(self):
        for fname in sorted(os.listdir(TOOLS)):
            if not fname.endswith('.xaml'):
                continue
            for tc in parse(fname).iter(P + 'TabControl'):
                hidden_by_container = any(
                    'T3.TabItem.Hidden' in (st.get('BasedOn') or '')
                    for st in tc.iter(P + 'Style'))
                items = list(tc.findall(P + 'TabItem'))
                hidden_each = items and all('T3.TabItem.Hidden' in (t.get('Style') or '')
                                            for t in items)
                self.assertTrue(hidden_by_container or hidden_each,
                                '%s %s' % (fname, tc.get(X + 'Name')))


def _thickness(value):
    if not value:
        return [0.0] * 4
    p = [float(x) for x in value.replace(' ', ',').split(',') if x]
    return p * 4 if len(p) == 1 else ([p[0], p[1], p[0], p[1]] if len(p) == 2 else p)


def _vertical(value):
    t = _thickness(value)
    return t[1] + t[3]


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ControlsFitTheirBars(unittest.TestCase):
    """Walk up from every sized control to the first fixed-height box around
    it (a Height, a fixed Grid row, or T3.FooterBar/T3.TitleBar) and check the
    control plus every margin, padding and border on the way fits inside."""

    ACTION_H = 30.0                                     # T3.H.Action
    CONTROL_H = {'T3.Button.Primary': ACTION_H, 'T3.Button.Secondary': ACTION_H,
                 'T3.Button.Ghost': ACTION_H, 'T3.Button.Danger': ACTION_H}
    BARS = {'T3.FooterBar': (48.0, '0,1,0,0'), 'T3.TitleBar': (48.0, '0,0,0,1')}

    def test_action_height_token(self):
        styles = read(os.path.join(REPO, 'pyRevit UI Design System', 'T3Lab.Styles.xaml'))
        self.assertIn('<sys:Double x:Key="T3.H.Action">%g</sys:Double>' % self.ACTION_H, styles)

    def fixed_height(self, el, parent):
        h = _number(el.get('Height'))
        key = (el.get('Style') or '').split(' ')[-1].rstrip('}')
        if h is None and key in self.BARS:
            h = self.BARS[key][0]
        par = parent.get(el)
        if h is None and par is not None and par.tag == P + 'Grid':
            rows = par.find(P + 'Grid.RowDefinitions')
            if rows is not None:
                first = int(el.get('Grid.Row', '0'))
                span = [_number(r.get('Height')) for r in
                        list(rows)[first:first + int(el.get('Grid.RowSpan', '1'))]]
                if span and None not in span:
                    h = sum(span) - _vertical(el.get('Margin'))
        return h

    def chrome(self, el):
        if el.tag != P + 'Border':
            return 0.0
        key = (el.get('Style') or '').split(' ')[-1].rstrip('}')
        border = el.get('BorderThickness') or self.BARS.get(key, (0, None))[1]
        return _vertical(el.get('Padding')) + _vertical(border)

    def test_no_control_is_clipped_by_its_bar(self):
        clipped = []
        for fname in sorted(os.listdir(TOOLS)):
            if not fname.endswith('.xaml'):
                continue
            root = parse(fname)
            parent = {c: p for p in root.iter() for c in p}
            for ctl in root.iter():
                if ctl.tag not in (P + 'Button', P + 'ToggleButton', P + 'TextBox', P + 'ComboBox'):
                    continue
                style = (ctl.get('Style') or '').split(' ')[-1].rstrip('}')
                need = _number(ctl.get('Height')) or self.CONTROL_H.get(style)
                if need is None:
                    continue
                need += _vertical(ctl.get('Margin'))
                el = parent.get(ctl)
                while el is not None and el.tag not in (P + 'ScrollViewer', P + 'DataTemplate',
                                                        P + 'ControlTemplate'):
                    need += self.chrome(el)
                    h = self.fixed_height(el, parent)
                    if h is not None:
                        if need > h + 0.01:
                            clipped.append('%s %s needs %g, bar is %g' % (
                                fname, ctl.get(X + 'Name') or ctl.get('Content'), need, h))
                        break
                    need += _vertical(el.get('Margin'))
                    el = parent.get(el)
        self.assertEqual(clipped, [])


if __name__ == '__main__':
    unittest.main()
