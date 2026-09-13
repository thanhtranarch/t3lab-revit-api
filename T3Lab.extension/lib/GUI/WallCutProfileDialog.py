# -*- coding: utf-8 -*-
"""Wall Cut Profile — Dialog and execution logic for Wall Profile Cut & Openings."""

import os
import sys

from pyrevit import forms

try:
    from GUI import RevitTheme as _theme
except Exception:
    _theme = None

from GUI.WPF_Base import T3WPFWindow
from Snippets._compat import eid_value

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'WallCutProfile.xaml')

try:
    import Autodesk.Revit.DB as DB
    from Autodesk.Revit.DB import (
        FilteredElementCollector,
        BuiltInCategory,
        BuiltInParameter,
        Transaction,
        ElementId,
        Wall,
        RevitLinkInstance,
        FamilySymbol,
        XYZ,
        Line,
        IFailuresPreprocessor,
        FailureProcessingResult,
        FailureSeverity,
    )
    from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
except Exception:
    DB = None


class LinkFilter(ISelectionFilter if DB else object):
    __namespace__ = "T3Lab.WallCutProfile_LinkFilter"

    def AllowElement(self, elem):
        return isinstance(elem, RevitLinkInstance)

    def AllowReference(self, ref, point):
        return False


class WallFilter(ISelectionFilter if DB else object):
    __namespace__ = "T3Lab.WallCutProfile_WallFilter"

    def AllowElement(self, elem):
        if not isinstance(elem, Wall):
            return False
        if elem.WallType and elem.WallType.Kind in (DB.WallKind.Curtain, DB.WallKind.Stacked):
            return False
        loc = elem.Location
        return loc is not None and hasattr(loc, 'Curve') and loc.Curve is not None

    def AllowReference(self, ref, point):
        return False


class WarningSwallower(IFailuresPreprocessor if DB else object):
    __namespace__ = "T3Lab.WallCutProfile_Failures"

    def PreprocessFailures(self, fa):
        for f in fa.GetFailureMessages():
            sev = f.GetSeverity()
            if sev == FailureSeverity.Warning:
                fa.DeleteWarning(f)
        return FailureProcessingResult.Continue


class OpeningFamilyItem(object):
    def __init__(self, symbol):
        self.symbol = symbol
        self.symbol_id = symbol.Id
        try:
            fam_name = symbol.Family.Name
        except Exception:
            fam_name = "Unknown"
        try:
            p = symbol.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
            type_name = p.AsString() if p else symbol.Name
        except Exception:
            type_name = symbol.Name
        self.name = "{} : {}".format(fam_name, type_name)

    def __str__(self):
        return self.name


class WallCutProfileWindow(T3WPFWindow):
    def __init__(self, doc, uidoc):
        T3WPFWindow.__init__(self, _XAML)
        self._doc = doc
        self._uidoc = uidoc
        self._link_instances = []
        self._selected_link = None
        self._picked_walls = []
        self._opening_families = []

        self._adopt_host_font()
        self._apply_theme()
        self._init_controls()
        self._load_links()
        self._load_opening_families()

    def _adopt_host_font(self):
        if _theme is None:
            return
        family, size = _theme.host_font()
        if family:
            try:
                self.FontFamily = family
                if size and size > 0:
                    self.FontSize = size
            except Exception:
                pass

    def _apply_theme(self, theme=None):
        if _theme is None:
            return
        try:
            _theme.apply(self, theme)
        except Exception:
            pass

    def _init_controls(self):
        if hasattr(self, 'cmb_method') and self.cmb_method:
            methods = ["Place Opening Family", "Edit Wall Profile", "Wall Opening"]
            self.cmb_method.ItemsSource = methods
            self.cmb_method.SelectedIndex = 0

    def _load_links(self):
        if not self._doc:
            return
        coll = FilteredElementCollector(self._doc).OfClass(RevitLinkInstance)
        links = []
        for l in coll:
            link_doc = l.GetLinkDocument()
            if link_doc is not None:
                links.append(l)

        self._link_instances = links
        names = []
        for l in links:
            ldoc = l.GetLinkDocument()
            title = ldoc.Title if ldoc else l.Name
            names.append(title)

        if hasattr(self, 'cmb_links') and self.cmb_links:
            self.cmb_links.ItemsSource = names
            if len(names) > 0:
                self.cmb_links.SelectedIndex = 0
                self._selected_link = links[0]

    def _load_opening_families(self):
        if not self._doc:
            return
        results = []
        cats = [
            BuiltInCategory.OST_GenericModel,
            BuiltInCategory.OST_Windows,
            BuiltInCategory.OST_Doors,
        ]
        for bic in cats:
            try:
                coll = FilteredElementCollector(self._doc).OfCategory(bic).OfClass(FamilySymbol)
                for sym in coll:
                    fam = sym.Family
                    if not fam:
                        continue
                    p = fam.get_Parameter(BuiltInParameter.FAMILY_HOSTING_BEHAVIOR)
                    if p and p.AsInteger() == 1:
                        results.append(OpeningFamilyItem(sym))
                    elif any(kw in fam.Name.lower() for kw in ["opening", "void", "cutout", "penetration", "sleeve"]):
                        results.append(OpeningFamilyItem(sym))
            except Exception:
                pass

        seen = set()
        unique = []
        for item in results:
            sid = eid_value(item.symbol_id)
            if sid not in seen:
                seen.add(sid)
                unique.append(item)

        self._opening_families = sorted(unique, key=lambda x: x.name)
        if hasattr(self, 'cmb_families') and self.cmb_families:
            self.cmb_families.ItemsSource = [f.name for f in self._opening_families]
            if len(self._opening_families) > 0:
                self.cmb_families.SelectedIndex = 0

    def cmb_links_changed(self, sender, e):
        idx = self.cmb_links.SelectedIndex
        if idx >= 0 and idx < len(self._link_instances):
            self._selected_link = self._link_instances[idx]

    def btn_pick_link_clicked(self, sender, e):
        if not self._uidoc:
            return
        self.Hide()
        try:
            ref = self._uidoc.Selection.PickObject(
                ObjectType.Element,
                LinkFilter(),
                "Select a Revit Link Instance in view"
            )
            elem = self._doc.GetElement(ref.ElementId)
            if isinstance(elem, RevitLinkInstance):
                self._selected_link = elem
                ldoc = elem.GetLinkDocument()
                title = ldoc.Title if ldoc else elem.Name
                # Find in cmb_links
                for i, l in enumerate(self._link_instances):
                    if eid_value(l.Id) == eid_value(elem.Id):
                        self.cmb_links.SelectedIndex = i
                        break
        except Exception:
            pass
        finally:
            self.Show()

    def btn_pick_walls_clicked(self, sender, e):
        if not self._uidoc:
            return
        self.Hide()
        try:
            refs = self._uidoc.Selection.PickObjects(
                ObjectType.Element,
                WallFilter(),
                "Select target walls, then click Finish"
            )
            walls = []
            if refs:
                for r in refs:
                    w = self._doc.GetElement(r.ElementId)
                    if w:
                        walls.append(w)
            self._picked_walls = walls
            if hasattr(self, 'rb_walls_sel') and self.rb_walls_sel:
                self.rb_walls_sel.IsChecked = True
            if hasattr(self, 'txt_wall_status') and self.txt_wall_status:
                self.txt_wall_status.Text = "{} wall(s) selected".format(len(walls))
        except Exception:
            pass
        finally:
            self.Show()

    def cmb_method_changed(self, sender, e):
        if not hasattr(self, 'panel_family') or not self.panel_family:
            return
        method = str(self.cmb_method.SelectedItem or "")
        if "Place Opening Family" in method:
            self.panel_family.Visibility = System.Windows.Visibility.Visible
        else:
            self.panel_family.Visibility = System.Windows.Visibility.Collapsed

    def win_minimize_clicked(self, sender, e):
        self.WindowState = System.Windows.WindowState.Minimized

    def win_close_clicked(self, sender, e):
        self.Close()

    def btn_cancel_clicked(self, sender, e):
        self.Close()

    def btn_apply_clicked(self, sender, e):
        if not self._selected_link:
            forms.alert("Please select a valid linked model!", title="Wall Cut Profile")
            return

        link_doc = self._selected_link.GetLinkDocument()
        if not link_doc:
            forms.alert("Selected link is not loaded!", title="Wall Cut Profile")
            return

        # Get walls
        walls = []
        if self.rb_walls_sel.IsChecked:
            walls = self._picked_walls
            if not walls:
                forms.alert("No walls selected! Please pick walls or select 'All Walls in Current View'.", title="Wall Cut Profile")
                return
        else:
            coll = FilteredElementCollector(self._doc, self._doc.ActiveView.Id).OfCategory(BuiltInCategory.OST_Walls).WhereElementIsNotElementType()
            w_filter = WallFilter()
            for eid in coll.ToElementIds():
                w = self._doc.GetElement(eid)
                if w_filter.AllowElement(w):
                    walls.append(w)

        if not walls:
            forms.alert("No valid walls found in view!", title="Wall Cut Profile")
            return

        # Selected categories
        sel_cats = []
        if self.chk_ducts.IsChecked: sel_cats.append(("Ducts", BuiltInCategory.OST_DuctCurves))
        if self.chk_pipes.IsChecked: sel_cats.append(("Pipes", BuiltInCategory.OST_PipeCurves))
        if self.chk_trays.IsChecked: sel_cats.append(("Cable Trays", BuiltInCategory.OST_CableTray))
        if self.chk_framing.IsChecked: sel_cats.append(("Structural Framing", BuiltInCategory.OST_StructuralFraming))
        if self.chk_columns.IsChecked: sel_cats.append(("Columns", BuiltInCategory.OST_Columns))

        if not sel_cats:
            forms.alert("Please select at least one intersecting category!", title="Wall Cut Profile")
            return

        try:
            offset_mm = float(self.txt_offset.Text or "25")
        except Exception:
            offset_mm = 25.0
        offset_ft = offset_mm / 304.8

        method = str(self.cmb_method.SelectedItem or "Place Opening Family")
        sel_symbol = None
        if "Place Opening Family" in method:
            fam_idx = self.cmb_families.SelectedIndex
            if fam_idx >= 0 and fam_idx < len(self._opening_families):
                sel_symbol = self._opening_families[fam_idx].symbol
            else:
                forms.alert("Please select an opening void family!", title="Wall Cut Profile")
                return

        self.txt_status.Text = "Scanning intersections..."
        # Execute opening creation
        created, errors = self._execute_openings(walls, link_doc, self._selected_link, sel_cats, method, offset_ft, sel_symbol)

        msg = "Completed!\nCreated: {} opening(s).".format(created)
        if errors > 0:
            msg += "\nFailed: {} item(s).".format(errors)
        self.Close()
        forms.alert(msg, title="Wall Profile Cut Results")

    def _execute_openings(self, walls, link_doc, link_inst, categories, method, offset_ft, symbol):
        transform = link_inst.GetTotalTransform()
        created = 0
        errors = 0

        # Collect link elements bounding boxes
        link_items = []
        for cat_name, bic in categories:
            try:
                coll = FilteredElementCollector(link_doc).OfCategory(bic).WhereElementIsNotElementType()
                for el in coll:
                    bb = el.get_BoundingBox(None)
                    if not bb:
                        continue
                    pt_min = transform.OfPoint(bb.Min)
                    pt_max = transform.OfPoint(bb.Max)
                    center = transform.OfPoint((bb.Min + bb.Max) * 0.5)
                    link_items.append({
                        'elem': el,
                        'cat': cat_name,
                        'center': center,
                        'min': pt_min,
                        'max': pt_max,
                        'dx': abs(pt_max.X - pt_min.X),
                        'dy': abs(pt_max.Y - pt_min.Y),
                        'dz': abs(pt_max.Z - pt_min.Z),
                    })
            except Exception:
                pass

        t = Transaction(self._doc, "T3Lab: Wall Cut Profile")
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(WarningSwallower())
        t.SetFailureHandlingOptions(opts)

        t.Start()
        try:
            if symbol and not symbol.IsActive:
                symbol.Activate()
                self._doc.Regenerate()

            for w in walls:
                w_bb = w.get_BoundingBox(None)
                if not w_bb:
                    continue
                w_min = w_bb.Min
                w_max = w_bb.Max

                for item in link_items:
                    # Bounding box overlap test
                    i_c = item['center']
                    if not (w_min.X - offset_ft <= i_c.X <= w_max.X + offset_ft and
                            w_min.Y - offset_ft <= i_c.Y <= w_max.Y + offset_ft and
                            w_min.Z - offset_ft <= i_c.Z <= w_max.Z + offset_ft):
                        continue

                    # Overlapping: place opening
                    try:
                        if symbol and "Place Opening Family" in method:
                            inst = self._doc.Create.NewFamilyInstance(i_c, symbol, w, DB.Structure.StructuralType.NonStructural)
                            # Set parameters if available
                            for p_name, val in [("Width", max(item['dx'], item['dy']) + offset_ft * 2),
                                                ("Height", item['dz'] + offset_ft * 2)]:
                                p = inst.LookupParameter(p_name)
                                if p and not p.IsReadOnly:
                                    p.Set(val)
                            created += 1
                        elif "Wall Opening" in method:
                            # Create rectangular opening
                            p0 = XYZ(i_c.X - 0.5, i_c.Y - 0.5, i_c.Z - item['dz']*0.5 - offset_ft)
                            p1 = XYZ(i_c.X + 0.5, i_c.Y + 0.5, i_c.Z + item['dz']*0.5 + offset_ft)
                            self._doc.Create.NewOpening(w, p0, p1)
                            created += 1
                    except Exception:
                        errors += 1

            t.Commit()
        except Exception:
            t.RollBack()
            errors += 1

        return created, errors


def show_wall_cut_profile(doc, uidoc):
    if not doc or not uidoc:
        forms.alert("No active Revit document found.", title="Wall Cut Profile")
        return
    win = WallCutProfileWindow(doc, uidoc)
    win.ShowDialog()
