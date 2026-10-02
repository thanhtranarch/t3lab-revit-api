# -*- coding: utf-8 -*-
"""Luật 22 · icon — các kiểm tra của dev/audit_t3.py (thêm 2026-10-02).

  - ô T3.Rail.Tile tự vẽ <Path>                     → P2
  - glyph không có trong Segoe MDL2 Assets (tofu)   → P1
  - glyph ngoài bảng glyph chuẩn của chuẩn UI       → P3
  - cùng hai luật cuối cho \\uXXXX trong Python lib/GUI

Run: python3 dev/test_audit_t3_icons.py
"""
import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "audit_t3", os.path.join(REPO, "dev", "audit_t3.py"))
audit_t3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_t3)

KEYS = audit_t3.stylesheet_keys()
NS = ('xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
      'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"')


def xaml(body):
    return '<Grid %s>%s</Grid>' % (NS, body)


def icon_issues(src, base="Tool.xaml"):
    issues, _, _, _ = audit_t3.audit(src, base, KEYS)
    # Chỉ lấy thông báo của luật 22 — fragment không nhúng khối T3 STYLES nên
    # luật "key phải tồn tại" cũng báo, nhưng đó không phải việc của test này.
    return [i for i in issues
            if "tự vẽ <Path>" in i[1] or i[1].startswith("glyph")]


RAIL_PATH = ('<RadioButton x:Name="nav_a" Style="{StaticResource T3.Rail.Tile}" ToolTip="A">'
             '<Path Data="M0 0 L1 1" Width="18" Height="18"/></RadioButton>')
RAIL_GLYPH = ('<RadioButton x:Name="nav_b" Style="{StaticResource T3.Rail.Tile}" ToolTip="B">'
              '<TextBlock Text="&#xE8FD;" Style="{StaticResource T3.Icon.Rail}"/></RadioButton>')
TOFU = '<TextBlock Text="&#xE7AE;" Style="{StaticResource T3.Icon}"/>'
OFF_TABLE = '<TextBlock Text="&#xE723;" Style="{StaticResource T3.Icon}"/>'


class RailTile(unittest.TestCase):
    def test_path_in_rail_tile_is_p2(self):
        found = icon_issues(xaml(RAIL_PATH))
        self.assertEqual([s for s, _ in found], ["P2"])
        self.assertIn("nav_a", found[0][1])
        self.assertIn("T3.Icon.Rail", found[0][1])

    def test_glyph_rail_tile_is_clean(self):
        self.assertEqual(icon_issues(xaml(RAIL_GLYPH)), [])

    def test_toggle_button_tile_is_checked_too(self):
        src = xaml(RAIL_PATH.replace("RadioButton", "ToggleButton"))
        self.assertEqual([s for s, _ in icon_issues(src)], ["P2"])

    def test_exempt_file_is_skipped(self):
        src = xaml(RAIL_PATH + TOFU + OFF_TABLE)
        self.assertEqual(icon_issues(src, "DWGManagement.xaml"), [])


class GlyphInFontAndTable(unittest.TestCase):
    def test_tofu_is_p1_and_not_double_reported(self):
        found = icon_issues(xaml(TOFU))
        self.assertEqual([s for s, _ in found], ["P1"])
        self.assertIn("E7AE", found[0][1])

    def test_off_table_is_p3(self):
        found = icon_issues(xaml(OFF_TABLE))
        self.assertEqual([s for s, _ in found], ["P3"])
        self.assertIn("E723 Attach", found[0][1])

    def test_lowercase_entity_and_literal_char(self):
        self.assertEqual(icon_issues(xaml('<TextBlock Text="&#xe7ae;"/>'))[0][0], "P1")
        self.assertEqual(icon_issues(xaml('<TextBlock Text=""/>'))[0][0], "P3")

    def test_glyph_inside_synced_style_block_is_ignored(self):
        block = "%s\n<!-- &#xE7AE; -->\n%s" % (audit_t3.SYNC_BEGIN + " ═══ -->",
                                               audit_t3.SYNC_END)
        self.assertEqual(icon_issues(xaml(block)), [])

    def test_non_glyph_entities_are_ignored(self):
        self.assertEqual(icon_issues(xaml('<TextBlock Text="&#x2014;&#xFEFF;"/>')), [])


class PythonGlyphs(unittest.TestCase):
    def test_escapes(self):
        self.assertEqual(audit_t3.audit_py('g = u"\\uE72C"', "X.py"), [])
        self.assertEqual([s for s, _ in audit_t3.audit_py('g = u"\\uE7AE"', "X.py")], ["P1"])
        self.assertEqual([s for s, _ in audit_t3.audit_py('g = u"\\uE723"', "X.py")], ["P3"])
        self.assertEqual(audit_t3.audit_py('bom = u"\\uFEFF"', "X.py"), [])

    def test_literal_private_use_char(self):
        self.assertEqual([s for s, _ in audit_t3.audit_py('g = u""', "X.py")], ["P1"])

    def test_exempt_assistant(self):
        self.assertEqual(audit_t3.audit_py('g = u"\\uE7AE"', "T3LabAssistantDialog.py"), [])


class ReferenceData(unittest.TestCase):
    def test_table_parsed_and_every_table_glyph_exists(self):
        cps, table = audit_t3.icon_refs()
        self.assertIsNotNone(cps)
        self.assertIsNotNone(table)
        for code in ("E721", "E8BB", "F140", "E923", "E8FD", "E9D5", "ECA5"):
            self.assertIn(code, table)
        self.assertNotIn("ED1A", table)          # Segoe Fluent Icons, not MDL2
        self.assertEqual(sorted(c for c in table if c not in cps), [])

    def test_rail_style_inherits_tile_colour(self):
        path = os.path.join(REPO, "pyRevit UI Design System", "T3Lab.Styles.xaml")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        i = src.index('<Style x:Key="T3.Icon.Rail"')
        style = src[i:src.index("</Style>", i)]
        self.assertIn('Value="16"', style)
        self.assertNotIn("Foreground", style)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
