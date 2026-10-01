# UI consistency review — 2026-10-01

## Outcome and scope

Reviewed all 65 tool XAML files against `pyRevit UI Design System/T3LAB_UI_STANDARD.md` and the shared stylesheet. Repository inventory contains 92 XAML files: 65 tool surfaces, 23 archived files, the shared T3 source dictionary, legacy runtime `WPF_styles.xaml`, legacy `Renaming/GUI_BaseRename.xaml`, and `scratch/original_dwg.xaml`. Archived/reference files were inventoried separately, not rewritten. Tool count includes the showcase and a row template; it is not a count of independently launched dialogs.

Reviewed Python-generated controls and runtime brush overrides as well as XAML. Green XAML gates do not cover those surfaces. Changes use existing T3 resources; the shared stylesheet and Revit transactions, element processing, preview/model colors and provider choices were not changed.

The user's whole-UI review includes source consistency in DWG Management, BatchOut and Assistant surfaces previously excluded by older governance documents. This is not a visual redesign or a claim that every runtime workflow has been exercised. Existing session changes to ManaLoca, DWG navigation, ManaGroup, ManaStyles and Workset navigation were preserved.

## Consistency changes

| Surface | Source inconsistency | Result |
|---|---|---|
| AutoWork; ExportManager and ExportManagerTest | Source selectors used ordinary radio circles | Seven source controls use the existing T3.Chip navigation convention; form option radios remain radio buttons |
| ManaTabs; ManaAnno; ManaStyles; SelectFromDict | Seven fixed 48px bars used vertical padding | Padding is horizontal only; content is vertically centered |
| ExportManager and ExportManagerTest | Row-selection boxes used the label checkbox style | T3.CheckBox.Cell supplies consistent padding and centering |
| BatchOut settings | Runtime expansion restored legacy blue/gray borders and Unicode arrows | T3.Ink/T3.Border and matching MDL2 chevrons; 14 XAML arrows aligned with the runtime handler |
| PropertyLine; Feedback; MCP Control | Runtime state colors bypassed the stylesheet | Semantic T3 status resources; state messages and actions retained |
| ManaSelect | Sidebar matched Python wrapper identity | Stable control names select all five modes; current-mode fallback retained |
| ManaContains | Generated result headers, rows and checkboxes had separate styling | Shared text, header surface, border and checkbox styles |
| TagChecker | Generated categories and result indicator used legacy brushes | Shared checkbox and semantic result resources |
| Assistant comment cards | Custom small fonts/buttons/colors/spacing | T3 typography, secondary buttons, semantic states and resource references; chat language and callbacks retained |
| AdvancedViewManager parameter chooser | Inter font, separate colors, green primary, field label beside input | Owner T3 resources, label above input, Cancel then primary, left copyright |
| TileLayout option details | Separate colors/type/control styling | Owner T3 resources and compact styled controls; preview colors retained |

Navigation chips, form radios, read-only state checkboxes, editable/selectable checkboxes, data swatches and status indicators serve different purposes. They were reviewed by role rather than replaced indiscriminately. Existing compact table controls were not enlarged without evidence of clipping. No new design-system component or dependency was introduced.

## Validation and limits

Baseline and final checks: T3 audit (65 files, zero violations), static tools audit, stylesheet synchronization (65 files, zero drift), wiring audit (zero W1/W2/W3/D1), and nine UI-overlap tests pass. Ribbon icon audit passes for 45/45 audited bundles with zero errors, warnings or migration debt; its documented exemptions remain.

Six focused runtime UI regression tests cover ManaSelect navigation and status/expansion transitions. Earlier session checks for Workset sidebar (three tests) and DWG navigation (seven tests) also pass. Existing Assistant UI checks pass (42 assertions). Modified Python files compile. XAML XML parsing, resource-key checks, names/events/bindings comparisons against the pre-review working files and whitespace checks pass. Native WPF rendering was not executed on this Linux machine.

Wiring audit advisory dead-code/orphan counts are pre-existing (D2=29, D3=3); they are not proof that a control is unused. Existing historical reports mention old counts and deleted audits; they should not be treated as current readiness evidence. No UX score is assigned without runtime, keyboard and DPI testing.

## Remaining findings

| Priority | Finding | Status / next check |
|---|---|---|
| P2 | Actual rendering, keyboard focus, click routing and text clipping at 100%/125% DPI | NEEDS VERIFICATION in Windows/Revit; checklist below |
| P2 | The two Python-built option/parameter dialogs still use native Window chrome | T3 presentation improved; custom chrome migration remains a separate design change requiring runtime validation |
| P3 | Legacy Renaming/GUI_BaseRename.xaml and old WPF_styles.xaml outside the tool folder | Inventoried legacy surfaces; no current ribbon consumer of BaseClass_FindReplace found by static search, but dynamic use cannot be ruled out; do not delete or migrate blindly |
| P3 | TagCheckerDialog and CADToElementsDialog load some XAML directly with XamlReader | Existing loading compatibility risk; route migration requires runtime-focused work and is not a cosmetic consistency fix |
| P3 | Archived 23 XAML and scratch/original_dwg snapshot | Historical/reference surfaces; intentionally retained |
| P3 | Glyph definitions and data-dependent layouts | Runtime font availability, model size, localization and contrast need real rendering; static audits are not visual certification |

## Windows/Revit checklist

- [ ] Open the reviewed tools at 100% and 125% DPI, including minimum supported window widths; inspect header, footer, list columns and toolbar clipping.
- [ ] AutoWork and BatchOut: switch each source chip, verify identical data-selection behavior and labels.
- [ ] BatchOut: expand/collapse all seven format settings, check chevrons, neutral borders and checkbox alignment; verify existing export settings remain intact.
- [ ] ManaTabs, ManaAnno, ManaStyles and SelectFromDict: inspect centered fixed bars; test search and normal actions.
- [ ] ManaSelect: click all five rail modes and click the active tile again; verify visible page and active icon agree.
- [ ] Workset Manager: verify all three sidebar tabs; DWG Management: double-click a concrete Views name and verify navigation.
- [ ] PropertyLine, Feedback and MCP Control: exercise idle, working, success and error states; verify both text feedback and colors.
- [ ] ManaContains and TagChecker: populate results/categories, toggle checkboxes and verify callbacks and counts.
- [ ] Assistant: display a comment report, test Run/Note/Skip and inspect it in supported light/dark host themes.
- [ ] AdvancedViewManager: open parameter chooser, search, add a column, cancel and resize; confirm field/footer layout and keyboard actions.
- [ ] TileLayout: open option details, change angle and shift, reset and close; confirm compact controls fit and preview geometry/colors are unchanged.

## Tool-surface inventory

Dimensions are read from source, not a UX score or a runtime sizing guarantee. Paths in this table are relative to `T3Lab.extension/lib/GUI/Tools/`.

| File | Root | Width × height | Evidence |
|---|---|---|---|
| `AdvancedViewManager.xaml` | Window | 1200 × 800 | Static review; Revit/DPI unverified |
| `AdvancedViewManagerBatchRename.xaml` | Window | 700 × 540 | Static review; Revit/DPI unverified |
| `AutoDimension.xaml` | Window | 880 × 680 | Static review; Revit/DPI unverified |
| `AutoJoin.xaml` | Window | 780 × 620 | Static review; Revit/DPI unverified |
| `AutoWork.xaml` | Window | 1100 × 700 | Static review; Revit/DPI unverified |
| `BCFReader.xaml` | Window | 1180 × 780 | Static review; Revit/DPI unverified |
| `BGTheme.xaml` | Window | 460 × 740 | Static review; Revit/DPI unverified |
| `BatchLink.xaml` | Window | 1080 × 720 | Static review; Revit/DPI unverified |
| `CADToElements.xaml` | Window | 940 × 820 | Static review; Revit/DPI unverified |
| `CADtoBeam.xaml` | Window | 640 × 620 | Static review; Revit/DPI unverified |
| `CadtoFloor.xaml` | Window | 880 × 700 | Static review; Revit/DPI unverified |
| `CadtoFloorLayerItem.xaml` | Border | auto × auto | Static review; Revit/DPI unverified |
| `CadtoWall.xaml` | Window | 880 × 680 | Static review; Revit/DPI unverified |
| `ContainsDefineValue.xaml` | Window | 560 × 440 | Static review; Revit/DPI unverified |
| `ContainsSetParam.xaml` | Window | 420 × 280 | Static review; Revit/DPI unverified |
| `CropSync.xaml` | Window | 880 × 580 | Static review; Revit/DPI unverified |
| `DWGManagement.xaml` | Window | 1100 × 720 | Static review; Revit/DPI unverified |
| `DatumSync.xaml` | Window | 880 × 580 | Static review; Revit/DPI unverified |
| `DimText.xaml` | Window | 500 × 680 | Static review; Revit/DPI unverified |
| `DoorThreshold.xaml` | Window | 880 × 620 | Static review; Revit/DPI unverified |
| `ExportManager.xaml` | Window | 1280 × 780 | Static review; Revit/DPI unverified |
| `ExportManagerTest.xaml` | Window | 1280 × 780 | Static review; Revit/DPI unverified |
| `FamiGen.xaml` | Window | 1180 × 800 | Static review; Revit/DPI unverified |
| `FamilyTransfer.xaml` | Window | 1000 × 620 | Static review; Revit/DPI unverified |
| `Feedback.xaml` | Window | 460 × 680 | Static review; Revit/DPI unverified |
| `FindReplace.xaml` | Window | 440 × 340 | Static review; Revit/DPI unverified |
| `IFCSG.xaml` | Window | 1250 × 820 | Static review; Revit/DPI unverified |
| `ImageToDrafting.xaml` | Window | 640 × 680 | Static review; Revit/DPI unverified |
| `LLMSetting.xaml` | Window | 460 × 700 | Static review; Revit/DPI unverified |
| `MCPControl.xaml` | Window | 460 × 820 | Static review; Revit/DPI unverified |
| `MakePattern.xaml` | Window | 1040 × 680 | Static review; Revit/DPI unverified |
| `ManaAnno.xaml` | Window | 1180 × 780 | Static review; Revit/DPI unverified |
| `ManaContains.xaml` | Window | 1380 × 820 | Static review; Revit/DPI unverified |
| `ManaFami.xaml` | Window | 1200 × 800 | Static review; Revit/DPI unverified |
| `ManaGroup.xaml` | Window | 1080 × 720 | Static review; Revit/DPI unverified |
| `ManaLoca.xaml` | Window | 1200 × 740 | Static review; Revit/DPI unverified |
| `ManaPara.xaml` | Window | 1100 × 750 | Static review; Revit/DPI unverified |
| `ManaSched.xaml` | Window | 1160 × 780 | Static review; Revit/DPI unverified |
| `ManaSelect.xaml` | Window | 560 × 840 | Static review; Revit/DPI unverified |
| `ManaSheets.xaml` | Window | 1200 × 720 | Static review; Revit/DPI unverified |
| `ManaStyles.xaml` | Window | 1260 × 780 | Static review; Revit/DPI unverified |
| `ManaTabs.xaml` | Window | 460 × 560 | Static review; Revit/DPI unverified |
| `ManaViews.xaml` | Window | 1280 × 720 | Static review; Revit/DPI unverified |
| `ManaWorkset.xaml` | Window | 1080 × 720 | Static review; Revit/DPI unverified |
| `ModelAuditor.xaml` | Window | 1200 × 800 | Static review; Revit/DPI unverified |
| `ModelAuditorDetail.xaml` | Window | 820 × 560 | Static review; Revit/DPI unverified |
| `PDFImport.xaml` | Window | 1080 × 720 | Static review; Revit/DPI unverified |
| `ParameterSelector.xaml` | Window | 760 × 560 | Static review; Revit/DPI unverified |
| `PointCloud.xaml` | Window | 960 × 680 | Static review; Revit/DPI unverified |
| `PropertyLine.xaml` | Window | 1080 × 720 | Static review; Revit/DPI unverified |
| `QuickElement.xaml` | Window | 980 × 720 | Static review; Revit/DPI unverified |
| `RibbonNames.xaml` | Window | 460 × 680 | Static review; Revit/DPI unverified |
| `RoomToFloor.xaml` | Window | 880 × 620 | Static review; Revit/DPI unverified |
| `SelectFromDict.xaml` | Window | 560 × 560 | Static review; Revit/DPI unverified |
| `SheetGen.xaml` | Window | 1100 × 720 | Static review; Revit/DPI unverified |
| `SplitElements.xaml` | Window | 480 × 360 | Static review; Revit/DPI unverified |
| `SubtypeDefinerColMap.xaml` | Window | 520 × 420 | Static review; Revit/DPI unverified |
| `T3Dialog.xaml` | Window | 440 × 240 | Static review; Revit/DPI unverified |
| `T3LabAssistant.xaml` | Window | 560 × 720 | Static review; Revit/DPI unverified |
| `TagChecker.xaml` | Window | 540 × 720 | Static review; Revit/DPI unverified |
| `TextToElement.xaml` | Window | 760 × 820 | Static review; Revit/DPI unverified |
| `TileLayout.xaml` | Window | 960 × 700 | Static review; Revit/DPI unverified |
| `UIStandardShowcase.xaml` | Window | 1180 × 720 | Static review; Revit/DPI unverified |
| `WallAdjustBase.xaml` | Window | 480 × 430 | Static review; Revit/DPI unverified |
| `WallCutProfile.xaml` | Window | 580 × 580 | Static review; Revit/DPI unverified |
