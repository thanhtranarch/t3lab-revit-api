import clr
from rpw import revit, DB
doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
lines = DB.FilteredElementCollector(doc, view.Id).OfCategory(DB.BuiltInCategory.OST_SectionLine).ToElements()
print(Found OST_SectionLine elements: , len(lines))
for line in lines:
    print(type(line))
    opts = DB.Options()
    opts.View = view
    opts.ComputeReferences = True
    geom = line.get_Geometry(opts)
    if geom:
        for g in geom:
            print(g.GetType())

