# -*- coding: utf-8 -*-
"""Model-length tools follow the PROJECT's length unit (Snippets/_units.py).

Room to Floor, Door Threshold, Wall Adjust Base, Wall Cut Profile, Auto
Dimension, Point Cloud to Model and Text to Element used to say "(mm)", start
at mm defaults and read `float(text) / 304.8` — in a feet-inches project the
label lied and a typed "6" became 6 mm. Each dialog's shipped code runs here
against fakes (no Revit, WPF or pyRevit), with the real `_units` helper reading
a fake document's Project Units:

* the label / unit text comes from the project unit, defaults show in it;
* what the user types goes through LengthUnit.parse (bare number = project
  unit, "1200 mm" / 3'-6" always work);
* a value that is not a length stops the tool with the helper's message — it
  is never silently replaced by a default.

Every control a dialog touches must exist as an x:Name in its XAML (the fake
window only hands out those), so a renamed label fails here, not in Revit.

Run: python3 dev/test_units_model_tools.py
"""
import ast
import math
import os
import re
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
GUI = os.path.join(LIB, 'GUI')
TOOLS = os.path.join(GUI, 'Tools')
sys.path.insert(0, LIB)

# project_length_unit() imports SpecTypeId from the Revit API. Give it one so
# the shipped code path reads the fake document's Project Units › Length.
_autodesk = sys.modules.setdefault('Autodesk', types.ModuleType('Autodesk'))
_revit = sys.modules.setdefault('Autodesk.Revit', types.ModuleType('Autodesk.Revit'))
_db = types.ModuleType('Autodesk.Revit.DB')
_db.SpecTypeId = SimpleNamespace(Length='autodesk.spec.aec:length-2.0.0')
sys.modules['Autodesk.Revit.DB'] = _db
_autodesk.Revit = _revit
_revit.DB = _db

from Snippets import _units as U  # noqa: E402
from Services import point_cloud_analysis as PCA  # noqa: E402

FT = 1.0
IN = 1.0 / 12.0
MM = 1.0 / 304.8
X = '{http://schemas.microsoft.com/winfx/2006/xaml}'


# ───────────────────────────── fake Revit / WPF ─────────────────────────────
class FakeDoc(object):
    """A document whose Project Units › Length is `unit_key`."""

    def __init__(self, unit_key, elements=None):
        self.unit_key = unit_key
        self.elements = elements or {}

    def GetUnits(self):
        key = self.unit_key

        class _Opt(object):
            def GetUnitTypeId(self):
                return SimpleNamespace(TypeId='autodesk.unit.unit:{}-1.0.1'.format(key))

        return SimpleNamespace(GetFormatOptions=lambda spec: _Opt())


METRIC = 'millimeters'
METERS = 'meters'
IMPERIAL = 'feetFractionalInches'


class FakeCollector(object):
    def __init__(self, doc, *view):
        self.doc = doc
        self.items = []

    def OfClass(self, cls):
        self.items = list(getattr(self.doc, 'elements', {}).get(cls.__name__, []))
        return self

    def OfCategory(self, cat):
        return self

    def WhereElementIsNotElementType(self):
        return self

    def WhereElementIsElementType(self):
        return self

    def ToElements(self):
        return list(self.items)

    def ToElementIds(self):
        return []

    def __iter__(self):
        return iter(self.items)


class Messages(list):
    """TaskDialog / pyrevit.forms stand-in: records every message shown."""

    def Show(self, title, message, *args):
        self.append(message)

    def alert(self, message, title=None, **kwargs):
        self.append(message)


class AnyName(object):
    """BuiltInCategory / BuiltInParameter stand-in: any member is its name."""

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return name


class Event(object):
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class Items(list):
    def Add(self, item):
        self.append(item)

    def Clear(self):
        del self[:]

    @property
    def Count(self):
        return len(self)

    def Refresh(self):
        pass


class Control(object):
    def __init__(self, attrs):
        self.Text = attrs.get('Text', '')
        self.IsChecked = attrs.get('IsChecked') == 'True'
        self.IsEnabled = attrs.get('IsEnabled', 'True') == 'True'
        self.Visibility = attrs.get('Visibility', 'Visible')
        self.ToolTip = attrs.get('ToolTip')
        self.Content = attrs.get('Content')
        self.SelectedIndex = -1
        self.SelectedItem = None
        self.SelectedItems = []
        self.ItemsSource = None
        self.Items = Items()
        self.Foreground = None
        for name in ('Click', 'Checked', 'SelectionChanged', 'TextChanged'):
            setattr(self, name, Event())


def xaml_controls(xaml_file):
    """x:Name → the attributes the XAML gives it."""
    root = ET.parse(os.path.join(TOOLS, xaml_file)).getroot()
    return {el.get(X + 'Name'): dict(el.attrib) for el in root.iter()
            if el.get(X + 'Name')}


def fake_window(xaml_file):
    """T3WPFWindow stand-in that only knows the controls the XAML names."""
    names = xaml_controls(xaml_file)

    class FakeWindow(object):
        XAML = xaml_file

        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            if name in names:
                ctl = Control(names[name])
                object.__setattr__(self, name, ctl)
                return ctl
            raise AttributeError('{} has no x:Name "{}"'.format(xaml_file, name))

        # T3WPFWindow services the dialogs call
        def begin_progress(self, maximum=100, disable=None):
            pass

        def step_progress(self, value, message=None):
            return True

        def end_progress(self):
            pass

        @property
        def is_cancelled(self):
            return False

        def sync_header_checkbox(self, *a):
            pass

        def init_ai_badge(self):
            pass

        def Close(self):
            self.closed = True

    return FakeWindow


def load(dialog_file, xaml_file, **scope):
    """Exec the dialog's top-level defs (and the constants that evaluate
    without Revit) with Revit/WPF/pyRevit names replaced by fakes."""
    path = os.path.join(GUI, dialog_file)
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read(), filename=path)
    messages = Messages()
    ns = {
        '__name__': 'dialog_under_test', '__file__': path,
        'math': math, 'os': os, 'sys': sys,
        'project_length_unit': U.project_length_unit, 'paper_unit': U.paper_unit,
        'LengthUnit': U.LengthUnit, 'MILLIMETERS': U.MILLIMETERS,
        'T3WPFWindow': fake_window(xaml_file),
        'TaskDialog': messages, 'forms': messages, 'MESSAGES': messages,
        'FilteredElementCollector': FakeCollector,
        'BuiltInCategory': AnyName(), 'BuiltInParameter': AnyName(),
        'to_items_source': list,
        'set_items_source': lambda ctl, items: setattr(ctl, 'ItemsSource', list(items)),
        'Visibility': SimpleNamespace(Visible='Visible', Collapsed='Collapsed'),
        'System': SimpleNamespace(Windows=SimpleNamespace(
            Visibility=SimpleNamespace(Visible='Visible', Collapsed='Collapsed'),
            WindowState=SimpleNamespace(Minimized=1, Normal=0, Maximized=2))),
        'logger': SimpleNamespace(debug=print, error=print, warning=print),
        'REVIT_VERSION': 2025, '_NS_SUFFIX': 'test', 'DB': None,
        'ft_to_mm': PCA.ft_to_mm, 'mm_to_ft': PCA.mm_to_ft,
    }
    ns.update(scope)
    # Revit classes the dialog imports (OfClass(Level), isinstance(x, Wall)…)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or '').startswith('Autodesk'):
            for alias in node.names:
                name = alias.asname or alias.name
                ns.setdefault(name, type(name, (object,), {}))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                for name in ast.walk(base):
                    if isinstance(name, ast.Name):
                        ns.setdefault(name.id, type(name.id, (object,), {}))
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), ns)
        elif isinstance(node, ast.Assign):
            try:
                exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), ns)
            except Exception:
                pass    # needs Revit (logger, REVIT_VERSION, …) — faked above
    return ns


class Recorder(object):
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return self.result


# ─────────────────────────────── the tools ──────────────────────────────────
class RoomToFloor(unittest.TestCase):
    def open(self, unit_key):
        ns = load('RoomToFloorDialog.py', 'RoomToFloor.xaml')
        win = ns['RoomToFloorWindow'](FakeDoc(unit_key), None)
        win._all_rooms = [SimpleNamespace(IsSelected=True, Element='room')]
        win._floor_type_map = {'Floor: Finish': 'floor_type'}
        win.cmb_floor_type.SelectedItem = 'Floor: Finish'
        win.generator = SimpleNamespace(generate_floors=Recorder(([], 1, 0)))
        return ns, win

    def test_label_and_default_follow_the_project_unit(self):
        _, win = self.open(IMPERIAL)
        self.assertEqual(win.lbl_offset.Text, 'Height Offset (ft-in):')
        self.assertEqual(win.txt_offset.Text, '0\' - 0"')
        _, win = self.open(METRIC)
        self.assertEqual(win.lbl_offset.Text, 'Height Offset (mm):')
        self.assertEqual(win.txt_offset.Text, '0')

    def test_typed_offset_goes_through_the_helper(self):
        _, win = self.open(IMPERIAL)
        win.txt_offset.Text = '1\'-6"'
        win.create_floors_clicked(None, None)
        offset_mm = win.generator.generate_floors.calls[0][2]
        self.assertAlmostEqual(offset_mm, 457.2, places=6)
        _, win = self.open(METRIC)
        win.txt_offset.Text = '150'
        win.create_floors_clicked(None, None)
        self.assertAlmostEqual(win.generator.generate_floors.calls[0][2], 150.0, places=6)

    def test_offset_below_level_under_one_foot_stays_negative(self):
        _, win = self.open(IMPERIAL)
        win.txt_offset.Text = '-0\' - 2"'
        win.create_floors_clicked(None, None)
        self.assertAlmostEqual(win.generator.generate_floors.calls[0][2], -50.8, places=6)

    def test_bad_offset_stops_with_the_reason(self):
        ns, win = self.open(IMPERIAL)
        win.txt_offset.Text = 'abc'
        win.create_floors_clicked(None, None)
        self.assertEqual(win.generator.generate_floors.calls, [])
        self.assertIn('Height Offset', ns['MESSAGES'][-1])
        self.assertIn('ft-in', ns['MESSAGES'][-1])
        self.assertIn('Height Offset', win.status_text.Text)


class DoorThreshold(unittest.TestCase):
    def open(self, unit_key):
        ns = load('DoorThresholdDialog.py', 'DoorThreshold.xaml')
        win = ns['DoorThresholdWindow'](FakeDoc(unit_key), None)
        win._all_doors = [SimpleNamespace(IsSelected=True, Element='door')]
        win._floor_type_map = {'Floor: Threshold': 'floor_type'}
        win.cmb_floor_type.SelectedItem = 'Floor: Threshold'
        win.generator = SimpleNamespace(generate_thresholds=Recorder(([], 1, 0, [])))
        return ns, win

    def test_label_and_default_follow_the_project_unit(self):
        _, win = self.open(METERS)
        self.assertEqual(win.lbl_offset.Text, 'Height Offset (m):')
        self.assertEqual(win.txt_offset.Text, '0')
        _, win = self.open(IMPERIAL)
        self.assertEqual(win.lbl_offset.Text, 'Height Offset (ft-in):')

    def test_typed_offset_goes_through_the_helper(self):
        _, win = self.open(METERS)
        win.txt_offset.Text = '0.02'                 # bare number = metres
        win.create_thresholds_clicked(None, None)
        self.assertAlmostEqual(win.generator.generate_thresholds.calls[0][2], 20.0, places=6)
        _, win = self.open(IMPERIAL)
        win.txt_offset.Text = '1/2"'
        win.create_thresholds_clicked(None, None)
        self.assertAlmostEqual(win.generator.generate_thresholds.calls[0][2], 12.7, places=6)

    def test_bad_offset_stops_with_the_reason(self):
        ns, win = self.open(METRIC)
        win.txt_offset.Text = '12 apples'
        win.create_thresholds_clicked(None, None)
        self.assertEqual(win.generator.generate_thresholds.calls, [])
        self.assertIn('Height Offset', ns['MESSAGES'][-1])
        self.assertIn('mm', ns['MESSAGES'][-1])

    def test_generator_reports_sizes_in_the_project_unit(self):
        ns = load('DoorThresholdDialog.py', 'DoorThreshold.xaml')
        gen = ns['ThresholdGenerator'](FakeDoc(IMPERIAL))
        self.assertEqual(gen.unit.tag, 'ft-in')
        self.assertEqual(gen.unit.show(3 * FT), '3\' - 0"')


class WallAdjustBase(unittest.TestCase):
    def levels(self):
        return {'Level': [SimpleNamespace(Name='Level 2', Elevation=10.0),
                          SimpleNamespace(Name='Level 1', Elevation=0.0)]}

    def test_level_elevations_show_in_the_project_unit(self):
        ns = load('WallAdjustBaseDialog.py', 'WallAdjustBase.xaml')
        win = ns['WallAdjustBaseWindow'](FakeDoc(IMPERIAL, self.levels()), None)
        self.assertEqual(win.cmb_levels.ItemsSource,
                         ['Level 1 (0\' - 0")', 'Level 2 (10\' - 0")'])
        win = ns['WallAdjustBaseWindow'](FakeDoc(METRIC, self.levels()), None)
        self.assertEqual(win.cmb_levels.ItemsSource, ['Level 1 (0 mm)', 'Level 2 (3048 mm)'])
        win = ns['WallAdjustBaseWindow'](FakeDoc(METERS, self.levels()), None)
        self.assertEqual(win.cmb_levels.ItemsSource[1], 'Level 2 (3.048 m)')


class WallCutProfile(unittest.TestCase):
    def open(self, unit_key):
        ns = load('WallCutProfileDialog.py', 'WallCutProfile.xaml')
        win = ns['WallCutProfileWindow'](FakeDoc(unit_key), None)
        win._selected_link = SimpleNamespace(GetLinkDocument=lambda: 'link_doc')
        win.rb_walls_sel.IsChecked = True
        win._picked_walls = ['wall']
        win.cmb_method.SelectedItem = 'Edit Wall Profile'
        win._execute_openings = Recorder((1, 0))
        return ns, win

    def test_label_and_default_follow_the_project_unit(self):
        _, win = self.open(IMPERIAL)
        self.assertEqual(win.lbl_offset.Text, 'CLEARANCE (FT-IN)')
        self.assertEqual(win.txt_offset.Text, '0\' - 1"')          # 25 mm
        _, win = self.open(METRIC)
        self.assertEqual(win.lbl_offset.Text, 'CLEARANCE (MM)')
        self.assertEqual(win.txt_offset.Text, '25')

    def test_typed_clearance_goes_through_the_helper(self):
        _, win = self.open(IMPERIAL)
        win.txt_offset.Text = '2"'
        win.btn_apply_clicked(None, None)
        self.assertAlmostEqual(win._execute_openings.calls[0][5], 2 * IN, places=9)
        _, win = self.open(IMPERIAL)
        win.txt_offset.Text = '50 mm'
        win.btn_apply_clicked(None, None)
        self.assertAlmostEqual(win._execute_openings.calls[0][5], 50 * MM, places=9)

    def test_bad_clearance_stops_with_the_reason(self):
        ns, win = self.open(METRIC)
        win.txt_offset.Text = ''
        win.btn_apply_clicked(None, None)
        self.assertEqual(win._execute_openings.calls, [])
        self.assertIn('Clearance', ns['MESSAGES'][-1])


class AutoDimension(unittest.TestCase):
    FIELDS = ('txt_offset', 'txt_l1', 'txt_l2', 'txt_l3', 'txt_min_seg',
              'txt_facade_offset', 'txt_facade_tol')

    def open(self, unit_key, scale=100):
        ns = load('AutoDimensionDialog.py', 'AutoDimension.xaml')
        ns['_is_valid_view'] = lambda view: True     # any fake view is dimensionable
        uidoc = SimpleNamespace(ActiveView=SimpleNamespace(Scale=scale))
        win = ns['AutoDimensionWindow'](uidoc, FakeDoc(unit_key))
        win._dim_types = ['dim_type']
        win.cmb_dim_type.SelectedIndex = 0
        win._dim_one_view = Recorder((1, 1, [], []))
        return ns, win

    def test_every_length_field_is_listed_with_its_unit_label(self):
        ns, _ = self.open(METRIC)
        self.assertEqual(tuple(f[0] for f in ns['LENGTH_FIELDS']), self.FIELDS)

    def test_unit_labels_and_defaults_follow_the_project_unit(self):
        ns, win = self.open(IMPERIAL)
        for box, label, default_mm, _name in ns['LENGTH_FIELDS']:
            self.assertEqual(getattr(win, label).Text, 'ft-in', label)
            self.assertEqual(getattr(win, box).Text,
                             U.LengthUnit(IMPERIAL).default_text(default_mm), box)
        self.assertEqual(win.txt_offset.Text, '3\' - 3 3/8"')      # 1000 mm
        self.assertIn('1/4" / 1/2" / 3/4"', win.btn_offsets_from_scale.ToolTip)
        ns, win = self.open(METRIC)
        self.assertEqual(win.lbl_l2_unit.Text, 'mm')
        self.assertEqual(win.txt_l2.Text, '1000')
        self.assertIn('6 mm / 12 mm / 18 mm', win.btn_offsets_from_scale.ToolTip)

    def test_offsets_from_scale_use_paper_sizes_of_the_unit_system(self):
        ns, _ = self.open(METRIC)
        ofs = ns['offsets_from_scale']
        mm = lambda vals: [round(v / MM, 6) for v in vals]
        self.assertEqual(mm(ofs(100, U.LengthUnit(METRIC))), [600, 1200, 1800])
        self.assertEqual(mm(ofs(50, U.LengthUnit(METERS))), [400, 800, 1200])   # minimums
        self.assertEqual(mm(ofs(200, U.LengthUnit(METRIC))), [1200, 2400, 3600])
        ftin = U.LengthUnit(IMPERIAL)
        self.assertEqual([round(v, 9) for v in ofs(96, ftin)], [2.0, 4.0, 6.0])   # 1/8" = 1'
        self.assertEqual([round(v * 12, 9) for v in ofs(48, ftin)], [16, 32, 48])

    def test_offsets_from_scale_button_writes_the_project_unit(self):
        _, win = self.open(IMPERIAL, scale=96)
        win.on_offsets_from_scale_clicked(None, None)
        self.assertEqual((win.txt_l1.Text, win.txt_l2.Text, win.txt_l3.Text),
                         ('2\' - 0"', '4\' - 0"', '6\' - 0"'))
        self.assertIn('L1=2\' - 0"', win.status_text.Text)
        _, win = self.open(METERS, scale=100)
        win.on_offsets_from_scale_clicked(None, None)
        self.assertEqual(win.txt_l3.Text, '1.8')
        self.assertIn('L3=1.8 m', win.status_text.Text)

    def test_typed_offsets_go_through_the_helper(self):
        _, win = self.open(IMPERIAL)
        win.cmb_offset_mode.SelectedIndex = 1          # 3-level
        win.txt_l1.Text, win.txt_l2.Text, win.txt_l3.Text = '2', '4\'-6"', '1500 mm'
        win.chk_facade.IsChecked = True
        win.txt_facade_offset.Text, win.txt_facade_tol.Text = '4', '6'
        win._run_auto_dimension()
        args = win._dim_one_view.calls[0]
        l1, l2, l3 = args[14:17]
        self.assertAlmostEqual(l1, 2.0)
        self.assertAlmostEqual(l2, 4.5)
        self.assertAlmostEqual(l3, 1500 * MM)
        self.assertAlmostEqual(args[23], 4.0)          # facade offset
        self.assertAlmostEqual(args[24], 6.0)          # facade tolerance
        # min segment: the default box (300 mm) reads 0' - 11 3/4" and is used as shown
        self.assertEqual(win.txt_min_seg.Text, '0\' - 11 3/4"')
        self.assertAlmostEqual(args[22], 11.75 * IN)

    def test_single_offset_in_metres(self):
        _, win = self.open(METERS)
        win.txt_offset.Text = '1.2'
        win._run_auto_dimension()
        l1, l2, l3 = win._dim_one_view.calls[0][14:17]
        self.assertAlmostEqual(l2, 1200 * MM)
        self.assertAlmostEqual(l1, 600 * MM)

    def test_bad_offset_stops_with_the_reason(self):
        ns, win = self.open(METRIC)
        win.chk_min_seg.IsChecked = True
        win.txt_min_seg.Text = 'short'
        win._run_auto_dimension()
        self.assertEqual(win._dim_one_view.calls, [])
        self.assertIn('Minimum segment', ns['MESSAGES'][-1])
        self.assertIn('Minimum segment', win.status_text.Text)

    def test_hidden_boxes_are_not_read(self):
        """Single-offset mode never reads the hidden L1-L3 boxes, and the
        facade boxes are read only when facade chains are on."""
        _, win = self.open(METRIC)
        win.txt_l1.Text = 'junk'
        win.chk_facade.IsChecked = False
        win.txt_facade_tol.Text = 'junk'
        win._run_auto_dimension()
        self.assertEqual(len(win._dim_one_view.calls), 1)


class PointCloud(unittest.TestCase):
    def elements(self):
        return [
            PCA.DetectedWall(0.0, 0.0, 0.0, 3000 * MM, 200 * MM, 'L1', True, ''),
            PCA.DetectedFloor(3000 * MM, [(0, 0), (4000 * MM, 0), (0, 2500 * MM)],
                              'floor', 'L1'),
            PCA.DetectedColumn(0, 0, 400 * MM, 600 * MM, 0.0, 3200 * MM, 'L1'),
            PCA.DetectedOpening('Door', {}, 0, 0, 900 * MM, 2100 * MM, 'L1'),
            PCA.DetectedStair(0.0, 3000 * MM, 18, 'L1'),
            PCA.DetectedRoof(9000 * MM, [], 12.5, 'L1'),
        ]

    def open(self, unit_key):
        ns = load('PointCloudDialog.py', 'PointCloud.xaml', doc=FakeDoc(unit_key), uidoc=None)
        win = ns['PointCloudModelWindow']({})
        win._brush = lambda colour: colour
        return ns, win

    def test_mm_project_keeps_the_service_text(self):
        ns, _ = self.open(METRIC)
        for el in self.elements():
            self.assertEqual(ns['_dimensions_text'](el, U.LengthUnit(METRIC)), el.Dimensions,
                             el.Type)

    def test_imperial_project_shows_feet_inches(self):
        ns, win = self.open(IMPERIAL)
        self.assertEqual(win._unit.tag, 'ft-in')
        text = {el.Type: ns['_dimensions_text'](el, win._unit) for el in self.elements()}
        self.assertEqual(text['Wall'], 'L=9\' - 10 1/8"  T=0\' - 7 7/8"')
        self.assertEqual(text['Column'], 'W=1\' - 3 3/4" D=1\' - 11 5/8" H=10\' - 6"')
        self.assertEqual(text['Door'], 'W=2\' - 11 3/8" H=6\' - 10 5/8"')
        self.assertEqual(text['Floor'], '~108 ft²  @Z=9\' - 10 1/8"')
        self.assertTrue(text['Stair'].startswith('18 treads  Rise=0\' - 0" to 9\' - 10 1/8"'))
        self.assertEqual(text['Roof'], 'Slope=12.5°  @Z=29\' - 6 3/8"')   # angle stays

    def test_results_grid_gets_the_project_unit(self):
        _, win = self.open(METERS)
        win._detected_elements = self.elements()
        win._populate_results()
        self.assertEqual(win.results_grid.ItemsSource[0].Dimensions, 'L=3 m  T=0.2 m')
        self.assertEqual(win.results_grid.ItemsSource[1].Dimensions, '~10.0 m²  @Z=3 m')

    def test_region_size_shows_in_the_project_unit(self):
        _, win = self.open(IMPERIAL)
        win._custom_min_pt = SimpleNamespace(X=0.0, Y=0.0, Z=0.0)
        win._custom_max_pt = SimpleNamespace(X=40.0, Y=25.5, Z=10.0)
        win._show_region_state()
        self.assertEqual(win.lbl_region_info.Text, 'Region set: 40\' - 0" × 25\' - 6"')


class TextToElement(unittest.TestCase):
    def open(self, unit_key):
        ns = load('TextToElementDialog.py', 'TextToElement.xaml')
        app = SimpleNamespace(ActiveUIDocument=SimpleNamespace(Document=FakeDoc(unit_key)))
        win = ns['TextToElementDialog'](app)
        win.cmb_category.SelectedItem = SimpleNamespace(Name='Walls', Bic='OST_Walls')
        win.cmb_parameter.SelectedItem = 'Comments'
        win.rb_pick_items.IsChecked = False
        win._get_text_notes_from_view = Recorder([])
        return ns, win

    def test_unit_and_default_follow_the_project_unit(self):
        _, win = self.open(IMPERIAL)
        self.assertEqual(win.lbl_tolerance_unit.Text, 'ft-in')
        self.assertEqual(win.txt_tolerance.Text, '0\' - 5 7/8"')    # 150 mm
        _, win = self.open(METRIC)
        self.assertEqual(win.lbl_tolerance_unit.Text, 'mm')
        self.assertEqual(win.txt_tolerance.Text, '150')

    def test_typed_tolerance_goes_through_the_helper(self):
        _, win = self.open(IMPERIAL)
        win.txt_tolerance.Text = '6"'
        self.assertAlmostEqual(win._get_tolerance_feet(), 0.5)
        win.txt_tolerance.Text = '1'                    # bare number = feet here
        self.assertAlmostEqual(win._get_tolerance_feet(), 1.0)
        _, win = self.open(METRIC)
        win.txt_tolerance.Text = '150'
        self.assertAlmostEqual(win._get_tolerance_feet(), 150 * MM)

    def test_bad_tolerance_stops_before_collecting(self):
        _, win = self.open(METRIC)
        win.txt_tolerance.Text = 'wide'
        win._on_find_intersections(None, None)
        self.assertEqual(win._get_text_notes_from_view.calls, [])
        self.assertIn('Tolerance', win.txt_status.Text)
        self.assertIn('mm', win.txt_status.Text)


# ─────────────────────────────── static guards ──────────────────────────────
DIALOGS = ('RoomToFloorDialog.py', 'DoorThresholdDialog.py', 'WallAdjustBaseDialog.py',
           'WallCutProfileDialog.py', 'AutoDimensionDialog.py', 'PointCloudDialog.py',
           'TextToElementDialog.py')


class NoHandRolledMillimetres(unittest.TestCase):
    def test_dialogs_use_the_shared_helper(self):
        for name in DIALOGS:
            with open(os.path.join(GUI, name), encoding='utf-8') as f:
                src = f.read()
            self.assertRegex(src, r'from Snippets\._units import [^\n]*project_length_unit',
                             name)

    def test_no_textbox_is_read_with_float(self):
        """`float(box.Text)` treats every number as mm and hides typos —
        typed lengths go through LengthUnit.parse."""
        for name in DIALOGS:
            path = os.path.join(GUI, name)
            with open(path, encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'float'):
                    attrs = [n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)]
                    self.assertNotIn('Text', attrs, '{}:{}'.format(name, node.lineno))

    def test_no_mm_conversion_on_displayed_values(self):
        """No `* 304.8` / `/ 304.8` / `ft_to_mm(...)` feeding a label or message
        in the window classes."""
        for name in DIALOGS:
            path = os.path.join(GUI, name)
            with open(path, encoding='utf-8') as f:
                src = f.read()
            for cls in [n for n in ast.parse(src).body if isinstance(n, ast.ClassDef)
                        and n.name.endswith(('Window', 'Dialog', 'Item'))]:
                seg = ast.get_source_segment(src, cls)
                self.assertNotRegex(seg, r'304\.8|ft_to_mm\(|FT_TO_MM', '{} {}'.format(
                    name, cls.name))


if __name__ == '__main__':
    unittest.main()
