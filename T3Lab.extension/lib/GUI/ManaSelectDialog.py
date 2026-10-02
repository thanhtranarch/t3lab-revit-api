# -*- coding: utf-8 -*-
"""ManaSelect — Explore: cây phần tử có đếm (Category -> Family -> Type).

2026-10-02: tool chỉ còn MỘT chức năng. Bốn mode cũ đã bỏ vì trùng tool khác:
  - Quick Select   — trùng Explore (Filter + Search) và Filter của Revit
  - Select Similar — trùng Explore (tick một Type/Family) và "Select All
                     Instances" có sẵn của Revit
  - On Sheets      — CAD import trên sheet đã có ở ManaDWG
  - Warnings       — trùng tab Warnings của ModelAuditor (đầy đủ hơn)

Logic Revit nằm ở `Selection.explorer` (không import WPF); file này chỉ dựng
cây, xử lý tick và gọi hành động.
"""

import csv
import io
import json
import os
import sys

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

import System
from System.Windows import RoutedEventHandler, Thickness, Visibility, WindowState
from System.Windows.Controls import (
    CheckBox,
    Orientation,
    StackPanel,
    TextBlock,
    TreeViewItem,
)
from System.Windows.Input import Cursors, Key
from System.Windows.Media import VisualTreeHelper
from System.Windows.Threading import DispatcherTimer

from Autodesk.Revit.DB import TemporaryViewMode, Transaction
from Autodesk.Revit.UI import ExternalEvent, IExternalEventHandler, TaskDialog
from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import forms, revit

# Parent của GUI/ để import được `GUI.*` và `Selection.*`.
_LIB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LIB_DIR not in sys.path:
    sys.path.append(_LIB_DIR)

from GUI.WPF_Base import T3WPFWindow
from GUI import T3Dialog
from Selection import explorer

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'ManaSelect.xaml')

# Gõ Search thì đợi người dùng ngừng tay rồi mới dựng lại cây — bản cũ dựng lại
# ở MỖI phím, model 8000 phần tử là giật theo từng chữ.
_SEARCH_DELAY_MS = 250

# Display / Sort by / Filter lần trước — tiện ích cho người dùng, mất cũng không
# sao (đọc/ghi luôn bọc try).
_SETTINGS_PATH = os.path.join(os.environ.get('APPDATA', ''), 'T3LabAI',
                              'mana_select.json')


def _load_settings():
    try:
        with io.open(_SETTINGS_PATH, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_settings(data):
    try:
        folder = os.path.dirname(_SETTINGS_PATH)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with io.open(_SETTINGS_PATH, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


class _ActionHandler(IExternalEventHandler):
    """Chạy một callable trên thread API của Revit.

    BẮT BUỘC cho cửa sổ modeless: handler của WPF nằm NGOÀI Revit API context,
    gọi thẳng `Transaction` hay `uidoc.Selection` từ đó là
    "Attempting to access Revit API outside of API context".

    `__namespace__` TĨNH là đúng ở đây vì class nằm trong `lib/` — nó chỉ được
    định nghĩa một lần cho cả phiên Revit. (Luật S15: namespace động `uuid` chỉ
    cần khi class sống trong `script.py`, nơi click thứ hai sẽ định nghĩa lại.)
    """

    __namespace__ = "T3Lab.ManaSelectActionHandler"

    def __init__(self, owner=None):
        self._action = None
        self._owner = owner

    def set_action(self, action):
        self._action = action

    def Execute(self, uiapp):
        action = self._action
        self._action = None
        if action is None:
            return
        # Bên trong Execute() ta ĐANG ở trong API context. Bật cờ để mọi
        # `_run_in_revit` lồng bên trong gọi THẲNG thay vì Raise() thêm một
        # lần: Raise lồng nhau đẩy việc sang chu kỳ idle sau, và vì
        # `set_action` ghi đè nên việc đang chờ có thể bị mất trắng.
        owner = self._owner
        if owner is not None:
            owner._in_api_context = True
        try:
            action()
        except Exception as ex:
            # Ngoại lệ thoát ra khỏi Execute() làm Revit crash native, nên
            # chặn ở đây và báo bằng TaskDialog (T3Dialog có thể chưa dựng được
            # nếu cửa sổ đang đóng).
            try:
                import traceback
                TaskDialog.Show('ManaSelect',
                                'Action failed:\n%s\n\n%s'
                                % (ex, traceback.format_exc()))
            except Exception:
                pass
        finally:
            if owner is not None:
                owner._in_api_context = False

    def GetName(self):
        return "T3Lab ManaSelect action"


class _NodeBinding(object):
    """Cầu nối TreeViewItem <-> explorer.Node.

    Là class Python THUẦN, không kế thừa CLR — luật S18 chỉ cấm class kế thừa
    T3WPFWindow/Window kế thừa thêm mixin, một holder rời như thế này thì
    không liên quan.
    """

    __slots__ = ('node', 'item', 'checkbox', 'parent', 'children', 'built')

    def __init__(self, node, item, checkbox, parent):
        self.node = node
        self.item = item
        self.checkbox = checkbox
        self.parent = parent
        self.children = []
        self.built = False


class ManaSelectWindow(T3WPFWindow):
    def __init__(self):
        T3WPFWindow.__init__(self, _XAML)
        self.uidoc = revit.uidoc
        self.doc = revit.doc

        self._loading = True            # chặn event trong lúc nạp combo box

        # ── Modeless plumbing ────────────────────────────────────────────
        # `_in_api_context` True suốt __init__: script.py đang chạy BÊN TRONG
        # một external command của Revit, nên lúc này gọi API thẳng là hợp lệ
        # và lần nạp đầu tiên không cần đi qua ExternalEvent (nếu đi qua thì
        # cửa sổ mở ra trắng tới khi Revit rảnh — phạm luật S7).
        # `show_dialog()` hạ cờ này xuống ngay trước khi Show().
        self._modeless = False
        self._in_api_context = True
        self._action_handler = _ActionHandler(self)
        self._action_event = ExternalEvent.Create(self._action_handler)

        self._records = []
        self._explore_root = None
        self._explore_binding = None

        self._search_timer = DispatcherTimer()
        self._search_timer.Interval = System.TimeSpan.FromMilliseconds(_SEARCH_DELAY_MS)
        self._search_timer.Tick += self._on_search_timer_tick

        self._init_explore_controls()
        self._wire_events()

        # Nạp dữ liệu NGAY trong __init__ (luật S7) — không đợi Loaded event,
        # nếu không tool mở ra là một trang trắng.
        self._loading = False
        self._reload_explore()

    # =========================================================================
    # SETUP
    # =========================================================================
    def _res(self, key):
        """Resource T3 theo tên chuẩn dot-notation, không bao giờ ném (luật 21)."""
        try:
            return self.FindResource(key)
        except Exception:
            return None

    def _run_in_revit(self, action):
        """Chạy `action` ở nơi Revit API dùng được.

        Modal (hoặc đang trong __init__): ta ĐANG ở trong API context, gọi thẳng.

        Modeless: đẩy qua ExternalEvent, Revit gọi lại khi nó rảnh. `Raise()`
        là BẤT ĐỒNG BỘ — tuyệt đối không đọc kết quả ở dòng sau nó; mọi thứ
        phụ thuộc kết quả phải nằm BÊN TRONG `action`. Execute() chạy trên
        thread chính của Revit, cũng là thread UI của WPF, nên `action` được
        phép cập nhật control luôn.

        Hai lần Raise liên tiếp: `set_action` ghi đè, Revit gộp lại một lần
        gọi — action sau thắng. Đó là hành vi đúng cho nạp lại cây, vì action
        đọc giá trị combo box lúc THỰC THI chứ không phải lúc xếp hàng.
        """
        if not self._modeless or self._in_api_context:
            action()
            return
        self._action_handler.set_action(action)
        self._action_event.Raise()

    @staticmethod
    def _pick_saved(combo, options, saved):
        """Chọn lại giá trị đã lưu nếu nó còn trong danh sách, không thì mục đầu."""
        combo.SelectedIndex = options.index(saved) if saved in options else 0

    def _init_explore_controls(self):
        """Nạp 3 combo box, khôi phục lựa chọn lần trước. `_loading` chặn
        SelectionChanged nổ ra giữa lúc gán, nếu không mỗi Add là một lần
        rebuild cây."""
        saved = _load_settings()
        self._loading = True
        try:
            for combo, options, key in (
                    (self.cbo_explore_scope, explorer.SCOPES, 'scope'),
                    (self.cbo_explore_group, explorer.GROUP_ORDER, 'group'),
                    (self.cbo_explore_filter, explorer.FILTER_ORDER, 'filter')):
                for option in options:
                    combo.Items.Add(option)
                self._pick_saved(combo, list(options), saved.get(key))
        finally:
            self._loading = False

    def _remember_query(self):
        _save_settings({
            'scope': str(self.cbo_explore_scope.SelectedItem or ''),
            'group': str(self.cbo_explore_group.SelectedItem or ''),
            'filter': str(self.cbo_explore_filter.SelectedItem or ''),
        })

    def _wire_events(self):
        # Chrome (min/max/close) do T3WPFWindow tự nối trong
        # _wire_window_controls() — nối tay thêm một lần nữa làm Maximize toggle
        # hai nhịp và trở thành no-op.

        # Query bar
        self.cbo_explore_scope.SelectionChanged += self._on_explore_query_changed
        self.cbo_explore_filter.SelectionChanged += self._on_explore_query_changed
        self.cbo_explore_group.SelectionChanged += self._on_explore_group_changed
        self.txt_explore_search.TextChanged += self._on_explore_search_changed

        # Action bar
        self.btn_explore_refresh.Click += self._on_explore_refresh
        self.btn_explore_pick.Click += self._on_explore_pick
        self.btn_explore_zoom.Click += self._on_explore_zoom
        self.btn_explore_isolate.Click += self._on_explore_isolate
        self.btn_explore_hide.Click += self._on_explore_hide
        self.btn_explore_reset.Click += self._on_explore_reset
        self.btn_explore_export.Click += self._on_explore_export
        self.btn_explore_delete.Click += self._on_explore_delete

        # Tally strip
        self.btn_explore_all.Click += self._on_check_all
        self.btn_explore_none.Click += self._on_check_none
        self.btn_explore_expand.Click += self._on_explore_expand_all
        self.btn_explore_collapse.Click += self._on_explore_collapse_all

        # Tree
        self.tree_explore.SelectedItemChanged += self._on_explore_node_highlighted
        # Lazy expand: Expanded là routed event nên một handler trên chính
        # TreeView phục vụ mọi node, không phải nối vào từng item.
        self.tree_explore.AddHandler(TreeViewItem.ExpandedEvent,
                                     RoutedEventHandler(self._on_item_expanded))
        # Nháy đúp = hiện trong model. Space = tick hàng đang trỏ.
        self.tree_explore.PreviewMouseDoubleClick += self._on_tree_double_click
        self.tree_explore.PreviewKeyDown += self._on_tree_key_down

        # Footer
        self.btn_apply.Click += self._on_apply
        self.btn_add_selection.Click += self._on_add_selection
        self.Closed += self._on_closed

    def _on_closed(self, sender, e):
        try:
            self._search_timer.Stop()
        except Exception:
            pass

    def _status_summary(self):
        """Câu trạng thái mặc định — luôn kèm SỐ LƯỢNG (mục 4 của chuẩn)."""
        if self._explore_root is None:
            return 'Nothing loaded yet — press Refresh'
        groups = sum(1 for n in self._explore_root.walk()
                     if n.kind in ('group', 'leaf'))
        return '%d element(s) in %d group(s)' % (self._explore_root.count, groups)

    def _set_status(self, text):
        try:
            self.status_text.Text = text
        except Exception:
            pass

    # =========================================================================
    # TREE BUILDING
    # =========================================================================
    def _make_header(self, node, checkable):
        """Header của một hàng: [checkbox] nhãn (đếm).

        Node instance và node ghi chú KHÔNG mang số đếm — chúng là một phần tử
        đơn lẻ, "(1)" đứng sau mỗi hàng chỉ là rác thị giác.
        """
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal

        checkbox = None
        if checkable:
            checkbox = CheckBox()
            style = self._res('T3.CheckBox')
            if style is not None:
                checkbox.Style = style
            checkbox.IsThreeState = False
            checkbox.Margin = Thickness(0, 0, 8, 0)
            checkbox.Click += self._on_node_checked
            panel.Children.Add(checkbox)

        label = TextBlock()
        label.Text = node.label
        label.Style = self._res('T3.Body' if node.kind != 'note' else 'T3.Caption')
        panel.Children.Add(label)

        if node.kind not in ('element', 'note'):
            count = TextBlock()
            count.Text = '(%d)' % node.count
            count.Style = self._res('T3.Caption')
            count.Margin = Thickness(4, 0, 0, 0)
            panel.Children.Add(count)

        return panel, checkbox

    def _placeholder(self):
        """Node giả để chevron hiện trước khi con được dựng thật."""
        stub = TreeViewItem()
        stub.Visibility = Visibility.Collapsed
        return stub

    def _add_node(self, container, node, parent_binding):
        item = TreeViewItem()
        style = self._res('T3.TreeViewItem')
        if style is not None:
            item.Style = style

        header, checkbox = self._make_header(node, checkable=(node.kind != 'note'))
        item.Header = header

        binding = _NodeBinding(node, item, checkbox, parent_binding)
        item.Tag = binding
        if checkbox is not None:
            checkbox.Tag = binding

        if node.children:
            item.Items.Add(self._placeholder())
        else:
            binding.built = True

        container.Add(item)
        if parent_binding is not None:
            parent_binding.children.append(binding)
        return binding

    def _build_tree(self, tree, root_node):
        """Dựng gốc + bậc 1. Các bậc sâu hơn dựng lười lúc mở (xem
        `_on_item_expanded`) — model 8000 phần tử mà dựng hết một lượt là
        8000 TreeViewItem và treo Revit vài giây."""
        tree.Items.Clear()
        if root_node is None:
            return None

        root_binding = self._add_node(tree.Items, root_node, None)
        # Dựng con bậc 1 THẲNG, không nhờ event Expanded: lúc __init__ chạy,
        # item còn chưa vào visual tree nên route của routed event chưa chắc
        # tới được TreeView, và gốc sẽ mở ra rỗng.
        self._expand_binding(root_binding)
        root_binding.item.IsExpanded = True
        return root_binding

    def _expand_binding(self, binding):
        """Dựng con của một node — chạy đúng một lần cho mỗi node."""
        if binding is None or binding.built:
            return
        binding.built = True
        binding.item.Items.Clear()
        for child in binding.node.children:
            self._add_node(binding.item.Items, child, binding)

        # Con mới sinh phải thừa hưởng trạng thái tick của CHA TRỰC TIẾP, nếu
        # không mở một nhánh đã tick ra lại thấy toàn ô trống.
        if self._is_ticked(binding):
            for child in binding.children:
                if child.checkbox is not None:
                    child.checkbox.IsChecked = True

    def _on_item_expanded(self, sender, e):
        """Người dùng mở một node. Expanded bubble lên tận TreeView nên phải
        lấy `OriginalSource`, không phải `sender`."""
        item = e.OriginalSource
        if not isinstance(item, TreeViewItem):
            return
        binding = item.Tag
        if isinstance(binding, _NodeBinding):
            self._expand_binding(binding)

    @staticmethod
    def _is_ticked(binding):
        """Checkbox của node có đang tick ĐẦY hay không.

        Không so `is True`: `IsChecked` là `Nullable<bool>` và pythonnet có thể
        trả về `System.Boolean` đã box thay vì bool của Python, lúc đó `is True`
        sai lặng lẽ. None (tick một phần) trả False — chỗ gọi sẽ đi xuống con.
        """
        if binding is None or binding.checkbox is None:
            return False
        state = binding.checkbox.IsChecked
        return state is not None and bool(state)

    # =========================================================================
    # CHECKBOX STATE
    # =========================================================================
    def _on_node_checked(self, sender, e):
        binding = getattr(sender, 'Tag', None)
        if not isinstance(binding, _NodeBinding):
            return
        self._apply_check(binding)

    def _apply_check(self, binding):
        """Lan trạng thái tick của `binding` xuống con và lên cha."""
        self._set_subtree(binding, self._is_ticked(binding))
        self._refresh_ancestors(binding)
        self._update_selected_count()

    def _set_subtree(self, binding, value):
        """Tick lan xuống chỉ tới những node ĐÃ dựng. Node chưa dựng nhận trạng
        thái lúc nó được mở (xem `_on_item_expanded`), nên không phải dựng cả
        cây chỉ để tick một nhánh."""
        stack = list(binding.children)
        while stack:
            child = stack.pop()
            if child.checkbox is not None:
                child.checkbox.IsChecked = value
            stack.extend(child.children)

    def _refresh_ancestors(self, binding):
        """Cha = tick hết -> checked · không cái nào -> unchecked · lẫn -> ô đặc.

        `IsThreeState` để False: nó chỉ quyết định CÚ CLICK của người dùng có đi
        qua indeterminate hay không. Gán None bằng code vẫn hiện ô đặc.
        """
        parent = binding.parent
        while parent is not None:
            if parent.checkbox is not None:
                flags = [bool(c.checkbox.IsChecked)
                         for c in parent.children if c.checkbox is not None]
                if not flags:
                    pass
                elif all(flags):
                    parent.checkbox.IsChecked = True
                elif not any(flags):
                    parent.checkbox.IsChecked = False
                else:
                    parent.checkbox.IsChecked = None
            parent = parent.parent

    def _checked_ids(self):
        """ElementId của mọi node đang tick, không trùng.

        Node tick thì lấy cả nhánh và DỪNG — không cần đi sâu, `Node.ids()` đã
        gộp hết phần tử bên dưới. Nhờ vậy tick một category là chọn đủ cả
        nhánh dù các bậc dưới chưa bao giờ được mở ra.
        """
        root = self._explore_binding
        if root is None:
            return []

        out = []
        seen = set()

        def walk(binding):
            if self._is_ticked(binding):
                for element_id in binding.node.ids():
                    value = explorer.eid_int(element_id)
                    if value not in seen:
                        seen.add(value)
                        out.append(element_id)
                return
            for child in binding.children:
                walk(child)

        walk(root)
        return out

    def _update_selected_count(self):
        """Số đã tick hiện ở dải tally VÀ trên nút primary — người dùng biết
        bấm Select sẽ chọn bao nhiêu trước khi bấm."""
        count = len(self._checked_ids())
        self.txt_explore_tally_checked.Text = '%d checked' % count
        self.btn_apply.Content = 'Select (%d)' % count if count else 'Select'

    # -- Space: tick hàng đang trỏ ------------------------------------------
    def _on_tree_key_down(self, sender, e):
        """Space bật/tắt checkbox của hàng đang highlight — chọn bằng bàn phím
        mà không phải ngắm vào ô 14px. Không chạm Revit API."""
        if e.Key != Key.Space:
            return
        item = self.tree_explore.SelectedItem
        binding = getattr(item, 'Tag', None) if item is not None else None
        if not isinstance(binding, _NodeBinding) or binding.checkbox is None:
            return
        e.Handled = True
        binding.checkbox.IsChecked = not self._is_ticked(binding)
        self._apply_check(binding)

    # =========================================================================
    # EXPLORE
    # =========================================================================
    def _reload_explore(self):
        self._run_in_revit(self._reload_explore_impl)

    def _reload_explore_impl(self):
        """Đọc lại model rồi dựng cây. Đây là chỗ tốn thời gian duy nhất,
        nên nó báo trạng thái trước khi chạy.

        Đọc combo box TẠI ĐÂY, không phải lúc xếp hàng: khi modeless, action
        này chạy trễ và phải phản ánh lựa chọn mới nhất của người dùng.
        """
        scope = self.cbo_explore_scope.SelectedItem or explorer.SCOPE_VIEW
        filter_name = self.cbo_explore_filter.SelectedItem or explorer.FILTER_NONE

        # Entire Model trên project lớn mất vài giây. Không có progress bar
        # trong cửa sổ này, nên ít nhất phải có câu trạng thái + con trỏ chờ,
        # không để Revit đóng băng im lặng (luật S5).
        self._set_status('Reading %s…' % str(scope).lower())
        self.Cursor = Cursors.Wait
        self._do_events()
        try:
            self._records = explorer.collect(self.doc, self.uidoc,
                                             scope=str(scope),
                                             filter_name=str(filter_name))
        except Exception as ex:
            self._records = []
            T3Dialog.show_error(
                'Could not read the elements for "%s".\n\n%s\n\n'
                'Try a different Display scope, or restart Revit if the model '
                'has just changed.' % (scope, ex),
                title='Explore', owner=self)
        finally:
            self.Cursor = Cursors.Arrow
        # Gọi _impl, không phải wrapper: ta ĐANG ở trong context rồi.
        self._rebuild_explore_tree_impl()

    def _rebuild_explore_tree(self):
        self._run_in_revit(self._rebuild_explore_tree_impl)

    def _rebuild_explore_tree_impl(self):
        """Gom nhóm lại từ `self._records` đã thu.

        VẪN phải chạy trong API context dù không gọi collector: `ElementRecord`
        đọc LƯỜI workset / level / phase / design option, nên gom theo một
        trong bốn cái đó sẽ chạm document (xem dev/audit_api_context.py).
        """
        group_by = str(self.cbo_explore_group.SelectedItem or explorer.GROUP_CATEGORY)
        search = self.txt_explore_search.Text or ''
        scope = str(self.cbo_explore_scope.SelectedItem or explorer.SCOPE_VIEW)

        # Gốc nói rõ đang xem ở đâu — quan trọng nhất với Active View, vì cùng
        # một model mỗi view ra một con số khác nhau.
        root_label = '%s by %s' % (scope, group_by)
        if scope == explorer.SCOPE_VIEW:
            try:
                root_label = '%s by %s  [%s]' % (scope, group_by,
                                                 self.doc.ActiveView.Name)
            except Exception:
                pass
        self._explore_root = explorer.build_tree(self._records,
                                                 group_by=group_by,
                                                 search=search,
                                                 root_label=root_label)
        self._explore_binding = self._build_tree(self.tree_explore, self._explore_root)

        total = self._explore_root.count
        self.txt_explore_tally_elements.Text = '%d elements' % total

        empty = (total == 0)
        self.txt_explore_empty.Visibility = (Visibility.Visible if empty
                                             else Visibility.Collapsed)
        self.tree_explore.Visibility = (Visibility.Collapsed if empty
                                        else Visibility.Visible)

        self._update_selected_count()
        if empty:
            self._set_status('No elements in scope — widen Display or clear Search')
        else:
            self._set_status(self._status_summary())

    def _on_explore_query_changed(self, sender, e):
        if self._loading:
            return
        self._remember_query()
        self._reload_explore()

    def _on_explore_group_changed(self, sender, e):
        if self._loading:
            return
        self._remember_query()
        self._rebuild_explore_tree()

    def _on_explore_search_changed(self, sender, e):
        """Chỉ khởi động lại đồng hồ — cây dựng lại ở `_on_search_timer_tick`
        khi người dùng ngừng gõ."""
        if self._loading:
            return
        self._search_timer.Stop()
        self._search_timer.Start()

    def _on_search_timer_tick(self, sender, e):
        self._search_timer.Stop()
        self._rebuild_explore_tree()

    def _on_explore_refresh(self, sender, e):
        self._reload_explore()

    def _on_explore_expand_all(self, sender, e):
        self._expand_all(self._explore_binding)

    def _on_explore_collapse_all(self, sender, e):
        self._collapse_all(self._explore_binding)

    @staticmethod
    def _has_group_children(node):
        """Node còn bậc GOM NHÓM bên dưới hay chỉ còn danh sách instance.

        Đây là cái chặn "Expand all" khỏi dựng hàng nghìn hàng instance: mở hết
        các bậc nhóm (category / family / type) rồi dừng, instance vẫn nằm sau
        chevron của chính nó.
        """
        return any(child.kind not in ('element', 'note')
                   for child in node.children)

    def _expand_all(self, root_binding, max_depth=6):
        """Mở mọi bậc gom nhóm. `max_depth` chỉ là chốt an toàn."""
        if root_binding is None:
            return

        opened = [0]

        def walk(binding, depth):
            if depth > max_depth or not self._has_group_children(binding.node):
                return
            self._expand_binding(binding)
            binding.item.IsExpanded = True
            opened[0] += 1
            for child in list(binding.children):
                walk(child, depth + 1)

        walk(root_binding, 0)
        self._set_status('Expanded %d group(s) — element rows stay behind their '
                         'own arrow' % opened[0])

    def _collapse_all(self, root_binding):
        if root_binding is None:
            return
        stack = list(root_binding.children)
        while stack:
            binding = stack.pop()
            binding.item.IsExpanded = False
            stack.extend(binding.children)
        root_binding.item.IsExpanded = True
        self._set_status('Collapsed to the top level')

    def _on_explore_node_highlighted(self, sender, e):
        self._show_node_info(self.tree_explore.SelectedItem,
                             self.txt_explore_info_title,
                             self.txt_explore_info_detail)

    # -- Double-click: hiện node trong model ---------------------------------
    @staticmethod
    def _binding_at(source):
        """`_NodeBinding` của hàng chứa `source`, hoặc None.

        Chuột rơi vào TextBlock/CheckBox bên trong header, không vào chính
        TreeViewItem, nên phải trèo lên cây visual tới TreeViewItem gần nhất.
        """
        node = source
        for _ in range(24):            # chốt an toàn, cây header chỉ sâu vài bậc
            if node is None:
                return None
            if isinstance(node, TreeViewItem):
                binding = node.Tag
                return binding if isinstance(binding, _NodeBinding) else None
            parent = None
            try:
                parent = VisualTreeHelper.GetParent(node)
            except Exception:
                parent = None       # source không phải Visual (vd một Run)
            if parent is None:
                parent = getattr(node, 'Parent', None)
            node = parent
        return None

    def _on_tree_double_click(self, sender, e):
        """Nháy đúp một hàng = chọn + zoom tới nó trong model.

        Dùng PreviewMouseDoubleClick (tunnel, cha nhận trước) rồi `Handled`:
        nếu để bubble thì TreeViewItem đã kịp mở/đóng nhánh, nháy đúp vừa
        hiện model vừa gập cây. Mở/đóng vẫn còn nguyên ở chevron.
        """
        # Đi LÊN từ OriginalSource, không dùng tree.SelectedItem: nháy đúp vào
        # khoảng trống dưới cây vẫn để SelectedItem là node cũ và sẽ hiện sai
        # node. OriginalSource cho biết đã nháy vào ĐÂU thật.
        binding = self._binding_at(e.OriginalSource)
        if binding is None or binding.node.kind == 'note':
            return

        ids = binding.node.ids()
        if not ids:
            return
        e.Handled = True
        label = binding.node.label
        self._run_in_revit(lambda: self._reveal_impl(ids, label))

    def _reveal_impl(self, ids, label):
        """Chọn rồi zoom — ShowElements một mình không đặt selection, mà người
        dùng nháy đúp là muốn có cả hai."""
        try:
            self.uidoc.Selection.SetElementIds(explorer.to_id_list(ids))
            self.uidoc.ShowElements(explorer.to_id_list(ids))
            self.uidoc.RefreshActiveView()
        except Exception as ex:
            self._set_status('Could not show "%s" in the model: %s' % (label, ex))
            return
        self._set_status('Showing %d element(s) of "%s" in the model'
                         % (len(ids), label))

    # -- Pick in model (cửa sổ vẫn mở) ---------------------------------------
    def _on_explore_pick(self, sender, e):
        self._run_in_revit(self._pick_impl)

    def _pick_impl(self):
        """Cho người dùng khoanh phần tử ngay trong model rồi xem đúng những
        cái đó. Cửa sổ KHÔNG ẩn đi — đó là cả điểm của chế độ modeless."""
        try:
            refs = self.uidoc.Selection.PickObjects(
                ObjectType.Element,
                'Pick elements to browse, then press Finish')
        except OperationCanceledException:
            self._set_status('Picking cancelled — the tree is unchanged')
            return
        except Exception as ex:
            T3Dialog.show_error(
                'Could not start picking in the model.\n\n%s\n\n'
                'Open a model view (not a sheet or schedule) and try again.'
                % ex, title='Pick in model', owner=self)
            return

        picked = [r.ElementId for r in refs] if refs else []
        if not picked:
            self._set_status('Nothing picked — the tree is unchanged')
            return

        self.uidoc.Selection.SetElementIds(explorer.to_id_list(picked))
        # Chuyển Display sang Current Selection để cây đúng bằng cái vừa khoanh.
        self._loading = True
        try:
            self.cbo_explore_scope.SelectedItem = explorer.SCOPE_SELECTION
        finally:
            self._loading = False
        self._reload_explore_impl()
        self._set_status('Browsing the %d element(s) you picked' % len(picked))

    def _show_node_info(self, item, title_block, detail_block):
        binding = getattr(item, 'Tag', None) if item is not None else None
        if not isinstance(binding, _NodeBinding):
            title_block.Text = 'Nothing highlighted'
            detail_block.Text = 'Click a row to read its details. Space ticks it, double-click shows it in the model.'
            return

        node = binding.node
        title_block.Text = node.label

        parts = []
        if node.kind == 'element' and node.records:
            record = node.records[0]
            parts.append('Category: %s' % record.category)
            parts.append('Family: %s' % record.family)
            parts.append('Type: %s' % record.type_name)
            parts.append('Element Id: %d' % record.id_int)
        elif node.kind == 'note':
            parts.append('Listing is capped at %d rows per type for speed.'
                         % explorer.INSTANCE_CAP)
        else:
            parts.append('%d element(s)' % node.count)
            parts.append('%d child group(s)' % len(node.children))
            # `walk_records` là generator — chỉ lấy phần tử ĐẦU, không
            # materialize cả 8000 record chỉ để in một dòng mẫu.
            sample = next(iter(node.walk_records()), None)
            if sample is not None:
                parts.append('First: %s / %s / %s'
                             % (sample.category, sample.family, sample.type_name))
        detail_block.Text = '  ·  '.join(parts)

    # -- Explore actions ------------------------------------------------------
    def _require_checked(self, verb):
        ids = self._checked_ids()
        if not ids:
            T3Dialog.show_warning(
                'Nothing is checked yet.\n\nTick a category, family or type in '
                'the tree — or press All under the tree — then %s again.' % verb,
                title='Nothing checked', owner=self)
            return None
        return ids

    def _on_explore_zoom(self, sender, e):
        ids = self._require_checked('Zoom')
        if ids:
            self._run_in_revit(lambda: self._zoom_impl(ids))

    def _zoom_impl(self, ids):
        try:
            self.uidoc.ShowElements(explorer.to_id_list(ids))
            self.uidoc.RefreshActiveView()
            self._set_status('Zoomed the active view to %d element(s)' % len(ids))
        except Exception as ex:
            T3Dialog.show_error(
                'Revit could not zoom to the checked elements.\n\n%s\n\n'
                'Elements outside the active view cannot be zoomed to — switch '
                'Display to Active View first.' % ex,
                title='Zoom', owner=self)

    def _on_explore_isolate(self, sender, e):
        ids = self._require_checked('Isolate')
        if ids:
            self._run_in_revit(lambda: self._isolate_impl(ids))

    def _isolate_impl(self, ids):
        view = self.doc.ActiveView
        if view is None:
            T3Dialog.show_warning('There is no active view to isolate in.',
                                  title='Isolate', owner=self)
            return

        transaction = Transaction(self.doc, 'ManaSelect - Isolate elements')
        try:
            transaction.Start()
            view.IsolateElementsTemporary(explorer.to_id_list(ids))
            transaction.Commit()
            self._set_status('Isolated %d element(s) in %s' % (len(ids), view.Name))
        except Exception as ex:
            try:
                if transaction.HasStarted():
                    transaction.RollBack()
            except Exception:
                pass
            T3Dialog.show_error(
                'Could not isolate the checked elements in "%s".\n\n%s\n\n'
                'Some views (schedules, sheets) do not support temporary '
                'isolation — open a model view and try again.'
                % (getattr(view, 'Name', '?'), ex),
                title='Isolate', owner=self)

    def _on_explore_hide(self, sender, e):
        ids = self._require_checked('Hide')
        if ids:
            self._run_in_revit(lambda: self._hide_impl(ids))

    def _hide_impl(self, ids):
        view = self.doc.ActiveView
        if view is None:
            T3Dialog.show_warning('There is no active view to hide in.',
                                  title='Hide', owner=self)
            return

        transaction = Transaction(self.doc, 'ManaSelect - Hide elements')
        try:
            transaction.Start()
            view.HideElementsTemporary(explorer.to_id_list(ids))
            transaction.Commit()
            self._set_status('Temporarily hid %d element(s) in %s — press Reset '
                             'to show them again' % (len(ids), view.Name))
        except Exception as ex:
            try:
                if transaction.HasStarted():
                    transaction.RollBack()
            except Exception:
                pass
            T3Dialog.show_error(
                'Could not hide the checked elements in "%s".\n\n%s\n\n'
                'Some views (schedules, sheets) do not support temporary '
                'hiding — open a model view and try again.'
                % (getattr(view, 'Name', '?'), ex),
                title='Hide', owner=self)

    def _on_explore_reset(self, sender, e):
        self._run_in_revit(self._reset_impl)

    def _reset_impl(self):
        """Tắt Temporary Hide/Isolate của view đang mở — gỡ cả Isolate lẫn Hide
        của tool này, giống nút "Reset Temporary Hide/Isolate" của Revit."""
        view = self.doc.ActiveView
        if view is None:
            return
        transaction = Transaction(self.doc, 'ManaSelect - Reset temporary hide/isolate')
        try:
            transaction.Start()
            view.DisableTemporaryViewMode(TemporaryViewMode.TemporaryHideIsolate)
            transaction.Commit()
            self._set_status('Reset temporary hide/isolate in %s' % view.Name)
        except Exception as ex:
            try:
                if transaction.HasStarted():
                    transaction.RollBack()
            except Exception:
                pass
            T3Dialog.show_error(
                'Could not reset temporary hide/isolate in "%s".\n\n%s'
                % (getattr(view, 'Name', '?'), ex),
                title='Reset', owner=self)

    def _on_explore_delete(self, sender, e):
        ids = self._require_checked('Delete')
        if not ids:
            return
        # Hỏi TRƯỚC khi vào API context: dialog xác nhận là việc của WPF, và
        # dựng nó bên trong Execute() sẽ chặn vòng lặp idle của Revit.
        if not T3Dialog.confirm(
                'Delete %d element(s) from the model?\n\n'
                'This changes the model. Ctrl+Z undoes it as a single step.'
                % len(ids),
                title='Delete elements', ok_text='Delete', cancel_text='Keep',
                danger=True, owner=self):
            self._set_status('Delete cancelled — nothing was changed')
            return
        self._run_in_revit(lambda: self._delete_impl(ids))

    def _delete_impl(self, ids):
        transaction = Transaction(self.doc, 'ManaSelect - Delete elements')
        deleted = 0
        try:
            transaction.Start()
            # Delete trả ICollection<ElementId> — số thật có thể LỚN HƠN số đã
            # tick, vì xoá một phần tử kéo theo phần tử phụ thuộc.
            removed = self.doc.Delete(explorer.to_id_list(ids))
            deleted = getattr(removed, 'Count', None)
            if deleted is None:
                deleted = len(list(removed)) if removed is not None else 0
            transaction.Commit()
        except Exception as ex:
            try:
                if transaction.HasStarted():
                    transaction.RollBack()
            except Exception:
                pass
            T3Dialog.show_error(
                'Delete failed and nothing was removed.\n\n%s\n\n'
                'Pinned or view-owned elements cannot be deleted this way; '
                'unpin them in Revit first.' % ex,
                title='Delete elements', owner=self)
            return

        self._set_status('Deleted %d element(s)' % deleted)
        T3Dialog.show_info('Deleted %d element(s) from the model.\n\n'
                           'Ctrl+Z in Revit undoes this as a single step.'
                           % deleted,
                           title='Delete elements', owner=self)
        self._reload_explore()

    def _export_records(self):
        """Record cần xuất: của các nhánh đang tick, hoặc cả cây nếu chưa tick
        gì. Không trùng Id."""
        root = self._explore_binding
        if root is None:
            return []
        picked = []

        def walk(binding):
            if self._is_ticked(binding):
                picked.extend(binding.node.walk_records())
                return
            for child in binding.children:
                walk(child)

        walk(root)
        if not picked:
            picked = list(self._explore_root.walk_records())

        seen = set()
        out = []
        for record in picked:
            if record.id_int not in seen:
                seen.add(record.id_int)
                out.append(record)
        return out

    def _on_explore_export(self, sender, e):
        """Hỏi đường dẫn TRƯỚC (dialog WPF), rồi mới vào API context để ghi —
        cột Level / Workset đọc lười từ document."""
        if self._explore_root is None or self._explore_root.count == 0:
            T3Dialog.show_warning('The tree is empty, so there is nothing to export.',
                                  title='Export CSV', owner=self)
            return
        try:
            path = forms.save_file(file_ext='csv', default_name='ManaSelect_Elements')
        except Exception:
            path = None
        if not path:
            self._set_status('Export cancelled')
            return
        self._run_in_revit(lambda: self._export_impl(path))

    def _export_impl(self, path):
        records = self._export_records()
        try:
            rows = explorer.element_rows(records)
            # newline='' + text mode: luật S13 cấm open(..., 'wb') cho CSV.
            with io.open(path, 'w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(explorer.ELEMENT_COLUMNS)
                writer.writerows(rows)
        except Exception as ex:
            T3Dialog.show_error(
                'Could not write "%s".\n\n%s\n\n'
                'Pick a folder you can write to, or close the file if it is '
                'already open in Excel.' % (path, ex),
                title='Export CSV', owner=self)
            return
        self._set_status('Exported %d element(s) to %s'
                         % (len(rows), os.path.basename(path)))

    # =========================================================================
    # FOOTER
    # =========================================================================
    def _set_all_checks(self, value):
        root = self._explore_binding
        if root is None:
            return
        if root.checkbox is not None:
            root.checkbox.IsChecked = value
        self._set_subtree(root, value)
        self._update_selected_count()

    def _on_check_all(self, sender, e):
        self._set_all_checks(True)

    def _on_check_none(self, sender, e):
        self._set_all_checks(False)

    def _on_apply(self, sender, e):
        ids = self._require_checked('Select')
        if ids:
            self._run_in_revit(lambda: self._select_impl(ids, add=False))

    def _on_add_selection(self, sender, e):
        ids = self._require_checked('Add to Selection')
        if ids:
            self._run_in_revit(lambda: self._select_impl(ids, add=True))

    def _select_impl(self, ids, add=False):
        """`add=True` giữ nguyên selection hiện có của Revit và cộng thêm."""
        try:
            final = list(ids)
            if add:
                seen = set(explorer.eid_int(i) for i in final)
                for current in self.uidoc.Selection.GetElementIds():
                    if explorer.eid_int(current) not in seen:
                        seen.add(explorer.eid_int(current))
                        final.append(current)
            self.uidoc.Selection.SetElementIds(explorer.to_id_list(final))
            self.uidoc.RefreshActiveView()
        except Exception as ex:
            T3Dialog.show_error(
                'Could not apply the selection.\n\n%s\n\n'
                'Elements that live in a linked model, or that were deleted '
                'since the tree was built, cannot be selected — press Refresh and try again.' % ex,
                title='Select in Revit', owner=self)
            return
        if add:
            self._set_status('Added %d element(s) — %d now selected in Revit'
                             % (len(ids), len(final)))
        else:
            self._set_status('Selected %d element(s) in Revit' % len(ids))

    # =========================================================================
    # WINDOW CHROME
    # =========================================================================
    def _minimize(self, sender, e):
        self.WindowState = WindowState.Minimized

    def _maximize(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def _close_chrome(self, sender, e):
        self.Close()


def detect_persistent_engine():
    """Engine thường trú có đang bật cho command này không.

    Cửa sổ modeless BẮT BUỘC cần nó: không có engine thường trú, pyRevit dẹp
    interpreter ngay khi script kết thúc và cửa sổ vừa `Show()` chết theo.
    `__persistentengine__ = True` trong `script.py` chỉ có hiệu lực SAU khi
    reload pyRevit, nên hàm này là cái chốt để rơi về modal một cách êm ái
    thay vì mở ra một cửa sổ chết.
    """
    try:
        from pyrevit import EXEC_PARAMS
        try:
            return bool(EXEC_PARAMS.needs_persistent_engine)
        except Exception:
            pass
        try:
            cfg_json = EXEC_PARAMS.script_runtime.ScriptRuntimeConfigs.EngineConfigs
            if cfg_json:
                return bool(json.loads(cfg_json).get('persistent', False))
        except Exception:
            pass
    except Exception:
        pass
    return False


# Giữ tham chiếu cửa sổ modeless ở cấp module: hết `show_dialog()` thì biến
# cục bộ tiêu, và không còn gì trỏ tới window nữa — GC của Python có thể thu
# nó trong khi WPF vẫn đang hiện.
_OPEN_WINDOW = None


def show_dialog(modal=None):
    """Mở ManaSelect.

    `modal=None` (mặc định) = tự quyết: modeless nếu engine thường trú đang
    bật, modal nếu chưa. Modeless là chế độ mong muốn — nó để người dùng bấm
    chọn trong model trong khi cửa sổ vẫn mở.
    """
    global _OPEN_WINDOW

    if not revit.doc:
        T3Dialog.show_warning('Open a Revit project before running ManaSelect.',
                              title='ManaSelect')
        return None

    if modal is None:
        modal = not detect_persistent_engine()

    window = ManaSelectWindow()
    window._modeless = not modal
    if not modal:
        window.Closed += _on_window_closed
        if window._explore_root is not None:
            window._set_status(window._status_summary()
                               + ' — the model stays clickable')
    else:
        window._set_status(window._status_summary()
                           + ' — restart Revit for a non-blocking window')

    # Từ đây trở đi mọi handler của WPF chạy NGOÀI Revit API context, nên
    # `_run_in_revit` phải đẩy qua ExternalEvent.
    window._in_api_context = False

    _OPEN_WINDOW = window
    window.show(modal=modal)
    return window


def _on_window_closed(sender, e):
    global _OPEN_WINDOW
    if _OPEN_WINDOW is sender:
        _OPEN_WINDOW = None
