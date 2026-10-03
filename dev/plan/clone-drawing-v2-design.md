# Clone Drawing V2 — design (Tekla "Clone drawing" fidelity on Revit 2025–2027)

> Date: 2026-10-03 · Status: **DESIGN + BUILD** (code in this branch follows this document;
> where they differ, this document wins and the code is wrong).
> Supersedes `rebar-tekla-implementation-spec.md` §3.4 / §5.3 for Clone Drawing only.
> Owner's bar: *"Clone drawing is a system that needs careful logic and must guarantee the
> full functionality of Tekla."* Everything Tekla clones is either cloned, or appears in the
> report as a named, reasoned exception. Nothing is dropped silently; nothing that exists is
> deleted without an explicit, confirmed option.
>
> Legend: `[PURE]` testable outside Revit · `[REVIT]` touches the API · `[NV]` needs
> verification in Revit (runtime probe or fallback with a user-visible reason) ·
> `[CITED]` backed by an external page listed in §8.

---

## 1 · What Tekla "Clone drawing" does — user-visible capabilities `[CITED]`

Collected from Tekla User Assistance (2021 → 2026 pages, §8). "Drawing" = assembly /
cast-unit / single-part drawing; GA-drawing cloning is out of scope (Revit assemblies have
no GA equivalent).

| # | Capability (Tekla wording) | Source |
|---|---|---|
| K1 | **Master drawing catalog — cloning templates.** A finished drawing is added to the catalog as a *cloning template*; templates may come from the current model or from other models (`XS_CLONING_TEMPLATE_DIRECTORY`) and from a template library (`XS_DRAWING_TEMPLATES_LIBRARY`). | T-1, T-5, T-7 |
| K2 | **Clone from Document manager.** Finalise, save and close the template drawing; select target parts / assemblies / cast units in the model; pick the drawing in Document manager; *Clone* → *Clone selected*. | T-3 |
| K3 | **One master → many targets.** From the catalog: *Create drawings* (selected objects) or *Create drawings for all parts*; "Tekla Structures by default creates only one drawing for each object". | T-5, T-6 |
| K4 | **Same main part type rule.** Cloned assembly / cast-unit drawings "must have the same type of main part as the assembly or cast unit from which the original drawing was created". | T-1, T-3 |
| K5 | **Numbering prerequisite.** Parts must be numbered; Tekla prompts to number before cloning. Identical (same position number) assemblies share one drawing — cloning is for *similar*, not identical, objects. | T-5 |
| K6 | **Clone settings per object type** (Clone Drawing dialog / catalog *Drawing creation* tab): *Dimensions* → Clone / Create / Ignore; *Other marks (all building object marks)* → Clone / Create / Ignore; every other object → Clone / Ignore. | T-1, T-3, T-5 |
| K7 | **Objects that can be cloned:** dimensions · weld marks (drawing / model) · level marks · revision marks · annotation objects · texts · symbols · graphical shapes · text files · DWG/DXF files · hyperlinks · manually created section and detail views · included single-part drawings · user-defined drawing attributes. | T-1 |
| K8 | **View-specific dimension creation method** (per view: *Clone* / *Do not create*) overriding the global dimension setting. | T-4 |
| K9 | **Views keep placement:** main views preserve shortening; section views keep their location; view placement avoids overlap / outside frame (2025); views wider than the sheet are adjusted to fit (2026, `XS_DRAWING_UPDATE_VIEW_PLACING`). | T-8, T-9, T-10 |
| K10 | **Part mapping.** "Tekla Structures clones marks that can be mapped to the original drawing and creates new marks for parts that cannot be mapped." Fewer parts → dimensions to missing parts are removed; more parts → auto-dimensioned when `XS_INTELLIGENT_CLONING_ADD_DIMENSIONS=TRUE` (default TRUE). | T-1, T-11, T-12 |
| K11 | **Associativity preserved.** Marks keep location + association to the right object; rebar marks keep leader type and association; dimension tags keep content; *Refresh associativity* re-creates associative rules without re-creating the drawing. | T-8, T-13 |
| K12 | **Mirrored parts limitation.** "Cloning of annotations to mirrored objects created with the Mirror command in the model does not produce accurate results." | T-14 |
| K13 | **Section views resize** to fit new parts; section marks far from parts are deleted; detail views no longer associated with non-existent objects (2025/2026). | T-9, T-10 |
| K14 | **Report / status.** Cloned drawings are flagged *"Drawing was cloned"* in the Changes column; the user is told what to check (marks location, view size / orientation / placement, missing or wrong dimensions). | T-3, T-11 |
| K15 | **Update when the model changes** is Tekla's generic associative *drawing update*, not a re-clone; drawings go *needs update* and update from the model. Cloning again produces a *new* drawing. | T-9 |
| K16 | **`XS_DRAWING_CLONING_IGNORE_CHECK`** — clone even when every original part was deleted and the position number is unchanged. | T-15 |
| K17 | **Clone selected** (inside one GA drawing): clone annotations, sketch objects and representations between assemblies "with the same type and similar shape". | T-14 |
| K18 | **Settings saved with the template** (catalog *Drawing creation* tab) → reusable presets; template drawing properties are *not* editable through the catalog. | T-5, T-6 |
| K19 | **Representations** (line colour / type, hatches) cloned by *Clone selected*. | T-14 |
| K20 | **Cannot clone** multidrawings; GA drawings only via Document manager. | T-1 |

---

## 2 · Capability matrix — Tekla → Revit 2025–2027

Status: **native** (Revit does it by itself) · **cloned** (this tool does it) · **limited**
(done with stated limits) · **impossible** (why).

| Tekla | Revit status | How / why |
|---|---|---|
| K1 catalog, current model | **cloned** | *Source* list = every assembly that owns ≥1 view or sheet (`View.AssociatedAssemblyInstanceId`). Clone settings are **presets** (§4) saved per user in `%APPDATA%\T3LabAI\clone_drawing_presets.json`. |
| K1 catalog, other models / library | **impossible** | Assembly views cannot be copied between documents (`ElementTransformUtils.CopyElements(Document→Document)` refuses view-owned assembly views; the assembly does not exist in the other model). Presets (settings) do travel: the JSON file can be copied between machines. Listed in the Options callout. |
| K2 clone from Document manager | **cloned** | Page 1: pick the source assembly; targets from the grid, from the current Revit selection (*From selection*) or picked in the model (*Pick in model*, `Hide()` → `PickObjects` with an `AssemblyInstance` filter → `Show()`). |
| K3 one → many; "one drawing per object" | **cloned** | Multi-tick targets, one `TransactionGroup` per click; per target "has drawing" policy (§5) enforces one drawing per assembly unless *Create new set* is chosen explicitly. |
| K4 same main part type | **cloned** | Hard rule: target `NamingCategoryId` must equal the source's → otherwise *Will skip: different naming category (Tekla: main part type)*; shown in the grid, never silently. *All* chip still lists them so the user sees why. |
| K5 numbering | **native** | Revit assembly types are numbering: instances of one `AssemblyType` are identical and **share one set of views** (`AssemblyViewUtils.AcquireAssemblyViews` moves view ownership between *siblings of the same type*; a type has at most one owning instance `[NV G1]`). Same-type targets are reported *same type — shares the source's views*. |
| K6 Dimensions: Clone | **limited** | T3 re-creates linear dimensions whose every reference is a planar **face of a matched host element** (face matched by local normal + distance, 5 mm, mirror-aware). Dimensions on rebar bars, on edges/points, on linked elements, angular / radial / arc-length / diameter → reported *unmatched: <reason>*. |
| K6 Dimensions: Create | **impossible** | Revit has no automatic dimensioning for assembly views (no `XS_INTELLIGENT_*` equivalent). The Options page shows the row disabled with this text. |
| K6 Dimensions: Ignore | **cloned** | Setting *Skip*. |
| K6 Other marks: Clone | **cloned** | T3 re-creates every `IndependentTag` (incl. material / keynote / rebar tags) that tags **one whole element** which has a match; tag type, orientation, leader, leader end condition, head position (moved into the target frame) kept. Rebar **set-position** tags (`REFERENCE_TYPE_…` sub-element), multi-element tags, tags on linked elements → *unmatched*. |
| K6 Other marks: Create | **cloned** | *Clone + create for unmatched*: for each category the source tagged in that view, every **unmatched** target member of that category gets a new tag of the **same tag type**, head at the element's centre projected into the view, leader as the source's tags. This is Tekla's "creates new marks for parts that cannot be mapped". |
| K6 Other marks: Ignore | **cloned** | Setting *Skip*. |
| K7 weld marks | **impossible** | Revit assemblies carry no welds (steel-connection welds are not assembly members and have no view annotation). Reported once per run as a named exception when the source view contains `OST_StructConnectionWelds` elements `[NV]`. |
| K7 level marks | **limited** | Spot elevations whose reference is a matched host **face** are re-created with `doc.Create.NewSpotElevation`; spot coordinates / slopes and spot elevations on rebar or on points → *unmatched*. |
| K7 revision marks | **limited** | Revision clouds are copied view-to-view (`OST_RevisionClouds`, keeps `RevisionId`); off by default because it adds the revision to the new sheet. Revision **tags** follow the cloud rule (tag on a copied cloud is re-created by T3). |
| K7 annotation objects / texts | **cloned** | T2 `ElementTransformUtils.CopyElements(view→view)`: `OST_TextNotes`. |
| K7 symbols | **cloned** | T2: `OST_GenericAnnotation` (symbol families). |
| K7 graphical shapes | **cloned** | T2: `OST_Lines` (detail lines), `OST_DetailComponents`, `OST_FilledRegion`, `OST_MaskingRegion`, `OST_InsulationLines`, `OST_IOSDetailGroups`. |
| K7 DWG/DXF files | **limited** | View-specific `ImportInstance`s (`OST_ImportObjectStyles` / `ImportInstance.ViewSpecific`) are copied by T2 when Revit accepts them `[NV]`; linked (not imported) CAD is model-wide and needs nothing. |
| K7 text files, hyperlinks | **impossible** | No Revit annotation kind. Not applicable — listed here, not in the UI. |
| K7 manual section & detail views | **limited** | Sections / callouts drawn inside the source's assembly views that are **not** one of the 9 `AssemblyDetailViewOrientation`s: re-created with `ViewSection.CreateCallout` (callout of a cloned parent) or `ViewSection.CreateSection` (box moved into the target frame) `[NV G17]`; the result's `AssociatedAssemblyInstanceId` is checked and, when it is not the target, the log says *created as an ordinary view (not owned by the assembly)*. Off/on via the *Manual sections & callouts* row. |
| K7 included single-part drawings | **native** | Revit assembly views already show every member; a part list / single-category schedule plays that role and is cloned by T1. |
| K7 user-defined drawing attributes | **cloned** | Sheet: `Drawn By`, `Checked By`, `Designed By`, `Approved By`, `Sheet Issue Date` and every other user-modifiable **text** parameter of the sheet are copied; view: *Title on Sheet* with the mark substituted. |
| K8 view-specific dimension method | **cloned** | Page 1 lists the source views with a per-view tick *Clone dimensions*; untick = *Do not create* for that view (the global row still rules the rest). |
| K9 view placement | **cloned** | Viewports and schedule instances re-placed at the same sheet centre, same rotation, same viewport type, same label offset. Overlap / fit-to-sheet logic: **native** (Revit's viewport has no auto-placement; same positions as the source are what the user wants). |
| K10 part mapping | **cloned** | §3 — members paired by `_rebar.match_by_fingerprint` (kind, shape, bar type, quantity, local centre; nearest within 2·tol; mirror flips); unmatched and ambiguous are reported per element with the reason and listed in *Unmatched*. |
| K10 fewer parts → dims removed | **cloned** | A dimension whose reference has no match is simply not created and reported *unmatched: no matching element in <mark>* (same effect as Tekla's removal, without touching anything). |
| K10 more parts → auto-dimension | **impossible** (dims) / **cloned** (marks) | No auto-dimensioning in Revit; marks for extra parts are covered by *Clone + create for unmatched*. The report lists every extra target member under *unmatched target members* so the user knows what to dimension by hand. |
| K11 associativity | **native** | Re-created tags and dimensions are real Revit references → associative. T2 copies are free annotation in both products. |
| K11 Refresh associativity | **cloned** | *Existing drawings → Add missing* re-runs T2/T3 onto the target's **existing** views, creating only tags / dimensions that are not there yet (dedupe: same tag type on the same element; same reference set for dimensions). |
| K12 mirrored parts | **limited** | *Allow mirrored matches* (default on): member and face matching try x- and y-mirror; a tie is *ambiguous — not guessed*. Text / detail lines are copied with the model→model delta transform, which flips nothing (a mirrored assembly gets un-mirrored text — stated in the Options callout, same limit as Tekla). |
| K13 views resize to parts | **native / cloned** | Crop row: *Fit to target* (Revit's own crop from `AssemblyViewUtils`) or *Copy from source* (source crop moved into the target frame). |
| K14 report | **cloned** | Results page: one row per target, a timestamped log with every created / skipped / failed / unmatched item and its reason, a tally strip, *Copy log*. Each created view gets the comment parameter `T3Lab cloned from <source mark> on <date>` (`VIEW_DESCRIPTION` is left alone; `ALL_MODEL_INSTANCE_COMMENTS` when writable `[NV]`) — Revit's *Drawing was cloned* flag. |
| K15 update when the model changes | **native** (annotations) / **cloned** (re-clone) | Revit tags and dimensions follow the model. Re-cloning uses the *Existing drawings* policy (§5): *Skip* · *Add missing* · *Create new set* · *Replace annotations* (confirmed, deletes annotations only). |
| K16 ignore check | **not applicable** | A Revit assembly whose members are deleted no longer exists; a source without members cannot be chosen (its views are empty). The source list shows member counts so an emptied assembly is obvious. |
| K17 clone selected inside one drawing | **impossible** as such | Revit assembly views show one assembly each; cross-assembly annotation in one view does not exist. The whole tool is K17 at assembly granularity. |
| K18 settings saved with template | **cloned** | Presets (§4): built-in *Tekla default*, *Views only*, *Annotations only* + user presets (name, save, delete). The last used preset is remembered. |
| K19 representations | **limited** | Per-element graphic overrides in the source view (`View.GetElementOverrides`) are re-applied to the **copied** T2 elements and to **matched** members (`SetElementOverrides`) when the row *Graphic overrides* is on. Category overrides come from the view template (native). |
| K20 multidrawings / GA | **not applicable** | No Revit counterpart. |

Count: **native 6 · cloned 19 · limited 8 · impossible 6 · not applicable 3** (rows above; K6 and K7
split into their sub-items).

---

## 3 · Matching algorithm `[PURE]` + `[REVIT]` readers

### 3.1 Frames
Every comparison happens in the **assembly's local frame** (`AssemblyInstance.GetTransform()`),
so two assemblies rotated or placed anywhere compare like-for-like. Model→model mapping for
annotation positions is `transform_between(src, dst) = dst.T · src.T⁻¹`.

### 3.2 Assembly similarity (grid score, pre-run)
`similarity(src_profile, dst_profile)` → 0–100, reasons: category −40 (and a **hard block**,
K4), bounding box side out of ±5 % −30, rebar count differs −20. Member count is shown but not
scored. Chips: *Similar* (≥70 and same category) · *Same category* · *All*.

### 3.3 Member pairing (T3)
`_rebar.match_by_fingerprint(src_items, dst_items, tol_mm, allow_mirror)`:
1. exact fingerprint `(kind, shape, bar type, quantity, cell_x, cell_y, cell_z)` with cells of
   `tol_mm` (default 10 mm);
2. nearest within `2·tol_mm` among same `(kind, shape, bar type, quantity)`;
3. when `allow_mirror`: the x- or y-mirror that pairs the most remaining items;
4. a source whose two best candidates are within 1e-6 mm of each other is **ambiguous** —
   never guessed, reported.
Host members use kind `Element:<Category>` and the type name; the assembly instances pair with
each other so a tag on the assembly itself is re-created. Unpaired **target** members are
collected too (`extra_targets`) for *create for unmatched* and the report.

### 3.4 Face pairing (dimensions, spot elevations)
Faces are read with `ComputeReferences=True` (family geometry from the symbol + instance
transform `[NV G3]`). Key = `(normal_local, signed distance from the element's bbox centre)`.
`match_face` tries exact, then x-mirror, then y-mirror; `FACE_DOT_MIN = 0.999`,
`FACE_TOL_MM = 5`; two hits = ambiguous. A dimension is re-created only when **every**
reference resolves; otherwise *unmatched: <first failing reason>*.

### 3.5 View pairing (existing drawings)
`view_key(spec)`: sheet → `("sheet",)`; schedule → `(kind, category_id)`; section/elevation →
`("view", orientation)`; manual section/callout → `("manual", name-with-mark-swapped)`; 3D →
`("3d",)`. `pair_existing` pairs in order, duplicates pair with duplicates.

### 3.6 Dedupe on *Add missing* / re-clone
- tag: an `IndependentTag` of the same tag type already on the matched element in the
  target view → *exists*;
- dimension: a `Dimension` in the target view whose stable-representation reference set equals
  the would-be set → *exists*;
- spot elevation: same reference + same type → *exists*;
- T2 free annotation: compared by (category, type id, local position within `tol_mm`) →
  *exists*; otherwise copied.

---

## 4 · Clone settings model `[PURE]` — `CloneSettings`

One row per Tekla object type; every row is user-visible on the *Clone settings* page and
saved in presets. Values are strings so presets stay readable.

| Row (`field`) | Choices | Default | Tekla row |
|---|---|---|---|
| `views` Views & sheet | clone · skip | clone | drawing views |
| `crop` Crop box | source · fit | source | views resize (K13) |
| `manual_views` Manual sections & callouts | clone · skip | clone | manual section / detail views |
| `dimensions` Dimensions | clone · skip (*create* shown disabled: impossible) | clone | Dimensions |
| `spot` Spot elevations (level marks) | clone · skip | clone | Level marks |
| `tags` Tags (marks) | clone · clone_create · skip | clone_create | Other marks (Clone / Create) |
| `mra` Multi-rebar annotations | skip (disabled, *not supported — API unverified*) | skip | rebar marks (partial) |
| `texts` Text notes | clone · skip | clone | Texts |
| `symbols` Symbols (generic annotations) | clone · skip | clone | Symbols |
| `shapes` Detail lines, regions, detail items, groups | clone · skip | clone | Graphical shapes |
| `imports` Imported DWG/DXF (view-specific) | clone · skip | clone | DWG/DXF files |
| `images` Images | clone · skip | clone | — |
| `revisions` Revision clouds | clone · skip | skip | Revision marks |
| `overrides` Graphic overrides of copied / matched elements | clone · skip | clone | Representations (K19) |
| `sheet_params` Sheet parameters (Drawn by, …) | clone · skip | clone | UDAs |
| `existing` Existing drawings | skip · add_missing · new_set · replace_annotations | skip | re-clone policy (§5) |
| `tol_mm` Position tolerance | number > 0 | 10 | — |
| `allow_mirror` | bool | true | mirrored (K12) |
| `sheet_pattern` / `view_pattern` | text | `{SourceNumber}-{Mark}` / `{SourceName}` | — |

Presets: `{"version": 2, "last": "<name>", "presets": {"<name>": {...fields}}}` at
`core.paths.user_data_path("clone_drawing_presets.json")`. Built-ins (not saved, not
deletable): **Tekla default** (everything clone, tags clone_create, revisions skip),
**Views only** (views + crop, all annotation skip), **Annotations only** (views skip, existing
add_missing). Unknown fields in a file are ignored; missing fields take defaults; invalid
choices fall back to the default and the status line says *Preset "<x>" had invalid values —
defaults used for <fields>*.

Per-view dimension method (K8) is kept outside presets: `ViewSpec.clone_dims` ticked on page 1.

---

## 5 · Existing drawings — policy (nothing is deleted without saying so)

| `existing` | Target has no views | Target has views |
|---|---|---|
| `skip` (default) | full clone | skipped: *has drawing* |
| `add_missing` | full clone | T1 creates only views / sheet whose `view_key` is absent; T2 / T3 run onto paired existing views with dedupe (§3.6) |
| `new_set` | full clone | full clone next to the existing views; names get ` (2)` … via `unique_name`; the sheet number too. Nothing paired, nothing deleted |
| `replace_annotations` | full clone | **P5 danger confirm** listing the count of annotation elements to delete; in the target's own Transaction *Replace annotations on <mark>*: deletes tags, dimensions, spot dimensions, text notes, detail lines/regions/items/groups, generic annotations, revision clouds, view-specific imports/images **owned by the target's assembly views** — never views, sheets, viewports, title blocks or model elements; then runs like `add_missing` (views that exist are reused) |

With *Views and sheet = Skip* (annotations only) the `skip` policy does not apply — there is
nothing to create, so the annotations go onto the target's existing views (paired by
`view_key`) and a target without views is *skipped: no target views to annotate*.

Same-type targets (K5) are always *skipped: same type — shares the source's views*.
A target of a different naming category (K4) is always *skipped: different naming category*.

---

## 6 · Transaction model

```
TransactionGroup "T3Lab: Clone drawing"                 (one undo step for the click)
  per target (isolated: an exception inside one target never touches the others)
    Transaction "Replace annotations on <mark>"         (only with existing=replace_annotations)
    Transaction "Clone views to <mark>"                 T1 · SubTransaction per view / sheet
    Transaction "Copy annotations to <mark>"            T2 · SubTransaction per view (CopyElements) + per override
    Transaction "Re-create references on <mark>"        T3 · SubTransaction per tag / dimension / spot
  Assimilate()
```
`disposing(...)` wraps every transaction, group, sub-transaction and collector. Progress
callback per target; *Stop* finishes the current target and marks the rest *skipped: stopped*.
Failure of the group itself rolls everything back and raises to the dialog (*nothing was
changed*).

---

## 7 · UI flow (L 1000×620, chip tab strip, modal — D8)

1. **Source & targets** — left column: SOURCE (combo of assemblies with views; summary of
   its views + member counts; list of source views with the per-view *Clone dimensions*
   tick `[K8]`); TARGETS (chips Similar / Same category / All, search, *From selection*,
   *Pick in model*). Right: targets grid ☐ · Mark · Similarity · Why · Members · Views ·
   Sheets · Status. Status pill tells the policy result before running.
2. **Clone settings** — PRESET row (combo + name box + Save + Delete), then the object-type
   rows of §4 as combo boxes with a one-line hint each, EXISTING DRAWINGS radios, MATCHING
   (tolerance, mirror, patterns), callout with the limits (dims create impossible, mirrored
   text, cross-model).
3. **Results** — grid per target (Views · Sheet · Copied · Tags · Dims · Spots · Unmatched ·
   Status), log with filter chips (All · Unmatched · Failed), tally, *Open sheet* (after
   close, R12), *Copy log*.
Primary: *Clone to N assemblies* with P5 confirm stating counts; *Replace annotations* adds a
danger confirm with the delete count.

Messages: what · where · next; counts always; English only.

---

## 8 · Report / log

Every log line: `HH:MM:SS <level> <mark>: <item> — <detail>`; levels ok / skipped / failed /
plain. Per target the log contains: each view created (name, notes such as *kept from view
template: scale*), the sheet (number, viewports placed), T2 per view (count copied; every
element not copied with its category), T3 per element (unmatched reason verbatim from
§3), extra target members (*not in source — no mark cloned; create for unmatched made a tag*
or *add by hand*), replace-annotations delete count, failures with `short_error`. *Copy log*
puts the whole thing on the clipboard.

---

## 9 · Runtime assumptions `[NV]` and their fallbacks (spike probes to add)

| Id | Assumption | In code | Fallback when false |
|---|---|---|---|
| G1 | Instances of one `AssemblyType` share views; only one owns them. | `SAME_TYPE_POLICY = "skip"` | Row *skipped: same type*; flip the constant after the probe (`AcquireAssemblyViews` doc supports it). |
| G2 | `CopyElements(view→view)` wants the model→model delta for assembly views. | `T2_TRANSFORM_MODE = "delta"` | Per-view SubTransaction; refusal → *copy refused by Revit: <msg>*, every element listed. |
| G3 | Face references of family instances are usable from symbol geometry + instance transform. | `_planar_faces` | Dimension → *unmatched: dimension reference is not a face of a host element*. |
| G8 | Crop box of assembly views is writable. | `_apply_view_props` | Note *not copied: crop box (<msg>)*; view still created. |
| G14 | `View.ViewDirection` points towards the viewer. | `VIEW_DIRECTION_TOWARDS_VIEWER` | View reported *orientation unknown*; with *manual sections* on it is re-created as a section box instead. |
| **G17** (new) | A `ViewSection.CreateCallout` / `CreateSection` made for a cloned parent is owned by the target assembly. | `_create_manual_view` | Log *created as an ordinary view (not owned by the assembly)*; the view is still placed on the sheet. |
| **G18** (new) | `doc.Create.NewSpotElevation(view, ref, origin, bend, end, refPt, hasLeader)` accepts a face reference of a host in an assembly view. | `_recreate_spot` | *unmatched: Revit refused the spot elevation: <msg>*. |
| **G19** (new) | `IndependentTag.Create` with `Reference(element)` works for every tag type that tagged a whole element (material / keynote tags may need `TagMode`). | `_recreate_tag` | *unmatched: Revit refused the tag: <msg>*. |
| **G20** (new) | View-specific `ImportInstance` / `ImageInstance` copy with `CopyElements(view→view)`. | T2 categories | Listed per element *not copied: <category> — <msg>*. |
| **G21** (new) | `View.GetElementOverrides` / `SetElementOverrides` work on copied ids returned by `CopyElements` (ids map 1:1 in order). | `_copy_overrides` | *overrides not applied: <msg>*; copied elements keep view defaults. |
| **G22** (new) | Sheet text parameters (`Drawn By` …) are writable right after `AssemblyViewUtils.CreateSheet`. | `_copy_sheet_params` | *sheet parameter <name> not copied: <msg>*. |
| **G23** (new) | `ALL_MODEL_INSTANCE_COMMENTS` on a view accepts the *cloned from* stamp. | `_stamp_view` | silently skipped (the stamp is a convenience, logged once as *stamp not written*). |
| **G24** (new) | `uidoc.Selection.PickObjects` works after `Hide()` of a modal dialog (D8). | `pick_in_model_clicked` | Status *Pick in model is not available here — tick the targets in the grid*. |
| **G25** (new) | Deleting annotation elements of assembly views with `doc.Delete` never removes the view itself (ids filtered by category + `OwnerViewId`). | `delete_annotations` | The delete list excludes every view / viewport / title block category; count is confirmed first. |

Spike additions proposed for `dev/debug/spike_rebar_assembly.py` (not owned here): G17–G25
above, each a try/log/rollback probe on the sample model.

---

## 10 · Tests

- `dev/test_rebar_fingerprint.py` — V1 suite kept (similarity, classify_view, names, plan,
  face matching, member matching, tally).
- `dev/test_clone_drawing_v2.py` — `CloneSettings` defaults / choices / preset round-trip
  (tmp file) / invalid values; `plan_for_target` for all four existing-drawing policies;
  `view_key` for manual views; dedupe helpers; `extra_targets`; `delete_plan` never lists a
  view category; report formatting; built-in presets.
- `dev/test_clone_drawing_import.py` — imports `Snippets._drawing_clone` for real and
  `GUI.CloneDrawingDialog` with `clr` / `System.*` / `GUI.WPF_Base` / `GUI.T3Dialog` stubbed,
  so a module-level `.NET` import error (the V1 crash) cannot ship again; also asserts every
  `x:Name` the dialog touches exists in the XAML.

---

## 11 · Sources

- T-1 Tekla Structures 2025 *Clone drawings* — https://support.tekla.com/doc/tekla-structures/2025/dra_cloning_drawings
- T-2 Tekla 2024 *Clone drawings* — https://support.tekla.com/doc/tekla-structures/2024/dra_cloning_drawings
- T-3 *Clone from Document manager* (2021) — https://support.tekla.com/doc/tekla-structures/2021/dra_cloning_from_the_drawing_list
- T-4 *View-specific dimension cloning* (2021) — https://support.tekla.com/doc/tekla-structures/2021/dra_view_specific_dimension_cloning
- T-5 *Create drawings with cloning templates* (2021) — https://support.tekla.com/doc/tekla-structures/2021/dra_creating_drawings_with_cloning_templates
- T-6 *Master drawing types* (2025) — https://support.tekla.com/doc/tekla-structures/2025/dra_master_drawing_types
- T-7 `XS_CLONING_TEMPLATE_DIRECTORY` (2026) — https://support.tekla.com/doc/tekla-structures/2026/xs_cloning_template_directory
- T-8 *Drawing cloning improvements* (2021 release notes) — https://support.tekla.com/doc/tekla-structures/2021/rel_2021_dra_cloning_improvements
- T-9 *Improved drawing automation — cloning and update* (2025) — https://support.tekla.com/doc/tekla-structures/2025/rel_improvements_in_drawing_cloning_and_update
- T-10 *Enhancements in drawing cloning* (2026) — https://support.tekla.com/doc/tekla-structures/2026/rel_dra_enhancements_in_cloning
- T-11 *What to check in cloned drawings* (2021) — https://support.tekla.com/doc/tekla-structures/2021/dra_checking_and_modifying_cloned_drawings
- T-12 `XS_INTELLIGENT_CLONING_ADD_DIMENSIONS` (2025) — https://support.tekla.com/doc/tekla-structures/2025/xs_intelligent_cloning_add_dimensions
- T-13 *Refresh drawing associativity* (2019i) — https://teklastructures.support.tekla.com/2019i/en/dra_refreshing_associativity
- T-14 *Clone selected annotations or representations* (2026) — https://support.tekla.com/doc/tekla-structures/2026/dra_clone_selected
- T-15 `XS_DRAWING_CLONING_IGNORE_CHECK` (2025) — https://support.tekla.com/doc/tekla-structures/2025/xs_drawing_cloning_ignore_check
- R-1 Revit API `AssemblyViewUtils.AcquireAssemblyViews` (2025) — https://rvtdocs.com/2025/9d899efa-112e-b169-fde8-303f0967593d
- R-2 Revit API `AssemblyViewUtils` — https://rvtdocs.com/2025/4c839bed-9f56-c255-afba-8152c9171a22
- R-3 Spec §2.2 / §2.3 for the remaining API facts (`IndependentTag.Create`, `Viewport`, 2027 changes).
