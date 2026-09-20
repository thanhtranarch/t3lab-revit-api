"""Revit operations owned by ManaSheets; callers supply staged fields only."""
import uuid
from pyrevit import DB


PARAMETERS = {
    "designed_by": "SHEET_DESIGNED_BY", "checked_by": "SHEET_CHECKED_BY",
    "drawn_by": "SHEET_DRAWN_BY", "approved_by": "SHEET_APPROVED_BY",
}


def _write(doc, label, action):
    transaction = DB.Transaction(doc, "T3Lab: " + label)
    try:
        transaction.Start()
        options = transaction.GetFailureHandlingOptions()
        options.SetForcedModalHandling(True)
        transaction.SetFailureHandlingOptions(options)
        action()
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise RuntimeError("Revit did not commit " + label)
    except Exception:
        if transaction.GetStatus() == DB.TransactionStatus.Started:
            transaction.RollBack()
        raise
    finally:
        transaction.Dispose()


def update_sheet(doc, element, changes):
    """Atomic row update, including empty text values and localized parameters."""
    def apply():
        for field, value in changes.items():
            value = "" if value is None else str(value)
            if field in ("sheet_number", "sheet_name"):
                if not value.strip():
                    raise ValueError("Sheet number and name cannot be empty")
                setattr(element, "SheetNumber" if field == "sheet_number" else "Name", value)
            elif field in PARAMETERS:
                parameter = element.get_Parameter(getattr(DB.BuiltInParameter, PARAMETERS[field]))
                if parameter is None or parameter.IsReadOnly:
                    raise ValueError(field + " is unavailable or read-only")
                if not parameter.Set(value):
                    raise ValueError("Revit rejected " + field)
            else:
                raise ValueError("Unsupported sheet field: " + field)
    _write(doc, "Update sheet", apply)


def renumber_sheets(doc, pairs, progress=None):
    """Atomic two-pass numbering permits swaps and rolls back on Stop."""
    pairs = list(pairs)
    targets = [str(number).strip() for _, number in pairs]
    if any(not number for number in targets):
        raise ValueError("Sheet numbers cannot be empty")
    if len({number.casefold() for number in targets}) != len(targets):
        raise ValueError("Preview contains duplicate sheet numbers")
    ids = {sheet.Id for sheet, _ in pairs}
    if len(ids) != len(pairs):
        raise ValueError("A sheet appears more than once")
    others = {sheet.SheetNumber.casefold()
              for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet)
              if sheet.Id not in ids}
    if any(number.casefold() in others for number in targets):
        raise ValueError("A preview number belongs to an unselected sheet")
    def apply():
        for index, (sheet, _) in enumerate(pairs):
            if progress is not None and not progress(index, len(pairs) * 2):
                raise RuntimeError("Renumber cancelled; no sheet numbers changed")
            sheet.SheetNumber = "T3Lab-" + uuid.uuid4().hex
        for index, ((sheet, _), number) in enumerate(zip(pairs, targets)):
            if progress is not None and not progress(len(pairs) + index, len(pairs) * 2):
                raise RuntimeError("Renumber cancelled; no sheet numbers changed")
            sheet.SheetNumber = number
    _write(doc, "Renumber sheets", apply)
    return len(pairs)
