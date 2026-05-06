from rpw import revit, DB
uidoc = revit.uidoc
doc = revit.doc
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    print(type(el))
    if hasattr(el, Category) and el.Category:
        print(el.Category.Name)

