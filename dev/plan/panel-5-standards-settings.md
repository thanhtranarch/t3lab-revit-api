# Panel 5 — Standards & Settings (Ngày 10)

> 4 tool nhưng đụng vào **cài đặt toàn model** (coordinates, object styles, workset).
> ⚠️ BẮT BUỘC test trên **bản copy** của model, không dùng file thật. Cần thêm 1 file workshared (tạo local copy) cho ManaWorkset.

## Tool 1/4 — ManaLoca ⚠️ nguy hiểm nhất panel
Chain: self-contained (871 loc) → `ManaLoca.xaml` · thao tác site/shared coordinates

- [ ] **Chỉ chạy trên file copy**
- [ ] Đọc/hiển thị đúng Project Base Point, Survey Point, shared site hiện tại
- [ ] Đổi 1 giá trị toạ độ → model dịch chuyển đúng hướng/đơn vị
- [ ] Ctrl+Z revert được thay đổi toạ độ
- [ ] File có nhiều shared site → list đủ, switch đúng
- [ ] Huỷ giữa chừng → không để model ở trạng thái toạ độ dở dang
- [ ] Ghi chú:

## Tool 2/4 — ManaStyles
Chain: launcher → `ManaStylesDialog.py` (2091 loc) → `ManaStyles.xaml`

- [ ] List object styles/line weights/line patterns đúng với Manage > Object Styles
- [ ] Đổi 1 style → áp dụng toàn model, Ctrl+Z OK
- [ ] Category không cho sửa (system) → disable hoặc thông báo
- [ ] Import/export style config (nếu có) → file đúng format
- [ ] Ghi chú:

## Tool 3/4 — ManaWorkset
Chain: self-contained (609 loc, 3 transaction) → `ManaWorkset.xaml`

- [ ] **File thường (không workshared)** → thông báo thân thiện yêu cầu enable worksharing, KHÔNG stacktrace (đây là edge case hay quên nhất)
- [ ] File workshared: list workset đúng
- [ ] Tạo workset mới → xuất hiện trong Revit
- [ ] Rename workset → OK; workset default (Workset1, Shared Levels and Grids) → xử lý đúng giới hạn Revit
- [ ] Element thuộc workset của user khác (chưa checkout) → thông báo ownership
- [ ] Ghi chú:

## Tool 4/4 — ModelAuditor
Chain: launcher → `ModelAuditorDialog.py` (1828 loc) → `ModelAuditor.xaml`

- [ ] Model nhỏ → report đầy đủ các hạng mục audit, số liệu đúng (đối chiếu tay 1–2 mục: số warning, số family in-place...)
- [ ] Model lớn → đo thời gian chạy, UI không treo (có progress?)
- [ ] Export report (nếu có) → file ra đúng
- [ ] Model rỗng → report không crash
- [ ] Ghi chú:

## Chốt ngày 10
- [x] Cập nhật bảng + README · Commit: `fix(panel-standards): <mô tả>`

| Tool | Trạng thái | Ghi chú |
|------|-----------|---------|
| ManaLoca | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| ManaStyles | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |
| ManaWorkset | ✅ | Gray-gutter fix đã sửa 2026-07-03 · user xác nhận chung 2026-07-05: hoạt động tốt |
| ModelAuditor | ✅ | User xác nhận chung 2026-07-05: hoạt động tốt |

> 2026-07-05 — user xác nhận tổng quát: **"các tool hiện tại đều đã hoạt động tốt"** (đóng
> luôn Ngày 10 theo xác nhận này — không có phiên review tĩnh riêng cho panel này). Mở lại
> nếu phát hiện lỗi khi dùng thực tế.

## Phát sinh

- **2026-07-03 — ManaWorkset: gray gutter quanh vùng nội dung** (yêu cầu UI trực tiếp từ user, không phải bug): `ManaWorkset.xaml` có `TabControl x:Name="tab_control"` (Grid.Row="1"/Col="1") với `Background="Transparent"` → nền xám cửa sổ `#E4E4E7` lộ ra qua mọi khe hở giữa các card trắng bên trong (cùng lớp bug đã gặp ở ManaSelect/ManaDWG/PDFImport, xem `panel-1-annotation-select.md` Phát sinh). Đã sửa theo đúng pattern chuẩn: bọc toàn bộ `TabControl` trong 1 `Border Background="#F8FAFC"` full-bleed (không margin) phủ kín ô Grid.Row=1/Col=1, không còn khe hở nào lộ màu xám gốc. Verify: XML well-formed, `audit_tools.py --quiet` clean, `sync_wpf_styles.py --check` 53/53 khớp. Chưa được user xác nhận trực quan trong Revit.
- **2026-07-03 (tiếp) — ManaWorkset: user báo tiếp "remove all gutter or gap" sau lần fix nền F8FAFC đầu tiên** — lần fix trước chỉ đổi màu (xám → F8FAFC) chứ chưa bỏ khoảng hở kết cấu: cả 3 tab (Worksets/Bulk Tools/3D Views) đều dùng pattern "floating card" — card nội dung chính có `Margin` (18px các cạnh) + `CornerRadius="20"` nổi giữa nền, để lộ viền F8FAFC quanh card dù không còn xám. Áp dụng đúng pattern "full-screen" đã dùng cho DWGManagement/ManaSelect trước đây (xem `panel-1-annotation-select.md` Phát sinh, mục "user yêu cầu áp dụng cách full-screen"): bỏ `Margin`/`CornerRadius` của card chính ở cả 3 tab, card giờ phủ kín edge-to-edge, chỉ giữ `BorderThickness="1"` (viền mảnh, không bo góc, không margin) — Tab 1 "Worksets card" (`Margin="18,14,18,18"` → `"0"`, `CornerRadius="20"` → `"0"`), Tab 2 "Bulk Tools" (`Grid Margin="18"` → bỏ, `CornerRadius="20"` → `"0"`), Tab 3 "3D Views" (tương tự Tab 2). Giữ nguyên `Padding` nội bộ của card (đó là khoảng cách nội dung bên trong, không phải gutter lộ nền ngoài) và banner cảnh báo "Enable Worksharing" ở Tab 1 (alert box nhỏ, có margin riêng theo đúng chuẩn thiết kế, không phải là "cái bảng" đang bị phàn nàn). Verify: XML well-formed, `audit_tools.py --quiet` clean, `sync_wpf_styles.py --check` 53/53 khớp. Chưa được user xác nhận trực quan trong Revit.
- **2026-09-29 — ManaLoca: redesign layout theo T3** (yêu cầu UI trực tiếp từ user: "layout look ugly"). Lỗi nhìn thấy trên ảnh chụp: toolbar 7 nút + combo trong một `WrapPanel` bị rớt dòng ở chế độ By Level, nút Apply lơ lửng giữa 2 hàng; rail trái chỉ có 2 icon khó hiểu (hamburger = Current View?); "Override Odd" nút đỏ Danger to hơn cả nút primary; nút All/None cao 24 tự chế; toạ độ `StringFormat={0:F2}` không chạy vì giá trị là PyObject → bảng hiện `25619.4021` lẫn `0.0`, lại căn trái; empty state `Visibility="Collapsed"` không bao giờ hiện; footer bên phải trống. **Layout mới**: title bar (search dời sát nút cửa sổ) · dải SOURCE (chip `T3.Chip` Current View / By Level + combo level, Refresh · Get Selection · Pick Elements) · 2 pane có header cùng cao 44px — CATEGORIES (All/None ghost, số đếm căn phải Consolas) | ELEMENTS (Select in Revit ghost · Round to 5 mm · Override Odd · Clear Overrides, cùng Secondary) · bảng + dải tally chung khung (`N of M shown` · ● `edited` · ● `odd`) · footer © + status (ellipsis + tooltip) + **Apply Changes (n)** primary. **Bảng**: X/Y/Z bind chuỗi đã format 2 chữ số (`x_mm`/`y_mm`/`z_mm`, setter parse, gõ sai giữ giá trị cũ), Consolas căn phải, header căn phải cùng lề; ô sửa mà chưa Apply tô vàng (`dirty_*_mm`, luật "ô vàng, ghi khi ấn Apply"); toạ độ không tròn mm chữ đỏ (`odd_*_mm`, cùng ngưỡng `ODD_TOL_MM` với Override Odd). Sửa ô → `CellEditEnding` → redraw qua dispatcher; Apply/Round commit ô đang gõ trước. Logic Revit không đổi (vẫn 1 transaction "Move Elements"). Gate: audit_t3 · audit_tools · audit_wiring · audit_revit_compat · audit_cpython (0 P0) · audit_api_context · sync --check · test_ui_overlap · test_xaml_names · check_xaml_load đều xanh. **NEEDS VERIFICATION trong Revit**: `check_xaml_wpf.ps1` (cần Windows) · ô vàng/chữ đỏ có hiện · sửa ô rồi Apply · Round to 5 mm · By Level combo không rớt dòng · 100% và 125%.
- **2026-09-29 — ManaStyles: cột ILLUSTRATION trống ở cả 3 tab** (user báo "missing illustrate" kèm ảnh tab Line Styles). Gốc: dòng là object Python → WPF nhận PyObject, đổi được sang chữ nhưng KHÔNG đổi được sang `Brush` / `double` / `DoubleCollection` → `Stroke="{Binding color_hex}"`, `StrokeThickness`, `StrokeDashArray`, và `Background="{Binding wpf_brush}"` (Fill Patterns) đều hỏng im lặng, không vẽ gì (cùng lớp lỗi với luật 24/25). Sửa: mọi giá trị hình vẽ là **chuỗi**, XAML đọc qua TextBlock ẩn (string bridge) để TypeConverter của WPF parse — Line Styles / Line Patterns: `color_hex` · `thickness_val` · `dash_view` (dash quy về bội số StrokeThickness, kéo dài theo nét dày); Fill Patterns: `preview_geometry` là Path Data vẽ đúng góc + khoảng cách của các fill grid thật (quy về 3–12 px; solid = hình chữ nhật đặc), thay DrawingBrush 3 kiểu cố định. Kèm: cột COLOR 100 → 144 (chuỗi `RGB(255,128,0)` bị cắt). Gate: audit_t3 · audit_tools · audit_wiring · audit_revit_compat · audit_cpython (0 P0) · audit_api_context · sync --check · test_ui_overlap · test_xaml_names · test_checkbox_bridge · check_xaml_load xanh. **NEEDS VERIFICATION trong Revit**: 3 tab đều thấy hình minh hoạ, màu/độ dày/nét đứt khớp Revit.
