# -*- coding: utf-8 -*-
"""dev/audit_ribbon.py catches each ribbon rule on a fake ribbon tree.

Run: python dev/test_audit_ribbon.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audit_ribbon  # noqa: E402

GOOD_PANEL = 'title: "%s"\nbackground:\n  title: "#46E07B00"\nlayout:\n%s'


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def _button(folder, name):
    _write(os.path.join(folder, name, 'bundle.yaml'), 'title: "%s"\n' % name.split('.')[0])


def _panel(tab, title, items, layout=None, color='#46E07B00'):
    folder = os.path.join(tab, title + '.panel')
    for item in items:
        if item.endswith('.stack'):
            stack = os.path.join(folder, item)
            for i in range(3):
                _button(stack, '%s%d.pushbutton' % (item.split('.')[0], i))
        else:
            _button(folder, item)
    names = layout if layout is not None else [i.split('.')[0] for i in items]
    text = GOOD_PANEL % (title, ''.join('  - %s\n' % n for n in names))
    _write(os.path.join(folder, 'bundle.yaml'), text.replace('#46E07B00', color))
    return folder


def _tab(root, name, panels):
    tab = os.path.join(root, name + '.tab')
    _write(os.path.join(tab, 'bundle.yaml'),
           'layout:\n' + ''.join('  - %s\n' % p for p in panels))
    return tab


class AuditRibbon(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.tab = _tab(self.root, 'T', ['Good'])
        _panel(self.tab, 'Good', ['A.pushbutton', 'B.pushbutton', 'S.stack'])

    def tearDown(self):
        self._tmp.cleanup()

    def rules(self):
        return sorted(set(r for r, _w, _m in audit_ribbon.Audit([self.tab]).run().issues))

    def test_clean_tree_passes(self):
        self.assertEqual(self.rules(), [])

    def test_p1_panel_slot_count(self):
        _panel(self.tab, 'Small', ['Only.pushbutton'])
        _tab(self.root, 'T', ['Good', 'Small'])
        self.assertEqual(self.rules(), ['P1'])

    def test_p2_large_button_after_stack(self):
        _panel(self.tab, 'Late', ['Q.stack', 'X.pushbutton'])
        _tab(self.root, 'T', ['Good', 'Late'])
        self.assertEqual(self.rules(), ['P2'])

    def test_p3_stack_size(self):
        folder = _panel(self.tab, 'Wide', ['C.pushbutton', 'W.stack'])
        _button(os.path.join(folder, 'W.stack'), 'Extra.pushbutton')
        _tab(self.root, 'T', ['Good', 'Wide'])
        self.assertEqual(self.rules(), ['P3'])

    def test_p4_tab_budget(self):
        names = []
        for i in range(5):
            _panel(self.tab, 'Wide%d' % i, ['%s%d.pushbutton' % (c, i) for c in 'UVW'] + ['X%d.stack' % i])
            names.append('Wide%d' % i)
        _tab(self.root, 'T', ['Good'] + names)
        self.assertEqual(self.rules(), ['P4'])

    def test_p5_panel_colour(self):
        _panel(self.tab, 'Red', ['R1.pushbutton', 'R2.pushbutton'], color='#FF0000')
        _tab(self.root, 'T', ['Good', 'Red'])
        self.assertEqual(self.rules(), ['P5'])

    def test_p6_title_case_and_duplicates(self):
        _panel(self.tab, 'lower case', ['L1.pushbutton', 'L2.pushbutton'])
        _tab(self.root, 'T', ['Good', 'lower case'])
        self.assertEqual(self.rules(), ['P6'])

    def test_p7_duplicate_bundle_name(self):
        _panel(self.tab, 'Copy', ['A.pushbutton', 'Z.pushbutton'])
        _tab(self.root, 'T', ['Good', 'Copy'])
        self.assertEqual(self.rules(), ['P7'])

    def test_p10_folder_missing_from_layout_is_hidden(self):
        _panel(self.tab, 'Hidden', ['H1.pushbutton', 'H2.pushbutton', 'H3.pushbutton'],
               layout=['H1', 'H2'])
        _tab(self.root, 'T', ['Good', 'Hidden'])
        self.assertEqual(self.rules(), ['P10'])

    def test_p10_ghost_layout_entry(self):
        _panel(self.tab, 'Ghost', ['G1.pushbutton', 'G2.pushbutton'], layout=['G1', 'G2', 'Gone'])
        _tab(self.root, 'T', ['Good', 'Ghost'])
        self.assertEqual(self.rules(), ['P10'])

    def test_layout_directive_and_separator_are_understood(self):
        _panel(self.tab, 'Fancy', ['F1.pushbutton', 'F2.pushbutton'],
               layout=['F1[title:First]', '---', 'F2'])
        _tab(self.root, 'T', ['Good', 'Fancy'])
        self.assertEqual(self.rules(), [])

    def test_real_ribbon_is_measured(self):
        audit = audit_ribbon.Audit().run()
        self.assertEqual(len(audit.tabs), len(audit_ribbon.TABS))
        self.assertTrue(all(width > 0 for _t, width, _s, _p in audit.tabs))


if __name__ == '__main__':
    unittest.main()
