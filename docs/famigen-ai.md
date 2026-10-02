# FamiGen: AI-modelled families with materials, reviewed before creation

FamiGen's **AI / JSON** panel turns a description (or a proposal from an external
AI over MCP) into a family JSON, shows it as a **3D preview with its materials**,
and only after the user is satisfied builds and saves a complete `.rfa` from the
right category template.

```
 describe ─┐                                    ┌─ Create Family (dialog)
 MCP ──────┼─> family JSON (schema v2) ─> review pane ─┤
 paste ────┘   validate_family_schema    3D + legend   └─ famigen_create_family (MCP)
                                                              │
                                    FamilyGen.builder ────────┘
           template of the category → parts → materials + Material parameters
           → subcategories → parameters → SaveAs .rfa → optional load into project
```

## Using it in Revit

1. Open **FamiGen → AI / JSON** and pick the family category.
2. Describe the object in millimeters, including its finishes, e.g. "Dining table
   2400 × 1200 × 750 mm, 40 mm oak top, four round black steel legs, radius 30 mm".
3. **AI Generate** (AI Mode provider) returns a checked JSON; or paste JSON; or let
   an AI client propose one over MCP (below).
4. Review it on the right: drag to orbit, wheel to zoom, **Fit** to reset. The
   legend lists each material (swatch, name, parts using it, hover for its
   parameter name); the summary shows category, solids/voids, size in mm and
   whether it is ready; problems and warnings appear underneath. The preview
   updates 0.6 s after you stop editing the JSON.
5. **Create Family** builds the family with the category's template and saves
   `<family_name>.rfa` to the output folder (asked once, then remembered). Tick
   **Load into project** to load it (an already loaded family is replaced).
   In a family document opened from the ribbon, the parts are added to that
   document instead. **Undo AI** restores the JSON from before the last AI
   result or MCP proposal.

## Schema v2 (backward compatible with v1)

Contract source: `T3Lab.extension/lib/Intelligence/family_schema.py`
(`build_system_prompt`, `validate_family_schema`, `schema_contract`,
`EXAMPLE_SCHEMA`). v1 files (no materials, numeric length parameters) stay valid.

```json
{
  "schema_version": 2,
  "family_name": "Side Table 500",
  "family_category": "Furniture",
  "materials": [
    {"name": "Oak", "color": "#B08050", "transparency": 0, "smoothness": 40,
     "parameter": "Top Material"},
    {"name": "Black Steel", "color": [40, 40, 44], "shininess": 90}
  ],
  "parameters": [
    {"name": "Top Thickness", "type": "length", "value": 30},
    {"name": "Designer", "type": "text", "value": "T3Lab"}
  ],
  "geometry": [
    {"id": "Top", "type": "Extrusion", "material": "Oak", "subcategory": "Top",
     "profile": [{"type": "Circle", "center": [0, 0, 0], "radius": 250}],
     "extrusion_start": 520, "extrusion_end": 550},
    {"id": "Leg", "type": "Cylinder", "material": "Black Steel",
     "start": [0, 0, 0], "end": [0, 0, 521], "radius": 25}
  ]
}
```

| Part | Rules |
|------|-------|
| `family_category` | One of the 13 template categories (Generic Model, Door, Window, Furniture, Plumbing Fixture, Electrical Equipment, Mechanical Equipment, Specialty Equipment, Casework, Columns, Lighting Fixture, Site, Entourage). Anything else is an error that lists them — no silent fallback. |
| `materials[]` | Unique `name`; `color` `"#RRGGBB"` or `[r,g,b]` 0–255; optional `transparency` 0–100, `shininess` 0–128, `smoothness` 0–100, `parameter` (default `"<name> Material"`). |
| `geometry[].material` | Must name a `materials[]` entry; ignored (warning) on voids. A solid without one keeps the category default (warning). |
| `geometry[].subcategory` | Optional; created under the family category and assigned to the part. |
| `parameters[]` | `name`, optional `type` `length` (mm, default) / `number` / `integer` / `text`, `value` matching it, `instance` (default false). Existing template parameters are set; missing ones are created. |
| names | No `\ : { } [ ] \| ; < > ? `` ` `` ~`, at most 60 characters. |

Geometry forms and curve segments are unchanged: Extrusion, Blend, Revolution,
Sweep, Cylinder; Line, Arc3P/ArcThreePoint, Spline, Arc, Circle, Ellipse;
millimeters and radians.

## What the builder creates

`T3Lab.extension/lib/FamilyGen/builder.py` (Revit API, no WPF), shared by the dialog
and the MCP server — one Transaction per family, rolled back on error:

* **Template**: the category's `.rft` from Revit's family template folders. A
  missing non-hosted template falls back to Generic Model **and re-categorises the
  family**, reported as a warning; a missing Door/Window template is an error.
* **Materials**: one Revit `Material` per entry (reused when the name exists) with
  its colour, transparency, shininess and smoothness; one **type** parameter per
  material in *Materials and Finishes* whose default is that material; every solid
  naming it has its Material associated to that parameter, so finishes change per
  family type. Only the shading colour is set — no render appearance asset.
* **Subcategories** and **parameters** as in the table above.
* **Save** as `<family_name>.rfa` (overwrites), optional **load** into the active
  project with overwrite of an already loaded family, then the family document is
  closed. Nothing is saved when no part could be built.

## MCP tools (Claude Desktop / Codex via the T3Lab bridge, and the in-app assistant)

| Tool | Thread / effect | Contract |
|------|-----------------|----------|
| `famigen_get_schema` | read, no document needed | `{category?, include_guidance?}` → `schema_version`, units, `supported_categories`, forms, segments, `material_rules`, `parameter_rules`, `example`, `workflow`; with `category` also `system_prompt` (with `include_guidance`, the long category design guide). |
| `famigen_propose_family` | Revit UI thread; **model unchanged** | `{schema, note?, open_window?=true}` → invalid: `{success:false, errors[], warnings[], summary, window:"not_opened"}`; valid: `{success:true, proposal_id, warnings[], summary, size_mm, window:"opened"\|"updated"\|"failed"\|"not_opened", next}`. Opens or reuses a **modeless** FamiGen window on the review pane and returns at once. |
| `famigen_create_family` | Revit UI thread; writes `.rfa`, may load | `{proposal_id? \| schema?, output_folder?, load_into_project?=false}` → `{success, saved_path, category, template, built, total, skipped[], warnings[], materials_created[], materials_reused[], material_parameters[], parameters_created[], parameters_set[], subcategories[], loaded_into_project, summary}`; errors `{success:false, error}`. Default folder `Documents\T3Lab\FamiGen`. |

Proposals are stored in `%APPDATA%\T3LabAI\famigen_proposals` (last 50).
Typical flow for an external AI: `famigen_get_schema` → model → `famigen_propose_family`
(repeat until no errors) → user reviews → user presses Create Family, or the AI
calls `famigen_create_family(proposal_id)` after the user approves.

The window MCP opens is modeless: **From CAD** is disabled there and Create
Family runs through an ExternalEvent. While FamiGen is open as a modal window from
the ribbon, Revit does not run external events, so a proposal waits (and the MCP
call times out after 120 s) until that window is closed.

## Development checks

```bash
python3 dev/test_family_schema.py        # contract v1 + v2
python3 dev/test_famigen_ai_workflow.py  # dialog callbacks: AI, create, proposal
python3 dev/test_famigen_preview.py      # tessellation + proposal store
python3 dev/test_famigen_builder.py      # builder orchestration with stub Revit API
python3 dev/test_famigen_mcp.py          # MCP registry/threading drift locks
python3 dev/test_tool_registry.py
```

Still needs Revit: geometry creation per form, material colours in Shaded view,
parameter creation and association, template lookup per language, load into
project, the rendered preview (WPF 3D, orbit/zoom, 100 % and 125 % scaling), the
modeless window opened by MCP, and live AI responses.
