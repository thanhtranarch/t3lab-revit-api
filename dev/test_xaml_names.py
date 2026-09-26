# -*- coding: utf-8 -*-
"""Mọi x:Name mà Python đọc KHÔNG bọc guard phải tồn tại trong XAML (luật S8).

Thiếu tên = AttributeError lúc người dùng bấm nút, không có test nào khác bắt được
vì audit_t3 chỉ đọc XAML. Các cặp dưới đây đều từng hỏng thật (2026-09-26):
  - ManaAnno > DimText: Apply đọc chk_leader / rb_view / chk_filter_enable
  - ManaContains: 2 dialog con dựng tay chết ngay khi mở, nay là XAML T3
  - BatchOut: code ghi sheet_set_label nhưng XAML đặt tên sheet_set_summary
"""
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, 'T3Lab.extension', 'lib', 'GUI', 'Tools')

REQUIRED = {
    'ManaAnno.xaml': ['txt_prefix', 'txt_suffix', 'txt_above', 'txt_below',
                      'txt_override', 'chk_leader', 'rb_view', 'rb_selection',
                      'chk_filter_enable', 'sp_filter_config', 'combo_combine',
                      'sp_rules'],
    'DimText.xaml': ['chk_leader', 'rb_view', 'chk_filter_enable',
                     'sp_filter_config', 'combo_combine', 'sp_rules', 'lbl_status'],
    'ContainsDefineValue.xaml': ['avail_list', 'selected_list', 'txt_sep',
                                 'lbl_prev', 'empty_avail', 'empty_selected'],
    'ContainsSetParam.xaml': ['param_cb', 'lbl_value'],
    'ExportManager.xaml': ['sheet_set_summary', 'sheet_set_checklist'],
}


def _names(fname):
    with open(os.path.join(TOOLS, fname), encoding='utf-8-sig') as f:
        return set(re.findall(r'x:Name="([^"]+)"', f.read()))


class XamlNames(unittest.TestCase):
    def test_required_names_exist(self):
        for fname, wanted in sorted(REQUIRED.items()):
            missing = sorted(set(wanted) - _names(fname))
            self.assertEqual(missing, [], '{} is missing {}'.format(fname, missing))

    def test_manaanno_presets_carry_tag(self):
        """dimtext_preset_below writes sender.Tag into the Below field."""
        with open(os.path.join(TOOLS, 'ManaAnno.xaml'), encoding='utf-8-sig') as f:
            buttons = re.findall(r'<Button [^>]*Click="dimtext_preset_below"[^>]*>', f.read())
        self.assertTrue(buttons)
        for b in buttons:
            self.assertIn('Tag="', b)


if __name__ == '__main__':
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
