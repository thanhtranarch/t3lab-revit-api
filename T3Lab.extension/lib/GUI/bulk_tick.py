# -*- coding: utf-8 -*-
"""Bulk tick — mouse/keyboard gestures for a row-checkbox column.

Bật bằng một dòng trong window kế thừa T3WPFWindow:

    self.enable_bulk_tick(self.dg_parameters, "is_selected",
                          on_change=self._update_selection_count)

Cử chỉ (chỉ cho cột checkbox đi qua string bridge — luật 24):
  * Click checkbox            -> đảo tick dòng đó (như trước), dòng thành "anchor".
  * Shift+click checkbox      -> mọi dòng từ anchor tới dòng vừa click nhận
                                 trạng thái của anchor (theo thứ tự đang hiển
                                 thị: sort + filter).
  * Nhấn trên checkbox + kéo  -> "tô": mỗi dòng đi qua nhận trạng thái mới của
                                 dòng đầu tiên. Kéo sát mép trên/dưới thì bảng
                                 tự cuộn. Thả chuột là xong.
  * Bôi đen dòng (click/Ctrl/Shift/kéo trên phần còn lại của dòng — hành vi
    gốc của DataGrid) rồi Space -> tick hết các dòng bôi đen; nếu tất cả đã
    tick thì bỏ tick hết.
  * Chuột phải -> menu: Tick selected rows / Untick selected rows /
    Tick all visible / Untick all / Invert ticks.

Sau mỗi thao tác: checkbox select-all ở header được đồng bộ
(checked / unchecked / indeterminate) rồi gọi `on_change()`.

Phần logic thuần (tính khoảng, giá trị, đổi cờ) không đụng .NET để test
được ngoài Revit: dev/test_bulk_tick.py.
"""

__author__ = "Tran Tien Thanh"


# ── PURE LOGIC (no .NET) ─────────────────────────────────────────────────────

def read_flag(row, prop):
    """bool của `row.prop`, hoặc None nếu dòng không có thuộc tính đó
    (placeholder "new item" của DataGrid, DisconnectedItem…)."""
    try:
        return bool(getattr(row, prop))
    except Exception:
        return None


def index_of(rows, row):
    """Vị trí của `row` trong `rows` theo identity (fallback ==), -1 nếu không có."""
    if row is None:
        return -1
    for i, r in enumerate(rows):
        if r is row:
            return i
    for i, r in enumerate(rows):
        try:
            if r == row:
                return i
        except Exception:
            continue
    return -1


def tick_span(count, a, b):
    """Chỉ số từ a tới b (hai đầu tính cả, không phụ thuộc chiều), kẹp vào [0, count)."""
    if not count or a is None or b is None or a < 0 or b < 0:
        return []
    lo, hi = (a, b) if a <= b else (b, a)
    lo = max(0, lo)
    hi = min(count - 1, hi)
    if lo > hi:
        return []
    return list(range(lo, hi + 1))


def apply_tick(rows, prop, value):
    """Đặt `prop = value` trên các dòng; trả về list dòng THỰC SỰ đổi."""
    value = bool(value)
    changed = []
    for row in rows:
        cur = read_flag(row, prop)
        if cur is None or cur == value:
            continue
        try:
            setattr(row, prop, value)
            changed.append(row)
        except Exception:
            continue
    return changed


def toggle_value(rows, prop):
    """Giá trị Space áp cho cả nhóm: True nếu còn dòng chưa tick, False nếu
    tất cả đã tick (giống Explorer/Gmail: một nhịp nhất quán, không đảo lẻ)."""
    flags = [f for f in (read_flag(r, prop) for r in rows) if f is not None]
    if not flags:
        return None
    return not all(flags)


def invert_ticks(rows, prop):
    """Đảo tick từng dòng; trả về list dòng đã đổi."""
    changed = []
    for row in rows:
        cur = read_flag(row, prop)
        if cur is None:
            continue
        try:
            setattr(row, prop, not cur)
            changed.append(row)
        except Exception:
            continue
    return changed


def range_tick(rows, prop, anchor, target):
    """Shift+click: dòng anchor..target nhận trạng thái hiện tại của anchor.

    Trả về (value, changed). value là None khi không làm được (anchor không
    còn hiển thị, target không có trong bảng) — caller xử lý như click thường.
    """
    a = index_of(rows, anchor)
    b = index_of(rows, target)
    if a < 0 or b < 0:
        return None, []
    value = read_flag(anchor, prop)
    if value is None:
        return None, []
    span = tick_span(len(rows), a, b)
    return value, apply_tick([rows[i] for i in span], prop, value)


def paint_step(rows, prop, last_index, index, value):
    """Kéo tô: phủ mọi dòng giữa vị trí trước và vị trí hiện tại (chuột đi nhanh
    nhảy cóc vài dòng thì vẫn không sót dòng nào). Trả về list dòng đã đổi."""
    if index is None or index < 0:
        return []
    if last_index is None or last_index < 0:
        last_index = index
    span = tick_span(len(rows), last_index, index)
    return apply_tick([rows[i] for i in span], prop, value)


def edge_direction(y, top, bottom, margin):
    """-1 = cuộn lên, 1 = cuộn xuống, 0 = đứng yên, theo vị trí chuột y."""
    if y < top + margin:
        return -1
    if y > bottom - margin:
        return 1
    return 0


def tri_state(flags):
    """True (tất cả) / False (không có hoặc rỗng) / None (một phần)."""
    flags = [bool(f) for f in flags if f is not None]
    if not flags or not any(flags):
        return False
    if all(flags):
        return True
    return None


# ── WPF CONTROLLER ───────────────────────────────────────────────────────────

MENU_TICK_SELECTED = "Tick selected rows"
MENU_UNTICK_SELECTED = "Untick selected rows"
MENU_TICK_ALL = "Tick all visible"
MENU_UNTICK_ALL = "Untick all"
MENU_INVERT = "Invert ticks"


class BulkTick(object):
    """Gắn cử chỉ tick hàng loạt vào một DataGrid / ListBox / ListView.

    Không kế thừa CLR class (luật S18) — chỉ giữ tham chiếu tới window, grid
    và các delegate. Window giữ object này (`_t3_bulk_tick`) để GC không thu.
    """

    EDGE = 16           # px sát mép trên/dưới vùng dòng thì tự cuộn
    SCROLL_MS = 60      # nhịp tự cuộn khi giữ chuột đứng yên ở mép
    BULK_REFRESH = 32   # đổi nhiều hơn chừng này dòng thì làm mới theo container đang hiện

    def __init__(self, window, grid, prop, on_change=None, header=None,
                 context_menu=True):
        self.window = window
        self.grid = grid
        self.prop = prop
        self.on_change = on_change
        self.header = header
        self._anchor = None
        self._dragging = False
        self._paint_value = True
        self._last_index = -1
        self._timer = None
        self._menu_items = {}
        self._attach(context_menu)

    # ── wiring ──────────────────────────────────────────────────────────

    def _attach(self, context_menu):
        g = self.grid
        g.PreviewMouseLeftButtonDown += self._on_down
        g.PreviewMouseMove += self._on_move
        g.PreviewMouseLeftButtonUp += self._on_up
        g.LostMouseCapture += self._on_lost_capture
        g.PreviewKeyDown += self._on_key
        if context_menu:
            try:
                self._build_menu()
            except Exception:
                pass

    def _build_menu(self):
        from System.Windows.Controls import ContextMenu, MenuItem, Separator
        menu = self.grid.ContextMenu
        if menu is None:
            menu = ContextMenu()
            self.grid.ContextMenu = menu
        elif menu.Items.Count:
            menu.Items.Add(Separator())
        spec = (
            (MENU_TICK_SELECTED, self._menu_tick_selected,
             "Tick every highlighted row (Space toggles them too)"),
            (MENU_UNTICK_SELECTED, self._menu_untick_selected,
             "Untick every highlighted row"),
            None,
            (MENU_TICK_ALL, self._menu_tick_all,
             "Tick every row the current filter shows"),
            (MENU_UNTICK_ALL, self._menu_untick_all,
             "Untick every row the current filter shows"),
            (MENU_INVERT, self._menu_invert,
             "Swap ticked and unticked rows the current filter shows"),
        )
        for entry in spec:
            if entry is None:
                menu.Items.Add(Separator())
                continue
            text, handler, tip = entry
            item = MenuItem()
            item.Header = text
            item.ToolTip = tip
            item.Click += handler
            menu.Items.Add(item)
            self._menu_items[text] = item
        menu.Opened += self._on_menu_opened

    # ── helpers ─────────────────────────────────────────────────────────

    def _rows(self):
        try:
            return list(self.grid.Items)
        except Exception:
            return []

    def _highlighted(self):
        try:
            sel = list(self.grid.SelectedItems)
        except Exception:
            try:
                sel = [self.grid.SelectedItem] if self.grid.SelectedItem is not None else []
            except Exception:
                sel = []
        return [r for r in sel if read_flag(r, self.prop) is not None]

    @staticmethod
    def _parent(node):
        try:
            from System.Windows.Media import VisualTreeHelper, Visual
            from System.Windows import LogicalTreeHelper
            if isinstance(node, Visual):
                return VisualTreeHelper.GetParent(node)
            return LogicalTreeHelper.GetParent(node)
        except Exception:
            return None

    def _row_checkbox(self, source):
        """CheckBox bridged trên đúng `prop` chứa `source`, hoặc None.

        Checkbox select-all ở header không bridged nên tự rơi ra ngoài.
        """
        from System.Windows.Controls import CheckBox, TextBlock
        from System.Windows.Controls.Primitives import ToggleButton
        from System.Windows.Data import BindingOperations
        from GUI.WPF_Base import bridged_row_property
        node = source
        while node is not None and not self._same(node, self.grid):
            if isinstance(node, CheckBox):
                prop, _bridge = bridged_row_property(
                    node, BindingOperations, ToggleButton.IsCheckedProperty,
                    TextBlock.TextProperty)
                return node if prop == self.prop else None
            node = self._parent(node)
        return None

    def _same(self, a, b):
        """Cùng một object .NET? (wrapper pythonnet không bảo đảm `is`)."""
        if a is b:
            return True
        try:
            from System import Object
            return a is not None and b is not None and Object.ReferenceEquals(a, b)
        except Exception:
            return False

    def _bfs(self, accept):
        from System.Windows.Media import VisualTreeHelper
        queue = [self.grid]
        while queue:
            node = queue.pop(0)
            try:
                if accept(node):
                    return node
                for i in range(VisualTreeHelper.GetChildrenCount(node)):
                    queue.append(VisualTreeHelper.GetChild(node, i))
            except Exception:
                continue
        return None

    def _items_host(self):
        """Panel chứa container dòng của CHÍNH grid (DataGridRowsPresenter,
        VirtualizingStackPanel…) — không phải panel của header cột hay của
        từng dòng, cũng là IsItemsHost nhưng thuộc ItemsControl khác. Cache."""
        host = getattr(self, '_host_cache', None)
        if host is not None:
            return host
        from System.Windows.Controls import Panel, ItemsControl

        def accept(node):
            return (isinstance(node, Panel) and node.IsItemsHost and
                    self._same(ItemsControl.GetItemsOwner(node), self.grid))
        host = self._bfs(accept)
        self._host_cache = host
        return host

    def _scroll_viewer(self):
        sv = getattr(self, '_sv_cache', None)
        if sv is not None:
            return sv
        from System.Windows.Controls import ScrollViewer
        sv = None
        try:
            sv = self.grid.Template.FindName("DG_ScrollViewer", self.grid)
        except Exception:
            sv = None
        if not isinstance(sv, ScrollViewer):
            sv = self._bfs(lambda node: isinstance(node, ScrollViewer))
        self._sv_cache = sv
        return sv

    def _item_at(self, point):
        """Dòng dưới điểm `point` (toạ độ của grid), hoặc None."""
        from System.Windows.Media import VisualTreeHelper
        from System.Windows.Controls import ItemsControl
        from System.Windows import DependencyProperty
        try:
            hit = VisualTreeHelper.HitTest(self.grid, point)
        except Exception:
            return None
        node = getattr(hit, 'VisualHit', None) if hit is not None else None
        if node is None:
            return None
        try:
            container = ItemsControl.ContainerFromElement(self.grid, node)
        except Exception:
            return None
        if container is None:
            return None
        try:
            item = self.grid.ItemContainerGenerator.ItemFromContainer(container)
        except Exception:
            return None
        if item is None or item is DependencyProperty.UnsetValue:
            return None
        return item

    def _bridge_in(self, container):
        """TextBlock bridge của `prop` trong một container dòng (DFS, dừng sớm)."""
        from System.Windows.Media import VisualTreeHelper
        from System.Windows.Controls import TextBlock
        from System.Windows.Data import BindingOperations
        stack = [container]
        while stack:
            node = stack.pop()
            try:
                if isinstance(node, TextBlock):
                    b = BindingOperations.GetBinding(node, TextBlock.TextProperty)
                    if b is not None and not b.ElementName and b.Path is not None \
                            and b.Path.Path == self.prop:
                        return node
                n = VisualTreeHelper.GetChildrenCount(node)
                # Đẩy ngược để duyệt trái -> phải: cột checkbox thường là cột đầu.
                for i in range(n - 1, -1, -1):
                    stack.append(VisualTreeHelper.GetChild(node, i))
            except Exception:
                continue
        return None

    def _update_container(self, container):
        from System.Windows.Controls import TextBlock
        from System.Windows.Data import BindingOperations
        bridge = self._bridge_in(container)
        if bridge is None:
            return
        expr = BindingOperations.GetBindingExpression(bridge, TextBlock.TextProperty)
        if expr is not None:
            expr.UpdateTarget()

    def _refresh(self, changed):
        """Đọc lại dòng vào bridge của các container ĐANG hiện.

        Dòng chưa realize (ảo hoá) không cần làm gì: khi cuộn tới, container
        nhận DataContext mới và binding đọc thẳng từ dòng. Không dùng
        Items.Refresh() vì nó dựng lại mọi container (chậm khi kéo tô) và có
        thể làm mất các dòng đang bôi đen.
        """
        if not changed:
            return
        try:
            gen = self.grid.ItemContainerGenerator
            if len(changed) <= self.BULK_REFRESH:
                for row in changed:
                    container = gen.ContainerFromItem(row)
                    if container is not None:
                        self._update_container(container)
                return
            host = self._items_host()
            if host is None:
                raise RuntimeError("no items host")
            for child in list(host.Children):
                self._update_container(child)
        except Exception:
            try:
                self.grid.Items.Refresh()
            except Exception:
                pass

    def _notify(self):
        header = self.header
        if header is None:
            try:
                header = getattr(self.window, 'chk_all_' + (self.grid.Name or ''), None)
            except Exception:
                header = None
        if header is not None:
            try:
                header.IsThreeState = False
                header.IsChecked = tri_state(
                    read_flag(r, self.prop) for r in self._rows())
            except Exception:
                pass
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    def _commit(self, changed):
        if changed:
            self._refresh(changed)
            self._notify()

    @staticmethod
    def _shift_down():
        from System.Windows.Input import Keyboard, Key
        return Keyboard.IsKeyDown(Key.LeftShift) or Keyboard.IsKeyDown(Key.RightShift)

    # ── mouse: click / shift+click / drag-paint ─────────────────────────

    def _on_down(self, sender, e):
        try:
            checkbox = self._row_checkbox(e.OriginalSource)
            if checkbox is None:
                return                  # ngoài cột tick: để DataGrid bôi đen như cũ
            row = checkbox.DataContext
            rows = self._rows()
            idx = index_of(rows, row)
            if idx < 0 or read_flag(row, self.prop) is None:
                return
            e.Handled = True            # checkbox không tự đảo — ta đảo một lần
            try:
                self.grid.Focus()       # để Space đi tiếp vào grid
            except Exception:
                pass
            if self._shift_down() and self._anchor is not None:
                value, changed = range_tick(rows, self.prop, self._anchor, row)
                if value is not None:
                    self._commit(changed)
                    return
            value = not read_flag(row, self.prop)
            changed = apply_tick([row], self.prop, value)
            self._anchor = row
            self._dragging = True
            self._paint_value = value
            self._last_index = idx
            try:
                self.grid.CaptureMouse()
            except Exception:
                pass
            self._start_timer()
            self._commit(changed)
        except Exception:
            pass

    def _rows_band(self):
        """(top, bottom) của vùng dòng theo toạ độ grid (dưới header cột)."""
        top = 0.0
        try:
            from System.Windows import Point
            host = self._items_host()
            if host is not None:
                top = max(0.0, host.TranslatePoint(Point(0, 0), self.grid).Y)
        except Exception:
            pass
        bottom = float(self.grid.ActualHeight)
        try:
            from System.Windows import Visibility
            sv = self._scroll_viewer()
            if sv is not None and sv.ComputedHorizontalScrollBarVisibility == Visibility.Visible:
                bottom -= 16
        except Exception:
            pass
        return top, bottom

    def _paint_at(self, pos):
        from System.Windows import Point
        top, bottom = self._rows_band()
        y = min(max(pos.Y, top + 2), bottom - 2)
        x = min(max(pos.X, 2), max(2.0, float(self.grid.ActualWidth) - 2))
        item = self._item_at(Point(x, y))
        if item is None:
            return
        rows = self._rows()
        idx = index_of(rows, item)
        if idx < 0:
            return
        changed = paint_step(rows, self.prop, self._last_index, idx, self._paint_value)
        self._last_index = idx
        self._commit(changed)

    def _on_move(self, sender, e):
        if not self._dragging:
            return
        try:
            from System.Windows.Input import MouseButtonState
            if e.LeftButton != MouseButtonState.Pressed:
                self._end_drag()
                return
            self._paint_at(e.GetPosition(self.grid))
        except Exception:
            pass

    def _on_up(self, sender, e):
        if not self._dragging:
            return
        try:
            e.Handled = True
        except Exception:
            pass
        self._end_drag()

    def _on_lost_capture(self, sender, e):
        if self._dragging:
            self._end_drag(release=False)

    def _end_drag(self, release=True):
        self._dragging = False
        self._last_index = -1
        self._stop_timer()
        if release:
            try:
                if self.grid.IsMouseCaptured:
                    self.grid.ReleaseMouseCapture()
            except Exception:
                pass

    # ── auto-scroll khi kéo sát mép ─────────────────────────────────────

    def _start_timer(self):
        try:
            if self._timer is None:
                from System import TimeSpan
                from System.Windows.Threading import DispatcherTimer
                self._timer = DispatcherTimer()
                self._timer.Interval = TimeSpan.FromMilliseconds(self.SCROLL_MS)
                self._timer.Tick += self._on_tick
            self._timer.Start()
        except Exception:
            self._timer = None

    def _stop_timer(self):
        try:
            if self._timer is not None:
                self._timer.Stop()
        except Exception:
            pass

    def _on_tick(self, sender, e):
        if not self._dragging:
            self._stop_timer()
            return
        try:
            from System.Windows.Input import Mouse
            pos = Mouse.GetPosition(self.grid)
            top, bottom = self._rows_band()
            direction = edge_direction(pos.Y, top, bottom, self.EDGE)
            if not direction:
                return
            sv = self._scroll_viewer()
            if sv is None:
                return
            if direction < 0:
                sv.LineUp()
            else:
                sv.LineDown()
            self.grid.UpdateLayout()
            self._paint_at(pos)
        except Exception:
            pass

    # ── keyboard: Space trên các dòng bôi đen ───────────────────────────

    def _on_key(self, sender, e):
        try:
            from System.Windows.Input import Key, Keyboard, ModifierKeys
            from System.Windows.Controls import TextBox
            from System.Windows.Controls.Primitives import ToggleButton
            # `ModifierKeys.None` là SyntaxError trong Python — đọc qua getattr.
            if e.Key != Key.Space or Keyboard.Modifiers != getattr(ModifierKeys, 'None'):
                return
            src = e.OriginalSource
            if isinstance(src, TextBox):
                return
            # Checkbox đang giữ focus bàn phím: để nó tự đảo như cũ.
            if isinstance(src, ToggleButton) and self._row_checkbox(src) is not None:
                return
            rows = self._highlighted()
            value = toggle_value(rows, self.prop)
            if value is None:
                return
            e.Handled = True
            self._commit(apply_tick(rows, self.prop, value))
        except Exception:
            pass

    # ── context menu ────────────────────────────────────────────────────

    def _on_menu_opened(self, sender, e):
        try:
            has_sel = bool(self._highlighted())
            has_rows = any(read_flag(r, self.prop) is not None for r in self._rows())
            for key in (MENU_TICK_SELECTED, MENU_UNTICK_SELECTED):
                if key in self._menu_items:
                    self._menu_items[key].IsEnabled = has_sel
            for key in (MENU_TICK_ALL, MENU_UNTICK_ALL, MENU_INVERT):
                if key in self._menu_items:
                    self._menu_items[key].IsEnabled = has_rows
        except Exception:
            pass

    def _menu_tick_selected(self, sender, e):
        self._commit(apply_tick(self._highlighted(), self.prop, True))

    def _menu_untick_selected(self, sender, e):
        self._commit(apply_tick(self._highlighted(), self.prop, False))

    def _menu_tick_all(self, sender, e):
        self._commit(apply_tick(self._rows(), self.prop, True))

    def _menu_untick_all(self, sender, e):
        self._commit(apply_tick(self._rows(), self.prop, False))

    def _menu_invert(self, sender, e):
        self._commit(invert_ticks(self._rows(), self.prop))
