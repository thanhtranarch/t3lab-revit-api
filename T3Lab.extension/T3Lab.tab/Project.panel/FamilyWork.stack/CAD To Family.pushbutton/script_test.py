# -*- coding: utf-8 -*-
import os
import tempfile
import clr

from pyrevit import revit, DB, UI, forms

# doc = revit.doc
# uidoc = revit.uidoc
# app = doc.Application

uidoc = __revit__.ActiveUIDocument
doc   = uidoc.Document
app   = __revit__.Application

# ==================================================
# 1. Selection Filter for CAD (ImportInstance)
# ==================================================
class CADSelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, DB.ImportInstance)
    def AllowReference(self, ref, pt):
        return False

try:
    ref = uidoc.Selection.PickObject(
        UI.Selection.ObjectType.Element, 
        CADSelectionFilter(), 
        "Please select a CAD file (ImportInstance)"
    )
    cad_inst = doc.GetElement(ref)
except Exception:
    forms.alert("Action cancelled by user.")
    import sys; sys.exit()

# ==================================================
# 2. Geometry Extraction (Thu thập các đường nét)
# ==================================================
opt = DB.Options()
opt.ComputeReferences = True
opt.IncludeNonVisibleObjects = True
geom_elem = cad_inst.get_Geometry(opt)

curves = []
min_len = app.ShortCurveTolerance

def collect_curves(g_elem):
    for item in g_elem:
        if isinstance(item, DB.Curve):
            if item.IsBound:
                if item.Length >= min_len:
                    curves.append(item)
            else:
                curves.append(item)
        elif isinstance(item, DB.PolyLine):
            pts = item.GetCoordinates()
            for i in range(item.NumberOfCoordinates - 1):
                try:
                    p1 = pts[i]
                    p2 = pts[i+1]
                    if p1.DistanceTo(p2) >= min_len:
                        curves.append(DB.Line.CreateBound(p1, p2))
                except:
                    pass
        elif isinstance(item, DB.GeometryInstance):
            nested = item.GetInstanceGeometry()
            if nested:
                collect_curves(nested)
        elif isinstance(item, DB.Solid):
            for edge in item.Edges:
                try:
                    ec = edge.AsCurve()
                    if isinstance(ec, DB.Curve) and ec.IsBound and ec.Length >= min_len:
                        curves.append(ec)
                except:
                    pass

if geom_elem:
    collect_curves(geom_elem)

if not curves:
    forms.alert("No curves found in this CAD file.")
    import sys; sys.exit()

# --------------------------------------------------
# PRINT CAD EXTRACTION REPORT
# --------------------------------------------------
print("=== T3LAB: CAD EXTRACTION REPORT ===")
print("=> Total curves extracted from CAD: {}".format(len(curves)))

curve_types = {}
for c in curves:
    c_type = c.GetType().Name
    curve_types[c_type] = curve_types.get(c_type, 0) + 1
    
for k, v in curve_types.items():
    print("   - {}: {}".format(k, v))
print("-" * 50)

# ==================================================
# 3. Calculate Bounding Box to Center the Geometry
# ==================================================
all_pts = []

for c in curves:
    try:
        if c.IsBound:
            all_pts.append(c.GetEndPoint(0))
            all_pts.append(c.GetEndPoint(1))
        else:
            all_pts.extend(c.Tessellate())
    except Exception:
        pass

if all_pts:
    min_x = min([p.X for p in all_pts])
    max_x = max([p.X for p in all_pts])
    min_y = min([p.Y for p in all_pts])
    max_y = max([p.Y for p in all_pts])

    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
else:
    cx, cy = 0.0, 0.0

# ==================================================
# 4. Find the Furniture Family Template
# ==================================================
template_name = "Metric Furniture.rft"
template_path = ""

base_paths = [
    app.FamilyTemplatePath,
    r"C:\ProgramData\Autodesk\RVT {}\Family Templates\English".format(app.VersionNumber),
    r"C:\ProgramData\Autodesk\RVT {}\Family Templates\English_I".format(app.VersionNumber)
]

for bp in base_paths:
    if bp and os.path.exists(bp):
        tp = os.path.join(bp, template_name)
        if os.path.exists(tp):
            template_path = tp
            break

if not template_path:
    template_path = forms.pick_file(file_ext='rft', title="Select Furniture Template (.rft)")

if not template_path:
    forms.alert("Template not found. Exiting.")
    import sys; sys.exit()

# ==================================================
# 5. Create Family & Draw 2D Model Curves
# ==================================================
fam_doc = app.NewFamilyDocument(template_path)

t = DB.Transaction(fam_doc, "Draw 2D CAD")
t.Start()

drawn_count = 0

try:
    sketch_plane = DB.SketchPlane.Create(
        fam_doc, 
        DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ.Zero)
    )

    def _draw_seg(pa, pb):
        paf = DB.XYZ(pa.X, pa.Y, 0.0)
        pbf = DB.XYZ(pb.X, pb.Y, 0.0)
        if paf.DistanceTo(pbf) > min_len:
            try:
                fam_doc.FamilyCreate.NewModelCurve(DB.Line.CreateBound(paf, pbf), sketch_plane)
                return 1
            except:
                return 0
        return 0

    translator = DB.Transform.CreateTranslation(DB.XYZ(-cx, -cy, 0))

    for c in curves:
        try:
            new_c = c.CreateTransformed(translator)
            if isinstance(new_c, DB.Line):
                drawn_count += _draw_seg(new_c.GetEndPoint(0), new_c.GetEndPoint(1))
            else:
                pts = new_c.Tessellate()
                for i in range(len(pts) - 1):
                    drawn_count += _draw_seg(pts[i], pts[i + 1])
                
                # BỔ SUNG QUAN TRỌNG: Nối điểm cuối với điểm đầu nếu là đường khép kín
                if not new_c.IsBound:
                    drawn_count += _draw_seg(pts[-1], pts[0])
        except Exception:
            pass

    t.Commit()
    
    # --------------------------------------------------
    # PRINT FAMILY CREATION REPORT
    # --------------------------------------------------
    print("=== T3LAB: FAMILY CREATION REPORT ===")
    print("=> Successfully generated {} 2D model lines in the Family.".format(drawn_count))
    if drawn_count > len(curves):
        print("   (Note: The number is higher because Arcs/Splines were tessellated into smaller straight lines).")
    print("=" * 50)

except Exception as e:
    t.RollBack()
    fam_doc.Close(False)
    forms.alert("Error drawing curves: " + str(e))
    import sys; sys.exit()

# ==================================================
# 6. Save to Temp Folder & Load into Project
# ==================================================
temp_dir = tempfile.gettempdir()
save_path = os.path.join(temp_dir, "T3Lab_QuickFurniture.rfa")

if os.path.exists(save_path):
    try:
        os.remove(save_path)
    except:
        save_path = os.path.join(temp_dir, "T3Lab_QuickFurniture_2.rfa")

opt_save = DB.SaveAsOptions()
opt_save.OverwriteExistingFile = True
fam_doc.SaveAs(save_path, opt_save)
fam_doc.Close(False)

class FamLoadOptions(DB.IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        return True
    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        return True

t_load = DB.Transaction(doc, "Load Quick Furniture")
t_load.Start()

family_ref = clr.Reference[DB.Family]()
doc.LoadFamily(save_path, FamLoadOptions(), family_ref)

t_load.Commit()

# ==================================================
# 7. Activate Symbol and Prompt Placement
# ==================================================
try:
    fam_symbol = None
    for sym_id in family_ref.Value.GetFamilySymbolIds():
        fam_symbol = doc.GetElement(sym_id)
        break

    if fam_symbol:
        if not fam_symbol.IsActive:
            t_act = DB.Transaction(doc, "Activate Symbol")
            t_act.Start()
            fam_symbol.Activate()
            t_act.Commit()
        
        uidoc.PromptForFamilyInstancePlacement(fam_symbol)
except Exception:
    forms.alert("Family loaded successfully, but automatic placement failed.")