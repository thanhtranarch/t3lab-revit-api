# IFC-SG Suite: UI consistency plan (2026-10-02)

Owner request: "lên phương án consistent UI của tool này", based on two screenshots of
the Subtype Assigner page and the Compliance Checker page. The owner approved going ahead ("PROCEED").

- **X** = `T3Lab.extension/lib/GUI/Tools/IFCSG.xaml`
- **Py** = `T3Lab.extension/lib/GUI/IFCSGDialog.py` (`IFCSGSuiteWindow`)
- **S** = `pyRevit UI Design System/T3Lab.Styles.xaml`

Line numbers are from before the edits.

Status: **Phase 1 + 2 implemented 2026-10-02** · Phase 3 pending · **NEEDS VERIFICATION in Revit**
(`check_xaml_wpf.ps1` not run yet either; it needs Windows).

Deviations from the plan, made while implementing Phase 1 + 2:
- The empty-state overlay margin is written as `Margin="16"`, not `{StaticResource T3.Pad.Panel}`.
  `audit_t3`'s spacing check reads the "3" in "T3" as a margin value; the value is the same.
- During a run, the config combo, Save As, Delete and both Import buttons are locked as well as
  the rail. Without this, deleting the last config mid-run would leave the result header with no config.
- A config that fails to load is cleared, so the previous one never runs under the new name.
- Phase 3 still to do: hex colours and Unicode icons in `_render_results` / `_refresh_tree` /
  `_make_comp_listitem` / `_style_assigner_column_headers`. The `dgTypes` columns add up to 780 px with
  no `*` column, so they get clipped at MinWidth.

## 1. Current state

- The file is fully T3 (synced `T3 STYLES` block, 0 drift). `audit_t3`, `audit_tools` and
  `audit_wiring` are all green.
- **The gates are green, but they can't see these problems:**
  - The empty-state check is per file, not per list: one `T3.Empty` anywhere satisfies it.
  - Nothing compares the two pages with each other or checks where actions sit.
  - Visuals built in Python are never scanned.

| # | Region | Assigner | Checker | Sev |
|---|---|---|---|---|
| 1 | Footer actions | Run Check (Primary, `IsDefault`) and Export are global (X:2239–2242). Enter in the filter box may run a check. | Export is enabled with no results and fails silently (Py:2343). Run Check is enabled with no config and returns silently (Py:1948). | P1 |
| 2 | List rows | Python builds 2-line items (Py:1326–1367) inside a fixed 26 px `T3.ListBoxItem` → the second line is clipped | — | P1 |
| 3 | Page header | Present (X:1798–1819) | **Missing** (X:1968) | P2 |
| 4 | Page title | `txtHeader` is the title, but Python overwrites it with "Loaded: x.xlsx…" | — | P2 |
| 5 | Status line | Shows the Checker's "Config loaded…" at open | — | P2 |
| 6 | Empty states | `lstComponents_empty` and `dgTypes_empty` are always Collapsed | `tvCategories` and `spResults` have none | P2 |
| 7 | Placeholder as header | `txtCompName` "Select a component from the list" in Title style | `txtResultHeader` "Load a config and click Run Check" | P2 |
| 8 | Destructive action | — | Delete is a filled red button next to Save | P2 |
| 9 | Filter state | — | Failed is always red; the real default is "all" and nothing shows it | P2 |
| 10 | Clipped header | — | "Disciplines / Categ…" is squeezed by Expand/Collapse | P2 |
| 11 | Python-built visuals | Hex colours, Bold, 9.5–12 px; grid columns with no `*` | Hex colours, Unicode ✔✘⚠ icons, 9–12 px | P2 |
| 12 | Left pane width | 340 / Min 260 | 280 / Min 220 | P3 |
| 13 | Pane strips | Mixed paddings and heights; count strip on the left only | Two stacked strips on the right; no count strips | P3 |
| 14 | Labels | "Target Subtype:" inline with colon | "Configuration:", "Filter:" inline with colon | P3 |
| 15 | Options strip | A 40 px strip with a hint sentence above the footer | — | P3 |
| 16 | Tree / KPI | — | Tree unstyled; KPI strip on Surface; dots on the neutral tiles; "NO ELEM" | P3 |
| 17 | Window | Literal `CornerRadius="12"`, MinWidth 900, no status dot, rail tooltips that don't match the page titles | | P3 |

## 2. Target page shell (identical on both pages)

```
Row0 HEADER BAND   T3.TitleBar (48): Title · rule · Subtitle | "bring data in" actions (Secondary)
Row1 TOOLBAR       Surface, bottom 1px, 16,8 — UPPERCASE T3.Label above each control group;
                   page-level options live here (no strip above the footer)
Row2 KPI (opt.)    Sunken, Caption + Display tiles, 1px rules, no dots — Checker only
Row3 SPLIT BODY    one Grid 4 rows × [320 | 1px divider | *] so strips align across the divider
   r0 pane header  T3.ListHeader (26): static noun left / dynamic context right
   r1 pane toolbar Surface, 16,8, control row 30
   r2 content      list + T3.Empty overlay (DataTrigger on HasItems — rule 25)
   r3 count strip  T3.ListHeader, top border
FOOTER (global)    Copyright · rule · status dot · status text | progress | PAGE action group
```

**Footer action groups:**
- The two groups switch with a `DataTrigger` on the selected tab, in XAML only.
- Assigner: Auto Assign All (Secondary).
- Checker: Export Results (Secondary, disabled until there are results) and **Run Check**
  (the only Primary in the file, `IsDefault`, disabled until a config is loaded).
- Run Check is collapsed on the Assigner page, so Enter there does nothing.

## 3. Phases

| Phase | Scope | Risk |
|---|---|---|
| **1: quick win** (XAML only) | Window radius token, MinWidth 1000, rail tooltips, per-page footer groups. Assigner: header band, multiline list items, working empty states, "No component selected". Checker: new header band (Import XML/Excel move into it), Delete becomes a ghost button with a trash icon, no permanent red filter, KPI strip on Sunken without dots, unclipped tree header, `T3.TreeView`, empty state, pane width 320/260. | Low |
| **2: shared shell** (+ ~40 lines of Python) | Same 4-row grid on both pages. Toolbar labels above controls. Write options move into the Assigner toolbar. Filter becomes `T3.Chip` radio buttons (All is the default). Count strips. Status text remembered per page. Rail disabled during a run. Delete confirm defaults to No. Run/Export enabled only when they can work. | Low–medium |
| **3: Python-built content** | Restyle the list items, tree items and the results area through `FindResource("T3.*")`: no hex, no Unicode icons, `T3.Dot` + status word, `T3.Meter` for the pass rate. Grid columns get `*` widths. | Medium (no gate covers it) |

**Defaults chosen for the open questions** (change any of these if you want):
1. Run Check stays the file's only Primary button; Auto Assign All is Secondary.
2. Delete config: Ghost button with a trash icon plus a Yes/No confirm (default No). It is not moved into an
   overflow menu, because T3 has no menu component (that would be a DESIGN SYSTEM GAP).
3. Results area: restyle the existing StackPanel in phase 3 rather than rebuild it as a
   virtualised `T3.DataGrid`. The rebuild would scale better to very large configs.

## 4. Revit QA (100% and 125% scaling)

- The footer actions swap per page, and Enter in the Assigner filter does not run a check.
- The empty states appear and disappear when you load the Excel, select a component, load a config, and apply a filter.
- Two-line component items are not clipped, and the tree header is not clipped.
- Delete still asks first, and the default answer is No.
- The status text follows the page. The rail is disabled during a run; Pause/Stop work.
- The chips show the active filter, and a re-run resets it to All.
- The write options still drive Apply and Auto Assign. Ctrl+Z undoes one step.
- The pane strips line up across the divider at MinWidth 1000.
