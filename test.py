from rpw import revit, DB
doc = revit.doc
uidoc = revit.uidoc
ids = uidoc.Selection.GetElementIds()
print([doc.GetElement(i).GetType() for i in ids])
