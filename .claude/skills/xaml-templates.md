# XAML snippets — chuẩn T3

> **Cần tham khảo một UI hoàn chỉnh thay vì snippet lẻ?** Mở
> `T3Lab.extension/lib/GUI/Tools/UIStandardShowcase.xaml` — nó là UI chuẩn mẫu
> tổng hợp cả 5 pattern vào một cửa sổ thực tế chuẩn mực, và nằm trong gate kiểm tra nên không bao giờ lệch.

Mọi snippet dưới đây dùng key có thật trong `pyRevit UI Design System/T3Lab.Styles.xaml`.
Luật đầy đủ: `pyRevit UI Design System/T3LAB_UI_STANDARD.md` ·
luật cho tool mới: `.claude/rules/new-tool-standard.md`.

Copy nguyên si. **Không sửa giá trị, không thêm hex, không định nghĩa `<Style>` trong
file tool.** Cần style chưa có → ghi `DESIGN SYSTEM GAP`, hỏi trước.

**Mọi chữ hiển thị cho người dùng phải là TIẾNG ANH** — label, nút, tooltip, empty
state, thông báo lỗi. Ghi chú trong tài liệu này bằng tiếng Việt, nhưng chuỗi trong
XAML thì không. `audit_t3.py` bắt lỗi này bằng cách dò dấu tiếng Việt.

---

## Khung `<Window>`

```xml
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        xmlns:sys="clr-namespace:System;assembly=mscorlib"
        Title="Tool name" Width="560" Height="420" MinWidth="560" MinHeight="420"
        FontFamily="Segoe UI" FontSize="13"
        UseLayoutRounding="True" SnapsToDevicePixels="True"
        TextOptions.TextFormattingMode="Display"
        Background="{DynamicResource T3.Canvas}"
        WindowStartupLocation="CenterOwner">
  <Window.Resources>
    <ResourceDictionary>
      <!-- `python3 dev/sync_t3_styles.py` nhúng stylesheet vào đây -->
    </ResourceDictionary>
  </Window.Resources>

  <DockPanel>
    <Border DockPanel.Dock="Top"    Style="{StaticResource T3.TitleBar}">…</Border>
    <Border DockPanel.Dock="Bottom" Style="{StaticResource T3.FooterBar}">…</Border>
    <Grid Margin="16">…</Grid>
  </DockPanel>
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

> Trên **chính thẻ `<Window>`** phải dùng `DynamicResource`, không phải `StaticResource`:
> attribute của Window được parse TRƯỚC `Window.Resources`, nên `StaticResource` ở đó
> không tìm thấy key. Bên trong cây con thì `StaticResource` bình thường.

## Title bar (48px)

### Vỏ cửa sổ custom chrome có bo góc

Khi `<Window>` dùng `WindowStyle="None"`, nền của chính Window phải trong suốt;
nếu không, bốn góc vuông của HWND sẽ lộ ra sau outer Border đã bo góc.

```xml
<Window ...
        WindowStyle="None" AllowsTransparency="True"
        Background="Transparent">
  ...
  <Border Background="{StaticResource T3.Surface}"
          BorderBrush="{StaticResource T3.BorderStrong}"
          BorderThickness="1"
          CornerRadius="{StaticResource T3.R.Window}"
          ClipToBounds="True">
    ...
  </Border>
</Window>
```

`Background="Transparent"` là ngoại lệ kỹ thuật duy nhất ở root Window cho custom
chrome; màu bề mặt thật vẫn do outer Border lấy từ token `T3.*`.
`T3WPFWindow` tự cắt cây con theo đúng `CornerRadius` khi resize vì
`ClipToBounds="True"` của WPF chỉ cắt hình chữ nhật; không tự gọi
`XamlReader.Load()` để mở cửa sổ này.

```xml
<Border Style="{StaticResource T3.TitleBar}" Padding="12,0,8,0">
  <Grid>
    <Grid.ColumnDefinitions>
      <ColumnDefinition Width="*"/>
      <ColumnDefinition Width="Auto"/>
    </Grid.ColumnDefinitions>
    <StackPanel Grid.Column="0" Orientation="Horizontal" VerticalAlignment="Center">
      <Image x:Name="logo_image" Height="28" Margin="0,0,12,0"
             VerticalAlignment="Center"
             RenderOptions.BitmapScalingMode="HighQuality"/>
      <StackPanel Orientation="Vertical" VerticalAlignment="Center">
        <TextBlock Text="Rename Sheets" Style="{StaticResource T3.Title}"/>
        <TextBlock Text="Batch-rename project sheets according to standards" Style="{StaticResource T3.Subtitle}"/>
      </StackPanel>
    </StackPanel>
  </Grid>
</Border>
```

## Footer (48px) — trạng thái trái · nút phải

Thứ tự cố định: ghost huỷ → secondary → secondary → **MỘT** primary. Gap 8.

```xml
<Border Style="{StaticResource T3.FooterBar}">
  <Grid>
    <Grid.ColumnDefinitions>
      <ColumnDefinition Width="*"/>
      <ColumnDefinition Width="Auto"/>
    </Grid.ColumnDefinitions>

    <StackPanel Grid.Column="0" Orientation="Horizontal" VerticalAlignment="Center">
      <!-- Copyright BẮT BUỘC — luôn là phần tử ĐẦU TIÊN, sát trái. Style tự cấp
           chữ và màu; không tự viết Text hay Foreground. -->
      <TextBlock Style="{StaticResource T3.Copyright}"/>
      <Border Width="1" Height="16" Margin="12,0"
              Background="{StaticResource T3.Border}" VerticalAlignment="Center"/>
      <Ellipse Width="6" Height="6" Margin="0,0,8,0" VerticalAlignment="Center"
               Fill="{StaticResource T3.Success.Accent}"/>
      <TextBlock x:Name="status_text" Text="Ready — 34 sheets selected"
                 Style="{StaticResource T3.Body.Secondary}" VerticalAlignment="Center"/>
    </StackPanel>

    <StackPanel Grid.Column="1" Orientation="Horizontal">
      <Button Content="Cancel" IsCancel="True"
              Style="{StaticResource T3.Button.Ghost}" Margin="0,0,8,0"/>
      <Button Content="Preview" Style="{StaticResource T3.Button.Secondary}" Margin="0,0,8,0"/>
      <Button x:Name="btn_apply" Content="Rename 34 sheets" IsDefault="True"
              Style="{StaticResource T3.Button.Primary}"/>
    </StackPanel>
  </Grid>
</Border>
```

> Primary luôn **mang số đếm** khi thao tác có số lượng. "Rename 34 sheets", không phải "OK".

## Field — label TRÊN control, cách 4px; nhóm field cách nhau 12px

```xml
<StackPanel Margin="0,0,0,12">
  <TextBlock Text="Prefix" Style="{StaticResource T3.Label}" Margin="0,0,0,4"/>
  <TextBox x:Name="tb_prefix" Style="{StaticResource T3.TextBox}"/>
</StackPanel>
```

Ô nhập số / ID dùng `T3.TextBox.Mono`.

## ComboBox · CheckBox · RadioButton

```xml
<ComboBox x:Name="cb_level" Style="{StaticResource T3.ComboBox}"
          ItemContainerStyle="{StaticResource T3.ComboBoxItem}"/>

<CheckBox x:Name="chk_include" Content="Include hidden sheets"
          Style="{StaticResource T3.CheckBox}"/>

<RadioButton x:Name="rb_all" Content="Whole model" GroupName="scope"
             Style="{StaticResource T3.RadioButton}"/>
```

## Section — label uppercase + `Separator`, KHÔNG dùng card

```xml
<TextBlock Text="SCOPE" Style="{StaticResource T3.Label}" Margin="0,0,0,4"/>
<Separator Style="{StaticResource T3.Rule}" Margin="0,0,0,12"/>
```

## Callout — hệ quả, luôn kèm số lượng

```xml
<Border Style="{StaticResource T3.Callout.Warning}">
  <TextBlock Style="{StaticResource T3.Body}" TextWrapping="Wrap"
             Text="12 sheets are checked out by someone else and will be skipped."/>
</Border>
```

Bốn biến thể: `T3.Callout` (trung tính) · `.Success` · `.Warning` · `.Danger`.

## ListBox (P2 — selection list)

`T3.ListBox` đã bật virtualization và tắt scroll ngang bằng Setter — **không khai lại**.

```xml
<Grid>
  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>
    <RowDefinition Height="*"/>
  </Grid.RowDefinitions>

  <TextBox x:Name="tb_filter" Grid.Row="0" Margin="0,0,0,8"
           Style="{StaticResource T3.TextBox}"/>

  <Grid Grid.Row="1">
    <ListBox x:Name="lst_items" Style="{StaticResource T3.ListBox}"
             SelectionMode="Extended"/>
    <TextBlock x:Name="lst_empty" Style="{StaticResource T3.Empty}"
               Visibility="Collapsed"
               Text="No sheets match the filter.&#x0a;Clear the search box to see all of them."/>
  </Grid>
</Grid>
```

> **Empty state là bắt buộc.** Nói *thiếu gì* và *làm gì tiếp* — không phải "No data".
> **Không bao giờ** bọc `ListBox` trong `ScrollViewer`: mất virtualization, treo Revit.

## DataGrid (P4 — results table)

`T3.DataGrid` đã set row 26, virtualization, tắt scroll ngang, header/row/cell style.

```xml
<DataGrid x:Name="grid_results" Style="{StaticResource T3.DataGrid}">
  <DataGrid.Columns>
    <DataGridTextColumn Header="ID" Width="90" Binding="{Binding Id}"
                        ElementStyle="{StaticResource T3.Cell.Mono}"/>
    <DataGridTextColumn Header="Name" Width="*" Binding="{Binding Name}"/>
    <DataGridTextColumn Header="Category" Width="140" Binding="{Binding Category}"/>
    <DataGridTextColumn Header="Count" Width="70" Binding="{Binding Count}"
                        ElementStyle="{StaticResource T3.Cell.Number}"/>
    <DataGridTemplateColumn Header="Status" Width="150"
                            CellTemplate="{StaticResource T3.StatusPill}"/>
  </DataGrid.Columns>
</DataGrid>
```

Luật cột: **đúng một** cột `Width="*"` (là Name). Còn lại fix px —
`ID 90` · `Category 140` · `Status 150–170` · số `70`. Không đủ chỗ thì **bỏ bớt cột**,
không bật scroll ngang.

`T3.StatusPill` cần row VM có 2 property: `StatusText` (string) và
`Severity` (`"Success"` / `"Warning"` / `"Danger"`).

## Progress & log (P3)

```xml
<StackPanel>
  <TextBlock x:Name="lbl_phase" Style="{StaticResource T3.BodyStrong}"
             Text="Exporting — 12 / 34"/>
  <TextBlock x:Name="lbl_item" Style="{StaticResource T3.Caption}" Margin="0,4,0,8"
             Text="A-101 — Ground Floor Plan"/>

  <ProgressBar x:Name="bar" Style="{StaticResource T3.ProgressBar}"
               Minimum="0" Maximum="34" Value="12" Margin="0,0,0,12"/>

  <ListBox x:Name="log" Style="{StaticResource T3.LogBox}" Height="180"/>
</StackPanel>
```

Log ghi màu theo severity **và kèm chữ**: `ok` / `skipped` / `failed`. Màu một mình
không bao giờ là trạng thái.

## Expander — chỉ cho Advanced

```xml
<Expander Header="Advanced options" Style="{StaticResource T3.Expander}">
  <StackPanel Margin="0,8,0,0">…</StackPanel>
</Expander>
```

## Confirmation (P5) — size S

```xml
<StackPanel Margin="16">
  <TextBlock Style="{StaticResource T3.Display}" TextWrapping="Wrap"
             Text="Delete 34 view templates?"/>
  <TextBlock Style="{StaticResource T3.Body.Secondary}" Margin="0,8,0,0" TextWrapping="Wrap"
             Text="This cannot be undone with Ctrl+Z once the file is closed."/>
</StackPanel>

<!-- footer -->
<Button Content="Cancel" IsCancel="True" IsDefault="True"
        Style="{StaticResource T3.Button.Secondary}" Margin="0,0,8,0"/>
<Button x:Name="btn_delete" Content="Delete 34 templates"
        Style="{StaticResource T3.Button.Danger}"/>
```

> Ở P5, **Cancel** là `IsDefault`, không phải nút phá huỷ. Nút phá huỷ mang **tên thao
> tác + số**, không phải "OK" hay "Yes".

---

## Bảng tra style key

| Cần gì | Key |
|--------|-----|
| Chữ | `T3.Display` `T3.Title` `T3.Body` `T3.Body.Secondary` `T3.BodyStrong` `T3.Caption` `T3.Label` `T3.Mono` |
| Copyright (bắt buộc) | `T3.Copyright` |
| Empty state | `T3.Empty` |
| Cell trong grid | `T3.Cell.Mono` `T3.Cell.Number` |
| Nút | `T3.Button.Primary` `.Secondary` `.Ghost` `.Danger` |
| Nhập liệu | `T3.TextBox` `T3.TextBox.Mono` `T3.ComboBox` `T3.ComboBoxItem` `T3.CheckBox` `T3.RadioButton` |
| List / grid | `T3.ListBox` `T3.ListBoxItem` `T3.LogBox` `T3.DataGrid` `T3.DataGridColumnHeader` `T3.DataGridRow` `T3.DataGridCell` |
| Trạng thái | `T3.StatusPill` `T3.ProgressBar` |
| Khung | `T3.TitleBar` `T3.FooterBar` `T3.Panel` `T3.Rule` `T3.Expander` |
| Callout | `T3.Callout` `.Success` `.Warning` `.Danger` |

Màu/size/spacing chỉ dùng khi thật sự cần đặt trực tiếp: `T3.Ink` `T3.Text`
`T3.TextSecondary` `T3.TextMuted` `T3.TextDisabled` `T3.Border` `T3.BorderStrong`
`T3.Surface` `T3.SurfaceSunken` `T3.Canvas` `T3.RowAlt` `T3.RowRule` ·
`T3.Success.*` `T3.Warning.*` `T3.Danger.*` `T3.Progress.*` ·
`T3.Size.*` `T3.H.*` `T3.Pad.*` `T3.R.*`.

## Kiểm tra

```bash
python3 dev/audit_t3.py --file T3Lab.extension/lib/GUI/Tools/<Tool>.xaml
```
