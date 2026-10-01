# FamiGen: create a family from a description

FamiGen's AI / JSON mode generates a definition to review before creating Revit
geometry. It uses the provider already configured through T3Lab AI Mode.

1. Open **FamiGen → AI / JSON** and choose the target family category.
2. Describe the parts, shapes and dimensions in **millimeters**. For example:
   “Dining table 2400 × 1200 × 750 mm; tabletop 40 mm thick; four round legs,
   radius 30 mm.” Add placement and connection details when they matter.
3. Click **AI Generate**. The description, category and current draft are captured
   together. The dependent controls remain disabled while the model responds.
4. Review the generated JSON and its dimensions. **Undo AI** restores the exact
   previous draft, including an empty draft.
5. Click **Create Family**. In a project, the existing template/output-folder
   workflow creates an RFA; in a family document, the existing builder adds the
   geometry there. Inspect the result and any skipped-part messages in Revit.

**Copy Prompt** copies the same schema contract and category guidelines used by
AI Generate, with the description when one is present, for an external model.

## Checked before replacing a draft

The response must be one object containing `family_name`, the exact selected
`family_category`, and a nonempty `geometry` array. Supported forms are
`Extrusion`, `Blend`, `Revolution`, `Sweep` and `Cylinder`; supported curve entries
match the existing Revit builder. Coordinates are `[x, y, z]` in millimeters and
angles are radians. Checks reject unsupported types, malformed fields,
nonfinite/boolean numbers, nonpositive radii, exact zero-length axes/segments and
invalid explicit offset/angle ordering.

For a parsed response with schema errors, AI Generate makes at most **one repair
request**, carrying the original description, category and specific error paths.
A provider error, unparseable response or invalid repair leaves the previous
draft untouched. A changed draft or closed window also prevents applying a late
response. Requests do not automatically create or modify a family.

## Limits and validation

These checks validate representation and basic degeneracy, not profile closure,
self-intersection, connectivity, template availability or Revit buildability.
Revit performs the final geometry checks. Parameter values can be assigned only
to parameters already present in the template; this change does not add new
parametric constraints or create new family parameters.

Development checks:

```bash
python3 dev/test_family_schema.py
python3 dev/test_famigen_ai_workflow.py
python3 dev/audit_t3.py --quiet
python3 dev/audit_tools.py --quiet
```

The 23 schema/workflow tests use mocked providers and controls. Live model
responses, template-based RFA creation and the rendered UI still require a
Windows/Revit check.
