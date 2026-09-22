"""Revit operations owned by ManaViews (no WPF dependencies)."""
from pyrevit import DB


def _write(doc, label, action):
    transaction = DB.Transaction(doc, "T3Lab: " + label)
    try:
        transaction.Start()
        options = transaction.GetFailureHandlingOptions()
        options.SetForcedModalHandling(True)
        transaction.SetFailureHandlingOptions(options)
        result = action()
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise RuntimeError("Revit did not commit " + label)
        return result
    except Exception:
        if transaction.GetStatus() == DB.TransactionStatus.Started:
            transaction.RollBack()
        raise
    finally:
        transaction.Dispose()


def write_field(doc, item, field, value):
    """Write a single cell; update the row only after a confirmed commit."""
    view = item.element
    def apply():
        if field == "name":
            if not str(value or "").strip():
                raise ValueError("View name cannot be empty")
            view.Name = value
        elif field == "view_template":
            template_id = DB.ElementId.InvalidElementId
            if value != "None":
                matches = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View)
                           if v.IsTemplate and v.Name == value]
                if len(matches) != 1:
                    raise ValueError("Template not found: " + str(value))
                template_id = matches[0].Id
                if not view.IsValidViewTemplate(template_id):
                    raise ValueError("Template is incompatible with this view")
            view.ViewTemplateId = template_id
        elif field == "scale":
            scale = int(str(value).strip())
            if scale <= 0:
                raise ValueError("Scale must be a positive integer")
            view.Scale = scale
        elif field == "detail_level":
            if value not in ("Coarse", "Medium", "Fine"):
                raise ValueError("Invalid detail level")
            view.DetailLevel = getattr(DB.ViewDetailLevel, value)
        elif field == "title_on_sheet":
            parameter = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
            if parameter is None or parameter.IsReadOnly:
                raise ValueError("Title on Sheet is unavailable or read-only")
            if not parameter.Set(str(value or "")):
                raise ValueError("Revit rejected Title on Sheet")
        else:
            raise ValueError("Unsupported view field: " + field)
    _write(doc, "Update view " + field, apply)
    setattr(item, field, value)
    return True


def rename_template(doc, template, new_name):
    if not str(new_name or "").strip():
        raise ValueError("Template name cannot be empty")
    _write(doc, "Rename view template", lambda: setattr(template, "Name", new_name))
    return True


def duplicate_views(doc, elements):
    """One transaction per element: rejected copies leave no orphan views."""
    succeeded = failed = 0
    names = {v.Name.casefold() for v in DB.FilteredElementCollector(doc).OfClass(DB.View)}
    for element in elements:
        base = element.Name + " - Copy"
        name, index = base, 2
        while name.casefold() in names:
            name = "{} {}".format(base, index)
            index += 1
        try:
            def duplicate():
                option = DB.ViewDuplicateOption.Duplicate
                if not element.CanViewBeDuplicated(option):
                    raise ValueError("View cannot be duplicated")
                new_view = doc.GetElement(element.Duplicate(option))
                new_view.Name = name
            _write(doc, "Duplicate view", duplicate)
            names.add(name.casefold())
            succeeded += 1
        except Exception:
            failed += 1
    return succeeded, failed


def delete_views(doc, elements):
    succeeded = failed = 0
    for element in elements:
        try:
            if element.IsTemplate:
                if any(not v.IsTemplate and v.ViewTemplateId == element.Id
                       for v in DB.FilteredElementCollector(doc).OfClass(DB.View)):
                    raise ValueError("Template is in use")
            _write(doc, "Delete view", lambda: doc.Delete(element.Id))
            succeeded += 1
        except Exception:
            failed += 1
    return succeeded, failed
