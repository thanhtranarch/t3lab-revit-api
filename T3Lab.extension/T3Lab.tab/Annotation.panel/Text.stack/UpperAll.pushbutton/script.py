# -*- coding: utf-8 -*-
"""
Upper All Text — selection-aware uppercase tool.

Behavior:
- Selection contains Dimensions  -> uppercase overrides on those dims only.
- Selection contains TextNotes   -> uppercase text on those notes only.
- Selection contains both        -> process both kinds, ignore other elements.
- Selection contains other only  -> do nothing (avoid accidental bulk run).
- Selection is empty             -> uppercase project-wide:
    * View names (all non-template views)
    * Sheet names
    * Title block instance text params
    * All TextNotes in the document
    * All Dimension overrides in the document

Author: Tran Tien Thanh
"""

__author__  = "Tran Tien Thanh"
__title__   = "Upper All Text"
__version__ = "2.0.0"

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory,
    Transaction, View, ViewSheet, Dimension, SpotDimension, TextNote, StorageType,
)
from Autodesk.Revit.UI import TaskDialog

# Param types we must NOT uppercase even when StorageType is String.
# (URL strings, image asset paths.) Compared by .ToString() so we keep IronPython
# 2.7 + Revit 2022+ compatibility (ParameterType is deprecated, GetSpecTypeId() is
# preferred, but the .ToString() name remains stable across both APIs.)
_SKIP_PARAM_TYPE_NAMES = frozenset(("URL", "Image"))

def _is_skippable_string_param(param):
    try:
        return param.Definition.ParameterType.ToString() in _SKIP_PARAM_TYPE_NAMES
    except Exception:
        return False

uidoc = __revit__.ActiveUIDocument
doc   = uidoc.Document


# ---------------- helpers ----------------

def needs_upper(text):
    return bool(text) and any(c.islower() for c in text)

def set_string_param(param, new_val):
    if param is None or param.IsReadOnly or param.StorageType != StorageType.String:
        return False
    try:
        param.Set(new_val)
        return True
    except Exception:
        return False

def upper_dimension(dim):
    """Uppercase Above/Below/Prefix/Suffix/ValueOverride. Returns True on success.

    SpotDimension (and its SpotElevation / SpotCoordinate subclasses) derive from
    Dimension but expose a different override surface and do not support
    HasOneSegment() / Segments. Skip them — they need their own pass."""
    if isinstance(dim, SpotDimension):
        return False
    def upd(target):
        if target.Above:         target.Above         = target.Above.upper()
        if target.Below:         target.Below         = target.Below.upper()
        if target.Prefix:        target.Prefix        = target.Prefix.upper()
        if target.Suffix:        target.Suffix        = target.Suffix.upper()
        if target.ValueOverride: target.ValueOverride = target.ValueOverride.upper()
    try:
        if dim.HasOneSegment():
            upd(dim)
        else:
            for seg in dim.Segments:
                upd(seg)
        return True
    except Exception:
        return False

def upper_text_note(note):
    """Uppercase TextNote.Text. Returns True if changed.

    Note: Revit's TextNote API does not expose per-run formatted text; both
    `.Text =` and `FormattedText.SetPlainText` flatten inline bold/italic/underline
    runs to the note's default format. Preserving mixed inline formatting would
    require parsing the raw RTF, which is out of scope here."""
    try:
        txt = note.Text
        if not needs_upper(txt):
            return False
        note.Text = txt.upper()
        return True
    except Exception:
        return False

def upper_element_string_params(elem):
    """Uppercase every editable string-storage parameter on `elem`. Returns count changed.

    Skips URL / Image params (StorageType is also String but the value is a path or
    asset reference — uppercasing would corrupt the link)."""
    count = 0
    for p in elem.Parameters:
        try:
            if p.IsReadOnly or p.StorageType != StorageType.String:
                continue
            if _is_skippable_string_param(p):
                continue
            val = p.AsString()
            if not needs_upper(val):
                continue
            if set_string_param(p, val.upper()):
                count += 1
        except Exception:
            continue
    return count

def rename_safely(elem, new_name):
    """Set elem.Name, swallow duplicate-name errors. Returns True if changed."""
    try:
        elem.Name = new_name
        return True
    except Exception:
        return False


# ---------------- dispatch ----------------

def get_selected_elements():
    return [doc.GetElement(eid) for eid in uidoc.Selection.GetElementIds()]

def process_selection(elements):
    dim_count = note_count = 0
    for el in elements:
        if isinstance(el, Dimension):
            if upper_dimension(el):
                dim_count += 1
        elif isinstance(el, TextNote):
            if upper_text_note(el):
                note_count += 1
    return dim_count, note_count

def process_all_text():
    s = {"views": 0, "sheets": 0, "titleblocks": 0, "notes": 0, "dims": 0, "skipped": 0}

    # Views (all types, skip templates). Some system / read-only views (browser
    # organization, system schedules) reject the rename — counted as "skipped" so
    # the summary makes it visible instead of swallowing silently.
    for v in FilteredElementCollector(doc).OfClass(View).WhereElementIsNotElementType():
        try:
            if v.IsTemplate:
                continue
            if needs_upper(v.Name):
                if rename_safely(v, v.Name.upper()):
                    s["views"] += 1
                else:
                    s["skipped"] += 1
            tos = v.LookupParameter("Title on Sheet")
            if tos:
                cur = tos.AsString()
                if needs_upper(cur):
                    set_string_param(tos, cur.upper())
        except Exception:
            pass

    # Sheets
    for sh in FilteredElementCollector(doc).OfClass(ViewSheet).WhereElementIsNotElementType():
        try:
            if needs_upper(sh.Name):
                if rename_safely(sh, sh.Name.upper()):
                    s["sheets"] += 1
                else:
                    s["skipped"] += 1
        except Exception:
            pass

    # Title block instances — uppercase every editable string param
    for tb in (FilteredElementCollector(doc)
               .OfCategory(BuiltInCategory.OST_TitleBlocks)
               .WhereElementIsNotElementType()):
        if upper_element_string_params(tb) > 0:
            s["titleblocks"] += 1

    # All TextNotes in document
    for tn in FilteredElementCollector(doc).OfClass(TextNote).WhereElementIsNotElementType():
        if upper_text_note(tn):
            s["notes"] += 1

    # All Dimensions in document
    for d in FilteredElementCollector(doc).OfClass(Dimension).WhereElementIsNotElementType():
        if upper_dimension(d):
            s["dims"] += 1

    return s


# ---------------- main ----------------

def main():
    selected = get_selected_elements()
    has_dim  = any(isinstance(e, Dimension) for e in selected)
    has_note = any(isinstance(e, TextNote)  for e in selected)

    if selected and not (has_dim or has_note):
        TaskDialog.Show(
            "Upper All Text",
            "Selection contains no Dimensions or TextNotes.\n\n"
            "Tip: clear the selection to uppercase the whole project, "
            "or select only Dimensions / TextNotes to scope the change.")
        return

    t = Transaction(doc, "Upper All Text")
    t.Start()
    try:
        if selected:
            dim_count, note_count = process_selection(selected)
            t.Commit()
            msg = ("Uppercase applied to selection:\n"
                   "  - Dimensions: {}\n"
                   "  - TextNotes:  {}").format(dim_count, note_count)
        else:
            s = process_all_text()
            t.Commit()
            msg = ("Uppercase applied across project:\n"
                   "  - Views:        {}\n"
                   "  - Sheets:       {}\n"
                   "  - Title blocks: {}\n"
                   "  - TextNotes:    {}\n"
                   "  - Dimensions:   {}\n"
                   "  - Skipped (locked/duplicate name): {}").format(
                       s["views"], s["sheets"], s["titleblocks"],
                       s["notes"], s["dims"], s["skipped"])
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        TaskDialog.Show("Upper All Text", "Error: {}".format(ex))
        return

    TaskDialog.Show("Upper All Text", msg)


if __name__ == "__main__":
    main()
