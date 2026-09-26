# -*- coding: utf-8 -*-
"""AI Mode chuẩn T3 (T3LAB_UI_STANDARD.md § AI Mode) — chỉ 6 tool, cùng một hình thức.

Thêm AI vào tool mới thì phải cập nhật AI_TOOLS ở đây VÀ bảng trong chuẩn UI,
kèm lý do vì sao luật không làm được việc đó.
"""
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI = os.path.join(REPO, 'T3Lab.extension', 'lib', 'GUI')
TOOLS = os.path.join(GUI, 'Tools')

# XAML -> (file dialog, số nút AI)
AI_TOOLS = {
    'CADToElements.xaml': ('CADToElementsDialog.py', 7),
    'IFCSG.xaml': ('IFCSGDialog.py', 1),
    'ManaPara.xaml': ('ManaParaDialog.py', 1),
    'FamiGen.xaml': ('FamiGenDialog.py', 1),
    'TextToElement.xaml': ('TextToElementDialog.py', 1),
    'ManaAnno.xaml': ('ManaAnnoDialog.py', 1),
}
EXEMPT = {'T3LabAssistant.xaml', 'LLMSetting.xaml'}   # chat surface / nơi bật tắt AI
AI_ICON = '&#xEA80;'


def read(path):
    with open(path, encoding='utf-8-sig') as fh:
        return fh.read()


def ai_buttons(src):
    """Số <Button> có nhãn bắt đầu bằng 'AI ' (icon EA80 + TextBlock 'AI …')."""
    return len(re.findall(r'<TextBlock Text="&#xEA80;" Style="\{StaticResource T3\.Icon\.Lead\}"/>\s*'
                          r'<TextBlock Text="AI [^"]+"', src))


class AIModeStandard(unittest.TestCase):
    def test_only_listed_tools_have_ai(self):
        for name in sorted(os.listdir(TOOLS)):
            if not name.endswith('.xaml') or name in EXEMPT:
                continue
            src = read(os.path.join(TOOLS, name))
            has_badge = 'x:Name="ai_mode_badge"' in src
            self.assertEqual(has_badge, name in AI_TOOLS,
                             '{}: badge AI {} danh sách AI_TOOLS'.format(
                                 name, 'có nhưng không nằm trong' if has_badge else 'thiếu dù nằm trong'))

    def test_badge_and_buttons_follow_one_pattern(self):
        for name, (dialog, n_buttons) in AI_TOOLS.items():
            src = read(os.path.join(TOOLS, name))
            badge = src[src.index('x:Name="ai_mode_badge"'):]
            badge = badge[:badge.index('</Border>')]
            self.assertIn('Style="{StaticResource T3.Pill}"', badge, name)
            self.assertIn(AI_ICON, badge, name)
            self.assertIn('x:Name="txt_ai_status"', badge, name)
            self.assertEqual(ai_buttons(src), n_buttons, '{}: số nút AI chuẩn'.format(name))
            self.assertNotIn(u'✨', src, name)       # ✨
            self.assertNotIn(u'⏳', src, name)       # ⏳
            py = read(os.path.join(GUI, dialog))
            self.assertRegex(py, r'\n    AI_TOOL = "\w+"', dialog)
            self.assertIn('init_ai_badge()', py, dialog)
            self.assertIn('ai_require()', py, dialog)
            self.assertNotRegex(py, r'\.Content = "[^"]*(✨|⏳)', dialog)


if __name__ == '__main__':
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
