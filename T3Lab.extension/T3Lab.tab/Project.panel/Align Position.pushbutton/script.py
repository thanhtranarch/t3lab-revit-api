# -*- coding: utf-8 -*-
"""
Align Positions
---------------
Snap element positions so their distance to a reference element
(Grid, Wall, or Column) becomes a clean multiple of 5 or 10 mm.

Workflow
  1. Pick a reference element (Grid / Wall / Column)
  2. Select surrounding elements
  3. Review the preview table
  4. Apply corrections

--------------------------------------------------------
Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
--------------------------------------------------------
"""

__title__   = "Align\nPositions"
__author__  = "Tran Tien Thanh"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==================================================
import os
import sys
import math
import clr

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')
clr.AddReference('System.Data')

from System.Windows import WindowState
from System.Windows.Media import SolidColorBrush, Color
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind, Boolean
from System.Data import DataTable

from Autodesk.Revit.DB import (
    XYZ,
    Transaction,
    ElementTransformUtils,
    Grid,
    Wall,
    FamilyInstance,
    BuiltInCategory,
    LocationPoint,
    LocationCurve,
    FilteredElementCollector,
    Line,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import forms, revit, script

# Path setup
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

# DEFINE VARIABLES
# ==================================================
doc    = revit.doc
uidoc  = revit.uidoc
logger = script.get_logger()
output = script.get_output()
REVIT_VERSION = int(revit.doc.Application.VersionNumber)
XAML_FILE = os.path.join(extension_dir, 'lib', 'GUI', 'Tools', 'AlignPositions.xaml')

# CLASS/FUNCTIONS
# ==================================================

# ════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════
MM_PER_FOOT = 304.8
TOLERANCE_MM = 0.05        # ignore corrections smaller than this


def feet_to_mm(feet):
    return feet * MM_PER_FOOT


def mm_to_feet(mm):
    return mm / MM_PER_FOOT


def round_to_snap(value_mm, snap_mm):
    """Round *value_mm* to the nearest multiple of *snap_mm*."""
    return round(value_mm / snap_mm) * snap_mm


# ════════════════════════════════════════════════════════════════
# SELECTION FILTER
# ════════════════════════════════════════════════════════════════
class ReferenceFilter(ISelectionFilter):
    """Allow only Grid, Wall, or Column elements."""

    def AllowElement(self, element):
        if isinstance(element, Grid):
            return True
        if isinstance(element, Wall):
            return True
        if isinstance(element, FamilyInstance):
            cat = element.Category
            if cat is None:
                return False
            cat_id = cat.Id.IntegerValue
            if cat_id == int(BuiltInCategory.OST_Columns):
                return True
            if cat_id == int(BuiltInCategory.OST_StructuralColumns):
                return True
        return False

    def AllowReference(self, reference, position):
        return False


# ════════════════════════════════════════════════════════════════
# GEOMETRY HELPERS
# ════════════════════════════════════════════════════════════════
def get_element_location(element):
    """Return an XYZ location point for any element."""
    loc = element.Location
    if loc is not None:
        if isinstance(loc, LocationPoint):
            return loc.Point
        if isinstance(loc, LocationCurve):
            return loc.Curve.Evaluate(0.5, True)

    # Fallback: bounding-box centre
    bb = element.get_BoundingBox(None)
    if bb is not None:
        return XYZ(
            (bb.Min.X + bb.Max.X) / 2.0,
            (bb.Min.Y + bb.Max.Y) / 2.0,
            (bb.Min.Z + bb.Max.Z) / 2.0,
        )
    return None


def project_onto_curve(point, curve):
    """Project *point* onto *curve*; return the projected XYZ or None."""
    result = curve.Project(point)
    if result is not None:
        return result.XYZPoint
    return None


def get_element_orientation(element):
    """Returns the primary X direction vector for an element. Only XY plane."""
    vec = None
    if isinstance(element, Grid):
        vec = element.Curve.GetEndPoint(1) - element.Curve.GetEndPoint(0)
    elif isinstance(element, Wall):
        vec = element.Location.Curve.GetEndPoint(1) - element.Location.Curve.GetEndPoint(0)
    elif isinstance(element, FamilyInstance):
        vec = element.GetTransform().BasisX
    else:
        loc = element.Location
        if isinstance(loc, LocationCurve):
            vec = loc.Curve.GetEndPoint(1) - loc.Curve.GetEndPoint(0)
        elif isinstance(loc, LocationPoint):
            ang = loc.Rotation
            vec = XYZ(math.cos(ang), math.sin(ang), 0)

    if vec is not None:
        v_xy = XYZ(vec.X, vec.Y, 0)
        if v_xy.GetLength() > 1e-6:
            return v_xy.Normalize()
            
    return XYZ.BasisX




# ════════════════════════════════════════════════════════════════
# CORE ALIGNMENT LOGIC (Headless Compatible)
# ════════════════════════════════════════════════════════════════
class PositionAligner:
    def __init__(self, doc, ref_element):
        self.doc = doc
        self.ref_element = ref_element
        self.ref_origin = get_element_location(ref_element)
        self.ref_dir = get_element_orientation(ref_element)

    def analyze_elements(self, elements, snap_feet):
        results = []
        for elem in elements:
            if elem.Id == self.ref_element.Id:
                continue
            result = self._compute(elem, snap_feet)
            if result:
                results.append(result)
        return results

    def _compute(self, elem, snap_feet):
        loc = get_element_location(elem)
        if loc is None or self.ref_origin is None:
            return None

        target_dir = get_element_orientation(elem)
        ref_x = self.ref_dir
        ref_y = XYZ(-ref_x.Y, ref_x.X, 0)

        is_curve_element = False
        c = None
        if isinstance(elem, Wall) and hasattr(elem.Location, "Curve"):
            c = elem.Location.Curve
            is_curve_element = c is not None and isinstance(c, Line)
        elif isinstance(elem, Grid):
            c = elem.Curve
            is_curve_element = c is not None and isinstance(c, Line)

        move_vector = None
        rot_correction = 0.0
        needs_move = False
        needs_rot = False
        new_curve = None

        if is_curve_element:
            pt1 = c.GetEndPoint(0)
            pt2 = c.GetEndPoint(1)

            u1 = (pt1 - self.ref_origin).DotProduct(ref_x)
            v1 = (pt1 - self.ref_origin).DotProduct(ref_y)
            u2 = (pt2 - self.ref_origin).DotProduct(ref_x)
            v2 = (pt2 - self.ref_origin).DotProduct(ref_y)

            is_mostly_x = abs(u2 - u1) > abs(v2 - v1)

            if is_mostly_x:
                v_avg = (v1 + v2) / 2.0
                v_snap = round(v_avg / snap_feet) * snap_feet
                v1_snap, v2_snap = v_snap, v_snap
                u1_snap = round(u1 / snap_feet) * snap_feet
                u2_snap = round(u2 / snap_feet) * snap_feet
            else:
                u_avg = (u1 + u2) / 2.0
                u_snap = round(u_avg / snap_feet) * snap_feet
                u1_snap, u2_snap = u_snap, u_snap
                v1_snap = round(v1 / snap_feet) * snap_feet
                v2_snap = round(v2 / snap_feet) * snap_feet

            new_pt1 = self.ref_origin + u1_snap * ref_x + v1_snap * ref_y + XYZ(0,0,pt1.Z - self.ref_origin.Z)
            new_pt2 = self.ref_origin + u2_snap * ref_x + v2_snap * ref_y + XYZ(0,0,pt2.Z - self.ref_origin.Z)

            if new_pt1.DistanceTo(new_pt2) > 0.0026:
                if pt1.DistanceTo(new_pt1) > 0.001 or pt2.DistanceTo(new_pt2) > 0.001:
                    new_curve = Line.CreateBound(new_pt1, new_pt2)
                    needs_move = True
                    mid_pt = (pt1 + pt2) / 2.0
                    new_mid_pt = (new_pt1 + new_pt2) / 2.0
                    move_vector = new_mid_pt - mid_pt
        else:
            # Angle correction
            ref_theta = math.atan2(ref_x.Y, ref_x.X)
            tar_theta = math.atan2(target_dir.Y, target_dir.X)
            diff_rad = tar_theta - ref_theta
            snap_theta_rad = round(diff_rad / (math.pi/2)) * (math.pi/2)
            rot_correction = snap_theta_rad - diff_rad
            
            # Distance correction
            v = XYZ(loc.X - self.ref_origin.X, loc.Y - self.ref_origin.Y, 0)
            dist_x_feet = v.DotProduct(ref_x)
            dist_y_feet = v.DotProduct(ref_y)
            snap_x_feet = round(dist_x_feet / snap_feet) * snap_feet
            snap_y_feet = round(dist_y_feet / snap_feet) * snap_feet
            move_vector = ref_x * (snap_x_feet - dist_x_feet) + ref_y * (snap_y_feet - dist_y_feet)
                
            if move_vector.GetLength() > 0.0026:
                needs_move = True
            if isinstance(elem, FamilyInstance):
                needs_rot = (abs(rot_correction) >= 0.001)

        if not needs_move and not needs_rot:
            return None
            
        return {
            "element": elem,
            "origin": loc,
            "move_vector": move_vector if needs_move else None,
            "rot_correction": rot_correction if needs_rot else 0.0,
            "new_curve": new_curve
        }

    def apply_corrections(self, results):
        moved = 0
        with Transaction(self.doc, "Align Positions") as t:
            t.Start()
            for res in results:
                try:
                    elem = res["element"]
                    if res.get("new_curve"):
                        if isinstance(elem, Wall): elem.Location.Curve = res["new_curve"]
                        elif isinstance(elem, Grid):
                            import Autodesk.Revit.DB as DB
                            try: elem.SetCurveInView(DB.DatumExtentType.Model, self.doc.ActiveView, res["new_curve"])
                            except: ElementTransformUtils.MoveElement(self.doc, elem.Id, res["move_vector"])
                    else:
                        if res["rot_correction"] != 0.0:
                            axis = Line.CreateBound(res["origin"], res["origin"] + XYZ.BasisZ)
                            ElementTransformUtils.RotateElement(self.doc, elem.Id, axis, res["rot_correction"])
                        if res["move_vector"]:
                            ElementTransformUtils.MoveElement(self.doc, elem.Id, res["move_vector"])
                    moved += 1
                except: pass
            t.Commit()
        return moved

# ════════════════════════════════════════════════════════════════
# WPF WINDOW
# ════════════════════════════════════════════════════════════════
# (Keeping AlignPositionsWindow but updating it to use PositionAligner)

class AlignPositionsWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self._aligner = None
        self._results = []
        self._dt = DataTable()
        self._dt.Columns.Add("Apply", Boolean)
        for col in ["Category", "Name", "ElementId", "StartPt", "EndPt", "LocDist", "AngleStr", "Correction"]:
            self._dt.Columns.Add(col)
        self.dg_elements.ItemsSource = self._dt.DefaultView
        self.btn_select.IsEnabled = False
        self.btn_apply.IsEnabled = False
        self._update_status("Ready — pick a reference element to begin")

    # ── Window chrome handlers ──────────────────────────
    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def close_button_clicked(self, sender, e):
        self.Close()

    # ── Reference picking ───────────────────────────────
    def pick_reference_clicked(self, sender, e):
        try:
            self.Hide()
            ref = uidoc.Selection.PickObject(
                ObjectType.Element,
                ReferenceFilter(),
                "Pick a Grid, Wall, or Column as reference"
            )
            element = doc.GetElement(ref.ElementId)
            self._set_reference(element)
        except OperationCanceledException:
            self._update_status("Reference pick cancelled")
        except Exception as ex:
            self._update_status("Error: {}".format(ex))
        finally:
            self.Show()

    # ── Settings handlers ───────────────────────────────
    def unit_changed(self, sender, e):
        """Re-analyze when unit system changes."""
        if self._aligner:
            self._auto_find_elements()

    def snap_changed(self, sender, e):
        """Re-analyze when snap value changes."""
        if self._aligner:
            self._auto_find_elements()

    # ── Element selection ───────────────────────────────
    def select_elements_clicked(self, sender, e):
        """Re-collect elements in active view and re-analyze."""
        if not self._aligner:
            self._update_status("Pick a reference element first")
            return
        self._auto_find_elements()

    # ── Internal helpers ────────────────────────────────
    def _update_status(self, text):
        self.status_text.Text = text

    def _snap_feet(self):
        is_metric = getattr(self, 'rb_metric', None) and self.rb_metric.IsChecked
        if is_metric:
            if self.rb_snap1.IsChecked: return 5.0 / MM_PER_FOOT
            if self.rb_snap2.IsChecked: return 10.0 / MM_PER_FOOT
            if getattr(self, 'rb_snap3', None) and self.rb_snap3.IsChecked: return 20.0 / MM_PER_FOOT
            return 5.0 / MM_PER_FOOT
        # Imperial: 1/8 inch
        return (1.0 / 8.0) / 12.0

    def _set_reference(self, element):
        self._aligner = PositionAligner(doc, element)
        self.txt_ref_info.Text = "{}: {}".format(type(element).__name__, element.Id)
        self.btn_select.IsEnabled = True
        self._auto_find_elements()

    def _auto_find_elements(self):
        if not self._aligner:
            return
        walls = FilteredElementCollector(doc, doc.ActiveView.Id).OfClass(Wall).ToElements()
        grids = FilteredElementCollector(doc, doc.ActiveView.Id).OfClass(Grid).ToElements()
        elements = list(walls) + list(grids)
        self._analyze(elements)

    def _analyze(self, elements):
        if not self._aligner:
            return
        snap_f = self._snap_feet()
        self._results = self._aligner.analyze_elements(elements, snap_f)
        self._dt.Clear()
        for res in self._results:
            elem = res["element"]
            row = self._dt.NewRow()
            row["Apply"] = True
            row["ElementId"] = str(elem.Id)
            cat = elem.Category
            row["Category"] = cat.Name if cat else ""
            row["Name"] = elem.Name if hasattr(elem, 'Name') else ""

            loc = res.get("origin")
            if loc:
                row["LocDist"] = "{:.1f} mm".format(feet_to_mm(
                    XYZ(loc.X - self._aligner.ref_origin.X,
                        loc.Y - self._aligner.ref_origin.Y, 0).GetLength()))

            mv = res.get("move_vector")
            if mv:
                row["Correction"] = "Δ {:.1f} mm".format(feet_to_mm(mv.GetLength()))
            else:
                row["Correction"] = "—"

            rot = res.get("rot_correction", 0.0)
            if abs(rot) >= 0.001:
                row["AngleStr"] = "{:.2f}°".format(math.degrees(rot))
            else:
                row["AngleStr"] = "—"

            self._dt.Rows.Add(row)

        count = len(self._results)
        self.btn_apply.IsEnabled = count > 0
        self.txt_element_count.Text = "{} element(s) need correction".format(count)
        self._update_status("{} element(s) analyzed".format(len(elements)))

    # ── Apply ───────────────────────────────────────────
    def apply_clicked(self, sender, e):
        if not self._aligner:
            return
        moved = self._aligner.apply_corrections(self._results)
        forms.alert("Moved {} elements".format(moved))
        self.Close()

def run_headless(args_json):
    import json
    try:
        data = json.loads(args_json)
        ref_id = data.get("ref_id")
        element_ids = data.get("element_ids", [])
        snap_mm = data.get("snap_mm", 10.0)
        
        ref_elem = doc.GetElement(DB.ElementId(int(ref_id)))
        aligner = PositionAligner(doc, ref_elem)
        
        elements = [doc.GetElement(DB.ElementId(int(eid))) for eid in element_ids]
        elements = [e for e in elements if e]
        
        results = aligner.analyze_elements(elements, snap_mm / MM_PER_FOOT)
        moved = aligner.apply_corrections(results)
        print(json.dumps({"status": "success", "moved": moved}))
    except Exception as ex:
        print(json.dumps({"status": "error", "message": str(ex)}))

if __name__ == '__main__':
    if len(sys.argv) > 1:
        run_headless(sys.argv[1])
    else:
        window = AlignPositionsWindow()
        window.ShowDialog()
