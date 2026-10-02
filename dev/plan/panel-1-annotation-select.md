# Panel 1 — Annotation & Select (Ngày 5)

> 4 tool. Model test: file nhỏ có wall, grid, dimension, text note, tag, 1 DWG import + 1 DWG link.
> Quy trình chung: xem "Định nghĩa xong" trong `dev/plan/README.md`. Bật debug: giữ CTRL khi bấm nút.

## Tool 1/4 — AutoDimension

Chain: `script.py` (24 loc) → `lib/GUI/AutoDimensionDialog.py` (1721 loc) → `AutoDimension.xaml`

- [ ] Mở cửa sổ, chrome OK
- [ ] Auto dim wall trên floor plan → dim tạo đúng, Ctrl+Z revert 1 bước
- [ ] Auto dim grid trên plan + trên section
- [ ] View không hỗ trợ (3D, schedule) → thông báo thân thiện
- [ ] Không chọn gì / model không có element khớp → không stacktrace
- [ ] Ghi chú:

## Tool 2/4 — ManaDWG

Chain: `script.py` (432 loc, self-contained) → `DWGManagement.xaml` (**UI-LOCKED** — chỉ debug logic, không đụng XAML)

- [ ] Mở cửa sổ, list đúng cả DWG **import** lẫn DWG **link**, phân biệt rõ
- [ ] Xoá 1 DWG import → biến mất khỏi model + khỏi list; Ctrl+Z khôi phục
- [ ] Thao tác trên DWG link (unload/remove tuỳ chức năng)
- [ ] Model không có DWG → list rỗng, không lỗi
- [ ] Kiểm tra 2 transaction trong script gom đúng thao tác
- [ ] Ghi chú:

## Tool 3/4 — ManaSelect

Chain: `script.py` (29 loc) → `lib/GUI/ManaSelectDialog.py` (660 loc) → `ManaSelect.xaml` · dialog phụ: `QuickElementDialog.py` → `QuickElement.xaml`

- [ ] Mở cửa sổ, chrome OK
- [ ] Pre-select hỗn hợp element → filter theo category/type hoạt động
- [ ] Selection rỗng khi mở tool → xử lý đẹp
- [ ] Mở được QuickElement dialog phụ từ trong tool (nếu có nút) → không XamlParseException
- [ ] Apply selection → selection trong Revit đổi đúng
- [ ] Ghi chú:

## Tool 4/4 — ManaAnno

Chain: `script.py` (36 loc) → `lib/GUI/ManaAnnoDialog.py` (1416 loc) → `ManaAnno.xaml` · dialog phụ: `DimTextDialog.py` → `DimText.xaml`, `TagCheckerDialog.py` → `TagChecker.xaml`

- [ ] Mở cửa sổ, chrome OK
- [ ] Batch đổi type text/tag/dim trên view test → đúng, Ctrl+Z OK
- [ ] Mở DimText dialog phụ → hoạt động
- [ ] Mở TagChecker dialog phụ → hoạt động
- [ ] View có view template khoá annotation → thông báo hợp lý, không crash
- [ ] Ghi chú:

## Chốt ngày 5
- [x] Cập nhật trạng thái 4 tool vào bảng dưới + `dev/plan/README.md`
- [ ] Lỗi tìm thấy → mục Phát sinh + quyết định sửa ngay (nhỏ) hay tạo việc riêng (lớn)
- [ ] Commit (nếu có sửa code): `fix(panel-annotation): <mô tả>`

| Tool | Trạng thái | Ghi chú |
|------|-----------|---------|
| AutoDimension | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| ManaDWG | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| ManaSelect | ✅ | Crash (xem Phát sinh) đã sửa 2026-07-03 · user xác nhận chung 2026-07-05: hoạt động tốt |
| ManaAnno | ✅ | Bug trang trắng khi mở (xem Phát sinh) đã sửa 2026-07-03 · user xác nhận chung 2026-07-05: hoạt động tốt |

> 2026-07-05 — user xác nhận tổng quát: **"các tool hiện tại đều đã hoạt động tốt"**. Các mục
> checklist chi tiết (Ctrl+Z, edge case) chưa có báo cáo riêng từng mục — coi là pass theo xác
> nhận chung; mở lại nếu phát hiện lỗi khi dùng thực tế.

## Phát sinh

_(ghi lỗi mới tại đây)_

- **2026-10-02 — AutoDimension v2.2.2: còn 2 warning "Chain dim failed: Invalid number of references"** (user chạy lại sau v2.2.1). 2 warning = đúng 1 chuỗi hỏng × "Mirror strings to both sides" (bật mặc định). Không có journal nên không biết chuỗi nào → sửa mọi nguồn ref sai hướng còn lại + làm chuỗi tự cứu (`AutoDimensionDialog.py`):
  1. `PARALLEL_TOL` (~0.1°) thay `AXIS_TOLERANCE = 0.1` (~5.7°): tường lệch vài độ, grid xiên/cong (`_separate_grids`), family instance xoay lệch trục (`_instance_axis_aligned`) bị loại khỏi chuỗi X/Y thay vì làm Revit từ chối cả chuỗi.
  2. `_col_ref_from_geom`: đọc face bằng `GetSymbolGeometry()` + transform normal — tài liệu Revit ghi rõ reference từ `GetInstanceGeometry()` là bản copy, **không dùng được cho dimension**. Ngưỡng hướng 0.7 → `PARALLEL_TOL`.
  3. Facade (Phase 6): chỉ lấy mặt của tường chạy CẮT ngang chuỗi; tường chạy dọc mặt đứng có mặt vuông góc hướng đo → trước đây làm hỏng cả chuỗi facade.
  4. `_dimension_string` + `_salvage_dimension`: Revit vẫn từ chối → dựng lại chuỗi tham lam, giữ mọi ref Revit nhận, log 1 dòng tên + ID element bị loại (không stack trace, không lặp ở chuỗi mirror). Áp cho cả chuỗi section/elevation.
  - Verify: `ast.parse` OK, audit_tools/compat/cpython/wiring xanh, test mock salvage (ref xấu giữa/đầu chuỗi, hết seed, chỉ 1 ref tốt) đúng. **Chưa smoke test Revit** — chạy lại đúng model cũ: kỳ vọng 0 warning; nếu còn, warning giờ ghi rõ element ID để chọn bằng Select by ID.

- **2026-07-04 — AutoDimension v2.2.1: sửa "Chain dim failed: Invalid number of references"** (user báo 6 warning khi chạy thật). Nguyên nhân: `NewDimension` loại các reference có mặt phẳng không song song hướng đo rồi còn <2 ref. Code cũ dùng `FamilyInstanceReferenceType.Left/Right/Front/Back` **cứng theo trục world**, nhưng đây là hướng *cục bộ của family* — cột xoay 90°, cửa/cửa sổ trong tường chạy dọc, lift xoay → ref quay sang trục vuông góc và bị Revit từ chối. Đã sửa (`AutoDimensionDialog.py`):
  1. Thêm `_hand_matches_axis()` + `_axis_ref_types()`: chọn cặp Left/Right vs Front/Back theo `HandOrientation` thật của instance — áp dụng cho cột (Phase 2 cả 2 chế độ), cửa/cửa sổ (Phase 3 cả 2 chế độ), lift (Phase 5), facade (Phase 6).
  2. Phase 3/6: cửa có bề rộng chạy dọc bị **loại khỏi chuỗi X** (thuộc chuỗi Y) thay vì nhét ref sai hướng; chuỗi Y đo bề rộng cửa bằng Left/Right (jamb) thay vì Front/Back (mặt tường) như trước.
  3. Chuỗi hàng cột/cửa/lift không lấy được ref element nào → **bỏ qua** thay vì tạo dim chỉ có 2 grid (trùng Phase 1) hoặc ném lỗi.
  4. `_col_ref_one`: fallback geometry (có kiểm hướng mặt) được ưu tiên TRƯỚC vòng quét mù WeakReference/StrongReference/enum-scan — quét mù hay trả về plane vuông góc hướng đo, chính là nguồn lỗi.
  5. `_collect_wall_core_refs`: cắt về 1 ref/mặt (`ext[:1]+int[:1]`) — trước đây index [1] có thể là mặt exterior thứ 2 chứ không phải interior.
  - Verify tĩnh: `ast.parse` OK · `audit_tools` clean. **Chưa smoke test Revit** — chạy lại đúng model cũ, kỳ vọng hết warning hoặc giảm mạnh; cột xoay/cửa tường dọc phải có dim đúng hướng.

- **2026-07-04 — AutoDimension: tái cấu trúc UI/UX 2 cột** (`AutoDimension.xaml`, không đổi Python — giữ nguyên toàn bộ `x:Name` + handler):
  - Cửa sổ 520×820 một cột dài phải cuộn → **780×700 hai cột**: trái = phạm vi & đối tượng (1·Views, 2·Elements + wall faces, Checking Mode), phải = cách tạo dim (3·Dimension Style, 4·Placement). Info box rút gọn, span 2 cột dưới cùng.
  - Element checkboxes xếp lưới 2 cột (UniformGrid); wall face radios chuyển nằm ngang ngay dưới nhóm Elements (gắn với checkbox Walls, vẫn `IsEnabled` binding); checkbox Facade chuyển từ "Element Types" về đúng chỗ — sub-section Facade trong Placement, 2 ô nhập offset/tolerance chỉ hiện khi bật (DataTrigger thuần XAML, không cần handler mới).
  - Đổi control mặc định sang bộ style T3 sẵn có trong shared block: `T3CheckBox`, `T3RadioButton`, `T3ComboBoxSmall`, `T3InputFieldSmall`; thêm tooltip giải thích cho các option khó hiểu (Checking Mode, Mirror, Perimeter tol.).
  - `grp_wall_layer` đổi GroupBox → Border nhưng giữ nguyên `x:Name` (checking_mode_changed chỉ set `.Visibility` nên tương thích).
  - Verify: XML well-formed · 41/41 `x:Name` Python cần đều còn · `audit_tools`/`audit_ui` clean · `sync_wpf_styles --check` 53/53. **Chưa smoke test Revit** (mở cửa sổ, toggle Facade/Checking/3-Level, chạy dim).

- **2026-07-04 — AutoDimension: sửa 5 lỗi logic + roadmap cải thiện** (chi tiết + checklist smoke test: `dev/plan/autodimension-improvement-roadmap.md`, dialog bump v2.2.0):
  1. Modeless → **modal**: `Transaction.Start()` chạy trực tiếp trong WPF button handler của cửa sổ modeless nằm ngoài Revit API context → "outside of API context" (tiền lệ: TagCheckerDialog từng thử ExternalEvent bị native crash, revert modal).
  2. Extent đặt dim: centroid → bounding box — dim tổng (y_max + L3…) không còn đè lên nửa ngoài tường/cột biên.
  3. Cột (Phase 2, chế độ thường): per-column (N chuỗi 2-ref chồng nhau cùng offset) → 1 chuỗi/hàng-cột kiểu grid biên–mặt L–mặt R–grid biên; bỏ mirror per-row (trước dồn mọi hàng về cùng 1 đường).
  4. Skip tường xiên (`AXIS_TOLERANCE`) — trước bị ép vào chuỗi X/Y → dim sai vị trí hoặc fail ngầm.
  5. Section/Elevation: thêm `_dim_section_view` (chuỗi grid ngang gần đỉnh crop + chuỗi level dọc mép trái, tính trong mặt phẳng view qua `CropBox.Transform`) — trước đây các view này luôn ra 0 dim vì toàn bộ toán học là mặt bằng XY.
  - Verify tĩnh: `ast.parse` OK · `audit_tools`/`audit_ui` clean · `sync_wpf_styles --check` 53/53. **Chưa smoke test Revit.**

- **2026-07-04 — AutoDimension: mặc định dùng active view, ẩn danh sách Plan Views sau toggle** (yêu cầu UX trực tiếp từ user khi test Ngày 5): trước đây section "Plan Views" luôn hiện `ListBox lst_views` (list toàn bộ plan view) ngay khi mở → user phải cuộn qua/bỏ chọn thủ công dù thường chỉ muốn dim view đang mở. Logic `_run` vốn đã fallback active view khi `lst_views.SelectedItems` rỗng (`AutoDimensionDialog.py:904`), nên chỉ cần đổi UI. Đã sửa:
  - `AutoDimension.xaml`: thêm toggle `chk_specific_views` ("Choose specific plan views (default: active view)", mặc định OFF) ở đầu GroupBox; bọc helper text + `lst_views` + nút Select All/Clear vào `StackPanel x:Name="pnl_views_list" Visibility="Collapsed"`. Bỏ đoạn "(leave empty to use active view)" trong helper text vì giờ hành vi đã tường minh.
  - `AutoDimensionDialog.py`: thêm handler `specific_views_changed()` — bật/tắt `pnl_views_list.Visibility`; khi tắt gọi `lst_views.UnselectAll()` để chắc chắn `_run` rơi về active view (không dính selection cũ). `Visibility` đã import sẵn (dùng ở `checking_mode_changed`/`offset_mode_changed`).
  - Verify: `ast.parse` OK · XAML well-formed · `audit_tools`/`audit_ui` clean · `sync_wpf_styles --check` 53/53. **Chưa user xác nhận trong Revit** — cần bật/tắt toggle + chạy dim ở cả 2 chế độ (mặc định active view / chọn nhiều view).

- **2026-07-03 — ManaSelect & ManaDWG (ManaDWG là DWGManagement.xaml, file UI-LOCKED — sửa lần này theo yêu cầu tường minh của user, override freeze)**: cả 2 tool hiện "gray gutter" — viền/khoảng trống màu xám cửa sổ (`#E4E4E7`) lộ ra quanh card trắng, do vùng content area không có background riêng (kế thừa background cửa sổ) trong khi card trắng bên trong có `Margin`/`CornerRadius` chừa khoảng hở.
  - `ManaSelect.xaml`: TabControl content (`main_tab_control`, Grid.Row=1/Col=1) có `Background="Transparent"` → tab 2/3/4 (`Margin="18"` bọc card trắng bo góc `CornerRadius="20"`) lộ gray ở viền 18px. Đã thêm 1 `Border Background="#F8FAFC"` lấp nền ngay trước TabControl (cùng pattern đã áp dụng ở ManaSheets/ModelAuditor...). Riêng tab 1 (Quick Select) là "bare mount point" (`grid_quick_select`) có background cứng `#E4E4E7` → đổi thành `#F8FAFC` cho khớp.
  - `ManaSelectDialog.py._init_sub_panels()`: bug riêng, nặng hơn — code re-parent nguyên `Border` gốc của cửa sổ `QuickElement.xaml` (khung chrome riêng: `BorderBrush="#A1A1AA" BorderThickness="1.5" CornerRadius="22" Background="#E4E4E7"`, thiết kế cho cửa sổ standalone) thẳng vào tab Quick Select của ManaSelect — tạo khung xám lồng bên trong khung chrome của chính ManaSelect. Đã sửa: chỉ re-parent `Grid` con (`quick_select_border.Child`), bỏ qua Border chrome ngoài.
  - `DWGManagement.xaml` (UI-LOCKED, user đã đồng ý override): vùng DataGrid (Grid.Row=1/Col=1) không có background riêng, card trắng `Margin="14,12,14,0"` lộ gray xung quanh. Fix đầu tiên: thêm 1 `Border Background="#F8FAFC"` lấp nền phía sau card — nhưng user báo lại thấy viền "light blue gutter" (chính là `#F8FAFC` lộ ra qua margin của card) và muốn table full-screen. Đã sửa lại: bỏ `Margin="14,12,14,0"` + `CornerRadius="20"` trên Border DataGrid (đặt `Margin="0"`, `BorderThickness="0"`), bỏ luôn Border lấp nền `#F8FAFC` (không cần nữa vì DataGrid giờ phủ kín ô Grid.Row=1/Col=1, không còn khe hở). Kết quả: bảng DWG chiếm trọn khu vực nội dung, không viền/góc bo/gutter màu nào lộ ra.
  - **2026-07-03 (tiếp)**: user yêu cầu áp dụng cách full-screen (đã dùng cho DWGManagement) sang ManaSelect thay vì chỉ đổi màu gutter. Đã sửa: bỏ `Margin="18"` + `CornerRadius="20"` trên card trắng ở Tab 2/3/4 (Select Similar / Select on Sheets / Quick Actions) → card phủ kín edge-to-edge; bỏ luôn Border lấp nền `#F8FAFC` phía sau TabControl (không cần nữa). Bảng kết quả thật sự nằm trong `QuickElement.xaml` (dùng lồng ở Tab 1 Quick Select) — sửa `Grid Grid.Row="2"` (Filter panel + DataGrid) từ `Margin="16,0,16,0"` → `Margin="0"`, bỏ `CornerRadius="8"` trên Border bọc DataGrid, để bảng chạm sát cạnh phải/dưới. Filter panel bên trái + khoảng cách 16px giữa nó và bảng vẫn giữ nguyên (không phải gutter, là spacing hợp lý giữa 2 panel).
  - Chưa được user xác nhận trong Revit (lần sửa full-screen mới nhất chưa test).
- **2026-07-03 — ManaSelect crash khi mở: `AttributeError: 'ManaSelectWindow' object has no attribute '_on_pick_similar_seed'`**: user báo traceback IronPython đầy đủ (`ManaSelectDialog.py` dòng 56, trong `__init__`). Nguyên nhân: dòng `self.btn_pick_similar_seed.Click += self._on_pick_similar_seed` wire sự kiện cho nút "Pick Element in Model" (tab Select Similar) tới 1 method chưa từng được định nghĩa trong class — code UI (`ManaSelect.xaml` dòng ~1217) đã có sẵn nút này nhưng phần logic bị thiếu/xoá nhầm ở lần sửa trước. Đã thêm `_on_pick_similar_seed(self, sender, e)` (`ManaSelectDialog.py`, ngay trước `_on_run_select_similar`): `self.Hide()` → `uidoc.Selection.PickObject(ObjectType.Element, ...)` (bắt `OperationCanceledException` khi user nhấn Esc) → set element vừa pick làm selection hiện tại của Revit (`SetElementIds` qua `dqt_compat.to_element_id_list`) để tương thích thẳng với `dqt_core.select_similar_*` (các hàm này đọc `uidoc.Selection.GetElementIds()`, không nhận tham số seed riêng) → cập nhật `txt_similar_seed_status.Text` hiện category + Id đã pick → `finally: self.Show()`. Theo đúng pattern Hide/Show đã dùng ở `ManaAnnoDialog.py` cho các dialog phụ. Chưa được user xác nhận mở lại OK + pick-seed hoạt động đúng trong Revit.
- **2026-07-03 — ManaAnno (và ManaPara, ManaContains ở panel khác): mở lên không hiện bảng, phải bấm tab khác rồi quay lại mới hiện**: user báo cùng lúc 3 tool dính triệu chứng giống hệt bug đã tìm/sửa ở ManaSheets/ManaViews (`panel-3-views-sheets.md` Phát sinh, mục "trang đầu tiên trống"). Nguyên nhân giống hệt: cả 3 tool dùng pattern "headerless TabControl" (`ContentPresenter ContentSource="SelectedContent"` trong `TabControl.Template`) điều hướng bằng RadioButton/ToggleButton sidebar có sẵn `IsChecked="True"` trong XAML — sự kiện `Checked`/`Click` của nút đã-checked-sẵn này bắn ra (hoặc đơn giản là never bắn, trường hợp `Click`) trước/không qua được lúc code Python kịp gắn handler, nên `TabControl.SelectedIndex` không bao giờ được set tường minh ở lần mở đầu → `ContentPresenter` không refresh nội dung tab đầu cho tới khi user chuyển tab. Đã sửa cả 3 file bằng đúng 1 dòng ở cuối `__init__`, theo mẫu đã dùng ở ManaSheets/ManaViews:
  - `ManaAnnoDialog.py`: thêm `self.main_tabs.SelectedIndex = 0` (TabControl `x:Name="main_tabs"`, nav mặc định `nav_dim`, khớp TabItem đầu = Dimensions).
  - `ManaContainsDialog.py`: thêm `self.tab_control.SelectedIndex = 0` (TabControl `x:Name="tab_control"`, nav mặc định `nav_contains`, khớp `tab_contains_item`).
  - `ManaParaDialog.py`: thêm `self.tab_main.SelectedIndex = 0` (TabControl `x:Name="tab_main"`, nav mặc định `btn_nav_browse`, khớp TabItem đầu = Browse Parameters).
  - Đã verify: `ast.parse` cả 3 file OK, `audit_tools.py --quiet` clean, `sync_wpf_styles.py --check` 0 lệch (không đụng XAML, chỉ sửa Python). Chưa được user xác nhận mở lại OK trong Revit cho cả 3 tool.
- **2026-07-03 — QuickElement (Quick Select, tab con của ManaSelect): dời Summary Cards xuống trên Action Buttons** (yêu cầu UI trực tiếp từ user, theo đúng pattern đã áp dụng ở ManaViews/ManaSheets): `QuickElement.xaml` đổi thứ tự `Grid.RowDefinitions` — Main Content lên `Row=1` height `*`, Summary Cards (TOTAL ELEMENTS/CHECKED/CATEGORIES/FAMILIES) xuống `Row=2` height `Auto`, ngay trên Action Buttons (`Row=3`, không đổi). Header (`Row=0`) và Footer (`Row=4`) giữ nguyên index vì `ManaSelectDialog.py._init_sub_panels()` collapse 2 hàng này bằng index cứng (`RowDefinitions[0]`/`RowDefinitions[4]`) khi nhúng vào tab Quick Select — đã kiểm tra lại code đó để đảm bảo không vỡ. Verify `sync_wpf_styles.py --check` (0 lệch) + `audit_ui.py --quiet` (0/54 vấn đề). Chưa được user xác nhận trong Revit.
