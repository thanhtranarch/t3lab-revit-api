# Luật cho MỌI tool / script mới

> Áp dụng cho mọi pushbutton, dialog, XAML **tạo mới từ 2026-08-28**.
> Chuẩn UI duy nhất: `pyRevit UI Design System/T3LAB_UI_STANDARD.md` + `T3Lab.Styles.xaml`.
> File cũ chưa migrate không bị luật này ràng buộc — chúng đi theo hàng đợi ở
> `docs/ui-governance/PRIORITY_QUEUE.md`.

Gate: `python3 dev/audit_t3.py --quiet` phải xanh trước khi commit.

**File mẫu:** `T3Lab.extension/lib/GUI/Tools/UIStandardShowcase.xaml` là UI hoàn chỉnh
chuẩn mẫu duy nhất, tổng hợp cả 5 pattern vào một cửa sổ thực tế chuẩn mực. Mở nó,
tham khảo bố cục hoặc copy khối control của tool mình — nhanh hơn và an toàn hơn viết từ đầu.

---

## 0 · Trước khi viết dòng đầu tiên

Trả lời 3 câu. Không trả lời được thì chưa viết code.

| Câu hỏi | Lựa chọn |
|---------|----------|
| Tool này là **pattern** nào? | P1 form · P2 selection list · P3 progress & log · P4 results table · P5 confirmation |
| **Size class** nào? | S 420×260–320 (NoResize) · M 560×420–560 · L 1000×620 |
| Nó **sửa model** không? | Có → bắt buộc có P5 confirm + câu trạng thái nói rõ số lượng |

Không có pattern nào vừa → dừng lại, ghi `DESIGN SYSTEM GAP` vào
`docs/ui-governance/PRIORITY_QUEUE.md`, hỏi trước khi tự chế pattern thứ 6.

---

## 1 · Cấu trúc file — đặt đúng chỗ

```
T3Lab.extension/
├── T3Lab.tab/<Panel>.panel/<Tool>.pushbutton/
│   ├── script.py          ← entry point, KHÔNG chứa logic Revit nặng
│   ├── icon.svg           ← NGUỒN DUY NHẤT, viewBox "0 0 32 32"
│   ├── icon.dark.svg      ← sinh tự động, KHÔNG sửa tay
│   ├── icon.png           ← sinh tự động, 64×64
│   ├── icon.dark.png      ← sinh tự động, 64×64
│   └── bundle.yaml        ← title + tooltip
├── lib/GUI/Tools/<Tool>.xaml       ← MỌI file .xaml nằm ở đây, không ngoại lệ
├── lib/GUI/<Tool>Dialog.py         ← class WPF (nếu tool đủ lớn để tách)
└── lib/Snippets/                   ← helper Revit API dùng lại được
```

**Tách bạch bắt buộc:** XAML không biết gì về Revit API; `script.py` / `*Dialog.py`
không hardcode màu, size, margin. Logic Revit dùng lại được thì đẩy vào `lib/Snippets/`.

**Icon ribbon:** chỉ vẽ `icon.svg` theo
`docs/ui-governance/09-ribbon-icon-standard.md`, rồi chạy
`python3 dev/build_icons.py` để sinh ba file còn lại. Tuyệt đối không vẽ tay
`icon.dark.svg` hay `*.png` — lần build sau ghi đè. `Support.panel` được miễn trừ.

---

## 2 · XAML — 23 luật, `audit_t3.py` kiểm tra tự động

| # | Luật | Vi phạm |
|---|------|---------|
| 1 | Nhúng stylesheet bằng `dev/sync_t3_styles.py`; **không** `<Style x:Key>` nào tự viết trong file tool (implicit style không key thì được) | P1/P2 |
| 2 | Không hex cứng — mọi màu là `{StaticResource T3.*}` | P2 |
| 3 | Font chỉ `Segoe UI` (text) và `Consolas` (số, ID, log) | P2 |
| 4 | FontSize chỉ `19 · 15 · 13 · 11.5 · 11 · 12.5` | P2 |
| 5 | `Margin`/`Padding` chỉ `4 / 8 / 12 / 16 / 24 / 32` | P3 |
| 6 | `CornerRadius` chỉ `8` window · `4` control · `2` pill · `0` grid row | P3 |
| 7 | `<Window>` có `UseLayoutRounding` · `SnapsToDevicePixels` · `TextOptions.TextFormattingMode="Display"` · `MinWidth` · `MinHeight` | P2 |
| 8 | Có đúng **một** nút `T3.Button.Primary`, ngoài cùng phải footer | P2 |
| 9 | Có nút `IsDefault="True"` **và** nút `IsCancel="True"` | P2 |
| 10 | Mọi list/grid: `HorizontalScrollBarVisibility="Disabled"`; DataGrid thêm `EnableRowVirtualization="True"` | P1/P2 |
| 11 | **Không bọc** `DataGrid`/`ListBox`/`ListView` trong `ScrollViewer` | **P0** |
| 12 | Có empty state (`T3.Empty`) cho mọi list/grid | P2 |
| 13 | **Copyright BẮT BUỘC**: đúng MỘT `<TextBlock Style="{StaticResource T3.Copyright}"/>` ở footer, sát trái, trước câu trạng thái | P2 |
| 15 | Không `x:Key` hoặc implicit `TargetType` trùng nhau — WPF crash ngay lúc parse (`Item has already been added`) | **P0** |
| 16 | Panel cuộn (`T3.Panel` bọc `ScrollViewer`): Border đặt `Padding="0" ClipToBounds="True"`, ScrollViewer đặt `Padding="16,16,12,16"` để thanh cuộn 5px ghim sát mép phải panel và nội dung không bị chạm vào thanh cuộn | P3 |
| 17 | **Không Style cục bộ ngoài block T3 Styles**: Toàn bộ `<Window.Resources>` chỉ chứa block T3 Styles được sync. Không tự viết `<Style TargetType="...">` bên ngoài để tránh xung đột trùng key với style toàn cục | **P0** |
| 18 | **Không gán trùng thuộc tính Style**: Khi đã dùng `<Element.Style>` thì KHÔNG đặt thêm `Style="..."` inline trên element đó (WPF báo lỗi `'Style' property has already been set`) | **P0** |
| 19 | **Bố cục liền mạch — Không có gap giữa các cột, hàng, bảng**: Bảng phải lấp đầy toàn bộ chiều rộng, cột cuối cùng phải tự động dãn khít mép phải (zero gap). Bảng và dải đếm/trạng thái dưới chân bảng phải nằm trong cùng một `<Border Style="{StaticResource T3.Panel}" Padding="0">`, ngăn cách bằng đường kẻ 1px `T3.Border`, tuyệt đối không tách 2 Border rời nhau gây khe hở margin lơ lửng | P2 |
| 20 | **Resource Key phải tồn tại trong T3Lab.Styles.xaml**: Mọi `{StaticResource T3.*}` đều phải được định nghĩa trong stylesheet chuẩn. Không tự đặt key không có thực (như `T3.SurfaceHover`) gây lỗi `Cannot find resource named...` | **P0** |
| 21 | **FindResource trong Python**: Khi code Python gọi `self.FindResource(...)`, bắt buộc dùng tên chuẩn `T3.*` dot-notation (ví dụ `T3.CheckBox`), cấm dùng tên cũ legacy (`T3CheckBox`), và luôn bọc trong `try/except` an toàn. | P1 |
| 22 | **Icon một bộ duy nhất**: icon LUÔN là `<TextBlock Text="&#xE721;" Style="{StaticResource T3.Icon...}"/>`. Cấm khai `FontFamily="Segoe MDL2 Assets"` tại chỗ dùng, cấm để icon làm `Content` của Button, cấm ký tự Unicode thường (`✓ ✕ ⚠ ▶ ▢ −`) làm icon. Bảng glyph chuẩn ở mục "Icon" trong `T3LAB_UI_STANDARD.md` | P2 |
| 23 | **Cột checkbox phải có select-all ở header**: bảng nào cho tick từng dòng thì header cột đó bắt buộc có `<X.Header><CheckBox x:Name="chk_all_<grid>" Style="{StaticResource T3.CheckBox}" Click="select_all_<grid>_clicked" ToolTip="Select all rows"/></X.Header>`. Handler chỉ một dòng: `self.toggle_all_rows(self.<grid>, "<prop>", sender.IsChecked)` (`toggle_all_rows` nằm sẵn trong `T3WPFWindow`). Miễn trừ: cột là **thuộc tính của dòng** chứ không phải để chọn dòng (ví dụ `ManaWorkset` ACTIVE/OPEN/EDITABLE) — khai vào `SELECTALL_EXEMPT` trong `dev/audit_t3.py` | P2 |

Thêm hai thứ `audit_t3.py` cũng bắt: `<Grid.RowDefinition/>` dot-notation (**P0**,
crash `EMPTYPROPERTYELEMENT` lúc mở tool) và mọi `Effect` (P2).

### Khung `<Window>` chuẩn

```xml
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        xmlns:sys="clr-namespace:System;assembly=mscorlib"
        Title="Tool name" Height="420" Width="560" MinHeight="420" MinWidth="560"
        FontFamily="Segoe UI" FontSize="13"
        UseLayoutRounding="True" SnapsToDevicePixels="True"
        TextOptions.TextFormattingMode="Display"
        Background="{DynamicResource T3.Canvas}"
        WindowStartupLocation="CenterOwner">
  <Window.Resources>
    <ResourceDictionary>
      <!-- chạy `python3 dev/sync_t3_styles.py` để nhúng stylesheet vào đây -->
    </ResourceDictionary>
  </Window.Resources>
  <!-- nội dung + footer có copyright: xem .claude/skills/xaml-templates.md -->
</Window>
```

> **Nhúng, KHÔNG dùng `MergedDictionaries`.** pyRevit nạp XAML bằng `XamlReader` trên
> một stream nên WPF không có base URI và không có entry assembly; mọi
> `<ResourceDictionary Source="..."/>` tương đối đều ném
> `IOException: Assembly.GetEntryAssembly() returns null` và tool chết ngay lúc mở
> (đã thử với BatchOut 2026-08-28). Nạp dictionary từ Python sau đó cũng không cứu
> được, vì `{StaticResource}` được resolve NGAY LÚC PARSE.
> Chạy `python3 dev/sync_t3_styles.py` để nhúng; `--check` để verify. **Không sửa tay
> khối giữa 2 marker `T3 STYLES`.**

---

## 3 · `script.py` — khung bắt buộc

```python
#! python3
# -*- coding: utf-8 -*-
"""<Tên tool> — <một câu tool này làm gì>."""
__title__ = 'Tên\nTool'
__author__ = 'T3Lab'

# ── IMPORTS ──────────────────────────────────────────────────────────────
import os
import sys

# ── PATH SETUP ───────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(__file__)
# 3 levels for non-stacked (Panel/Tool.pushbutton), 4 for stacked (Panel/Stack/Tool.pushbutton)
EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
LIB_DIR = os.path.join(EXT_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from pyrevit import forms, script, revit
from Autodesk.Revit.DB import Transaction, FilteredElementCollector

# ── CONSTANTS ────────────────────────────────────────────────────────────
doc = revit.doc
uidoc = revit.uidoc
XAML_FILE = os.path.join(LIB_DIR, 'GUI', 'Tools', '<Tool>.xaml')

# ── WINDOW ───────────────────────────────────────────────────────────────
class ToolWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self._load()                    # nạp dữ liệu NGAY, không đợi Loaded event

    # ...

# ── ENTRY POINT ──────────────────────────────────────────────────────────
if __name__ == '__main__':
    ToolWindow().ShowDialog()
```

### Luật `script.py`

| # | Luật | Vì sao |
|---|------|--------|
| S1 | Một `Transaction` cho một thao tác người dùng | Ctrl+Z revert đúng **một** bước |
| S2 | `t.Start()` / `t.Commit()` trong `try/except` có `t.RollBack()` | Lỗi giữa chừng không để model dở dang |
| S3 | Không bắt `except:` trần — bắt đúng loại, và **hiện lỗi cho người dùng** | Exception bị nuốt = tool "không làm gì" mà không ai biết |
| S4 | Không `print()` trong luồng bình thường | Rác ra output window |
| S5 | Tác vụ >2 giây phải có progress (pattern P3) | Revit đóng băng im lặng là bug |
| S6 | Không chọn gì / model rỗng → thông báo thân thiện, không stacktrace | |
| S7 | Nạp dữ liệu grid trong `__init__`, không trong `Loaded` | Tránh trang trắng lúc mở |
| S8 | Mọi `x:Name` dùng trong Python phải tồn tại trong XAML | Sai = `AttributeError` lúc runtime |
| S9 | Không thêm dependency mới | CPython 3 (pyRevit) + .NET + stock WPF |
| S10 | Shebang `#! python3` bắt buộc ở dòng 1 | pyRevit định tuyến chạy CPython 3 (CPY3123) |
| S11 | Path setup `sys.path.insert(0, LIB_DIR)` bắt buộc | Đảm bảo import `GUI`, `Snippets`, `Services` không phụ thuộc vào vị trí chạy |
| S12 | .NET Interface bắt buộc có `__namespace__` | Trong PythonNet, thiếu `__namespace__` khiến đối tượng không implement interface (`object does not implement <Interface>`). |
| S13 | Cấm cú pháp Python 2 (`xrange`, `__builtin__`, `execfile`, `unicode`, `open(..., 'wb')` cho CSV) | Gây crash ngay lập tức trên Python 3 |
| S14 | **Debug lỗi Revit bắt buộc đọc Journal**: Đọc file `%LOCALAPPDATA%\Autodesk\Revit\Autodesk Revit <Year>\Journals\journal.XXXX.txt` | Trích xuất stack trace thực tế, tuyệt đối không đoán mò |
| S15 | **Khai báo `__namespace__` an toàn**: đưa class vào `lib/` HOẶC dùng namespace động `uuid` trong `script.py` | Engine CPython là interpreter **thường trú**; nếu class nằm trong `script.py` với tên tĩnh thì lần click thứ 2 ném `TypeError: Duplicate type name within an assembly`. Giải pháp: đưa class vào `lib/` (import 1 lần) hoặc dùng `__namespace__ = "T3Lab.<Name>_" + uuid.uuid4().hex[:8]`. Tuyệt đối không bỏ `__namespace__` vì sẽ gây `object does not implement <Interface>`. |
| S16 | **Không dùng `pyrevit.forms.*` trực tiếp** — dùng `GUI.T3Dialog`, hoặc thêm API vào `_cpython_bootstrap.install_forms_shim()` | `pyrevit/forms/__init__.py` có module `__getattr__` ném `PyRevitCPythonNotSupported` cho **mọi** thuộc tính dưới CPython |
| S17 | **Mọi sửa đổi trong `lib/` cần Reload pyRevit** mới có hiệu lực | `sys.modules` sống suốt phiên Revit. Triệu chứng đánh lừa: pyRevit in traceback theo **file hiện tại trên đĩa** nhưng chạy **code cũ**, nên số dòng không khớp lỗi. Thấy lỗi vô lý so với dòng được chỉ → nghi module cũ trước khi nghi code |
| S18 | **Cấm Multiple Inheritance với CLR Class** (`Non .NET type used as super class for meta type`) | Trong PythonNet, class kế thừa CLR class (`T3WPFWindow` / `System.Windows.Window`) không được phép kế thừa thêm pure Python class/mixin. Toàn bộ progress/pause methods đã có sẵn trong `T3WPFWindow`; chỉ khai báo `class MyWindow(T3WPFWindow):`. |

---

## 4 · Nội dung hiển thị

- Error message nói đủ ba phần: **cái gì sai · ở đâu · làm gì tiếp**. Không mã lỗi trần.
- Warning và success luôn kèm **số lượng**: "Đã đổi tên 34 sheet", không phải "Xong".
- Status **không bao giờ chỉ bằng màu** — chấm màu phải đi kèm chữ (`ok` / `skipped` / `failed`).
- **Ngôn ngữ UI là TIẾNG ANH**, không ngoại lệ — kể cả tool chỉ người Việt dùng.
  Comment trong code và tài liệu thì tiếng Việt vẫn được.
- Thuật ngữ Revit giữ nguyên tiếng Anh: View Template, Workset, Sheet, Family.

---

## 5 · Checklist trước khi commit / sau khi debug

```
[ ] Shebang `#! python3` ở dòng 1 của script.py
[ ] Path setup chèn `lib_dir` vào sys.path
[ ] python3 dev/audit_t3.py --quiet      → xanh (0 vi phạm)
[ ] python3 dev/audit_tools.py --quiet   → xanh (clean)
[ ] python3 dev/audit_cpython.py --quiet → 0 P0 (bẫy migration CPython)
[ ] python3 dev/build_icons.py --check   → không lệch (icon đã build)
[ ] python3 dev/audit_icons.py --quiet   → xanh (0 lỗi)
[ ] python3 dev/check_xaml_load.py --out %TEMP%\t3xaml  → 0 hỏng sau sanitise
[ ] powershell -STA -File dev/check_xaml_wpf.ps1 -Dir %TEMP%\t3xaml → 0 FAILED
[ ] Pattern P1–P5 rõ ràng, size class đúng S/M/L
[ ] Mở tool trong Revit: không lỗi, chrome hoạt động, không trang trắng
[ ] Happy path đúng; Ctrl+Z revert đúng một bước
[ ] Edge case (không chọn gì / model rỗng / bấm Cancel) ra câu tiếng người
[ ] Đọc được ở 100% VÀ 125% display scaling, không cắt chữ
[ ] Thao tác phá huỷ có P5 confirm, nút đỏ KHÔNG phải IsDefault
[ ] Footer có đúng một dòng © Copyright by T3Lab, sát trái
[ ] Không còn chữ tiếng Việt nào hiển thị cho người dùng
```

Bốn dòng cuối cần Revit thật. Chưa test thì ghi `NEEDS VERIFICATION`, **không tick**.

> **Hai script này bắt thứ `audit_t3.py` không bắt được.** Audit đọc XAML bằng
> ElementTree — nó chỉ biết file có well-formed **trên đĩa** hay không. pyRevit thì
> chạy `WPF_Base._sanitize_xaml` (một lượt regex viết lại text để gỡ event handler)
> **trước**, rồi mới đưa cho `XamlReader`. Nên một file hoàn toàn hợp lệ trên đĩa vẫn
> có thể hỏng đúng lúc WPF đọc nó.
>
> `check_xaml_load.py` chạy **đúng cái sanitiser đó**, lấy thẳng từ source đang ship
> nên không thể lệch. `check_xaml_wpf.ps1 -Dir` nạp kết quả bằng **chính `XamlReader`
> của WPF**, tóm được lớp lỗi crash-lúc-mở: thuộc tính không tồn tại trên control
> (`<ListBox HorizontalScrollBarVisibility>` — phải là
> `ScrollViewer.HorizontalScrollBarVisibility`), `x:Key` trùng, attached property sai
> cú pháp, resource không tồn tại. Chạy cả hai trước khi commit bất kỳ XAML nào.
>
> Xem một tool trông thế nào mà không cần mở Revit:
> `powershell -STA -File dev/preview_t3_xaml.ps1 -Xaml <đường dẫn> -Out out.png -Tab <n>`
