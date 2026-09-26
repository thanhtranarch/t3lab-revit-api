# -*- coding: utf-8 -*-
"""BatchOut DWG "Match Revit display": the exported DWG carries the colors,
overrides and linetype scale the Revit view shows — not the export layer
table's index colors (magenta text, orange doors, blue schedule fills).

Runs the shipped apply_revit_display_fidelity / ExportProfile code against a
fake Revit API, without Revit.

Run: python dev/test_batchout_dwg_display.py
"""
import ast
import os
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'T3Lab.extension/lib/GUI/BatchOutDialog.py'
XAML = ROOT / 'T3Lab.extension/lib/GUI/Tools/ExportManager.xaml'
X_NAME = '{http://schemas.microsoft.com/winfx/2006/xaml}Name'


def fake_db(per_view=True):
    colors = dict(IndexColors='index', TrueColor='true')
    if per_view:
        colors['TrueColorPerView'] = 'true-per-view'
    return SimpleNamespace(
        ExportColorMode=SimpleNamespace(**colors),
        PropOverrideMode=SimpleNamespace(ByLayer='by-layer', ByEntity='by-entity'),
        LineScaling=SimpleNamespace(ViewScale='view-scale', ModelSpace='model',
                                    PaperSpace='paper'),
        Color=lambda r, g, b: (r, g, b))


def load(names, db):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
    tree.body = [n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    scope = dict(DB=db, logger=Mock(), os=os, datetime=datetime)
    exec(compile(tree, str(SOURCE), 'exec'), scope)
    return scope


class DwgOptions(object):
    """What a named export setup on the AIA layer table typically holds."""

    def __init__(self, hatch_background=False):
        self.Colors = 'index'
        self.PropOverrides = 'by-layer'
        self.LineScaling = 'model'
        self.UseHatchBackgroundColor = hatch_background
        self.HatchBackgroundColor = None


class MatchRevitDisplay(unittest.TestCase):
    def apply(self, options, db=None, for_sheets=True):
        scope = load({'_enum_member', 'apply_revit_display_fidelity'}, db or fake_db())
        return scope['apply_revit_display_fidelity'](options, for_sheets=for_sheets)

    def test_sheet_gets_view_colors_entity_overrides_and_paper_space_linetypes(self):
        opts = DwgOptions()
        applied = self.apply(opts)
        self.assertEqual(opts.Colors, 'true-per-view')
        self.assertEqual(opts.PropOverrides, 'by-entity')
        self.assertEqual(opts.LineScaling, 'paper')
        self.assertEqual(applied, ['true colors', 'overrides by entity', 'linetype scale'])

    def test_view_exported_alone_scales_linetypes_by_view(self):
        opts = DwgOptions()
        self.apply(opts, for_sheets=False)
        self.assertEqual(opts.LineScaling, 'view-scale')

    def test_build_without_per_view_mode_falls_back_to_true_color(self):
        opts = DwgOptions()
        self.apply(opts, db=fake_db(per_view=False))
        self.assertEqual(opts.Colors, 'true')

    def test_hatch_background_is_never_forced_on(self):
        opts = DwgOptions(hatch_background=False)
        self.apply(opts)
        self.assertFalse(opts.UseHatchBackgroundColor)
        self.assertIsNone(opts.HatchBackgroundColor)

    def test_hatch_background_from_setup_becomes_paper_white(self):
        opts = DwgOptions(hatch_background=True)
        self.assertIn('white hatch background', self.apply(opts))
        self.assertEqual(opts.HatchBackgroundColor, (255, 255, 255))

    def test_missing_api_members_are_skipped_not_fatal(self):
        opts = SimpleNamespace()            # an options object with none of them
        self.assertEqual(self.apply(opts, db=SimpleNamespace()), [])


class ProfileAndUi(unittest.TestCase):
    def setUp(self):
        self.Profile = load({'ExportProfile'}, fake_db())['ExportProfile']

    def test_on_by_default_and_round_trips(self):
        profile = self.Profile()
        self.assertTrue(profile.CADMatchRevitDisplay)
        profile.CADMatchRevitDisplay = False
        again = self.Profile.from_dict(profile.to_dict())
        self.assertFalse(again.CADMatchRevitDisplay)

    def test_profile_saved_before_the_option_keeps_it_on(self):
        old = self.Profile().to_dict()
        del old['CADMatchRevitDisplay']
        self.assertTrue(self.Profile.from_dict(old).CADMatchRevitDisplay)

    def test_checkbox_ships_checked(self):
        box = next(n for n in ET.parse(XAML).getroot().iter()
                   if n.get(X_NAME) == 'cad_match_revit_display')
        self.assertEqual(box.get('IsChecked'), 'True')

    def test_dwg_export_only_overrides_the_setup_when_checked(self):
        src = SOURCE.read_text(encoding='utf-8-sig')
        tree = ast.parse(src)
        cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == 'ExportManagerWindow')
        method = next(n for n in cls.body
                      if isinstance(n, ast.FunctionDef) and n.name == 'export_to_dwg')
        body = ast.get_source_segment(src, method)
        self.assertIn('if self.cad_match_revit_display.IsChecked:', body)
        self.assertIn('apply_revit_display_fidelity(', body)
        # The old unconditional overrides are gone.
        self.assertNotIn('UseHatchBackgroundColor = True', body)


if __name__ == '__main__':
    unittest.main()
