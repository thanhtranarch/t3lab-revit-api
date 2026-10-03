# Rebar & Assembly toolkit — Implementation Spec (buildable)

> Ngày lập: 2026-10-02 · Trạng thái: **SPEC CHỐT — chưa có dòng code tool nào** ·
> Nguồn gốc: `dev/plan/rebar-tekla-toolkit-roadmap.md` (rev 3). Spec này **thay roadmap** làm nguồn
> cho builder: khi hai file lệch nhau, spec thắng.
>
> Builder (Opus / Sonnet) đọc **đúng mục của package mình** (§7) + §0–§4 (nền chung) rồi code
> **không hỏi lại**. Mọi hành vi chạm Revit runtime trong spec này là **NEEDS VERIFICATION** cho tới khi
> `dev/debug/spike_rebar_assembly.py` (§3.11) chạy xong trên Revit 2027 — code vì thế phải *dò* (probe)
> và *fallback* với thông báo cho người dùng, không được giả định.
>
> Ngôn ngữ: nội dung code-facing bằng **English**; bình luận bằng tiếng Việt được phép. **Mọi chữ hiện
> trong Revit là ENGLISH.** Tài liệu `docs/tekla-to-revit-2027.md` song ngữ EN/VI.

Legend: `[PURE]` pure Python, test được ngoài Revit · `[REVIT]` chạm Revit API · `[NV]` NEEDS VERIFICATION
on Revit 2027 · `[CITED]` xác nhận bằng tài liệu bên ngoài (nguồn ở §2).

---

## 0 · Reading order & gates (what every builder runs)

Read first, in this order: `CLAUDE.md` → `.claude/CLAUDE.md` → `.claude/rules/new-tool-standard.md`
(XAML rules 1–29, script rules S1–S19) → `pyRevit UI Design System/T3LAB_UI_STANDARD.md` →
`.claude/skills/xaml-templates.md` → `.claude/skills/wpf-pattern.md` → this spec.

Reference implementations to copy structure from (do not copy logic):

| Need | Copy from |
|---|---|
| `script.py` frame, bootstrap, `resolve_doc` | `T3Lab.extension/T3Lab Model.tab/Standards & Settings.panel/ManaGroup.pushbutton/script.py` |
| Dialog class / Snippets split, row classes, `AddHandler(CheckBox.ClickEvent, …)`, `_select_in_revit` | `lib/GUI/ManaGroupDialog.py` + `lib/Snippets/_group_ops.py` |
| Window chrome: outer Border, title bar, rail tiles, hidden TabControl, footer | `lib/GUI/Tools/ManaWorkset.xaml` lines 1715–1830 and 2118–2136; rail handler `ManaWorksetDialog.nav_toggle_clicked` |
| Footer progress (`progress_panel` / `pb_run` / `status_text`) | `lib/GUI/Tools/CADToElements.xaml` lines 2468–2495; API `T3WPFWindow.begin_progress / step_progress / end_progress / is_cancelled` (`lib/GUI/WPF_Base.py` 1294–1503) |
| TransactionGroup + per-item Transaction/SubTransaction, `Result` rows, `step()` callback | `lib/Snippets/family_transfer.py` `execute_plan` |
| `disposing`, `net_list`, `eid_value`, `make_eid`, `elem_name` | `lib/Snippets/_compat.py` |
| `resolve_doc`, `resolve_uidoc`, `host_uiapp`, `get_revit_version` | `lib/Snippets/_host.py` |
| `confirm / show_info / show_warning / show_error` | `lib/GUI/T3Dialog.py` |
| Tests that exec a Snippets module with Revit stubbed | `dev/test_group_manager.py` `_load_group_ops_module` |
| Per-user data file under `%APPDATA%\T3LabAI` | `lib/core/paths.py` `user_data_path(*parts)` |
| Picking in model from a modal window | `lib/GUI/ManaLocaDialog.py` `_pick_elements` (window `Hide()` → `PickObjects` → `Show()`); `OperationCanceledException` handled |
| Temporary isolate | `lib/GUI/ManaSelectDialog.py` ~865 (`IsolateElementsTemporary` inside a `Transaction`) |
| Ribbon icon | `…/ManaGroup.pushbutton/icon.svg` (32×32, tokens only) + `docs/ui-governance/09-ribbon-icon-standard.md` |

Gates (every package, before handing back — all must be GREEN; run with `PYTHONPATH=""`):

```
python3 dev/audit_t3.py --quiet            # UI: 29 XAML rules, glyph tables, copyright, chrome, string bridge
python3 dev/audit_tools.py --quiet         # shebang, py3 syntax, XAML handler ↔ method, Start() without Commit()
python3 dev/audit_wiring.py --quiet        # W1 self.<name> missing in XAML · W2 handler in DataTemplate · W3 dead button · D1 dead handler
python3 dev/audit_revit_compat.py --quiet  # API removed/added inside 2022–2027 must sit in a short try / hasattr guard / _compat helper
python3 dev/audit_cpython.py --quiet       # 0 P0: C2 module-level revit.doc · C4 __namespace__ in script.py · C6 print() · C10 bare `with Transaction` · C11 ItemsSource = list
python3 dev/audit_api_context.py --quiet   # only bites modeless dialogs (none in V1)
python3 dev/sync_t3_styles.py --check      # stylesheet block embedded & identical
python3 dev/check_xaml_load.py --out /tmp/t3xaml   # sanitiser output still well-formed
python3 dev/build_icons.py --check && python3 dev/audit_icons.py --quiet
python3 dev/test_<package tests>.py        # see §6
```

Windows-only (coordinator, before QA in Revit): `powershell -STA -File dev/check_xaml_wpf.ps1 -Dir %TEMP%\t3xaml`
and `powershell -STA -File dev/preview_t3_xaml.ps1 -Xaml <file> -Out out.png`.

What the gates concretely check that bites new tools (so you do not discover it after the fact):

- `audit_t3`: every `{StaticResource T3.*}` key must exist in `pyRevit UI Design System/T3Lab.Styles.xaml` (full key list in §4.1); FontSize only 19/15/13/11.5/11/12.5; Margin/Padding values only 4/8/12/16/24/32 (0 allowed); CornerRadius only via `T3.R.*`; exactly one `T3.Button.Primary`, right-most in the footer, `IsDefault="True"` on it; `IsCancel="True"` on the `T3.WinClose` button; exactly one `<TextBlock Style="{StaticResource T3.Copyright}"/>` first in the footer's left StackPanel; every `DataGrid`/`ListBox` has an `x:Name` sibling `TextBlock Style="T3.Empty"`; no `DataGrid`/`ListBox` inside a `ScrollViewer`; `HorizontalScrollBarVisibility="Disabled"` on grids (`T3.DataGrid`/`T3.ListBox` already set it — do not re-declare on `ListBox` as a direct attribute, it is `ScrollViewer.HorizontalScrollBarVisibility`); row checkbox columns use the string bridge + header select-all `chk_all_<grid>` + handler `select_all_<grid>_clicked`; no `DataTrigger` bound straight to a Python row property (use `ElementName` bridge or `AutomationProperties.ItemStatus`); glyphs only from the two tables (§4.2) and present in `dev/icons/mdl2_codepoints.tsv`; no Unicode icon characters; no inline `FontFamily="Segoe MDL2 Assets"`; custom chrome = `WindowStyle="None" AllowsTransparency="True" Background="Transparent"` + outer `Border BorderBrush=T3.BorderStrong BorderThickness=1 CornerRadius=T3.R.Window ClipToBounds=True`, no `CanResizeWithGrip`; no footer/body button whose handler only closes the window (rule 29); no Vietnamese diacritics in user-facing strings; no `<Grid.RowDefinition/>` dot-notation typo; no `Effect`.
- `audit_tools`: pushbutton `script.py` line 1 is exactly `#! python3`; every `Click="x"` in a XAML has `def x(self, sender, e)` in the Python file that references that XAML (or in `T3WPFWindow`); every file that calls `.Start()` also contains `.Commit()` or `.Assimilate()`; no XAML without a Python referrer.
- `audit_wiring`: every `self.<name>` read in a dialog must be an `x:Name` in its XAML, be assigned somewhere, or be guarded (`hasattr`/`getattr`/`FindName`); no event attribute inside `<DataTemplate>`; a `Button` without `Click=` must have its `x:Name` wired from Python (`self.btn.Click += …`); handler-looking methods (`*_clicked`, `*_changed`, `on_*`) must be referenced by a XAML or a call.
- `audit_revit_compat`: `element_id.Value` / `.IntegerValue` → use `eid_value`; API added after 2022 or removed before 2027 (list in that file's `RULES`) must be inside a ≤40-line `try:` or a `hasattr`/`getattr`/version `if`, or carry `# revit-compat: ok`. Coordinator adds the rebar rules of §7.0 to `RULES` so `RebarHookOrientation` (removed 2027), `BarTerminationsData` (added 2026), `Rebar.Mass` / `BarMassPerUnitLength` (added 2027), `NumberingSchemaType` (removed 2027) are also enforced.
- `audit_cpython`: no `doc = revit.doc` at module level (resolve inside functions); no class with `__namespace__` inside `script.py` (put `ISelectionFilter`s in `lib/Snippets/`); no bare `print()`; `with disposing(Transaction(...)) as t:` only; `self.set_items_source(grid, rows)` instead of `grid.ItemsSource = rows`.

---

## 1 · Final decisions

Owner's decisions D1–D7 are binding; D8–D18 are the planning lead's, each with a one-line rationale.

| # | Decision | Rationale |
|---|---|---|
| D1 | V1 treats in-situ and precast the same — no precast mode, no embeds/lifters logic. | Owner. Assembly rules A1–A8 are identical for both. |
| D2 | BVBS V1 = `BF2D` only. Any bar whose centreline is not planar, or contains arcs that are not bends, is reported `skipped: free-form 3D` / `skipped: curved leg`. | Owner. BF3D/BFMA later. |
| D3 | `docs/tekla-to-revit-2027.md` is bilingual EN/VI, generated from `lib/data/tekla_bridge.json`. All in-Revit UI text is English. | Owner. One source for tool + docs. |
| D4 | **Keyboard shortcuts: no invented keymap.** Ship `lib/data/KeyboardShortcuts_Tekla.xml` as a *template generated by `dev/build_tekla_docs.py`*: one `<ShortcutItem>` per Bridge row that has a Revit command id, `Shortcuts=""` (empty), with an XML comment carrying the Tekla command and its *documented* Tekla default key (cited from Tekla's "Default keyboard shortcuts" page, §2.4) so the user decides. `CommandId` values are **not hand-written**: they are filled from `RevitCommandId.Name` logged by the spike (§3.11, probe G10) into `revit_command_id` in the JSON; rows without an id are emitted as a comment, not as an item. Docs §"Shortcuts" explain: Keyboard Shortcuts dialog → Import, how Revit key sequences differ from Tekla chords (Revit uses typed letter sequences; single letters and most Ctrl-chords are reserved or ambiguous), and which Tekla defaults *could* be adopted without clashing (e.g. `Ctrl+H` Phase manager → `Phases`). | Tekla defaults are single letters / F-keys / Ctrl-chords that collide with Revit reserved keys; a wrong mapping is worse than none, and Revit's XML format (`<ShortcutItem CommandName CommandId Shortcuts Paths/>`, `#`-separated shortcuts, `CommandId` language-independent) is only *reported* by forum posts, not by Autodesk docs — `[NV]`. |
| D5 | The Revit spike cannot run here. Every G-assumption is a runtime probe or try/fallback with a user-visible message. `dev/debug/spike_rebar_assembly.py` ships in WP1 and logs to `%APPDATA%\T3LabAI\spike_rebar.log`. All Revit-runtime behaviour is `[NV]`. | Owner. |
| D6 | Target Revit 2027. **Correction of fact:** Revit 2027 runs on **.NET 10** (Autodesk SDK blog, §2.3), 2025/2026 on .NET 8; pythonnet 3 / CPython 3.12 in both. Code still passes `audit_revit_compat.py` for 2022–2027 through `_compat` helpers. | Owner + research. The .NET 8 note in the brief was a 2025/2026 fact. |
| D7 | Rebar Wizard V1: rectangular straight beams, rectangular columns, pad-footing bottom mesh. Everything else → "out of scope" row, no exception. | Owner. |
| D8 | **All six windows are modal** (`ShowDialog()`), no `ExternalEvent`, no `__persistentengine__`. "Select in Revit" sets the selection and tells the user to close the window (ManaGroup pattern). "Pick in model" uses `Hide()` → `PickObjects` → `Show()`. | Removes the whole API-context / persistent-engine class of bugs on .NET 8/10 (CLAUDE.md §7); none of the six tools needs a live window while the user models. |
| D9 | One `TransactionGroup` per button click, `Assimilate()` at the end; one `Transaction` per assembly / host / file inside it (A6). Partition-by-rule and Isolate are single `Transaction`s. | S1/S2; Ctrl+Z = one step. |
| D10 | Tekla Bridge is **M 560×420** (not S as the roadmap said): ≥ 8 visible rows + a tip callout do not fit in 420×320. Still `ResizeMode="NoResize"`? No — `CanResize`, MinWidth 560 MinHeight 420. | Readability beats the roadmap's size guess; the pattern (P2) is unchanged. |
| D11 | Rail tiles only where a tool has ≥3 peer pages *and* is L-size (Cast Unit Manager). Clone Drawing and Rebar Wizard use the horizontal chip tab strip (`T3.Chip` + `T3.TabItem.Hidden`, standard §"Thanh tab ngang"). Rebar Check, BVBS, Tekla Bridge are single-page. | Fewer glyph decisions; no new glyph is needed anywhere (all picks are in the two tables, §4.2). |
| D12 | Rebar Check gets its own `lib/Snippets/_rebar_check.py`; it *calls* `_assembly.sync_rebar` and `_rebar.assign_partition` for the two fixes, never re-implements them. | Disjoint file ownership between WP3/WP5 while sharing logic. |
| D13 | Weight per metre chain for BVBS/Check: `RebarBarType.BarMassPerUnitLength` (Revit 2027+, `[NV]` units) → shared/project parameter `T3_WeightPerMetre` on the bar type → `lib/data/rebar_weights.json` by nominal Ø → `0.006165·d²`. The source used is shown in the preview row tooltip. | Revit 2027 added native mass (§2.3); the roadmap's parameter stays as the 2022–2026 path. |
| D14 | BVBS leg lengths are **outer (out-to-out) dimensions**, per BVBS 3.1 §"General": *"All length specifications of the 2D shapes are referred to the outer dimension… Diameters and radii are inner dimensions."* `_rebar.centerline_to_bvbs_segments` converts Revit's sharp-corner centreline polyline to outer legs (formula §3.3). `[NV]` against Revit's shape parameters (spike G12). | A wrong leg length is a wrong bar on the machine; the conversion is isolated and unit-tested. |
| D15 | Partition by rule writes `BuiltInParameter.NUMBER_PARTITION_PARAM` ("Partition", still present in the 2027 enum) and, on 2027+, warns when the model's Rebar `NumberingSchema` does not partition by that parameter (2027 numbering overhaul, §2.3). It never touches `Rebar Number`. | Layer-3 behaviour: Revit numbers; we only place bars in the right partition. |
| D16 | Rebar creation goes through one helper `_compat.create_rebar_from_curves` that tries the `BarTerminationsData` overload (2026+) and falls back to the `RebarHookOrientation` overload (2022–2025). `RebarHookOrientation` is **removed in 2027** (§2.3). | The only way one code path can satisfy 2022–2027 and the compat gate. |
| D17 | Clone Drawing V1 recreates (T3) **IndependentTag on elements** and **dimensions whose references are faces of matched host elements**; rebar-set-position tags, dimensions on rebar bars, spot elevations and `MultiReferenceAnnotation` are logged `unmatched: <kind> not supported in V1`. | MRA options API unverified; dimension-to-rebar references are not reproducible without the bar reference API. Honest scope. |
| D18 | No AI mode in any of the six tools. | Standard §"AI Mode": nothing here is a job rules do badly. |

---

## 2 · Research findings (facts the code relies on)

### 2.1 BVBS 2.0 / 3.1 — `BF2D` syntax and checksum `[CITED]`

Source: **BVBS-Guideline "Data exchange of reinforcement data" v3.1 (Bundesverband Bausoftware e.V., 2021-06-14)**,
<https://www.bvbs.de/wp-content/uploads/2024/01/BVBS-Guideline-Data-Exchange-Reinforcement-Data-3.1-Engl_20210614.pdf>;
corroborated by the C# gist <https://gist.github.com/IsNull/28e853ee16aaeb60135b> and Tekla's BVBS page
<https://support.tekla.com/doc/tekla-structures/2026/int_bvbs_exporting> (Tekla exports BVBS 2.0 by default, 3.0 optional).

- Record: `BF2D@` + `H…` header block + `G…` geometry block + optional `M…` coupler / `P…` private block + `C<n>@` checksum block, then **CR LF**. Every field ends with `@`; the block letter glues onto its first field (`HjTestPDF@r417@…`).
- Header fields **for BF2D, in this exact order**: `j` project · `r` schedule/drawing no. · `i` revision · `p` bar mark · `l` bar length [mm] · `n` quantity · `e` weight per bar [kg] · `d` steel diameter [mm] · `g` steel grade · `s` mandrel diameter [mm] · `v` designer (empty) · optional `a` layer · `t` delta-l staggered · `c` staggered group. **`m` (mesh type) is NOT a BF2D field** — it is BFMA/BFGT/BFAU only. The guideline: *"All field identifications permissible for a main group must be completed and in the listed order."*
- Geometry: `l<leg mm>@w<bend angle deg>@` pairs; bar ends with `w0@`; arcs as `r<inner radius>@w<arc angle>@[w<bend>@]`; sign: positive = bend one way, negative the other (`w-45`); decimals allowed (`w79.7`). Lengths are **outer dimensions**; `s` is the one mandrel diameter for all bends; deviations go through `r`.
- Checksum: `IP = 96 − (Σ ASCII(cᵢ) mod 32)` where the sum runs over **every character from the start of the record up to and including the `C`** of the checksum block. Guideline worked example: `"abcde@C"` → `96 − (626 mod 32) = 78`.
- **Reference fixtures (guideline §"Examples with barcodes", verified by computation — all five match with the "including C" rule; excluding the `C` gives 75 for example 1, which proves the rule):**

| # | Line (without CRLF) | Checksum |
|---|---|---|
| 1 | `BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@Gl400@w90@l600@w0@C72@` | 72 |
| 2 | `BF2D@HjTestPDF@r417@ia@p1@l800@n10@e0.710@d12@gB500A@s48@v@Gl100@w180@l600@w180@l100@w0@C71@` | 71 |
| 3 | `BF2D@HjTestPDF@r417@ia@p1@l1224@n10@e1.087@d12@gB500A@s48@v@Gl100@w90@l300@w45@l424@w-45@l300@w-90@l100@w0@C82@` | 82 |
| 4 | `BF2D@HjTestPDF@r417@ia@p1@l1428@n10@e1.268@d12@gB500A@s48@v@Gl400@w0@r400@w90@w0@l400@w0@C79@` | 79 |
| 5 | `BF2D@HjTestPDF@r417@ia@p1@l1428@n10@e1.268@d12@gB500A@s48@v@Gl400@w45@r400@w90@w45@l400@w0@C93@` | 93 |

`T3Lab.extension/lib/Snippets/_bvbs.py` `checksum()` was already "including C" → **correct, kept**. Its
`header_fields()` emitted a spurious `m` field → **fixed in this commit** (removed; see §3.5). Fixture 1 is the
unit-test anchor in `dev/test_bvbs_writer.py`.

### 2.2 Revit API facts used by the tools (rvtdocs 2025/2027, revitapidocs 2026) `[CITED]`

| API | Signature / fact | Source |
|---|---|---|
| `AssemblyInstance.Create(Document, ICollection<ElementId>, ElementId namingCategoryId)` | elements must be of a valid category, not already members; naming category must be the category of one member; **commit the creating transaction before touching `AssemblyTypeName`**. | rvtdocs.com/2025 AssemblyInstance.Create |
| `AssemblyInstance` members | `AddMemberIds(ICollection<ElementId>)`, `RemoveMemberIds`, `SetMemberIds`, `GetMemberIds()`, `IsMember(id)`, `AssemblyTypeName` (string, get/set), `NamingCategoryId`, `GetCenter()`→XYZ, `GetTransform()`→Transform, `SetTransform`, static `AreElementsValidForAssembly(Document, ICollection<ElementId>, ElementId assemblyInstanceId)`, `IsValidNamingCategory(Document, ElementId, ICollection<ElementId>)`, `CanRemoveElementsFromAssembly`, `Disassemble()`, `AllowsAssemblyViewCreation()`, `CompareAssemblyInstances`. | rvtdocs.com/2025 AssemblyInstance |
| `Element.AssemblyInstanceId` | ElementId of the owning assembly or `InvalidElementId`. | (API, long-standing) |
| `AssemblyViewUtils` | `Create3DOrthographic(doc, asmId[, templateId, isAssigned])`, `CreateDetailSection(doc, asmId, AssemblyDetailViewOrientation[, templateId, isAssigned])`, `CreatePartList(doc, asmId[, templateId, isAssigned])`, `CreateMaterialTakeoff(doc, asmId[, templateId, isAssigned])`, `CreateSingleCategorySchedule(doc, asmId, categoryId[, templateId, isAssigned])`, `CreateSheet(doc, asmId, titleBlockTypeId)`, `AcquireAssemblyViews(doc, sourceAsmId, targetAsmId)` (sibling instances of one type). | rvtdocs.com/2025 AssemblyViewUtils |
| `AssemblyDetailViewOrientation` | `HorizontalDetail` (looking down), `DetailSectionA` (looking north), `DetailSectionB` (looking west), `ElevationTop/Bottom/Left/Right/Front/Back` (cut plane on that bbox face, looking in). | rvtdocs.com/2025 |
| `View.AssociatedAssemblyInstanceId` | read-only ElementId of the owning assembly (`InvalidElementId` for normal views, `[NV]`). | rvtdocs.com/2025 |
| `ElementTransformUtils.CopyElements(View src, ICollection<ElementId>, View dst, Transform, CopyPasteOptions)` | view-to-view overload exists; constraints on element kinds not documented → spike G2/G3. | rvtdocs.com/2025 |
| `IndependentTag.Create(Document, ElementId tagTypeId, ElementId viewId, Reference, bool addLeader, TagOrientation, XYZ)` and `Create(Document, ElementId viewId, Reference, bool addLeader, TagMode, TagOrientation, XYZ)` | plus `GetTaggedReferences()`, `GetTaggedLocalElementIds()`, `TagHeadPosition`, `HasLeader`, `LeaderEndCondition`, `HasTagText()` (2026+). | rvtdocs.com/2025, revitapidocs 2026 news |
| `MultiReferenceAnnotation.Create(Document, ElementId viewId, MultiReferenceAnnotationOptions)`; `DimensionId`, `TagId` | options members unconfirmed → D17 excludes MRA from T3 in V1. | rvtdocs.com/2025 |
| `Viewport.Create(Document, sheetId, viewId, XYZ)`, `CanAddViewToSheet(doc, sheetId, viewId)`, `GetBoxCenter/SetBoxCenter`, `GetBoxOutline`, `LabelOffset`, `Rotation`, `ViewId`, `SheetId` | | rvtdocs.com/2025 Viewport |
| `Rebar.GetHostId()`, `SetHostId(doc, id)`; `RebarInSystem.GetHostId()`, `.SystemId` | | rvtdocs.com/2025 |
| `Rebar.GetCenterlineCurves(bool adjustForSelfIntersection, bool suppressHooks, bool suppressBendRadius, MultiplanarOption, int barPositionIndex)` → `IList<Curve>`; `GetTransformedCenterlineCurves(...)` same args; `RebarInSystem.GetCenterlineCurves(bool, bool, bool)` | `suppressBendRadius=True` returns the unfilleted (sharp-corner) chain; `suppressHooks=True` drops hook curves; `barPositionIndex` 0..`NumberOfBarPositions-1`. | rvtdocs.com/2026 Rebar.GetCenterlineCurves |
| `Rebar` props | `NumberOfBarPositions`, `Quantity`, `TotalLength`, `ScheduleMark`, `DoesBarExistAtPosition(i)`, `IncludeFirstBar/IncludeLastBar`, `GetShapeId()`, `IsRebarShapeDriven()`, `GetShapeDrivenAccessor()`, `GetHookTypeId(int end)`, **`Mass` (2027+)**, `SetLayoutFormula(string)` (2027+). | rvtdocs 2025/2027 |
| `RebarBarType` | `BarNominalDiameter`, `BarModelDiameter`, `StandardBendDiameter`, `StandardHookBendDiameter`, `StirrupTieBendDiameter`, `MaximumBendRadius`, `DeformationType`, **`BarMassPerUnitLength` (2027+)**. There is no `BarDiameter` property; use `BarNominalDiameter` (internal feet). | rvtdocs 2025/2027 |
| `Rebar.CreateFromCurves` | **2022–2025:** `(Document, RebarStyle, RebarBarType, RebarHookType startHook, RebarHookType endHook, Element host, XYZ norm, IList<Curve>, RebarHookOrientation, RebarHookOrientation, bool useExistingShapeIfPossible, bool createNewShape)`. **2026:** that overload obsolete; new `(Document, RebarStyle, RebarBarType, Element host, XYZ norm, IList<Curve>, BarTerminationsData, bool, bool)`. **2027:** only the `BarTerminationsData` overload; `RebarHookOrientation` enum, `GetHookOrientation/SetHookOrientation` **removed**; `RebarTerminationOrientation {Left, Right}` replaces it. | revitapidocs 2026 b020c9d5, rvtdocs 2027 Rebar, rvtdocs 2027 WhatsNew |
| `BarTerminationsData(Document)` | properties `HookTypeIdAtStart/End`, `CrankTypeIdAtStart/End`, `EndTreatmentTypeIdAtStart/End`, `TerminationOrientationAtStart/End` (`RebarTerminationOrientation`), `TerminationRotationAngleAtStart/End` (radians). | rvtdocs.com/2026 BarTerminationsData |
| `Rebar.CreateFromRebarShape(Document, RebarShape, RebarBarType, Element host, XYZ origin, XYZ xVec, XYZ yVec)` | unchanged 2022–2027. | rvtdocs 2025/2027 |
| `RebarShapeDrivenAccessor` | `SetLayoutAsSingle()`, `SetLayoutAsFixedNumber(int, double arrayLength, bool barsOnNormalSide, bool includeFirstBar, bool includeLastBar)`, `SetLayoutAsMaximumSpacing(double spacing, double arrayLength, bool, bool, bool)`, `SetLayoutAsNumberWithSpacing(int, double spacing, bool, bool, bool)`, `SetLayoutAsMinimumClearSpacing(double, double, bool, bool, bool)`, `SetLayoutAsCustomSpacing(string, double, bool, bool, bool)` (2027), `Normal`, `BarsOnNormalSide`, `ArrayLength`, `GetBarPositionTransform(i)`, `GetDistributionPath()`, `ScaleToBox(origin, xVec, yVec)`, `ComputeDrivingCurves()`, `SetRebarShapeId`. No obsolete members in 2027. | rvtdocs 2025/2027 |
| `RebarHostData` | `GetRebarHostData(Element)`, static `IsValidHost(Element)`, `GetRebarsInHost()`, `GetAreaReinforcementsInHost()`, `GetPathReinforcementsInHost()`, `GetFabricSheetsInHost()`, `GetFabricAreasInHost()`, `GetRebarContainersInHost()`, `GetCommonCoverType()`, `GetCoverType(Reference)`, `GetExposedFaces()`. | rvtdocs.com/2025 |
| `BuiltInParameter` | `NUMBER_PARTITION_PARAM` "Partition" (present in **2027**), `ASSEMBLY_NAME`, `ASSEMBLY_NAMING_CATEGORY`, `REBAR_ELEM_SCHEDULE_MARK`, `REBAR_ELEM_TOTAL_LENGTH`, `REBAR_ELEM_LENGTH`, `REBAR_ELEM_QUANTITY_OF_BARS`, `REBAR_SHAPE`, `REBAR_BAR_DIAMETER`, `REBAR_NUMBER_SUFFIX`. `REBAR_NUMBER` / `REBAR_ELEM_HOST_MARK` not confirmed by fetch (page truncated) → read "Rebar Number" via `LookupParameter("Rebar Number")` fallback. | rvtdocs 2025/2027 BuiltInParameter |
| Numbering (2027) | `NumberingSchema.GetSchemasInDocument(doc)`, `GetNumberingSchema(doc, string name)`, `GetPartitioningParameters()`→`ICollection<NumberingParameter>`, `AddPartitioningParameter`, `ChangePartitioningValue(string, string)`, `GetNumberingSequences()`, `GetNumberOfPartitions()`, `RemoveGaps(partition)`, `Enabled`, `NumberingParameterId`, `GetScopeDefiningCategories()`; `GetNumberingSchema(doc, NumberingSchemaType)` and `NumberingSchemaType` **obsolete/removed in 2027**. `NumberingParameter` carries an `ElementId` for a parameter + `NumberingParameterType`. | rvtdocs.com/2027 NumberingSchema, WhatsNew 2027 |
| `PostableCommand` (2027, 585 members) | present: `CreateAssembly`, `StructuralAreaReinforcement`, `StructuralPathReinforcement`, `StructuralFabricArea`, `SingleFabricSheetPlacement`, `ReinforcementSettings`, `RebarCoverSettings`, `RebarBendingDetail`, `AlignedMultiRebarAnnotation`, `LinearMultiRebarAnnotation`, `RunInterferenceCheck`, `KeyboardShortcuts`, `Copy`, `AlignedToSelectedViews`, `AlignedToCurrentView`, `AlignedToSamePlace`, `MirrorPickAxis`, `Beam`, `StructuralColumn`, `Isolated`, `StructuralFloor`, `Phases`, `Worksets`, `Filters`, `VisibilityOrGraphics`, `ScheduleOrQuantities`, `NewSheet`, `AlignedDimension`, `TagByCategory`, `ExportIFC`, `ProjectBrowser`, **`Numbering`** (new). 2025 also has `StructuralRebar`, `RebarLine`, `InsertCoupler`, `ReinforcementNumbers`; the 2027 fetch did **not** list `StructuralRebar` / `InsertCoupler` / `ReinforcementNumbers` → each JSON row carries a **candidate list**, resolved by `getattr` at runtime (§3.7). No postable command for Edit Assembly / Disassemble / Create Views. | rvtdocs.com/2025 + /2027 PostableCommand |
| `RevitCommandId` | `LookupPostableCommandId(PostableCommand)`, `LookupCommandId(string)`, `Name` (non-localised id string such as `ID_…`), `CanHaveBinding`; `UIApplication.PostCommand(RevitCommandId)`, `CanPostCommand(RevitCommandId)`. | rvtdocs.com/2025 RevitCommandId |
| `UnitUtils.ConvertFromInternalUnits(value, UnitTypeId.Millimeters)` / `ConvertToInternalUnits` | ForgeTypeId units exist on all of 2022–2027. | (API) |

### 2.3 Revit 2027 changes that alter the roadmap `[CITED]`

Sources: Autodesk "What's New in Revit 2027" <https://help.autodesk.com/cloudhelp/2027/ENU/Revit-WhatsNew/files/GUID-C81929D7-02CB-4BF7-A637-9B98EC9EB38B.htm>;
Revit 2027 API WhatsNew <https://revapidocs.com/2027/WhatsNew.htm>; Autodesk Platform blog "Revit 2027 SDK: .NET 10 Migration"
<https://blog.autodesk.io/revit-2027-sdk-net-10-api-changes-and-additions/>; Symetri "What's New in Revit Structure 2027".

| Change | Effect on this toolkit |
|---|---|
| Revit 2027 runs on **.NET 10**. | D6. Nothing in the code depends on the .NET minor version; keep CLAUDE.md §7 reload rules. |
| **Rule-based numbering** replaces "Reinforcement Numbering": numbering schemas with templates, filters and *partitioning parameters*; `Rebar Number` is an editable parameter; new `PostableCommand.Numbering`. | Partition by rule (CastUnit page 3) keeps writing `NUMBER_PARTITION_PARAM` and adds the 2027 warning of D15. Tekla Bridge row "Numbering settings / renumber / remove gaps" posts `Numbering` on 2027 and `ReinforcementNumbers` before. Rebar Check "duplicate number" reads `Rebar Number` by name. |
| **Automatic rebar mass**: `Rebar.Mass`, `RebarBarType.BarMassPerUnitLength`. | D13. Roadmap §2.5 "Weight report" is now Layer 1 on 2027 (docs row says so); `T3_WeightPerMetre` template remains for ≤2026. |
| `RebarHookOrientation` removed; `CreateFromCurves` only with `BarTerminationsData`; `SetLayoutAsCustomSpacing` added. | D16. Wizard zone stirrups still use three `SetLayoutAsMaximumSpacing` sets (works 2022–2027). |
| 3D placement of shape-driven rebar, 3D path distribution, longitudinal-from-transverse, dedicated **Concrete Detailing** tab. | Ribbon paths in `tekla_bridge.json` carry `ribbon_2026` (Structure → Reinforcement) **and** `ribbon_2027` (Concrete Detailing → …) `[NV]`; Bridge shows the one matching `get_revit_version()`. |
| No sign of auto-reinforcement generators for beam/column/footing (G6), of assembly-drawing propagation (G7) or of BVBS export (G9) in 2027 What's New. | Keep Rebar Wizard, Clone Drawing, BVBS Export. Spike still confirms G6/G7/G9 by ribbon inspection. |

### 2.4 Tekla default shortcuts used by D4 `[CITED]`

Source: Tekla Structures 2026 "Default keyboard shortcuts" <https://support.tekla.com/doc/tekla-structures/2026/gen_keyboard_shortcuts>.
Only rows with a one-to-one Revit command get a comment in the template: `Ctrl+H` Open Phase manager → `Phases`;
`Ctrl+B` Create report → `ScheduleOrQuantities`; `Ctrl+G` Selection filters → `Filters`; `Shift+I` Inquire object →
Properties palette (no postable command → comment only); `Ctrl+Q` Quick Launch → (Revit has no equivalent; Tekla Bridge
itself); `Ctrl+Shift+C` Keyboard shortcuts dialog → `KeyboardShortcuts`; `Ctrl+J` AutoConnections → none; `Alt+Q/W/E`
rebar selection switches → none (Revit uses selection filters). All others stay out.

---

## 3 · Shared foundation

Common conventions for every Snippets module in this spec:

- Pure functions take/return plain Python (`float` mm, tuples, dicts, small record classes) and never import Revit at module level. Revit-touching functions import `Autodesk.Revit.DB` **inside the function** (so `dev/test_*.py` can `exec` the module with the stub loader from `dev/test_group_manager.py`).
- Lengths crossing the Revit boundary are converted once: `_compat.to_mm(feet)` / `_compat.to_feet(mm)`.
- Every Revit-touching function catches `Exception` per *item*, never per *batch*, and returns a `Result` row (`name, status in {"ok","skipped","failed"}, count, detail`) in the style of `family_transfer.Result`. Batches raise only on transaction-group failure.
- `progress(index, total, label)` callbacks return `False` to stop (ties into `T3WPFWindow.step_progress` which returns `not is_cancelled`). Pending items after a stop are reported `skipped: stopped`.
- Error text for users: `_compat.short_error(exc)` = first line of `exc.Message` or `str(exc)`. Messages follow *what · where · next* (§4.4).
- `__author__ = "Tran Tien Thanh"` module docstrings in the house style; no `print()`.

### 3.1 `lib/Snippets/_compat.py` — additions (owner: WP1)

Append; do not change existing helpers.

```python
def to_mm(feet):            # [PURE-ish] float feet -> float mm (×304.8); no Revit import
def to_feet(mm):            # [PURE-ish]
def short_error(exc):       # [PURE] first line of exc.Message / str(exc) / class name
def revit_year(doc=None):   # [REVIT] Snippets._host.get_revit_version(doc); never raises
def pick_enum(enum_type, candidates):
    """[REVIT] First member of `enum_type` whose name is in `candidates` (list of str), else None.
    Used for PostableCommand / RebarTerminationOrientation names that changed between releases."""
def postable_command_id(candidates):
    """[REVIT] RevitCommandId for the first PostableCommand name in `candidates` that exists on this
    release, or None. Wraps RevitCommandId.LookupPostableCommandId; swallows exceptions."""
def bar_nominal_diameter_mm(bar_type):      # [REVIT] to_mm(bar_type.BarNominalDiameter)
def bar_mass_per_metre(bar_type):
    """[REVIT] kg/m from RebarBarType.BarMassPerUnitLength (2027+, converted with
    UnitUtils.ConvertFromInternalUnits(v, UnitTypeId.KilogramsPerMeter) — [NV] internal unit),
    else from the 'T3_WeightPerMetre' parameter, else None. Returns (value, source) with
    source in {"revit", "T3_WeightPerMetre", None}."""
def partition_parameter(element):
    """[REVIT] element.get_Parameter(BuiltInParameter.NUMBER_PARTITION_PARAM) or
    element.LookupParameter('Partition'); None when absent or read-only."""
def rebar_number_text(element):
    """[REVIT] 'Rebar Number' as text: BuiltInParameter.REBAR_NUMBER when the enum has it
    (getattr guard), else LookupParameter('Rebar Number'); '' when unset."""
def numbering_partitions_by_partition_param(doc):
    """[REVIT] 2027+: True/False whether any enabled NumberingSchema whose scope includes OST_Rebar
    lists NUMBER_PARTITION_PARAM among GetPartitioningParameters(); None on ≤2026 or on any error.
    [NV] NumberingParameter member names (expects .ParameterId or .Id — probe both with getattr)."""
def create_rebar_from_curves(doc, style, bar_type, host, normal, curves,
                             hook_start=None, hook_end=None,
                             orient_start="Left", orient_end="Left",
                             use_existing_shape=True, create_new_shape=False):
    """[REVIT] One call that works on 2022–2027 (D16).
    1. try: data = BarTerminationsData(doc); data.HookTypeIdAtStart = hook_start.Id or InvalidElementId; …
            data.TerminationOrientationAtStart = pick_enum(RebarTerminationOrientation, [orient_start]);
            return Rebar.CreateFromCurves(doc, style, bar_type, host, normal, net_list(Curve, curves), data,
                                          use_existing_shape, create_new_shape)
       except (AttributeError, TypeError, MissingMemberException): fall through   # ≤2025: no BarTerminationsData
    2. legacy = Rebar.CreateFromCurves(doc, style, bar_type, hook_start, hook_end, host, normal,
               net_list(Curve, curves), RebarHookOrientation.<orient_start>, RebarHookOrientation.<orient_end>,
               use_existing_shape, create_new_shape)        # resolved with getattr so 2027 does not fail at import
    Raises the last exception with short_error() text when both fail."""
def add_to_assembly_of(doc, host, new_ids):
    """[REVIT] If host.AssemblyInstanceId is valid: doc.GetElement(it).AddMemberIds(net_list(ElementId, new_ids)).
    Returns (added_count, assembly_id_or_None, error_text_or_None). Must be called inside the caller's transaction (A2)."""
```

`audit_revit_compat` rules the coordinator adds (§7.0): `RebarHookOrientation` type removed=2027, `BarTerminationsData` type added=2026, member `Mass` receiver `rebar` added=2027, member `BarMassPerUnitLength` added=2027, type `NumberingSchemaType` removed=2027, member `SetLayoutAsCustomSpacing` added=2027.

### 3.2 `lib/Snippets/_assembly.py` (owner: WP1)

```python
# ── records [PURE] ───────────────────────────────────────────────────────
class HostRecord(object):   # id:int, name:str, category:str, level:str, workset:str, type_name:str,
                            # assembly_id:int(-1 none), in_group:bool, is_link:bool, is_valid_host:bool
class RebarRecord(object):  # id:int, kind:str in {"Rebar","RebarInSystem","AreaReinforcement","PathReinforcement",
                            #   "FabricSheet","FabricArea","RebarCoupler"}, host_id:int, assembly_id:int,
                            #   partition:str, number:str, mark:str, bar_type:str, diameter_mm:float,
                            #   quantity:int, shape:str, is_shape_driven:bool, system_id:int(-1)
class AssemblyRecord(object): # id:int, type_id:int, mark:str (AssemblyTypeName), naming_category:str,
                            #   instances:int (same type), members:list[int], rebar_ids:list[int],
                            #   view_ids:list[int], sheet_ids:list[int], level:str, center:(x,y,z) ft
class Result(object):       # name, status, count, detail   (copy of family_transfer.Result)

SKIP_IN_GROUP = "in group"; SKIP_IN_LINK = "from link"; SKIP_IN_ASSEMBLY = "already in assembly %s"
SKIP_NOT_VALID = "not valid for assembly"; SKIP_NO_HOST = "host missing"; SKIP_STOPPED = "stopped"

def filter_assembly_candidates(records, allow_assembly_id=-1):
    """[PURE] A5: split records into (ok, [(record, reason)]) — reason from SKIP_* for in_group, is_link,
    assembly_id not in (-1, allow_assembly_id)."""
def plan_batch_create(hosts, rebar_by_host, one_per_host=True, include_rebar=True):
    """[PURE] -> list of BatchPlan(host_ids:list[int], rebar_ids:list[int], naming_host_id:int, skips:[(id, reason)])
    one_per_host → one plan per host; else one plan with all hosts (naming_host = first)."""
def series_names(count, prefix, start, step, digits):
    """[PURE] ['C-001','C-002',…]; digits=0 → no padding."""
def diff_type_split(before, after):
    """[PURE] before/after: {assembly_id: type_id}. Returns (split_count, merged_count, changed_ids) — A4 report."""
def expand_selection(records_by_id, selected_ids, assembly_members):
    """[PURE] A1: selected AssemblyInstance ids expand to their members; returns ordered unique host ids."""

# ── collectors [REVIT] ───────────────────────────────────────────────────
def collect_assemblies(doc):                 # -> list[AssemblyRecord], one per instance; type instance counts filled
def collect_assembly_views(doc):             # -> {assembly_id: ([view_ids], [sheet_ids])} via View.AssociatedAssemblyInstanceId (A7)
def collect_hosts(doc, category_ids=None, level_id=None, workset_id=None, type_ids=None):
                                             # -> list[HostRecord] for OST_StructuralFraming/Columns/Foundation/Floors/Walls (valid rebar hosts only)
def host_records_from_selection(doc, uidoc): # A1: selection may contain hosts, rebar (→ their host), AssemblyInstance (→ members)
def level_name_of(doc, element):             # Level / Reference Level / Base Level / Schedule Level, "" when none
def workset_name_of(doc, element)

# ── mutations [REVIT] (each inside the caller's TransactionGroup; one Transaction per plan) ──
def batch_create(doc, plans, progress=None, prefix="", start=1, step=1, digits=3):
    """For each BatchPlan: Transaction 'Create assembly' → AssemblyInstance.Create(doc, net_list(ElementId, ids),
    naming_category_id) → Commit; if prefix: Transaction 'Name assembly' → inst.AssemblyTypeName = series name →
    Commit (type split/merge is Revit's call — report with diff_type_split). Returns [Result] (name = naming host,
    count = members)."""
def sync_rebar(doc, assembly_ids, rebar_index, progress=None):
    """For each assembly: hosts = members that RebarHostData.IsValidHost; candidates = rebar_index rows with
    host_id in hosts and assembly_id == -1 and not in group/link; AreElementsValidForAssembly(doc, ids, asm.Id)
    gate; Transaction 'Sync rebar into <mark>' → AddMemberIds. Returns [Result] (count = added)."""
def rename_series(doc, type_ids_in_order, prefix, start, step, digits):
    """One Transaction 'Rename assembly series': set AssemblyTypeName once per TYPE (first instance of each type).
    Returns [Result] + diff_type_split report."""
def select_in_revit(uidoc, ids)              # Selection.SetElementIds(net_list(ElementId, …)); returns count
def transform_between(src_asm, dst_asm):     # dst.GetTransform() * src.GetTransform().Inverse  (model→model)
```

`rebar_index`: the host→rebar map built **once per window open** by `_rebar.build_rebar_index(doc)` (roadmap §9 perf).

### 3.3 `lib/Snippets/_rebar.py` (owner: WP1)

```python
REBAR_KINDS = ("Rebar", "RebarInSystem", "AreaReinforcement", "PathReinforcement", "FabricSheet", "FabricArea", "RebarCoupler")
PARTITION_TOKENS = ("{AssemblyMark}", "{Level}", "{HostType}", "{HostMark}", "{Workset}", "{Category}")

# [PURE]
def render_partition(rule, ctx):
    """ctx: dict with keys AssemblyMark, Level, HostType, HostMark, Workset, Category (str, may be '').
    Replaces tokens; collapses '--'/leading/trailing separators; returns '' if every token resolved empty.
    Unknown tokens are left verbatim. Max 64 chars (Revit partition name limit unknown [NV] → warn >64)."""
def fingerprint(record, local_center, tol_mm=10.0):
    """(kind, shape, bar_type, quantity, round(x/tol), round(y/tol), round(z/tol)) — used by Clone Drawing T3.
    local_center is the element centre expressed in the ASSEMBLY's local frame (mm)."""
def match_by_fingerprint(src_items, dst_items, tol_mm=10.0, allow_mirror=True):
    """[(src, dst)] + unmatched_src + ambiguous; exact fingerprint first, then nearest within 2·tol on same
    (kind, shape, bar_type, quantity); mirror = also try x→-x and y→-y when allow_mirror."""
def centerline_to_legs(points, planar_tol_mm=1.0):
    """points: [(x,y,z) mm] of the sharp-corner centreline polyline (one bar).
    Returns LegChain(legs=[(length_mm, angle_deg_after)], planar=True) or LegChain(planar=False, reason=...).
    angle sign: + when the chain turns left about the plane normal (normal = first two legs' cross product);
    last angle = 0. Collinear consecutive points are merged; a 180° reversal is a hook (angle 180)."""
def outer_legs(legs, bar_d_mm):
    """D14: outer dimension of leg i = centreline length + Σ over its ≤2 adjacent bends of (d/2)·tan(|θ|/2),
    θ in degrees; a 180° hook uses tan(90°) → capped: + (d/2) per hook end (hook leg already is the outer
    length in BVBS examples). [NV] against Revit shape parameters (spike G12)."""
def legs_to_bvbs_segments(legs): # [(l, w)] → [(l_int, w_rounded_1dp)] with last w = 0
def classify_centerline(curves_info):
    """curves_info: [('Line', p0, p1) | ('Arc', p0, p1, pm)]; → 'planar_lines' | 'planar_with_arcs' | 'non_planar'."""

# [REVIT]
def build_rebar_index(doc, progress=None):
    """-> RebarIndex: .rows list[RebarRecord], .by_host {host_id: [RebarRecord]}, .by_id.
    Collects FilteredElementCollector(doc).OfClass(Rebar | RebarInSystem | AreaReinforcement | PathReinforcement |
    FabricSheet | FabricArea | RebarCoupler). Host of RebarInSystem = its system's host (SystemId); host of a
    coupler = host of the first coupled rebar (GetCoupledReinforcementData → [NV], try/except → host_id -1)."""
def centerline_points(rebar, position_index=0):
    """GetCenterlineCurves(False, False, True, MultiplanarOption.IncludeAllMultiplanarCurves, i) → list of
    ('Line'|'Arc', points mm). Uses GetTransformedCenterlineCurves when present (2024+?) [NV] else transforms
    by GetShapeDrivenAccessor().GetBarPositionTransform(i). RebarInSystem: 3-arg overload."""
def bar_type_of(doc, rebar)                  # RebarBarType via GetTypeId() (Rebar) / REBAR_BAR_TYPE param (RebarInSystem) [NV]
def mandrel_diameter_mm(bar_type, style)     # StirrupTieBendDiameter if stirrup else StandardBendDiameter (inner, mm)
def assign_partition(doc, element_ids, rule, context_fn, progress=None):
    """ONE Transaction 'Assign rebar partition': for each id: value = render_partition(rule, context_fn(id));
    param = _compat.partition_parameter(el); param.Set(value). Returns [Result] (ok / skipped: read-only /
    skipped: empty value / failed). Never touches Rebar Number (D15)."""
def partition_warning_2027(doc):             # D15 text or None, from _compat.numbering_partitions_by_partition_param
def local_center(element, assembly_transform) # bbox centre → inverse transform → (x,y,z) mm
```

### 3.4 `lib/Snippets/_drawing_clone.py` (owner: WP4)

```python
# [PURE]
class ViewSpec(object):   # kind in {"3d","section","elevation","partlist","takeoff","single_schedule","sheet"},
                          # orientation:str|None, template_id:int, scale:int, detail_level:str, name:str,
                          # crop_local:(min,max)|None, category_id:int(-1), title_block_type_id:int, sheet_number:str,
                          # viewports:[(view_name, (x,y) ft, rotation, label_offset)]
def classify_view(view_type, view_dir_local, cut_plane_local, has_crop):
    """→ AssemblyDetailViewOrientation name for ViewType.Section / Elevation using the direction in the SOURCE
    assembly's local frame: (0,0,-1) looking down → 'HorizontalDetail'; (0,+1,0) → 'DetailSectionA' (north);
    (-1,0,0) → 'DetailSectionB' (west); elevations: face hit by the cut plane → 'ElevationFront|Back|Left|Right|Top|Bottom'.
    [NV] spike G14 logs (ViewType, ViewDirection_local, name) of every source view to calibrate the table."""
def substitute_mark(text, src_mark, dst_mark): # 'C-01 Elevation Front' → 'C-02 Elevation Front'; falls back to suffix ' (dst_mark)'
def similarity(src, dst, tol_pct=5.0):
    """src/dst: dict(category, bbox_mm=(dx,dy,dz), rebar_count, member_count) → (score 0..100, [reasons]).
    100 = same category, bbox within tol, same rebar count; -40 different category; -30 bbox out of tol; -20 rebar count differs."""
def plan_clone(src_specs, dst_existing_specs, add_missing_only):
    """→ (to_create:[ViewSpec], skipped:[(ViewSpec, 'exists')])"""

# [REVIT]
def read_source(doc, src_asm):               # → [ViewSpec] from collect_assembly_views + view properties + sheets/viewports
def create_views(doc, dst_asm, specs, progress=None):
    """T1 — Transaction 'Clone views to <mark>': per spec → AssemblyViewUtils.Create…(doc, dst.Id, …template, isAssigned=True) →
    copy Scale/DetailLevel/DisplayStyle/CropBoxActive/CropBoxVisible/CropBox (local→model) when not template-locked;
    rename with substitute_mark; sheets: CreateSheet(doc, dst.Id, title_block_type_id) → SheetNumber via pattern →
    Viewport.Create(doc, sheet.Id, new_view.Id, center) if CanAddViewToSheet → ChangeTypeId(viewport type) → LabelOffset/Rotation.
    Returns ({src_view_id: new_view_id}, [Result])."""
def copy_annotations(doc, src_view, dst_view, transform, progress=None):
    """T2 — ids = view-owned (OwnerViewId == src_view.Id) elements of categories OST_TextNotes, OST_Lines (detail),
    OST_DetailComponents, OST_FilledRegion, OST_GenericAnnotation, OST_InsulationLines, OST_RevisionClouds? (no) —
    excluding OST_RebarTags, OST_MultiReferenceAnnotations, OST_Dimensions, OST_SpotElevations and anything with
    IndependentTag/Dimension class. SubTransaction per view: ElementTransformUtils.CopyElements(src_view, ids, dst_view,
    transform, CopyPasteOptions()). [NV] G2: transform = transform_between(src, dst) first; if the spike shows CopyElements
    expects identity for same-orientation assembly views, swap to Transform.Identity — keep the choice in ONE constant
    T2_TRANSFORM_MODE = 'delta' | 'identity'."""
def recreate_references(doc, src_view, dst_view, pairs, src_asm, dst_asm, progress=None):
    """T3 (D17) — tags: for each IndependentTag in src_view: local ids → matched dst element → IndependentTag.Create(doc,
    tag.GetTypeId(), dst_view.Id, Reference(dst_el), tag.HasLeader, tag.TagOrientation, T(tag.TagHeadPosition));
    LeaderEndCondition copied. Dimensions: for each Dimension in src_view whose every Reference is a FACE of a matched
    host element → match face by (normal_local, distance_to_center_local) within 5 mm → doc.Create.NewDimension(dst_view,
    T(dim.Curve) as Line, ReferenceArray, dim.DimensionType). Everything else → unmatched with reason.
    Returns Tally(tags_ok, tags_unmatched, dims_ok, dims_unmatched, [(src_id, reason)])."""
```

### 3.5 `lib/Snippets/_bvbs.py` (existing; owner: WP6) — changes made now + remaining work

Done in this commit (spec author): header field `m` removed (BF2D has none, §2.1); docstring states the checksum rule
as **confirmed** with fixture #1; `REFERENCE_LINE_1` constant = fixture 1 added (verified: `write_block` reproduces
fixtures 1 and 3 byte-for-byte, `verify_block(REFERENCE_LINE_1)` is True). WP6 keeps the public API as is and uses
`REFERENCE_LINE_1` in `dev/test_bvbs_writer.py` and in the BVBS dialog's self-test at open (`verify_block(REFERENCE_LINE_1)`
must be `True`, else the window refuses to export with message E-BVBS-01). Optional for WP6: `r` (arc) segments as a
third tuple element `(length_or_radius, angle, is_arc)` so fixtures 4/5 pass — not required for V1.

Field semantics the dialog must feed: `project` = user field (default `doc.ProjectInformation.Number`), `plan` = user field
(default assembly mark or "MODEL"), `index` = revision field, `mark` = `<partition>-<number>` or ScheduleMark when the number is
empty, `quantity` = `Rebar.Quantity` (whole set when all positions identical; else one record per *distinct* leg chain with its
own count), `weight_kg` = kg/m × length_m per bar, `grade` = bar type `Material` name or user default, `roll_diameter_mm` = mandrel
Ø from `_rebar.mandrel_diameter_mm`.

### 3.6 `lib/Snippets/_rebar_wizard.py` (owner: WP7)

```python
# [PURE] — all mm, local frame: X along member axis (beam) / up (column), Y = width b, Z = depth h (beam) ; footing: X,Y plan, Z up
class Section(object):      # b, h, length, kind in {"beam","column","footing"}, rectangular:bool, reason:str
class BarPlan(object):      # label, style in {"Standard","StirrupTie"}, bar_type_name, hook_start, hook_end,
                            # points:[(x,y,z)] local polyline (centreline, sharp corners), normal:(x,y,z),
                            # layout: ("single",) | ("fixed", n, array_len) | ("max_spacing", s, array_len) ,
                            # bars_on_normal_side:bool, zone:str
class WizardInput(object):  # per tab fields (§5.6) as plain attributes; .validate() → [str]

def beam_plan(section, inp):      # bottom bars (n_bot, Ø), top bars (n_top, Ø), stirrups 3 zones (Ø, s_end, L_end, s_mid), cover
def column_plan(section, inp):    # verticals: 4 corners + n_side per face; ties: outer tie 3 zones (dense top/bottom L_dense, s_dense, s_mid); optional cross tie when n_side>=1
def footing_plan(section, inp):   # bottom mesh: X bars Ø@s over width minus 2·cover, Y bars Ø@s; straight bars with optional 90° end hooks
def stirrup_points(b, h, cover, d_stirrup, hook_len):  # closed rectangle centreline, 4 legs + 135° hooks (BarTerminationsData hook types), origin corner
def zone_lengths(total, l_end, min_mid=200.0)         # → (end1, mid, end2); when 2·l_end >= total → single zone
def summarize(plans):              # "4 × Ø20 bottom · 2 × Ø16 top · stirrups Ø8 @100 (2×600) / @150 — 3 sets" (English)
def preset_io(path)                # load/save %APPDATA%\T3LabAI\rebar_wizard_presets.json {name: WizardInput dict}

# [REVIT]
def read_section(doc, element):
    """FamilyInstance framing: axis = Location.Curve (must be Line else reason 'curved beam'); frame from HandOrientation/
    FacingOrientation; b,h from the solid's extents in that frame; rectangular := |volume − b·h·L| ≤ 2 % (else reason
    'section is not rectangular'). Column: axis vertical from Location.Point + height from bbox; footing: bbox extents.
    Elements that are not FamilyInstance / not OST_StructuralFraming|Columns|Foundation → reason 'not a beam, column or
    isolated footing'. Walls/slabs → 'use Area Reinforcement (Revit)'."""
def resolve_types(doc)             # {diameter_mm: RebarBarType}, {name: RebarHookType}, by sorted nominal Ø
def create_plans(doc, host, plans, types, progress=None):
    """Transaction 'Rebar Wizard: <host name>': for each BarPlan → model curves = host transform × local points →
    _compat.create_rebar_from_curves(doc, style, bar_type, host, normal, curves, hooks…) → accessor.SetLayoutAs…;
    collect ids → _compat.add_to_assembly_of(doc, host, ids) (A2) → Commit. Returns [Result] per plan + assembly note."""
```

### 3.7 `lib/Snippets/_tekla_bridge.py` (owner: WP2)

```python
DATA_FILE = <lib>/data/tekla_bridge.json
GROUPS = [("model", "Model & cast unit"), ("rebar", "Rebar"), ("numbering", "Numbering"),
          ("drawing", "Drawings"), ("report", "Reports & export"), ("env", "Environment & habits")]
TOOL_ENTRY = {"CastUnit": ("GUI.CastUnitDialog", "show_cast_unit"), "CloneDrawing": ("GUI.CloneDrawingDialog", "show_clone_drawing"),
              "RebarCheck": ("GUI.RebarCheckDialog", "show_rebar_check"), "BVBSExport": ("GUI.BVBSExportDialog", "show_bvbs_export"),
              "RebarWizard": ("GUI.RebarWizardDialog", "show_rebar_wizard")}

# [PURE]
def load_rows(path=DATA_FILE)             # → [dict] validated by validate_rows (raises ValueError listing bad rows)
def validate_rows(rows)                   # schema §3.8; unique ids; layer in {1,2,3}; layer 2 ⇒ tool set; postable list of str
def search(rows, text, group=None)        # case-insensitive over tekla, revit, keywords; group filter; stable order by group then order
def ribbon_path_for(row, year)            # row['ribbon_2027'] if year >= 2027 and present else row['ribbon_2026']
def seen_tips_path()                      # user_data_path('tekla_bridge.json')
def load_seen(path) / mark_seen(path, row_id)

# [REVIT]
def post_revit_command(uiapp, row):
    """→ ("posted", name) | ("no_command", ribbon_path) | ("cannot_post", reason). Uses _compat.postable_command_id(row['postable'])
    then uiapp.CanPostCommand(cmd) → uiapp.PostCommand(cmd). Must be called AFTER the Bridge window has closed (PostCommand runs
    when the current pyRevit command returns)."""
def run_tool(row, doc)                    # importlib TOOL_ENTRY → function(doc); returns error text or None
```

### 3.8 `lib/data/tekla_bridge.json` — schema + full content (owner: WP2)

Schema (every row):

```json
{"id": "model.part", "group": "model", "order": 10,
 "tekla": "Create part (beam, column, slab, footing)",
 "revit": "Structural Framing / Column / Floor / Foundation",
 "layer": 1,                               // 1 Revit has it · 2 T3Lab tool · 3 Revit does it differently (better)
 "postable": ["Beam", "StructuralColumn"], // PostableCommand candidate names, first found wins; [] = none
 "tool": null,                             // "CastUnit" | "CloneDrawing" | "RebarCheck" | "BVBSExport" | "RebarWizard" | null
 "ribbon_2026": "Structure › Structure › Beam / Column / Floor / Foundation",
 "ribbon_2027": "Structure › Structure › Beam / Column / Floor / Foundation",   // [NV] — null = same as 2026
 "tip_en": "Part = family instance; a Tekla profile is a Family Type.",
 "tip_vi": "Part = family instance; profile của Tekla là Family Type.",
 "doc_en": "…one paragraph for docs…", "doc_vi": "…",
 "keywords": ["beam","column","part","profile"],
 "tekla_shortcut": null,                   // documented Tekla default (§2.4) or null — never invented
 "revit_command_id": null}                 // RevitCommandId.Name filled from spike G10 log; null until then
```

Full content (`order` ascending inside each group; `doc_*` may equal `tip_*` when nothing more is needed; `ribbon_2027`
null unless stated). Builder writes these verbatim, adding `doc_vi` as a faithful Vietnamese rendering of `doc_en`:

| id | group | tekla | revit | layer | postable | tool | ribbon_2026 | ribbon_2027 | tip_en | tip_vi | tekla_shortcut |
|---|---|---|---|---|---|---|---|---|---|---|---|
| model.part | model | Create part (beam, column, slab, footing) | Structural Framing / Column / Floor / Foundation | 1 | ["Beam","StructuralColumn","Isolated","StructuralFloor"] | — | Structure › Structure › Beam · Column · Floor · Foundation | null | Part = family instance; a Tekla profile is a Family Type. | Part = family instance; profile của Tekla là Family Type. | — |
| model.castunit | model | Cast unit (in-situ / precast), main part | Assembly › Create Assembly (Naming Category = main part) | 1 | ["CreateAssembly"] | CastUnit | Modify › Create › Create Assembly (with elements selected) | null | Select the host and its rebar, then Create Assembly; use Cast Unit Manager to do this for many hosts and to pull in hosted rebar automatically. | Chọn host và rebar rồi Create Assembly; dùng Cast Unit Manager khi làm hàng loạt hoặc cần tự gom rebar. | — |
| model.castunit.edit | model | Add / remove part to cast unit | Modify Assembly › Edit Assembly | 1 | [] | CastUnit | Modify › Assembly › Edit Assembly (assembly selected) | null | Rebar added after the assembly was made is not a member until you add it — Cast Unit Manager › Sync rebar does that for every assembly at once. | Rebar thêm sau không tự vào assembly — Cast Unit Manager › Sync rebar làm việc đó cho mọi assembly. | — |
| model.castunit.numbering | model | Cast unit numbering (prefix + number, same shape = same number) | Assembly type name = mark; identical assemblies share one type | 3 | [] | CastUnit | Properties › Assembly Name (type) | null | Revit merges identical assemblies into one type automatically; Cast Unit Manager › Rename series sets prefix/start/step per type. | Revit tự gộp assembly giống hệt thành một type; Rename series đặt prefix/start/step theo type. | — |
| model.pour | model | Pour unit / pour break | Parts + Phases | 1 | ["Phases"] | — | Manage › Phasing › Phases; Modify › Create › Create Parts | null | Pours are modelled with Parts and Phases; outside this toolkit's scope. | Pour mô hình bằng Parts + Phases; ngoài phạm vi bộ tool. | Ctrl+H |
| model.organizer | model | Organizer (filter by property) | Project Browser organization + View Filters + Schedules | 3 | ["Filters","ProjectBrowser"] | — | View › Graphics › Filters; View › Windows › User Interface › Project Browser | null | Filters work per view and drive graphics too — stronger than Organizer for review views. | Filter theo từng view và điều khiển cả đồ hoạ — mạnh hơn Organizer khi soát. | Ctrl+G |
| rebar.single | rebar | Rebar (create rebar, polygon) | Structure › Rebar (sketch / place by shape / free form) | 1 | ["StructuralRebar","Rebar"] | — | Structure › Reinforcement › Rebar | Concrete Detailing › Reinforcement › Rebar | Shape-driven rebar = catalogue shape; Free Form = Tekla polygon rebar. | Shape-driven = shape catalog; Free Form = rebar polygon kiểu Tekla. | — |
| rebar.group | rebar | Rebar group (spacing / exact number) | Rebar Set + layout rule | 1 | ["StructuralRebar","Rebar"] | — | Structure › Reinforcement › Rebar, then Layout on the Options bar | Concrete Detailing › Reinforcement › Rebar | Layout rules Fixed Number / Maximum Spacing / Number with Spacing / Minimum Clear Spacing are the Tekla group options under other names. | Layout rule là tuỳ chọn group của Tekla dưới tên khác. | — |
| rebar.mesh | rebar | Rebar mesh | Fabric Sheet / Fabric Area | 1 | ["SingleFabricSheetPlacement","StructuralFabricArea"] | — | Structure › Reinforcement › Fabric Sheet · Fabric Area | Concrete Detailing › Reinforcement › Fabric | A single mesh is a Fabric Sheet; a meshed region is a Fabric Area. | Một tấm lưới = Fabric Sheet; vùng lưới = Fabric Area. | — |
| rebar.area | rebar | Area reinforcement (slab / wall) | Area Reinforcement · Path Reinforcement | 1 | ["StructuralAreaReinforcement","StructuralPathReinforcement"] | — | Structure › Reinforcement › Area · Path | Concrete Detailing › Reinforcement › Area · Path | Area = both directions over a region; Path = bars perpendicular to a sketched path. | Area = hai phương trên vùng; Path = thanh vuông góc với đường vẽ. | — |
| rebar.cover | rebar | Cover | Rebar Cover Settings + cover per host face | 1 | ["RebarCoverSettings"] | — | Structure › Reinforcement › Rebar Cover Settings | Concrete Detailing › Reinforcement › Cover | In Revit cover belongs to the host face, not to the bar. | Cover trong Revit là thuộc tính mặt host, không phải của thanh. | — |
| rebar.hook | rebar | Hook / bend radius | Rebar Hook Type · Rebar Bar Type bend diameter | 1 | [] | — | Properties › Rebar Hook Type; Project Browser › Families › Structural Rebar › Rebar Bar Type | null | Bend diameters live on the Bar Type; hooks are types you pick per bar end. | Đường kính uốn nằm ở Bar Type; hook là type chọn cho từng đầu thanh. | — |
| rebar.splice | rebar | Splice / coupler / end anchor | Rebar Coupler · lap by overlapping bars | 1 | ["InsertCoupler"] | — | Structure › Reinforcement › Coupler | Concrete Detailing › Reinforcement › Coupler | Couplers are a family; laps are modelled as overlapping bars. | Coupler là family; nối chồng mô hình bằng hai thanh chồng nhau. | — |
| rebar.component | rebar | System component (Beam 63, Column 83, Pad footing 77) | — (no Revit equivalent) | 2 | [] | RebarWizard | — | — | Rebar Wizard reinforces rectangular beams, columns and pad footings from a preset — V1 scope only. | Rebar Wizard đặt thép cho dầm, cột chữ nhật và móng đơn theo preset — phạm vi V1. | — |
| rebar.copy | rebar | Copy special → to another object / Mirror | Copy · Paste Aligned · Mirror (rebar re-hosts itself) | 1 | ["Copy","AlignedToSelectedViews","MirrorPickAxis"] | CastUnit | Modify › Clipboard › Paste › Aligned to…; Modify › Modify › Mirror | null | Pasted rebar takes the new host but NOT its assembly — run Cast Unit Manager › Sync rebar afterwards. | Rebar dán vào nhận host mới nhưng không vào assembly — chạy Sync rebar sau đó. | Ctrl+C |
| rebar.visibility | rebar | Rebar visibility / representation | View Visibility States · View Filters · Rebar set presentation | 1 | ["VisibilityOrGraphics"] | — | Properties (rebar) › View Visibility States; View › Graphics › Visibility/Graphics | null | Unobscured / Solid are set per view from the bar's properties; this is finer than Tekla's global representation. | Unobscured/Solid đặt theo từng view từ thuộc tính thanh — tinh hơn Tekla. | — |
| rebar.clash | rebar | Clash check (rebar–rebar, rebar–part) | Collaborate › Interference Check | 1 | ["RunInterferenceCheck"] | — | Collaborate › Coordinate › Interference Check › Run | null | Geometry clashes only; data problems (orphans, missing members) are Rebar Check's job. | Chỉ va chạm hình học; lỗi dữ liệu để Rebar Check. | — |
| numbering.series | numbering | Numbering series per part / assembly | Rebar Partition (numbering partition) | 3 | [] | CastUnit | Properties (rebar) › Partition | Manage › Numbering (2027 schema partitions) | Revit numbers automatically and continuously inside each partition — you only choose the partition. Cast Unit Manager › Partition by rule sets it from the assembly mark, level, host type or workset. | Revit đánh số tự động trong partition — chỉ cần gán partition đúng; Partition by rule làm theo quy tắc. | — |
| numbering.settings | numbering | Numbering settings / renumber / remove gaps | Reinforcement Numbering (≤2026) · Numbering (2027) | 1 | ["Numbering","ReinforcementNumbers"] | — | Structure › Reinforcement › Reinforcement Numbering | Manage › Numbering | 2027 uses rule-based numbering schemas: templates, filters and partition parameters live in one dialog. | 2027 dùng numbering schema theo luật: template, filter, partition trong một hộp thoại. | — |
| numbering.check | numbering | Check duplicate / missing numbers | — | 2 | [] | RebarCheck | — | — | Rebar Check lists duplicate numbers with different geometry and bars without a partition. | Rebar Check liệt kê số trùng khác hình và thanh chưa có partition. | — |
| drawing.castunit | drawing | Cast unit drawing (view set + sheet) | Assembly › Create Views (view template + title block) | 1 | [] | — | Modify › Assembly › Create Views (assembly selected) | null | Create Views makes 3D, plan, elevations, sections, part list, material take-off and the sheet in one go. | Create Views tạo 3D, mặt bằng, mặt đứng, mặt cắt, part list, take-off và sheet một lần. | — |
| drawing.clone | drawing | Clone drawing | — | 2 | [] | CloneDrawing | — | — | Clone Drawing copies the finished drawing of one assembly to similar assemblies: views, sheet, free annotations, and re-creates tags/dimensions it can match. | Clone Drawing nhân bản bản vẽ mẫu sang assembly tương tự. | — |
| drawing.ga | drawing | GA drawing | Sheet + ordinary views | 1 | ["NewSheet"] | — | View › Sheet Composition › Sheet | null | Outside this toolkit — standard Revit sheets. | Ngoài phạm vi — sheet Revit thường. | — |
| drawing.marks | drawing | Rebar marks / pull-out picture / dimension | Rebar Tag · Multi-Rebar Annotation · Rebar Bending Detail · Dimension | 1 | ["TagByCategory","AlignedMultiRebarAnnotation","RebarBendingDetail","AlignedDimension"] | — | Annotate › Tag; Annotate › Multi-Rebar; Structure › Reinforcement › Bending Detail | Concrete Detailing › Annotation | Bending Detail (2023+) is the pull-out picture; Multi-Rebar Annotation tags a set with one leader and dimension. | Bending Detail là pull-out picture; Multi-Rebar Annotation gắn tag + dim cho cả set. | — |
| drawing.list | drawing | Drawing list | Sheet List schedule · Cast Unit Manager Views/Sheets columns | 1 | ["ScheduleOrQuantities"] | CastUnit | View › Create › Schedules › Sheet List | null | Cast Unit Manager › Manage shows which assemblies still have no views or sheet. | Manage của Cast Unit Manager cho thấy assembly nào chưa có view/sheet. | Ctrl+B |
| drawing.uptodate | drawing | Drawing not up-to-date flag | — (views are always live) | 3 | [] | — | — | — | There is no "update drawing" in Revit: every view reads the model live. | Revit không có "update drawing": view luôn sống với model. | — |
| report.bbs | report | Bending schedule / rebar list | Rebar Schedule (Shape, A–F, Total Bar Length, Quantity) + Bending Detail | 1 | ["ScheduleOrQuantities"] | — | View › Create › Schedules › Schedule/Quantities › Structural Rebar | null | Add Shape, Bar Diameter, Quantity, Total Bar Length and the shape parameters A–F as fields. | Thêm field Shape, Bar Diameter, Quantity, Total Bar Length và tham số A–F. | — |
| report.weight | report | Weight report by diameter | Rebar Mass (2027) · T3_WeightPerMetre calculated field (≤2026) | 1 | ["ScheduleOrQuantities"] | — | View › Create › Schedules | null | 2027 computes mass natively; before 2027 add the shared parameter T3_WeightPerMetre to the bar type and a calculated field — template in the docs. | 2027 có Mass sẵn; trước đó dùng T3_WeightPerMetre + calculated field. | — |
| report.castunitlist | report | Cast unit list | Assembly schedule · Part List · Material Takeoff | 1 | ["ScheduleOrQuantities"] | — | View › Create › Schedules › Assemblies | null | Assembly schedules list marks and instance counts. | Schedule assembly liệt kê mark và số instance. | — |
| report.bvbs | report | BVBS (.abs) for bending machines | — | 2 | [] | BVBSExport | — | — | BVBS Export writes BF2D blocks with a verified checksum; 3D bars are reported, not exported. | BVBS Export ghi BF2D có checksum đã kiểm; thanh 3D được báo, không xuất. | — |
| report.ifc | report | IFC | Export IFC | 1 | ["ExportIFC"] | — | File › Export › IFC | null | Use the IFC-SG panel for Singapore deliverables. | Dùng panel IFC-SG cho hồ sơ Singapore. | — |
| report.unitechnik | report | Unitechnik / PXML | — | 3 | [] | — | — | — | Not available in V1; noted as a request. | Chưa có trong V1; đã ghi nhận nhu cầu. | — |
| env.shortcuts | env | Tekla shortcuts | Keyboard Shortcuts (import XML) | 1 | ["KeyboardShortcuts"] | — | View › Windows › User Interface › Keyboard Shortcuts | null | Import the T3Lab template and fill the keys you want — Revit shortcuts are typed sequences, Tekla's are chords. | Import template T3Lab rồi điền phím — phím Revit là chuỗi gõ, Tekla là tổ hợp. | Ctrl+Shift+C |
| env.find | env | Find a command by Tekla name | — | 2 | [] | — | — | — | This window. Type the Tekla name and press Enter. | Chính cửa sổ này. | Ctrl+Q |
| env.attributes | env | Attribute file (saved component settings) | Rebar Wizard presets · View Template · Type | 3 | [] | RebarWizard | — | — | Presets are JSON under %APPDATA%\T3LabAI; view templates and types are the Revit way. | Preset là JSON trong %APPDATA%\T3LabAI; view template và type là cách của Revit. | — |
| env.phase | env | Phase manager | Phases / Worksets | 1 | ["Phases","Worksets"] | — | Manage › Phasing › Phases; Collaborate › Worksets | null | Phases are time; worksets are ownership — Tekla's phases are usually worksets here. | Phase là thời gian; workset là quyền sở hữu — phase Tekla thường là workset. | Ctrl+H |

Rows with `tool` AND `layer == 1` (model.castunit, model.castunit.edit, rebar.copy, drawing.list) show **two** actions in the
Bridge: *Open in Revit* (primary) and *Open T3Lab tool* (secondary).

### 3.9 `lib/data/KeyboardShortcuts_Tekla.xml` (generated; owner: WP2 via `build_tekla_docs.py`)

Generated, never hand-edited. Shape `[NV]` (forum-reported format; Autodesk only documents the dialog):

```xml
<?xml version="1.0" encoding="utf-8"?>
<!-- GENERATED by dev/build_tekla_docs.py from lib/data/tekla_bridge.json - do not edit.
     Fill the Shortcuts="" values you want, then Revit: View > User Interface > Keyboard Shortcuts > Import. -->
<Shortcuts>
  <!-- Tekla: Open Phase manager (Tekla default Ctrl+H) -> Revit: Phases -->
  <ShortcutItem CommandName="Phases" CommandId="ID_…(from revit_command_id)" Shortcuts="" Paths="Manage>Phasing"/>
  <!-- Tekla: Create report (Ctrl+B) -> Revit: Schedule/Quantities — no command id captured yet: run dev/debug/spike_rebar_assembly.py -->
</Shortcuts>
```

Rows with `revit_command_id == null` are emitted as a comment only. The docs page §"Shortcuts" (generated) carries the
Tekla default key table from §2.4 with a "Revit equivalent / clash" column.

### 3.10 `dev/build_tekla_docs.py` (owner: WP2)

```
python3 dev/build_tekla_docs.py            # writes docs/tekla-to-revit-2027.md + lib/data/KeyboardShortcuts_Tekla.xml
python3 dev/build_tekla_docs.py --check    # exit 1 when either output differs from what the JSON would generate
```

Doc layout (bilingual, EN then VI per section): title + generation banner; §Three layers explanation; one table per
group with columns `Tekla · Revit 2027 · Layer · Where (ribbon 2026 / 2027) · What is different`; §Weight schedule
template (`T3_WeightPerMetre` steps, 2027 native note); §Shortcuts (D4); §Assembly rules A1–A8 in user words; §Known
limits (D2, D7, D17). Validation = `_tekla_bridge.validate_rows`. Add `dev/test_tekla_bridge.py` (§6).

### 3.11 `dev/debug/spike_rebar_assembly.py` (owner: WP1) — the Revit 2027 probe

pyRevit console script (`#! python3`, bootstrap, `resolve_doc`); standalone — imports only `_compat` / `_host`, not the new
Snippets, so it can run on day 1. Writes `%APPDATA%\T3LabAI\spike_rebar.log` (append, timestamped, one `G<n> PASS|FAIL|SKIP —
detail` line per probe) and shows a summary `show_info`. Model prerequisite (printed when missing): ≥2 identical column
cast units with rebar + 1 different one, one of them with views + sheet + a text note, a rebar tag and a dimension.

| Probe | What it does | Writes |
|---|---|---|
| G1 | For two instances of one AssemblyType: list views per instance via `AssociatedAssemblyInstanceId`; rename one instance's `AssemblyTypeName` in a Transaction, re-list, roll back. | whether views stay with the type / instance |
| G2 | `CopyElements(viewA, [text note], viewB, transform_between, opts)` with delta transform, roll back; repeat with `Transform.Identity`. | where the copy landed (local coords of both) → sets `T2_TRANSFORM_MODE` |
| G3 | Same with a rebar tag and a dimension; inspect `GetTaggedLocalElementIds()` / `References` of the copies. | kept / lost reference |
| G4 | `AreElementsValidForAssembly` + `AddMemberIds` for one each of RebarInSystem, AreaReinforcement, FabricSheet, RebarCoupler (roll back). | accepted kinds |
| G5 | Copy a rebar with `ElementTransformUtils.CopyElements(doc, ids, XYZ)` onto an assembled host, read `AssemblyInstanceId`, roll back. | auto-added or not |
| G6/G7/G9 | Reflection: list `PostableCommand` names containing `Rebar`, `Reinforc`, `Assembl`, `Number`, `BVBS`; print ribbon tab names via `UIApplication.GetRibbonPanels(tab)` for 'Concrete Detailing' / 'Structure' `[NV]`. | names → `tekla_bridge.json` `postable` fix-ups and `ribbon_2027` |
| G8 | `Create3DOrthographic`, `CreateDetailSection(HorizontalDetail)`, `CreatePartList`, `CreateSheet`, `Viewport.Create` on a test assembly inside one Transaction, roll back. | pass/fail + exception text |
| G10 | For every `postable` candidate of every JSON row: `getattr(PostableCommand, name)` → `LookupPostableCommandId` → `.Name`, `CanPostCommand`. | `revit_command_id` values (copy into JSON) |
| G11 | `NUMBER_PARTITION_PARAM` on a rebar: readable/writable; 2027: `NumberingSchema.GetSchemasInDocument`, each `GetPartitioningParameters()` and `dir(NumberingParameter)`. | partition API shape |
| G12 | For 3 shape-driven bars: `GetCenterlineCurves(False, False, True, …, 0)` points in mm + shape parameters (`rebar.LookupParameter('A'/'B'/'C'…)` as mm) + `BarNominalDiameter`. | calibrates `outer_legs` |
| G13 | Rename one AssemblyType with 2 instances by setting `AssemblyTypeName` on one instance; count types before/after; roll back. | A4 behaviour |
| G14 | For every assembly view of the sample: `ViewType`, `ViewDirection` and `Origin` in the assembly's local frame, `Name`, `ViewTemplateId`. | calibrates `classify_view` |
| G15 | `BarTerminationsData` present? `RebarHookOrientation` present? `Rebar.Mass`, `RebarBarType.BarMassPerUnitLength` value + `UnitUtils.ConvertFromInternalUnits(v, UnitTypeId.KilogramsPerMeter)`. | compat matrix + mass unit |
| G16 | `MultiReferenceAnnotationOptions`: `dir()` of the class. | decides whether D17 can be lifted in V2 |

---

## 4 · Panel-wide UI conventions

### 4.1 Window frame (identical in all six XAMLs)

Copy `ManaWorkset.xaml` 1–30 (Window attributes + `WindowChrome`) and 1715–1762 (outer Border, title bar with logo,
title/subtitle, `btn_minimize`/`btn_maximize`/`btn_close` with `IsCancel="True"`) and 2118–2136 (footer). Then run
`python3 dev/sync_t3_styles.py` to embed the stylesheet between the two `T3 STYLES` markers — never paste it by hand.
`Window.Resources` contains only that block (rule 17). On `<Window>` use `DynamicResource`. Sizes: **M** 560×420/560, **L**
1000×620 (`MinWidth/MinHeight` = same). Title text = tool name; subtitle = one line of purpose. Tooltips carry the Tekla term
in brackets: `ToolTip="Assembly (Tekla: cast unit)"`.

Footer (left → right): `T3.Copyright` · 1×16 rule · `Ellipse x:Name="dot_status" Style="T3.Dot"` · `StackPanel x:Name="progress_panel"`
(Collapsed; `ProgressBar x:Name="pb_run" Style="T3.ProgressBar" Width="160"`; `Button x:Name="btn_stop" Style="T3.Button.Ghost"
Click="stop_clicked"` with `T3.Icon.Lead` `&#xE71A;` + `Stop`) · `TextBlock x:Name="status_text" Style="T3.Body.Secondary"
TextTrimming="CharacterEllipsis" MaxWidth="520"`. Right: ≤2 `T3.Button.Secondary` then ONE `T3.Button.Primary` `IsDefault="True"`
whose label carries the count (`Create 12 assemblies`). Buttons in the footer set no `Height`/`Padding`; icon+label pattern =
`<TextBlock Text="&#x…;" Style="{StaticResource T3.Icon.Lead}"/><TextBlock Text="Label" VerticalAlignment="Center"/>` inside a
horizontal `StackPanel` as `Content`.

Status line copy: `Ready — 42 assemblies, 1 286 rebar` · running: `Creating assemblies — 3 / 12 · C-03` · done:
`Created 12 assemblies · 2 skipped (in group) · 0 failed` (dot `T3.Success.Accent` / `T3.Warning.Accent` / `T3.Danger.Accent`
**and** the words). Stop: `step_progress` returns False → finish the current item, mark the rest `skipped: stopped`.

Available style keys (the only ones `audit_t3` accepts): `T3.Ink T3.Text T3.TextSecondary T3.TextMuted T3.TextDisabled T3.Border
T3.BorderStrong T3.SurfaceSunken T3.Canvas T3.Surface T3.RowAlt T3.RowRule T3.Success.Fill/.Accent/.Text T3.Warning.Fill/.Accent/.Text
T3.Danger.Fill/.Accent/.Text/.Border T3.Copyright.Fg T3.Progress.Track/.Fill T3.Plot.1..8 T3.Plot.Off T3.Font T3.Font.Mono T3.Size.Display/
Title/Body/Caption/Label/Mono T3.H.Row/Control/Action/TitleBar/Footer T3.Pad.Panel/Field T3.R.Window/Control/Pill T3.Display T3.Title T3.Subtitle
T3.Body T3.Body.Secondary T3.BodyStrong T3.Caption T3.Label T3.Copyright T3.Mono T3.Empty T3.Cell.Mono T3.Cell.Number T3.Cell.Center T3.Rule
T3.Icon T3.Icon.Muted T3.Icon.Field T3.Icon.Lead T3.Icon.Lg T3.Callout.Icon T3.Icon.Rail T3.Button.Base/Primary/Secondary/Ghost/Danger T3.TextBox
T3.TextBox.Mono T3.ComboBoxItem T3.ComboBox T3.CheckBox T3.RadioButton T3.ProgressBar T3.ListBoxItem T3.ListBoxItem.Multiline T3.ListBox T3.LogBox
T3.DataGridColumnHeader T3.DataGridColumnHeader.Center T3.DataGridRow T3.DataGridCell T3.DataGrid T3.DataGridColumnHeader.Filter
T3.DataGridColumnHeader.Check T3.DataGridCell.Check T3.CheckBox.Cell T3.Filter.Item T3.GridViewColumnHeader(.Check) T3.ListViewItem T3.ListView
T3.StatusPill T3.Expander T3.TreeViewItem T3.TreeView T3.Callout T3.Callout.Success/Warning/Danger T3.FooterBar T3.TitleBar T3.Panel T3.WinCtrl
T3.WinClose T3.TabItem.Hidden T3.Rail.Tile T3.ComboBox.Toggle T3.ScrollBar.Thumb T3.ScrollBar.PageButton T3.Rail.Logo T3.Pill T3.Chip T3.Search
T3.ListHeader T3.ListHeader.Label T3.Meter T3.Cell.Muted T3.Dot T3.Log.Time/Ok/Skipped/Failed/Plain T3.Tally`.

### 4.2 Glyphs (no additions needed)

From the standard tables: `E721` Search · `E8BB` Close · `E921`/`E922` Min/Max · `E71A` Stop · `E72C` Refresh · `E710` Add ·
`E73E` Check · `E7B3` Isolate · `E7C9` Pick · `E896` Export · `E8E5` OpenFile · `E74E` Save · `E74D` Delete · `E946` Info ·
`E7BA` Warning · `E713` Settings · `E8FD` List · `E9D5` Audit · `E8C8` Duplicate · `E8AC` Rename · `E8EC` Classify/assign ·
`E81C` History/log · `EA3A` Open. Rail tiles (CastUnit only): Batch create `E710` · Manage `E8FD` · Partition `E8EC`.
Because every glyph is already in both tables, **no edit** to `T3LAB_UI_STANDARD.md` or the stylesheet ICON comment is needed;
the coordinator only appends the "Đang dùng ở" column entries (§7.0).

### 4.3 Row objects, grids, string bridge

Rows are plain Python classes with properties (copy `GroupRow`): `is_selected` (bool → bridge), `StatusText`, `Severity`
(`"Success" | "Warning" | "Danger"`) for `T3.StatusPill`; every text cell is a `str`. Grids: `Style="{StaticResource T3.DataGrid}"`,
exactly one `Width="*"` column; checkbox column = the standard's template (`chk_all_<grid>` header + `select_all_<grid>_clicked` →
`self.toggle_all_rows(self.<grid>, "is_selected", sender.IsChecked)`); row clicks reach Python through
`grid.AddHandler(CheckBox.ClickEvent, RoutedEventHandler(self.<grid>_checkbox_clicked), True)` in `__init__`. Load with
`self.set_items_source(grid, rows)`; refresh with `grid.Items.Refresh()`. Empty state `TextBlock x:Name="<grid>_empty" Style="T3.Empty"`
toggled from Python with `Visibility.Visible/Collapsed` (import `from System.Windows import Visibility`). Status/Severity in the
`T3.StatusPill` template are read through the string bridge already inside that template.

Filters (A8): every results grid has chips `All · In assemblies · Loose` (`RadioButton Style="T3.Chip" GroupName="<grid>_scope"`)
and a `T3.Search` box; filtering is done in Python on the row list, then `set_items_source`.

### 4.4 Messages (English, what · where · next)

Template: `<What is wrong>. <Where: element id / assembly mark / file>. <Next step>.` Examples used in §5:

- E-DOC: `Open a Revit project before running <Tool>.` (details: `<Tool> needs an active document…`)
- E-FAM: `<Tool> works on projects, not on family documents.`
- E-SEL-0: `Nothing to work on. Select hosts, rebar or assemblies in the model, or use the filter, then try again.`
- E-GROUP: skip reason `in group` → row detail `Element <id> is inside group "<name>" — ungroup it or exclude it.`
- E-LINK: `from link` → `Element <id> belongs to linked model "<name>" — assemblies cannot include linked elements.`
- E-ASM-DUP: `already in assembly <mark>` → `Element <id> is already a member of <mark> — remove it there first (Edit Assembly).`
- E-TYPE-SPLIT (A4): `Revit split <n> assembly types and merged <m> while renaming — marks changed on: <list of ≤5>. Check the Assembly schedule.`
- E-BVBS-01: `BVBS self-test failed: the writer no longer matches the reference line from the BVBS guideline. Do not send this file to a machine — report this to T3Lab.`
- E-POST: `Revit <ver> has no postable command for "<Tekla>". Open it from the ribbon: <path>.`
- E-SCOPE-WIZ: `<Element id> is out of the V1 scope: <reason>. Rectangular straight beams and columns, and pad footings, are supported.`
- E-PARTITION-2027: `This model's rebar numbering schema does not partition by "Partition". Partition values were written, but numbers will not change until you add "Partition" as a partitioning parameter in Manage › Numbering.`

### 4.5 `script.py` frame (identical skeleton for all six)

```python
#! python3
# -*- coding: utf-8 -*-
"""<Tool> — <one line>."""
__title__ = "<Two\nLines>"; __author__ = "Tran Tien Thanh"; __version__ = "1.0.0"
# bootstrap block copied verbatim from ManaGroup.pushbutton/script.py (lines 9–38)
from pyrevit import revit            # noqa (keeps pyRevit happy; not used at module level)
if __name__ == '__main__':
    from Snippets._host import resolve_doc
    doc, doc_error = resolve_doc()
    if not doc:  show_warning(E-DOC)            # GUI.T3Dialog
    elif doc.IsFamilyDocument: show_warning(E-FAM)
    else:
        from GUI.<Tool>Dialog import show_<tool>
        show_<tool>(doc)
```

Tekla Bridge is the exception: it opens without a document (`doc` may be None) and runs the returned action after
`ShowDialog()` returns (§5.1).

bundle.yaml per pushbutton: `title: "Two\nLines"`, `tooltip:` (one sentence, ends with the Tekla term in brackets),
`description: |` (3–5 bullets), `author: "Tran Tien Thanh"`, `version: "1.0.0"`.

Icon: `icon.svg` only, `viewBox="0 0 32 32"`, tokens from `dev/icons/tokens.json` (`#000000` line 1px on `.5` grid, `#F3F3F3`
surface, `#858585` detail, `#3C3C3C` deep, accents `#E07B00` amber default / `#178FE6` blue for view objects / `#57B97A`
green for create), ≤6 shapes, no `rx`, no opacity, same-token shapes in the dark build must not touch (≥1 unit gap).
Then coordinator runs `python3 dev/build_icons.py`.

---

## 5 · Tools

Panel: `T3Lab.extension/T3Lab Model.tab/Rebar & Assembly.panel/bundle.yaml` (coordinator):

```yaml
title: "Rebar & Assembly"
background:
  title: "#46E07B00"
layout:
  - TeklaBridge
  - CastUnit
  - CloneDrawing
  - RebarCheck
  - BVBSExport
  - RebarWizard
```

and `T3Lab.extension/T3Lab_Dev.tab/bundle.yaml` gains `  - Rebar & Assembly` after `Modeling & Datum`. Never hardcode the tab
folder name: tests use `dev/tabdir.py`, runtime uses `core.extension_paths.tab_dir()`.
*(Done. Since 2026-10-03 the panel sits in `T3Lab Model.tab`, listed in `T3Lab Model.tab/bundle.yaml`; code finds a
button with `core.extension_paths.find_bundle()` / `bundle_path()` — see `dev/plan/ribbon-tab-split.md`.)*

Common QA checklist (append to each tool's own list; copy roadmap §8):

```
[ ] Loose element → runs                       [ ] Element in assembly → result is a member (Project Browser + assembly schedule)
[ ] AssemblyInstance selected → treated as all its valid members (A1)
[ ] Element in group / from link → "skipped: <reason>", no traceback (A5)
[ ] 2 identical assemblies → still one type afterwards, or the split count is reported (A4)
[ ] Ctrl+Z once undoes the whole click (A6)    [ ] No button does what Revit already has (N1)
[ ] 2 000+ rebar: anything > 2 s shows progress; Stop works; Revit does not freeze
[ ] 100 % and 125 % DPI: no clipped text        [ ] Esc / X closes; no footer button that only closes
```

### 5.1 Tekla Bridge — `TeklaBridge.pushbutton` (WP2, Sonnet)

- Folder: `T3Lab.extension/T3Lab Model.tab/Rebar & Assembly.panel/TeklaBridge.pushbutton/` — `script.py`, `bundle.yaml`, `icon.svg`.
- bundle.yaml: `title: "Tekla\nBridge"` · `tooltip: "Find any Revit command or T3Lab tool by its Tekla name, with a one-line note on what is different (Tekla: Quick Launch)"` · description bullets: *Search by Tekla term, grouped as the Tekla workflow* · *Open the Revit command (PostCommand) or the T3Lab tool* · *Shows the ribbon path when Revit has no postable command* · *Works without an open model*.
- Icon concept: a bridge arch — surface rectangle deck `x=2.5 y=19.5 w=27 h=5` (line + surface), two piers (`detail`, x=7 and x=23, w=3, h=7 below deck), an amber arrow glyph above the deck (`accent.amber` polygon pointing right, 4 points), total 5 shapes.
- Pattern **P2**, size **M 560×420** (D10), `MinWidth=560 MinHeight=420`.
- XAML `lib/GUI/Tools/TeklaBridge.xaml` — title "Tekla Bridge", subtitle "Tekla words, Revit commands". Body (Grid Margin 16, rows Auto/Auto/*/Auto):
  - Row 0: `StackPanel Orientation=Horizontal`: `Border Style="T3.Search"` wrapping `TextBox x:Name="tb_search" Style="T3.TextBox" Tag="Type a Tekla command…" TextChanged="search_text_changed" KeyDown="search_key_down"` (Width 320) · `ComboBox x:Name="cb_group" Style="T3.ComboBox" ItemContainerStyle="T3.ComboBoxItem" Width="180" Margin="8,0,0,0" SelectionChanged="group_changed"` (items: "All groups" + GROUPS labels).
  - Row 1: `TextBlock Style="T3.Label" Text="COMMANDS" Margin="0,12,0,4"` + `Separator Style="T3.Rule"`.
  - Row 2: `Grid`: `ListBox x:Name="lst_rows" Style="T3.ListBox" ItemContainerStyle="T3.ListBoxItem.Multiline" SelectionMode="Single" SelectionChanged="row_selected" MouseDoubleClick="row_double_clicked"`; `ItemTemplate` = `StackPanel`: `TextBlock Text="{Binding tekla}" Style="T3.BodyStrong"` / `StackPanel Horizontal`: `Border Style="T3.Pill"` containing `TextBlock Text="{Binding layer_label}" Style="T3.Caption"` + `TextBlock Text="{Binding revit}" Style="T3.Body.Secondary" Margin="8,0,0,0"`. **No event attributes inside the template** (W2). Empty state `TextBlock x:Name="lst_rows_empty" Style="T3.Empty" Visibility="Collapsed" Text="No command matches that text.&#x0a;Try the Revit word, or clear the search box."`.
  - Row 3: `Border x:Name="tip_box" Style="T3.Callout" Margin="0,12,0,0"`: `StackPanel`: `TextBlock x:Name="txt_tip" Style="T3.Body" TextWrapping="Wrap"` · `TextBlock x:Name="txt_where" Style="T3.Caption" Margin="0,4,0,0"` (ribbon path, Revit-version aware) · `CheckBox x:Name="chk_tip_once" Content="Show this tip before opening, first time only" Style="T3.CheckBox" Margin="0,8,0,0" Click="tip_once_clicked"`.
  - Footer right: `Button x:Name="btn_tool" Style="T3.Button.Secondary" Click="open_tool_clicked" Margin="0,0,8,0"` (`E8E5` + "Open T3Lab tool", Collapsed unless row.tool) · `Button x:Name="btn_guide" Style="T3.Button.Secondary" Click="open_guide_clicked" Margin="0,0,8,0"` (`E7C3` + "Guide") · `Button x:Name="btn_open" Style="T3.Button.Primary" IsDefault="True" Click="open_clicked"` (`EA3A` + "Open in Revit").
- Dialog `lib/GUI/TeklaBridgeDialog.py`: `class BridgeRow(object)` (`tekla`, `revit`, `layer_label` = "Revit" / "T3Lab" / "Revit does it differently", `row` dict) · `class TeklaBridgeDialog(T3WPFWindow)`: `__init__(doc=None)`, `_load_rows`, `_apply_filter`, `_show_row(row)`, `_set_primary(row)` (label becomes "Open T3Lab tool" when layer 2 and no postable; "Show in ribbon" when postable empty & layer 1), handlers `search_text_changed`, `search_key_down` (Enter → `open_clicked`), `group_changed`, `row_selected`, `row_double_clicked`, `tip_once_clicked`, `open_tool_clicked`, `open_guide_clicked`, `open_clicked`, `close_button_clicked` (inherited). `self.action = None` holds `("post", row) | ("tool", row) | ("ribbon", row)`; `open_clicked` validates, applies tip-once (`show_info(tip)` if first time), sets `self.action`, `self.Close()`. `def show_tekla_bridge(doc=None) -> action`.
- script.py: after `dlg.ShowDialog()`: `("post", row)` → `_tekla_bridge.post_revit_command(host_uiapp(), row)`; result `no_command` → `show_info(E-POST)`; `("tool", row)` → `_tekla_bridge.run_tool(row, doc)` (doc may be None → `show_warning(E-DOC)` for tools that need it); `("ribbon", row)` → `show_info("Open from the ribbon: <path>")`.
- Revit API: only `UIApplication.CanPostCommand` / `PostCommand` — no transactions, no document (N1 guard: **no other Revit call is allowed in this tool**).
- Assembly rules: n/a.
- Messages: E-POST; `Guide not found at <path> — it ships with the repository under docs/.`; status `42 commands · 6 groups`.
- QA: `[ ]` open with no model → works · `[ ]` Enter in search opens the first match · `[ ]` "Create Assembly" posts the Revit command after the window closes (nothing selected → Revit's own prompt) · `[ ]` layer-2 row opens the T3Lab tool · `[ ]` tip-once remembered across sessions (`%APPDATA%\T3LabAI\tekla_bridge.json`) · `[ ]` 2027: "Numbering" posts; 2026: "Reinforcement Numbering".

### 5.2 Cast Unit Manager — `CastUnit.pushbutton` (WP3, Opus)

- bundle.yaml: `title: "Cast Unit\nManager"` · `tooltip: "Create assemblies for many hosts at once with their rebar, sync rebar into assemblies, rename marks as a series and set rebar partitions by rule (Tekla: cast unit)"`.
- Icon concept: column block (surface rect `x=12.5 y=2.5 w=7 h=27` with line), three horizontal stirrup lines (`detail`, 1px, y=8.5/16.5/24.5, x=9..23), amber bracket on the left (`accent.amber` rect `x=3 y=6 w=2 h=20`) = cast-unit boundary. 5 shapes.
- Pattern **P2 + P4**, size **L 1000×620**, left rail (3 tiles, `GroupName="CastUnitNav"`, `Checked="nav_toggle_clicked"`): `nav_create` `E710` "Batch create" · `nav_manage` `E8FD` "Manage" · `nav_partition` `E8EC` "Partition by rule"; `TabControl x:Name="tab_control"` with `T3.TabItem.Hidden`.
- **Page 1 Batch create** (`tab_create`): left column (Width 300, `T3.Panel` with ScrollViewer rule 16): section SOURCE: `RadioButton rb_src_selection "Current selection" GroupName="cu_src" Checked="source_changed"` · `rb_src_filter "Filter"` · filter fields (enabled when rb_src_filter): `cb_category` (Structural Framing / Columns / Foundations / Floors / Walls), `cb_level`, `cb_workset`, `cb_type` (all `T3.ComboBox`, `SelectionChanged="filter_changed"`) · `Button btn_pick Style=T3.Button.Secondary Click="pick_clicked"` (`E7C9` "Pick in model"). Section OPTIONS: `chk_include_rebar` "Include hosted rebar" (default on, ToolTip "Rebar, area/path reinforcement, fabric and couplers hosted by each element") · `rb_one_per_host "One assembly per host"` (default) / `rb_all_in_one "All selected into one assembly"` (`GroupName="cu_mode"`) · `chk_name_series` "Name as a series" + `tb_prefix` (T3.TextBox, default "CU-"), `tb_start` (T3.TextBox.Mono "1"), `tb_step` ("1"), `tb_digits` ("3"). Right: `DataGrid grid_create` columns: ☐ 36 (`chk_all_grid_create`) · `Host` * · `Category` 140 · `Level` 110 · `Rebar` 70 (`T3.Cell.Number`) · `Assembly` 120 (existing mark or "— none —" `T3.Cell.Muted`) · `Mark (expected)` 120 · `Status` 170 (`T3.StatusPill`: Ready / Will skip: in group…). Empty: `"No hosts yet.&#x0a;Select beams, columns, footings or assemblies in the model, or set a filter."`. Footer primary `btn_create` "Create 12 assemblies" → P5 `confirm("Create 12 assemblies from 12 hosts (1 286 rebar)?", ok_text="Create 12 assemblies")`.
- **Page 2 Manage** (`tab_manage`): toolbar row 44px: `T3.Search tb_manage_search` (TextChanged `manage_search_changed`) · chips `chip_all_asm / chip_no_views / chip_no_sheet` (`GroupName="cu_manage"`, Checked `manage_chip_checked`) · `Button btn_refresh` (`E72C` Refresh, `refresh_clicked`). `DataGrid grid_manage`: ☐ 36 · `Mark` * · `Instances` 70 · `Members` 70 · `Rebar` 70 · `Unsynced` 70 (rebar hosted but not member — the Sync count) · `Views` 70 · `Sheets` 70 · `Level` 110 · `Status` 150. Empty: `"This model has no assemblies.&#x0a;Create them with Revit's Create Assembly or on the Batch create page."`. Series fields above the grid right side: `tb_ren_prefix / tb_ren_start / tb_ren_step / tb_ren_digits` + `Button btn_rename Style=Secondary Click="rename_clicked"` (`E8AC` "Rename 5 types as series") · `Button btn_open_sheet Style=Secondary Click="open_sheet_clicked"` (`EA3A` "Open sheet", enabled when one row with a sheet). Footer primary on this page: `btn_sync` "Sync rebar into 7 assemblies" (label updates with the ticked count of rows whose Unsynced > 0). Footer secondary: `btn_select` "Select in Revit".
- **Page 3 Partition by rule** (`tab_partition`): SCOPE chips `rb_scope_selection / rb_scope_assemblies (ticked on Manage) / rb_scope_model` (`GroupName="cu_pscope"`, `partition_scope_changed`) · RULE: `cb_rule` editable? No — `ComboBox cb_rule_preset` (presets `{AssemblyMark}` · `{Level}` · `{HostType}` · `{Level}-{HostType}` · `{Workset}` · "Custom…") + `TextBox tb_rule` (`TextChanged="rule_changed"`, tokens listed in a `T3.Caption` below: `{AssemblyMark} {Level} {HostType} {HostMark} {Workset} {Category}`) · `chk_skip_assigned` "Skip bars that already have a partition". Preview `DataGrid grid_partition`: ☐ · `Rebar` * (id + bar type, `T3.Cell.Mono`) · `Host` 140 · `Assembly` 120 · `Current partition` 140 · `New partition` 140 · `Status` 150. Empty `"No rebar in scope.&#x0a;Pick a scope on the left; the preview fills in automatically."`. 2027 callout `Border x:name="partition_warn" Style="T3.Callout.Warning"` Collapsed, shows E-PARTITION-2027 when `_rebar.partition_warning_2027(doc)` returns text. Footer primary `btn_assign` "Assign partition to 412 bars" → P5 confirm (`"Set Partition on 412 bars? Revit will renumber them inside their partitions."`).
- Footer: the single Primary button is `btn_primary` (Click `primary_button_clicked`), relabelled per page by `PRIMARY_LABELS` like ManaGroup; secondaries `btn_select` (`E73E` "Select in Revit", `select_clicked`) and `btn_isolate` (`E7B3` "Isolate in view", `isolate_clicked`).
- Dialog `lib/GUI/CastUnitDialog.py`: rows `HostRow`, `AssemblyRow`, `PartitionRow`; `CastUnitDialog(T3WPFWindow)` methods: `__init__(doc)`, `_load_logo`, `_wire_row_events`, `_build_index` (once: `_rebar.build_rebar_index` with `begin_progress`), `_reload_assemblies`, `_reload_hosts`, `_preview_create`, `_preview_partition`, `_apply_filter_*`, `_set_status`, `_run_batch_create`, `_run_sync`, `_run_rename`, `_run_partition`, `_select_in_revit`, `_isolate`, handlers listed above + `nav_toggle_clicked`, `grid_create_checkbox_clicked`, `grid_manage_checkbox_clicked`, `grid_partition_checkbox_clicked`, `select_all_grid_create_clicked`, `select_all_grid_manage_clicked`, `select_all_grid_partition_clicked`, `primary_button_clicked`, `stop_clicked` (inherited). `def show_cast_unit(doc)`.
- Revit flow (A6): `_run_batch_create`: `with disposing(TransactionGroup(doc, "T3Lab: Create assemblies")) as g: g.Start(); results = _assembly.batch_create(doc, plans, progress=self._step, prefix=…); g.Assimilate()`; on exception `RollBack` + `show_error`. Each plan: Transaction `Create assembly <host>` → `AssemblyInstance.Create(doc, net_list(ElementId, [host]+rebar), host.Category.Id)` → Commit → optional Transaction `Name assembly` → Commit. After: `diff_type_split` → status + E-TYPE-SPLIT info when non-zero; refresh both pages; `uidoc.Selection.SetElementIds(new assembly ids)`.
  `_run_sync`: same group, `_assembly.sync_rebar` (one Transaction per assembly). `_run_rename`: one Transaction via `rename_series` on the ticked rows' types (sorted by current mark, natural sort). `_run_partition`: `_rebar.assign_partition` (one Transaction).
- Assembly rules: A1 selection expansion in `_reload_hosts` (`host_records_from_selection`); A2 n/a (creates assemblies); A3: hosts already in an assembly are *skipped* (`already in assembly <mark>`) — this tool never moves members; A4 reported after create/rename; A5 skip reasons shown in Status before running; A6 above; A7 Views/Sheets columns from `collect_assembly_views`; A8 chips.
- QA extra: `[ ]` Batch create 2 identical columns → both in one AssemblyType · `[ ]` Include hosted rebar off → assembly has host only · `[ ]` Sync rebar after Paste Aligned adds the bar · `[ ]` Rename series keeps identical assemblies as one type and reports splits · `[ ]` Partition value appears in Revit Partition parameter; Reinforcement Numbering/Numbering shows the new partition · `[ ]` 2027 warning text appears only when the schema lacks "Partition".

### 5.3 Clone Drawing — `CloneDrawing.pushbutton` (WP4, Opus)

- bundle.yaml: `title: "Clone\nDrawing"` · `tooltip: "Copy the finished drawing of one assembly — views, sheet, annotations — to similar assemblies (Tekla: clone drawing)"`.
- Icon concept: two overlapping sheets (surface rects `x=2.5 y=6.5 w=17 h=21` and `x=12.5 y=2.5 w=17 h=21`, lines), the front sheet carries a blue viewport rect (`accent.blue`, `x=16 y=7 w=10 h=8`), back sheet a detail line. 4 shapes.
- Pattern **P2 + P3 + P5**, **L 1000×620**, chip tab strip (D11): `chip_tab_select "Source & targets" Tag=0` · `chip_tab_options "Options" Tag=1` · `chip_tab_results "Results" Tag=2` (`GroupName="cd_tabs"`, `Checked="tab_chip_checked"`), `TabControl x:Name="tab_control"`.
- **Tab Select**: left (Width 320): SOURCE ASSEMBLY `ComboBox cb_source` (`SelectionChanged="source_changed"`; items = assemblies that own ≥1 view, label `"C-01 — 5 views, 1 sheet"`) · `TextBlock txt_source_summary Style=T3.Caption` ("3D · 2 elevations · 2 sections · Part list · Sheet S-01 (A1 titleblock)"). TARGETS: chips `chip_tgt_similar "Similar" / chip_tgt_sametype "Same category" / chip_tgt_all "All"` (`GroupName="cd_tgt"`, `targets_chip_checked`) · `T3.Search tb_target_search`. Right: `DataGrid grid_targets`: ☐ · `Mark` * · `Similarity` 90 (`T3.Cell.Number`, "92 %") · `Why` 220 (reasons joined, `T3.Cell.Muted` when none) · `Views` 70 · `Sheets` 70 · `Status` 170 (Ready / Has drawing — add missing only / Will skip: has drawing). Empty: `"No other assemblies in the model.&#x0a;Create the target assemblies first (Cast Unit Manager or Create Assembly)."`.
- **Tab Options** (form, P1 style, M-width column): LAYERS: `chk_t1 "T1 — Views and sheet (same kind, template, scale, crop)"` on · `chk_t2 "T2 — Free annotations (text, detail lines, detail items, filled regions)"` on · `chk_t3 "T3 — Re-create tags and dimensions on matched elements"` on. EXISTING DRAWINGS: `rb_skip_existing "Skip assemblies that already have views"` (default) / `rb_add_missing "Add only the missing views and sheet"` (`GroupName="cd_existing"`). MATCHING: `tb_tolerance` (T3.TextBox.Mono "10", mm) · `chk_mirror "Allow mirrored matches"` on · `tb_sheet_pattern` ("{SourceNumber}-{Mark}") · `tb_view_pattern` ("{SourceName}" with mark substitution note). Callout `T3.Callout`: "Tags and dimensions are re-created only where the target has a matching element; the rest is listed under Unmatched so you can add them by hand."
- **Tab Results**: `DataGrid grid_results`: `Assembly` * · `Views` 70 · `Sheet` 110 · `Copied` 70 · `Tags` 90 ("4 / 5") · `Dims` 90 · `Unmatched` 90 · `Status` 150. Below (rule 19: same `T3.Panel`, 1px rule): `ListBox lst_log Style=T3.LogBox` (`T3.Log.*` lines: `12:01:03 ok C-02: 5 views, sheet S-02`, `skipped C-04: has drawing`, `failed C-05: <short_error>`), `T3.Tally` strip `txt_tally`. Empty: `"Nothing cloned yet.&#x0a;Pick a source and targets, then press Clone."`. Secondary `btn_open_sheet` (`EA3A` "Open sheet") activates the selected result's sheet via `uidoc.RequestViewChange`? — modal: set `uidoc.ActiveView` is not allowed while modal `[NV]` → store the sheet id and apply after `Close()` (script.py applies `uidoc.ActiveView = sheet` when the dialog returns `open_sheet_id`).
- Footer: secondary `btn_open_sheet`, secondary `btn_log_copy` (`E8C8` "Copy log" → clipboard), primary `btn_clone` "Clone to 6 assemblies" (`IsDefault`) → P5 `confirm("Clone the drawing of C-01 to 6 assemblies? This creates 30 views and 6 sheets.", ok_text="Clone to 6 assemblies")`.
- Dialog `lib/GUI/CloneDrawingDialog.py`: rows `TargetRow`, `ResultRow`; `CloneDrawingDialog(T3WPFWindow)`: `__init__(doc)`, `_load_sources`, `_load_targets`, `_score_targets`, `_apply_target_filter`, `_options()`, `_run_clone`, `_log(level, text)`, handlers `tab_chip_checked`, `source_changed`, `targets_chip_checked`, `target_search_changed`, `grid_targets_checkbox_clicked`, `select_all_grid_targets_clicked`, `open_sheet_clicked`, `log_copy_clicked`, `clone_clicked`, `results_selected`. `def show_clone_drawing(doc) -> open_sheet_id|None`.
- Revit flow: `with disposing(TransactionGroup(doc, "T3Lab: Clone drawing")) as g:` per target: Transaction "Clone views to <mark>" (T1: `_drawing_clone.create_views`) → Commit; Transaction "Copy annotations to <mark>" (T2 per view in SubTransactions, failures rolled back per view) → Commit; Transaction "Re-create references on <mark>" (T3) → Commit; `g.Assimilate()`. `step_progress` per target with label; Stop → remaining `skipped: stopped`. Targets with existing views: skip or add-missing per option (A7 via `AssociatedAssemblyInstanceId`). Nothing is ever deleted.
- Assembly rules: A1 (source/targets are assemblies), A4 (if `AssemblyTypeName` of a target equals the source's, warn "same type — Revit shares views between instances of one type; nothing to clone" `[NV G1]`), A6, A7, A8 (target grid chips).
- QA extra: `[ ]` T1 only → views exist with the same template/scale/crop and sheet with same title block; viewport at the same position · `[ ]` T2 text note lands at the same place relative to the target assembly (G2 decides `T2_TRANSFORM_MODE`) · `[ ]` T3 tag on the matched rebar set, leader kept · `[ ]` dimension between two host faces re-created · `[ ]` different-shape target → unmatched count > 0, no crash · `[ ]` Ctrl+Z removes all created views/sheets at once · `[ ]` re-run with "Skip existing" → 0 created, status says so.

### 5.4 Rebar Check — `RebarCheck.pushbutton` (WP5, Sonnet)

- bundle.yaml: `title: "Rebar\nCheck"` · `tooltip: "Find rebar without a valid host, rebar missing from its assembly, assemblies without drawings, duplicate numbers and bars outside their host — data checks Revit does not run (Tekla: model checks)"`.
- Icon concept: three parallel bars (`deep` rects `x=4..` w=20 h=2 at y=8/15/22) and an amber check-mark polyline (`accent.amber` stroke 2, points `19,22 23,27 29,17`). 4 shapes.
- Pattern **P4**, **L 1000×620**, single page.
- Summary strip (52px, `T3.Panel`, counts separated by 1px rules): `txt_n_total "1 286 rebar"` · `txt_n_issues "37 issues"` · `txt_n_asm "12 assemblies"` · `T3.Meter meter_ok` (pass rate) — each number `T3.Display`, label `T3.Caption`.
- Filter row: chips (`GroupName="rc_check"`, `check_chip_checked`): `chip_all "All" · chip_host "No valid host" · chip_member "Not in assembly" · chip_drawing "No drawing" · chip_dup "Duplicate number" · chip_bbox "Outside host" · chip_partition "No partition" · chip_shape "Shape unknown"` · scope chips (`rc_scope`): `All / In assemblies / Loose` · `T3.Search tb_search` · `Button btn_rescan` (`E72C` "Rescan", `rescan_clicked`).
- `DataGrid grid_issues`: ☐ 36 · `Check` 150 · `Element` 90 (id, `T3.Cell.Mono`) · `Detail` * · `Category` 140 · `Assembly` 120 · `Partition / Number` 140 · `Fix` 150 (`T3.StatusPill`: "Sync into assembly" Warning / "Assign partition" Warning / "Manual" Muted). Empty: `"No issues found — 1 286 rebar checked.&#x0a;Change the filter or rescan after editing the model."` (count injected from Python).
- Footer: secondary `btn_fix` (`E90F` Tools/bulk edit + "Fix selected (12)", `fix_clicked`; enabled when every ticked row is fixable) · secondary `btn_isolate` (`E7B3` "Isolate in view", `isolate_clicked`) · primary `btn_select` "Select in Revit (12)" (`select_clicked`, `IsDefault`).
- Checks (`lib/Snippets/_rebar_check.py` — pure classification over `RebarIndex` + `AssemblyRecord`s, Revit only for bbox):

| id | rule `[PURE]` unless noted | detail text |
|---|---|---|
| `host` | `host_id == -1` or host element missing/deleted | `Host element <id> no longer exists` |
| `member` | host.assembly_id != -1 and rebar.assembly_id != host.assembly_id | `Hosted by <host> in <mark> but not a member` → fix Sync |
| `drawing` | per assembly: no view ids (and separately no sheet) — row per assembly, Element = assembly id | `No views` / `Views but no sheet` |
| `dup` | group by (partition, number); >1 distinct (shape, bar_type, legs rounded 1 mm, quantity) | `Number 12 in P1 used by 2 different bars` |
| `bbox` | `[REVIT]` rebar bbox not inside host bbox grown by `tol = max(50 mm, cover)` | `Bar extends 120 mm outside host <id>` |
| `partition` | partition == "" (shape-driven & free-form rebar only) | `No partition` → fix Assign |
| `shape` | `is_shape_driven and shape == ""` | `Shape not recognised — edit the bar` |

- Dialog `lib/GUI/RebarCheckDialog.py`: `IssueRow`; `RebarCheckDialog(T3WPFWindow)`: `__init__(doc)`, `_scan` (progress), `_apply_filter`, `_update_summary`, `_run_fix` (groups ticked rows: `member` → `_assembly.sync_rebar` for the involved assemblies; `partition` → asks the rule with `T3Dialog`-style small prompt? No — opens Cast Unit Manager page 3 preset to those bars: `show_cast_unit(doc, page="partition", ids=[…])`; so `btn_fix` for partition rows = hand-off, for member rows = direct sync inside one TransactionGroup), `_select_in_revit`, `_isolate`, handlers `check_chip_checked`, `scope_chip_checked`, `search_text_changed`, `rescan_clicked`, `grid_issues_checkbox_clicked`, `select_all_grid_issues_clicked`, `fix_clicked`, `isolate_clicked`, `select_clicked`. `def show_rebar_check(doc)`.
- Revit flow: scan is read-only; Fix(member) = `TransactionGroup "T3Lab: Sync rebar"` + per-assembly Transaction (A6); Isolate = Transaction "Isolate in view" → `uidoc.ActiveView.IsolateElementsTemporary(net_list(ElementId, ids))` (view must be a model view — else `show_warning("Isolate needs a model view. Open a plan, section or 3D view and try again.")`).
- Assembly rules: A1 (scope = whole model; selection chips), A2 via Sync, A5 (rows in groups/links are reported `Manual` with reason), A6, A7 (`drawing` check), A8 chips. No clash check (N1).
- QA extra: `[ ]` delete a host → `host` row · `[ ]` paste-aligned rebar → `member` row, Fix adds it · `[ ]` assembly without Create Views → `drawing` row · `[ ]` two different bars with the same number (after manual edit) → `dup` row · `[ ]` Isolate then "Reset Temporary Hide/Isolate" in Revit restores.

### 5.5 BVBS Export — `BVBSExport.pushbutton` (WP6, Sonnet)

- bundle.yaml: `title: "BVBS\nExport"` · `tooltip: "Write .abs files (BVBS BF2D) for bending machines from shape-driven rebar, with checksum — one file per assembly or one for all (Tekla: BVBS export)"`.
- Icon concept: bent bar polyline (`line`, stroke 2: `4.5,27.5 4.5,8.5 14.5,8.5 14.5,20.5`) + document corner (surface rect `x=17.5 y=2.5 w=12 h=15` with folded corner polygon in `detail`) + amber arrow down (`accent.amber` polygon). 4 shapes.
- Pattern **P2 + P4**, **M 560×560** (`MinHeight 560`).
- Body: SCOPE chips (`bv_scope`, `scope_changed`): `rb_scope_model "Whole model" · rb_scope_selection "Selection" · rb_scope_assembly "Assembly" · rb_scope_partition "Partition"` + `ComboBox cb_assembly` / `cb_partition` (enabled per chip). FILE: `tb_project` (default `ProjectInformation.Number`), `tb_plan` (default "MODEL" or the assembly mark), `tb_revision` ("a"), `tb_grade` (default "B500B"), `chk_per_assembly "One file per assembly"`, `chk_only_shape_driven "Shape-driven rebar only"` (on; off = also planar free-form). PREVIEW `DataGrid grid_preview`: ☐ · `Mark` * (`T3.Cell.Mono`) · `Ø` 50 · `n` 50 · `Length` 70 · `Legs` 150 (`400/90/600`) · `Status` 150 (`Ready` / `Skipped: free-form 3D` / `Skipped: curved leg` / `Skipped: no diameter`). Empty: `"No rebar in this scope.&#x0a;Pick another scope or select rebar in the model."`. `txt_weight_source` caption under the grid: `"Weight: Revit mass (2027)"` / `"Weight: T3_WeightPerMetre"` / `"Weight: default table (TCVN/BS)"`.
- Footer: secondary `btn_preview` (`E72C` "Refresh preview", `preview_clicked`) · secondary `btn_folder` (`E8E5` "Choose folder…", `folder_clicked`, uses `_cpython_bootstrap.install_forms_shim` `pick_folder`) · primary `btn_export` "Export 128 bars" (`export_clicked`, `IsDefault`).
- Dialog `lib/GUI/BVBSExportDialog.py`: `BarRow`; `BVBSExportDialog(T3WPFWindow)`: `__init__(doc)` runs the self-test (`verify_block(REFERENCE_LINE_1)`; on failure disables `btn_export` and shows E-BVBS-01 in a `T3.Callout.Danger`), `_collect_scope`, `_build_records` (progress; per rebar: `centerline_points` → `classify_centerline` → `centerline_to_legs` → `outer_legs` → `BarRecord`), `_weight_for(bar_type)` (D13 chain), `_write` (`write_file` per group; filename `<plan>.abs` or `<plan>_<mark>.abs`, sanitised), `_set_status`, handlers `scope_changed`, `assembly_changed`, `partition_changed`, `preview_clicked`, `folder_clicked`, `export_clicked`, `grid_preview_checkbox_clicked`, `select_all_grid_preview_clicked`. `def show_bvbs_export(doc)`.
- Revit flow: read-only (no transactions). Export writes files; after writing, re-read each file with `verify_block` on every line (**roadmap §4.4: not "done" until read back**) and report `Wrote 128 bars to 3 files · 4 skipped (3D) · verified`. Opens the folder? No (rule: no shell actions without asking) — status shows the path and `show_info` lists files.
- Assembly rules: A1 (scope "Assembly" uses members' rebar via `rebar_index.by_host`), A8 (per-assembly file naming uses marks).
- QA extra: `[ ]` fixture line round-trips (unit test) · `[ ]` a straight bar, an L bar and a stirrup export; open in a free BVBS viewer (e.g. the Soft-Tec/BVBS viewer) — lengths match the bending schedule's out-to-out dimensions · `[ ]` free-form 3D bar → skipped row · `[ ]` rebar set with varying bars → one line per distinct geometry with its own count.

### 5.6 Rebar Wizard — `RebarWizard.pushbutton` (WP7, Opus)

- bundle.yaml: `title: "Rebar\nWizard"` · `tooltip: "Reinforce rectangular beams, columns and pad footings from a preset — main bars, stirrups in three zones, footing mesh — added to the host's assembly (Tekla: system components 63 / 83 / 77)"`.
- Icon concept: beam section — surface rect `x=4.5 y=4.5 w=23 h=23` (line), stirrup inner rect (`line`, `x=8.5 y=8.5 w=15 h=15`), four green dots as corner bars (`accent.green` circles r=1.5 at (11,11)(21,11)(11,21)(21,21)) → 6 shapes.
- Pattern **P1 + P5**, **L 1000×620**, chip tab strip `chip_tab_beam "Beam" · chip_tab_column "Column" · chip_tab_footing "Pad footing"` (`GroupName="rw_tabs"`, `tab_chip_checked`), `TabControl tab_control`.
- Common top band (above tabs, 44px): HOSTS: `Button btn_use_selection` (`E73E` "Use selection", `use_selection_clicked`) · `Button btn_pick` (`E7C9` "Pick in model", `pick_clicked`) · `TextBlock txt_hosts Style=T3.Body.Secondary` ("3 beams · 1 out of scope"). PRESET: `ComboBox cb_preset` (`preset_changed`) · `Button btn_preset_save` (`E74E` "Save preset", `preset_save_clicked`) · `Button btn_preset_delete` (`E74D` "Delete", `preset_delete_clicked`).
- Each tab = two columns: left form (`T3.Panel` + ScrollViewer rule 16, Width 420), right `T3.Panel`: `DataGrid grid_hosts` (`Host` * · `Section` 110 ("300×600") · `Length` 80 · `Assembly` 120 · `Status` 170: Ready / Out of scope: <reason>) + `Border Style=T3.Callout` with `txt_summary` (from `summarize`) + hosts empty `"No hosts yet.&#x0a;Select beams in the model or press Pick in model."`.
  - Beam form fields (x:Name prefix `bm_`): `cb_bm_bot_dia` (Ø list from `resolve_types`), `tb_bm_bot_n` ("4"), `cb_bm_top_dia`, `tb_bm_top_n` ("2"), `cb_bm_stir_dia`, `tb_bm_s_end` ("100"), `tb_bm_l_end` ("600"), `tb_bm_s_mid` ("150"), `tb_bm_cover` ("25"), `cb_bm_hook` (RebarHookType names; default the 135° one if present), `chk_bm_add_assembly "Add bars to the host's assembly"` (on, disabled when host not in assembly).
  - Column (`col_`): `cb_col_vert_dia`, `tb_col_side_n` ("1" extra bars per face besides corners), `cb_col_tie_dia`, `tb_col_s_dense` ("100"), `tb_col_l_dense` ("600"), `tb_col_s_mid` ("200"), `tb_col_cover` ("40"), `cb_col_hook`, `chk_col_cross_tie` "Add cross ties when a face has extra bars", `chk_col_add_assembly`.
  - Footing (`ft_`): `cb_ft_x_dia`, `tb_ft_x_s` ("150"), `cb_ft_y_dia`, `tb_ft_y_s` ("150"), `tb_ft_cover` ("50"), `chk_ft_hooks` "90° end hooks", `chk_ft_add_assembly`.
  - All numeric `TextBox`es `Style=T3.TextBox.Mono`, `TextChanged="field_changed"` → `_recompute()` (validation message in `txt_validation` `T3.Caption` `T3.Danger.Text`? — colour via `Foreground="{StaticResource T3.Danger.Text}"` is allowed as a token; text says the error).
- Footer: secondary `btn_select` ("Select created bars", enabled after a run) · primary `btn_create` "Create rebar for 3 beams" → P5 `confirm("Create 42 rebar sets in 3 beams? Bars are added to each beam's assembly.", ok_text="Create rebar for 3 beams")`.
- Dialog `lib/GUI/RebarWizardDialog.py`: `HostRow`; `RebarWizardDialog(T3WPFWindow)`: `__init__(doc)`, `_load_types`, `_load_presets`, `_read_form(kind) -> WizardInput`, `_write_form(kind, inp)`, `_recompute` (sections → plans → summary), `_run_create`, handlers `tab_chip_checked`, `use_selection_clicked`, `pick_clicked` (Hide → `PickObjects` with `_rebar_wizard.HostFilter` (ISelectionFilter, `__namespace__ = "T3Lab.RebarWizard"`, lives in the Snippets module) → Show), `preset_changed`, `preset_save_clicked` (asks a name with `GUI.forms` `ask_for_string` shim), `preset_delete_clicked`, `field_changed`, `select_clicked`, `create_clicked`. `def show_rebar_wizard(doc)`.
- Revit flow: `TransactionGroup "T3Lab: Rebar Wizard"`; per host Transaction `Rebar Wizard: <host>` → `_rebar_wizard.create_plans` (creates every BarPlan; on the first failure inside a host the host's Transaction is rolled back and the host reported `failed`, other hosts continue); `Assimilate`. A2 inside the same Transaction via `_compat.add_to_assembly_of`. Progress per host.
- Layout math summary (pure, mm, verified by `dev/test_rebar_wizard_layout.py`): beam local frame origin at axis start, X along axis, Y across width, Z up; bottom bars at `z = -h/2 + cover + d_stir + d_main/2`, spread evenly across `b - 2·(cover + d_stir) - d_main` with `n` bars (n=1 → centre); top symmetric; stirrups: closed rectangle at `±(b/2 - cover - d_stir/2)`, `±(h/2 - cover - d_stir/2)`, normal = X, three sets `SetLayoutAsMaximumSpacing(s, zone_len, True, True, True)` positioned by `zone_lengths(L, l_end)`; main bars run the full length minus `cover` each end (no hooks in V1 beams; hook types only for stirrups). Column: same in Z; verticals full height minus cover; ties = beam stirrups rotated. Footing: X bars at `z = cover + d_y + d_x/2` from bottom, every `s` across `Y` extent minus 2·cover; Y bars below them.
- Out-of-scope reasons (`read_section`): curved beam · section is not rectangular · sloped beam (axis not horizontal within 1°) · tapered/non-vertical column · footing not a FamilyInstance · wall or slab (use Area Reinforcement) · element from link · element in group (A5) · no RebarBarType in the model (`show_warning("This model has no Rebar Bar Types. Load a rebar family (Structure › Rebar) and try again.")`).
- QA extra: `[ ]` 300×600 beam L=6 m → 4 Ø20 bottom, 2 Ø16 top, stirrups Ø8 @100 in 600 mm end zones and @150 mid; bars inside cover · `[ ]` beam in assembly → bars are members · `[ ]` column 400×400 → 4 corner + side bars, ties dense top/bottom · `[ ]` pad footing → two-layer mesh · `[ ]` curved beam → out of scope row · `[ ]` preset save / reload / delete · `[ ]` Revit 2027 and 2026 create (BarTerminationsData path); 2025 legacy path `[NV]`.

---

## 6 · Tests (`dev/test_*.py`, all runnable without Revit)

Every test file loads its Snippets module with the stub loader copied from `dev/test_group_manager.py` (`_load_module(name)`
→ exec with `Autodesk.*`, `System`, `clr`, `pyrevit` stubbed) so the **shipped source** is tested, and uses `unittest`.
Run: `python3 dev/test_<name>.py`. Each file ends with `if __name__ == '__main__': unittest.main()`.

| File (owner) | Module under test | Cases |
|---|---|---|
| `dev/test_bvbs_writer.py` (WP6) | `_bvbs` | `test_checksum_guideline_example` (`"abcde@C"` → 78) · `test_fixture_1_to_5_roundtrip` (build `BarRecord` for each §2.1 fixture: `write_block` equals the line byte-for-byte — fixture 4/5 need `r` arcs: mark those two as `expectedFailure` until arc support lands, keep 1–3 strict) · `test_verify_block_detects_corruption` (flip one char) · `test_header_has_no_m_field` · `test_problems_lists_every_reason` · `test_write_file_crlf_and_ascii` (tmp file, `\r\n`, non-ASCII mark replaced) · `test_default_weight_table_then_formula` |
| `dev/test_rebar_geometry.py` (WP1) | `_rebar` | `test_straight_bar_single_leg` · `test_L_bar_90` (`[(0,0,0),(400,0,0),(400,600,0)]` → legs `[(400,90),(600,0)]`) · `test_U_bar_signs` (left turns + , right turns −) · `test_collinear_points_merged` · `test_180_hook` · `test_non_planar_rejected` (z varies > tol) · `test_outer_legs_90_adds_half_d_per_bend` (d=12: `[(400,90),(600,0)]` → `[406,606]`) · `test_outer_legs_45` (`(d/2)·tan(22.5°)`) · `test_legs_to_bvbs_rounding` · `test_classify_centerline_arcs` · `test_fingerprint_tolerance_and_mirror` · `test_match_by_fingerprint_unmatched_reported` |
| `dev/test_partition_rule.py` (WP1) | `_rebar.render_partition` | tokens resolved · empty tokens collapse separators · unknown token kept · all-empty → `''` · >64 chars flagged |
| `dev/test_assembly_rules.py` (WP1) | `_assembly` pure parts | `test_filter_candidates_group_link_assembly` (A5/A3 reasons) · `test_plan_batch_one_per_host_vs_all` · `test_series_names_padding_step` · `test_diff_type_split_counts` (A4) · `test_expand_selection_assembly_to_members` (A1) · `test_result_statuses_only_ok_skipped_failed` |
| `dev/test_rebar_fingerprint.py` (WP4) | `_drawing_clone` + `_rebar.match_by_fingerprint` | `test_similarity_same_category_within_tol_is_100` · `test_similarity_penalties` · `test_classify_view_table` (each orientation vector → name; unknown → `None`) · `test_substitute_mark` · `test_plan_clone_skip_existing_vs_add_missing` |
| `dev/test_rebar_check_rules.py` (WP5) | `_rebar_check` | one test per check id with fake `RebarRecord`/`AssemblyRecord`/`HostRecord` lists: host missing · member mismatch · no views / no sheet · duplicate number with different legs vs same legs (no issue) · empty partition · unknown shape · `test_group_and_link_rows_are_manual` · `test_scope_filter_in_assemblies_loose` |
| `dev/test_rebar_wizard_layout.py` (WP7) | `_rebar_wizard` pure parts | `test_beam_bottom_bars_spacing_and_cover` · `test_beam_single_bar_centered` · `test_stirrup_points_closed_and_inside_cover` · `test_zone_lengths_short_beam_single_zone` · `test_column_corner_plus_side_bars` · `test_footing_mesh_counts` · `test_validate_rejects_negative_and_zero` · `test_summarize_english_counts` · `test_preset_roundtrip_tmpfile` |
| `dev/test_tekla_bridge.py` (WP2) | `_tekla_bridge` + `dev/build_tekla_docs.py` | `test_json_loads_and_validates` · `test_every_group_present_and_rows_ordered` · `test_layer2_rows_name_a_tool` · `test_postable_names_are_identifiers` · `test_search_matches_tekla_and_revit_and_keywords` · `test_ribbon_path_for_year` · `test_no_vietnamese_in_tip_en` (reuse `VN_CHARS` regex idea) · `test_build_docs_check_passes` (subprocess `--check` exit 0) · `test_shortcut_template_only_cited_keys` (every `tekla_shortcut` value ∈ §2.4 set) |
| `dev/test_compat_rebar.py` (WP1) | `_compat` additions | `test_to_mm_feet_roundtrip` · `test_short_error_first_line` · `test_pick_enum_first_candidate` (fake enum class) · `test_create_rebar_from_curves_falls_back_to_legacy` (stub `BarTerminationsData` missing → legacy path called with expected args) · `test_create_rebar_from_curves_prefers_terminations_data` |

Existing suites must stay green: `dev/test_group_manager.py`, `dev/test_compat_disposing.py`, `dev/test_family_transfer.py`,
`dev/test_audit_t3_icons.py`, `dev/test_extension_paths.py`.

---

## 7 · Work packages

File ownership is **disjoint**; a package never edits a file outside its list. Shared files belong to the coordinator (§7.0).
Recommended models: **Opus** for WP3 (Cast Unit Manager), WP4 (Clone Drawing), WP7 (Rebar Wizard); **Sonnet** for the rest.

| WP | Name | Model | Files owned (create/modify) | Depends on |
|---|---|---|---|---|
| WP0 | Coordinator | — | panel + tab `bundle.yaml`, `dev/audit_t3.py` (exemptions only if ever needed), `dev/audit_revit_compat.py` RULES, `pyRevit UI Design System/T3LAB_UI_STANDARD.md` (glyph "Đang dùng ở" column), `docs/ui-governance/PRIORITY_QUEUE.md`, `dev/plan/README.md`, runs `build_icons.py`, `sync_t3_styles.py`, merges | — |
| WP1 | Foundation + spike | Sonnet | `lib/Snippets/_compat.py` (append only), `lib/Snippets/_assembly.py`, `lib/Snippets/_rebar.py`, `dev/debug/spike_rebar_assembly.py`, `dev/test_rebar_geometry.py`, `dev/test_partition_rule.py`, `dev/test_assembly_rules.py`, `dev/test_compat_rebar.py` | — (first) |
| WP2 | Tekla Bridge + docs | Sonnet | `lib/Snippets/_tekla_bridge.py`, `lib/data/tekla_bridge.json`, `lib/data/KeyboardShortcuts_Tekla.xml` (generated), `dev/build_tekla_docs.py`, `docs/tekla-to-revit-2027.md` (generated), `…/TeklaBridge.pushbutton/{script.py,bundle.yaml,icon.svg}`, `lib/GUI/Tools/TeklaBridge.xaml`, `lib/GUI/TeklaBridgeDialog.py`, `dev/test_tekla_bridge.py` | WP1 only for `_compat.postable_command_id` (may stub-call through `getattr` until merged) |
| WP3 | Cast Unit Manager | Opus | `…/CastUnit.pushbutton/*`, `lib/GUI/Tools/CastUnit.xaml`, `lib/GUI/CastUnitDialog.py` | WP1 |
| WP4 | Clone Drawing | Opus | `lib/Snippets/_drawing_clone.py`, `…/CloneDrawing.pushbutton/*`, `lib/GUI/Tools/CloneDrawing.xaml`, `lib/GUI/CloneDrawingDialog.py`, `dev/test_rebar_fingerprint.py` | WP1 |
| WP5 | Rebar Check | Sonnet | `lib/Snippets/_rebar_check.py`, `…/RebarCheck.pushbutton/*`, `lib/GUI/Tools/RebarCheck.xaml`, `lib/GUI/RebarCheckDialog.py`, `dev/test_rebar_check_rules.py` | WP1; WP3 for `show_cast_unit(doc, page="partition", ids=…)` (call guarded with `try/ImportError` → `show_info("Open Cast Unit Manager › Partition by rule…")` until WP3 lands) |
| WP6 | BVBS Export | Sonnet | `lib/Snippets/_bvbs.py`, `lib/data/rebar_weights.json`, `…/BVBSExport.pushbutton/*`, `lib/GUI/Tools/BVBSExport.xaml`, `lib/GUI/BVBSExportDialog.py`, `dev/test_bvbs_writer.py` | WP1 (`_rebar.centerline_*`, `_compat.bar_mass_per_metre`) |
| WP7 | Rebar Wizard | Opus | `lib/Snippets/_rebar_wizard.py`, `…/RebarWizard.pushbutton/*`, `lib/GUI/Tools/RebarWizard.xaml`, `lib/GUI/RebarWizardDialog.py`, `dev/test_rebar_wizard_layout.py` | WP1 (`create_rebar_from_curves`, `add_to_assembly_of`) |

Order: WP1 first (merge when its tests + compat gate pass). Then WP2–WP7 in parallel. The coordinator merges, runs
`build_icons.py`, `sync_t3_styles.py`, all gates, then the Revit QA (spike first).

### 7.0 Coordinator tasks (exact edits)

1. `T3Lab.extension/T3Lab_Dev.tab/bundle.yaml`: add `  - Rebar & Assembly` to `layout` after `Modeling & Datum`.
   *(Done; now `T3Lab Model.tab/bundle.yaml` since the 2026-10-03 tab split.)*
2. Create `T3Lab.extension/T3Lab Model.tab/Rebar & Assembly.panel/bundle.yaml` (§5 header).
3. `dev/audit_revit_compat.py` `RULES` — append:
   ```python
   dict(name="RebarHookOrientation", kind="type", removed=2027, fix="Snippets._compat.create_rebar_from_curves()"),
   dict(name="BarTerminationsData", kind="type", added=2026, fix="Snippets._compat.create_rebar_from_curves()"),
   dict(name="NumberingSchemaType", kind="type", removed=2027, fix="NumberingSchema.GetNumberingSchema(doc, name) / GetSchemasInDocument"),
   dict(name="BarMassPerUnitLength", kind="member", added=2027, fix="Snippets._compat.bar_mass_per_metre()"),
   dict(name="Mass", kind="member", added=2027, receiver=re.compile(r"(?i)rebar|bar$"), fix="Snippets._compat.bar_mass_per_metre()"),
   dict(name="SetLayoutAsCustomSpacing", kind="member", added=2027, fix="SetLayoutAsMaximumSpacing (all releases)"),
   ```
   then run the gate over the whole extension (existing code must stay green; fix any false positive with `receiver`).
4. `pyRevit UI Design System/T3LAB_UI_STANDARD.md` glyph table "Khái niệm trang": append to the "Đang dùng ở" cells — `E710` (standard table; add a row `E710 Add — Tạo mới hàng loạt · CastUnit Batch create` under the page-concept table), `E8FD` += `CastUnit Manage`, `E8EC` += `CastUnit Partition by rule`, `E9D5` += `RebarCheck`, `E8C8` += `CloneDrawing`, `E896` += `BVBSExport`. No new codepoints. Mirror the same additions in the ICON comment of `pyRevit UI Design System/T3Lab.Styles.xaml` (page-concept list) and run `python3 dev/sync_t3_styles.py`.
5. `dev/audit_t3.py`: no exemption expected. If a builder believes one is needed, they stop and report; the coordinator decides (`SELECTALL_EXEMPT` / `BRIDGE_EXEMPT` / `PYROW_TRIGGER_EXEMPT` / `CLOSE_ONLY_EXEMPT` keys as in that file).
6. `docs/ui-governance/PRIORITY_QUEUE.md`: add a "Rebar & Assembly panel — Revit 2027 QA" block listing the six tools + spike with `NEEDS VERIFICATION`, and a `DESIGN SYSTEM GAP` note only if one appears (none expected).
7. `dev/plan/README.md`: link this spec; progress table rows WP1–WP7 (⬜ → 🔄 → ✅); QA rows separate (ticked only from user feedback).
8. After merge: `python3 dev/build_icons.py` (needs Node + `@resvg/resvg-js`), `python3 dev/build_tekla_docs.py`, full gate list (§0), commit with the `Co-Authored-By` lines from the session reminder.

### 7.1 Builder hand-back format (every package)

Reply with: files created/modified (absolute paths) · gate output lines (`audit_t3 … 0 violations`, etc.) · tests run and
counts · every `[NV]` item touched and how the code degrades when it fails · anything deliberately left out with the reason.
Do not commit; do not touch files outside your list; do not add dependencies (S9).

---

## 8 · Definition of done

Per package (all mandatory):

```
[ ] Files only in the package's ownership list
[ ] python3 dev/audit_t3.py --quiet            → 0 violations (and no new waiver)
[ ] python3 dev/audit_tools.py --quiet         → clean
[ ] python3 dev/audit_wiring.py --quiet        → W1/W2/W3/D1 = 0 (D3: every new Dialog is imported by its script.py)
[ ] python3 dev/audit_revit_compat.py --quiet  → clean (after the coordinator's RULES additions)
[ ] python3 dev/audit_cpython.py --quiet       → 0 P0
[ ] python3 dev/audit_api_context.py --quiet   → clean (no modeless dialogs added)
[ ] python3 dev/sync_t3_styles.py --check      → no drift
[ ] python3 dev/check_xaml_load.py --out /tmp/t3xaml → 0 broken
[ ] python3 dev/audit_icons.py --quiet         → clean for the package's icon.svg (PNG/dark built by coordinator)
[ ] package tests green; existing suites green
[ ] Every user-facing string English; every message has what · where · next
[ ] Every Revit-touching function returns Result rows, never raises per item
[ ] script.py: `#! python3` line 1, bootstrap block, resolve_doc, no module-level doc
[ ] XAML: one Primary (IsDefault) right-most; IsCancel on btn_close; copyright first in footer; empty state per grid; string bridge; no local <Style x:Key>
```

Panel-level (coordinator): `build_icons.py --check` clean · `build_tekla_docs.py --check` clean · `check_xaml_wpf.ps1` 0 FAILED ·
spike log reviewed, every G1–G16 line copied into `dev/plan/README.md` with PASS/FAIL · each tool's QA list run on Revit 2027
on the sample model · `[NV]` markers in code comments resolved or kept with the spike result quoted.

---

## 9 · Open risks

| # | Risk | Mitigation in this spec |
|---|---|---|
| R1 | `CopyElements` view-to-view may reject assembly views or need identity transform (G2). | `T2_TRANSFORM_MODE` single constant; T2 failures are per-view SubTransactions → `skipped: copy refused` rows, T1/T3 still run. |
| R2 | `classify_view` mapping of assembly view orientation is inferred, not documented (G14). | Views with unknown orientation are created as `HorizontalDetail`? No — reported `skipped: orientation unknown` to avoid wrong drawings; spike calibrates the table. |
| R3 | Dimension re-creation needs face references that match; Revit `Reference` of faces may differ between instances of one family. | V1 limits dims to host faces matched by normal+distance; everything else → unmatched; promised in UI copy. |
| R4 | BVBS outer-dimension conversion vs Revit shape parameters (D14, G12). | Isolated in `outer_legs`; unit-tested; dialog shows legs so the user can compare with the bending schedule before sending to a machine; self-test gate. |
| R5 | 2027 numbering overhaul: Partition parameter may stop driving numbers. | D15 warning; tool never writes numbers; Bridge row points to Manage › Numbering. |
| R6 | `RebarHookOrientation` removed in 2027; `BarTerminationsData` member names from third-party docs. | `create_rebar_from_curves` probes both; failure text names the overload tried; Wizard reports per host. |
| R7 | `RebarBarType.BarMassPerUnitLength` internal unit unknown (G15). | Weight source shown in the UI; falls back to `T3_WeightPerMetre` / table when the converted value is absurd (outside 0.1–20 kg/m). |
| R8 | `PostableCommand` names differ per release (StructuralRebar / InsertCoupler / ReinforcementNumbers not seen in 2027 list). | Candidate lists + `E-POST` ribbon fallback; G10 log corrects the JSON. |
| R9 | Keyboard shortcut XML format unverified. | Template generated with empty shortcuts and ids only from the spike; docs explain manual import. |
| R10 | Type split/merge on rename (A4, G13) may behave differently via API than UI. | Rename per type, report `diff_type_split`, never silent. |
| R11 | Performance on 2 000+ rebar: `GetCenterlineCurves` per bar in BVBS / bbox per bar in Check. | Index built once per window; progress + Stop everywhere; Check `bbox` rule runs only for the `bbox` chip or on demand (`Rescan` with that chip active). |
| R12 | Modal windows block `uidoc.ActiveView` changes (Clone "Open sheet"). | Deferred to script.py after the dialog closes. |
| R13 | Node/resvg missing on the builder's machine for icons. | Builders write only `icon.svg`; coordinator renders. |
