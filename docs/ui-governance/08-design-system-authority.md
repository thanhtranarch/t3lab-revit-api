# 08 · Design System Authority — một chuẩn duy nhất

> **Đọc file này trước khi sửa bất kỳ XAML nào.**

---

## 1 · Chuẩn duy nhất

```
pyRevit UI Design System/
├── T3LAB_UI_STANDARD.md    ← luật: token, type, spacing, 5 pattern, luật bố cục
└── T3Lab.Styles.xaml       ← 82 resource key T3.* — nguồn duy nhất của màu/size/style
```

**Đây là nguồn chuẩn cao nhất và là nguồn chuẩn duy nhất.**
Mọi hệ UI khác trong repo đã bị **bỏ**. Không có "track", không có ngoại lệ theo file,
không có chuẩn song song.

## 2 · Những gì đã bị bỏ (LEGACY — không còn hiệu lực)

| Hệ đã bỏ | Đặc điểm nhận dạng | Xử lý khi gặp |
|----------|--------------------|----------------|
| **Lumina** | `FontFamily="Hanken Grotesk"` / `"Inter"`, nền `White`, hex cứng `#0F172A` `#3B82F6` `#E2E8F0` `#CBD5E1` `#64748B` `#94A3B8` `#F8FAFC` `#10B981` `#EF4444`, block `T3LAB SHARED STYLES` | Ghi là **LEGACY DEBT**, migrate sang `T3.*` theo hàng đợi |
| **Revit-native** | `{DynamicResource T3Theme*}`, `RevitTheme.py`, Segoe UI 12, `WindowChrome CaptionHeight="56"` | Như trên |
| **Terra v2** | `#0F766E` `#115E59` `#0B4F4A` `#F6F8F8` `#E6EDEC` `#D9EBE8` `#DDE5E7` `#C7D2D4` `#15803D` `#166534` `#AEBFBC` | Như trên, ưu tiên cao hơn (chết lâu nhất) |
| **Kinetix** | `#083D56` | Như trên |

**Các file luật cũ đã bị XOÁ (2026-08-28):** `.claude/rules/ui-design-standard.md`,
`.claude/standard/UIStandardShowcase.xaml`, `.claude/docs/wpf-window-templates.md`,
`.claude/agents/ui-police-agent.md`, `dev/audit_ui.py`, `dev/sync_wpf_styles.py`, cùng
6 script migration one-shot của chuẩn cũ. Nội dung vẫn còn trong lịch sử git nếu cần
tra. Dấu hiệu nhận dạng legacy nay sống trong `dev/audit_t3.py` (`DEAD_PALETTE`,
`DEAD_FONTS`) chứ không còn trong tài liệu.

`lib/GUI/Resources/WPF_styles.xaml` và block `T3LAB SHARED STYLES` bên trong 51 XAML
chưa migrate được **đóng băng**: không còn được đồng bộ, và biến mất dần theo từng file
được migrate.

## 3 · Thứ tự thẩm quyền

```
1. Ràng buộc kỹ thuật & an toàn Revit  (IronPython 2.7, .NET 4.8, virtualization, transaction)
2. pyRevit UI Design System/           ← chuẩn thiết kế duy nhất
3. Sở thích cá nhân                    ← không bao giờ
```

Chỉ mục 1 được phép thắng mục 2, và chỉ khi có lý do kỹ thuật viết ra được. Ví dụ hợp
lệ: một list phải bật virtualization dù Design System không nói tới — vì không bật thì
treo Revit. Ví dụ **không** hợp lệ: "để nguyên màu cũ cho đỡ khác biệt".

---

## 4 · Tóm tắt luật T3 (bản tra nhanh — bản đầy đủ ở `T3LAB_UI_STANDARD.md`)

### 4.1 · Ràng buộc kỹ thuật

- Chỉ stock WPF. Không thư viện thứ ba (MahApps, HandyControl, custom DLL).
- IronPython 2.7 / .NET 4.8 / `pyrevit.forms.WPFWindow`.
- **Không effect**: không gradient, không blur, không transition, không custom scrollbar.
- Đọc được ở 100% **và** 125% display scaling.

### 4.2 · Token — không tool nào được tự định nghĩa màu/size/margin

| Nhóm | Token |
|------|-------|
| Chữ | `T3.Ink #18181B` · `T3.Text #27272A` · `T3.TextSecondary #52525B` · `T3.TextMuted #71717A` · `T3.TextDisabled #9A9AA2` |
| Viền | `T3.Border #DCDCE0` · `T3.BorderStrong #A1A1AA` |
| Nền | `T3.Surface #FFFFFF` · `T3.SurfaceSunken #F4F4F6` · `T3.Canvas #E4E4E7` · `T3.RowAlt #FAFAFB` · `T3.RowRule #F1F1F4` |
| Success | `Fill #EAF8F0` · `Accent #22A85C` · `Text #157038` |
| Warning | `Fill #FFF6E6` · `Accent #F5CE5A` · `Text #8A6308` |
| Danger | `Fill #FDECEC` · `Accent #F87171` · `Text #D23B3B` · `Border #F6D5D5` |
| Progress | `Track #ECECEF` · `Fill #C2410C` |

> Cam `#C2410C` **CHỈ** dùng cho progress / trạng thái đang chạy. Không làm button,
> không làm border, không làm brand.

### 4.3 · Type — Segoe UI, đúng 7 size

`Display 19 SemiBold` · `Title 15 SemiBold` · `Body 13 Regular` · `BodyStrong 13 SemiBold`
· `Caption 11.5` · `Label 11 SemiBold uppercase` · `Mono 12.5 Consolas`

Không size ngoài danh sách. Không font khác Segoe UI / Consolas.

### 4.4 · Spacing · chiều cao · bo góc · size window

- Spacing: chỉ `4 / 8 / 12 / 16 / 24 / 32`. Mọi margin chia hết cho 4.
- Chiều cao: row `26` · control `28` · action button `30` · title bar `40` · footer `48`.
- Bo góc: window `8` · control `4` · pill `2` · grid row `0`.
- Size window: `S 420×260–320` (NoResize) · `M 560×420–560` · `L 1000×620`.

### 4.5 · 10 luật bố cục

| # | Luật |
|---|------|
| L1 | Một left rail duy nhất: 16px từ mép window, 12px trong panel lồng |
| L2 | Label nằm **TRÊN** control (4px); nhóm field cách nhau 12px. Không label cột bên trái |
| L3 | Mỗi grid chỉ **một** cột `*` (là Name). Cột khác fix px: ID 90 · Category 140 · Status 150–170 · số 70 |
| L4 | `HorizontalScrollBarVisibility="Disabled"` mọi grid/list. Không đủ chỗ thì **bỏ bớt cột** |
| L5 | Số căn phải (Consolas) · text căn trái · Element ID căn trái Consolas |
| L6 | Footer cố định: trái = dot + câu trạng thái; phải = secondary → secondary → **MỘT** primary. Gap 8. **Không nút nào chỉ để đóng cửa sổ** ngoài nút X title bar — Close / Cancel / Done chỉ gọi `Close()` là lặp việc của X (luật 29); giữ nút làm thêm việc (kết quả, dừng tác vụ, rollback, hỏi lại) và Cancel/No của dialog trả lời |
| L7 | Panel lồng tối đa 2 cấp. Chia section bằng label uppercase + `Separator`, **không** bằng card |
| L8 | `UseLayoutRounding="True"` · `SnapsToDevicePixels="True"` · `TextOptions.TextFormattingMode="Display"` trên Window. Luôn có `MinWidth`/`MinHeight`. Không set `Height` cho `TextBlock`. Không fix `Width` cho text dịch |
| L9 | `IsCancel` trên nút X title bar (`T3.WinClose`). `IsDefault` bắt buộc khi có `T3.Button.Primary` (đặt trên nó); cửa sổ chỉ hiện trạng thái / cài đặt áp dụng ngay thì không cần. P5: `IsDefault` trên Cancel. Focus = viền 1px `T3.Ink`, không dotted rectangle |
| L10 | Mọi list/grid có **empty state**: TextBlock canh giữa, `T3.TextDisabled`, nói thiếu gì và làm gì tiếp |

### 4.6 · 5 pattern — mọi tool phải là một trong số này

| Pattern | Size | Đặc trưng bắt buộc |
|---------|------|--------------------|
| **P1** Parameter input form | M | Form một cột · `Expander` cho Advanced · callout hệ quả **có số lượng** trên footer |
| **P2** Element selection list | M | Filter pinned · `ListBox` virtualized · All/None ghost ở footer · primary **mang số đếm** |
| **P3** Progress & log | M | Phase + `n / total` + item hiện tại · bar 8px cam · log `ListBox` Consolas màu theo severity **kèm chữ** (`ok`/`skipped`/`failed`) · dải tally · footer "đang chạy, đừng đóng Revit" · Cancel lúc chạy là Stop, xong thì ẩn (không đổi thành Close — nút X làm việc đó), dot xanh |
| **P4** Results table | L | Dải summary 52px (số + label, chia bằng rule 1px) · chip filter · `DataGrid` row 26 · status pill nền tint + dot + chữ · primary trả người dùng về Revit ("Select in Revit") |
| **P5** Confirmation | S | Headline là **câu hỏi có số** ("Delete 34 view templates?") · nút phá huỷ đỏ mang tên thao tác + số · nút đó **KHÔNG** `IsDefault` (Cancel mới là) · bản success dùng lại vỏ này ở thì quá khứ |

### 4.7 · Checklist review (dán vào PR)

Merge `T3Lab.Styles.xaml`, không tự định nghĩa brush · đúng một trong P1–P5 · size S/M/L
· Segoe UI 13, không size lạ · margin chia hết 4 · đúng một primary, ngoài cùng phải
· `IsCancel` trên nút X, `IsDefault` trên primary · không nút Close/Cancel/Done nào chỉ để đóng
· grid một cột `*`, tắt scroll ngang · có empty state
· chụp màn hình 100% và 125% không cắt chữ · thao tác phá huỷ có P5 · status không bao
giờ chỉ bằng màu.

### 4.8 · Khi viết tool mới

Hỏi tool thuộc pattern nào, rồi sinh XAML dùng `{StaticResource T3.*}` — không hardcode
hex/size/margin. **Không tạo style mới trong file tool**; style mới phải thêm vào
`T3Lab.Styles.xaml` (và việc thêm style là `MANUAL REVIEW REQUIRED`).

---

## 5 · Cách merge stylesheet vào một tool

```bash
python3 dev/sync_t3_styles.py           # nhúng stylesheet vào mọi tool đã khai T3
python3 dev/sync_t3_styles.py --check   # verify, exit 1 nếu lệch
```

Script chép nội dung `T3Lab.Styles.xaml` vào `<Window.Resources>` của từng tool XAML,
giữa hai marker `T3 STYLES`. **Không sửa tay khối đó** — sửa ở nguồn rồi chạy sync.

### Vì sao nhúng chứ không `MergedDictionaries`

Đã thử `<ResourceDictionary Source="../Resources/T3Lab.Styles.xaml"/>` và pyRevit
**chết ngay lúc mở tool** (BatchOut, 2026-08-28):

```
System.IO.IOException: Assembly.GetEntryAssembly() returns null.
Set the Application.ResourceAssembly property or use the
pack://application:,,,/assemblyname;component/ syntax ...
```

pyRevit nạp XAML bằng `XamlReader` trên một stream, nên WPF không có base URI và
không có entry assembly; mọi `Source` tương đối bị resolve thành `pack://application`
rồi ném IOException. Nạp dictionary từ Python **sau** khi cửa sổ dựng xong cũng không
cứu được, vì `{StaticResource}` trong XAML được resolve NGAY LÚC PARSE.

Nhúng là cách duy nhất còn lại — và là cách repo này vốn đã dùng cho chuẩn Lumina cũ.
Giá phải trả: mỗi tool XAML nặng thêm ~970 dòng sinh tự động.

### Hai cái bẫy khi nhúng

1. **`x:Key` trùng** giữa khối nhúng và style của tool → WPF ném
   `Item has already been added` ngay lúc parse. `audit_t3.py` bắt lỗi này ở mức **P0**.
2. **`StaticResource` trên chính thẻ `<Window>`** không resolve được — attribute của
   Window parse TRƯỚC `Window.Resources`. Dùng `DynamicResource` ở đó.

---

## 6 · Migration — luật vàng của giai đoạn chuyển tiếp

Hiện trạng: **0/54 XAML đạt chuẩn T3**. Toàn bộ là legacy debt. Điều đó là bình thường
và không phải lỗi của cycle nào — nhiệm vụ của routine là kéo con số đó xuống, đều đặn,
không phải trong một đêm.

### 6.1 · Hai loại công việc

| Loại | Nội dung | Auto-fix? |
|------|----------|-----------|
| **In-place fix** | Sửa lỗi T3 mà không đổi hệ màu/font của file: spacing chia 4, một primary, `IsDefault`/`IsCancel`, empty state, tắt scroll ngang, virtualization, bỏ `Effect`, dot-notation crash, status có chữ | ✅ Có — đây là công việc thường ngày |
| **Full migration** | Chuyển hẳn một file sang `T3.*` + Segoe UI 13 + pattern P1–P5 + size class | ⚠️ Có, nhưng **tối đa 2 file/cycle**, mỗi file phải verify riêng |

Lý do tách: in-place fix rủi ro thấp, làm được hàng loạt. Full migration đụng toàn bộ
visual của một cửa sổ nên **bắt buộc có QA trong Revit** trước khi đánh `FIXED`.

### 6.2 · Quy trình full migration một file

1. Xác định pattern (P1–P5) và size class (S/M/L). Không rõ → `NEEDS REVIEW`, dừng.
2. Ghi lại mọi `x:Name` đang có → script.py bám vào chúng. **Không đổi tên, không xoá.**
3. Ghi lại mọi `Click=` / `SelectionChanged=` / binding → phải còn nguyên sau migrate.
4. Thay style: merge `T3Lab.Styles.xaml`, xoá mọi brush/style tự định nghĩa, xoá block
   `T3LAB SHARED STYLES` của Lumina.
5. Áp 10 luật bố cục (§4.5) + yêu cầu riêng của pattern (§4.6).
6. Verify theo `05-improvement-and-safety.md` — bao gồm **đối chiếu `x:Name`** trước/sau.
7. Đánh `NEEDS VERIFICATION` cho tới khi user xác nhận đã mở trong Revit.

### 6.3 · Không bao giờ

- Đổi hoặc xoá `x:Name` mà script.py đang dùng.
- Đổi tên event handler.
- Xoá một control chỉ vì Design System không có chỗ cho nó → `NEEDS REVIEW`.
- Migrate file UI-locked (§7).
- Migrate quá 2 file trong một cycle.

## 7 · File UI-locked — bỏ qua hoàn toàn

Ba file dưới đây có thiết kế đã chốt riêng và **không thuộc phạm vi routine**:

- `T3Lab.extension/lib/GUI/Tools/DWGManagement.xaml`
- `T3Lab.extension/lib/GUI/Tools/ExportManager.xaml`
- `T3Lab.extension/lib/GUI/Tools/T3LabAssistant.xaml`

Liệt kê trong BASELINE với trạng thái `LOCKED`, điểm `—`, không tính vào Overall Score.
Việc gỡ khoá là quyết định của user, không phải của routine.

## 8 · Regression gate

| Script | Gác gì | Trạng thái |
|--------|--------|-----------|
| `dev/audit_tools.py --quiet` | Code tĩnh (bundle, cấu trúc script) — trung lập với Design System | ✅ Gate |
| `dev/audit_t3.py --quiet` | **Luật T3** (§4) | ✅ Gate |
| `dev/sync_t3_styles.py --check` | Khối stylesheet nhúng khớp nguồn | ✅ Gate |
| ~~`dev/audit_ui.py`~~ · ~~`dev/sync_wpf_styles.py`~~ | Chuẩn Lumina đã bỏ | ❌ **Đã xoá 2026-08-28** |

### `dev/audit_t3.py` hoạt động thế nào

Script chia file làm hai loại, và đó là lý do gate xanh được ngay hôm nay dù 0/51 file
đạt chuẩn:

| Loại | Điều kiện | Xử lý |
|------|-----------|-------|
| **T3-DECLARED** | Đã merge `T3Lab.Styles.xaml` **hoặc** đã dùng `{StaticResource T3.*}` | Soi **đầy đủ**. Vi phạm ⇒ `exit 1` |
| **LEGACY** | Chưa migrate | Chỉ **đếm** và liệt kê nợ. Không làm fail gate |

Nghĩa là: file nào **khai** dùng T3 thì file đó **phải đúng** T3. Gate siết dần theo
từng file được migrate, thay vì fail 51 file trong một đêm — một gate fail 51 file là
gate không ai chạy.

```bash
python3 dev/audit_t3.py                 # báo cáo đầy đủ
python3 dev/audit_t3.py --quiet         # chỉ vi phạm, exit 1 nếu có
python3 dev/audit_t3.py --legacy        # liệt kê nợ migration từng file
python3 dev/audit_t3.py --legacy-count  # in đúng một số: số file chưa migrate
python3 dev/audit_t3.py --file <path>   # soi một file, luôn soi đầy đủ
```

Luật script kiểm tra tự động: xem `.claude/rules/new-tool-standard.md` §2.
File UI-locked (§7) được bỏ qua hoàn toàn.
