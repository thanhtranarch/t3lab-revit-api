# -*- coding: utf-8 -*-
"""IFC-SG Suite — event handling and Revit API logic for the unified IFC-SG Suite tool."""

import os
import io
import sys
import json
import codecs
import datetime
import traceback
import clr
import builtins as __builtin__

# Add required assemblies
clr.AddReference("System")
clr.AddReference("System.Windows.Forms")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")
clr.AddReference("System.Xml")

import System
from System.IO import MemoryStream, StringReader
from System.Text import Encoding
from System.Windows import Window, Thickness, Visibility, WindowState
from System.Windows import MessageBox as WPFMessageBox
from System.Windows import MessageBoxButton, MessageBoxResult, MessageBoxImage
from System.Windows.Markup import XamlReader
from System.Windows.Media import BrushConverter, SolidColorBrush, Color
from System.Windows.Controls import (
    DataGridTextColumn, ComboBox, TreeViewItem, StackPanel, TextBlock,
    CheckBox, Button, Grid, ColumnDefinition, RowDefinition,
    ListBoxItem, Border, Orientation, DockPanel
)
from System.Windows.Data import Binding
from System.Windows.Forms import OpenFileDialog, SaveFileDialog, DialogResult as WFDialogResult
from System.Xml import XmlReader

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInParameter, BuiltInCategory,
    Transaction, ElementId, StorageType
)
from pyrevit import script, forms
from GUI.WPF_Base import T3WPFWindow
from GUI.ProgressPauseMixin import ProgressPauseMixin
from Snippets._compat import make_eid, eid_value

# Dynamically find the XAML layout
_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'IFCSG.xaml')
col_map_xaml_path = os.path.join(os.path.dirname(__file__), 'Tools', 'SubtypeDefinerColMap.xaml')

bc = BrushConverter()

# Global variables for Revit session (initialized when showing the dialog)
doc = None
uidoc = None
output = None


# ==============================================================================
# Category Maps & Forge API Helpers
# ==============================================================================

# Subtype Category Map
REVIT_CAT_MAP = {
    "Areas": [BuiltInCategory.OST_Areas],
    "Ceilings": [BuiltInCategory.OST_Ceilings],
    "Columns": [BuiltInCategory.OST_Columns],
    "Curtain Systems": [BuiltInCategory.OST_CurtainWallPanels],
    "Curtain Wall Panels": [BuiltInCategory.OST_CurtainWallPanels],
    "Doors": [BuiltInCategory.OST_Doors],
    "Duct Accessories": [BuiltInCategory.OST_DuctAccessory],
    "Duct Fittings": [BuiltInCategory.OST_DuctFitting],
    "Ducts": [BuiltInCategory.OST_DuctCurves],
    "Electrical Equipment": [BuiltInCategory.OST_ElectricalEquipment],
    "Fire Alarm Devices": [BuiltInCategory.OST_FireAlarmDevices],
    "Floors": [BuiltInCategory.OST_Floors],
    "Furniture": [BuiltInCategory.OST_Furniture],
    "Generic Models": [BuiltInCategory.OST_GenericModel],
    "Levels": [BuiltInCategory.OST_Levels],
    "Lighting Fixtures": [BuiltInCategory.OST_LightingFixtures],
    "Mechanical Equipment": [BuiltInCategory.OST_MechanicalEquipment],
    "Parking": [BuiltInCategory.OST_Parking],
    "Pipe Accessories": [BuiltInCategory.OST_PipeAccessory],
    "Pipe Fittings": [BuiltInCategory.OST_PipeFitting],
    "Pipes": [BuiltInCategory.OST_PipeCurves],
    "Planting": [BuiltInCategory.OST_Planting],
    "Plumbing Fixtures": [BuiltInCategory.OST_PlumbingFixtures],
    "Railings": [BuiltInCategory.OST_StairsRailing],
    "Ramps": [BuiltInCategory.OST_Ramps],
    "Roofs": [BuiltInCategory.OST_Roofs],
    "Rooms": [BuiltInCategory.OST_Rooms],
    "Shaft Openings": [BuiltInCategory.OST_ShaftOpening],
    "Specialty Equipment": [BuiltInCategory.OST_SpecialityEquipment],
    "Sprinklers": [BuiltInCategory.OST_Sprinklers],
    "Stairs": [BuiltInCategory.OST_Stairs],
    "Structural Columns": [BuiltInCategory.OST_StructuralColumns],
    "Structural Foundations": [BuiltInCategory.OST_StructuralFoundation],
    "Structural Framing": [BuiltInCategory.OST_StructuralFraming],
    "Toposolid": [BuiltInCategory.OST_Topography],
    "Walls": [BuiltInCategory.OST_Walls],
    "Windows": [BuiltInCategory.OST_Windows],
}

# Checker Category Map
CATEGORY_MAP = {
    "Areas": BuiltInCategory.OST_Areas,
    "Generic Models": BuiltInCategory.OST_GenericModel,
    "Plumbing Fixtures": BuiltInCategory.OST_PlumbingFixtures,
    "Project Information": None,
    "Ceilings": BuiltInCategory.OST_Ceilings,
    "Doors": BuiltInCategory.OST_Doors,
    "Toposolid": BuiltInCategory.OST_Topography,
    "Floors": BuiltInCategory.OST_Floors,
    "Shaft Openings": BuiltInCategory.OST_ShaftOpening,
    "Windows": BuiltInCategory.OST_Windows,
    "Planting": BuiltInCategory.OST_Planting,
    "Specialty Equipment": BuiltInCategory.OST_SpecialityEquipment,
    "Parking": BuiltInCategory.OST_Parking,
    "Rooms": BuiltInCategory.OST_Rooms,
    "Walls": BuiltInCategory.OST_Walls,
    "Railings": BuiltInCategory.OST_StairsRailing,
    "Ramps": BuiltInCategory.OST_Ramps,
    "Model Groups": BuiltInCategory.OST_IOSModelGroups,
    "Roofs": BuiltInCategory.OST_Roofs,
    "Furniture": BuiltInCategory.OST_Furniture,
    "Stairs": BuiltInCategory.OST_Stairs,
    "Structural Framing": BuiltInCategory.OST_StructuralFraming,
    "Structural Columns": BuiltInCategory.OST_StructuralColumns,
    "Columns": BuiltInCategory.OST_Columns,
    "Structural Foundations": BuiltInCategory.OST_StructuralFoundation,
    "Electrical Equipment": BuiltInCategory.OST_ElectricalEquipment,
    "Duct Accessories": BuiltInCategory.OST_DuctAccessory,
    "Mechanical Equipment": BuiltInCategory.OST_MechanicalEquipment,
    "Pipes": BuiltInCategory.OST_PipeCurves,
    "Pipe Fittings": BuiltInCategory.OST_PipeFitting,
    "Ducts": BuiltInCategory.OST_DuctCurves,
    "Duct Fittings": BuiltInCategory.OST_DuctFitting,
    "Pipe Accessories": BuiltInCategory.OST_PipeAccessory,
}


def _eid_int(eid):
    """Get integer value from ElementId - compatible with Revit 2024-2026+"""
    try:
        return eid.Value  # Revit 2026+
    except:
        return eid.IntegerValue  # Revit 2024/2025


def _get_group_type_id(pg_key):
    """Get ForgeTypeId for parameter group - compatible with Revit 2024-2026+"""
    group_map = {
        "PG_IFC": "Ifc",
        "PG_GEOMETRY": "Geometry",
        "PG_FIRE_PROTECTION": "FireProtection",
        "PG_MATERIALS": "Materials",
        "PG_IDENTITY_DATA": "IdentityData",
        "PG_STRUCTURAL": "Structural",
        "PG_MECHANICAL": "Mechanical",
        "PG_CONSTRUCTION": "Construction",
        "PG_PLUMBING": "Plumbing",
        "PG_ELECTRICAL": "Electrical",
        "PG_PHASING": "Phasing",
        "PG_GENERAL": "General",
        "PG_DATA": "Data",
    }
    
    # Try GroupTypeId first (Revit 2022+, required in 2026)
    try:
        from Autodesk.Revit.DB import GroupTypeId
        attr_name = group_map.get(pg_key, "Ifc")
        return getattr(GroupTypeId, attr_name)
    except:
        pass
    
    # Fallback to BuiltInParameterGroup (Revit 2024/2025)
    try:
        return getattr(BuiltInParameterGroup, pg_key, BuiltInParameterGroup.PG_IFC)
    except:
        pass
    
    return None


# ==============================================================================
# Excel Reader (COM Interop) & Column Mapper for Assigner
# ==============================================================================

def read_excel_headers(filepath):
    """Read sheet names and column headers from Excel without full parse."""
    clr.AddReference("Microsoft.Office.Interop.Excel")
    import Microsoft.Office.Interop.Excel as Excel

    app = Excel.ApplicationClass()
    app.Visible = False
    app.DisplayAlerts = False
    result = {"sheets": [], "headers": {}}

    try:
        wb = app.Workbooks.Open(filepath)
        for i in range(1, wb.Sheets.Count + 1):
            sname = wb.Sheets[i].Name
            result["sheets"].append(sname)
            headers = []
            ws = wb.Sheets[i]
            for c in range(1, min(ws.UsedRange.Columns.Count + 1, 30)):
                val = ws.Cells[1, c].Value2
                h = str(val).strip().replace("\n", " ") if val else "(empty)"
                headers.append(h)
            result["headers"][sname] = headers
        wb.Close(False)
    except Exception as ex:
        if output:
            output.print_md("**Excel Error:** {}".format(str(ex)))
        return None
    finally:
        try:
            app.Quit()
        except:
            pass
    return result


def show_column_mapping_dialog(excel_info, filepath):
    """Show a WPF dialog for user to map Excel columns to mapping fields."""
    with io.open(col_map_xaml_path, 'r', encoding='utf-8') as f:
        xaml_content = f.read()
    stream = MemoryStream(Encoding.UTF8.GetBytes(xaml_content))
    win = XamlReader.Load(stream)
    stream.Close()

    cmbSheet = win.FindName("cmbSheet")
    field_combos = {
        "component": win.FindName("cmbComponent"),
        "entity": win.FindName("cmbEntity"),
        "subtype": win.FindName("cmbSubtype"),
        "revit": win.FindName("cmbRevit"),
        "agency": win.FindName("cmbAgency"),
    }

    result = {"ok": False}

    AUTO_KEYWORDS = {
        "component": ["identified component", "component", "element name"],
        "entity": ["ifc4", "ifc entity", "entities"],
        "subtype": ["ifc sub", "sub type", "predefined"],
        "revit": ["suggested revit", "revit representation", "revit"],
        "agency": ["agency"],
    }

    def populate_combos(sheet_name):
        headers = excel_info["headers"].get(sheet_name, [])
        for field, cmb in field_combos.items():
            cmb.Items.Clear()
            cmb.Items.Add("(not mapped)")
            best_idx = 0
            for i, h in enumerate(headers):
                cmb.Items.Add("Col {}: {}".format(i + 1, h))
                h_lower = h.lower()
                for kw in AUTO_KEYWORDS.get(field, []):
                    if kw in h_lower and best_idx == 0:
                        best_idx = i + 1
            cmb.SelectedIndex = best_idx

    # Populate sheets
    for sname in excel_info["sheets"]:
        cmbSheet.Items.Add(sname)
    best_sheet = 0
    for i, sname in enumerate(excel_info["sheets"]):
        if "pilot" in sname.lower() or "mapping" in sname.lower():
            best_sheet = i
            break
    cmbSheet.SelectedIndex = best_sheet

    def on_sheet_changed(s, e):
        sel = cmbSheet.SelectedItem
        if sel:
            populate_combos(str(sel))

    cmbSheet.SelectionChanged += on_sheet_changed
    populate_combos(excel_info["sheets"][best_sheet])

    def on_ok(s, e):
        comp_idx = field_combos["component"].SelectedIndex
        ent_idx = field_combos["entity"].SelectedIndex
        if comp_idx == 0 or ent_idx == 0:
            WPFMessageBox.Show("Component Name and IFC4 Entity are required.",
                               "Missing Fields", MessageBoxButton.OK,
                               MessageBoxImage.Warning)
            return
        result["ok"] = True
        result["sheet"] = str(cmbSheet.SelectedItem)
        for field, cmb in field_combos.items():
            idx = cmb.SelectedIndex
            result[field] = idx if idx > 0 else 0
        win.Close()

    def on_cancel(s, e):
        win.Close()

    win.FindName("btnOK").Click += on_ok
    win.FindName("btnCancel").Click += on_cancel
    top_cancel = win.FindName("btnCancelTop")
    if top_cancel:
        top_cancel.Click += on_cancel
    win.ShowDialog()
    return result


def load_mapping_with_dialog(filepath):
    """Load Excel mapping rules using the column mapper dialog."""
    excel_info = read_excel_headers(filepath)
    if not excel_info:
        return None

    col_result = show_column_mapping_dialog(excel_info, filepath)
    if not col_result.get("ok"):
        return None

    clr.AddReference("Microsoft.Office.Interop.Excel")
    import Microsoft.Office.Interop.Excel as Excel

    app = Excel.ApplicationClass()
    app.Visible = False
    app.DisplayAlerts = False
    mapping = {}

    try:
        wb = app.Workbooks.Open(filepath)
        ws = wb.Sheets[col_result["sheet"]]
        rows = ws.UsedRange.Rows.Count

        c_comp = col_result["component"]
        c_ent = col_result["entity"]
        c_sub = col_result.get("subtype", 0)
        c_rev = col_result.get("revit", 0)
        c_agency = col_result.get("agency", 0)

        for r in range(2, rows + 1):
            raw_comp = ws.Cells[r, c_comp].Value2
            if raw_comp is None:
                continue
            comp = str(raw_comp).strip()
            if not comp:
                continue

            raw_entity = ws.Cells[r, c_ent].Value2
            entity = str(raw_entity).strip() if raw_entity else ""

            subtypes_in_cell = []
            if c_sub:
                raw_sub = ws.Cells[r, c_sub].Value2
                if raw_sub and str(raw_sub).strip() not in ("N.A", "N.A.", "nan", ""):
                    for s in str(raw_sub).split(","):
                        s = s.strip()
                        if s and s not in ("N.A", "N.A."):
                            subtypes_in_cell.append(s)

            revit_cat = ""
            if c_rev:
                raw_revit = ws.Cells[r, c_rev].Value2
                revit_cat = str(raw_revit).strip() if raw_revit else ""

            agency = ""
            if c_agency:
                raw_ag = ws.Cells[r, c_agency].Value2
                agency = str(raw_ag).strip() if raw_ag else ""

            if comp not in mapping:
                mapping[comp] = {
                    "ifc_entities": set(), "subtypes": set(),
                    "revit_categories": set(), "agencies": set(),
                }
            m = mapping[comp]
            if entity:
                m["ifc_entities"].add(entity)
            for st in subtypes_in_cell:
                m["subtypes"].add(st)
            if revit_cat and revit_cat not in ("N.A", "N.A."):
                m["revit_categories"].add(revit_cat)
            if agency:
                m["agencies"].add(agency)

        wb.Close(False)
    except Exception as ex:
        if output:
            output.print_md("**Excel Error:** {}".format(str(ex)))
        return None
    finally:
        try:
            app.Quit()
        except:
            pass

    for comp, m in mapping.items():
        m["ifc_entities"] = sorted(m["ifc_entities"])
        m["subtypes"] = sorted(m["subtypes"])
        m["revit_categories"] = sorted(m["revit_categories"])
        m["agencies"] = sorted(m["agencies"])

    return mapping


# ==============================================================================
# IFC Parameter Read/Write Helpers
# ==============================================================================

def _try_bip_get(elem, bip_name):
    try:
        bip = getattr(BuiltInParameter, bip_name, None)
        if bip is not None:
            p = elem.get_Parameter(bip)
            if p and p.HasValue:
                v = p.AsString()
                if v:
                    return v
    except:
        pass
    return None


def _try_lookup_get(elem, name):
    try:
        p = elem.LookupParameter(name)
        if p and p.HasValue:
            v = p.AsString()
            if v:
                return v
    except:
        pass
    return None


def _try_bip_set(elem, bip_name, value):
    try:
        bip = getattr(BuiltInParameter, bip_name, None)
        if bip is not None:
            p = elem.get_Parameter(bip)
            if p and not p.IsReadOnly:
                p.Set(value)
                return True
    except:
        pass
    return False


def _try_lookup_set(elem, name, value):
    try:
        p = elem.LookupParameter(name)
        if p and not p.IsReadOnly:
            p.Set(value)
            return True
    except:
        pass
    return False


def get_ifc_export_as(elem):
    return (_try_bip_get(elem, "IFC_EXPORT_ELEMENT_TYPE_AS")
            or _try_bip_get(elem, "IFC_EXPORT_ELEMENT_AS")
            or _try_lookup_get(elem, "IfcExportAs")
            or _try_lookup_get(elem, "Export to IFC As")
            or "")


def get_ifc_predefined_type(elem):
    return (_try_bip_get(elem, "IFC_EXPORT_PREDEFINEDTYPE_TYPE")
            or _try_bip_get(elem, "IFC_EXPORT_PREDEFINEDTYPE")
            or _try_lookup_get(elem, "IfcExportType")
            or _try_lookup_get(elem, "IFC Predefined Type")
            or "")


def set_ifc_export_as(elem, value, use_type=True):
    if _try_bip_set(elem, "IFC_EXPORT_ELEMENT_TYPE_AS", value):
        return True
    if _try_bip_set(elem, "IFC_EXPORT_ELEMENT_AS", value):
        return True
    for name in ["Export to IFC As", "Export Type to IFC As",
                 "IfcExportAs", "IFCExportAs"]:
        if _try_lookup_set(elem, name, value):
            return True
    return False


def set_ifc_predefined_type(elem, value, use_type=True):
    if _try_bip_set(elem, "IFC_EXPORT_PREDEFINEDTYPE_TYPE", value):
        return True
    if _try_bip_set(elem, "IFC_EXPORT_PREDEFINEDTYPE", value):
        return True
    for name in ["IFC Predefined Type", "Type IFC Predefined Type",
                 "IfcExportType", "IFCExportType"]:
        if _try_lookup_set(elem, name, value):
            return True
    return False


def set_ifc_object_type(elem, value, use_type=True):
    for name in ["IfcObjectType", "IFCObjectType", "ObjectType"]:
        if _try_lookup_set(elem, name, value):
            return True
    return False


class TypeRow(object):
    """Represent one type row in the Subtype Assigner DataGrid."""
    def __init__(self, family, type_name, count, cur_entity, cur_subtype,
                 status, type_elem, items):
        self.Family = family
        self.TypeName = type_name
        self.Count = count
        self.CurEntity = cur_entity
        self.CurSubtype = cur_subtype
        self.Status = status
        self._type_elem = type_elem
        self._items = items


def collect_elements_for_bics(bic_list):
    elems = []
    for bic in bic_list:
        try:
            found = FilteredElementCollector(doc).OfCategory(bic) \
                .WhereElementIsNotElementType().ToElements()
            for e in found:
                elems.append(e)
        except:
            pass
    return elems


def build_type_rows(elems):
    type_groups = {}
    for e in elems:
        type_id = e.GetTypeId()
        type_elem = doc.GetElement(type_id) if type_id != ElementId.InvalidElementId else None
        fam_name = "N/A"
        type_name = "N/A"
        if type_elem:
            try:
                fam_name = type_elem.get_Parameter(
                    BuiltInParameter.ALL_MODEL_FAMILY_NAME).AsString() or "N/A"
            except:
                pass
            try:
                type_name = type_elem.get_Parameter(
                    BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString() or "N/A"
            except:
                pass

        cur_entity = ""
        cur_subtype = ""
        if type_elem:
            cur_entity = get_ifc_export_as(type_elem)
            cur_subtype = get_ifc_predefined_type(type_elem)
        if not cur_entity:
            cur_entity = get_ifc_export_as(e)
        if not cur_subtype:
            cur_subtype = get_ifc_predefined_type(e)

        key = (fam_name, type_name)
        if key not in type_groups:
            type_groups[key] = {
                "family": fam_name, "type": type_name,
                "count": 0, "cur_entity": cur_entity,
                "cur_subtype": cur_subtype,
                "type_elem": type_elem, "items": [],
            }
        type_groups[key]["count"] += 1
        type_groups[key]["items"].append({"elem": e, "type_elem": type_elem})

    rows = []
    for key in sorted(type_groups.keys()):
        g = type_groups[key]
        if g["cur_entity"] and g["cur_subtype"]:
            status = "OK"
        elif g["cur_entity"]:
            status = "No Sub"
        else:
            status = "Not Set"
        rows.append(TypeRow(
            family=g["family"],
            type_name=g["type"],
            count=g["count"],
            cur_entity=g["cur_entity"] or "(default)",
            cur_subtype=g["cur_subtype"] or "(none)",
            status=status,
            type_elem=g["type_elem"],
            items=g["items"],
        ))
    return rows


# ==============================================================================
# Compliance Checker Configuration Parser & Checker Engine
# ==============================================================================

class ParamCheckConfig:
    """Config structure to represent XML, Excel or JSON compliance check rules."""
    def __init__(self):
        self.name = ""
        self.source = ""
        self.description = ""
        self.disciplines = {}

    @staticmethod
    def from_xml(filepath):
        """Parse Autodesk Model Checker XML configuration."""
        import xml.etree.ElementTree as ET
        
        config = ParamCheckConfig()
        config.source = "XML"
        
        tree = ET.parse(filepath)
        root = tree.getroot()
        config.name = root.get("Name", "Imported XML Config")
        config.description = root.get("Description", "")
        
        for heading in root.findall("Heading"):
            disc_name = heading.get("HeadingText", "")
            disc_enabled = heading.get("IsChecked", "True") == "True"
            
            categories = {}
            for section in heading.findall("Section"):
                cat_name = section.get("SectionName", "")
                cat_enabled = section.get("IsChecked", "True") == "True"
                
                params = []
                for check in section.findall("Check"):
                    param_name = check.get("CheckName", "")
                    if param_name:
                        params.append(param_name)
                
                if params:
                    categories[cat_name] = {
                        "enabled": cat_enabled,
                        "params": sorted(set(params))
                    }
            
            if categories:
                config.disciplines[disc_name] = {
                    "enabled": disc_enabled,
                    "categories": categories
                }
        
        return config

    @staticmethod
    def from_excel(filepath):
        """Parse Excel parameter mapping file."""
        config = ParamCheckConfig()
        config.source = "Excel"
        config.name = os.path.splitext(os.path.basename(filepath))[0]
        
        try:
            clr.AddReference('Microsoft.Office.Interop.Excel')
            from Microsoft.Office.Interop import Excel as ExcelInterop
            
            excel_app = ExcelInterop.ApplicationClass()
            excel_app.Visible = False
            excel_app.DisplayAlerts = False
            
            wb = excel_app.Workbooks.Open(filepath)
            ws = wb.Sheets[1]
            
            used = ws.UsedRange
            rows = used.Rows.Count
            
            for r in range(2, rows + 1):
                disc = str(ws.Cells[r, 1].Value2 or "").strip()
                cat = str(ws.Cells[r, 2].Value2 or "").strip()
                param = str(ws.Cells[r, 3].Value2 or "").strip()
                required = str(ws.Cells[r, 4].Value2 or "Yes").strip().lower()
                
                if not disc or not cat or not param:
                    continue
                if required in ("no", "false", "0"):
                    continue
                
                if disc not in config.disciplines:
                    config.disciplines[disc] = {"enabled": True, "categories": {}}
                if cat not in config.disciplines[disc]["categories"]:
                    config.disciplines[disc]["categories"][cat] = {"enabled": True, "params": []}
                
                if param not in config.disciplines[disc]["categories"][cat]["params"]:
                    config.disciplines[disc]["categories"][cat]["params"].append(param)
            
            wb.Close(False)
            excel_app.Quit()
            System.Runtime.InteropServices.Marshal.ReleaseComObject(excel_app)
            
        except Exception as e:
            raise Exception("Excel parse error: {}".format(str(e)))
        
        return config

    @staticmethod
    def from_json(filepath):
        """Load from saved JSON config."""
        config = ParamCheckConfig()
        with codecs.open(filepath, 'r', 'utf-8') as f:
            data = json.load(f)
        config.name = data.get("name", "")
        config.source = data.get("source", "JSON")
        config.description = data.get("description", "")
        config.disciplines = data.get("disciplines", {})
        return config

    def to_json(self, filepath):
        """Save config as JSON."""
        data = {
            "name": self.name,
            "source": self.source,
            "description": self.description,
            "disciplines": self.disciplines
        }
        with codecs.open(filepath, 'w', 'utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_total_stats(self):
        """Return counts of total disciplines, categories, and parameters."""
        total_disc = len(self.disciplines)
        total_cat = 0
        total_param = 0
        for d in self.disciplines.values():
            cats = d.get("categories", {})
            total_cat += len(cats)
            for c in cats.values():
                total_param += len(c.get("params", []))
        return total_disc, total_cat, total_param


class CheckResult:
    """Store audit results for one parameter check inside a category."""
    def __init__(self, discipline, category, param_name, status, 
                 total_elements=0, missing_count=0, element_ids=None):
        self.discipline = discipline
        self.category = category
        self.param_name = param_name
        self.status = status  # "pass", "fail", "warning", "no_elements"
        self.total_elements = total_elements
        self.missing_count = missing_count
        self.element_ids = element_ids or []


class ParamChecker:
    """Run parameter compliance audits against elements in the current Revit doc."""
    def __init__(self, document):
        self.doc = document
        self._element_cache = {}

    def _get_elements(self, category_name):
        if category_name in self._element_cache:
            return self._element_cache[category_name]
        
        bic = CATEGORY_MAP.get(category_name)
        elements = []
        
        if category_name == "Project Information":
            elements = [self.doc.ProjectInformation]
        elif bic is not None:
            try:
                collector = FilteredElementCollector(self.doc)\
                    .OfCategory(bic)\
                    .WhereElementIsNotElementType()
                elements = list(collector)
            except:
                elements = []
        
        self._element_cache[category_name] = elements
        return elements

    def _check_param_has_value(self, element, param_name):
        for p in element.Parameters:
            if p.Definition.Name == param_name:
                if not p.HasValue:
                    return False
                if p.StorageType == StorageType.String:
                    val = p.AsString()
                    return val is not None and val.strip() != ""
                elif p.StorageType == StorageType.Integer:
                    return True
                elif p.StorageType == StorageType.Double:
                    return True
                elif p.StorageType == StorageType.ElementId:
                    return p.AsElementId() != ElementId.InvalidElementId
                return True
        return False

    def run_check(self, config, progress_callback=None, cancel_check=None):
        results = []
        self._element_cache = {}

        total_checks = 0
        for d_data in config.disciplines.values():
            if not d_data.get("enabled", True):
                continue
            for c_data in d_data.get("categories", {}).values():
                if not c_data.get("enabled", True):
                    continue
                total_checks += len(c_data.get("params", []))

        current = 0

        for disc_name, disc_data in config.disciplines.items():
            if not disc_data.get("enabled", True):
                continue
            if cancel_check and cancel_check():
                break

            for cat_name, cat_data in disc_data.get("categories", {}).items():
                if not cat_data.get("enabled", True):
                    continue
                if cancel_check and cancel_check():
                    break

                elements = self._get_elements(cat_name)
                
                if not elements:
                    for param_name in cat_data.get("params", []):
                        results.append(CheckResult(
                            disc_name, cat_name, param_name,
                            "no_elements", 0, 0))
                        current += 1
                        if progress_callback:
                            progress_callback(current, total_checks)
                    continue
                
                for param_name in cat_data.get("params", []):
                    missing_ids = []
                    total = len(elements)
                    
                    for el in elements:
                        try:
                            if not self._check_param_has_value(el, param_name):
                                missing_ids.append(_eid_int(el.Id))
                        except:
                            pass
                    
                    missing = len(missing_ids)
                    
                    if missing == 0:
                        status = "pass"
                    elif missing == total:
                        status = "fail"
                    else:
                        status = "warning"
                    
                    results.append(CheckResult(
                        disc_name, cat_name, param_name,
                        status, total, missing, missing_ids[:100]))
                    
                    current += 1
                    if progress_callback:
                        progress_callback(current, total_checks)
        
        return results


# ==============================================================================
# Excel Report Generator for compliance checks
# ==============================================================================

class ExcelReporter:
    def __init__(self, doc):
        self.doc = doc

    def _rgb(self, r, g, b):
        return r + (g * 256) + (b * 256 * 256)

    def generate(self, config, results, filepath):
        clr.AddReference('Microsoft.Office.Interop.Excel')
        from Microsoft.Office.Interop import Excel as ExcelInterop
        
        excel_app = ExcelInterop.ApplicationClass()
        excel_app.Visible = False
        excel_app.DisplayAlerts = False
        
        try:
            wb = excel_app.Workbooks.Add()
            
            # --- Sheet 1: Summary ---
            ws = wb.Sheets[1]
            ws.Name = "Summary"
            
            ws.Cells[1, 1].Value2 = "IFC-SG PARAMETER CHECK REPORT"
            ws.Cells[1, 1].Font.Size = 16
            ws.Cells[1, 1].Font.Bold = True
            ws.Range["A1:E1"].Merge()
            ws.Range["A1:E1"].Interior.Color = self._rgb(240, 204, 136)
            
            row = 3
            info = [
                ("Project", self.doc.ProjectInformation.Name or "N/A"),
                ("Config", config.name),
                ("Source", config.source),
                ("Date", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ]
            for label, val in info:
                ws.Cells[row, 1].Value2 = label
                ws.Cells[row, 1].Font.Bold = True
                ws.Cells[row, 2].Value2 = val
                row += 1
            
            row += 1
            total = len(results)
            passed = len([r for r in results if r.status == "pass"])
            failed = len([r for r in results if r.status == "fail"])
            warning = len([r for r in results if r.status == "warning"])
            no_elem = len([r for r in results if r.status == "no_elements"])
            
            stats = [("Total Checks", total), ("Passed", passed),
                     ("Failed (all missing)", failed), ("Warning (partial)", warning),
                     ("No Elements", no_elem)]
            for label, val in stats:
                ws.Cells[row, 1].Value2 = label
                ws.Cells[row, 1].Font.Bold = True
                ws.Cells[row, 2].Value2 = val
                row += 1
            
            ws.Columns["A:E"].AutoFit()
            
            # --- Sheet 2: Detailed Results ---
            ws2 = wb.Sheets.Add(After=wb.Sheets[wb.Sheets.Count])
            ws2.Name = "Detailed Results"
            
            headers = ["Discipline", "Category", "Parameter", "Status",
                       "Total Elements", "Missing Count", "Element IDs (sample)"]
            for i, h in enumerate(headers, 1):
                ws2.Cells[1, i].Value2 = h
                ws2.Cells[1, i].Font.Bold = True
                ws2.Cells[1, i].Interior.Color = self._rgb(240, 204, 136)
            
            row = 2
            status_colors = {
                "pass": self._rgb(200, 230, 201),
                "fail": self._rgb(255, 205, 210),
                "warning": self._rgb(255, 236, 179),
                "no_elements": self._rgb(224, 224, 224),
            }
            
            for r in results:
                ws2.Cells[row, 1].Value2 = r.discipline
                ws2.Cells[row, 2].Value2 = r.category
                ws2.Cells[row, 3].Value2 = r.param_name
                ws2.Cells[row, 4].Value2 = r.status.upper()
                ws2.Cells[row, 5].Value2 = r.total_elements
                ws2.Cells[row, 6].Value2 = r.missing_count
                ws2.Cells[row, 7].Value2 = ", ".join(str(eid) for eid in r.element_ids[:20])
                
                color = status_colors.get(r.status)
                if color:
                    ws2.Cells[row, 4].Interior.Color = color
                row += 1
            
            ws2.Columns["A:G"].AutoFit()
            
            # --- Sheet 3: Failed Only ---
            ws3 = wb.Sheets.Add(After=wb.Sheets[wb.Sheets.Count])
            ws3.Name = "Failed Parameters"
            
            fail_headers = ["Discipline", "Category", "Parameter", "Missing Count", "Total Elements"]
            for i, h in enumerate(fail_headers, 1):
                ws3.Cells[1, i].Value2 = h
                ws3.Cells[1, i].Font.Bold = True
                ws3.Cells[1, i].Interior.Color = self._rgb(255, 205, 210)
            
            row = 2
            for r in results:
                if r.status in ("fail", "warning"):
                    ws3.Cells[row, 1].Value2 = r.discipline
                    ws3.Cells[row, 2].Value2 = r.category
                    ws3.Cells[row, 3].Value2 = r.param_name
                    ws3.Cells[row, 4].Value2 = r.missing_count
                    ws3.Cells[row, 5].Value2 = r.total_elements
                    row += 1
            
            if row == 2:
                ws3.Cells[2, 1].Value2 = "All parameters passed!"
                ws3.Range["A2:E2"].Merge()
            
            ws3.Columns["A:E"].AutoFit()
            
            wb.SaveAs(filepath)
            wb.Close()
            excel_app.Quit()
            System.Runtime.InteropServices.Marshal.ReleaseComObject(excel_app)
            return True
            
        except Exception as e:
            try:
                wb.Close(False)
                excel_app.Quit()
                System.Runtime.InteropServices.Marshal.ReleaseComObject(excel_app)
            except:
                pass
            raise e


# ==============================================================================
# Unified IFC-SG Suite Window
# ==============================================================================

class IFCSGSuiteWindow(T3WPFWindow):

    # ProgressPauseMixin — IFCSG.xaml progress panel element names
    PP_PANEL      = "ifc_progress_panel"
    PP_BAR        = "ifc_pb"
    PP_PAUSE      = "ifc_btn_pause"
    PP_STOP       = "ifc_btn_stop"
    PP_PAUSE_ICON = "ifc_btn_pause_icon"
    PP_PAUSE_TEXT = "ifc_btn_pause_label"
    PP_STATUS     = "txtStatus"
    PP_STOP_MSG   = u"Stopping… finishing current check"

    def __init__(self, script_dir, revit):
        T3WPFWindow.__init__(self, _XAML)
        self._script_dir = script_dir
        self._revit = revit
        self.doc = doc
        self.uidoc = uidoc

        # --- TAB CONTROLS ---
        self.main_tab_control = self.FindName("main_tab_control")
        self.btn_tab_assigner = self.FindName("btn_tab_assigner")
        self.btn_tab_checker = self.FindName("btn_tab_checker")
        
        self.btn_tab_assigner.Checked += self._on_tab_changed
        self.btn_tab_checker.Checked += self._on_tab_changed

        # Chrome Window controls
        self.btn_minimize = self.FindName("btn_minimize")
        self.btn_maximize = self.FindName("btn_maximize")
        self.btn_close_chrome = self.FindName("btn_close_chrome")
        
        self.btn_minimize.Click += self._minimize
        self.btn_maximize.Click += self._maximize
        self.btn_close_chrome.Click += self._close_chrome

        # --- TAB 1: SUBTYPE ASSIGNER INITIALIZATION ---
        self.txtHeader = self.FindName("txtHeader") # Excel filename/status text block
        self.btnLoadExcel = self.FindName("btnLoadExcel")
        self.btnAutoAssign = self.FindName("btnAutoAssign")
        
        self.txtFilter = self.FindName("txtFilter")
        self.lstComponents = self.FindName("lstComponents")
        self.txtSummary = self.FindName("txtSummary")
        self.txtCompName = self.FindName("txtCompName")
        self.txtCompInfo = self.FindName("txtCompInfo")
        self.txtAgencies = self.FindName("txtAgencies")
        self.cmbSubtype = self.FindName("cmbSubtype")
        self.btnApply = self.FindName("btnApply")
        self.btnApplyAll = self.FindName("btnApplyAll")
        self.btn_ai_auto_match = self.FindName("btn_ai_auto_match")
        self.dgTypes = self.FindName("dgTypes")
        self.chkApplyType = self.FindName("chkApplyType")
        self.chkApplyEntity = self.FindName("chkApplyEntity")
        self.chkSetObjectType = self.FindName("chkSetObjectType")

        self._setup_assigner_columns()
        self._style_assigner_column_headers()

        # Event Handlers for Tab 1
        self.lstComponents.SelectionChanged += self._on_comp_selected
        self.btnLoadExcel.Click += self._on_load_excel
        self.btnAutoAssign.Click += self._on_auto_assign
        if self.btn_ai_auto_match is not None:
            self.btn_ai_auto_match.Click += self._on_ai_auto_match
        self.btnApply.Click += self._on_apply_selected
        self.btnApplyAll.Click += self._on_apply_all
        self.txtFilter.TextChanged += self._on_filter_changed
        self._update_ai_status()

        # State variables for Tab 1
        self.mapping = {}
        self.current_comp = ""
        self.current_rows = []
        self._comp_names = []
        self._all_entries = []
        self._all_comp_names_list = []

        # --- TAB 2: COMPLIANCE CHECKER INITIALIZATION ---
        self.cmbConfig = self.FindName("cmbConfig")
        self.btnImportXML = self.FindName("btnImportXML")
        self.btnImportExcel = self.FindName("btnImportExcel")
        self.btnSaveConfig = self.FindName("btnSaveConfig")
        self.btnDeleteConfig = self.FindName("btnDeleteConfig")
        
        self.txtTotalParams = self.FindName("txtTotalParams")
        self.txtCategories = self.FindName("txtCategories")
        self.txtPassed = self.FindName("txtPassed")
        self.txtFailed = self.FindName("txtFailed")
        self.txtWarning = self.FindName("txtWarning")
        self.txtNoElem = self.FindName("txtNoElem")
        
        self.tvCategories = self.FindName("tvCategories")
        self.btnExpandAll = self.FindName("btnExpandAll")
        self.btnCollapseAll = self.FindName("btnCollapseAll")
        
        self.txtResultHeader = self.FindName("txtResultHeader")
        self.spResults = self.FindName("spResults")
        
        self.btnFilterAll = self.FindName("btnFilterAll")
        self.btnFilterFail = self.FindName("btnFilterFail")
        self.btnFilterWarn = self.FindName("btnFilterWarn")
        self.btnFilterPass = self.FindName("btnFilterPass")
        self.btnSelectAllFailed = self.FindName("btnSelectAllFailed")
        self.txtSearch = self.FindName("txtSearch")
        
        self.txtStatus = self.FindName("txtStatus")
        self.btnRunCheck = self.FindName("btnRunCheck")
        self.btnExportExcel = self.FindName("btnExportExcel")

        # Event Handlers for Tab 2
        self.btnImportXML.Click += self._on_import_xml
        self.btnImportExcel.Click += self._on_import_excel
        self.btnSaveConfig.Click += self._on_save_config
        self.btnDeleteConfig.Click += self._on_delete_config
        self.cmbConfig.SelectionChanged += self._on_config_changed
        self.btnExpandAll.Click += self._on_expand_all
        self.btnCollapseAll.Click += self._on_collapse_all
        self.btnFilterAll.Click += lambda s, e: self._apply_filter("all")
        self.btnFilterFail.Click += lambda s, e: self._apply_filter("fail")
        self.btnFilterWarn.Click += lambda s, e: self._apply_filter("warning")
        self.btnFilterPass.Click += lambda s, e: self._apply_filter("pass")
        self.txtSearch.TextChanged += lambda s, e: self._apply_filter(self._current_filter)
        self.btnSelectAllFailed.Click += self._on_select_all_failed
        self.btnRunCheck.Click += self._on_run_check
        self.btnExportExcel.Click += self._on_export_excel

        # Progress + Pause/Stop (ProgressPauseMixin) for the Run Check batch
        self.ifc_progress_panel = self.FindName("ifc_progress_panel")
        self.ifc_pb             = self.FindName("ifc_pb")
        self.ifc_btn_pause      = self.FindName("ifc_btn_pause")
        self.ifc_btn_pause_icon = self.FindName("ifc_btn_pause_icon")
        self.ifc_btn_pause_label = self.FindName("ifc_btn_pause_label")
        self.ifc_btn_stop       = self.FindName("ifc_btn_stop")
        if self.ifc_btn_pause is not None:
            self.ifc_btn_pause.Click += self.pause_resume_clicked
        if self.ifc_btn_stop is not None:
            self.ifc_btn_stop.Click += self.stop_clicked

        # State variables for Tab 2
        self.config = None
        self.results = None
        self.all_results = []
        self._current_filter = "all"
        
        self.checker = ParamChecker(self.doc)
        self.reporter = ExcelReporter(self.doc)

        # Load saved compliance configurations
        self.configs_dir = os.path.join(self._script_dir, "configs")
        self.reports_dir = os.path.join(self._script_dir, "reports")
        for d in [self.configs_dir, self.reports_dir]:
            if not os.path.exists(d):
                try:
                    os.makedirs(d)
                except:
                    pass
        self._load_saved_configs()

        # Force initial tab content to render: btn_tab_assigner.IsChecked was already
        # True when the XAML was parsed, so its Checked event fired before the
        # += wiring above and main_tab_control.SelectedIndex was never explicitly
        # set (same fix as ManaSheets/ManaViews/ManaAnno/ManaPara/ManaContains).
        self.main_tab_control.SelectedIndex = 0

    # ==============================================================================
    # Window Chrome Handlers
    # ==============================================================================

    def _minimize(self, sender, e):
        self.WindowState = WindowState.Minimized

    def _maximize(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def _close_chrome(self, sender, e):
        self.Close()

    def _on_tab_changed(self, sender, e):
        if self.btn_tab_assigner.IsChecked:
            self.main_tab_control.SelectedIndex = 0
        elif self.btn_tab_checker.IsChecked:
            self.main_tab_control.SelectedIndex = 1

    # ==============================================================================
    # Tab 1: Subtype Assigner Logic
    # ==============================================================================

    def _setup_assigner_columns(self):
        from System.Windows.Controls import DataGridLength
        cols = [
            ("Family", "Family", 190),
            ("Type", "TypeName", 180),
            ("Qty", "Count", 45),
            ("Current IFC Entity", "CurEntity", 160),
            ("Current Subtype", "CurSubtype", 140),
            ("Status", "Status", 65),
        ]
        for header, binding_path, width in cols:
            col = DataGridTextColumn()
            col.Header = header
            col.Binding = Binding(binding_path)
            col.Width = DataGridLength(width)
            self.dgTypes.Columns.Add(col)

    def _style_assigner_column_headers(self):
        try:
            from System.Windows import Style as WPFStyle, Setter
            from System.Windows.Controls.Primitives import DataGridColumnHeader
            from System.Windows.Controls import Control
            
            style = WPFStyle(DataGridColumnHeader)
            style.Setters.Add(Setter(Control.BackgroundProperty, bc.ConvertFromString("#FFFFFF")))
            style.Setters.Add(Setter(Control.ForegroundProperty, bc.ConvertFromString("#9A9AA2")))
            style.Setters.Add(Setter(Control.FontWeightProperty, System.Windows.FontWeights.Bold))
            style.Setters.Add(Setter(Control.FontSizeProperty, 11.0))
            style.Setters.Add(Setter(Control.PaddingProperty, Thickness(10, 8, 10, 8)))
            style.Setters.Add(Setter(Control.BorderBrushProperty, bc.ConvertFromString("#E2E8F0")))
            style.Setters.Add(Setter(Control.BorderThicknessProperty, Thickness(0, 0, 0, 1)))
            self.dgTypes.ColumnHeaderStyle = style
        except:
            pass

    def _populate_datagrid(self, rows):
        clr.AddReference("System.Data")
        from System.Data import DataTable

        dt = DataTable()
        dt.Columns.Add("Family")
        dt.Columns.Add("TypeName")
        dt.Columns.Add("Count", System.Type.GetType("System.Int32"))
        dt.Columns.Add("CurEntity")
        dt.Columns.Add("CurSubtype")
        dt.Columns.Add("Status")

        for r in rows:
            row = dt.NewRow()
            row["Family"] = r.Family
            row["TypeName"] = r.TypeName
            row["Count"] = r.Count
            row["CurEntity"] = r.CurEntity
            row["CurSubtype"] = r.CurSubtype
            row["Status"] = r.Status
            dt.Rows.Add(row)

        self.dgTypes.ItemsSource = dt.DefaultView

    def _on_load_excel(self, sender, args):
        dlg = OpenFileDialog()
        dlg.Title = "Select IFC-SG Industry Mapping Excel"
        dlg.Filter = "Excel Files|*.xlsx;*.xls"
        if dlg.ShowDialog() != WFDialogResult.OK:
            return

        self.txtHeader.Text = "Reading Excel headers..."
        self.UpdateLayout()

        mapping = load_mapping_with_dialog(dlg.FileName)
        if not mapping:
            self.txtHeader.Text = "Load Industry Mapping Excel to start"
            return

        self.mapping = mapping
        import System.IO
        fname = System.IO.Path.GetFileName(dlg.FileName)
        self.txtHeader.Text = "Loaded: {} ({} components)".format(fname, len(mapping))
        self._populate_component_list()

    def _populate_component_list(self):
        self._comp_names = []
        total_elems = 0
        entries = []

        for comp_name in sorted(self.mapping.keys()):
            m = self.mapping[comp_name]
            bics = []
            for rc in m["revit_categories"]:
                if rc in REVIT_CAT_MAP:
                    bics.extend(REVIT_CAT_MAP[rc])
            elems = collect_elements_for_bics(bics) if bics else []
            count = len(elems)
            total_elems += count
            m["_bics"] = bics
            m["_elements"] = elems

            entity_str = ", ".join(m["ifc_entities"][:2])
            if len(m["ifc_entities"]) > 2:
                entity_str += "..."
            sub_count = len(m["subtypes"])

            entries.append((comp_name, count, entity_str, sub_count))

        self.lstComponents.Items.Clear()
        self._comp_names = []
        self._all_entries = entries
        self._all_comp_names_list = [e[0] for e in entries]

        for comp_name, count, entity_str, sub_count in entries:
            item = self._make_comp_listitem(comp_name, count, entity_str, sub_count)
            self.lstComponents.Items.Add(item)
            self._comp_names.append(comp_name)

        self.txtSummary.Text = "{} components | {} elements in model".format(
            len(self.mapping), total_elems)

    def _make_comp_listitem(self, comp_name, count, entity_str, sub_count):
        from System.Windows.Controls import (
            StackPanel as WPFStackPanel, TextBlock as WPFTextBlock,
            Border as WPFBorder, Orientation, DockPanel
        )
        from System.Windows import (
            Thickness as WPFThickness, HorizontalAlignment,
            VerticalAlignment as WPFVAlign, FontWeights
        )

        sp = WPFStackPanel()
        sp.Margin = WPFThickness(2, 3, 2, 3)

        row1 = DockPanel()

        badge = WPFBorder()
        badge.Background = bc.ConvertFromString("#E2E8F0")
        badge.CornerRadius = System.Windows.CornerRadius(4)
        badge.Padding = WPFThickness(6, 1, 6, 1)
        badge.Margin = WPFThickness(4, 0, 0, 0)
        DockPanel.SetDock(badge, System.Windows.Controls.Dock.Right)
        badge_text = WPFTextBlock()
        badge_text.Text = str(count)
        badge_text.FontSize = 9.5
        badge_text.Foreground = bc.ConvertFromString("#0F172A")
        badge_text.HorizontalAlignment = HorizontalAlignment.Center
        badge.Child = badge_text
        row1.Children.Add(badge)

        name_tb = WPFTextBlock()
        name_tb.Text = comp_name
        name_tb.FontSize = 12
        name_tb.FontWeight = FontWeights.SemiBold
        name_tb.Foreground = bc.ConvertFromString("#0F172A")
        name_tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        row1.Children.Add(name_tb)

        sp.Children.Add(row1)

        info_parts = []
        if entity_str:
            info_parts.append(entity_str)
        if sub_count:
            info_parts.append("{} subtypes".format(sub_count))
        if info_parts:
            info_tb = WPFTextBlock()
            info_tb.Text = "  ".join(info_parts)
            info_tb.FontSize = 10
            info_tb.Foreground = bc.ConvertFromString("#64748B")
            info_tb.Margin = WPFThickness(0, 1, 0, 0)
            info_tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
            sp.Children.Add(info_tb)

        return sp

    def _on_filter_changed(self, sender, args):
        txt = self.txtFilter.Text.strip().lower()
        self.lstComponents.Items.Clear()
        self._comp_names = []

        for comp_name, count, entity_str, sub_count in self._all_entries:
            search_str = "{} {} {}".format(comp_name, entity_str, sub_count).lower()
            if txt and txt not in search_str:
                continue
            item = self._make_comp_listitem(comp_name, count, entity_str, sub_count)
            self.lstComponents.Items.Add(item)
            self._comp_names.append(comp_name)

    def _on_comp_selected(self, sender, args):
        idx = self.lstComponents.SelectedIndex
        if idx < 0 or idx >= len(self._comp_names):
            return

        comp_name = self._comp_names[idx]
        self.current_comp = comp_name
        m = self.mapping.get(comp_name, {})

        entities = ", ".join(m.get("ifc_entities", []))
        revit_cats = ", ".join(m.get("revit_categories", []))
        self.txtCompName.Text = comp_name
        self.txtCompInfo.Text = "IFC: {}  |  Revit: {}".format(entities, revit_cats)
        self.txtAgencies.Text = "Agencies: {}".format(", ".join(m.get("agencies", [])))

        self.cmbSubtype.Items.Clear()
        subtypes = m.get("subtypes", [])
        userdefined = sorted(s for s in subtypes if s.startswith("*"))
        standard = sorted(s for s in subtypes if not s.startswith("*"))
        for st in userdefined:
            self.cmbSubtype.Items.Add("[SG] " + st)
        for st in standard:
            self.cmbSubtype.Items.Add(st)
        if self.cmbSubtype.Items.Count > 0:
            self.cmbSubtype.SelectedIndex = 0

        elems = m.get("_elements", [])
        rows = build_type_rows(elems)
        self.current_rows = rows
        self._populate_datagrid(rows)

    def _get_subtype_info(self):
        sel = self.cmbSubtype.SelectedItem
        if not sel:
            return "", False
        sel_str = str(sel)
        if sel_str.startswith("[SG] "):
            sel_str = sel_str[5:]
        is_ud = sel_str.startswith("*")
        return sel_str, is_ud

    def _update_ai_status(self):
        try:
            txt = getattr(self, 'txt_ai_status', None) or self.FindName('txt_ai_status')
            if txt is not None:
                if self.is_ai_mode_active("IFCSG"):
                    info = self.get_ai_status_info()
                    txt.Text = "AI Mode: " + info.get("label", "Active")
                else:
                    txt.Text = "AI Mode: Offline"
        except Exception:
            pass

    def _on_ai_auto_match(self, sender=None, args=None):
        """Predict best matching IFC-SG Subtype for selected types using AI Mode."""
        if not self.is_ai_mode_active("IFCSG"):
            forms.alert(
                "AI Mode is currently offline or no API Key is configured.\n\n"
                "Please configure an API Key in LLMs Setting to enable AI Subtype Predictions.",
                title="AI Mode Offline"
            )
            return

        if not self.cmbSubtype or not self.cmbSubtype.Items or self.cmbSubtype.Items.Count == 0:
            forms.alert("No subtypes available for the selected component.", title="No Subtypes")
            return

        candidates = [str(item) for item in self.cmbSubtype.Items if str(item).strip()]
        if not candidates:
            return

        sel_items = []
        if self.dgTypes and self.dgTypes.SelectedItems and self.dgTypes.SelectedItems.Count > 0:
            sel_items = list(self.dgTypes.SelectedItems)
        elif self.current_rows:
            sel_items = self.current_rows[:1]

        if not sel_items:
            forms.alert("Please select at least one type in the grid to predict subtype.", title="Selection Required")
            return

        target_row = sel_items[0]
        type_name = getattr(target_row, "TypeName", "") or getattr(target_row, "Type", "") or str(target_row)
        comp_name = getattr(self, "current_comp", "") or "BIM Component"

        btn = getattr(self, 'btn_ai_auto_match', None)
        orig_content = "✨ AI Predict"

        def _restore_btn():
            if btn:
                btn.Content = orig_content
                btn.IsEnabled = True

        if btn:
            btn.Content = "⏳ Predicting..."
            btn.IsEnabled = False

        # Backup current selection for undo/safety
        self._prev_subtype_idx = self.cmbSubtype.SelectedIndex

        # Smart candidate pre-filter if candidate list is very long
        filtered_candidates = candidates
        if len(candidates) > 35:
            tokens = set(re.findall(r'\w+', (type_name + " " + comp_name).lower()))
            scored = []
            for c in candidates:
                c_tokens = set(re.findall(r'\w+', c.lower()))
                common = len(tokens.intersection(c_tokens))
                scored.append((common, c))
            scored.sort(key=lambda x: x[0], reverse=True)
            filtered_candidates = [c for _, c in scored[:30]]

        def _bg():
            b = self.ai_bridge
            if not b:
                return None
            return b.classify(
                input_text="Revit Type: {} | Component: {}".format(type_name, comp_name),
                candidate_categories=filtered_candidates,
                context="Singapore BCA CORENET X IFC-SG classification"
            )

        def _on_done(res):
            try:
                if res and isinstance(res, dict) and "selected_category" in res:
                    pred = res["selected_category"]
                    conf = res.get("confidence", 0.9)
                    ratio = res.get("rationale", "")

                    matched_idx = -1
                    for i in range(self.cmbSubtype.Items.Count):
                        if str(self.cmbSubtype.Items[i]).strip().lower() == pred.strip().lower():
                            matched_idx = i
                            break

                    if matched_idx >= 0:
                        self.cmbSubtype.SelectedIndex = matched_idx
                        forms.alert(
                            "Predicted Subtype: {}\nConfidence: {:.0%}\n\nRationale:\n{}".format(pred, float(conf), ratio),
                            title="✨ AI Subtype Prediction"
                        )
                    else:
                        forms.alert("AI predicted: {}, but not in current subtype list.".format(pred), title="Prediction Note")
                else:
                    forms.alert("Could not determine a matching subtype. Please select manually.", title="Prediction Inconclusive")
            finally:
                _restore_btn()

        def _on_err(err):
            _restore_btn()
            forms.alert("AI Error:\n" + str(err), title="AI Error")

        self.run_ai_async(_bg, _on_done, _on_err)

    def _on_apply_selected(self, sender, args):
        sel_indices = set()
        for item in self.dgTypes.SelectedItems:
            try:
                idx = self.dgTypes.Items.IndexOf(item)
                sel_indices.add(idx)
            except:
                pass
        if not sel_indices:
            WPFMessageBox.Show("Select types in the grid (Ctrl+Click for multi).",
                               "No Selection", MessageBoxButton.OK, MessageBoxImage.Warning)
            return
        selected_rows = [self.current_rows[i] for i in sel_indices
                         if i < len(self.current_rows)]
        if not selected_rows:
            return
        self._apply_to_rows(selected_rows)

    def _on_apply_all(self, sender, args):
        if not self.current_rows:
            return
        subtype_str, _ = self._get_subtype_info()
        result = WPFMessageBox.Show(
            "Apply '{}' to ALL {} types in '{}'?".format(
                subtype_str, len(self.current_rows), self.current_comp),
            "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result == MessageBoxResult.Yes:
            self._apply_to_rows(self.current_rows)

    def _apply_to_rows(self, rows):
        subtype_str, is_ud = self._get_subtype_info()
        if not subtype_str:
            WPFMessageBox.Show("Select a subtype first.", "No Subtype",
                               MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        use_type = self.chkApplyType.IsChecked == True
        also_entity = self.chkApplyEntity.IsChecked == True
        set_obj = self.chkSetObjectType.IsChecked == True

        m = self.mapping.get(self.current_comp, {})
        primary_entity = m["ifc_entities"][0] if m.get("ifc_entities") else ""

        if is_ud:
            pdt_value = "USERDEFINED"
            obj_value = subtype_str.lstrip("*")
        else:
            pdt_value = subtype_str
            obj_value = ""

        ok = 0
        fail = 0
        debug_lines = []

        t = Transaction(self.doc, "DQT - Set IFC-SG Subtypes")
        t.Start()
        try:
            for row in rows:
                targets_tried = []
                if use_type and row._type_elem:
                    te = row._type_elem
                    targets_tried.append(("Type", te))

                if row._items:
                    inst = row._items[0]["elem"]
                    targets_tried.append(("Instance", inst))

                type_ok = False
                for target_label, target in targets_tried:
                    entity_ok = True
                    pdt_ok = True

                    if also_entity and primary_entity:
                        entity_ok = set_ifc_export_as(target, primary_entity, use_type)

                    pdt_ok = set_ifc_predefined_type(target, pdt_value, use_type)

                    if is_ud and set_obj and obj_value:
                        set_ifc_object_type(target, obj_value, use_type)

                    if entity_ok and pdt_ok:
                        type_ok = True
                        debug_lines.append("[OK] {} '{}' -> {} on {} (id:{})".format(
                            target_label, row.Family + ":" + row.TypeName,
                            pdt_value, target_label, _eid_int(target.Id)))
                        break
                    else:
                        debug_lines.append("[FAIL] {} '{}' entity={} pdt={} on {} (id:{})".format(
                            target_label, row.Family + ":" + row.TypeName,
                            entity_ok, pdt_ok, target_label, _eid_int(target.Id)))

                if type_ok:
                    ok += 1
                else:
                    fail += 1

            t.Commit()
        except Exception as ex:
            t.RollBack()
            WPFMessageBox.Show("Error: " + str(ex), "Failed",
                               MessageBoxButton.OK, MessageBoxImage.Error)
            return

        self._refresh()

        msg = "Applied '{}' -> PredefinedType='{}'\n".format(subtype_str, pdt_value)
        if is_ud and obj_value:
            msg += "ObjectType = '{}'\n".format(obj_value)
        msg += "\nSuccess: {}  |  Failed: {}\n".format(ok, fail)

        if debug_lines and output:
            output.print_md("### IFC-SG Subtype Apply Log")
            for line in debug_lines:
                output.print_md("- " + line)

        if fail > 0:
            msg += "\nCheck pyRevit output for detailed log."

        WPFMessageBox.Show(msg, "Done", MessageBoxButton.OK, MessageBoxImage.Information)

    def _on_auto_assign(self, sender, args):
        """Auto-assign IFC entity + first subtype to all elements missing subtypes."""
        if not self.mapping:
            WPFMessageBox.Show("Load a mapping Excel first.",
                               "No Mapping", MessageBoxButton.OK, MessageBoxImage.Warning)
            return

        preview_lines = []
        auto_plan = []

        for comp_name in sorted(self.mapping.keys()):
            m = self.mapping[comp_name]
            elems = m.get("_elements", [])
            if not elems:
                continue

            entity = m["ifc_entities"][0] if m.get("ifc_entities") else ""
            if not entity:
                continue

            subtypes = m.get("subtypes", [])
            if not subtypes:
                subtype_str = ""
                is_ud = False
            else:
                ud = sorted(s for s in subtypes if s.startswith("*"))
                std = sorted(s for s in subtypes if not s.startswith("*"))
                subtype_str = (ud + std)[0] if (ud + std) else ""
                is_ud = subtype_str.startswith("*")

            rows = build_type_rows(elems)
            unset = [r for r in rows if r.Status != "OK"]
            if not unset:
                continue

            total_instances = sum(r.Count for r in unset)
            pdt = "USERDEFINED" if is_ud else subtype_str
            obj = subtype_str.lstrip("*") if is_ud else ""

            line = "{}: {} types ({} instances) -> {} / {}".format(
                comp_name, len(unset), total_instances, entity, pdt)
            if obj:
                line += " [ObjectType={}]".format(obj)
            preview_lines.append(line)
            auto_plan.append((comp_name, unset, entity, subtype_str, is_ud))

        if not auto_plan:
            WPFMessageBox.Show(
                "All elements already have IFC Entity + Subtype assigned!",
                "Nothing to Auto-Assign", MessageBoxButton.OK,
                MessageBoxImage.Information)
            return

        preview_text = "Auto-Assign will update {} components:\n\n".format(len(auto_plan))
        preview_text += "\n".join(preview_lines)
        preview_text += "\n\nOnly elements with Status != 'OK' will be updated."
        preview_text += "\nExisting assignments will NOT be overwritten."
        preview_text += "\n\nProceed?"

        result = WPFMessageBox.Show(preview_text, "Auto-Assign Preview",
                                    MessageBoxButton.YesNo, MessageBoxImage.Question)
        if result != MessageBoxResult.Yes:
            return

        use_type = self.chkApplyType.IsChecked == True
        set_obj = self.chkSetObjectType.IsChecked == True
        total_ok = 0
        total_fail = 0

        t = Transaction(self.doc, "DQT - Auto-Assign IFC-SG Subtypes")
        t.Start()
        try:
            for comp_name, rows, entity, subtype_str, is_ud in auto_plan:
                if is_ud:
                    pdt_value = "USERDEFINED"
                    obj_value = subtype_str.lstrip("*")
                else:
                    pdt_value = subtype_str
                    obj_value = ""

                for row in rows:
                    if use_type and row._type_elem:
                        targets = [row._type_elem]
                    else:
                        targets = [it["elem"] for it in row._items]

                    for target in targets:
                        s = True
                        if entity:
                            if not set_ifc_export_as(target, entity, use_type):
                                s = False
                        if pdt_value:
                            if not set_ifc_predefined_type(target, pdt_value, use_type):
                                s = False
                        if is_ud and set_obj and obj_value:
                            set_ifc_object_type(target, obj_value, use_type)
                        if s:
                            total_ok += 1
                        else:
                            total_fail += 1

            t.Commit()
        except Exception as ex:
            t.RollBack()
            WPFMessageBox.Show("Error: " + str(ex), "Failed",
                               MessageBoxButton.OK, MessageBoxImage.Error)
            return

        self._populate_component_list()

        msg = "Auto-Assign complete!\n{} types updated successfully.".format(total_ok)
        if total_fail:
            msg += "\n{} failed (read-only or missing parameter).".format(total_fail)
        WPFMessageBox.Show(msg, "Auto-Assign Done",
                           MessageBoxButton.OK, MessageBoxImage.Information)

    def _refresh(self):
        if not self.current_comp:
            return
        m = self.mapping.get(self.current_comp, {})
        m["_elements"] = collect_elements_for_bics(m.get("_bics", []))
        rows = build_type_rows(m["_elements"])
        self.current_rows = rows
        self._populate_datagrid(rows)

    # ==============================================================================
    # Tab 2: Compliance Checker Logic
    # ==============================================================================

    def _load_saved_configs(self):
        self.cmbConfig.Items.Clear()
        if os.path.exists(self.configs_dir):
            for f in sorted(os.listdir(self.configs_dir)):
                if f.endswith('.json'):
                    self.cmbConfig.Items.Add(os.path.splitext(f)[0])
        if self.cmbConfig.Items.Count > 0:
            self.cmbConfig.SelectedIndex = 0

    def _on_config_changed(self, sender, args):
        sel = self.cmbConfig.SelectedItem
        if sel:
            path = os.path.join(self.configs_dir, str(sel) + ".json")
            try:
                self.config = ParamCheckConfig.from_json(path)
                self._refresh_tree()
                self._update_config_stats()
                self.btnRunCheck.IsEnabled = True
                self.txtStatus.Text = "Config loaded: {} ({})".format(
                    self.config.name, self.config.source)
            except Exception as e:
                self.txtStatus.Text = "Error loading config: {}".format(str(e))

    def _on_import_xml(self, sender, args):
        dlg = OpenFileDialog()
        dlg.Filter = "XML Files (*.xml)|*.xml|All Files (*.*)|*.*"
        dlg.Title = "Import Autodesk Model Checker XML"
        
        if dlg.ShowDialog() == WFDialogResult.OK:
            try:
                self.config = ParamCheckConfig.from_xml(dlg.FileName)
                name = os.path.splitext(os.path.basename(dlg.FileName))[0]
                save_path = os.path.join(self.configs_dir, name + ".json")
                self.config.to_json(save_path)
                
                self._load_saved_configs()
                for i in range(self.cmbConfig.Items.Count):
                    if str(self.cmbConfig.Items[i]) == name:
                        self.cmbConfig.SelectedIndex = i
                        break
                
                d, c, p = self.config.get_total_stats()
                self.txtStatus.Text = "Imported XML: {} disciplines, {} categories, {} params".format(d, c, p)
            except Exception as e:
                WPFMessageBox.Show("Error importing XML:\n{}".format(str(e)),
                                   "Import Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_import_excel(self, sender, args):
        dlg = OpenFileDialog()
        dlg.Filter = "Excel Files (*.xlsx;*.xls)|*.xlsx;*.xls|All Files (*.*)|*.*"
        dlg.Title = "Import Excel Parameter Mapping"
        
        if dlg.ShowDialog() == WFDialogResult.OK:
            try:
                self.config = ParamCheckConfig.from_excel(dlg.FileName)
                name = os.path.splitext(os.path.basename(dlg.FileName))[0]
                save_path = os.path.join(self.configs_dir, name + ".json")
                self.config.to_json(save_path)
                
                self._load_saved_configs()
                for i in range(self.cmbConfig.Items.Count):
                    if str(self.cmbConfig.Items[i]) == name:
                        self.cmbConfig.SelectedIndex = i
                        break
                
                d, c, p = self.config.get_total_stats()
                self.txtStatus.Text = "Imported Excel: {} disciplines, {} categories, {} params".format(d, c, p)
            except Exception as e:
                WPFMessageBox.Show("Error importing Excel:\n{}".format(str(e)),
                                   "Import Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_save_config(self, sender, args):
        if not self.config:
            return
        dlg = SaveFileDialog()
        dlg.Filter = "JSON Files (*.json)|*.json"
        dlg.Title = "Save Config"
        dlg.InitialDirectory = self.configs_dir
        if dlg.ShowDialog() == WFDialogResult.OK:
            self.config.to_json(dlg.FileName)
            self.txtStatus.Text = "Config saved: {}".format(dlg.FileName)

    def _on_delete_config(self, sender, args):
        sel = self.cmbConfig.SelectedItem
        if not sel:
            return
        result = WPFMessageBox.Show(
            "Delete config '{}'?".format(sel),
            "Confirm", MessageBoxButton.YesNo, MessageBoxImage.Warning)
        if result == MessageBoxResult.Yes:
            path = os.path.join(self.configs_dir, str(sel) + ".json")
            if os.path.exists(path):
                os.remove(path)
            self._load_saved_configs()

    def _refresh_tree(self):
        self.tvCategories.Items.Clear()
        if not self.config:
            return
        
        for disc_name, disc_data in self.config.disciplines.items():
            disc_item = TreeViewItem()
            disc_item.IsExpanded = True
            
            disc_sp = StackPanel()
            disc_sp.Orientation = Orientation.Horizontal
            
            chk_disc = CheckBox()
            chk_disc.IsChecked = System.Nullable[System.Boolean](bool(disc_data.get("enabled", True)))
            chk_disc.Margin = Thickness(0, 0, 6, 0)
            chk_disc.Tag = disc_name
            chk_disc.Checked += self._on_disc_toggled
            chk_disc.Unchecked += self._on_disc_toggled
            
            lbl_disc = TextBlock()
            lbl_disc.Text = u"{} ({} categories)".format(disc_name, len(disc_data.get("categories", {})))
            lbl_disc.FontWeight = System.Windows.FontWeights.Bold
            lbl_disc.FontSize = 12
            lbl_disc.Foreground = bc.ConvertFromString("#5D4E37")
            
            disc_sp.Children.Add(chk_disc)
            disc_sp.Children.Add(lbl_disc)
            disc_item.Header = disc_sp
            
            for cat_name, cat_data in disc_data.get("categories", {}).items():
                cat_item = TreeViewItem()
                
                cat_sp = StackPanel()
                cat_sp.Orientation = Orientation.Horizontal
                
                chk_cat = CheckBox()
                chk_cat.IsChecked = System.Nullable[System.Boolean](bool(cat_data.get("enabled", True)))
                chk_cat.Margin = Thickness(0, 0, 6, 0)
                chk_cat.Tag = "{}|{}".format(disc_name, cat_name)
                chk_cat.Checked += self._on_cat_toggled
                chk_cat.Unchecked += self._on_cat_toggled
                
                param_count = len(cat_data.get("params", []))
                lbl_cat = TextBlock()
                lbl_cat.Text = u"{} ({} params)".format(cat_name, param_count)
                lbl_cat.FontSize = 11
                
                cat_sp.Children.Add(chk_cat)
                cat_sp.Children.Add(lbl_cat)
                cat_item.Header = cat_sp
                
                disc_item.Items.Add(cat_item)
            
            self.tvCategories.Items.Add(disc_item)

    def _on_disc_toggled(self, sender, args):
        disc_name = str(sender.Tag)
        if disc_name in self.config.disciplines:
            self.config.disciplines[disc_name]["enabled"] = bool(sender.IsChecked)

    def _on_cat_toggled(self, sender, args):
        tag = str(sender.Tag)
        parts = tag.split("|")
        if len(parts) == 2:
            disc, cat = parts
            if disc in self.config.disciplines:
                cats = self.config.disciplines[disc].get("categories", {})
                if cat in cats:
                    cats[cat]["enabled"] = bool(sender.IsChecked)

    def _on_expand_all(self, sender, args):
        for item in self.tvCategories.Items:
            item.IsExpanded = True

    def _on_collapse_all(self, sender, args):
        for item in self.tvCategories.Items:
            item.IsExpanded = False

    def _update_config_stats(self):
        if self.config:
            d, c, p = self.config.get_total_stats()
            self.txtTotalParams.Text = str(p)
            self.txtCategories.Text = str(c)

    def _on_run_check(self, sender, args):
        if not self.config:
            return

        self.txtStatus.Text = "Running IFC-SG parameter checks..."
        self.Cursor = System.Windows.Input.Cursors.Wait
        self.UpdateLayout()

        # Show progress bar + Pause/Stop; disable Run/Export while running
        self.begin_progress(100, disable=[self.btnRunCheck, self.btnExportExcel])

        def progress_cb(current, total):
            pct = int(float(current) / float(total) * 100) if total > 0 else 0
            self.step_progress(pct, "Checking parameters... {}/{}".format(current, total))

        try:
            self.results = self.checker.run_check(
                self.config,
                progress_callback=progress_cb,
                cancel_check=lambda: self.is_cancelled)
            self.all_results = list(self.results)

            passed = len([r for r in self.results if r.status == "pass"])
            failed = len([r for r in self.results if r.status == "fail"])
            warning = len([r for r in self.results if r.status == "warning"])
            no_elem = len([r for r in self.results if r.status == "no_elements"])

            self.txtPassed.Text = str(passed)
            self.txtFailed.Text = str(failed)
            self.txtWarning.Text = str(warning)
            self.txtNoElem.Text = str(no_elem)

            self._current_filter = "all"
            self._render_results(self.results)

            self.btnExportExcel.IsEnabled = True
            self.btnSelectAllFailed.IsEnabled = True
            self.txtResultHeader.Text = "Check Results ({} checks)".format(len(self.results))

            # Read cancel flag BEFORE end_progress() resets it
            cancelled = self.is_cancelled
            if cancelled:
                self.txtStatus.Text = "Cancelled — partial results: {} checks".format(len(self.results))
            else:
                self.txtStatus.Text = "Done: {} passed, {} failed, {} partial, {} no elements".format(
                    passed, failed, warning, no_elem)

        except Exception as e:
            self.txtStatus.Text = "Error: {}".format(str(e))
            WPFMessageBox.Show("Error:\n{}".format(traceback.format_exc()),
                               "Error", MessageBoxButton.OK, MessageBoxImage.Error)
        finally:
            self.end_progress()
            self.Cursor = System.Windows.Input.Cursors.Arrow

    def _apply_filter(self, filter_type):
        self._current_filter = filter_type
        if not self.all_results:
            return
        
        search_text = self.txtSearch.Text.strip().lower() if self.txtSearch.Text else ""
        
        filtered = []
        for r in self.all_results:
            if filter_type == "fail" and r.status not in ("fail",):
                continue
            if filter_type == "warning" and r.status not in ("warning",):
                continue
            if filter_type == "pass" and r.status not in ("pass",):
                continue
            
            if search_text:
                searchable = "{}{}{}".format(r.discipline, r.category, r.param_name).lower()
                if search_text not in searchable:
                    continue
            
            filtered.append(r)
        
        self._render_results(filtered)

    def _on_select_all_failed(self, sender, args):
        if not self.all_results:
            return
        all_ids = []
        for r in self.all_results:
            if r.status in ("fail", "warning"):
                all_ids.extend(r.element_ids)
        unique_ids = list(set(all_ids))
        if unique_ids:
            self._select_elements_in_revit(unique_ids[:2000])
            self.txtStatus.Text = "Selected {} failed elements in Revit".format(len(unique_ids))
        else:
            self.txtStatus.Text = "No failed elements to select"

    def _select_elements_in_revit(self, element_ids):
        try:
            ids = System.Collections.Generic.List[ElementId]()
            for eid in element_ids:
                try:
                    eid_obj = make_eid(int(eid))
                    if eid_obj and eid_value(eid_obj) != -1:
                        ids.Add(eid_obj)
                except:
                    pass
            if ids.Count > 0:
                self.uidoc.Selection.SetElementIds(ids)
                self.txtStatus.Text = "Selected {} elements in Revit".format(ids.Count)
        except Exception as e:
            self.txtStatus.Text = "Select error: {}".format(str(e))

    def _compute_category_stats(self, results):
        stats = {}
        for r in results:
            key = "{}|{}".format(r.discipline, r.category)
            if key not in stats:
                stats[key] = {"total": 0, "pass": 0, "fail": 0, "warning": 0, "no_elements": 0}
            stats[key]["total"] += 1
            stats[key][r.status] = stats[key].get(r.status, 0) + 1
        
        for key, s in stats.items():
            checkable = s["total"] - s.get("no_elements", 0)
            if checkable > 0:
                s["pct"] = int(round(s["pass"] / float(checkable) * 100))
            else:
                s["pct"] = -1
        return stats

    def _render_results(self, results):
        self.spResults.Children.Clear()
        
        status_bg = {
            "pass": "#E8F5E9", "fail": "#FFEBEE",
            "warning": "#FFF8E1", "no_elements": "#ECEFF1"
        }
        status_fg = {
            "pass": "#2E7D32", "fail": "#C62828",
            "warning": "#F57F17", "no_elements": "#78909C"
        }
        status_icon = {
            "pass": u"\u2714", "fail": u"\u2718",
            "warning": u"\u26A0", "no_elements": u"\u23F8"
        }
        
        cat_stats = self._compute_category_stats(self.all_results)
        
        current_disc = ""
        current_cat = ""
        
        for r in results:
            # Discipline header
            if r.discipline != current_disc:
                current_disc = r.discipline
                current_cat = ""
                
                disc_border = Border()
                disc_border.Margin = Thickness(0, 8, 0, 2)
                disc_border.Padding = Thickness(8, 4, 8, 4)
                disc_border.Background = bc.ConvertFromString("#0F172A")
                disc_border.CornerRadius = System.Windows.CornerRadius(4)
                
                disc_txt = TextBlock()
                disc_txt.Text = r.discipline
                disc_txt.FontWeight = System.Windows.FontWeights.Bold
                disc_txt.FontSize = 13
                disc_txt.Foreground = bc.ConvertFromString("#E5B85C")
                disc_border.Child = disc_txt
                self.spResults.Children.Add(disc_border)
            
            # Category header with progress bar
            if r.category != current_cat:
                current_cat = r.category
                stat_key = "{}|{}".format(r.discipline, r.category)
                stat = cat_stats.get(stat_key, {})
                pct = stat.get("pct", 0)
                cat_pass = stat.get("pass", 0)
                cat_total = stat.get("total", 0)
                cat_no_elem = stat.get("no_elements", 0)
                cat_fail = stat.get("fail", 0)
                cat_warn = stat.get("warning", 0)
                
                cat_border = Border()
                cat_border.Margin = Thickness(0, 4, 0, 2)
                cat_border.Padding = Thickness(4, 3, 4, 3)
                cat_border.CornerRadius = System.Windows.CornerRadius(4)
                cat_border.Background = bc.ConvertFromString("#F9F6EE")
                cat_border.BorderBrush = bc.ConvertFromString("#E8E0D0")
                cat_border.BorderThickness = Thickness(1)
                
                cat_grid = Grid()
                cg1 = ColumnDefinition()
                cg1.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
                cg2 = ColumnDefinition()
                cg2.Width = System.Windows.GridLength(200)
                cg3 = ColumnDefinition()
                cg3.Width = System.Windows.GridLength(80)
                cat_grid.ColumnDefinitions.Add(cg1)
                cat_grid.ColumnDefinitions.Add(cg2)
                cat_grid.ColumnDefinitions.Add(cg3)
                
                cat_info = StackPanel()
                cat_name_txt = TextBlock()
                cat_name_txt.Text = u"\u25B8 {}".format(r.category)
                cat_name_txt.FontWeight = System.Windows.FontWeights.SemiBold
                cat_name_txt.FontSize = 11
                cat_name_txt.Foreground = bc.ConvertFromString("#5D4E37")
                cat_info.Children.Add(cat_name_txt)
                
                sub_parts = []
                if cat_pass > 0:
                    sub_parts.append("{} pass".format(cat_pass))
                if cat_fail > 0:
                    sub_parts.append("{} fail".format(cat_fail))
                if cat_warn > 0:
                    sub_parts.append("{} partial".format(cat_warn))
                if cat_no_elem > 0:
                    sub_parts.append("{} N/A".format(cat_no_elem))
                
                sub_txt = TextBlock()
                sub_txt.Text = " | ".join(sub_parts)
                sub_txt.FontSize = 9
                sub_txt.Foreground = bc.ConvertFromString("#999999")
                cat_info.Children.Add(sub_txt)
                Grid.SetColumn(cat_info, 0)
                cat_grid.Children.Add(cat_info)
                
                # Progress bar
                if pct >= 0:
                    prog_sp = StackPanel()
                    prog_sp.VerticalAlignment = System.Windows.VerticalAlignment.Center
                    prog_sp.Margin = Thickness(4, 0, 4, 0)
                    
                    bar_border = Border()
                    bar_border.Height = 10
                    bar_border.CornerRadius = System.Windows.CornerRadius(2)
                    bar_border.Background = bc.ConvertFromString("#E0E0E0")
                    
                    bar_grid = Grid()
                    bar_bg = Border()
                    bar_bg.Height = 10
                    bar_bg.CornerRadius = System.Windows.CornerRadius(2)
                    bar_bg.Background = bc.ConvertFromString("#E0E0E0")
                    bar_grid.Children.Add(bar_bg)
                    
                    bar_fill = Border()
                    bar_fill.Height = 10
                    bar_fill.CornerRadius = System.Windows.CornerRadius(2)
                    bar_fill.HorizontalAlignment = System.Windows.HorizontalAlignment.Left
                    bar_fill.Width = max(1, pct * 1.8)
                    
                    if pct >= 80:
                        fill_color = "#66BB6A"
                    elif pct >= 50:
                        fill_color = "#FFA726"
                    else:
                        fill_color = "#EF5350"
                    bar_fill.Background = bc.ConvertFromString(fill_color)
                    bar_grid.Children.Add(bar_fill)
                    
                    prog_sp.Children.Add(bar_grid)
                    Grid.SetColumn(prog_sp, 1)
                    cat_grid.Children.Add(prog_sp)
                
                pct_sp = StackPanel()
                pct_sp.VerticalAlignment = System.Windows.VerticalAlignment.Center
                pct_sp.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
                
                if pct >= 0:
                    pct_txt = TextBlock()
                    pct_txt.Text = "{}%".format(pct)
                    pct_txt.FontSize = 12
                    pct_txt.FontWeight = System.Windows.FontWeights.Bold
                    pct_txt.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
                    if pct >= 80:
                        pct_txt.Foreground = bc.ConvertFromString("#2E7D32")
                    elif pct >= 50:
                        pct_txt.Foreground = bc.ConvertFromString("#F57F17")
                    else:
                        pct_txt.Foreground = bc.ConvertFromString("#C62828")
                    pct_sp.Children.Add(pct_txt)
                else:
                    na_txt = TextBlock()
                    na_txt.Text = "N/A"
                    na_txt.FontSize = 11
                    na_txt.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
                    na_txt.Foreground = bc.ConvertFromString("#999999")
                    pct_sp.Children.Add(na_txt)
                
                # "Select" button for failed categories
                if cat_fail > 0 or cat_warn > 0:
                    all_fail_ids = []
                    for ar in self.all_results:
                        if ar.discipline == r.discipline and ar.category == r.category:
                            if ar.status in ("fail", "warning"):
                                all_fail_ids.extend(ar.element_ids)
                    
                    if all_fail_ids:
                        sel_all_btn = Button()
                        sel_all_btn.Content = "Select"
                        sel_all_btn.FontSize = 9
                        sel_all_btn.Padding = Thickness(4, 1, 4, 1)
                        sel_all_btn.Margin = Thickness(0, 2, 0, 0)
                        sel_all_btn.Cursor = System.Windows.Input.Cursors.Hand
                        sel_all_btn.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
                        sel_all_btn.Background = bc.ConvertFromString("#FFCDD2")
                        sel_all_btn.Foreground = bc.ConvertFromString("#C62828")
                        sel_all_btn.BorderBrush = bc.ConvertFromString("#EF9A9A")
                        sel_all_btn.BorderThickness = Thickness(1)
                        
                        unique_ids = list(set(all_fail_ids))[:500]
                        sel_all_btn.Tag = unique_ids
                        sel_all_btn.Click += self._on_select_btn_click
                        pct_sp.Children.Add(sel_all_btn)
                
                Grid.SetColumn(pct_sp, 2)
                cat_grid.Children.Add(pct_sp)
                
                cat_border.Child = cat_grid
                self.spResults.Children.Add(cat_border)
            
            # Parameter Row
            row_border = Border()
            row_border.Margin = Thickness(16, 1, 0, 1)
            row_border.Padding = Thickness(8, 3, 8, 3)
            row_border.CornerRadius = System.Windows.CornerRadius(4)
            row_border.Background = bc.ConvertFromString(status_bg.get(r.status, "#FAFAFA"))
            
            row_grid = Grid()
            c1 = ColumnDefinition()
            c1.Width = System.Windows.GridLength(28)
            c2 = ColumnDefinition()
            c2.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
            c3 = ColumnDefinition()
            c3.Width = System.Windows.GridLength(120)
            c4 = ColumnDefinition()
            c4.Width = System.Windows.GridLength(55)
            row_grid.ColumnDefinitions.Add(c1)
            row_grid.ColumnDefinitions.Add(c2)
            row_grid.ColumnDefinitions.Add(c3)
            row_grid.ColumnDefinitions.Add(c4)
            
            icon = TextBlock()
            icon.Text = status_icon.get(r.status, "?")
            icon.FontSize = 12
            icon.VerticalAlignment = System.Windows.VerticalAlignment.Center
            icon.Foreground = bc.ConvertFromString(status_fg.get(r.status, "#666666"))
            Grid.SetColumn(icon, 0)
            row_grid.Children.Add(icon)
            
            name_txt = TextBlock()
            name_txt.Text = r.param_name
            name_txt.FontSize = 11
            name_txt.VerticalAlignment = System.Windows.VerticalAlignment.Center
            Grid.SetColumn(name_txt, 1)
            row_grid.Children.Add(name_txt)
            
            if r.status == "no_elements":
                count_text = "No elements"
            elif r.status == "pass":
                count_text = "{} OK".format(r.total_elements)
            else:
                count_text = "{}/{} missing".format(r.missing_count, r.total_elements)
            
            count_txt = TextBlock()
            count_txt.Text = count_text
            count_txt.FontSize = 10
            count_txt.VerticalAlignment = System.Windows.VerticalAlignment.Center
            count_txt.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
            count_txt.Foreground = bc.ConvertFromString(status_fg.get(r.status, "#888888"))
            Grid.SetColumn(count_txt, 2)
            row_grid.Children.Add(count_txt)
            
            if r.status in ("fail", "warning") and r.element_ids:
                sel_btn = Button()
                sel_btn.Content = u"\u25BA Select"
                sel_btn.FontSize = 9
                sel_btn.Padding = Thickness(3, 1, 3, 1)
                sel_btn.VerticalAlignment = System.Windows.VerticalAlignment.Center
                sel_btn.Cursor = System.Windows.Input.Cursors.Hand
                sel_btn.Background = bc.ConvertFromString("#FFF3E0")
                sel_btn.Foreground = bc.ConvertFromString("#E65100")
                sel_btn.BorderBrush = bc.ConvertFromString("#FFCC80")
                sel_btn.BorderThickness = Thickness(1)
                sel_btn.Tag = list(r.element_ids)[:200]
                sel_btn.Click += self._on_select_btn_click
                Grid.SetColumn(sel_btn, 3)
                row_grid.Children.Add(sel_btn)
            
            row_border.Child = row_grid
            self.spResults.Children.Add(row_border)

    def _on_select_btn_click(self, sender, args):
        ids = sender.Tag
        if ids:
            self._select_elements_in_revit(ids)

    def _on_export_excel(self, sender, args):
        if not self.results or not self.config:
            return
        
        dlg = SaveFileDialog()
        dlg.Filter = "Excel Files (*.xlsx)|*.xlsx"
        dlg.Title = "Export Compliance Report"
        dlg.InitialDirectory = self.reports_dir
        
        import System.IO
        dlg.FileName = "IFC-SG_Check_{}_{}".format(
            doc.ProjectInformation.Name or "Project",
            datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            
        if dlg.ShowDialog() == WFDialogResult.OK:
            self.txtStatus.Text = "Exporting Report..."
            self.Cursor = System.Windows.Input.Cursors.Wait
            self.UpdateLayout()
            try:
                self.reporter.generate(self.config, self.all_results, dlg.FileName)
                self.txtStatus.Text = "Exported: {}".format(System.IO.Path.GetFileName(dlg.FileName))
                result = WPFMessageBox.Show(
                    "Report exported successfully!\nOpen file now?", "Success",
                    MessageBoxButton.YesNo, MessageBoxImage.Information)
                if result == MessageBoxResult.Yes:
                    os.startfile(dlg.FileName)
            except Exception as e:
                WPFMessageBox.Show("Export error:\n{}".format(str(e)),
                                   "Error", MessageBoxButton.OK, MessageBoxImage.Error)
            finally:
                self.Cursor = System.Windows.Input.Cursors.Arrow


# ==============================================================================
# Entry Point function
# ==============================================================================

def show_ifcsg_suite(script_dir, revit):
    global doc, uidoc, output
    uidoc = revit.ActiveUIDocument
    doc = uidoc.Document
    # CPython has no ScriptOutput.GetDefault; safe_output()
    # returns a no-op window instead of killing the tool.
    try:
        from _cpython_bootstrap import safe_output
        output = safe_output()
    except Exception:
        output = script.get_output()

    try:
        win = IFCSGSuiteWindow(script_dir, revit)
        win.ShowDialog()
    except Exception as ex:
        if output:
            output.print_md("## Error Launching IFC-SG Suite")
            output.print_md("```\n{}\n```".format(traceback.format_exc()))
        else:
            WPFMessageBox.Show("Error launching IFC-SG Suite:\n" + str(ex), "Error",
                               MessageBoxButton.OK, MessageBoxImage.Error)
