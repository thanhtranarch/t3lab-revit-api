# PRIORITY QUEUE — cập nhật 2026-09-15

Hàng đợi hiện tại dựa trên [review có bằng chứng](REVIEW-2026-09-13.md).
Các sửa transaction P1 đã hoàn tất ở mức source/test trong [đợt B](REVIEW-2026-09-14-PHASE-B.md).
Đường BatchOut từ Assistant và selection/empty state đã sửa ở [đợt C](REVIEW-2026-09-14-PHASE-C.md); còn cần runtime verification.
Không migrate lại 59 XAML đã đạt T3. Các checkbox migration bên dưới giữ làm lịch sử.
RibbonNames và kết quả export native được sửa ở [đợt D](REVIEW-2026-09-15-PHASE-D.md); còn cần thử trong Revit.

| Thứ tự | Mức | Hạng mục | Điều kiện hoàn thành |
|---|---|---|---|
| 1 | P1 | Verify các sửa SelectFromDict, ParameterSelector, Workset, Smart Purge trong Revit | Happy path, failure, Cancel, Undo, click lần 2; ghi kết quả thực |
| 2 | P1 | Verify AutoJoin / Advanced Purge / Group Manager trong Revit; mã sửa và 37 test mới đã qua | Failure dialog, Pending, Undo, hủy giữa chừng và tên hoán đổi trên model thật |
| 3 | P2 | Verify BatchOut direct/configured/native sau đợt C/D | File thật khớp kết quả; Stop, PDF gộp/IFC, Pending, cấu hình không bị ghi đè |
| 4 | P2 | ManaSheets / ManaViews / SheetGen: preview, validation, recovery | Preview khớp dữ liệu sau thao tác và thông báo lỗi theo item |
| 5 | P2 | Modeless và hình học | Đổi/đóng document, dữ liệu lớn, progress và cancel |
| 6 | P3 | Phân loại 444 cảnh báo C5/C6 | Xác minh từng nhóm, không nới gate |
| 7 | P3 | Chấm UX theo rubric | Test keyboard và DPI 100/125%, không gán điểm từ static gate |
| 8 | P2 | Runtime verify ManaFami, ParameterSelector/AutoJoin và RibbonNames sau đợt C/D | Kiểm chứng binding/DPI; RibbonNames đổi hai lần, lưu, mở lại, restore và lỗi persistence |

---

## Lịch sử hàng đợi 2026-08-28 (không phải trạng thái hiện tại)

> Cập nhật cuối: 2026-08-28 (cycle 0 — bootstrap + dọn dẹp)
> Thứ tự xếp hàng theo `04-severity-and-issues.md` §4. Cycle sau vào là chạy từ trên xuống.

---

## P1 — HIGH

*(trống — Q1 đã xong 2026-08-28)*

---

## P2 — MEDIUM

### Q2 · Cycle 1: migrate từng tool sang T3

- [x] **Tool 1: `SplitElements`** — Đã hoàn thành migrate sang T3 (100/100 điểm, 0 vi phạm).
- [ ] **Tool 2: `RibbonNames`** (`Tools/RibbonNames.xaml`) — Đang chờ tiếp theo.
- [ ] **Tool 3: `Feedback`** (`Tools/Feedback.xaml`)
- [ ] **Tool 4: `SelectFromDict`** (`Tools/SelectFromDict.xaml`)
- [ ] **Tool 5: `ManaTabs`** (`Tools/ManaTabs.xaml`)
- [ ] **Tool 6: `SubtypeDefinerColMap`** (`Tools/SubtypeDefinerColMap.xaml`)

Lý do bắt đầu từ file nhỏ: hiệu chỉnh rubric trên file dễ trước, tránh chấm sai hàng
loạt trên file 2000 dòng rồi phải chấm lại.

### Q3 · Cycle 1–2: migrate 2 file đầu tiên sang T3

Chọn sau khi Q2 xong (cần biết pattern của từng tool trước). Ứng viên ưu tiên: tool
nhỏ, một pattern rõ ràng, ít binding — để quy trình migration ở
`08-design-system-authority.md` §6.2 được thử ở chi phí thấp nhất.

### Q4 · Quét toàn bộ 51 file cho 3 lỗi P0 tiềm ẩn

Chạy ngay ở cycle 1, không cần audit sâu:

```bash
grep -rln '<Grid\.\(Row\|Column\)Definition' T3Lab.extension/lib/GUI/Tools/
grep -rln 'DropShadowEffect\|<.*\.Effect>'   T3Lab.extension/lib/GUI/Tools/
grep -rlzoP '(?s)<ScrollViewer.{0,400}?<(DataGrid|ListBox|ListView)' T3Lab.extension/lib/GUI/Tools/
```

Lệnh 1 tìm crash `EMPTYPROPERTYELEMENT`; lệnh 3 tìm nguyên nhân treo Revit phổ biến
nhất (list bị bọc `ScrollViewer` → mất virtualization). Cả hai là **P0** nếu có kết quả.

Vì sao vẫn cần grep dù đã có `audit_t3.py`: script chỉ soi đầy đủ file **đã khai T3**.
51 file legacy không được soi, nên 2 lỗi P0 này phải quét thủ công cho tới khi chúng
được migrate.

---

## DESIGN SYSTEM GAPS — chờ user duyệt

### GAP #2 · Standard định nghĩa size class S/M/L nhưng stylesheet không cung cấp cách áp

`T3LAB_UI_STANDARD.md` quy định `S 420×260–320` · `M 560×420–560` · `L 1000×620`, nhưng
`T3Lab.Styles.xaml` không có `Style` nào `TargetType="Window"`. Mỗi tool sẽ tự đặt
`Width`/`Height`/`MinWidth` bằng tay → chính là loại inconsistency mà Design System sinh
ra để chống.

**Đề xuất:** thêm `T3.Window.S` / `T3.Window.M` / `T3.Window.L` vào `T3Lab.Styles.xaml`.

---

## Đã hoàn thành

### ✅ GAP #4 · Chrome wizard & cửa sổ — 2026-08-28

Đóng bằng hướng 1 (thêm component), theo quyết định của user. 8 component vào
`T3Lab.Styles.xaml`: `T3.WinCtrl` · `T3.WinClose` · `T3.TabItem.Hidden` ·
`T3.Rail.Tile` · `T3.Rail.Logo` · `T3.Pill` · `T3.ComboBox.Toggle` ·
`T3.ScrollBar.Thumb`. Stylesheet 84 → 92 key.

Đo lại khi bắt tay vào thì gap nhỏ hơn ước tính ban đầu: trong 26 `<Style>` bị báo,
**15 là định nghĩa chết** (không ai tham chiếu) và **5 là implicit style** hợp lệ —
chỉ 6 cái thật sự cần nâng thành component. Xoá 15 style chết gỡ luôn phần lớn vi
phạm `CornerRadius`.

Hai chỗ chỉnh trong gate, cả hai là lỗi của gate chứ không phải nới luật:
- `<Style>` không có `x:Key` là override cục bộ, stock WPF không có cách khác —
  chỉ style **có** `x:Key` mới là component đặt sai chỗ.
- `PENDING_GAP` nay **rỗng**: BatchOut 0 vi phạm, 0 waiver.

### ✅ GAP #1 · Vị trí deploy `T3Lab.Styles.xaml` — 2026-08-28

Chốt: thư mục `pyRevit UI Design System/` là **file mẫu để refer**, không phải artifact
runtime. `python3 dev/sync_t3_styles.py` **nhúng** nội dung stylesheet vào
`<Window.Resources>` của từng tool XAML, giữa hai marker `T3 STYLES`.

**Bản `MergedDictionaries` đã thử và THẤT BẠI** (2026-08-28): pyRevit nạp XAML bằng
`XamlReader` không có base URI → `IOException: Assembly.GetEntryAssembly() returns null`
→ BatchOut chết ngay lúc mở. Chi tiết + hai cái bẫy khi nhúng:
`08-design-system-authority.md` §5.

### ✅ GAP #3 · Copyright — 2026-08-28

Chốt: **BẮT BUỘC** trong UI của mọi tool. Đã thêm token `T3.Copyright #F59E0B` + style
`T3.Copyright` vào stylesheet, luật 11 vào `T3LAB_UI_STANDARD.md`, luật 13 vào
`.claude/rules/new-tool-standard.md`, và `audit_t3.py` enforce trên **mọi** file kể cả
legacy. Hiện 53/53 file có đúng một dòng (`CadtoFloorLayerItem.xaml` là item-template,
miễn trừ theo chuẩn). Đã bổ sung cho `T3LabAssistant.xaml` — file duy nhất còn thiếu.

### ✅ Q1 · Thay `dev/audit_ui.py` bằng `dev/audit_t3.py` — 2026-08-28

`dev/audit_t3.py` enforce luật T3, chia file làm **T3-DECLARED** (soi đầy đủ, vi phạm ⇒
`exit 1`) và **LEGACY** (chỉ đếm, không fail gate) — nên gate xanh ngay hôm nay dù
0/51 file đạt chuẩn, và siết dần theo từng file được migrate. Cờ: `--quiet` ·
`--legacy` · `--legacy-count` · `--file`.

Đã xoá cùng đợt: `dev/audit_ui.py`, `dev/sync_wpf_styles.py`,
`.claude/rules/ui-design-standard.md`, `.claude/standard/UIStandardShowcase.xaml`,
`.claude/docs/wpf-window-templates.md`, `.claude/agents/ui-police-agent.md`,
`.codex/agents/ui-police-agent.toml`, và 6 script migration one-shot
(`fix_ui.py`, `migrate_xaml_colors.py`, `add_sidebar_nav.py`, `export_palette.py`,
`mute_colors.js`, `refine_palette.js`).

Đã thêm: `.claude/rules/new-tool-standard.md` — luật cho **mọi tool/script mới**.
Đã viết lại theo T3: `.claude/agents/ui-agent.md`, `.claude/skills/xaml-templates.md`,
`.codex/agents/ui-agent.toml`.

**Kiểm chứng:** dựng 1 XAML cố ý sai → bắt đủ 18 vi phạm, `exit 1`; dựng 1 XAML từ
đúng snippet trong `xaml-templates.md` → `exit 0`. Tài liệu và gate khớp nhau.
