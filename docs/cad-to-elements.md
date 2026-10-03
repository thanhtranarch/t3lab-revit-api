# CAD to Elements — modes, layout and research note

Tool: ribbon **T3Lab Model › Model & Datum › CAD to BIM › CAD to Elements**
(`T3Lab.extension/T3Lab Model.tab/Model & Datum.panel/Create Elements.pulldown/CADToElements.pushbutton/`).

| Part | File |
|------|------|
| Window (UI only) | `T3Lab.extension/lib/GUI/Tools/CADToElements.xaml` |
| Dialog (wiring, plans, confirm/report) | `T3Lab.extension/lib/GUI/CADToElementsDialog.py` |
| Pure rules (what gets created) | `T3Lab.extension/lib/Snippets/_cad_geometry.py` |
| Revit API (scan, types, creators) | `T3Lab.extension/lib/Snippets/_cad_revit.py` |
| Tests | `dev/test_cad_to_elements_geometry.py`, `dev/test_cad_to_elements_layout.py`, `dev/test_cad_to_elements_units.py` |

**Units (2026-10-02).** Every length in the options card follows the project's
length unit (`Snippets/_units.py`, read when the window opens): labels read
"HEIGHT (MM)" / "HEIGHT (FT-IN)", defaults (kept in mm in the dialog's
`LENGTH_FIELDS` and `geo.MEP_CATEGORIES`) are shown in that unit, and typed text
is parsed by it ("1200 mm", "1.2 m" and 3'-6" work in any project). The level
combo shows elevations in the project unit. Column size rounding and the
per-size column type names use mm (metric) or inches (imperial), never m or ft.
Still metric by design: the beam rule (width rounded to 50 mm, depth table) and
the per-thickness wall type names ("Generic - 200mm").

## 1 · Before (2026-10-01)

Seven modes: Wall, Floor, Beam, Duct, Pipe, Cable Tray, Conduit. Each mode was
an independently laid-out `StackPanel` (940×820 window) with its own copy of
the layer card, so:

- the options card had 2–4 rows depending on the mode, and some modes had a
  "CREATE MODE / LINE MODE" card while Pipe and Conduit did not, so **the layer
  list jumped up or down on every mode switch**;
- the Part/DirectShape fields appeared and disappeared inside a mode, so
  that layout jumped too;
- the layer list was capped at 320 px inside a scrolling page, which left a
  big empty band under it;
- layers had to be scanned per mode with **Refresh**, and the selection was lost
  when the mode changed;
- there were 7 copies of the AI Select / Select All / Clear / search toolbar;
- the footer order was Refresh · **Run (primary)** · Close, with the primary
  not right-most;
- the maximize button was wired twice, by `T3WPFWindow` and again by the
  dialog, so each click toggled twice and did nothing.

Logic bugs found while porting:

- **Beams:** the scan re-applied each nested block's `Transform` to geometry that
  `GetInstanceGeometry()` had already transformed. The old wall extractor
  documents exactly this bug. Beams from rotated or offset imports landed in
  the wrong place.
- **Beams:** the line was placed at level + offset and `Z_OFFSET_VALUE` was also
  set to the offset, so the offset was applied twice.
- **Beams:** only near-horizontal and near-vertical pairs were found (±10°).
- **Floors:** an outline inside an outline became a second floor on top of the
  first instead of an opening.
- **Floors:** only one level of block nesting was read, and geometry outside
  blocks went to a fake layer called "Default".
- **Parts:** the DirectShape was placed at the offset only, ignoring the level
  elevation.
- **Walls:** the type copy set the *structure* layer to the full CAD
  thickness, so a multi-layer base type ended up thicker than the CAD.
- **All modes:** errors went to `print()` or to `pyrevit.forms.alert`, with
  bare "Created: n" messages and no confirm before changing the model.
- **Dead code:** about 1,200 lines of three unreachable sub-windows
  (`_CADtoWallWindow`, `_CADtoFloorWindow`, `_CADtoBeamWindow`) and their four
  XAMLs (`CadtoWall`, `CadtoFloor`, `CadtoFloorLayerItem`, `CADtoBeam`). All
  were removed.

## 2 · After — one fixed shell

Window class **L 1000×620**, no `SizeToContent`.

```
┌ title bar 48 (logo · title · AI badge · min/max/close) ───────────────────────┐
│rail│ A  CAD SOURCE [combo]                 │ LEVEL [combo]                     │
│ 58 │ B  Mode title / one-line description                                     │
│    │ C  OPTIONS card 424 px                │ D  CAD LAYERS (fills the rest)   │
│    │    R0 52  CREATE AS (radios)          │   search · AI Select · Select    │
│    │    R1 60  type / view / style combo   │   All · Clear                    │
│    │    R2 60  field | field               │   ☐ LAYER ............ COUNT    │
│    │    R3 60  field | field               │   (empty state)                  │
│    │    R4 60  field | field               │   "3 of 42 layers · 318 lines"   │
│    │    R5 24  checkboxes                  │                                  │
│    │    R6 24  checkboxes                  │                                  │
│    │    R7 *   note                        │                                  │
├ footer 48: © · ● status [progress]          Close · Rescan · [Create 12 Walls] ┤
```

- **A** is shared by every mode, and the selected CAD and level persist.
  Layers are scanned once, when the window opens or the CAD changes (rule S7).
  F5 or **Rescan** reloads files, levels, types and layers.
- **C** contains nine `opt_<mode>` grids that all use the **same** row and
  column skeleton and sit in the same cell; switching mode only changes which
  one is visible. Row 0 is always the CREATE AS choice and row 1 always the type
  selector. Fields that do not apply inside a mode are **disabled, never
  collapsed**. `dev/test_cad_to_elements_layout.py` enforces all of this.
- **C and D sit side by side** rather than stacked. At the standard 620 px
  height, stacking left the list less than 100 px; side by side, the list fills
  the full body height and the empty band is gone.
- **D** is one DataGrid shared by all modes, with a select-all header (rule
  23), checkboxes read through the string bridge (rule 24), an empty state,
  and a tally strip inside the same panel (rule 19). Each mode keeps its own
  ticked layers. The count column changes meaning with the mode: LINES,
  OUTLINES, SHAPES or CURVES.
- **Footer** is identical in every mode: Close (ghost) · Rescan · one primary
  on the right whose label names the action ("Create Walls", "Create Cable
  Trays"). `MinWidth="152"` stops the label change from moving anything.
- **Rail:** tiles are grouped Architecture · Structure · Datum/Lines · MEP.
  Each tile has a tooltip, and the selected tile is the T3.Rail.Tile checked
  state. Icons are Segoe MDL2 glyphs (`T3.Icon.Rail`, one glyph per concept
  in the standard's table): Tiles (wall courses), TiltUp (floor plane),
  TiltDown (ceiling grid), Home (room), Bank (columns), IBeam (beam section),
  MapPin2 (grid head), Flow (line with two end grips) and Wire (MEP run).
  The four MEP tiles became one **MEP Runs** tile with Ducts / Pipes / Cable
  trays / Conduits as its CREATE AS choice, because their options were the same
  apart from system and height. This keeps nine tiles inside the 524 px body.
  Each category remembers its own width, height and offset.
- **Every run** builds a plan, shows a P5 confirm with the count ("Create 12
  walls on Level 1?", details: pairs, sizes, types), runs **one transaction**
  that rolls back if nothing was made, and reports created, failed and skipped
  counts plus the number of Revit warnings dismissed. Errors name what went
  wrong, where, and what to do next.

## 3 · Modes

| Mode | Source geometry | Creates | Notes |
|------|-----------------|---------|-------|
| Walls *(existing)* | paired parallel lines ≤ 610 mm apart | `Wall.Create`, type copied per thickness from a chosen base type, or DirectShape parts | Pairing at any angle; collinear merge now also joins overlapping pieces, never across layers |
| Floors *(existing)* | closed outlines | `Floor.Create` (2022+) with **openings** from nested outlines, or parts | Outlines = closed polylines + circles + loose lines/arcs/splines that close (face tracing) |
| **Ceilings** *(new)* | closed outlines | `Ceiling.Create` (2022+) with openings, height offset, or parts | Same outline engine as floors |
| **Rooms** *(new)* | any linework + closed outlines | room separation lines in a floor plan of the level, plus one room per enclosed outline (or lines only / rooms only) | Nested outline = its own room; outlines < 1 m² or narrower than 500 mm (wall strips) are skipped; rooms Revit cannot enclose are deleted again; outlines already holding a room are skipped |
| **Columns** *(new)* | closed rectangles (any rotation) and circles, 100–3000 mm | structural or architectural columns, type copied per size (`450x600mm`, `D500mm`) via b/h, Width/Depth or d/Diameter, rotated to match, top level or unconnected height | A column drawn twice (outline + hatch boundary) is placed once |
| Beams *(existing)* | paired parallel lines, 50–1500 mm apart, any angle | structural framing, type per size (width rounded to 50, depth from width), or parts hanging under the top offset | Transform and double-offset bugs fixed |
| **Grids** *(new)* | axis lines ≥ min length | `Grid.Create`, dashed axes joined, numbered 1… left to right and lettered A… bottom to top (I/O skipped, AA after Z), optional end extension, grid type | Axes that already have a grid are skipped; names already used keep Revit's name |
| **Lines** *(new)* | every curve | model lines (at level + offset) or detail lines in the active plan or drafting view, on a chosen line style | Arcs, circles and splines kept |
| MEP Runs *(existing, merged)* | single lines or parallel pairs | ducts, pipes, cable trays, conduits with system, size, offset and auto elbows | Logic unchanged; UI unified |

## 4 · Considered and rejected

| Idea | Why not (for now) |
|------|-------------------|
| Grid names from CAD bubble text | The Revit API does not expose the text of an imported DWG (no text in `ImportInstance` geometry). Names are generated deterministically instead. |
| Doors / windows from blocks | Block names are office-specific and are not reliably exposed for imported DWGs. Swing and width have to be read from arcs, and hosting needs each opening to land in a wall created in the same run. Too fragile to be trustworthy. |
| Topography / Toposolid from text elevations | Needs CAD text (not exposed), and `Toposolid` is 2024+ only. |
| Railings from polylines | `Railing.Create` needs a connected, ordered path and a valid railing type and host. Possible, but low value next to the modes above; a candidate for later. |
| Area boundary lines | Needs an area plan per area scheme; narrow use. Rooms cover the common case. |
| Column detection from block instances | Blocks are read through their geometry, so columns drawn inside blocks are found as rectangles or circles anyway. |
| Keeping per-mode Refresh | Scanning once and caching per layer is faster and is what made a shared list possible. |
| Auto-sorting layers with matches first | It would reorder the list on every mode switch, which is the kind of jumping this rework removes. Rows stay alphabetical and the count column shows relevance. |

## 5 · Needs verification in Revit

Static gates and unit tests pass; nothing here has run in Revit yet.
See the checklist in the PR or session report. Main risks:
`Floor.Create`/`Ceiling.Create` with hole loops, `NewRoomBoundaryLines` with
arcs, column size parameter names in office families, and detail-line Z in
plan views.
