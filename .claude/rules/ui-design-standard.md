# UI Design Standard

All new pyrevit tool windows **MUST** follow the **T3Lab Kinetix** design system.
The canonical reference is **`BulkFamilyExport.xaml`**.

## Design Reference Files
- **Canonical UI (reference)**: `T3Lab.extension/lib/GUI/Tools/BulkFamilyExport.xaml`
- **All XAML files**: `T3Lab.extension/lib/GUI/Tools/`
- **XAML templates doc**: `.claude/docs/wpf-window-templates.md`
- **Python WPF pattern**: `.claude/docs/python-wpf-pattern.md`

## Two XAML Variants

The codebase has **two distinct XAML root patterns**. Pick the one that matches the use case — do not mix them inside a single file.

### Variant A — Standard Tool Window (default, 24 files)
- Root is `<Window>` and the XAML defines its own chrome and title bar.
- Loaded from Python via `wpf.LoadComponent(self, xaml_path)` after `Window.__init__()`.
- **Use this for every new tool.** Examples: `BulkFamilyExport.xaml`, `AutoDimension.xaml`, `DWGManagement.xaml`.

### Variant B — Modal Dialog Content (rare, 3 existing files)
- Root is `<Grid>`; the XAML provides only the content. The hosting Python class creates a `Window` with `WindowStyle=NoStyle`, `AllowsTransparency=True`, then assigns the parsed Grid to `self.Content`.
- Used only for borderless modal popups with a custom in-grid title bar.
- **Do not create new Variant B files** unless you are explicitly building a borderless modal that must be hosted inside another window. Existing files: `FamilyLoader.xaml`, `FamilyLoaderCloud.xaml`, `ParameterSelector.xaml`.

The rest of this document targets **Variant A** unless otherwise noted.

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
- **Never** use `Manrope` or `Segoe UI` for body text. `Segoe MDL2 Assets` is allowed **only** for icon glyphs.

## Window Structure (every tool)

1. `<Window>` with `Background="White"`, `ResizeMode="CanResizeWithGrip"`, `FontFamily="Inter"`
2. **WindowChrome — multi-line form is required.** All five attributes below must be present together. The single-line shortcut (`<WindowChrome CaptionHeight="64" UseAeroCaptionButtons="False" ResizeBorderThickness="5"/>`) silently drops `CornerRadius` and `GlassFrameThickness` and breaks the rounded-corner shell:
   ```xml
   <WindowChrome.WindowChrome>
       <WindowChrome CaptionHeight="64"
                     ResizeBorderThickness="5"
                     GlassFrameThickness="0"
                     CornerRadius="8"
                     UseAeroCaptionButtons="False"/>
   </WindowChrome.WindowChrome>
   ```
3. **Title bar** (Row 0, Height=64): White bg
   - Left: `T3Lab` (11px Bold `#083D56`) + Tool Name (18px Bold `#2C3E50`)
   - Below: Separator (1px `#546E7A`) + subtitle (10px Italic `#7F8C8D`)
   - Right: Min/Max/Close buttons (Segoe MDL2 Assets glyphs — see table below)
   - Bottom: Border 1px `#546E7A`
4. **Content area** — tool-specific
5. **Status bar** (last row): `Background="#F8F9FA"`, `BorderBrush="#546E7A"`, `BorderThickness="0,1,0,0"`, `Padding="14,8"`
6. **Copyright overlay** — always before closing root `<Grid>`

## Button Style Keys (Window.Resources)

| Style Key         | Background  | Foreground | Hover Bg   | Border     | Padding  | FontSize | CornerRadius |
|-------------------|-------------|------------|------------|------------|----------|----------|--------------|
| `PrimaryButton`   | `#083D56`   | White      | `#062A3C`  | none       | `12,6`   | 12       | 3            |
| `SecondaryButton` | `#F8F9FA`   | `#2C3E50`  | `#E2E6EA`  | `#546E7A`  | `12,6`   | 12       | 3            |
| `SuccessButton`   | `#27AE60`   | White      | `#1E8449`  | none       | `12,6`   | 12       | 3            |
| `DangerButton`    | `#D32F2F`   | White      | `#B71C1C`  | none       | `12,6`   | 12       | 3            |
| `WinCtrlButton`   | Transparent | —          | `#F8F9FA`  | none       | —        | —        | —            |
| `CloseButton`     | Transparent | —          | `#D32F2F`  | none       | —        | —        | —            |

## Window Control Buttons

The three chrome buttons (min/max/close) must use **embedded `<TextBlock>` children** with explicit `FontFamily`, `FontSize`, and `Foreground` — never the inline `Content="…"` shortcut, which inherits style colors and breaks visual consistency.

### Glyph table (Segoe MDL2 Assets, FontSize 10, Foreground `#083D56`)

| Button   | Glyph      | Notes                                                  |
|----------|------------|--------------------------------------------------------|
| Minimize | `&#xE921;` | Do **not** substitute Unicode minus `&#x2212;`.        |
| Maximize | `&#xE922;` | Do **not** substitute Unicode white square `&#x25A1;`. |
| Close    | `&#xE8BB;` |                                                        |

### Canonical XAML
```xml
<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Top"
            WindowChrome.IsHitTestVisibleInChrome="True">
    <Button x:Name="btn_minimize" Style="{StaticResource WinCtrlButton}"
            Click="minimize_button_clicked" ToolTip="Minimize">
        <TextBlock Text="&#xE921;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#083D56"/>
    </Button>
    <Button x:Name="btn_maximize" Style="{StaticResource WinCtrlButton}"
            Click="maximize_button_clicked" ToolTip="Maximize">
        <TextBlock Text="&#xE922;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#083D56"/>
    </Button>
    <Button x:Name="btn_close" Style="{StaticResource CloseButton}"
            Click="close_button_clicked" ToolTip="Close">
        <TextBlock Text="&#xE8BB;" FontFamily="Segoe MDL2 Assets" FontSize="10" Foreground="#083D56"/>
    </Button>
</StackPanel>
```

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

## Wizard-Style Navigation (multi-step tools)

When a tool has multiple steps (BatchOut / TileLayout / ExportManager):
- Use hidden `TabItem` with `Visibility="Collapsed"` — navigation driven by code-behind
- Add a **step progress bar** (Row 1) with numbered circles
- Action bar contains Back (`SecondaryButton`) + Next (`SuccessButton`)
- Nav step icons (Segoe MDL2 Assets): Selection `&#xE14C;`, Format `&#xE1DC;`, Queue `&#xE914;`, Settings `&#xE713;`

## Copyright Notice

**Exactly one copyright TextBlock per file**, placed as the **last child of the root `<Grid>`** (immediately before the closing `</Grid>`), using this **verbatim snippet** — same for Variant A and Variant B:

```xml
<!-- Copyright added automatically -->
<TextBlock Text="© Copyright by T3Lab" HorizontalAlignment="Right" VerticalAlignment="Bottom" Margin="0,0,14,8" Foreground="#3498DB" FontSize="11" IsHitTestVisible="False" Panel.ZIndex="999"/>
```

- Do **not** embed it inside the status bar, action bar, or any other content `<Border>` / `<StackPanel>` — it must be a direct child of the root `<Grid>` so it floats as an overlay.
- Do **not** add `Grid.Row`, `Grid.Column`, or `Grid.RowSpan` — keep the snippet byte-for-byte identical across files.
- Do **not** use `&#169;` — use the literal `©` character.
- Do **not** duplicate it (some legacy files had two copyrights; only one is allowed).

## Common Deviations to Avoid

These mistakes have all appeared in the codebase before — flag them in review.

| Anti-pattern                                                            | Use instead                                                          |
|-------------------------------------------------------------------------|----------------------------------------------------------------------|
| Single-line `<WindowChrome CaptionHeight="64" .../>`                    | Multi-line form with all 5 attributes (see Window Structure §2)      |
| `<Button Content="&#x25A1;" />` for maximize                            | `<Button><TextBlock Text="&#xE922;" FontFamily="Segoe MDL2 Assets" .../></Button>` |
| `<Button Content="&#x2212;" />` for minimize                            | Same pattern with `&#xE921;`                                         |
| `FontFamily="Manrope"` or `FontFamily="Segoe UI"`                       | `FontFamily="Inter"` (only Segoe MDL2 Assets is allowed, for glyphs) |
| `<Image Source="…/T3Lab_logo.png"/>` in title bar                       | Logo was removed; do not add it back                                 |
| Hard-coded button colors (`Background="#FF0000"` etc.)                  | Use the named styles in `Window.Resources`                           |
| Status bar with `Background="White"`                                    | Status bar background is `#F8F9FA`                                   |
| Copyright TextBlock embedded inside a status-bar / action-bar Border    | Place it as the last child of the root `<Grid>` (overlay form)       |
| Two copyright TextBlocks in the same file                               | Exactly one, using the verbatim snippet                              |

## Checklist for New Tools

When creating a new pushbutton with a WPF UI:

- [ ] `Window.Background="White"`, `ResizeMode="CanResizeWithGrip"`, `FontFamily="Inter"`
- [ ] `WindowChrome` uses multi-line form with all 5 attributes (`CaptionHeight=64`, `ResizeBorderThickness=5`, `GlassFrameThickness=0`, `CornerRadius=8`, `UseAeroCaptionButtons=False`)
- [ ] Title bar: 64px, white, T3Lab brand (#083D56) + tool name + subtitle
- [ ] Separator (1px #546E7A) between title and subtitle
- [ ] Min/Max/Close buttons use the canonical TextBlock pattern with Segoe MDL2 glyphs `&#xE921; &#xE922; &#xE8BB;` (FontSize=10, Foreground=#083D56)
- [ ] All button styles defined (`PrimaryButton`, `SecondaryButton`, `DangerButton`, `SuccessButton`)
- [ ] DataGrid with correct header/row styles (if applicable)
- [ ] Status bar: `#F8F9FA` background, `#546E7A` top border
- [ ] Copyright TextBlock overlay before closing `</Grid>`
- [ ] Font: `Inter` throughout (no Manrope, no Segoe UI)
- [ ] `minimize_button_clicked`, `maximize_button_clicked`, `close_button_clicked` implemented in Python
- [ ] No logo/image loading — logo was removed
- [ ] No deviation from the "Common Deviations to Avoid" table above

## Template References

See `.claude/docs/` for full XAML and Python templates:
- `wpf-window-templates.md` — Window structure, button styles, DataGrid, info box XAML
- `python-wpf-pattern.md` — Python WPF Window class pattern
