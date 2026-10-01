# -*- coding: utf-8 -*-
"""
AssistantCards — code-built WPF cards for the T3Lab Assistant chat.

Keeps script.py thin: card construction lives here, callbacks stay in
the window class. Zinc styling matches T3LabAssistant.xaml's local
palette. UI THREAD only (WPF imports happen at call time).

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

__author__ = "Tran Tien Thanh"
__title__ = "Assistant Cards"

# These cards used to be Vietnamese-only, which is why the window still mixed
# languages after the 2026-07-18 "English lock": nothing here consulted the
# user's language at all. Every visible string now has both variants and the
# caller passes `viet` for the current turn.
_ACTION_LABELS_VI = {
    'fix_parameter':  'Sửa parameter',
    'move_element':   'Di chuyển element',
    'fix_tag':        'Sửa tag',
    'fix_dimension':  'Sửa dimension',
    'create_element': 'Tạo element',
    'delete_element': 'Xóa element',
    'place_note':     'Đặt ghi chú',
    'info':           'Chỉ thông tin',
    'manual':         'Xử lý tay',
}

_ACTION_LABELS_EN = {
    'fix_parameter':  'Fix parameter',
    'move_element':   'Move element',
    'fix_tag':        'Fix tag',
    'fix_dimension':  'Fix dimension',
    'create_element': 'Create element',
    'delete_element': 'Delete element',
    'place_note':     'Place note',
    'info':           'Information only',
    'manual':         'Handle manually',
}

# key → (vi, en)
_TEXT = {
    'title':        (u"Comment bản vẽ: ",           u"Drawing comments: "),
    'sheet_match':  (u"Sheet khớp: {} — {}   ·   model đang mở",
                     u"Matched sheet: {} — {}   ·   in the open model"),
    'needs_switch': (u"Không thấy sheet trong model ACTIVE — có model khác "
                     u"đang mở, cần switch document trước khi xử lý.",
                     u"Sheet is not in the ACTIVE model — another model is "
                     u"open; switch document before processing."),
    'no_match':     (u"Chưa khớp được sheet nào trong model đang mở.",
                     u"No sheet matched in the open model."),
    'partial':      (u"PDF nén một phần — có thể còn markup chưa đọc được.",
                     u"PDF is partly compressed — some markup may be unread."),
    'row_head':     (u"[{}] trang {} · {} · {}",
                     u"[{}] page {} · {} · {}"),
    'no_author':    (u"không rõ tác giả",           u"unknown author"),
    'no_content':   (u"(markup không có nội dung chữ)",
                     u"(markup has no text content)"),
    'proposal':     (u"Đề xuất: [{}] {}",           u"Proposed: [{}] {}"),
    'run':          (u"Thực hiện",                u"Run"),
    'run_tip':      (u"Chạy phương án qua AI agent",
                     u"Run this proposal through the AI agent"),
    'note':         (u"Ghi chú vào sheet",          u"Note on sheet"),
    'note_tip':     (u"Đặt TextNote đánh dấu comment này trong model",
                     u"Place a TextNote marking this comment in the model"),
    'skip':         (u"Bỏ qua",                     u"Skip"),
    'skip_tip':     (u"Đánh dấu không xử lý",       u"Mark as not handled"),
}


def _t(key, viet):
    """Localised string for `key`."""
    pair = _TEXT.get(key)
    if not pair:
        return key
    return pair[0] if viet else pair[1]

_EXECUTABLE_ACTIONS = frozenset([
    'fix_parameter', 'move_element', 'fix_tag', 'fix_dimension',
    'create_element', 'delete_element', 'place_note',
])


def _brush(r, g, b):
    from System.Windows.Media import SolidColorBrush, Color
    return SolidColorBrush(Color.FromRgb(r, g, b))


def _font():
    from System.Windows.Media import FontFamily
    return FontFamily("Segoe UI")


def _mini_button(label, tooltip, fg=(82, 82, 91)):
    from System.Windows.Controls import Button
    from System.Windows import Thickness
    import System.Windows.Input
    btn = Button()
    btn.Content = label
    # Resolve the shared style after insertion into the owner resource tree.
    btn.SetResourceReference(Button.StyleProperty, "T3.Button.Secondary")
    btn.Margin = Thickness(0, 0, 4, 0)
    btn.Cursor = System.Windows.Input.Cursors.Hand
    btn.ToolTip = tooltip
    return btn


def build_comment_report_card(report, on_run, on_note, on_skip, viet=False):
    """The comment-resolution report card.

    Args:
        report: CommentAgent.analyze() result.
        on_run/on_note/on_skip: callbacks receiving (item, status_setter)
            where status_setter(text, ok) updates the row's status label.
        viet: render the card in Vietnamese instead of English.

    Returns a WPF Border ready to append to the chat panel.
    """
    from System.Windows.Controls import (Border, TextBlock, StackPanel,
                                         Orientation, Grid, ColumnDefinition)
    from System.Windows import Thickness, CornerRadius, TextWrapping, GridLength
    import System.Windows

    card = Border()
    card.SetResourceReference(Border.BackgroundProperty, "T3.Surface")
    card.SetResourceReference(Border.BorderBrushProperty, "T3.Border")
    card.BorderThickness = Thickness(1)
    card.SetResourceReference(Border.CornerRadiusProperty, "T3.R.Control")
    card.Padding = Thickness(12)
    card.Margin = Thickness(32, 0, 40, 12)

    root = StackPanel()

    # ── header ──
    title = TextBlock()
    title.Text = _t('title', viet) + (report.get('pdf_name') or '')
    title.SetResourceReference(TextBlock.FontSizeProperty, "T3.Size.Body")
    title.FontWeight = System.Windows.FontWeights.SemiBold
    title.FontFamily = _font()
    title.SetResourceReference(TextBlock.ForegroundProperty, "T3.Ink")
    title.TextWrapping = TextWrapping.Wrap
    root.Children.Add(title)

    match = report.get('sheet_match')
    sub = TextBlock()
    if match:
        sub.Text = _t('sheet_match', viet).format(
            match.get('number', ''), match.get('name', ''))
        sub.SetResourceReference(TextBlock.ForegroundProperty, "T3.Success.Text")
    elif report.get('needs_switch'):
        sub.Text = _t('needs_switch', viet)
        sub.SetResourceReference(TextBlock.ForegroundProperty, "T3.Warning.Text")
    else:
        sub.Text = _t('no_match', viet)
        sub.SetResourceReference(TextBlock.ForegroundProperty, "T3.Danger.Text")
    sub.SetResourceReference(TextBlock.FontSizeProperty, "T3.Size.Caption")
    sub.FontFamily = _font()
    sub.TextWrapping = TextWrapping.Wrap
    sub.Margin = Thickness(0, 4, 0, 0)
    root.Children.Add(sub)

    if report.get('partial_extraction'):
        warn = TextBlock()
        warn.Text = _t('partial', viet)
        warn.SetResourceReference(TextBlock.FontSizeProperty, "T3.Size.Caption")
        warn.FontFamily = _font()
        warn.SetResourceReference(TextBlock.ForegroundProperty, "T3.TextMuted")
        warn.Margin = Thickness(0, 4, 0, 0)
        root.Children.Add(warn)

    sep = Border()
    sep.Height = 1
    sep.SetResourceReference(Border.BackgroundProperty, "T3.Border")
    sep.Margin = Thickness(0, 8, 0, 8)
    root.Children.Add(sep)

    # ── items ──
    for item in report.get('items', []):
        root.Children.Add(_build_item_row(item, report,
                                          on_run, on_note, on_skip, viet))

    card.Child = root
    return card


def _build_item_row(item, report, on_run, on_note, on_skip, viet=False):
    from System.Windows.Controls import (Border, TextBlock, StackPanel,
                                         Orientation)
    from System.Windows import Thickness, CornerRadius, TextWrapping
    import System.Windows

    prop = item.get('proposal') or {}
    row = Border()
    row.SetResourceReference(Border.BackgroundProperty, "T3.SurfaceSunken")
    row.SetResourceReference(Border.BorderBrushProperty, "T3.Border")
    row.BorderThickness = Thickness(1)
    row.CornerRadius = CornerRadius(4)
    row.Padding = Thickness(8)
    row.Margin = Thickness(0, 0, 0, 8)

    box = StackPanel()

    head = TextBlock()
    head.Text = _t('row_head', viet).format(
        item.get('id', ''), item.get('page', ''),
        item.get('subtype', ''), item.get('author') or _t('no_author', viet))
    head.SetResourceReference(TextBlock.FontSizeProperty, "T3.Size.Caption")
    head.FontFamily = _font()
    head.SetResourceReference(TextBlock.ForegroundProperty, "T3.TextMuted")
    box.Children.Add(head)

    content = TextBlock()
    content.Text = (item.get('content') or item.get('subject')
                    or _t('no_content', viet))
    content.SetResourceReference(TextBlock.FontSizeProperty, "T3.Size.Body")
    content.FontFamily = _font()
    content.SetResourceReference(TextBlock.ForegroundProperty, "T3.Text")
    content.TextWrapping = TextWrapping.Wrap
    content.Margin = Thickness(0, 4, 0, 0)
    box.Children.Add(content)

    proposal = TextBlock()
    action = prop.get('action_type', 'manual')
    labels = _ACTION_LABELS_VI if viet else _ACTION_LABELS_EN
    label = labels.get(action, action)
    desc = prop.get('description') or ''
    proposal.Text = _t('proposal', viet).format(label, desc)
    proposal.SetResourceReference(TextBlock.FontSizeProperty, "T3.Size.Caption")
    proposal.FontFamily = _font()
    proposal.SetResourceReference(TextBlock.ForegroundProperty, "T3.TextSecondary")
    proposal.TextWrapping = TextWrapping.Wrap
    proposal.Margin = Thickness(0, 4, 0, 0)
    box.Children.Add(proposal)

    # buttons + status
    bar = StackPanel()
    bar.Orientation = Orientation.Horizontal
    bar.Margin = Thickness(0, 8, 0, 0)

    status = TextBlock()
    status.SetResourceReference(TextBlock.FontSizeProperty, "T3.Size.Caption")
    status.FontFamily = _font()
    status.SetResourceReference(TextBlock.ForegroundProperty, "T3.TextMuted")
    status.VerticalAlignment = System.Windows.VerticalAlignment.Center
    status.Margin = Thickness(4, 0, 0, 0)

    def _make_setter(_status=status, _row=row):
        def _set(text, ok):
            try:
                _status.Text = text
                _status.SetResourceReference(TextBlock.ForegroundProperty,
                                             "T3.Success.Text" if ok else "T3.TextMuted")
            except Exception:
                pass
        return _set
    setter = _make_setter()

    executable = action in _EXECUTABLE_ACTIONS
    if executable and on_run is not None:
        b_run = _mini_button(_t('run', viet), _t('run_tip', viet),
                             fg=(16, 185, 129))
        b_run.Click += _wrap_cb(on_run, item, setter)
        bar.Children.Add(b_run)
    if on_note is not None:
        b_note = _mini_button(_t('note', viet), _t('note_tip', viet))
        b_note.Click += _wrap_cb(on_note, item, setter)
        bar.Children.Add(b_note)
    if on_skip is not None:
        b_skip = _mini_button(_t('skip', viet), _t('skip_tip', viet),
                              fg=(161, 161, 170))
        b_skip.Click += _wrap_cb(on_skip, item, setter)
        bar.Children.Add(b_skip)
    bar.Children.Add(status)

    box.Children.Add(bar)
    row.Child = box
    return row


def _wrap_cb(cb, item, setter):
    def _handler(sender, e):
        try:
            cb(item, setter)
        except Exception:
            pass
    return _handler
