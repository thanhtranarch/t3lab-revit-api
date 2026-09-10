# -*- coding: utf-8 -*-
"""
DataGrid column filter — bộ lọc theo cột kiểu Excel cho T3.DataGrid.

Cột nào khai HeaderStyle="{StaticResource T3.DataGridColumnHeader.Filter}" thì
bấm thẳng vào TÊN cột sẽ mở popup liệt kê các giá trị phân biệt của cột (ô
search + "Select all" + Clear); tick/bỏ tick là lọc ngay. Header không đeo icon
phễu — cột đang lọc được tô đậm, và bảng cũng có sẵn thứ tự sắp mặc định.

Vì click header giờ mang nghĩa "lọc", sự kiện `Sorting` của DataGrid bị chặn
cho các cột có bộ lọc; popup cấp lại ba nút A→Z / Z→A / Default để không mất
khả năng sắp xếp.

Popup dựng bằng Python thay vì XAML vì hai lý do:
  * pyRevit nạp XAML bằng XamlReader nên không có base URI — stylesheet phải
    nhúng, và nhúng thêm khối popup vào từng tool XAML là nhân bản 4 lần cùng
    một đoạn markup.
  * Danh sách giá trị phụ thuộc dữ liệu runtime của từng cột, không khai tĩnh
    trong DataTemplate được.
Diện mạo vẫn lấy hết từ {StaticResource T3.*} qua FindResource — không hardcode
màu/size ở đây.

Cách dùng:

    from GUI.DataGridColumnFilter import ColumnFilterController

    self.views_filter = ColumnFilterController(
        self, self.views_grid,
        columns=[("TYPE", "view_type"), ("LEVEL", "level_name")],
        source=lambda: self.all_views,
        on_changed=self._apply_views_filters)

    # trong hàm lọc:
    if not self.views_filter.passes(item):
        continue
"""

import clr  # noqa: F401 — pythonnet cần import này trước khi nạp assembly WPF

from System.Windows import (Thickness, HorizontalAlignment,
                            FontWeights, TextWrapping, Visibility)
from System.Windows.Controls import (Border, StackPanel, ScrollViewer, TextBox,
                                     CheckBox, Button, TextBlock, Orientation,
                                     ScrollBarVisibility, DataGridRow)
from System.Windows.Controls.Primitives import (Popup, PlacementMode, Thumb,
                                                DataGridColumnHeader)
from System.Windows.Media import VisualTreeHelper
from System.ComponentModel import ListSortDirection, SortDescription

BLANK = u"(Blank)"      # nhãn cho ô rỗng / None
MAX_ROWS = 400          # trần số dòng dựng trong popup — còn lại dùng search
POPUP_WIDTH = 280


def _sort_key(text):
    """Sắp giá trị: số ra số, chữ ra chữ (không phân biệt hoa thường), rỗng cuối."""
    if text == BLANK:
        return (2, 0.0, u"")
    try:
        return (0, float(str(text).replace(",", "").strip()), u"")
    except (ValueError, TypeError):
        return (1, 0.0, str(text).lower())


class ColumnFilterController(object):
    """Quản lý bộ lọc theo cột cho MỘT DataGrid."""

    def __init__(self, window, grid, columns, source, on_changed, status_setter=None):
        """
        window        — cửa sổ T3WPFWindow (FindResource lấy style T3.*)
        grid          — DataGrid được gắn bộ lọc
        columns       — [(header_text, attr_name hoặc callable(item)), ...]
        source        — callable() -> danh sách item gốc (chưa lọc)
        on_changed    — callable(), gọi lại mỗi khi bộ lọc đổi
        status_setter — callable(str) tuỳ chọn, báo trạng thái ra footer
        """
        self.window = window
        self.grid = grid
        self.source = source
        self.on_changed = on_changed
        self.status_setter = status_setter

        self._getters = {}
        for header, accessor in columns:
            self._getters[header] = accessor
        self._selected = {}     # header -> set(giá trị được giữ lại)
        self._popup = None
        self._rows = []         # [(value, CheckBox)] của popup đang mở
        self._pending = None    # header đang được nhấn giữ chuột

        # Bắt bằng cặp Preview mouse chứ KHÔNG bằng ButtonBase.ClickEvent:
        # DataGrid có class handler riêng cho click header (để sort) và có thể
        # đánh dấu Handled trước khi tới handler của mình — bắt ở tầng preview
        # thì luôn tới, và chặn luôn cả hành vi nhấn/sort của header.
        self.grid.PreviewMouseLeftButtonDown += self._on_header_down
        self.grid.PreviewMouseLeftButtonUp += self._on_header_up
        # Chốt chặn thứ hai cho sort, phòng khi click tới header theo đường khác.
        self.grid.Sorting += self._on_sorting

    # ── Truy vấn ────────────────────────────────────────────────────────
    def value_of(self, item, header):
        accessor = self._getters.get(header)
        if accessor is None:
            return BLANK
        try:
            raw = accessor(item) if callable(accessor) else getattr(item, accessor, None)
        except Exception:
            raw = None
        if raw is None:
            return BLANK
        text = u"{}".format(raw).strip()
        return text if text else BLANK

    def passes(self, item, skip=None):
        """True nếu item lọt qua mọi bộ lọc cột (trừ cột `skip`)."""
        for header, keep in self._selected.items():
            if header == skip or not keep:
                continue
            if self.value_of(item, header) not in keep:
                return False
        return True

    def is_active(self):
        return bool(self.active_columns())

    def active_columns(self):
        return sorted(h for h, keep in self._selected.items() if keep)

    def clear_all(self):
        """Bỏ toàn bộ bộ lọc cột (dùng khi Refresh / nạp lại dữ liệu)."""
        if not self._selected:
            return
        self._selected = {}
        self._close_popup()
        self.refresh_glyphs()

    # ── Sự kiện ─────────────────────────────────────────────────────────
    def _header_of(self, node):
        """Đi ngược visual tree tìm DataGridColumnHeader chứa `node`."""
        for _ in range(8):
            if node is None:
                return None
            if isinstance(node, DataGridRow):
                return None     # click rơi vào thân bảng, không phải header
            if isinstance(node, DataGridColumnHeader):
                return node
            try:
                node = VisualTreeHelper.GetParent(node)
            except Exception:
                return None
        return None

    def _target_header(self, args):
        """(header_element, header_text) nếu chuột đang ở trên một cột lọc được."""
        node = getattr(args, "OriginalSource", None)
        # Thanh kéo giãn cột nằm trong header — không được cướp chuột của nó.
        probe = node
        for _ in range(4):
            if probe is None:
                break
            if isinstance(probe, Thumb):
                return None, None
            try:
                probe = VisualTreeHelper.GetParent(probe)
            except Exception:
                break
        header_el = self._header_of(node)
        if header_el is None:
            return None, None
        column = getattr(header_el, "Column", None)
        if column is None:
            return None, None   # header lấp chỗ trống ở cuối bảng
        header = u"{}".format(column.Header or "")
        if header not in self._getters:
            return None, None
        return header_el, header

    def _on_header_down(self, sender, args):
        """Nuốt cú nhấn trên tên cột để header không sort/nhấn như nút."""
        header_el, header = self._target_header(args)
        if header_el is None:
            self._pending = None
            return
        self._close_popup()     # đang mở popup cột khác thì dọn trước
        self._pending = header
        args.Handled = True

    def _on_header_up(self, sender, args):
        """Nhả chuột đúng trên cột vừa nhấn → mở popup lọc.

        Mở ở lúc nhả (không phải lúc nhấn) vì Popup `StaysOpen=False` đóng theo
        sự kiện mouse-down bên ngoài; mở lúc nhấn thì nó tự tắt ngay.
        """
        pending = self._pending
        self._pending = None
        if not pending:
            return
        header_el, header = self._target_header(args)
        if header_el is None or header != pending:
            return
        args.Handled = True
        self._open_popup(header_el, header)

    def _on_sorting(self, sender, args):
        """Chặn sort mặc định ở các cột có bộ lọc (click = lọc, không phải sort)."""
        try:
            column = getattr(args, "Column", None)
            if column is None:
                return
            if u"{}".format(column.Header or "") in self._getters:
                args.Handled = True
        except Exception:
            pass

    # ── Popup ───────────────────────────────────────────────────────────
    def _res(self, key):
        try:
            return self.window.FindResource(key)
        except Exception:
            return None

    def _close_popup(self):
        if self._popup is not None:
            try:
                self._popup.IsOpen = False
            except Exception:
                pass
        self._popup = None
        self._rows = []

    def _distinct(self, header):
        """Giá trị phân biệt của cột, tính trên item lọt qua CÁC cột khác."""
        seen = set()
        try:
            items = list(self.source() or [])
        except Exception:
            items = []
        for item in items:
            if self.passes(item, skip=header):
                seen.add(self.value_of(item, header))
        return sorted(seen, key=_sort_key)

    def _sort_handler(self, header, mode):
        """Trả về handler Click cho một nút sort (mode: 'asc' / 'desc' / None)."""
        def handler(sender, args):
            self._sort_by(header, mode)
        return handler

    def _sort_by(self, header, mode):
        """Sắp bảng theo cột này — bù lại việc click header đã dành cho bộ lọc.

        mode None trả bảng về thứ tự mặc định do tool tự dựng (SortDescriptions
        rỗng nghĩa là hiển thị đúng thứ tự của collection nguồn).
        """
        path = self._getters.get(header)
        if not isinstance(path, str):
            return              # accessor là hàm, không có đường dẫn để sort
        try:
            view = self.grid.Items
            view.SortDescriptions.Clear()
            if mode:
                view.SortDescriptions.Add(SortDescription(
                    path, ListSortDirection.Descending if mode == "desc"
                    else ListSortDirection.Ascending))
            view.Refresh()
        except Exception:
            pass

    def _open_popup(self, anchor, header):
        self._close_popup()
        values = self._distinct(header)
        keep = self._selected.get(header)

        panel = Border()
        style = self._res("T3.Panel")
        if style is not None:
            panel.Style = style
        panel.Width = POPUP_WIDTH

        root = StackPanel()
        panel.Child = root

        if isinstance(self._getters.get(header), str):
            sort_row = StackPanel()
            sort_row.Orientation = Orientation.Horizontal
            sort_row.Margin = Thickness(0, 0, 0, 8)
            options = ((u"A → Z", "asc"), (u"Z → A", "desc"), (u"Default", None))
            for index, (label, mode) in enumerate(options):
                b = Button()
                s = self._res("T3.Button.Ghost")
                if s is not None:
                    b.Style = s
                b.Content = label
                if index < len(options) - 1:
                    b.Margin = Thickness(0, 0, 8, 0)
                b.Click += self._sort_handler(header, mode)
                sort_row.Children.Add(b)
            root.Children.Add(sort_row)

        search = TextBox()
        style = self._res("T3.Search")
        if style is not None:
            search.Style = style
        search.Tag = u"Search values..."
        search.Margin = Thickness(0, 0, 0, 8)
        root.Children.Add(search)

        select_all = CheckBox()
        style = self._res("T3.CheckBox")
        if style is not None:
            select_all.Style = style
        select_all.Content = u"(Select all)"
        select_all.FontWeight = FontWeights.SemiBold
        select_all.Margin = Thickness(0, 0, 0, 8)
        root.Children.Add(select_all)

        scroller = ScrollViewer()
        scroller.MaxHeight = 240
        scroller.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroller.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        rows_panel = StackPanel()
        scroller.Content = rows_panel
        root.Children.Add(scroller)

        hint = TextBlock()
        style = self._res("T3.Caption")
        if style is not None:
            hint.Style = style
        hint.TextWrapping = TextWrapping.Wrap
        hint.Margin = Thickness(0, 8, 0, 0)
        hint.Visibility = Visibility.Collapsed
        root.Children.Add(hint)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        buttons.Margin = Thickness(0, 12, 0, 0)
        btn_clear = Button()
        style = self._res("T3.Button.Ghost")
        if style is not None:
            btn_clear.Style = style
        btn_clear.Content = u"Clear"
        btn_clear.Margin = Thickness(0, 0, 8, 0)
        btn_done = Button()
        style = self._res("T3.Button.Secondary")
        if style is not None:
            btn_done.Style = style
        btn_done.Content = u"Done"
        buttons.Children.Add(btn_clear)
        buttons.Children.Add(btn_done)
        root.Children.Add(buttons)

        # `quiet` chặn vòng lặp sự kiện khi code tự set IsChecked. Không gỡ
        # handler bằng `-=` vì pythonnet bọc mỗi tham chiếu hàm thành một
        # delegate mới — gỡ có thể trượt và handler dính lại vĩnh viễn.
        state = {"keep": keep, "quiet": False}

        def sync_select_all():
            checked = [b for _, b in self._rows if b.IsChecked]
            state["quiet"] = True
            try:
                select_all.IsChecked = bool(self._rows) and len(checked) == len(self._rows)
            finally:
                state["quiet"] = False

        def commit():
            """Ghi lại lựa chọn — tick đủ mọi giá trị nghĩa là bỏ lọc cột."""
            visible = set(v for v, _ in self._rows)
            picked = set(v for v, box in self._rows if box.IsChecked)
            prev = state["keep"]
            # Giá trị đang bị ô search giấu đi vẫn giữ nguyên trạng thái cũ.
            for value in values:
                if value in visible:
                    continue
                if prev is None or value in prev:
                    picked.add(value)
            if not picked:
                # Trạng thái trung gian: vừa bỏ tick "(Select all)" để đi tick lại
                # vài giá trị. Không áp bộ lọc rỗng (bảng trắng là vô dụng) nhưng
                # cũng KHÔNG tự tick lại, để người dùng chọn tiếp.
                self._selected.pop(header, None)
                state["keep"] = picked
            elif len(picked) >= len(values):
                self._selected.pop(header, None)
                state["keep"] = None
            else:
                self._selected[header] = picked
                state["keep"] = picked
            self.refresh_glyphs()
            self._report()
            if self.on_changed:
                self.on_changed()

        def on_row_toggle(sender, args):
            if state["quiet"]:
                return
            sync_select_all()
            commit()

        def on_select_all(sender, args):
            if state["quiet"]:
                return
            checked = bool(select_all.IsChecked)
            state["quiet"] = True
            try:
                for _, box in self._rows:
                    box.IsChecked = checked
            finally:
                state["quiet"] = False
            commit()

        def build(sender=None, args=None):
            needle = (search.Text or u"").lower()
            matches = [v for v in values if not needle or needle in v.lower()]
            rows_panel.Children.Clear()
            self._rows = []
            current = state["keep"]
            for value in matches[:MAX_ROWS]:
                box = CheckBox()
                item_style = self._res("T3.Filter.Item")
                if item_style is not None:
                    box.Style = item_style
                box.Content = value
                box.IsChecked = (current is None) or (value in current)
                box.Checked += on_row_toggle
                box.Unchecked += on_row_toggle
                rows_panel.Children.Add(box)
                self._rows.append((value, box))
            if not values:
                hint.Text = u"No values to filter in this column."
                hint.Visibility = Visibility.Visible
            elif len(matches) > len(self._rows):
                hint.Text = u"Showing {} of {} values — refine the search.".format(
                    len(self._rows), len(matches))
                hint.Visibility = Visibility.Visible
            elif not matches:
                hint.Text = u"No value matches the search."
                hint.Visibility = Visibility.Visible
            else:
                hint.Visibility = Visibility.Collapsed
            sync_select_all()

        def on_clear(sender, args):
            self._selected.pop(header, None)
            state["keep"] = None
            build()
            self.refresh_glyphs()
            self._report()
            if self.on_changed:
                self.on_changed()

        def on_done(sender, args):
            self._close_popup()

        search.TextChanged += build
        select_all.Checked += on_select_all
        select_all.Unchecked += on_select_all
        btn_clear.Click += on_clear
        btn_done.Click += on_done

        build()

        popup = Popup()
        popup.Child = panel
        popup.PlacementTarget = anchor
        popup.Placement = PlacementMode.Bottom
        popup.VerticalOffset = 2
        popup.StaysOpen = False
        popup.AllowsTransparency = True
        try:
            popup.Closed += self._on_popup_closed
        except Exception:
            pass
        self._popup = popup
        popup.IsOpen = True
        try:
            search.Focus()
        except Exception:
            pass

    def _on_popup_closed(self, sender=None, args=None):
        self._popup = None
        self._rows = []

    # ── Dấu hiệu cột đang lọc ───────────────────────────────────────────
    def refresh_glyphs(self):
        """Tô đậm TÊN của những cột đang lọc — không thêm icon nào lên header."""
        active = self._res("T3.Ink")
        idle = self._res("T3.TextDisabled")
        for head in _column_headers(self.grid):
            column = getattr(head, "Column", None)
            if column is None:
                continue
            header = u"{}".format(column.Header or "")
            if header not in self._getters:
                continue
            on = bool(self._selected.get(header))
            try:
                if active is not None and idle is not None:
                    head.Foreground = active if on else idle
                head.FontWeight = FontWeights.Bold if on else FontWeights.SemiBold
                head.ToolTip = (u"Filtered — click the name to change"
                                if on else u"Click the column name to filter")
            except Exception:
                pass

    def _report(self):
        if not self.status_setter:
            return
        cols = self.active_columns()
        if cols:
            self.status_setter(u"Column filter on: {}".format(u", ".join(cols)))
        else:
            self.status_setter(u"Ready")


def _column_headers(root):
    """Duyệt visual tree lấy mọi DataGridColumnHeader.

    Không chui vào DataGridRow: header không nằm trong đó, còn thân bảng có thể
    có hàng nghìn node — duyệt hết là phí trên mỗi lần lọc.
    """
    found = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        if isinstance(node, DataGridRow):
            continue
        if isinstance(node, DataGridColumnHeader):
            found.append(node)
            continue
        try:
            count = VisualTreeHelper.GetChildrenCount(node)
        except Exception:
            continue
        for i in range(count):
            try:
                stack.append(VisualTreeHelper.GetChild(node, i))
            except Exception:
                pass
    return found
