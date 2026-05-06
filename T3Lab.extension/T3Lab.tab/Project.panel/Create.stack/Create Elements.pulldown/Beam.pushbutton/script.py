# -*- coding: utf-8 -*-
"""
Revit Beam From CAD
-------------------
Automatically create Structural Beams in Revit from imported CAD files.

Workflow:
  1. Find CAD file by Level/Pattern.
  2. Detect Beam Layers.
  3. Pair parallel lines to find centerlines and widths.
  4. Auto-create beam types if they don't exist.
  5. Place beams with correct level and Z-offset.

--------------------------------------------------------
Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
--------------------------------------------------------
"""

__title__   = "Beam"
__author__  = "Tran Tien Thanh"
__version__ = "2.0.0"

import os
import sys
import math
import clr
import re

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Windows import Window
from Autodesk.Revit import DB
from pyrevit import forms, revit, script

# Path setup
# We need to go up 5 levels: Beam.pushbutton -> CAD.stack -> Project.panel -> T3Lab.tab -> T3Lab.extension
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

_GUI_DIR = os.path.join(lib_dir, 'GUI')
_XAML = os.path.join(_GUI_DIR, 'Tools', 'CADtoBeam.xaml')

# CONSTANTS
FT_TO_MM = 304.8
MM_TO_FT = 1.0 / 304.8

# HELPERS
# ==================================================
def get_or_create_beam_type(family_name, width_mm, height_mm):
    """Finds or creates a beam type within the specified family."""
    type_name = "{}x{}mm".format(int(width_mm), int(height_mm))
    
    # Get all symbols in this family
    symbols = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).ToElements()
    target_family_symbols = [s for s in symbols if s.Family.Name == family_name]
    
    if not target_family_symbols:
        return None
    
    # Check if type exists
    for s in target_family_symbols:
        if revit.DB.Element.Name.__get__(s) == type_name:
            return s
    
    # Create new type by duplicating the first one found
    source_symbol = target_family_symbols[0]
    try:
        new_symbol = source_symbol.Duplicate(type_name)
        # Set parameters (b, h or Width, Height)
        p_b = new_symbol.LookupParameter('b') or new_symbol.LookupParameter('Width') or new_symbol.LookupParameter('B')
        p_h = new_symbol.LookupParameter('h') or new_symbol.LookupParameter('Height') or new_symbol.LookupParameter('H')
        if p_b: p_b.Set(width_mm * MM_TO_FT)
        if p_h: p_h.Set(height_mm * MM_TO_FT)
        return new_symbol
    except Exception as ex:
        logger.debug("Failed to create type {}: {}".format(type_name, ex))
        return source_symbol

# GEOMETRY PAIRING LOGIC
# ==================================================
def pair_lines_h(lines):
    pairs = []
    used = set()
    for i, l1 in enumerate(lines):
        if i in used: continue
        best_j, best_dist = None, None
        for j, l2 in enumerate(lines):
            if j <= i or j in used: continue
            min1, max1 = min(l1['x1'],l1['x2']), max(l1['x1'],l1['x2'])
            min2, max2 = min(l2['x1'],l2['x2']), max(l2['x1'],l2['x2'])
            overlap = min(max1,max2) - max(min1,min2)
            min_len = min(max1-min1, max2-min2)
            if min_len < 1 or overlap/min_len < 0.7: continue
            dist = abs(l1['y1'] - l2['y1'])
            if dist < 50 or dist > 1500: continue
            if best_dist is None or dist < best_dist:
                best_dist = dist; best_j = j
        if best_j is not None:
            l2 = lines[best_j]
            x_start = (min(l1['x1'],l1['x2']) + min(l2['x1'],l2['x2'])) / 2
            x_end   = (max(l1['x1'],l1['x2']) + max(l2['x1'],l2['x2'])) / 2
            cy = (l1['y1'] + l2['y1']) / 2
            pairs.append({
                'dir': 'H', 'main_s': x_start, 'main_e': x_end,
                'perp': cy, 'z': l1['z'],
                'width': round(abs(l1['y1'] - l2['y1']))
            })
            used.add(i); used.add(best_j)
    return pairs

def pair_lines_v(lines):
    pairs = []
    used = set()
    for i, l1 in enumerate(lines):
        if i in used: continue
        best_j, best_dist = None, None
        for j, l2 in enumerate(lines):
            if j <= i or j in used: continue
            min1, max1 = min(l1['y1'],l1['y2']), max(l1['y1'],l1['y2'])
            min2, max2 = min(l2['y1'],l2['y2']), max(l2['y1'],l2['y2'])
            overlap = min(max1,max2) - max(min1,min2)
            min_len = min(max1-min1, max2-min2)
            if min_len < 1 or overlap/min_len < 0.7: continue
            dist = abs(l1['x1'] - l2['x1'])
            if dist < 50 or dist > 1500: continue
            if best_dist is None or dist < best_dist:
                best_dist = dist; best_j = j
        if best_j is not None:
            l2 = lines[best_j]
            y_start = (min(l1['y1'],l1['y2']) + min(l2['y1'],l2['y2'])) / 2
            y_end   = (max(l1['y1'],l1['y2']) + max(l2['y1'],l2['y2'])) / 2
            cx = (l1['x1'] + l2['x1']) / 2
            pairs.append({
                'dir': 'V', 'main_s': y_start, 'main_e': y_end,
                'perp': cx, 'z': l1['z'],
                'width': round(abs(l1['x1'] - l2['x1']))
            })
            used.add(i); used.add(best_j)
    return pairs

# UI CLASS
# ==================================================
class CADtoBeamWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, _XAML)
        self._populate_initial_data()

    def _populate_initial_data(self):
        # 1. CAD Links (Imports & Links)
        import_instances = DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance).ToElements()
        self.cad_map = {}
        for imp in import_instances:
            type_elem = doc.GetElement(imp.GetTypeId())
            name = type_elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
            self.cad_map["{} (Id:{})".format(name, imp.Id)] = imp
        
        self.cb_cad_links.ItemsSource = sorted(self.cad_map.keys())

        # 2. Beam Families (Unique Names)
        beam_symbols = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).OfClass(DB.FamilySymbol).ToElements()
        self.family_names = sorted(list(set(s.Family.Name for s in beam_symbols)))
        self.cb_beam_types.ItemsSource = self.family_names # Reusing field for Family selection

        # 3. Levels
        levels = DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements()
        self.level_map = { l.Name: l for l in levels }
        self.cb_levels.ItemsSource = sorted(self.level_map.keys())

    def cad_link_changed(self, sender, e):
        selected_key = self.cb_cad_links.SelectedItem
        if not selected_key: return
        
        instance = self.cad_map[selected_key]
        layers = set()
        
        opt = DB.Options()
        geom = instance.get_Geometry(opt)
        for obj in geom:
            if isinstance(obj, DB.GeometryInstance):
                for sym_obj in obj.GetSymbolGeometry():
                    g_style = doc.GetElement(sym_obj.GraphicsStyleId)
                    if g_style: layers.add(g_style.GraphicsStyleCategory.Name)
        
        self.cb_layers.ItemsSource = sorted(list(layers))

    def close_button_clicked(self, sender, e):
        self.Close()

    def generate_clicked(self, sender, e):
        cad_key = self.cb_cad_links.SelectedItem
        layer_name = self.cb_layers.SelectedItem
        family_name = self.cb_beam_types.SelectedItem
        level_name = self.cb_levels.SelectedItem

        if not all([cad_key, layer_name, family_name, level_name]):
            forms.alert("Please select all required fields.")
            return

        instance = self.cad_map[cad_key]
        level = self.level_map[level_name]

        try:
            default_z_offset = float(self.txt_offset.Text)
        except:
            default_z_offset = -50.0

        # Mapping Height logic (as per user's table)
        def get_height_for_width(w):
            if w <= 200: return 500
            if w <= 250: return 600
            if w <= 300: return 600
            if w <= 400: return 800
            if w <= 500: return 1000
            return w * 2 # Fallback

        # 1. Get GraphicsStyle ID for the layer
        beam_gs_id = None
        import_cat = instance.Category
        for sc in import_cat.SubCategories:
            if sc.Name == layer_name:
                beam_gs_id = sc.GetGraphicsStyle(DB.GraphicsStyleType.Projection).Id
                break
        
        if not beam_gs_id:
            forms.alert("Could not find GraphicsStyle for the selected layer.")
            return

        # 2. Extract Geometry
        raw_curves = []
        opt = DB.Options()
        geom = instance.get_Geometry(opt)
        
        def scan_geo(geo_iterable, transform=None):
            for obj in geo_iterable:
                if isinstance(obj, DB.GeometryInstance):
                    scan_geo(obj.GetInstanceGeometry(), obj.Transform)
                elif isinstance(obj, (DB.Line, DB.Curve)):
                    if obj.GraphicsStyleId == beam_gs_id:
                        if transform: raw_curves.append(obj.CreateTransformed(transform))
                        else: raw_curves.append(obj)
        
        scan_geo(geom)

        # 3. Pair Lines
        lines_h, lines_v = [], []
        for c in raw_curves:
            sp, ep = c.GetEndPoint(0), c.GetEndPoint(1)
            dx, dy = ep.X - sp.X, ep.Y - sp.Y
            length_2d = math.sqrt(dx*dx + dy*dy) * FT_TO_MM
            if length_2d < 10: continue
            angle = abs(math.degrees(math.atan2(dy, dx))) % 180
            entry = {
                'x1': sp.X*FT_TO_MM, 'y1': sp.Y*FT_TO_MM,
                'x2': ep.X*FT_TO_MM, 'y2': ep.Y*FT_TO_MM,
                'z': sp.Z*FT_TO_MM, 'length': length_2d
            }
            if angle < 10 or angle > 170: lines_h.append(entry)
            elif 80 < angle < 100: lines_v.append(entry)

        all_pairs = pair_lines_h(lines_h) + pair_lines_v(lines_v)

        if not all_pairs:
            forms.alert("No parallel pairs found in the selected layer.")
            return

        # 4. Create Beams
        with revit.Transaction("Automated CAD to Beam"):
            created = 0
            for p in all_pairs:
                width_rounded = round(p['width'] / 50) * 50
                height = get_height_for_width(width_rounded)
                
                # Get/Create type
                fam_sym = get_or_create_beam_type(family_name, width_rounded, height)
                if not fam_sym: continue
                if not fam_sym.IsActive: fam_sym.Activate()

                z_ft = level.Elevation + (default_z_offset * MM_TO_FT)

                if p['dir'] == 'H':
                    sp = DB.XYZ(p['main_s']*MM_TO_FT, p['perp']*MM_TO_FT, z_ft)
                    ep = DB.XYZ(p['main_e']*MM_TO_FT, p['perp']*MM_TO_FT, z_ft)
                else:
                    sp = DB.XYZ(p['perp']*MM_TO_FT, p['main_s']*MM_TO_FT, z_ft)
                    ep = DB.XYZ(p['perp']*MM_TO_FT, p['main_e']*MM_TO_FT, z_ft)

                if sp.DistanceTo(ep) < 0.1: continue

                line = DB.Line.CreateBound(sp, ep)
                beam = doc.Create.NewFamilyInstance(line, fam_sym, level, DB.Structure.StructuralType.Beam)
                
                # Set Z Offset parameter
                p_offset = beam.get_Parameter(DB.BuiltInParameter.STRUCTURAL_BEAM_Z_OFFSET_VALUE)
                if p_offset: p_offset.Set(default_z_offset * MM_TO_FT)
                
                created += 1

        forms.alert("Created {} beams successfully!".format(created))
        self.Close()

if __name__ == "__main__":
    window = CADtoBeamWindow()
    window.ShowDialog()
