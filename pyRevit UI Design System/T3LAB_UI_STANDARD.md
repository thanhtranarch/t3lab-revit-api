# T3Lab pyRevit UI Standard — project instructions

Mọi dialog WPF/XAML sinh ra trong project này PHẢI tuân theo standard dưới đây.
Bản đặc tả đầy đủ (có mock + lý do từng quyết định): `T3Lab pyRevit UI Standard.dc.html`
File dùng thật: `dist/T3Lab.Styles.xaml` · `dist/T3LAB_UI_STANDARD.md`

## Ràng buộc kỹ thuật
- Chỉ stock WPF: Grid, StackPanel, DockPanel, UniformGrid, ListBox, ListView, ComboBox,
  CheckBox, RadioButton, TextBox, DataGrid, ProgressBar, Button, Expander, TabControl, Separator.
- Không thư viện thứ ba (MahApps, HandyControl, custom DLL).
- IronPython 2.7 / .NET 4.8 / `pyrevit.forms.WPFWindow`.
- Không effect: không gradient, không blur, không transition, không custom scrollbar.
- Phải đọc được ở 100% và 125% display scaling.

## Token — không tool nào được tự định nghĩa màu/size/margin
Màu: `T3.Ink #18181B` · `T3.Text #27272A` · `T3.TextSecondary #52525B` · `T3.TextMuted #71717A`
· `T3.TextDisabled #9A9AA2` · `T3.Border #DCDCE0` · `T3.BorderStrong #A1A1AA`
· `T3.SurfaceSunken #F4F4F6` · `T3.Canvas #E4E4E7` · `T3.Surface #FFFFFF`
Status (Fill / Accent / Text): Success `#EAF8F0 #22A85C #157038` · Warning `#FFF6E6 #F5CE5A #8A6308`
· Danger `#FDECEC #F87171 #D23B3B` · Progress track `#ECECEF`, fill `#C2410C`.
Cam `#C2410C` CHỈ dùng cho progress / trạng thái đang chạy. Không dùng làm button, border, brand.
Amber `T3.Copyright #F59E0B` CHỈ dùng cho dòng copyright ở footer — xem luật 11.

Type — Segoe UI, đúng 7 size: Display 19 SemiBold · Title 15 SemiBold · Body 13 Regular
· BodyStrong 13 SemiBold · Caption 11.5 · Label 11 SemiBold uppercase · Mono 12.5 Consolas.
Không dùng size ngoài danh sách. Không dùng font khác Segoe UI / Consolas.

Spacing: chỉ 4 / 8 / 12 / 16 / 24 / 32. Mọi margin chia hết cho 4.
Chiều cao: row 26 · control 28 · action button 30 · title bar 40 · footer 48.
Bo góc: window 12 · control 6 · pill 4 · grid row 0.
Kích thước cửa sổ: S 420×260–320 (NoResize) · M 560×420–560 · L 1000×620.

## Luật bố cục
1. Một left rail duy nhất: 16px từ mép window, 12px trong panel lồng.
2. Label nằm TRÊN control (4px), nhóm field cách nhau 12px. Không label cột bên trái.
3. Mỗi grid chỉ một cột `*` (là Name). Cột khác fix px: ID 90, Category 140, Status 150–170, số 70.
4. `HorizontalScrollBarVisibility="Disabled"` mọi grid/list. Không đủ chỗ thì bỏ bớt cột.
5. Số căn phải (Consolas), text căn trái, Element ID căn trái Consolas.
6. Footer cố định: trái = dot + câu trạng thái; phải = ghost huỷ → secondary → secondary → MỘT primary. Gap 8.
7. Panel lồng tối đa 2 cấp. Chia section bằng label uppercase + `Separator`, không bằng card.
8. `UseLayoutRounding="True"`, `SnapsToDevicePixels="True"`, `TextOptions.TextFormattingMode="Display"`
   trên Window. Luôn có MinWidth/MinHeight. Không set Height cho TextBlock. Không fix Width cho text dịch.
9. Mọi window có `IsDefault` và `IsCancel`. Focus = viền 1px `T3.Ink`, không dùng dotted rectangle.
10. Mọi list/grid có empty state: TextBlock canh giữa, `T3.TextDisabled`, nói thiếu gì và làm gì tiếp.
11. **Copyright BẮT BUỘC.** Mọi cửa sổ tool có ĐÚNG MỘT dòng `© Copyright by T3Lab`,
    dùng `{StaticResource T3.Copyright}`, đặt ở footer **sát trái**, đứng trước câu
    trạng thái và cách nó bằng một `Border` dọc 1×14 `T3.Border`, margin `12,0`.
    Ký tự `©` phải là ký tự thật — không dùng `&#169;`. Không right-align, không
    canh giữa, không thả nổi đè lên nội dung, không lặp lại.
    Miễn trừ duy nhất: XAML item-template (root là `<Border>`/`<DataTemplate>` của một
    dòng list, không phải cửa sổ) — copyright thuộc về cửa sổ chứa nó.
12. **Ngôn ngữ UI là TIẾNG ANH.** Mọi chữ người dùng đọc — label, nút, header, tooltip,
    empty state, thông báo lỗi/cảnh báo/thành công — viết bằng tiếng Anh, không ngoại lệ.
    Comment trong XAML/code và tài liệu nội bộ thì không bị ràng buộc.

## 5 pattern — mọi tool phải là một trong số này
- **P1 Parameter input form** (M) — form một cột, Expander cho Advanced, callout hệ quả có số lượng trên footer.
- **P2 Element selection list** (M) — filter pinned, ListBox virtualized, All/None ghost ở footer, primary mang số đếm.
- **P3 Progress & log** (M) — phase + `n / total` + item hiện tại, bar 8px cam, log ListBox Consolas
  màu theo severity + chữ (`ok` / `skipped` / `failed`), dải tally, footer "đang chạy, đừng đóng Revit".
  Xong thì Cancel → Close, dot chuyển xanh.
- **P4 Results table** (L) — dải summary 52px (số + label, chia bằng rule 1px), chip filter, DataGrid 26px row,
  status pill nền tint + dot + chữ, primary trả người dùng về Revit ("Select in Revit").
- **P5 Confirmation** (S) — headline là câu hỏi có số ("Delete 34 view templates?"), nút phá huỷ màu đỏ
  mang tên thao tác + số, và KHÔNG phải `IsDefault` (Cancel mới là). Bản success dùng lại vỏ này ở thì quá khứ.

## Chrome cửa sổ & wizard — 8 component (thêm 2026-08-28)

Tool nhiều bước có sidebar rail không khớp P1–P5. Thay vì tự chế pattern thứ 6,
dùng 8 component này; chúng mang sẵn giá trị hình dạng riêng nên file tool không
bao giờ phải tự viết số lạ:

`T3.WinCtrl` nút minimize/maximize · `T3.WinClose` nút đóng (hover đỏ) ·
`T3.TabItem.Hidden` tab ẩn cho wizard điều khiển bằng code-behind ·
`T3.Rail.Tile` ô rail 42×42 (ToggleButton) · `T3.Rail.Logo` ô logo 42×42 ·
`T3.Pill` dải ngang 36px · `T3.ComboBox.Toggle` nút mở của combo nhỏ ·
`T3.ScrollBar.Thumb` thumb thanh cuộn.

Implicit style (`<Style TargetType="X">` **không** có `x:Key`) được phép trong file
tool: đó là override chỉ có hiệu lực trong đúng container chứa nó, và stock WPF không
có cách nào khác. Style **có** `x:Key` thì không — nó là component mới, phải vào
`T3Lab.Styles.xaml`.

### Vị trí ScrollBar trong panel cuộn (`T3.Panel` chứa `ScrollViewer`)
Khi một card panel (`T3.Panel`) cần cuộn nội dung form:
- Thẻ bọc ngoài `<Border Style="{StaticResource T3.Panel}" Padding="0" ClipToBounds="True">`.
- Thẻ `<ScrollViewer>` bên trong nhận `Padding="16,16,12,16"` (trái 16, trên 16, phải 12, dưới 16).
- **Lý do:**
  1. Thanh cuộn siêu mỏng 5px được ghim sát viền mép phải của panel (chuẩn như Properties Palette trong Revit), không bị trôi lơ lửng giữa khoảng trắng.
  2. Nội dung form, separator và các khung callout có khoảng thở đệm 12px ngăn cách với thanh cuộn, không bao giờ bị đè hay chạm sát vào thumb thanh cuộn.

## Component data-dense (thêm 2026-08-28)

Rút từ 5 mock chuẩn. Trước đó mỗi tool tự dựng bằng tay — đúng thứ Design System
sinh ra để chống:

`T3.Chip` chip filter (RadioButton cùng GroupName, chip chọn = nền Ink) ·
`T3.Search` ô tìm có kính lúp + placeholder lấy từ `Tag` ·
`T3.ListHeader` + `T3.ListHeader.Label` header của list tự dựng ·
`T3.Meter` thanh tỉ lệ TĨNH (pass rate) — khác `T3.ProgressBar`, không cam vì không
có gì đang chạy · `T3.Cell.Muted` ô "— none —" / "n/a" ·
`T3.Callout.Icon` icon của callout · `T3.Dot` chấm trạng thái 6px ·
`T3.Log.Time/.Ok/.Skipped/.Failed/.Plain` dòng log · `T3.Tally` dải đếm dưới log.

## Bảng có checkbox — bắt buộc có select-all ở header

Bảng nào cho tick từng dòng thì **phải** cho tick tất cả. Không có ngoại lệ vì
"bảng này thường ít dòng" — filter đổi là số dòng đổi.

```xml
<DataGridTemplateColumn Width="36" CanUserResize="False" CanUserSort="False">
  <DataGridTemplateColumn.Header>
    <CheckBox x:Name="chk_all_sheets_grid" Style="{StaticResource T3.CheckBox}"
              Click="select_all_sheets_grid_clicked"
              HorizontalAlignment="Center" VerticalAlignment="Center"
              ToolTip="Select all rows"/>
  </DataGridTemplateColumn.Header>
  <DataGridTemplateColumn.CellTemplate>
    <DataTemplate>
      <CheckBox IsChecked="{Binding is_selected, Mode=TwoWay,
                            UpdateSourceTrigger=PropertyChanged}"
                Style="{StaticResource T3.CheckBox}"
                HorizontalAlignment="Center" VerticalAlignment="Center"/>
    </DataTemplate>
  </DataGridTemplateColumn.CellTemplate>
</DataGridTemplateColumn>
```

Code-behind đúng một dòng — `toggle_all_rows` nằm sẵn trong `T3WPFWindow`:

```python
def select_all_sheets_grid_clicked(self, sender, e):
    self.toggle_all_rows(self.sheets_grid, "is_selected", sender.IsChecked)
```

- `toggle_all_rows` chạy trên `grid.Items` chứ không phải `ItemsSource`, nên
  **chỉ đổi những dòng đang hiển thị** sau filter/sort — select-all không được
  chọn lén các dòng đã bị lọc đi.
- Tool nào đã có sẵn nút *Select All / Select None* thì gọi thêm
  `self.sync_header_checkbox(self.FindName("chk_all_<grid>"), self.<grid>, "<prop>")`
  ở cuối handler của nút, để checkbox header không lệch pha với nút.
  Chọn một phần → header về trạng thái indeterminate.

**Miễn trừ:** cột checkbox là **thuộc tính của chính dòng đó**, không phải để
chọn dòng — ví dụ `ManaWorkset` ACTIVE / OPEN / EDITABLE là trạng thái từng
workset trong Revit. "Tất cả" ở đó nghĩa là mở/khoá toàn bộ workset, một hành
động khác hẳn và phải làm có ý thức trên từng dòng. Khai vào `SELECTALL_EXEMPT`
trong `dev/audit_t3.py`.

## Sửa trực tiếp trên bảng — ô vàng, ghi khi ấn Apply

Bảng cho sửa ô thì theo đúng ba luật này, không tự chế kiểu khác:

1. **Không bao giờ ghi thẳng vào model lúc gõ.** Ô vừa sửa được *treo*, tô vàng,
   và chỉ đi vào Revit khi người dùng ấn nút Apply của tab đó.
2. **Tô từng Ô, không tô cả dòng.** Sửa 2 ô trên 1 dòng thì vàng đúng 2 ô.
3. **Điều hướng theo BINDING PATH, cấm theo `column.Header`.** Header là chữ hiển
   thị; đổi chữ header là logic chết câm. `GUI/GridPendingEdits.column_key()` đọc
   binding path — dùng nó.

```xml
<DataGridTextColumn Header="SHEET NAME" Binding="{Binding sheet_name}" Width="*">
  <DataGridTextColumn.CellStyle>
    <Style TargetType="DataGridCell" BasedOn="{StaticResource T3.DataGridCell}">
      <Style.Triggers>
        <DataTrigger Binding="{Binding dirty_sheet_name}" Value="True">
          <Setter Property="Background"      Value="{StaticResource T3.Warning.Fill}"/>
          <Setter Property="BorderBrush"     Value="{StaticResource T3.Warning.Accent}"/>
          <Setter Property="BorderThickness" Value="3,0,0,0"/>
        </DataTrigger>
      </Style.Triggers>
    </Style>
  </DataGridTextColumn.CellStyle>
</DataGridTextColumn>
```

Python — `GUI/GridPendingEdits.py` lo phần treo, `init_pending` phải chạy trên
mọi dòng lúc nạp, nếu không cờ `dirty_*` không tồn tại và DataTrigger **im lặng
không bao giờ nổ**:

```python
from GUI import GridPendingEdits as _pend
SHEET_EDIT_FIELDS = ("sheet_number", "sheet_name")

for row in rows:
    _pend.init_pending(row, SHEET_EDIT_FIELDS)      # lúc nạp dữ liệu

def _on_cell_edit(self, sender, args):              # grid.CellEditEnding
    field = _pend.column_key(args.Column)           # KHÔNG dùng args.Column.Header
    typed = _pend.editor_text(args.EditingElement)
    if _pend.same_text(typed, getattr(item, field, None)):
        _pend.unstage(item, field)                  # gõ về như cũ → hết vàng
    else:
        _pend.stage(item, field, typed)
```

### Hai cái bẫy phải biết

- **Handler viết trong `<DataTemplate>` KHÔNG BAO GIỜ chạy.** Template có
  namescope riêng, `FindName` lúc load không với tới, binding bị bỏ im lặng. Ô
  sửa được phải là `DataGridTextColumn` (bắt `CellEditEnding` của chính grid),
  hoặc nối bằng routed event từ Python:
  `grid.AddHandler(CheckBox.ClickEvent, RoutedEventHandler(handler), True)`.
- **Dòng không có `INotifyPropertyChanged` thì phải refresh** mới thấy vàng, và
  refresh ngay trong `CellEditEnding` sẽ ném *"not allowed during an EditItem
  transaction"*. Đẩy qua dispatcher:
  `self.Dispatcher.BeginInvoke(DispatcherPriority.Background, Action(lambda: grid.Items.Refresh()))`.

## Icon — một bộ duy nhất cho toàn extension

Font icon **duy nhất** là `Segoe MDL2 Assets`. Icon **luôn** là một `<TextBlock>` mang
style `T3.Icon.*`, **không bao giờ** là `Content` của Button và **không bao giờ**
khai `FontFamily`/`FontSize` tại chỗ dùng.

| Style | Dùng cho |
|-------|----------|
| `T3.Icon` | icon nền — cỡ Label (11), màu thừa kế từ control chứa nó. Chrome cửa sổ, icon trong nút |
| `T3.Icon.Muted` | icon phụ trợ, không phải nội dung chính (`T3.TextDisabled`) |
| `T3.Icon.Field` | icon dẫn của ô nhập/search — mờ + cách chữ 8px |
| `T3.Icon.Lead` | icon đứng trước nhãn/chữ trong nút — cách chữ 8px, màu thừa kế |
| `T3.Icon.Lg` | icon lớn cho empty state / header — cỡ Display (19) |
| `T3.Callout.Icon` | icon của callout — cỡ Caption, canh đỉnh dòng chữ đầu |

**Cấm ký tự Unicode thường làm icon** (`✓ ✕ ⚠ ▶ ▢ − ◀ ▲ ▼`). Chúng render bằng
Segoe UI nên lệch nét, lệch baseline và lệch chiều cao so với glyph MDL2 đứng cạnh.

Bảng glyph chuẩn — **một khái niệm, một glyph, toàn dự án**:

| | | | |
|---|---|---|---|
| `E721` Search | `E8BB` Close | `E921` Minimize | `E922` Maximize |
| `E768` Play | `E769` Pause | `E71A` Stop | `E72C` Refresh |
| `E73E` Check | `E711` Cancel | `E710` Add | `E74D` Delete |
| `E946` Info | `E7BA` Warning | `E783` Error | `E713` Settings |
| `E70D` ChevronDown | `E70E` ChevronUp | `E76B` ChevronLeft | `E76C` ChevronRight |
| `E74E` Save | `E8E5` OpenFile | `E774` Globe | `E7A7` Undo |
| `E8A3` Zoom | `E7B3` Isolate | `E7C9` Pick | `E7C3` Document |
| `E896` Download | `EA80` Insight / AI | | |

Cần glyph chưa có trong bảng → thêm vào bảng này **và** vào comment đầu khối ICON
trong `T3Lab.Styles.xaml`, đừng dùng lẻ.

**Hai cái bẫy khi chọn glyph mới** (học được lúc thêm Zoom/Isolate/Pick, 2026-09-12):

1. **Đừng chọn glyph TRÔNG GIỐNG glyph đã có.** `E71E` cũng là kính lúp và vẽ ra
   gần như y hệt `E721` Search — đặt Zoom bằng `E71E` là hai khái niệm khác nhau
   cùng một hình. Zoom dùng `E8A3` (kính lúp có dấu +) để phân biệt được.
2. **Kiểm tra codepoint CÓ THẬT trong font.** Glyph không tồn tại render ra ô vuông
   tofu, và `audit_t3.py` không bắt được — nó chỉ grep chuỗi trong source. `E7AE` và
   `E92B` chẳng hạn là KHÔNG có trong Segoe MDL2 Assets. Cách kiểm nhanh, không cần
   mở Revit:

   ```powershell
   $tf = New-Object Windows.Media.Typeface('Segoe MDL2 Assets'); $gt = $null
   $tf.TryGetGlyphTypeface([ref]$gt) | Out-Null
   $gt.CharacterToGlyphMap.ContainsKey([Convert]::ToInt32('E8A3', 16))   # True = có thật
   ```

> **Nợ hiện có:** 14 glyph lẻ ngoài bảng vẫn còn trong `T3LabAssistant.xaml` (`E81C`
> `E723` `E8BD` `E74C` `ED25` `E8B7`), `ParameterSelector.xaml` (`E74A` `E74B`),
> `AutoJoin.xaml` (`E8AB`), `BCFReader.xaml` (`EA3A`), `ManaGroup.xaml` (`E9A6`),
> `PointCloud.xaml` (`E753`), `PropertyLine.xaml` (`E707`). Chưa khai vào bảng vì
> chưa rõ khái niệm chủ ý của từng cái — ai sửa tool đó thì khai luôn.

Gate: `python3 dev/audit_t3.py` bắt cả hai vi phạm (FontFamily inline · ký tự Unicode).
Miễn trừ: `DWGManagement.xaml` (thiết kế riêng đã chốt) và `T3LabAssistant.xaml`
(chat surface theo theme Revit — brush tĩnh của `T3.Icon` sẽ hỏng dark mode).

## AI Mode — khi nào có, trông ra sao

AI Mode chỉ được có mặt ở tool khi nó làm được việc **luật không làm tốt** và kết quả
của nó **đổ thẳng vào thao tác** của tool. Đánh giá lại 2026-09-26 theo đúng hai câu:

| Giữ AI | Việc AI làm | Vì sao cần |
|---|---|---|
| CADToElements | Chọn layer CAD cho từng loại element | Tên layer mỗi văn phòng mỗi kiểu — so khớp ngữ nghĩa |
| IFCSG | Đoán IFC-SG subtype từ tên type | Phân loại theo nghĩa, danh sách subtype dài |
| ManaPara | Ghép parameter nguồn → đích khi chuyển dữ liệu | Tên khác nhau, cùng nghĩa (`Mark` ↔ `Tag No.`) |
| FamiGen | Sinh JSON family từ mô tả | Việc sinh nội dung — không có luật thay thế |
| TextToElement | Đoán category + parameter từ nội dung text note | Đọc hiểu nội dung chữ |
| ManaAnno › Text | Soát chính tả / viết tắt text note (chỉ stage, Apply mới ghi) | Việc ngôn ngữ |

Đã bỏ (AI chỉ đoán lại con số luật tính đúng, chỉ in một đoạn văn không kèm hành
động, hoặc tạo kết quả phải tất định): AutoDimension (offset → nút **Offsets from
Scale**), ManaStyles (→ nút **Select CAD Styles**), SheetGen, ManaSched, ModelAuditor,
BCFReader, BatchOut (tên file xuất phải tất định — dùng naming pattern), DimText Suggest.

**Muốn thêm AI vào tool mới** → trả lời hai câu trên trong PR. Không trả lời được thì
không thêm.

### Hình thức — giống nhau ở mọi tool

- **Badge** trên title bar, ngay sau khối tiêu đề/phụ đề: `Border x:Name="ai_mode_badge"`
  `Style="{StaticResource T3.Pill}"` · icon `&#xEA80;` `T3.Icon.Lead` · `TextBlock
  x:Name="txt_ai_status"` `T3.Caption`. Code chỉ gọi `self.init_ai_badge()` — chữ
  **AI ready / AI off** (trạng thái bằng chữ, không chỉ bằng màu), tooltip nêu provider.
- **Nút AI**: `T3.Button.Secondary`, nội dung là icon `&#xEA80;` (`T3.Icon.Lead`) +
  nhãn **"AI &lt;Động từ&gt;"** (AI Select, AI Match, AI Predict…). Đặt sát ô dữ liệu mà nó
  điền, không gom vào toolbar chung.
- **Code**: khai `AI_TOOL = "<Tên>"` trên class; đầu handler `if not self.ai_require():
  return` (một câu báo thống nhất khi AI tắt); lúc chờ model `self.ai_busy(btn, True/False)`
  — **không** đổi `Content` của nút (nó là icon + nhãn). Không có "fallback bằng luật"
  ngầm làm việc khác đi: AI tắt thì báo, không tự sửa gì.
- Cấm `✨` `⏳` và mọi ký tự Unicode làm icon — `audit_t3.py` bắt cả dạng dán thẳng.
- Kết quả AI hiện lên UI phải là **tiếng Anh** như mọi chữ khác của tool.

## File mẫu — copy từ đây

`T3Lab.extension/lib/GUI/Tools/UIStandardShowcase.xaml` là **UI hoàn chỉnh chuẩn mẫu duy nhất**, tổng hợp toàn bộ 5 pattern (P1 form cấu hình thông số, P2 chọn phần tử & filter bar, P3 thanh tiến trình & log box, P4 bảng kết quả DataGrid & summary metrics, P5 callout cảnh báo & an toàn) vào một cửa sổ làm việc hoàn chỉnh duy nhất. Dùng 98/106 resource key, 0 vi phạm audit, 0 waiver. Viết tool mới thì mở nó ra để copy cấu trúc layout, control, hoặc toàn bộ khung.

## Checklist review (dán vào PR)
Nhúng `T3Lab.Styles.xaml` bằng `dev/sync_t3_styles.py`, không tự định nghĩa brush · đúng một trong P1–P5 · size S/M/L
· có đúng một dòng copyright ở footer trái · mọi chữ hiển thị là tiếng Anh
· Segoe UI 13, không size lạ · margin chia hết 4 · đúng một primary, ngoài cùng phải
· có `IsDefault` + `IsCancel` · grid một cột `*`, tắt scroll ngang · có empty state
· chụp màn hình 100% và 125% không cắt chữ · thao tác phá huỷ có P5 · status không bao giờ chỉ bằng màu.

## Khi được nhờ viết tool mới
Hỏi tool thuộc pattern nào, rồi sinh XAML dùng `{StaticResource T3.*}` — không hardcode hex/size/margin.
Không tạo style mới trong file tool; style mới phải thêm vào `T3Lab.Styles.xaml`.
