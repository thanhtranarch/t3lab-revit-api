# UI Design Standard

All new pyrevit tool windows **MUST** follow the **T3Lab Kinetix** design system.
The canonical reference is **`BulkFamilyExport.xaml`**.

## Design Reference Files
- **Canonical UI (reference)**: `T3Lab.extension/lib/GUI/Tools/BulkFamilyExport.xaml`
- **All XAML files**: `T3Lab.extension/lib/GUI/Tools/`
- **XAML templates doc**: `.claude/docs/wpf-window-templates.md`
- **Python WPF pattern**: `.claude/docs/python-wpf-pattern.md`

## Color Palette

| Token          | Hex       | Usage                                      |
|----------------|-----------|--------------------------------------------|
| Primary        | `#083D56` | Primary buttons, T3Lab brand, active states |
| Primary Hover  | `#062A3C` | Primary button hover                        |
| Secondary      | `#546E7A` | Borders, separators, secondary elements     |
| Neutral        | `#F8F9FA` | Backgrounds, cards, inputs, status bar      |
| Neutral Hover  | `#E2E6EA` | Secondary button hover, row selected        |
| Dark text      | `#2C3E50` | Headings, labels, main text                 |
| Gray text      | `#7F8C8D` | Subtitles, placeholders, disabled text      |
| Success green  | `#27AE60` | Success/confirm buttons (hover: `#1E8449`)  |
| Danger red     | `#D32F2F` | Delete/destructive buttons (hover: `#B71C1C`) |
| Disabled bg    | `#546E7A` | Disabled button background                  |
| Copyright blue | `#3498DB` | Copyright text only                         |

## Typography

- **All text**: `Inter` (body, labels, headings — all use Inter)
- **Root Window**: `FontFamily="Inter"` on `<Window>` element

## Window Structure (every tool)

1. `<Window>` with `Background="White"`, `ResizeMode="CanResizeWithGrip"`, `FontFamily="Inter"`
2. `WindowChrome`: `CaptionHeight="64"`, `ResizeBorderThickness="5"`, `GlassFrameThickness="0"`, `CornerRadius="8"`, `UseAeroCaptionButtons="False"`
3. **Title bar** (Row 0, Height=64): White bg
   - Left: `T3Lab` (11px Bold `#083D56`) + Tool Name (18px Bold `#2C3E50`)
   - Below: Separator (1px `#546E7A`) + subtitle (10px Italic `#7F8C8D`)
   - Right: Min/Max/Close buttons (Segoe MDL2 Assets icons, `Foreground="#083D56"`)
   - Bottom: Border 1px `#546E7A`
4. **Content area** — tool-specific
5. **Status bar** (last row): `Background="#F8F9FA"`, `BorderBrush="#546E7A"`, `BorderThickness="0,1,0,0"`, `Padding="14,8"`
6. **Copyright overlay** — always before closing root `<Grid>`

## Button Style Keys (Window.Resources)

| Style Key         | Background | Foreground | Hover Bg   | Border     | Padding  | FontSize | CornerRadius |
|-------------------|-----------|------------|------------|------------|----------|----------|--------------|
| `PrimaryButton`   | `#083D56` | White      | `#062A3C`  | none       | `12,6`   | 12       | 3            |
| `SecondaryButton`  | `#F8F9FA` | `#2C3E50`  | `#E2E6EA`  | `#546E7A`  | `12,6`   | 12       | 3            |
| `SuccessButton`   | `#27AE60` | White      | `#1E8449`  | none       | `12,6`   | 12       | 3            |
| `DangerButton`    | `#D32F2F` | White      | `#B71C1C`  | none       | `12,6`   | 12       | 3            |
| `WinCtrlButton`   | Transparent | —        | `#F8F9FA`  | none       | —        | —        | —            |
| `CloseButton`     | Transparent | —        | `#D32F2F`  | none       | —        | —        | —            |

## Window Control Icons (Segoe MDL2 Assets)

| Button   | Glyph      | FontSize |
|----------|-----------|----------|
| Minimize | `&#xE921;` | 10       |
| Maximize | `&#xE922;` | 10       |
| Close    | `&#xE8BB;` | 10       |

## DataGrid Style

- `Background="White"`, `BorderBrush="#546E7A"`, `BorderThickness="1"`
- `AlternatingRowBackground="#F8F9FA"`, `FontFamily="Inter"`, `FontSize="12"`
- Headers: `Background="#F8F9FA"`, `Foreground="#2C3E50"`, `FontWeight="SemiBold"`, `Height="34"`, `BorderBrush="#546E7A"`
- Row hover: `#F8F9FA`
- Row selected: `#E2E6EA`

## Info / Tip Box

- `BorderBrush="#083D56"`, `Background="#F8F9FA"`, `CornerRadius="2"`, `Padding="10"`
- Label: `Tip:` — `FontWeight="Bold"`, `Foreground="#062A3C"`
- Body: `Foreground="#2C3E50"`

## Progress Bar (long-running tasks)

- `Height="8"`, `Foreground="#083D56"`, `Background="#E2E6EA"`, `BorderThickness="0"`
- Inline Pause (secondary style) + Stop (`#D32F2F`) buttons
- Panel `Visibility="Collapsed"` when idle

## Copyright Notice

Every XAML file must include this TextBlock before the closing root `</Grid>`:
```xml
<!-- Copyright added automatically -->
<TextBlock Text="© Copyright by T3Lab" HorizontalAlignment="Right" VerticalAlignment="Bottom" Margin="0,0,14,8" Foreground="#3498DB" FontSize="11" IsHitTestVisible="False" Panel.ZIndex="999"/>
```

## Checklist for New Tools

When creating a new pushbutton with a WPF UI:

- [ ] `Window.Background="White"`, `ResizeMode="CanResizeWithGrip"`, `FontFamily="Inter"`
- [ ] `WindowChrome` with `CaptionHeight="64"`, `CornerRadius="8"`, `UseAeroCaptionButtons="False"`
- [ ] Title bar: 64px, white, T3Lab brand (#083D56) + tool name + subtitle
- [ ] Separator (1px #546E7A) between title and subtitle
- [ ] Minimize / Maximize / Close chrome buttons with Segoe MDL2 Assets icons
- [ ] All button styles defined (`PrimaryButton`, `SecondaryButton`, `DangerButton`, `SuccessButton`)
- [ ] DataGrid with correct header/row styles (if applicable)
- [ ] Status bar: `#F8F9FA` background, `#546E7A` top border
- [ ] Copyright TextBlock overlay before closing `</Grid>`
- [ ] Font: `Inter` throughout (no Manrope, no Segoe UI)
- [ ] `minimize_button_clicked`, `maximize_button_clicked`, `close_button_clicked` implemented in Python
- [ ] No logo/image loading — logo was removed

## Template References

See `.claude/docs/` for full XAML and Python templates:
- `wpf-window-templates.md` — Window structure, button styles, DataGrid, info box XAML
- `python-wpf-pattern.md` — Python WPF Window class pattern
