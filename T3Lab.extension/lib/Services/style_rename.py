# -*- coding: utf-8 -*-
"""Style Manager (ManaStyles) rename operations — the ONE Revit code path.

Both the inline rename (double-click / F2 / Enter on the NAME cell) and the
footer "Rename" button of the Line Styles, Line Patterns and Fill Patterns
tabs call these functions. Each one runs exactly ONE Transaction, so Ctrl+Z
reverts the rename in one step, and a failure rolls everything back
(`disposing()` rolls back a transaction left open by an exception).

Names must already be validated with `Services.style_naming`.
The dialog is modal (ShowDialog), so these run inside the Revit API context.
"""

from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    CurveElement,
    GraphicsStyleType,
    BuiltInCategory,
)

from Snippets._compat import disposing


def _eid_int(element_id):
    """ElementId -> int on Revit 2022–2027 (`Value` from 2024, `IntegerValue` before)."""
    if element_id is None:
        return -1
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def rename_line_pattern(doc, element, new_name):
    """Rename a LinePatternElement in place. Returns the new name."""
    with disposing(Transaction(doc, "Rename Line Pattern")) as t:
        t.Start()
        element.Name = new_name
        t.Commit()
    return new_name


def rename_fill_pattern(doc, element, new_name):
    """Rename a FillPatternElement in place. Returns the new name.

    `Element.Name` is the documented setter; if this Revit build refuses it
    for fill patterns, fall back to writing a renamed copy of the FillPattern
    back with `SetFillPattern` — still inside the same transaction.
    """
    with disposing(Transaction(doc, "Rename Fill Pattern")) as t:
        t.Start()
        try:
            element.Name = new_name
        except Exception:
            pattern = element.GetFillPattern()
            pattern.Name = new_name
            element.SetFillPattern(pattern)
        t.Commit()
    return new_name


def _curves_using(doc, subcategory):
    """Every CurveElement whose line style belongs to `subcategory`."""
    target = _eid_int(subcategory.Id)
    found = []
    with disposing(FilteredElementCollector(doc)) as col:
        for curve in col.OfClass(CurveElement):
            try:
                style = curve.LineStyle
                cat = style.GraphicsStyleCategory if style is not None else None
            except Exception:
                continue
            if cat is not None and _eid_int(cat.Id) == target:
                found.append(curve)
    return found


def rename_line_style(doc, subcategory, new_name):
    """Rename a line style (a subcategory of OST_Lines).

    Revit has no API to rename a category, so — like the tool always did —
    this creates a subcategory with the new name and the same colour, weight
    and pattern, moves every line onto it and deletes the old one. All in ONE
    transaction: if any line cannot be moved the whole rename is rolled back,
    because deleting the old style would otherwise take those lines' style
    with it.

    Returns (new_subcategory, moved_line_count).
    """
    lines_category = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
    curves = _curves_using(doc, subcategory)

    with disposing(Transaction(doc, "Rename Line Style")) as t:
        t.Start()
        new_sub = doc.Settings.Categories.NewSubcategory(lines_category, new_name)
        try:
            color = subcategory.LineColor
            if color is not None:
                new_sub.LineColor = color
        except Exception:
            pass
        weight = subcategory.GetLineWeight(GraphicsStyleType.Projection)
        if weight:
            new_sub.SetLineWeight(weight, GraphicsStyleType.Projection)
        pat_id = subcategory.GetLinePatternId(GraphicsStyleType.Projection)
        if pat_id is not None and _eid_int(pat_id) > 0:
            new_sub.SetLinePatternId(pat_id, GraphicsStyleType.Projection)

        moved = 0
        if curves:
            new_style = new_sub.GetGraphicsStyle(GraphicsStyleType.Projection)
            failed = 0
            for curve in curves:
                try:
                    curve.LineStyle = new_style
                    moved += 1
                except Exception:
                    failed += 1
            if failed:
                raise RuntimeError(
                    "{} of {} line(s) could not be moved to the new style "
                    "(for example lines inside a group). "
                    "Nothing was renamed.".format(failed, len(curves)))

        doc.Delete(subcategory.Id)
        t.Commit()
    return new_sub, moved
