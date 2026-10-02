# -*- coding: utf-8 -*-
"""Project length units in Tile Layout, Sheet Gen, Make Pattern, Image to
Drafting, FamiGen, ManaLoca and ManaStyles (Snippets/_units.py).

Each tool must label its length fields from the project unit and read what the
user types through the helper (bare number = project unit, "600 mm" / "2'" /
"3'-6\"" always work, bad text → a message, nothing runs). Paper sizes (sheet
header strip, drafting pattern, line-pattern dashes) use the paper unit: mm in
a metric project, inches in an imperial one.

The dialogs need Revit/WPF, so the tested definitions are lifted out of the
shipped source with `ast` and run against plain fakes. Run:
    python3 dev/test_units_design_tools.py
"""
import ast
import io
import os
import re
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
GUI = os.path.join(LIB, 'GUI')
TOOLS = os.path.join(GUI, 'Tools')
sys.path.insert(0, LIB)

from Snippets import _units as U  # noqa: E402  (pure Python)
from GUI import TileLayoutCore as TC  # noqa: E402  (stdlib only)

MM = 1.0 / 304.8
IN = 1.0 / 12.0
METRIC = U.LengthUnit('millimeters')
METRES = U.LengthUnit('meters')
FTIN = U.LengthUnit('feetFractionalInches')


def _source(name):
    with io.open(os.path.join(GUI, name), encoding='utf-8') as fh:
        return fh.read()


def _xaml(name):
    with io.open(os.path.join(TOOLS, name), encoding='utf-8-sig') as fh:
        return fh.read()


def _lift(module, names, scope):
    """Exec the module-level defs / assignments called `names` into `scope`."""
    tree = ast.parse(_source(module))
    body = [n for n in tree.body
            if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names)
            or (isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in n.targets))]
    found = {getattr(n, 'name', None) for n in body} | {
        t.id for n in body if isinstance(n, ast.Assign) for t in n.targets
        if isinstance(t, ast.Name)}
    missing = set(names) - found
    assert not missing, '%s: not found %s' % (module, sorted(missing))
    exec(compile(ast.Module(body=body, type_ignores=[]), module, 'exec'), scope)
    return scope


def _bare(cls, **attrs):
    """An instance without running __init__ (it needs WPF)."""
    obj = object.__new__(cls)
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


def _named(xaml, name):
    return re.search(r'x:Name="%s"' % re.escape(name), _xaml(xaml)) is not None


class _Items(list):
    """ItemCollection stand-in: .Count + indexing."""
    @property
    def Count(self):
        return len(self)


# ═════════════════════════════════════════════════════════════════════════════
class TileLayout(unittest.TestCase):
    def test_runtime_labels_are_named(self):
        for name in ('lbl_tile_w', 'lbl_tile_h', 'lbl_joint', 'col_dimensions'):
            self.assertTrue(_named('TileLayout.xaml', name), name)

    def _window(self, unit, w='600', h='600', joint='3'):
        scope = _lift('TileLayoutDialog.py', ['TileLayoutWindow'],
                      {'T3WPFWindow': object})
        cls = scope['TileLayoutWindow']
        return _bare(cls, _unit=unit,
                     txt_tile_w=NS(Text=w), txt_tile_h=NS(Text=h), txt_joint=NS(Text=joint),
                     chk_nesting=NS(IsChecked=True),
                     lbl_tile_w=NS(Text=''), lbl_tile_h=NS(Text=''), lbl_joint=NS(Text=''),
                     col_dimensions=NS(Header=''))

    def test_labels_and_defaults_follow_the_project_unit(self):
        win = self._window(FTIN)
        win._apply_units()
        self.assertEqual(win.lbl_tile_w.Text, 'Width (ft-in):')
        self.assertEqual(win.lbl_joint.Text, 'Joint / Grout (ft-in):')
        self.assertEqual(win.col_dimensions.Header, 'DIMENSIONS (FT-IN)')
        self.assertEqual(win.txt_tile_w.Text, FTIN.default_text(600))
        win = self._window(METRIC)
        win._apply_units()
        self.assertEqual((win.lbl_tile_h.Text, win.txt_tile_h.Text, win.txt_joint.Text),
                         ('Height (mm):', '600', '3'))

    def test_tile_size_is_read_through_the_helper(self):
        p = self._window(FTIN, w="2'", h='600 mm', joint='1/8"')._read_params()
        self.assertAlmostEqual(p['tile_w_ft'], 2.0)
        self.assertAlmostEqual(p['tile_h_ft'], 600 * MM)
        self.assertAlmostEqual(p['joint_ft'], 0.125 * IN)
        p = self._window(METRES, w='0.6', h='0,3', joint='3 mm')._read_params()
        self.assertAlmostEqual(p['tile_h_ft'], 300 * MM)

    def test_bad_or_zero_size_names_the_field(self):
        with self.assertRaisesRegex(ValueError, '^Tile Width: .*not a length'):
            self._window(METRIC, w='abc')._read_params()
        with self.assertRaisesRegex(ValueError, 'Joint Width must be greater than zero'):
            self._window(METRIC, joint='0')._read_params()

    def test_engine_labels_use_the_unit(self):
        self.assertEqual(TC.size_text(600 * MM, 300 * MM, METRIC), u'600 × 300 mm')
        self.assertEqual(TC.size_text(600 * MM, 300 * MM, None), u'600 × 300 mm')
        self.assertEqual(TC.shift_text(150 * MM, 0.0, None), '+150/+0 mm')
        self.assertEqual(TC.shift_text(150 * MM, -20 * MM, METRES), '+0.15/-0.02 m')
        self.assertIn(' / ', TC.shift_text(6 * IN, -0.75 * IN, FTIN))
        self.assertEqual(TC.length_text(1.5 * MM, METRIC, extra=1), '1.5 mm')
        self.assertEqual(TC.length_text(50 * MM, METRIC), '50 mm')
        self.assertTrue(TC.variant_desc(0.5, 0.0, 45.0, FTIN).startswith("shift +0' - 6\""))

    def test_generated_options_carry_the_unit_into_their_labels(self):
        sq = [TC.V2(0, 0), TC.V2(10, 0), TC.V2(10, 10), TC.V2(0, 10)]
        floor = NS(pts=sq)
        for unit, tag in ((None, ' mm,'), (METRES, ' m,'), (FTIN, '"')):
            gen = TC.OptionGenerator(2.0, 2.0, 0.01, False, top_n=2, unit=unit)
            opt = gen.build_variant(floor, 'grid', 0.0, 0.5, 0.25)
            self.assertIn(tag, opt.variant, unit)
            self.assertTrue(opt.regenerate(dx=1.0))
            self.assertIn(tag, opt.variant, unit)


# ═════════════════════════════════════════════════════════════════════════════
class SheetGen(unittest.TestCase):
    def setUp(self):
        scope = {'T3WPFWindow': object, 'math': __import__('math'),
                 'project_length_unit': U.project_length_unit,
                 'paper_unit': U.paper_unit, 'MILLIMETERS': U.MILLIMETERS}
        _lift('SheetGenDialog.py',
              ['DEFAULT_OFFSET_MM', 'DEFAULT_STRIP_MM', 'CreateRoomPlanWindow'], scope)
        self.cls = scope['CreateRoomPlanWindow']

    def _window(self, unit):
        return _bare(self.cls, _unit=unit, _paper=U.paper_unit(unit),
                     lbl_offset=NS(Text=''), txt_offset=NS(Text=''),
                     lbl_strip_size=NS(Text=''), txt_strip_mm=NS(Text=''),
                     cmb_strip_side=NS(SelectedIndex=0))

    def test_runtime_labels_are_named(self):
        self.assertTrue(_named('SheetGen.xaml', 'lbl_offset'))
        self.assertTrue(_named('SheetGen.xaml', 'lbl_strip_size'))

    def test_offset_is_model_unit_and_header_is_paper_unit(self):
        win = self._window(FTIN)
        win._apply_units()
        self.assertEqual(win.lbl_offset.Text, 'OFFSET (FT-IN)')
        self.assertEqual(win.lbl_strip_size.Text, 'HEADER SIZE (IN)')   # never ft
        self.assertEqual(win.txt_offset.Text, FTIN.default_text(1000))
        win = self._window(METRES)
        win._apply_units()
        self.assertEqual((win.lbl_offset.Text, win.txt_offset.Text), ('OFFSET (M)', '1'))
        self.assertEqual((win.lbl_strip_size.Text, win.txt_strip_mm.Text),
                         ('HEADER SIZE (MM)', '70'))           # paper: mm, not m

    def test_values_are_parsed_in_their_unit(self):
        win = self._window(FTIN)
        win.txt_offset.Text = "3'-6\""
        self.assertAlmostEqual(win._get_offset(), 3.5)
        win.txt_strip_mm.Text = '2 3/4'               # bare number on paper = inches
        self.assertAlmostEqual(win._get_strip_config()[1], 2.75 * IN)
        win.txt_strip_mm.Text = '70 mm'
        self.assertAlmostEqual(win._get_strip_config()[1], 70 * MM)
        win.txt_offset.Text = 'abc'                    # mockup falls back to 1 m
        self.assertAlmostEqual(win._get_offset(), 1000 * MM)

    def test_paper_label_uses_the_paper_unit(self):
        a1 = (841 * MM, 594 * MM)
        self.assertEqual(self.cls._paper_label(*a1), 'A1  (841 x 594 mm)')
        self.assertEqual(self.cls._paper_label(*a1, paper=U.paper_unit(METRES)),
                         'A1  (841 x 594 mm)')
        arch_d = (36 * IN, 24 * IN)
        self.assertEqual(self.cls._paper_label(*arch_d, paper=U.paper_unit(FTIN)),
                         '36" x 24"')


# ═════════════════════════════════════════════════════════════════════════════
class MakePattern(unittest.TestCase):
    def setUp(self):
        scope = {'T3WPFWindow': object, 'MILLIMETERS': U.MILLIMETERS}
        _lift('MakePatternDialog.py', ['PRESETS', 'GRID_STEPS', 'MIN_MODULE_MM',
                                       'length_label', 'size_label', 'MakePatternDialog'],
              scope)
        self.scope = scope

    def _dialog(self, model_unit, drafting=False):
        presets = _Items([NS(Content='Custom size')] +
                         [NS(Content='') for _ in self.scope['PRESETS']])
        dlg = _bare(self.scope['MakePatternDialog'],
                    _model_unit=model_unit, _paper_unit=U.paper_unit(model_unit),
                    _shown_dims=(None, None), mod_w=600.0, mod_h=300.0,
                    rb_type_model=NS(IsChecked=not drafting, Content=''),
                    rb_type_drafting=NS(IsChecked=drafting),
                    lbl_module_size=NS(Text=''), txt_mod_w=NS(Text=''), txt_mod_h=NS(Text=''),
                    cmb_presets=NS(Items=presets),
                    cmb_grid_step=NS(Items=_Items(NS(Content='') for _ in range(4))))
        dlg._apply_units()
        return dlg

    def test_model_pattern_uses_the_project_unit(self):
        dlg = self._dialog(FTIN)
        self.assertEqual(dlg.lbl_module_size.Text, 'MODULE SIZE (FT-IN)')
        self.assertEqual(dlg.rb_type_model.Content, 'Model (ft-in)')
        self.assertEqual(dlg.txt_mod_w.Text, FTIN.default_text(600))
        self.assertTrue(dlg.cmb_presets.Items[1].Content.endswith('(Running Bond)'))
        self.assertNotIn('mm', dlg.cmb_presets.Items[1].Content)
        self.assertEqual(self._dialog(METRIC).cmb_presets.Items[1].Content,
                         '600 x 300 mm (Running Bond)')
        self.assertEqual(self._dialog(METRIC).cmb_grid_step.Items[2].Content, '50 mm')

    def test_drafting_pattern_uses_the_paper_unit(self):
        dlg = self._dialog(FTIN, drafting=True)
        self.assertEqual(dlg.lbl_module_size.Text, 'MODULE SIZE (IN)')
        dlg = self._dialog(METRES, drafting=True)
        self.assertEqual(dlg.lbl_module_size.Text, 'MODULE SIZE (MM)')   # paper, not m
        self.assertEqual(dlg.txt_mod_w.Text, '600')

    def test_typed_size_goes_through_the_helper(self):
        dlg = self._dialog(METRES)
        dlg.txt_mod_w.Text, dlg.txt_mod_h.Text = '1.2', '300 mm'
        self.assertEqual([round(v, 6) for v in dlg._parse_dims()], [1200.0, 300.0])
        dlg.txt_mod_w.Text = 'wide'
        with self.assertRaisesRegex(ValueError, '^Module width: .*not a length'):
            dlg._parse_dims()

    def test_untouched_box_keeps_the_exact_value(self):
        """A box shows a rounded value; leaving it alone must not round the module."""
        dlg = self._dialog(METRIC)
        dlg.mod_w = 1234.5
        dlg._sync_inputs_from_state()
        self.assertEqual(dlg.txt_mod_w.Text, '1234')       # rounded for display
        self.assertEqual(dlg._parse_dims()[0], 1234.5)     # value kept


# ═════════════════════════════════════════════════════════════════════════════
class ImageToDrafting(unittest.TestCase):
    def setUp(self):
        self.alert = Mock()
        scope = {'T3WPFWindow': object, 'ProgressPauseMixin': object,
                 'forms': NS(alert=self.alert)}
        _lift('ImageToDraftingDialog.py', ['DEFAULT_WIDTH_MM', 'ImageToDraftingWindow'], scope)
        self.cls = scope['ImageToDraftingWindow']

    def _click(self, unit, width):
        win = _bare(self.cls, _unit=unit, ViewNameInput=NS(Text='Logo'),
                    WidthInput=NS(Text=width),
                    ThicknessInput=NS(Text='stop-here'))   # next check after width
        win.Create_Click(None, None)
        return self.alert.call_args.args[0]

    def test_label_is_named(self):
        self.assertTrue(_named('ImageToDrafting.xaml', 'lbl_output_width'))
        src = _source('ImageToDraftingDialog.py')
        self.assertIn('self._unit.label("OUTPUT WIDTH")', src)

    def test_width_is_parsed_in_the_project_unit(self):
        # Width accepted → the dialog moves on to the thickness check.
        self.assertIn('Thickness', self._click(FTIN, "1'"))
        self.assertIn('Thickness', self._click(METRIC, '300'))
        self.assertIn('Thickness', self._click(METRES, '300 mm'))

    def test_bad_width_says_what_is_accepted(self):
        self.assertTrue(self._click(METRIC, 'wide').startswith('Output Width: '))
        self.assertIn('greater than zero', self._click(METRIC, '0'))


# ═════════════════════════════════════════════════════════════════════════════
class FamiGen(unittest.TestCase):
    def setUp(self):
        class Arc(object):
            pass
        scope = {'MILLIMETERS': U.MILLIMETERS, 'Arc': Arc, 'T3WPFWindow': object,
                 'get_xy_bounds': lambda curves: (0.0, 3.0, 0.0, 2.0),   # ft
                 '_suggest_category': lambda *a, **k: 'Generic Model'}
        _lift('FamiGenDialog.py', ['BlockItem', 'FamilyCreatorDialog'], scope)
        self.scope = scope

    def test_block_columns_are_named(self):
        self.assertTrue(_named('FamiGen.xaml', 'col_block_width'))
        self.assertTrue(_named('FamiGen.xaml', 'col_block_depth'))
        self.assertIn('Binding="{Binding WidthText}"', _xaml('FamiGen.xaml'))

    def test_block_size_in_the_document_unit_name_stays_mm(self):
        item = self.scope['BlockItem']('Desk', 4, 1, [], unit=FTIN)
        self.assertEqual((item.WidthText, item.DepthText), ("3' - 0\"", "2' - 0\""))
        self.assertEqual((item.WidthMM, item.DepthMM), ('914', '610'))   # .rfa name
        item = self.scope['BlockItem']('Desk', 4, 1, [])
        self.assertEqual(item.WidthText, '914')

    def test_summary_size_uses_the_unit(self):
        self.scope['schema_summary'] = lambda s: {
            'family_name': 'Table', 'family_category': 'Furniture',
            'solids': 1, 'voids': 0, 'materials': [], 'parameters': 0}
        dlg = _bare(self.scope['FamilyCreatorDialog'], _unit=METRES,
                    preview_summary=NS(Text=''))
        model = NS(size_mm=lambda: (2400.0, 1200.0, 750.0))
        dlg._fill_summary({'x': 1}, model, [])
        self.assertIn(u'Size 2.4 × 1.2 × 0.75 m (W', dlg.preview_summary.Text)
        dlg._unit = METRIC
        dlg._fill_summary({'x': 1}, model, [])
        self.assertIn(u'Size 2400 × 1200 × 750 mm', dlg.preview_summary.Text)


# ═════════════════════════════════════════════════════════════════════════════
class ManaLoca(unittest.TestCase):
    def setUp(self):
        scope = {'MILLIMETERS': U.MILLIMETERS, 'paper_unit': U.paper_unit}
        _lift('ManaLocaDialog.py', ['ODD_TOL_MM', 'EDIT_TOL_MM', 'METRIC_STEPS_MM',
                                    'IMPERIAL_STEPS_MM', 'unit_steps', 'step_text',
                                    'format_coord', 'parse_coord', 'is_odd',
                                    'ElementData'], scope)
        self.s = scope

    def _row(self, unit, x_mm):
        return _bare(self.s['ElementData'], _unit=unit,
                     _odd_step=self.s['unit_steps'](unit)[0], parse_error=None,
                     x=x_mm, y=0.0, z=0.0, _orig_mm=(x_mm, 0.0, 0.0))

    def test_columns_are_named_and_bindings_unchanged(self):
        src = _xaml('ManaLoca.xaml')
        for axis in 'xyz':
            self.assertTrue(_named('ManaLoca.xaml', 'col_' + axis))
            self.assertIn('Binding="{Binding %s_mm, Mode=TwoWay}"' % axis, src)

    def test_metric_display_is_unchanged(self):
        self.assertEqual(self._row(METRIC, 25619.4021).x_mm, '25619.40')
        self.assertEqual(self._row(METRES, 25619.4021).x_mm, '25.61940')
        self.assertEqual(self.s['step_text'](METRES, 5.0), '5 mm')

    def test_imperial_display_and_steps(self):
        row = self._row(FTIN, 3.5 * 304.8)
        self.assertEqual(row.x_mm, "3' - 6\"")
        self.assertEqual(self.s['step_text'](FTIN, self.s['unit_steps'](FTIN)[1]), '1/2"')
        self.assertFalse(row.odd_x_mm)                      # on the 1/8" step
        self.assertTrue(self._row(FTIN, 1000.0).odd_x_mm)   # 1000 mm is off it
        self.assertTrue(self._row(METRIC, 1000.4).odd_x_mm)
        self.assertFalse(self._row(METRIC, 1000.0).odd_x_mm)

    def test_typed_coordinate_uses_the_helper(self):
        row = self._row(FTIN, 0.0)
        row.x_mm = "10'-6\""
        self.assertAlmostEqual(row.x, 10.5 * 304.8)
        row.x_mm = '-0\' - 2"'                               # negative under a foot
        self.assertAlmostEqual(row.x, -2 * 25.4)
        row.x_mm = '1200 mm'
        self.assertAlmostEqual(row.x, 1200.0)
        self.assertTrue(row.dirty_x_mm)

    def test_writing_back_the_shown_text_does_not_move_the_element(self):
        row = self._row(FTIN, 1000.0)                        # shows 3' - 3 3/8"
        row.x_mm = row.x_mm
        self.assertEqual(row.x, 1000.0)
        self.assertFalse(row.dirty_x_mm)

    def test_bad_text_keeps_the_value_and_records_why(self):
        row = self._row(METRIC, 500.0)
        row.x_mm = 'north'
        self.assertEqual(row.x, 500.0)
        self.assertIn('not a length', row.parse_error)


# ═════════════════════════════════════════════════════════════════════════════
class ManaStyles(unittest.TestCase):
    def test_line_pattern_dashes_use_the_paper_unit(self):
        scope = _lift('ManaStylesDialog.py', ['segment_text'],
                      {'MILLIMETERS': U.MILLIMETERS})
        seg = scope['segment_text']
        self.assertEqual(seg(3 * MM), '3.00 mm')
        self.assertEqual(seg(3 * MM, U.paper_unit(METRES)), '3.00 mm')   # never m
        self.assertEqual(seg(0.125 * IN, U.paper_unit(FTIN)), '0.125"')
        self.assertIn('project_paper_unit(doc)', _source('ManaStylesDialog.py'))


# ═════════════════════════════════════════════════════════════════════════════
class NoHardCodedMillimetres(unittest.TestCase):
    """User-facing lengths in these dialogs go through the helper."""
    FILES = ('TileLayoutDialog.py', 'SheetGenDialog.py', 'MakePatternDialog.py',
             'ImageToDraftingDialog.py', 'FamiGenDialog.py', 'ManaLocaDialog.py')

    def test_no_bare_mm_conversion_left(self):
        for name in self.FILES:
            for i, line in enumerate(_source(name).splitlines(), 1):
                code = line.split('#', 1)[0]
                if re.search(r'[*/]\s*304\.8\b', code) and 'logger.' not in code \
                        and 'SCL' not in code:
                    self.fail('%s:%d converts mm by hand: %s' % (name, i, line.strip()))

    def test_xaml_labels_no_longer_fixed_in_mm(self):
        """The (MM) labels still exist as XAML defaults, but each is named
        so Python can rewrite it from the project unit."""
        checks = {'TileLayout.xaml': ('Width (mm):', 'lbl_tile_w'),
                  'SheetGen.xaml': ('OFFSET (M)', 'lbl_offset'),
                  'MakePattern.xaml': ('MODULE SIZE (MM)', 'lbl_module_size'),
                  'ImageToDrafting.xaml': ('OUTPUT WIDTH (MM)', 'lbl_output_width')}
        for xaml, (text, name) in checks.items():
            line = [l for l in _xaml(xaml).splitlines() if 'Text="%s"' % text in l]
            self.assertEqual(len(line), 1, xaml)
            self.assertIn('x:Name="%s"' % name, line[0], xaml)


if __name__ == '__main__':
    unittest.main()
