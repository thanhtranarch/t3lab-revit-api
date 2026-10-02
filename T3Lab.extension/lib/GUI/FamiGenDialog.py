# -*- coding: utf-8 -*-
"""
FamilyCreatorDialog.py
Combined WPF dialog for Family Creator — CAD, JSON, and Batch modes.
"""

import os
import re
import sys
import math
import codecs
import traceback
import json

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

from System import TimeSpan
from System.Windows import WindowState, Visibility as WinVis, Clipboard
from System.Windows.Controls import DataGridComboBoxColumn, DataGridLength
from System.Windows.Data import Binding, BindingMode, UpdateSourceTrigger
# Int32Collection lives in System.Windows.Media, not Media3D — importing it
# from Media3D raised ImportError and FamiGen never opened.
from System.Windows.Media import Color as MediaColor, Colors, Int32Collection, SolidColorBrush
from System.Windows.Media.Media3D import (
    AmbientLight, DiffuseMaterial, DirectionalLight, GeometryModel3D,
    MeshGeometry3D, Model3DGroup, ModelVisual3D, PerspectiveCamera, Point3D,
    Point3DCollection, Vector3D,
)
from System.Windows.Threading import DispatcherTimer

from pyrevit import forms
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Intelligence.family_schema import (
    CATEGORY_TABLE, SUPPORTED_CATEGORIES, build_system_prompt, color_hex,
    generate_family_schema, material_parameter_name, parse_color, schema_summary,
    validate_ai_schema, validate_family_schema,
)
import pyrevit.script as _pyrevit_script

logger = _pyrevit_script.get_logger()
_GUI_DIR = os.path.dirname(__file__)
_XAML = os.path.join(_GUI_DIR, 'Tools', 'FamiGen.xaml')

_LIB_DIR = os.path.dirname(_GUI_DIR)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

_EXTENSION_DIR = os.path.dirname(_LIB_DIR)
from core.extension_paths import tab_dir  # noqa: E402  (after the sys.path insert)
# Per-category prompts (prompts/<slug>.md) are located by FamilyGen.guidance,
# shared with the MCP tool famigen_get_schema.

from Autodesk.Revit.DB import (
    ImportInstance, FilteredElementCollector,
    Options, GeometryInstance,
    Line, Arc, Ellipse, XYZ, Plane,
    CurveArray, CurveArrArray,
    SketchPlane, SaveAsOptions,
    Transaction, ElementId,
    View, ViewType, ReferencePlane, ReferenceArray,
    PlanarFace, Solid, IFailuresPreprocessor, FailureProcessingResult, FailureSeverity,
    Transform, ProfilePlaneLocation, HermiteSpline,
)
from System.Collections.Generic import List as _NetList

from Autodesk.Revit.UI import ExternalEvent, IExternalEventHandler, TaskDialog

from Utils.DWGFamilyHelpers import get_xy_bounds, _project_curve_to_z as _dwg_project_curve
from GUI.ProgressPauseMixin import ProgressPauseMixin
# Sizes on screen follow the active document's length unit (a family
# document's own units when FamiGen runs inside one). The JSON contract and
# the presets stay in mm. Read per window, never at import.
from Snippets._units import project_length_unit, MILLIMETERS
from FamilyGen import builder as family_builder
from FamilyGen import guidance as family_guidance
from FamilyGen import preview_mesh
from FamilyGen import proposals as family_proposals

# ==============================================================================
# CONSTANTS
# ==============================================================================
SCL = 1.0 / 304.8
_PREVIEW_DEBOUNCE_MS = 600
MAX_DETAIL_CURVES = 150
MIN_CURVE_RATIO = 0.30

_DISCIPLINES = [
    "Architecture", "Structure", "Mechanical", "Electrical",
    "Plumbing", "Fire Protection", "General",
]

# One category table for CAD, presets and JSON: Intelligence.family_schema
# (the JSON contract validates against the same list the builder can template).
_CATEGORY_TEMPLATES = [(name, list(templates)) for name, templates, _bic, _hosted in CATEGORY_TABLE]

DOOR_PRESETS = [
    ("Single_Swing_700x2100",   700, 2100, 65, 25, 25, 40, 1),
    ("Single_Swing_810x2200",   810, 2200, 65, 25, 25, 40, 1),
    ("Single_Swing_900x2200",   900, 2200, 65, 25, 25, 40, 1),
    ("Single_Swing_1000x2200", 1000, 2200, 65, 25, 25, 40, 1),
    ("Single_Swing_810x2400",   810, 2400, 65, 25, 25, 40, 1),
    ("Single_Swing_900x2400",   900, 2400, 65, 25, 25, 40, 1),
    ("Double_Swing_1600x2200", 1600, 2200, 65, 25, 25, 40, 2),
    ("Double_Swing_1800x2200", 1800, 2200, 65, 25, 25, 40, 2),
    ("Double_Swing_2000x2200", 2000, 2200, 65, 25, 25, 40, 2),
    ("Double_Swing_1600x2400", 1600, 2400, 65, 25, 25, 40, 2),
]

WINDOW_PRESETS = [
    ("Fixed_600x1200",      600, 1200),
    ("Fixed_600x1500",      600, 1500),
    ("Fixed_900x1200",      900, 1200),
    ("Fixed_900x1500",      900, 1500),
    ("Fixed_1200x1500",    1200, 1500),
    ("Fixed_1500x1500",    1500, 1500),
    ("Fixed_1800x1500",    1800, 1500),
    ("Casement_1200x1500", 1200, 1500),
    ("Casement_1500x1500", 1500, 1500),
    ("Casement_1800x1500", 1800, 1500),
    ("Casement_2400x1500", 2400, 1500),
    ("Sliding_1800x1500",  1800, 1500),
    ("Sliding_2400x1500",  2400, 1500),
]

FURNITURE_PRESETS = [
    ("Chair_Office_600x600x900",    600,   600,  900),
    ("Chair_Dining_500x500x800",    500,   500,  800),
    ("Table_Dining_4pax_1200x800", 1200,   800,  750),
    ("Table_Dining_6pax_1600x800", 1600,   800,  750),
    ("Table_Conference_2400x1000", 2400,  1000,  750),
    ("Table_Conference_3600x1200", 3600,  1200,  750),
    ("Desk_Office_1200x600",       1200,   600,  750),
    ("Desk_Office_1600x700",       1600,   700,  750),
    ("Sofa_2Seat_1400x850",        1400,   850,  800),
    ("Sofa_3Seat_2000x850",        2000,   850,  800),
    ("Bed_Single_1000x2000",       1000,  2000,  500),
    ("Bed_Double_1600x2000",       1600,  2000,  500),
    ("Bed_King_1800x2000",         1800,  2000,  500),
    ("Wardrobe_2Door_1200x600",    1200,   600, 2200),
    ("Wardrobe_3Door_1800x600",    1800,   600, 2200),
    ("Bookcase_900x300x2100",       900,   300, 2100),
]

CASEWORK_PRESETS = [
    ("Cabinet_Base_600x600x850",       600,   600,  850),
    ("Cabinet_Base_800x600x850",       800,   600,  850),
    ("Cabinet_Wall_600x300x600",       600,   300,  600),
    ("Cabinet_Wall_800x300x600",       800,   300,  600),
    ("Counter_Kitchen_1200x600",      1200,   600,  900),
    ("Counter_Kitchen_1800x600",      1800,   600,  900),
    ("Counter_Kitchen_2400x600",      2400,   600,  900),
    ("Island_Kitchen_1200x900",       1200,   900,  900),
    ("Island_Kitchen_1500x900",       1500,   900,  900),
    ("Vanity_Unit_900x500x850",        900,   500,  850),
    ("Vanity_Unit_1200x500x850",      1200,   500,  850),
    ("Shelf_Unit_900x300x2100",        900,   300, 2100),
    ("Display_Cabinet_1200x400x2200", 1200,   400, 2200),
]

PLUMBING_PRESETS = [
    ("WC_Toilet_Std_380x700",      380,  700, 400),
    ("WC_Toilet_Compact_360x650",  360,  650, 400),
    ("WC_Wall_Hung_380x560",       380,  560, 390),
    ("Sink_Vanity_600x500",        600,  500, 150),
    ("Sink_Vanity_800x500",        800,  500, 150),
    ("Sink_Kitchen_800x500",       800,  500, 200),
    ("Sink_Kitchen_1000x500",     1000,  500, 200),
    ("Bath_Builtin_1500x700",     1500,  700, 600),
    ("Bath_Builtin_1700x700",     1700,  700, 600),
    ("Bath_Freestanding_1700x800",1700,  800, 600),
    ("Shower_Tray_900x900",        900,  900, 150),
    ("Shower_Tray_1200x800",      1200,  800, 150),
    ("Shower_Tray_1200x900",      1200,  900, 150),
    ("Urinal_Std_360x330",         360,  330, 560),
    ("Floor_Drain_150x150",        150,  150,  80),
]

LIGHTING_PRESETS = [
    ("Downlight_D100",        100,  100,  80),
    ("Downlight_D150",        150,  150,  80),
    ("Downlight_D200",        200,  200, 100),
    ("Panel_300x600",         300,  600,  80),
    ("Panel_300x1200",        300, 1200,  80),
    ("Panel_600x600",         600,  600,  80),
    ("Ceiling_Round_D300",    300,  300, 120),
    ("Ceiling_Round_D400",    400,  400, 150),
    ("Ceiling_Round_D600",    600,  600, 180),
    ("Linear_Strip_1200",     100, 1200,  60),
    ("Linear_Strip_2400",     100, 2400,  60),
    ("Pendant_D200",          200,  200, 300),
    ("Pendant_D400",          400,  400, 300),
    ("Wall_Sconce_200x100",   200,  100, 250),
    ("Floodlight_300x200",    300,  200, 150),
]

MECHANICAL_PRESETS = [
    ("FCU_Cassette_600x600",          600,   600,  280),
    ("FCU_Cassette_900x900",          900,   900,  280),
    ("FCU_Cassette_1200x1200",       1200,  1200,  280),
    ("FCU_Wall_Mount_900x300",        900,   300,  250),
    ("FCU_Wall_Mount_1200x300",      1200,   300,  250),
    ("AHU_Floor_800x600x1500",        800,   600, 1500),
    ("AHU_Floor_1200x800x1800",      1200,   800, 1800),
    ("AHU_Ceiling_1500x800x500",     1500,   800,  500),
    ("Chiller_2000x1000x1500",       2000,  1000, 1500),
    ("Cooling_Tower_2000x2000x3000", 2000,  2000, 3000),
    ("Pump_600x400x500",              600,   400,  500),
    ("Boiler_900x700x1200",           900,   700, 1200),
    ("Expansion_Tank_D600x900",       600,   600,  900),
]

ELECTRICAL_PRESETS = [
    ("Panel_DB_500x200x600",           500,  200,  600),
    ("Panel_DB_600x250x1000",          600,  250, 1000),
    ("Panel_MDB_800x400x1200",         800,  400, 1200),
    ("Cabinet_Control_800x400x1800",   800,  400, 1800),
    ("Cabinet_Control_1000x500x2000", 1000,  500, 2000),
    ("UPS_600x600x1000",               600,  600, 1000),
    ("UPS_800x700x1200",               800,  700, 1200),
    ("Transformer_1000x700x1400",     1000,  700, 1400),
    ("Switchgear_800x600x2000",        800,  600, 2000),
    ("Switchgear_1200x800x2200",      1200,  800, 2200),
    ("Socket_Outlet_86x86",             86,   86,   60),
    ("Junction_Box_150x150x100",       150,  150,  100),
]

SPECIALTY_PRESETS = [
    ("Counter_Reception_2000x800x1100",  2000,  800, 1100),
    ("Counter_Reception_3000x800x1100",  3000,  800, 1100),
    ("Counter_Service_1500x700x900",     1500,  700,  900),
    ("ATM_Machine_500x500x1800",          500,  500, 1800),
    ("Vending_Machine_700x800x1900",      700,  800, 1900),
    ("Safe_600x500x800",                  600,  500,  800),
    ("Server_Rack_600x1000x2000",         600, 1000, 2000),
    ("Server_Rack_800x1200x2200",         800, 1200, 2200),
    ("Kiosk_Self_Service_700x700x1500",   700,  700, 1500),
    ("Turnstile_1000x600x1000",          1000,  600, 1000),
    ("Fire_Extinguisher_D200x550",        200,  200,  550),
    ("Fire_Hose_Cabinet_700x250x900",     700,  250,  900),
]

COLUMN_PRESETS = [
    ("Col_Square_200x200x3000", 200, 200, 3000),
    ("Col_Square_250x250x3000", 250, 250, 3000),
    ("Col_Square_300x300x3000", 300, 300, 3000),
    ("Col_Square_400x400x3000", 400, 400, 3000),
    ("Col_Square_500x500x3000", 500, 500, 3000),
    ("Col_Square_600x600x3000", 600, 600, 3000),
    ("Col_Rect_200x400x3000",   200, 400, 3000),
    ("Col_Rect_250x500x3000",   250, 500, 3000),
    ("Col_Rect_300x600x3000",   300, 600, 3000),
    ("Col_Rect_350x700x3000",   350, 700, 3000),
    ("Col_Round_D300x3000",     300, 300, 3000),
    ("Col_Round_D400x3000",     400, 400, 3000),
    ("Col_Round_D500x3000",     500, 500, 3000),
]

GENERIC_PRESETS = [
    ("Box_100x100x100",     100,  100,  100),
    ("Box_200x200x200",     200,  200,  200),
    ("Box_500x500x500",     500,  500,  500),
    ("Box_1000x500x500",   1000,  500,  500),
    ("Box_1000x1000x500",  1000, 1000,  500),
    ("Box_1000x1000x1000", 1000, 1000, 1000),
    ("Slab_2000x1000x200", 2000, 1000,  200),
    ("Slab_3000x2000x300", 3000, 2000,  300),
    ("Wall_3000x200x3000", 3000,  200, 3000),
]

ENTOURAGE_PRESETS = [
    ("Tree_Small_D2000x4000",        2000, 2000, 4000),
    ("Tree_Medium_D3000x6000",       3000, 3000, 6000),
    ("Tree_Large_D5000x8000",        5000, 5000, 8000),
    ("Shrub_D1000x600",              1000, 1000,  600),
    ("Person_Standing_600x300x1750",  600,  300, 1750),
    ("Person_Seated_600x700x1200",    600,  700, 1200),
    ("Car_Sedan_4500x1900x1450",     4500, 1900, 1450),
    ("Car_SUV_4700x2000x1700",       4700, 2000, 1700),
    ("Bicycle_1800x600x1100",        1800,  600, 1100),
    ("Motorcycle_2200x800x1200",     2200,  800, 1200),
]

SITE_PRESETS = [
    ("Parking_Space_2500x5000",   2500, 5000,   50),
    ("Parking_Space_2700x5500",   2700, 5500,   50),
    ("Disabled_Parking_3500x5000",3500, 5000,   50),
    ("Bike_Stand_600x200x1000",    600,  200, 1000),
    ("Bollard_D200x800",           200,  200,  800),
    ("Planter_Box_1000x500x600",  1000,  500,  600),
    ("Planter_Box_2000x500x600",  2000,  500,  600),
    ("Bench_1800x500x450",        1800,  500,  450),
    ("Bench_1200x500x450",        1200,  500,  450),
    ("Sign_Post_100x100x3000",     100,  100, 3000),
    ("Waste_Bin_D400x800",         400,  400,  800),
    ("Light_Pole_D200x5000",       200,  200, 5000),
]

CATEGORY_PRESETS = {
    "Generic Model":        ("generic", GENERIC_PRESETS),
    "Door":                 ("door",    DOOR_PRESETS),
    "Window":               ("window",  WINDOW_PRESETS),
    "Furniture":            ("generic", FURNITURE_PRESETS),
    "Plumbing Fixture":     ("generic", PLUMBING_PRESETS),
    "Electrical Equipment": ("generic", ELECTRICAL_PRESETS),
    "Mechanical Equipment": ("generic", MECHANICAL_PRESETS),
    "Specialty Equipment":  ("generic", SPECIALTY_PRESETS),
    "Casework":             ("generic", CASEWORK_PRESETS),
    "Columns":              ("generic", COLUMN_PRESETS),
    "Lighting Fixture":     ("generic", LIGHTING_PRESETS),
    "Site":                 ("generic", SITE_PRESETS),
    "Entourage":            ("generic", ENTOURAGE_PRESETS),
}

_CAT_HINTS = [
    ("Door", [
        "door", "swing", "sliding door", "folding door",
        "cua di", "cuadi", "cua ra", "cua vao",
        "a-door", "kl-door", "arch-door", "-door",
    ]),
    ("Window", [
        "window", "casement", "skylight",
        "cua so", "cuaso",
        "a-wind", "kl-wind", "-wind",
    ]),
    ("Furniture", [
        "furnitur", "chair", "table", "desk", "sofa", "bed",
        "armchair", "bookcase", "wardrobe", "lounge", "seating",
        "noi that", "noithat", "ban ghe", "banghe",
        "ban lam viec", "ghe ngoi", "tu quan ao",
        "a-furn", "kl-furn", "-furn", "ff&e", "ff-e",
        "-furniture", "a-furniture",
    ]),
    ("Casework", [
        "casework", "counter", "kitchen", "shelv", "cabinet",
        "cupboard", "joinery",
        "tu bep", "tubep", "tu am tuong", "tu bep duoi",
        "quay bep", "quay le tan", "bep",
        "a-case", "kl-case", "-casework",
    ]),
    ("Plumbing Fixture", [
        "plumb", "sanitary", "toilet", "wc", "sink", "basin",
        "shower", "bath", "urinal", "lavatory", "bidet",
        "thiet bi ve sinh", "thietbivesinnh", "bon tam", "bon cau",
        "chau rua", "chau lavabo", "thiet bi nuoc", "ve sinh",
        "p-fixt", "m-plmb", "kl-plmb", "-plumb", "-sanitary",
        "eqpm-fixd", "eqpm-fix", "eqpm",
    ]),
    ("Lighting Fixture", [
        "light", "lamp", "luminaire", "led", "spotlight",
        "downlight", "pendant", "sconce", "chandelier",
        "den", "den chieu sang", "chieu sang", "den treo",
        "den am tran", "den tuong",
        "e-lite", "e-lght", "kl-lght", "-light", "-lite",
        "-lighting", "a-lighting",
    ]),
    ("Mechanical Equipment", [
        "mechanical", "hvac", "ahu", "fcu", "fahu", "chiller",
        "cooling", "boiler", "pump", "fan", "duct", "damper",
        "may lanh", "maylanh", "dieu hoa", "dieuhoa",
        "thong gio", "cap nhiet", "bom nhiet",
        "m-equip", "m-mech", "kl-mech", "-mech", "-hvac",
    ]),
    ("Electrical Equipment", [
        "electrical", "switchgear", "transformer", "ups",
        "panel", "mdb", "smdb", "db", "mcb", "busbar",
        "dien", "tu dien", "tudien", "bang dien", "thiet bi dien",
        "e-equip", "e-powr", "kl-elec", "-elec", "-electr",
    ]),
    ("Specialty Equipment", [
        "machine", "appliance", "kiosk", "atm",
        "vending", "server", "rack",
        "may moc",
        "a-equip", "kl-equip",
    ]),
    ("Columns", [
        "column", " col ", "pillar", "pier", "post",
        "struc", "structural", "ket cau", "ketcau",
        "beam", "slab", "footing", "foundation",
        "cot", "tru", "dam", "san", "mong",
        "s-col", "a-col", "kl-col", "-col-", "-column",
        "kc-", "s-beam", "s-slab", "s-wall", "s-str",
    ]),
    ("Site", [
        "site", "parking", "landscape", "paving", "bollard",
        "tree", "bench", "pavement",
        "san vuon", "cay xanh", "bai xe", "he thong ngoai that",
        "l-site", "a-site", "kl-site", "-site", "-land",
    ]),
    ("Entourage", [
        "entourage", "person", "people", "car", "vehicle",
        "bicycle", "human", "figure",
        "nguoi", "xe hoi", "xe dap",
        "-entour", "a-entour",
    ]),
    ("Generic Model", [
        "wall", "tuong", "a-wall", "kl-wall", "s-wall-",
        "glass", "glazing", "curtain", "kinh",
        "title block", "titleblock", "title blk", "title-blk",
        "khung ten", "khungten", "khung-ten",
        "border", "sheet border", "annotation", "detailitem",
        "detail item", "tb-", "-tblock",
    ]),
]


# ==============================================================================
# HELPER CLASSES / FUNCTIONS
# ==============================================================================

class WarningSwallower(IFailuresPreprocessor):
    __namespace__ = "T3Lab.FamiGen"

    def PreprocessFailures(self, failuresAccessor):
        fail_list = failuresAccessor.GetFailureMessages()
        if fail_list.Count == 0:
            return FailureProcessingResult.Continue
        for failure in fail_list:
            if failure.GetSeverity() == FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(failure)
        return FailureProcessingResult.Continue


def start_transaction(t):
    options = t.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(WarningSwallower())
    t.SetFailureHandlingOptions(options)
    return t.Start()


def _suggest_category(name, arc_count, width_mm, depth_mm, layer=""):
    combined = (name + " " + layer).lower()
    for cat, keywords in _CAT_HINTS:
        if any(k in combined for k in keywords):
            return "Generic Model" if cat == "Door" else cat
    if arc_count == 0 and 0 < depth_mm < 350 and width_mm >= 400:
        return "Window"
    return "Generic Model"


def _graphicstyle_layer(geom_elem, doc):
    for item in geom_elem:
        try:
            sid = getattr(item, 'GraphicsStyleId', None)
            if sid and sid != ElementId.InvalidElementId:
                style = doc.GetElement(sid)
                if style:
                    try:
                        cat = style.GraphicsStyleCategory
                        if cat and cat.Name:
                            return cat.Name
                    except Exception:
                        pass
                    try:
                        if style.Name:
                            return style.Name
                    except Exception:
                        pass
        except Exception:
            pass
        if isinstance(item, GeometryInstance):
            try:
                nested = item.GetInstanceGeometry()
                if nested:
                    result = _graphicstyle_layer(nested, doc)
                    if result:
                        return result
            except Exception:
                pass
    return ""


class BlockItem(object):
    def __init__(self, name, curve_count, instance_count, curves,
                 layer_level="", placements=None, import_inst=None, unit=None):
        self.IsSelected    = True
        self.BlockName     = name
        self.CurveCount    = curve_count
        self.InstanceCount = instance_count
        self.LayerLevel    = layer_level
        self._curves       = curves
        self._placements   = placements if placements is not None else []
        self._import_inst  = import_inst

        arc_count = sum(1 for c in curves if isinstance(c, Arc))
        self.ArcCount = arc_count

        # Grid shows the size in the document's unit (column header carries
        # it); the category hint and the .rfa name keep using whole mm.
        unit = unit or MILLIMETERS
        try:
            min_x, max_x, min_y, max_y = get_xy_bounds(curves)
            w = MILLIMETERS.from_feet(max_x - min_x)
            d = MILLIMETERS.from_feet(max_y - min_y)
            self.WidthMM = "{:.0f}".format(w)
            self.DepthMM = "{:.0f}".format(d)
            self.WidthText = unit.text(max_x - min_x)
            self.DepthText = unit.text(max_y - min_y)
        except Exception:
            w, d = 0.0, 0.0
            self.WidthMM = "-"
            self.DepthMM = "-"
            self.WidthText = "-"
            self.DepthText = "-"

        self.SuggestedCat = _suggest_category(name, arc_count, w, d, layer=layer_level)
        self.Category     = self.SuggestedCat


class _LegendRow(object):
    """One materials-legend row. `swatch` is the material colour as "#RRGGBB" -
    DATA from the schema, read by the XAML through a string bridge (WPF cannot
    convert a Python attribute to a Brush, but converts a string)."""

    def __init__(self, material, rgb, solids):
        self.name = material.get('name', '')
        self.swatch = color_hex(rgb) if rgb else '#00000000'
        self.count_text = '{} part{}'.format(solids, '' if solids == 1 else 's')
        extras = []
        if material.get('transparency'):
            extras.append('transparency {}%'.format(material['transparency']))
        if material.get('shininess') is not None:
            extras.append('shininess {}'.format(material['shininess']))
        if material.get('smoothness') is not None:
            extras.append('smoothness {}'.format(material['smoothness']))
        self.detail = '{} · parameter "{}"{}'.format(
            self.swatch, material_parameter_name(material),
            (' · ' + ', '.join(extras)) if extras else '')


class _FamiGenAction(IExternalEventHandler):
    """Runs one queued callable on Revit's API thread for the modeless window.

    Only the window the MCP server opens is modeless; its WPF handlers run
    OUTSIDE Revit API context, so Create Family is queued here. Static
    `__namespace__` is right: the class lives in lib/ (rule S15).
    """
    __namespace__ = "T3Lab.FamiGenAction"

    def __init__(self):
        self._action = None

    def set_action(self, action):
        self._action = action

    def Execute(self, uiapp):
        action, self._action = self._action, None
        if action is None:
            return
        try:
            action(uiapp)
        except Exception as ex:
            # An exception escaping Execute() takes Revit down - report instead.
            try:
                TaskDialog.Show('FamiGen', 'The action failed:\n{}\n\n{}'.format(
                    ex, traceback.format_exc()))
            except Exception:
                pass

    def GetName(self):
        return "T3Lab FamiGen action"


# Aliases so methods copied verbatim from CAD script compile without change
DISCIPLINES       = _DISCIPLINES
CATEGORY_TEMPLATES = _CATEGORY_TEMPLATES


# ==============================================================================
# COMBINED DIALOG
# ==============================================================================

class FamilyCreatorDialog(T3WPFWindow):
    AI_TOOL = "FamiGen"


    # ProgressPauseMixin element names — FamiGen.xaml uses export-suffixed names
    PP_BAR      = "pb_export"
    PP_PAUSE    = "btn_pause_export"
    PP_STOP     = "btn_stop_export"
    PP_STOP_MSG = u"Stopping… finishing current block"

    def __init__(self, revit_doc, revit_app, initial_mode='cad', modeless=False):
        T3WPFWindow.__init__(self, _XAML)
        self._doc = revit_doc
        self._app = revit_app
        self._unit = project_length_unit(revit_doc)
        for name, text in (('col_block_width', "WIDTH"), ('col_block_depth', "DEPTH")):
            col = getattr(self, name, None)     # grid columns: guard the lookup
            if col is not None:
                col.Header = self._unit.label(text)
        # Modeless = opened by the MCP server (famigen_propose_family). It
        # runs outside Revit API context after Show(), so it offers the JSON
        # review workflow only and queues Create Family on an ExternalEvent.
        # ExternalEvent.Create needs API context: true here in both cases.
        self._modeless = bool(modeless)
        self._action_handler = None
        self._action_event = None
        if self._modeless:
            self._action_handler = _FamiGenAction()
            self._action_event = ExternalEvent.Create(self._action_handler)
        self._block_items      = []
        self._cad_instances    = []
        self._filter_text      = ""
        self._filter_cat       = ""
        self._cancel_requested = False
        self._pause_requested  = False
        self._prev_json_backup = None
        self._ai_generating = False
        self._ai_request_id = 0
        self._ai_closed = False
        self._ai_control_states = []

        self._init_cad_panel()
        self._init_json_panel()
        self._init_preview()
        if self._modeless:
            try:
                self.mode_cad.IsEnabled = False
                self.mode_cad.ToolTip = ("From CAD is available when FamiGen is opened "
                                         "from the ribbon")
            except Exception:
                pass

        if initial_mode == 'json' or self._modeless:
            self._show_panel('json')
        else:
            self._show_panel('cad')

        self._update_ai_status()

    def _update_ai_status(self):
        self.init_ai_badge()

    # ── Window chrome ────────────────────────────────────────────────────────

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self.Close()

    def ai_window_closed(self, sender, e):
        """Discard pending responses when this window closes, including Alt+F4."""
        self._ai_closed = True
        self._ai_request_id += 1
        self._ai_generating = False
        self._ai_control_states = []
        timer = getattr(self, '_preview_timer', None)
        if timer is not None:
            try:
                timer.Stop()
            except Exception:
                pass
        family_proposals.clear_active_window(self)

    # ── Mode switching ───────────────────────────────────────────────────────

    def nav_cad_clicked(self, sender, e):
        """Handle cad toggle click."""
        self._show_panel('cad')

    def nav_json_clicked(self, sender, e):
        """Handle json toggle click."""
        self._show_panel('json')

    def _show_panel(self, mode):
        try:
            panel_cad = self.FindName('panel_cad') or getattr(self, 'panel_cad', None)
            panel_json = self.FindName('panel_json') or getattr(self, 'panel_json', None)
            
            if panel_cad:
                panel_cad.Visibility = WinVis.Visible if mode == 'cad' else WinVis.Collapsed
            if panel_json:
                panel_json.Visibility = WinVis.Visible if mode == 'json' else WinVis.Collapsed
            
            # Sync button checked status
            mode_cad = self.FindName('mode_cad') or getattr(self, 'mode_cad', None)
            mode_json = self.FindName('mode_json') or getattr(self, 'mode_json', None)
            if mode_cad:
                mode_cad.IsChecked = (mode == 'cad')
            if mode_json:
                mode_json.IsChecked = (mode == 'json')
            # The active workflow has one primary action and one Enter default.
            self.btn_export.Style = self.FindResource(
                'T3.Button.Primary' if mode == 'cad' else 'T3.Button.Secondary')
            self.create_btn.Style = self.FindResource(
                'T3.Button.Primary' if mode == 'json' else 'T3.Button.Secondary')
            self.btn_export.IsDefault = (mode == 'cad')
            self.create_btn.IsDefault = (mode == 'json')
        except Exception as ex:
            print("Error in _show_panel: {}".format(ex))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass


    # ── Status helpers ───────────────────────────────────────────────────────

    def _update_status(self, text):
        try:
            self.status_text.Text = text
        except Exception:
            pass

    # ── CAD panel initialisation ─────────────────────────────────────────────

    def _init_cad_panel(self):
        self._init_cad_files()
        self._init_disciplines()
        self._init_categories()
        self._init_filter_bar()
        self._update_status("Ready")

    def _init_cad_files(self):
        collector = FilteredElementCollector(self._doc).OfClass(ImportInstance)
        self.cad_file_combo.Items.Add("<All Imported CAD Files>")
        for inst in collector:
            name = self._get_cad_name(inst)
            self._cad_instances.append(inst)
            self.cad_file_combo.Items.Add(name)
        if self._cad_instances:
            self.cad_file_combo.SelectedIndex = 0

    @staticmethod
    def _read_symbol_name(inst):
        from Autodesk.Revit.DB import BuiltInParameter as BIP
        try:
            p = inst.get_Parameter(BIP.IMPORT_SYMBOL_NAME)
            if p and p.HasValue:
                val = p.AsString()
                if val:
                    return val
        except Exception:
            pass
        try:
            for p in inst.Parameters:
                if p.Definition.Name == "Name" and p.StorageType.ToString() == "String":
                    val = p.AsString()
                    if val:
                        return val
        except Exception:
            pass
        try:
            type_id = inst.GetTypeId()
            if type_id and type_id != ElementId.InvalidElementId:
                elem_type = inst.Document.GetElement(type_id)
                if elem_type and hasattr(elem_type, 'Name') and elem_type.Name:
                    return elem_type.Name
        except Exception:
            pass
        return inst.Name if hasattr(inst, 'Name') else "Unknown"

    def _get_cad_name(self, inst):
        return self._read_symbol_name(inst)

    def _init_disciplines(self):
        for name in DISCIPLINES:
            self.discipline_combo.Items.Add(name)
        self.discipline_combo.SelectedIndex = 6

    def _init_categories(self):
        cat_names = [name for name, _ in CATEGORY_TEMPLATES]
        for name in cat_names:
            self.category_combo.Items.Add(name)
        self.category_combo.SelectedIndex = 0

        col = DataGridComboBoxColumn()
        col.Header = "Category"
        col.Width = DataGridLength(140)
        col.ItemsSource = to_items_source(cat_names)
        b = Binding("Category")
        b.Mode = BindingMode.TwoWay
        b.UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged
        col.SelectedItemBinding = b
        self.blocks_grid.Columns.Add(col)

    def _init_json_panel(self):
        """Populate the family category selector on the AI / JSON panel with
        every category FamiGen has a template for (the same list the schema
        validates against). A prompts/<slug>.md overlay adds category guidance
        when it exists; it is optional."""
        try:
            combo = (getattr(self, 'json_category_combo', None)
                     or self.FindName('json_category_combo'))
            if combo is None:
                return
            combo.Items.Clear()
            for name in SUPPORTED_CATEGORIES:
                combo.Items.Add(name)
            combo.SelectedIndex = 0
        except Exception:
            logger.warning("json panel init: {}".format(traceback.format_exc()))

    def _init_filter_bar(self):
        self.combo_filter_suggested.Items.Add("All Categories")
        self.combo_filter_suggested.SelectedIndex = 0

    # ── Filter ───────────────────────────────────────────────────────────────

    def _refresh_suggested_combo(self):
        prev = self._filter_cat
        self.combo_filter_suggested.SelectionChanged -= self.filter_suggested_changed
        self.combo_filter_suggested.Items.Clear()
        self.combo_filter_suggested.Items.Add("All Categories")
        cats = sorted(set(item.Category for item in self._block_items if item.Category))
        for c in cats:
            self.combo_filter_suggested.Items.Add(c)
        if prev and prev in cats:
            self.combo_filter_suggested.SelectedItem = prev
        else:
            self.combo_filter_suggested.SelectedIndex = 0
            self._filter_cat = ""
        self.combo_filter_suggested.SelectionChanged += self.filter_suggested_changed

    def _apply_filter(self):
        txt = self._filter_text.lower().strip()
        cat = self._filter_cat
        if not txt and not cat:
            visible = self._block_items
        else:
            visible = []
            for item in self._block_items:
                if txt and txt not in item.BlockName.lower() \
                        and txt not in item.LayerLevel.lower():
                    continue
                if cat and item.Category != cat:
                    continue
                visible.append(item)
        self.blocks_grid.ItemsSource = to_items_source(visible)
        total   = len(self._block_items)
        showing = len(visible)
        if total == 0:
            self.txt_filter_count.Text = ""
        elif showing == total:
            self.txt_filter_count.Text = "{} items".format(total)
        else:
            self.txt_filter_count.Text = "{} / {} items".format(showing, total)

    def search_text_changed(self, sender, e):
        self._filter_text = self.txt_search.Text or ""
        has_text = bool(self._filter_text)
        self.txt_search_placeholder.Visibility = WinVis.Collapsed if has_text else WinVis.Visible
        self.btn_clear_search.Visibility       = WinVis.Visible   if has_text else WinVis.Collapsed
        self._apply_filter()

    def clear_search_clicked(self, sender, e):
        self.txt_search.Text = ""

    def filter_suggested_changed(self, sender, e):
        sel = self.combo_filter_suggested.SelectedItem
        self._filter_cat = "" if (sel is None or sel == "All Categories") else sel
        self._apply_filter()

    # ── Progress / Pause / Stop — provided by ProgressPauseMixin ────────────
    # FamiGen.xaml wires Click="stop_export_clicked"; delegate to the mixin.

    def stop_export_clicked(self, sender, e):
        self.stop_clicked(sender, e)

    # ── Scanning ─────────────────────────────────────────────────────────────

    def scan_blocks_clicked(self, sender, e):
        if not self._cad_instances:
            forms.alert("No imported CAD files found in the document.")
            return
        idx = self.cad_file_combo.SelectedIndex - 1
        if idx < -1 or idx >= len(self._cad_instances):
            forms.alert("Please select a CAD file.")
            return
        self._update_status("Scanning blocks...")
        blocks = []
        try:
            if idx == -1:
                name_counts = {}
                for inst in self._cad_instances:
                    item = self._scan_entire_cad(inst)
                    if item:
                        base_name = item.BlockName
                        if base_name in name_counts:
                            name_counts[base_name] += 1
                            item.BlockName = "{}_{}".format(base_name, name_counts[base_name])
                        else:
                            name_counts[base_name] = 1
                        blocks.append(item)
            else:
                import_inst = self._cad_instances[idx]
                blocks = self._scan_blocks(import_inst)
                if not blocks:
                    item = self._scan_entire_cad(import_inst)
                    if item:
                        blocks.append(item)
        except Exception as ex:
            logger.error("Scan error:\n{}".format(traceback.format_exc()))
            forms.alert("Error scanning blocks:\n{}".format(str(ex)))
            self._update_status("Scan failed")
            return
        if not blocks:
            forms.alert("No blocks or curves found in the selected CAD file(s).")
            self._update_status("No geometry found")
            return
        self._block_items  = blocks
        self._filter_text  = ""
        self._filter_cat   = ""
        self.txt_search.Text = ""
        self._refresh_suggested_combo()
        self._apply_filter()
        self._update_status("Found {} unique item(s)".format(len(blocks)))
        self.block_count_text.Text = "{} items found".format(len(blocks))

    def _scan_entire_cad(self, import_inst):
        opt = Options()
        opt.ComputeReferences = True
        opt.IncludeNonVisibleObjects = True
        geom = import_inst.get_Geometry(opt)
        if not geom:
            return None
        min_len = getattr(self._app, 'ShortCurveTolerance', 0.00256)

        def is_curve(item):
            try:
                from Autodesk.Revit.DB import Curve as _Curve
                return isinstance(item, _Curve) and item.IsBound and item.Length >= min_len
            except Exception:
                return False

        def collect_curves(geo_elem):
            from Autodesk.Revit.DB import PolyLine, Curve as _Curve
            curves = []
            for item in geo_elem:
                if is_curve(item):
                    curves.append(item)
                elif isinstance(item, _Curve) and not item.IsBound:
                    curves.append(item)
                elif isinstance(item, PolyLine):
                    pts = item.GetCoordinates()
                    for i in range(item.NumberOfCoordinates - 1):
                        try:
                            p1, p2 = pts[i], pts[i + 1]
                            if p1.DistanceTo(p2) >= min_len:
                                curves.append(Line.CreateBound(p1, p2))
                        except Exception:
                            pass
                elif isinstance(item, GeometryInstance):
                    try:
                        nested = item.GetInstanceGeometry()
                        if nested:
                            curves.extend(collect_curves(nested))
                    except Exception:
                        pass
                elif isinstance(item, Solid):
                    try:
                        for edge in item.Edges:
                            try:
                                ec = edge.AsCurve()
                                if is_curve(ec):
                                    curves.append(ec)
                            except Exception:
                                pass
                    except Exception:
                        pass
            return curves

        curves = collect_curves(geom)
        if curves:
            name = self._get_cad_name(import_inst)
            layer_name = _graphicstyle_layer(geom, self._doc)
            try:
                min_x, max_x, min_y, max_y = get_xy_bounds(curves)
                centroid = XYZ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, 0.0)
            except Exception:
                centroid = XYZ.Zero
            placements = [(centroid, 0.0)]
            return BlockItem(name, len(curves), 1, curves,
                             layer_level=layer_name, placements=placements,
                             import_inst=import_inst, unit=self._unit)
        return None

    def _scan_blocks(self, import_inst):
        opt = Options()
        opt.ComputeReferences = True
        opt.IncludeNonVisibleObjects = True
        geom = import_inst.get_Geometry(opt)
        if not geom:
            return []
        min_len = getattr(self._app, 'ShortCurveTolerance', 0.00256)
        found   = {}
        counter = [0]

        def is_curve(item):
            try:
                from Autodesk.Revit.DB import Curve as _Curve
                return isinstance(item, _Curve) and item.IsBound and item.Length >= min_len
            except Exception:
                return False

        def collect_curves(geo_elem):
            curves = []
            for item in geo_elem:
                if is_curve(item):
                    curves.append(item)
                elif isinstance(item, GeometryInstance):
                    try:
                        nested = item.GetInstanceGeometry()
                        if nested:
                            curves.extend(collect_curves(nested))
                    except Exception:
                        pass
                elif isinstance(item, Solid):
                    try:
                        for edge in item.Edges:
                            try:
                                ec = edge.AsCurve()
                                if is_curve(ec):
                                    curves.append(ec)
                            except Exception:
                                pass
                    except Exception:
                        pass
            return curves

        def fingerprint(curves):
            return (len(curves), round(sum(c.Length for c in curves), 1))

        def style_name(geo_inst):
            try:
                sid = geo_inst.GraphicsStyleId
                if sid and sid != ElementId.InvalidElementId:
                    style = self._doc.GetElement(sid)
                    if style:
                        try:
                            cat = style.GraphicsStyleCategory
                            if cat and cat.Name:
                                return cat.Name
                        except Exception:
                            pass
                        try:
                            if style.Name:
                                return style.Name
                        except Exception:
                            pass
            except Exception:
                pass
            return None

        def _instance_placement(curves, geo_inst):
            try:
                min_x, max_x, min_y, max_y = get_xy_bounds(curves)
                centroid = XYZ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, 0.0)
            except Exception:
                centroid = XYZ.Zero
            try:
                bx    = geo_inst.Transform.BasisX
                angle = math.atan2(bx.Y, bx.X)
            except Exception:
                angle = 0.0
            return (centroid, angle)

        def register(curves, geo_inst):
            fp        = fingerprint(curves)
            placement = _instance_placement(curves, geo_inst)
            if fp in found:
                found[fp]['count'] += 1
                found[fp]['placements'].append(placement)
                return
            layer = style_name(geo_inst)
            counter[0] += 1
            block_name = ""
            try:
                if hasattr(geo_inst, 'Symbol') and geo_inst.Symbol:
                    block_name = (geo_inst.Symbol.Name or "").strip()
            except Exception:
                pass
            if not block_name:
                block_name = (FamilyCreatorDialog._read_symbol_name(import_inst) or "").strip()
            if block_name:
                name = block_name
            elif layer:
                name = "{}_Block_{:03d}".format(layer, counter[0])
            else:
                name = "Block_{:03d}".format(counter[0])
            found[fp] = {
                'name': name, 'curves': curves, 'count': 1,
                'layer': layer or "", 'placements': [placement],
            }

        def walk(geo_elem, depth):
            for item in geo_elem:
                if not isinstance(item, GeometryInstance):
                    continue
                inst_geom = item.GetInstanceGeometry()
                if not inst_geom:
                    continue
                if depth == 0:
                    walk(inst_geom, depth + 1)
                else:
                    curves = collect_curves(inst_geom)
                    if curves:
                        register(curves, item)

        walk(geom, 0)

        items = []
        for data in sorted(found.values(), key=lambda d: d['name']):
            items.append(BlockItem(
                data['name'], len(data['curves']), data['count'], data['curves'],
                layer_level=data.get('layer', ""),
                placements=data.get('placements', []),
                import_inst=import_inst, unit=self._unit))
        return items

    # ── CAD export UI ────────────────────────────────────────────────────────

    def browse_folder_clicked(self, sender, e):
        folder = forms.pick_folder()
        if folder:
            self.output_path.Text = folder

    def select_all_clicked(self, sender, e):
        for item in self._block_items:
            item.IsSelected = True
        self.blocks_grid.Items.Refresh()
        # Giu checkbox select-all o header khop voi nut nay.
        self.sync_header_checkbox(
            self.FindName("chk_all_blocks_grid"), self.blocks_grid, "IsSelected")

    def deselect_all_clicked(self, sender, e):
        for item in self._block_items:
            item.IsSelected = False
        self.blocks_grid.Items.Refresh()
        # Giu checkbox select-all o header khop voi nut nay.
        self.sync_header_checkbox(
            self.FindName("chk_all_blocks_grid"), self.blocks_grid, "IsSelected")

    def export_clicked(self, sender, e):
        output_folder = self.output_path.Text
        if not output_folder or not os.path.isdir(output_folder):
            forms.alert("Please select a valid output folder.")
            return
        selected = [b for b in self._block_items if b.IsSelected]
        if not selected:
            forms.alert("No blocks selected for export.")
            return
        disc_idx = self.discipline_combo.SelectedIndex
        if disc_idx < 0:
            forms.alert("Please select a discipline.")
            return
        discipline_name = DISCIPLINES[disc_idx]
        load_to_project = (self.chk_load_to_project.IsChecked == True)
        mode_2d = False
        try:
            mode_2d = bool(getattr(self, 'rb_2d_lines', None) and self.rb_2d_lines.IsChecked)
        except Exception:
            pass
        self._cancel_requested = False
        self._pause_requested  = False
        self._update_status("Exporting {} block(s)...".format(len(selected)))
        self._update_progress(0, len(selected))
        success, failed = 0, 0
        saved_paths = []
        import System as _System
        for i, item in enumerate(selected):
            if self._cancel_requested:
                break
            try:
                category_name = item.Category or "Generic Model"
                template_path = self._find_template_by_name(category_name)
                if not template_path:
                    logger.warning("No template for '{}', skipping '{}'".format(
                        category_name, item.BlockName))
                    failed += 1
                    self._update_progress(i + 1, len(selected))
                    continue
                self._update_status("Exporting [{}/{}]: {}".format(
                    i + 1, len(selected), item.BlockName))
                self._update_progress(i, len(selected))
                if self._cancel_requested:
                    break
                save_path = self._export_block(
                    item, template_path, output_folder,
                    discipline_name, category_name,
                    load_to_project=False,
                    mode_2d_only=mode_2d)
                if save_path:
                    saved_paths.append(save_path)
                    success += 1
                else:
                    failed += 1
            except Exception:
                logger.error("Export '{}' failed:\n{}".format(
                    item.BlockName, traceback.format_exc()))
                failed += 1
            self._update_progress(i + 1, len(selected))
            if (i + 1) % 10 == 0:
                try:
                    _System.GC.Collect()
                    _System.GC.WaitForPendingFinalizers()
                except Exception:
                    pass
        was_cancelled = self._cancel_requested
        loaded_count = 0
        if not was_cancelled and load_to_project and saved_paths:
            self._update_status("Loading {} families to project...".format(len(saved_paths)))
            loaded_count = self._batch_load_families(saved_paths)
        status = "Stopped" if was_cancelled else "Done"
        self._update_status("{}: {} exported, {} failed".format(status, success, failed))
        self._hide_progress()
        load_note = "\nLoaded to project: {}".format(loaded_count) if load_to_project else ""
        cancelled_note = "\n\nExport was stopped early." if was_cancelled else ""
        forms.alert(
            "Export complete!\n\nExported: {}\nFailed: {}{}{}\n\nOutput folder:\n{}".format(
                success, failed, load_note, cancelled_note, output_folder))

    def export_and_place_clicked(self, sender, e):
        output_folder = self.output_path.Text
        if not output_folder or not os.path.isdir(output_folder):
            forms.alert("Please select a valid output folder.")
            return
        selected = [b for b in self._block_items if b.IsSelected]
        if not selected:
            forms.alert("No blocks selected.")
            return
        disc_idx = self.discipline_combo.SelectedIndex
        discipline_name = DISCIPLINES[disc_idx] if disc_idx >= 0 else "General"
        place_level = None
        try:
            from pyrevit import revit as _revit
            from Autodesk.Revit.DB import Level
            place_level = self._doc.GetElement(_revit.uidoc.ActiveView.GenLevel.Id)
        except Exception:
            pass
        if not place_level:
            try:
                from Autodesk.Revit.DB import Level
                for lv in FilteredElementCollector(self._doc).OfClass(Level):
                    place_level = lv
                    break
            except Exception:
                pass
        mode_2d = False
        try:
            mode_2d = bool(getattr(self, 'rb_2d_lines', None) and self.rb_2d_lines.IsChecked)
        except Exception:
            pass
        exported, placed_total, failed = 0, 0, 0
        self._cancel_requested = False
        self._pause_requested  = False
        self._update_progress(0, len(selected))
        t_place = Transaction(self._doc, "T3Lab - Export & Place Families")
        start_transaction(t_place)
        try:
            for i, item in enumerate(selected):
                if self._cancel_requested:
                    break
                self._update_status(
                    "Exporting & placing [{}/{}]: {}".format(i + 1, len(selected), item.BlockName))
                self._update_progress(i, len(selected))
                if self._cancel_requested:
                    break
                try:
                    category_name = item.Category or "Generic Model"
                    template_path = self._find_template_by_name(category_name)
                    if not template_path:
                        failed += 1
                        self._update_progress(i + 1, len(selected))
                        continue
                    save_path = self._export_block(
                        item, template_path, output_folder,
                        discipline_name, category_name,
                        load_to_project=False, mode_2d_only=mode_2d)
                    if not save_path:
                        failed += 1
                        self._update_progress(i + 1, len(selected))
                        continue
                    exported += 1
                    n = self._place_family_instances(save_path, item, place_level)
                    placed_total += n
                except Exception:
                    logger.error("Export+Place '{}' failed:\n{}".format(
                        item.BlockName, traceback.format_exc()))
                    failed += 1
                self._update_progress(i + 1, len(selected))
            t_place.Commit()
        except Exception:
            try:
                t_place.RollBack()
            except Exception:
                pass
            self._hide_progress()
            logger.error("Export & Place failed:\n{}".format(traceback.format_exc()))
            forms.alert("Transaction failed - check the pyRevit log.")
            return
        was_cancelled = self._cancel_requested
        status = "Stopped" if was_cancelled else "Done"
        self._update_status("{}: {} exported, {} placed, {} failed".format(
            status, exported, placed_total, failed))
        self._hide_progress()
        forms.alert(
            "Export & Place complete!\n\n"
            "Families exported: {}\nInstances placed: {}\nFailed: {}\n\n"
            "Output folder:\n{}".format(exported, placed_total, failed, output_folder))

    # ── Template lookup ───────────────────────────────────────────────────────

    def _find_template(self, cat_idx):
        _, template_names = CATEGORY_TEMPLATES[cat_idx]
        search_dirs = []
        try:
            tdir = self._app.FamilyTemplatePath
            if tdir and os.path.isdir(tdir):
                search_dirs.append(tdir)
        except Exception:
            pass
        ver  = self._app.VersionNumber
        base = r"C:\ProgramData\Autodesk\RVT {}".format(ver)
        for sub in ("English", "", "English-Imperial", "English_I"):
            if sub:
                search_dirs.append(os.path.join(base, "Family Templates", sub))
            else:
                search_dirs.append(os.path.join(base, "Family Templates"))
        for d in search_dirs:
            if not os.path.isdir(d):
                continue
            for tname in template_names:
                fp = os.path.join(d, tname)
                if os.path.isfile(fp):
                    return fp
        return None

    def _find_template_by_name(self, cat_name):
        idx = next((i for i, (n, _) in enumerate(CATEGORY_TEMPLATES) if n == cat_name), 0)
        return self._find_template(idx)

    # ── Parametric reference planes ──────────────────────────────────────────

    def _find_family_views(self, fam_doc):
        plan_view = elev_view = None
        for v in FilteredElementCollector(fam_doc).OfClass(View):
            try:
                if v.IsTemplate:
                    continue
                vt = v.ViewType
                if vt == ViewType.FloorPlan and plan_view is None:
                    plan_view = v
                elif vt == ViewType.Elevation and elev_view is None:
                    try:
                        if abs(v.ViewDirection.Y) > 0.99:
                            elev_view = v
                    except Exception:
                        pass
                if plan_view and elev_view:
                    break
            except Exception:
                continue
        return plan_view, elev_view

    def _create_parametric_refs(self, fam_doc, half_w, height,
                                 plan_view, elev_view, param_width_fp, param_height_fp):
        rp_left = rp_right = rp_top = None
        if plan_view is not None:
            try:
                rp_left = fam_doc.FamilyCreate.NewReferencePlane(
                    XYZ(-half_w, -3, 0), XYZ(-half_w, 3, 0), XYZ.BasisZ, plan_view)
                rp_left.Name = "Edge_Left"
                rp_right = fam_doc.FamilyCreate.NewReferencePlane(
                    XYZ(half_w, -3, 0), XYZ(half_w, 3, 0), XYZ.BasisZ, plan_view)
                rp_right.Name = "Edge_Right"
                if param_width_fp is not None:
                    ref_arr = ReferenceArray()
                    ref_arr.Append(rp_left.GetReference())
                    ref_arr.Append(rp_right.GetReference())
                    dim_line = Line.CreateBound(
                        XYZ(-half_w * 1.5, 2, 0), XYZ(half_w * 1.5, 2, 0))
                    dim = fam_doc.FamilyCreate.NewDimension(plan_view, dim_line, ref_arr)
                    if dim:
                        dim.FamilyLabel = param_width_fp
            except Exception:
                pass
        if elev_view is not None:
            try:
                rp_top = fam_doc.FamilyCreate.NewReferencePlane(
                    XYZ(-3, 0, height), XYZ(3, 0, height), XYZ.BasisY, elev_view)
                rp_top.Name = "Top"
                if param_height_fp is not None:
                    rp_level = None
                    for rp in FilteredElementCollector(fam_doc).OfClass(ReferencePlane):
                        try:
                            n = rp.Name.lower()
                            if any(k in n for k in ("level", "floor", "bottom", "ref level")):
                                rp_level = rp
                                break
                        except Exception:
                            continue
                    if rp_level:
                        ref_arr = ReferenceArray()
                        ref_arr.Append(rp_level.GetReference())
                        ref_arr.Append(rp_top.GetReference())
                        dim_line = Line.CreateBound(XYZ(0, 0, -0.1), XYZ(0, 0, height + 0.1))
                        dim = fam_doc.FamilyCreate.NewDimension(elev_view, dim_line, ref_arr)
                        if dim:
                            dim.FamilyLabel = param_height_fp
            except Exception:
                pass
        return rp_left, rp_right, rp_top

    def _lock_faces_to_planes(self, fam_doc, solid_elem,
                               plan_view, elev_view, rp_left, rp_right, rp_top):
        try:
            geom_opt = Options()
            geom_opt.ComputeReferences = True
            geom_elem = solid_elem.get_Geometry(geom_opt)
            for geom_obj in geom_elem:
                if not isinstance(geom_obj, Solid):
                    continue
                for face in geom_obj.Faces:
                    if not isinstance(face, PlanarFace):
                        continue
                    n = face.FaceNormal
                    pairs = []
                    if rp_right and plan_view and n.X > 0.99:
                        pairs.append((rp_right, plan_view))
                    elif rp_left and plan_view and n.X < -0.99:
                        pairs.append((rp_left, plan_view))
                    elif rp_top and elev_view and n.Z > 0.99:
                        pairs.append((rp_top, elev_view))
                    for rp, view in pairs:
                        try:
                            align = fam_doc.FamilyCreate.NewAlignment(
                                view, rp.GetReference(), face.Reference)
                            if align:
                                align.IsLocked = True
                        except Exception:
                            pass
        except Exception:
            pass

    def _create_window_body(self, fam_doc, sketch_plane, half_w, half_depth, height,
                            param_height_fp, param_material):
        from Autodesk.Revit.DB import (
            FamilyElementVisibility, FamilyElementVisibilityType, BuiltInParameter,
        )
        FRAME_W = max(min(half_w * 0.12, 0.1312), 0.0492)
        half_d  = max(half_depth, 0.2461)

        def rect_loop(xmin, xmax, ymin, ymax):
            arr = CurveArray()
            pts = [XYZ(xmin, ymin, 0), XYZ(xmax, ymin, 0),
                   XYZ(xmax, ymax, 0), XYZ(xmin, ymax, 0)]
            for i in range(4):
                arr.Append(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
            return arr

        outer = rect_loop(-half_w, half_w, -half_d, half_d)
        inner = rect_loop(-(half_w - FRAME_W), (half_w - FRAME_W),
                          -(half_d - FRAME_W), (half_d - FRAME_W))
        frame_profile = CurveArrArray()
        frame_profile.Append(outer)
        frame_profile.Append(inner)
        frame_ext = fam_doc.FamilyCreate.NewExtrusion(True, frame_profile, sketch_plane, height)
        try:
            if param_height_fp:
                end_p = frame_ext.get_Parameter(BuiltInParameter.EXTRUSION_END_PARAM)
                if end_p:
                    fam_doc.FamilyManager.AssociateElementParameterToFamilyParameter(
                        end_p, param_height_fp)
            if param_material:
                mat_p = frame_ext.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM)
                if mat_p:
                    fam_doc.FamilyManager.AssociateElementParameterToFamilyParameter(
                        mat_p, param_material)
        except Exception:
            pass
        glass_ext = None
        try:
            iw    = half_w - FRAME_W
            GLASS = 0.0082
            glass_rect = rect_loop(-iw, iw, -GLASS, GLASS)
            glass_profile = CurveArrArray()
            glass_profile.Append(glass_rect)
            glass_height = max(height - FRAME_W * 2, FRAME_W)
            glass_ext = fam_doc.FamilyCreate.NewExtrusion(
                True, glass_profile, sketch_plane, glass_height)
            glass_ext.StartOffset = FRAME_W
            vis = FamilyElementVisibility(FamilyElementVisibilityType.Model)
            vis.IsShownInTopBottom = False
            glass_ext.SetVisibility(vis)
        except Exception:
            glass_ext = None
        return frame_ext, glass_ext

    # ── Single block export ──────────────────────────────────────────────────

    def _embed_dwg_into_family(self, fam_doc, import_inst, cx, cy):
        pass  # fallback not implemented in combined dialog

    def _export_block(self, block_item, template_path, output_folder,
                      discipline_name, category_name, load_to_project=False,
                      mode_2d_only=False):
        from Autodesk.Revit.DB import (
            BuiltInParameter, FamilyElementVisibility,
            FamilyElementVisibilityType, GraphicsStyleType,
        )
        curves = block_item._curves
        if not curves:
            return None
        fam_doc = None
        fam_doc = self._app.NewFamilyDocument(template_path)
        try:
            min_x, max_x, min_y, max_y = get_xy_bounds(curves)
            is_door   = "door"   in category_name.lower()
            is_window = "window" in category_name.lower()
            door_width = None
            if is_door:
                frame_xs, frame_ys = [], []
                for curve in curves:
                    if isinstance(curve, Arc):
                        try:
                            C  = curve.Center
                            p0 = curve.GetEndPoint(0)
                            p1 = curve.GetEndPoint(1)
                            frame_xs.append(C.X)
                            frame_ys.append(C.Y)
                            if abs(p0.Y - C.Y) < abs(p1.Y - C.Y):
                                frame_xs.append(p0.X); frame_ys.append(p0.Y)
                            else:
                                frame_xs.append(p1.X); frame_ys.append(p1.Y)
                        except Exception:
                            pass
                if frame_xs:
                    cx = (min(frame_xs) + max(frame_xs)) / 2.0
                    cy = (min(frame_ys) + max(frame_ys)) / 2.0
                    calc_w = max(frame_xs) - min(frame_xs)
                    if calc_w > 0.01:
                        door_width = calc_w
                else:
                    cx = (min_x + max_x) / 2.0
                    cy = (min_y + max_y) / 2.0
                    door_width = max_x - min_x
            else:
                cx = (min_x + max_x) / 2.0
                cy = (min_y + max_y) / 2.0
            half_w = max((max_x - min_x) / 2.0, 0.01)
            half_h = max((max_y - min_y) / 2.0, 0.01)

            t = Transaction(fam_doc, 'Create Block Geometry')
            start_transaction(t)
            try:
                sketch_plane = None
                for sp in FilteredElementCollector(fam_doc).OfClass(SketchPlane):
                    try:
                        if abs(sp.GetPlane().Normal.Z - 1.0) < 0.001:
                            sketch_plane = sp
                            break
                    except Exception:
                        pass
                if not sketch_plane:
                    sketch_plane = SketchPlane.Create(
                        fam_doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))

                if mode_2d_only:
                    _zvals = []
                    for _c in curves:
                        try:
                            _zvals.append(_c.GetEndPoint(0).Z)
                            _zvals.append(_c.GetEndPoint(1).Z)
                        except Exception:
                            pass
                    if _zvals:
                        _zvals.sort()
                        _mid = len(_zvals) // 2
                        cz = (_zvals[_mid] if len(_zvals) % 2 == 1
                              else (_zvals[_mid - 1] + _zvals[_mid]) / 2.0)
                    else:
                        cz = 0.0
                    translator = Transform.CreateTranslation(XYZ(-cx, -cy, -cz))

                    def _seg(pa, pb):
                        paf = XYZ(pa.X, pa.Y, 0.0)
                        pbf = XYZ(pb.X, pb.Y, 0.0)
                        if paf.DistanceTo(pbf) < 1e-4:
                            return False
                        try:
                            fam_doc.FamilyCreate.NewModelCurve(
                                Line.CreateBound(paf, pbf), sketch_plane)
                            return True
                        except Exception:
                            return False

                    ok_2d = fail_2d = 0
                    for curve in curves:
                        try:
                            new_c = curve.CreateTransformed(translator)
                            if isinstance(new_c, Line):
                                p0 = new_c.GetEndPoint(0)
                                p1 = new_c.GetEndPoint(1)
                                if _seg(p0, p1):
                                    ok_2d += 1
                                else:
                                    fail_2d += 1
                            else:
                                written = False
                                pts = new_c.Tessellate()
                                for i in range(len(pts) - 1):
                                    if _seg(pts[i], pts[i + 1]):
                                        written = True
                                if not new_c.IsBound:
                                    if _seg(pts[-1], pts[0]):
                                        written = True
                                if written:
                                    ok_2d += 1
                                else:
                                    fail_2d += 1
                        except Exception:
                            fail_2d += 1
                    logger.info("2D '{}': {} ok / {} skipped".format(
                        block_item.BlockName, ok_2d, fail_2d))
                else:
                    THICKNESS      = 0.1312
                    HEIGHT         = 7.2178
                    WINDOW_HEIGHT  = 4.9213
                    extrusion_depth = HEIGHT if is_door else (WINDOW_HEIGHT if is_window else 1.0)

                    swing_gs = frame_gs = None
                    if is_door:
                        try:
                            fam_cat = fam_doc.OwnerFamily.FamilyCategory
                            def get_or_create_subcat(name):
                                if fam_cat.SubCategories.Contains(name):
                                    return fam_cat.SubCategories.get_Item(name)
                                return fam_doc.Settings.Categories.NewSubcategory(fam_cat, name)
                            swing_subcat = get_or_create_subcat("Plan Swing")
                            frame_subcat = get_or_create_subcat("Frame/Mullion")
                            if swing_subcat:
                                swing_gs = swing_subcat.GetGraphicsStyle(GraphicsStyleType.Projection)
                            if frame_subcat:
                                frame_gs = frame_subcat.GetGraphicsStyle(GraphicsStyleType.Projection)
                        except Exception:
                            pass

                    param_height_fp = param_width_fp = param_material = None
                    try:
                        fam_mgr = fam_doc.FamilyManager
                        for param in fam_mgr.Parameters:
                            pname = param.Definition.Name.lower()
                            if pname in ("height", "chieu cao"):
                                try: fam_mgr.Set(param, extrusion_depth)
                                except Exception: pass
                                param_height_fp = param
                            elif pname in ("width", "chieu rong"):
                                if door_width:
                                    try: fam_mgr.Set(param, door_width)
                                    except Exception: pass
                                param_width_fp = param
                            elif pname in ("depth", "chieu sau", "length", "chieu dai"):
                                if not is_door and half_h * 2.0 > 0.01:
                                    try: fam_mgr.Set(param, half_h * 2.0)
                                    except Exception: pass
                            elif pname in ("material", "vat lieu"):
                                param_material = param
                    except Exception:
                        pass

                    ext_box = None
                    if is_window:
                        window_frame_ext, _ = self._create_window_body(
                            fam_doc, sketch_plane, half_w, half_h,
                            extrusion_depth, param_height_fp, param_material)
                        ext_box = window_frame_ext
                    elif not is_door:
                        c1 = XYZ(-half_w, -half_h, 0.0)
                        c2 = XYZ( half_w, -half_h, 0.0)
                        c3 = XYZ( half_w,  half_h, 0.0)
                        c4 = XYZ(-half_w,  half_h, 0.0)
                        rect = CurveArray()
                        rect.Append(Line.CreateBound(c1, c2))
                        rect.Append(Line.CreateBound(c2, c3))
                        rect.Append(Line.CreateBound(c3, c4))
                        rect.Append(Line.CreateBound(c4, c1))
                        profile = CurveArrArray()
                        profile.Append(rect)
                        ext_box = fam_doc.FamilyCreate.NewExtrusion(
                            True, profile, sketch_plane, extrusion_depth)
                        try:
                            if param_height_fp:
                                end_p = ext_box.get_Parameter(BuiltInParameter.EXTRUSION_END_PARAM)
                                if end_p:
                                    fam_doc.FamilyManager.AssociateElementParameterToFamilyParameter(
                                        end_p, param_height_fp)
                            if param_material:
                                mat_p = ext_box.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM)
                                if mat_p:
                                    fam_doc.FamilyManager.AssociateElementParameterToFamilyParameter(
                                        mat_p, param_material)
                        except Exception:
                            pass
                        top_sp = SketchPlane.Create(
                            fam_doc,
                            Plane.CreateByNormalAndOrigin(
                                XYZ.BasisZ, XYZ(0.0, 0.0, extrusion_depth)))
                        ok_3d = fail_3d = 0
                        for curve in curves:
                            projected = _dwg_project_curve(curve, cx, cy, extrusion_depth)
                            if projected is None:
                                fail_3d += 1
                                continue
                            try:
                                fam_doc.FamilyCreate.NewModelCurve(projected, top_sp)
                                ok_3d += 1
                            except Exception:
                                try:
                                    pts = curve.Tessellate()
                                    for i in range(len(pts) - 1):
                                        pa = XYZ(pts[i].X - cx,   pts[i].Y - cy,   extrusion_depth)
                                        pb = XYZ(pts[i+1].X - cx, pts[i+1].Y - cy, extrusion_depth)
                                        if pa.DistanceTo(pb) > 1e-4:
                                            fam_doc.FamilyCreate.NewModelCurve(
                                                Line.CreateBound(pa, pb), top_sp)
                                            ok_3d += 1
                                except Exception:
                                    pass
                                fail_3d += 1
                        threshold = max(5, int(len(curves) * MIN_CURVE_RATIO))
                        if ok_3d < threshold:
                            self._embed_dwg_into_family(
                                fam_doc, block_item._import_inst, cx, cy)

                    panel_ext = None
                    for curve in curves:
                        if not is_door:
                            break
                        try:
                            translator = Transform.CreateTranslation(XYZ(-cx, -cy, 0.0))
                            new_c = curve.CreateTransformed(translator)
                            if isinstance(curve, Line):
                                sym_line = fam_doc.FamilyCreate.NewSymbolicCurve(new_c, sketch_plane)
                                if frame_gs:
                                    sym_line.Subcategory = frame_gs
                            elif isinstance(curve, Arc):
                                sym_arc = fam_doc.FamilyCreate.NewSymbolicCurve(new_c, sketch_plane)
                                if swing_gs:
                                    sym_arc.Subcategory = swing_gs
                                ctr  = curve.Center
                                nc   = ctr + XYZ(-cx, -cy, 0.0)
                                p0_orig = curve.GetEndPoint(0)
                                p1_orig = curve.GetEndPoint(1)
                                p_closed = (p0_orig if abs(p0_orig.Y - ctr.Y) < abs(p1_orig.Y - ctr.Y)
                                            else p1_orig)
                                np_closed = p_closed + XYZ(-cx, -cy, 0.0)
                                v_dir   = (np_closed - nc).Normalize()
                                v_ortho = XYZ(-v_dir.Y, v_dir.X, 0.0)
                                half_t  = THICKNESS / 2.0
                                pt1 = nc + v_ortho * half_t
                                pt2 = nc - v_ortho * half_t
                                pt3 = pt2 + v_dir * curve.Radius
                                pt4 = pt1 + v_dir * curve.Radius
                                p_rect = CurveArray()
                                p_rect.Append(Line.CreateBound(pt1, pt2))
                                p_rect.Append(Line.CreateBound(pt2, pt3))
                                p_rect.Append(Line.CreateBound(pt3, pt4))
                                p_rect.Append(Line.CreateBound(pt4, pt1))
                                p_profile = CurveArrArray()
                                p_profile.Append(p_rect)
                                panel_ext = fam_doc.FamilyCreate.NewExtrusion(
                                    True, p_profile, sketch_plane, HEIGHT)
                                try:
                                    vis = FamilyElementVisibility(
                                        FamilyElementVisibilityType.Model)
                                    vis.IsShownInTopBottom = False
                                    panel_ext.SetVisibility(vis)
                                    if param_height_fp:
                                        end_p = panel_ext.get_Parameter(
                                            BuiltInParameter.EXTRUSION_END_PARAM)
                                        if end_p:
                                            fam_doc.FamilyManager\
                                                .AssociateElementParameterToFamilyParameter(
                                                    end_p, param_height_fp)
                                    if param_material:
                                        mat_p = panel_ext.get_Parameter(
                                            BuiltInParameter.MATERIAL_ID_PARAM)
                                        if mat_p:
                                            fam_doc.FamilyManager\
                                                .AssociateElementParameterToFamilyParameter(
                                                    mat_p, param_material)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    if is_door or is_window:
                        fam_doc.Regenerate()
                        plan_view, elev_view = self._find_family_views(fam_doc)
                        rp_left, rp_right, rp_top = self._create_parametric_refs(
                            fam_doc,
                            half_w if not is_door else (door_width / 2.0 if door_width else half_w),
                            HEIGHT if is_door else extrusion_depth,
                            plan_view, elev_view, param_width_fp, param_height_fp)
                        fam_doc.Regenerate()
                        target_solid = panel_ext if is_door else ext_box
                        if target_solid and (rp_left or rp_right or rp_top):
                            self._lock_faces_to_planes(
                                fam_doc, target_solid,
                                plan_view, elev_view, rp_left, rp_right, rp_top)

                t.Commit()
            except Exception:
                try:
                    t.RollBack()
                except Exception:
                    pass
                raise

            safe_cad_name = block_item.BlockName.strip() or "Family"
            w_str = getattr(block_item, 'WidthMM', '-')
            d_str = getattr(block_item, 'DepthMM', '-')
            dim_suffix = "_{}x{}".format(w_str, d_str) if (w_str != '-' and d_str != '-') else ""
            base_name = "T3Lab_{}_{}{}".format(
                category_name.replace(" ", "_"),
                safe_cad_name.replace(" ", "_"),
                dim_suffix)
            base_name = re.sub(r'[\\/*?:"<>|]', "", base_name)
            save_path = os.path.join(output_folder, "{}.rfa".format(base_name))
            ctr = 1
            while os.path.exists(save_path):
                save_path = os.path.join(output_folder, "{}_{}.rfa".format(base_name, ctr))
                ctr += 1
            opts = SaveAsOptions()
            opts.OverwriteExistingFile = True
            fam_doc.SaveAs(save_path, opts)
            logger.info("Exported: {}".format(save_path))
            if load_to_project:
                try:
                    t_load = Transaction(self._doc, 'Load Family - {}'.format(safe_cad_name))
                    start_transaction(t_load)
                    try:
                        self._doc.LoadFamily(save_path)
                        t_load.Commit()
                    except Exception:
                        try: t_load.RollBack()
                        except Exception: pass
                        logger.warning("Could not load: {}".format(save_path))
                except Exception:
                    pass
            return save_path
        finally:
            if fam_doc is not None:
                try:
                    fam_doc.Close(False)
                except Exception:
                    pass

    # ── Preset generators ────────────────────────────────────────────────────

    def presets_clicked(self, sender, e):
        output_folder = self.output_path.Text
        if not output_folder or not os.path.isdir(output_folder):
            forms.alert("Please select a valid output folder (Browse...) first.")
            return
        cat_name = self.category_combo.SelectedItem
        if cat_name is None:
            forms.alert("Please select a category first.")
            return
        if cat_name not in CATEGORY_PRESETS:
            forms.alert("No presets for: {}".format(cat_name))
            return
        mode, preset_list = CATEGORY_PRESETS[cat_name]
        labels = [p[0] for p in preset_list]
        selected_labels = forms.SelectFromList.show(
            labels,
            title="T3Lab - {} Presets".format(cat_name),
            multiselect=True, button_name="Generate")
        if not selected_labels:
            return
        selected = [p for p in preset_list if p[0] in selected_labels]
        load_to_project = (self.chk_load_to_project.IsChecked == True)
        skip_existing   = (self.batch_skip_existing.IsChecked == True)
        cat_idx = next((i for i, (n, _) in enumerate(CATEGORY_TEMPLATES) if n == cat_name), 0)
        template_path = self._find_template(cat_idx)
        if not template_path:
            forms.alert("Template (.rft) for '{}' not found.".format(cat_name))
            return
        ok_count = fail_count = skipped = 0
        self._cancel_requested = False
        self._pause_requested  = False
        self._update_progress(0, len(selected))
        for i, preset in enumerate(selected):
            if self._cancel_requested:
                break
            self._update_status("[{}/{}] Generating: {}".format(i + 1, len(selected), preset[0]))
            self._update_progress(i, len(selected))
            if skip_existing:
                safe  = re.sub(r'[/*?:"<>|]', "_", preset[0])
                prefix = re.sub(r'\s+', '_', cat_name)
                check = os.path.join(output_folder, "T3Lab_{}_{}.rfa".format(prefix, safe))
                if os.path.exists(check):
                    skipped += 1
                    self._update_progress(i + 1, len(selected))
                    continue
            try:
                if mode == "door":
                    success = self._generate_door_from_preset(
                        preset, template_path, output_folder, load_to_project)
                elif mode == "window":
                    success = self._generate_window_from_preset(
                        preset, template_path, output_folder, load_to_project)
                else:
                    success = self._generate_generic_from_preset(
                        preset, template_path, output_folder, cat_name, load_to_project)
                if success:
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception:
                logger.error("Preset error: {}\n{}".format(preset[0], traceback.format_exc()))
                fail_count += 1
            self._update_progress(i + 1, len(selected))
        was_cancelled = self._cancel_requested
        status = "Stopped" if was_cancelled else "Done"
        self._update_status("{}: {} ok, {} skipped, {} failed".format(status, ok_count, skipped, fail_count))
        self._hide_progress()
        forms.alert("{} Preset Export\n\nGenerated: {}\nSkipped: {}\nFailed: {}\n\nFolder: {}".format(
            cat_name, ok_count, skipped, fail_count, output_folder))

    def _generate_window_from_preset(self, preset, template_path, output_folder, load_to_project):
        label, width_mm, height_mm = preset
        half_w = (width_mm / 2.0) * SCL
        height = height_mm * SCL
        half_d = 0.3937
        fam_doc = self._app.NewFamilyDocument(template_path)
        t = Transaction(fam_doc, "T3Lab Window - " + label)
        start_transaction(t)
        try:
            sketch_plane = None
            for sp in FilteredElementCollector(fam_doc).OfClass(SketchPlane):
                try:
                    if abs(sp.GetPlane().Normal.Z - 1.0) < 0.001:
                        sketch_plane = sp
                        break
                except Exception:
                    pass
            if not sketch_plane:
                sketch_plane = SketchPlane.Create(
                    fam_doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))
            param_width_fp = param_height_fp = param_material = None
            try:
                fm = fam_doc.FamilyManager
                for p in fm.Parameters:
                    pn = p.Definition.Name.lower()
                    if pn == "height":
                        try: fm.Set(p, height)
                        except Exception: pass
                        param_height_fp = p
                    elif pn == "width":
                        try: fm.Set(p, half_w * 2.0)
                        except Exception: pass
                        param_width_fp = p
                    elif "material" in pn:
                        param_material = p
            except Exception:
                pass
            self._create_window_body(fam_doc, sketch_plane, half_w, half_d, height,
                                     param_height_fp, param_material)
            fam_doc.Regenerate()
            plan_view, elev_view = self._find_family_views(fam_doc)
            self._create_parametric_refs(fam_doc, half_w, height, plan_view, elev_view,
                                         param_width_fp, param_height_fp)
            t.Commit()
        except Exception:
            try: t.RollBack()
            except Exception: pass
            fam_doc.Close(False)
            raise
        safe = re.sub(r'[/*?:"<>|]', "_", label)
        save_path = os.path.join(output_folder, "T3Lab_Window_{}.rfa".format(safe))
        ctr = 1
        while os.path.exists(save_path):
            save_path = os.path.join(output_folder, "T3Lab_Window_{}_{}.rfa".format(safe, ctr))
            ctr += 1
        try:
            opts = SaveAsOptions()
            opts.OverwriteExistingFile = True
            fam_doc.SaveAs(save_path, opts)
        finally:
            fam_doc.Close(False)
        logger.info("Saved: " + save_path)
        if load_to_project:
            try:
                t2 = Transaction(self._doc, "Load " + label)
                start_transaction(t2)
                try: self._doc.LoadFamily(save_path); t2.Commit()
                except Exception:
                    try: t2.RollBack()
                    except Exception: pass
            except Exception:
                pass
        return True

    def _generate_generic_from_preset(self, preset, template_path, output_folder,
                                       category_name, load_to_project):
        label, w_mm, d_mm, h_mm = preset
        w = w_mm * SCL
        d = d_mm * SCL
        h = h_mm * SCL
        fam_doc = self._app.NewFamilyDocument(template_path)
        t = Transaction(fam_doc, "T3Lab {} - {}".format(category_name, label))
        start_transaction(t)
        try:
            sketch_plane = None
            for sp in FilteredElementCollector(fam_doc).OfClass(SketchPlane):
                try:
                    if abs(sp.GetPlane().Normal.Z - 1.0) < 0.001:
                        sketch_plane = sp
                        break
                except Exception:
                    pass
            if not sketch_plane:
                sketch_plane = SketchPlane.Create(
                    fam_doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))
            try:
                fm = fam_doc.FamilyManager
                for p in fm.Parameters:
                    pn = p.Definition.Name.lower()
                    if pn == "width":
                        try: fm.Set(p, w)
                        except Exception: pass
                    elif pn in ("depth", "length"):
                        try: fm.Set(p, d)
                        except Exception: pass
                    elif pn == "height":
                        try: fm.Set(p, h)
                        except Exception: pass
            except Exception:
                pass
            half_w = w / 2.0
            half_d = d / 2.0
            arr = CurveArray()
            pts = [XYZ(-half_w, -half_d, 0), XYZ(half_w, -half_d, 0),
                   XYZ(half_w, half_d, 0), XYZ(-half_w, half_d, 0)]
            for i in range(4):
                arr.Append(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
            prof = CurveArrArray()
            prof.Append(arr)
            fam_doc.FamilyCreate.NewExtrusion(True, prof, sketch_plane, h)
            t.Commit()
        except Exception:
            try: t.RollBack()
            except Exception: pass
            fam_doc.Close(False)
            raise
        cat_prefix = re.sub(r'\s+', '_', category_name)
        safe = re.sub(r'[/*?:"<>|]', "_", label)
        save_path = os.path.join(output_folder, "T3Lab_{}_{}.rfa".format(cat_prefix, safe))
        ctr = 1
        while os.path.exists(save_path):
            save_path = os.path.join(
                output_folder, "T3Lab_{}_{}_{}.rfa".format(cat_prefix, safe, ctr))
            ctr += 1
        try:
            opts = SaveAsOptions()
            opts.OverwriteExistingFile = True
            fam_doc.SaveAs(save_path, opts)
        finally:
            fam_doc.Close(False)
        logger.info("Saved: " + save_path)
        if load_to_project:
            try:
                t2 = Transaction(self._doc, "Load " + label)
                start_transaction(t2)
                try: self._doc.LoadFamily(save_path); t2.Commit()
                except Exception:
                    try: t2.RollBack()
                    except Exception: pass
            except Exception:
                pass
        return True

    def _generate_door_from_preset(self, preset, template_path, output_folder, load_to_project):
        from Autodesk.Revit.DB import BuiltInParameter, GraphicsStyleType
        label, width_mm, height_mm, frame_w_mm, proj_ext_mm, proj_int_mm, leaf_t_mm, door_count = preset
        half_w   = (width_mm / 2.0) * SCL
        h        = height_mm * SCL
        fw       = frame_w_mm * SCL
        fpe      = proj_ext_mm * SCL
        fpi      = proj_int_mm * SCL
        dt       = leaf_t_mm * SCL
        half_fw  = half_w + fw
        total_fh = h + fw
        fam_doc = self._app.NewFamilyDocument(template_path)
        t = Transaction(fam_doc, "T3Lab Door - " + label)
        start_transaction(t)
        try:
            plan_sp = elev_sp = None
            for sp in FilteredElementCollector(fam_doc).OfClass(SketchPlane):
                n, org = sp.GetPlane().Normal, sp.GetPlane().Origin
                if abs(n.Z - 1.0) < 0.001 and abs(org.X) < 0.01 and abs(org.Y) < 0.01 and plan_sp is None:
                    plan_sp = sp
                if abs(n.Y + 1.0) < 0.001 and abs(org.X) < 0.01 and abs(org.Y) < 0.01 and elev_sp is None:
                    elev_sp = sp
            if plan_sp is None:
                plan_sp = SketchPlane.Create(
                    fam_doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))
            if elev_sp is None:
                elev_sp = SketchPlane.Create(
                    fam_doc, Plane.CreateByNormalAndOrigin(XYZ(0.0, -1.0, 0.0), XYZ.Zero))
            param_width_fp = param_height_fp = None
            try:
                fm = fam_doc.FamilyManager
                for p in fm.Parameters:
                    pn = p.Definition.Name.lower()
                    if pn == "height":
                        try: fm.Set(p, h)
                        except Exception: pass
                        param_height_fp = p
                    elif pn == "width":
                        try: fm.Set(p, half_w * 2.0)
                        except Exception: pass
                        param_width_fp = p
                    elif pn == "frame width":
                        try: fm.Set(p, fw)
                        except Exception: pass
                    elif pn in ("frame projection ext.", "frame projection ext"):
                        try: fm.Set(p, fpe)
                        except Exception: pass
                    elif pn in ("frame projection int.", "frame projection int"):
                        try: fm.Set(p, fpi)
                        except Exception: pass
            except Exception:
                pass
            frame_gs = leaf_gs = None
            try:
                fam_cat = fam_doc.OwnerFamily.FamilyCategory
                def _sc(name):
                    return (fam_cat.SubCategories.get_Item(name)
                            if fam_cat.SubCategories.Contains(name)
                            else fam_doc.Settings.Categories.NewSubcategory(fam_cat, name))
                sc_f = _sc("Frame/Mullion")
                sc_p = _sc("Panel")
                if sc_f: frame_gs = sc_f.GetGraphicsStyle(GraphicsStyleType.Projection)
                if sc_p: leaf_gs  = sc_p.GetGraphicsStyle(GraphicsStyleType.Projection)
            except Exception:
                pass

            def _extrude_xz(x0, x1, z0, z1, depth, gs=None):
                arr = CurveArray()
                arr.Append(Line.CreateBound(XYZ(x0, 0, z0), XYZ(x1, 0, z0)))
                arr.Append(Line.CreateBound(XYZ(x1, 0, z0), XYZ(x1, 0, z1)))
                arr.Append(Line.CreateBound(XYZ(x1, 0, z1), XYZ(x0, 0, z1)))
                arr.Append(Line.CreateBound(XYZ(x0, 0, z1), XYZ(x0, 0, z0)))
                prof = CurveArrArray()
                prof.Append(arr)
                ext = fam_doc.FamilyCreate.NewExtrusion(True, prof, elev_sp, depth)
                if gs:
                    try: ext.Subcategory = gs
                    except Exception: pass
                return ext

            def _extrude_xy_leaf(x0, x1, y0, y1):
                arr = CurveArray()
                pts = [XYZ(x0, y0, 0), XYZ(x1, y0, 0), XYZ(x1, y1, 0), XYZ(x0, y1, 0)]
                for i in range(4): arr.Append(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
                prof = CurveArrArray()
                prof.Append(arr)
                ext = fam_doc.FamilyCreate.NewExtrusion(True, prof, plan_sp, h)
                if leaf_gs:
                    try: ext.Subcategory = leaf_gs
                    except Exception: pass
                try:
                    if param_height_fp:
                        ep = ext.get_Parameter(BuiltInParameter.EXTRUSION_END_PARAM)
                        if ep: fam_doc.FamilyManager.AssociateElementParameterToFamilyParameter(
                            ep, param_height_fp)
                except Exception:
                    pass
                return ext

            frame_pieces = [
                (-half_fw, -half_w, 0.0, total_fh),
                ( half_w,  half_fw, 0.0, total_fh),
                (-half_fw,  half_fw, h, total_fh),
            ]
            for x0, x1, z0, z1 in frame_pieces:
                _extrude_xz(x0, x1, z0, z1,  fpe, frame_gs)
                _extrude_xz(x0, x1, z0, z1, -fpi, frame_gs)
            GAP = 0.00328
            if door_count == 1:
                _extrude_xy_leaf(-half_w, half_w, 0.0, dt)
            else:
                _extrude_xy_leaf(-half_w, -GAP / 2.0, 0.0, dt)
                _extrude_xy_leaf( GAP / 2.0, half_w,  0.0, dt)
            fam_doc.Regenerate()
            plan_view, elev_view = self._find_family_views(fam_doc)
            self._create_parametric_refs(fam_doc, half_w, h, plan_view, elev_view,
                                         param_width_fp, param_height_fp)
            t.Commit()
        except Exception:
            try: t.RollBack()
            except Exception: pass
            fam_doc.Close(False)
            raise
        safe = re.sub(r'[/*?:"<>|]', "_", label)
        save_path = os.path.join(output_folder, "T3Lab_Door_{}.rfa".format(safe))
        ctr = 1
        while os.path.exists(save_path):
            save_path = os.path.join(output_folder, "T3Lab_Door_{}_{}.rfa".format(safe, ctr))
            ctr += 1
        try:
            opts = SaveAsOptions()
            opts.OverwriteExistingFile = True
            fam_doc.SaveAs(save_path, opts)
        finally:
            fam_doc.Close(False)
        logger.info("Saved: " + save_path)
        if load_to_project:
            try:
                t2 = Transaction(self._doc, "Load " + label)
                start_transaction(t2)
                try: self._doc.LoadFamily(save_path); t2.Commit()
                except Exception:
                    try: t2.RollBack()
                    except Exception: pass
            except Exception:
                pass
        return True

    # ── Place & batch load ────────────────────────────────────────────────────

    def _place_family_instances(self, rfa_path, block_item, level):
        from Autodesk.Revit.DB import ElementTransformUtils, Family, Line as DBLine
        from Autodesk.Revit.DB.Structure import StructuralType
        if not block_item._placements:
            return 0
        family = None
        try:
            loaded_ref = clr.Reference[Family]()
            if self._doc.LoadFamily(rfa_path, loaded_ref):
                family = loaded_ref.Value
        except Exception:
            pass
        if not family:
            stem = os.path.splitext(os.path.basename(rfa_path))[0]
            for f in FilteredElementCollector(self._doc).OfClass(Family):
                if f.Name == stem:
                    family = f
                    break
        if not family:
            logger.warning("Could not load family: {}".format(rfa_path))
            return 0
        symbol = None
        for sid in family.GetFamilySymbolIds():
            symbol = self._doc.GetElement(sid)
            break
        if not symbol:
            return 0
        if not symbol.IsActive:
            symbol.Activate()
            self._doc.Regenerate()
        placed = 0
        for (centroid, angle) in block_item._placements:
            try:
                z  = level.Elevation if level else centroid.Z
                pt = XYZ(centroid.X, centroid.Y, z)
                inst = self._doc.Create.NewFamilyInstance(
                    pt, symbol, level, StructuralType.NonStructural)
                if inst and abs(angle) > 0.001:
                    axis = DBLine.CreateBound(pt, XYZ(pt.X, pt.Y, pt.Z + 1.0))
                    ElementTransformUtils.RotateElement(self._doc, inst.Id, axis, angle)
                placed += 1
            except Exception:
                logger.warning("Could not place '{}': {}".format(
                    block_item.BlockName, traceback.format_exc()))
        return placed

    def _batch_load_families(self, save_paths):
        loaded = 0
        total  = len(save_paths)
        for i, path in enumerate(save_paths):
            self._update_status("Loading [{}/{}]: {}".format(
                i + 1, total, os.path.basename(path)))
            try:
                t = Transaction(self._doc, "T3Lab - Load {}".format(
                    os.path.splitext(os.path.basename(path))[0]))
                start_transaction(t)
                try:
                    self._doc.LoadFamily(path)
                    t.Commit()
                    loaded += 1
                except Exception:
                    try: t.RollBack()
                    except Exception: pass
                    logger.warning("Could not load: {}".format(path))
            except Exception:
                logger.warning("Transaction failed: {}".format(path))
        return loaded

    # ── JSON mode ────────────────────────────────────────────────────────────

    @staticmethod
    def _category_slug(cat_name):
        """'Plumbing Fixture' -> 'plumbing_fixture' (overlay filename stem)."""
        return family_guidance.category_slug(cat_name)

    def _overlay_path(self, cat_name):
        return family_guidance.overlay_path(cat_name)

    def copy_prompt_clicked(self, sender, e):
        # External models receive the same authoritative contract as AI Mode.
        cat = None
        try:
            cat = self.json_category_combo.SelectedItem
        except Exception:
            cat = None

        ppath = self._overlay_path(cat)
        if not cat:
            forms.alert("Select a family category first.", title="Family Category")
            return
        try:
            overlay = ""
            if ppath and os.path.isfile(ppath):
                with codecs.open(ppath, 'r', 'utf-8') as f:
                    overlay = f.read()
            text = build_system_prompt(str(cat), overlay)
            description = (self.ai_prompt_tb.Text or "").strip()
            if description:
                text += "\nFamily description:\n" + description
        except Exception as ex:
            forms.alert("Could not read prompt: {}".format(ex))
            return

        try:
            Clipboard.SetText(text)
            self.lbl_status.Text = "Prompt copied: '{}'.".format(cat)
        except Exception as ex:
            forms.alert("Could not copy prompt: {}".format(ex))

    def _set_ai_generation_busy(self, busy):
        """Restore each control's prior enabled state after an AI request."""
        self._ai_generating = busy
        if busy:
            self._ai_control_states = []
            for name in ('btn_ai_generate', 'ai_prompt_tb', 'json_category_combo',
                         'json_tb', 'create_btn', 'btn_ai_undo', 'copy_prompt_btn',
                         'mode_cad', 'mode_json', 'chk_json_load'):
                control = getattr(self, name, None)
                if control is not None:
                    self._ai_control_states.append((control, control.IsEnabled))
                    control.IsEnabled = False
        else:
            states, self._ai_control_states = self._ai_control_states, []
            for control, enabled in states:
                try:
                    control.IsEnabled = enabled
                except Exception as ex:
                    logger.warning("Could not restore an AI control: {}".format(ex))

    def ai_generate_clicked(self, sender, e):
        """Generate a checked definition without replacing a draft on failure."""
        if self._ai_generating or self._ai_closed:
            return
        user_prompt = (self.ai_prompt_tb.Text or "").strip()
        if not user_prompt:
            forms.alert("Describe the family, including dimensions in mm.",
                        title="AI Prompt Required")
            return
        category = self.json_category_combo.SelectedItem
        if category is None:
            forms.alert("Select a family category first.", title="Family Category")
            return
        category = str(category)
        if not self.ai_require():
            return

        # Capture all inputs on the UI thread. The worker never reads controls.
        bridge = self.ai_bridge
        previous_json = self.json_tb.Text or ""
        instructions = ""
        ppath = self._overlay_path(category)
        if ppath and os.path.isfile(ppath):
            try:
                with codecs.open(ppath, 'r', 'utf-8') as stream:
                    instructions = stream.read()
            except Exception as ex:
                forms.alert("Could not read category guidelines:\n{}".format(ex),
                            title="AI Generation")
                return

        self._ai_request_id += 1
        request_id = self._ai_request_id

        def _bg_task():
            return generate_family_schema(bridge, user_prompt, category, instructions)

        def _on_done(schema):
            if self._ai_closed or request_id != self._ai_request_id:
                return
            try:
                errors = validate_ai_schema(schema, category)
                if errors:
                    raise ValueError("\n".join(errors))
                if (self.json_tb.Text or "") != previous_json:
                    self.lbl_status.Text = "Draft changed during generation. It was preserved; generate again to replace it."
                    return
                formatted = json.dumps(schema, indent=2, ensure_ascii=False, allow_nan=False)
                self._prev_json_backup = previous_json
                self._request_preview(fit=True)
                self.json_tb.Text = formatted
                self.btn_ai_undo.Visibility = WinVis.Visible
                self.lbl_status.Text = (
                    "JSON checked: {} part(s), {} material(s), {}. Review the model on the "
                    "right, then Create Family. Revit checks the geometry during creation."
                ).format(len(schema['geometry']), len(schema.get('materials') or []), category)
            except Exception as ex:
                self.lbl_status.Text = "AI JSON could not be used. Your previous draft is unchanged."
                forms.alert("AI JSON validation failed:\n{}".format(ex), title="AI Generation")
            finally:
                self._set_ai_generation_busy(False)

        def _on_err(error):
            if self._ai_closed or request_id != self._ai_request_id:
                return
            self._set_ai_generation_busy(False)
            self.lbl_status.Text = "AI generation failed. Your previous draft is unchanged."
            forms.alert("AI generation failed:\n{}".format(error), title="AI Generation")

        try:
            self._set_ai_generation_busy(True)
            self.lbl_status.Text = "Generating and checking family JSON. Your current draft is preserved."
            self.run_ai_async(_bg_task, _on_done, _on_err)
        except Exception as ex:
            _on_err(ex)

    def ai_undo_clicked(self, sender, e):
        """Revert to previous JSON content before AI generation."""
        if self._ai_generating:
            return
        try:
            if self._prev_json_backup is not None:
                j_tb = getattr(self, 'json_tb', None) or self.FindName('json_tb')
                if j_tb:
                    j_tb.Text = self._prev_json_backup
                self._prev_json_backup = None
                u_btn = getattr(self, 'btn_ai_undo', None) or self.FindName('btn_ai_undo')
                if u_btn:
                    u_btn.Visibility = WinVis.Collapsed
                l_el = getattr(self, 'lbl_status', None) or self.FindName('lbl_status')
                if l_el:
                    l_el.Text = "Reverted to previous JSON content."
        except Exception as ex:
            logger.warning("Error reverting JSON: {}".format(ex))

    # ── Create Family (shared builder) ───────────────────────────────────────

    def create_clicked(self, sender, e):
        """Validate the reviewed JSON, then build it with FamilyGen.builder.

        Every check that can fail runs here, on the UI side, before any
        document access - so a bad draft never opens a family document. The
        Revit work itself runs in `_create_family_impl`, through
        `_run_in_revit` (direct when modal, ExternalEvent when modeless).
        """
        if self._ai_generating:
            return
        raw = self.json_tb.Text
        if not raw or raw.strip() in ("", "Paste your JSON schema here..."):
            forms.alert("Paste or generate a family JSON first, review it in the preview, "
                        "then press Create Family.", title="Create Family")
            return
        try:
            schema = json.loads(raw)
        except ValueError as ex:
            forms.alert("The family JSON cannot be read:\n\n{}\n\nFix the JSON on the left "
                        "and try again.".format(ex), title="JSON Error")
            return
        if (not isinstance(schema, dict)
                or not isinstance(schema.get("geometry"), list)
                or not schema["geometry"]
                or any(not isinstance(part, dict) for part in schema["geometry"])):
            forms.alert("Use a JSON object with a nonempty 'geometry' array of part objects.\n"
                        "Generate a new definition or fix the JSON before creating a family.",
                        title="JSON Error")
            return
        errors, _warnings = validate_family_schema(schema)
        if errors:
            shown = errors[:8]
            more = "\n... and {} more".format(len(errors) - 8) if len(errors) > 8 else ""
            self.lbl_status.Text = "{} problem(s) in the JSON. Nothing was created.".format(len(errors))
            forms.alert("The family JSON has {} problem(s), so nothing was created:\n\n{}{}\n\n"
                        "Fix the JSON, or run AI Generate again.".format(
                            len(errors), "\n".join(shown), more),
                        title="JSON Error")
            return

        # A modeless window (opened by MCP) always creates a new .rfa; only the
        # modal ribbon window may add geometry to an open family document.
        into_active = (not self._modeless) and self._doc.IsFamilyDocument
        output_folder = None
        if not into_active:
            output_folder = self.output_path.Text
            if not output_folder or not os.path.isdir(output_folder):
                output_folder = forms.pick_folder(title="Choose the folder to save the family in")
            if not output_folder:
                self.lbl_status.Text = "No output folder selected. Nothing was created."
                return
            try:
                self.output_path.Text = output_folder
            except Exception:
                pass
        load = bool(getattr(self.chk_json_load, 'IsChecked', False))
        self.lbl_status.Text = "Creating '{}'...".format(schema.get("family_name"))
        self._run_in_revit(lambda uiapp: self._create_family_impl(
            schema, output_folder, load, into_active, uiapp))

    def _create_family_impl(self, schema, output_folder, load, into_active, uiapp=None):
        """Runs in Revit API context. One Transaction in the family document."""
        doc, app = self._doc, self._app
        if uiapp is not None:
            try:
                active = uiapp.ActiveUIDocument
                if active is not None:
                    doc = active.Document
                app = uiapp.Application
            except Exception:
                pass
        name = schema.get("family_name")
        try:
            if into_active:
                report = family_builder.build_into_document(doc, schema)
            else:
                report = family_builder.create_family(
                    app, schema, output_folder, project_doc=doc,
                    load_into_project=load and not doc.IsFamilyDocument)
        except family_builder.FamilyBuildError as ex:
            self.lbl_status.Text = "'{}' was not created. See the message for what to fix.".format(name)
            forms.alert(str(ex), title="Create Family")
            return
        except Exception as ex:
            logger.warning("FamiGen create: {}".format(traceback.format_exc()))
            self.lbl_status.Text = "'{}' was not created: Revit reported an error.".format(name)
            forms.alert("Revit could not create '{}':\n{}\n\nCheck the parts listed in the "
                        "preview warnings, simplify the failing part and try again."
                        .format(name, ex), title="Create Family")
            return
        where = os.path.basename(report.get('saved_path') or '') or 'the open family'
        self.lbl_status.Text = "{}: built {} of {} part(s), {} material(s){}.".format(
            where, report['built'], report['total'],
            len(report['materials_created']) + len(report['materials_reused']),
            ", loaded into the project" if report.get('loaded') else "")
        problems = report['skipped'] or report['warnings']
        forms.alert("\n".join(family_builder.report_lines(report)),
                    title="Family Created (with warnings)" if problems else "Family Created")

    # ── Modeless plumbing (window opened by the MCP server) ─────────────────

    def _run_in_revit(self, action):
        """Run `action(uiapp)` where the Revit API is usable.

        Modal ribbon window: we are inside the command, call it directly.
        Modeless (MCP) window: queue it on an ExternalEvent; `Raise()` is
        asynchronous, so everything depending on the result lives in `action`.
        """
        if not self._modeless or self._action_event is None:
            action(None)
            return
        self._action_handler.set_action(action)
        self._action_event.Raise()

    # ── Review pane (3D preview) ─────────────────────────────────────────────

    def _init_preview(self):
        self._preview_yaw = preview_mesh.DEFAULT_YAW
        self._preview_pitch = preview_mesh.DEFAULT_PITCH
        self._preview_zoom = 1.0
        self._preview_model = None
        self._preview_drag = None
        self._preview_fit_next = True
        self._preview_camera = None
        try:
            self._preview_timer = DispatcherTimer()
            self._preview_timer.Interval = TimeSpan.FromMilliseconds(_PREVIEW_DEBOUNCE_MS)
            self._preview_timer.Tick += self._on_preview_timer
        except Exception:
            self._preview_timer = None
        try:
            camera = PerspectiveCamera()
            camera.FieldOfView = preview_mesh.FIELD_OF_VIEW
            camera.NearPlaneDistance = 0.001
            camera.FarPlaneDistance = 10000.0
            self.preview_viewport.Camera = camera
            self._preview_camera = camera
        except Exception:
            logger.warning("preview camera: {}".format(traceback.format_exc()))

    def json_text_changed(self, sender, e):
        """Debounce: rebuild the preview once typing pauses."""
        self._request_preview()

    def _request_preview(self, fit=False):
        if fit:
            self._preview_fit_next = True
        timer = getattr(self, '_preview_timer', None)
        if timer is None:
            self._refresh_preview()
            return
        timer.Stop()
        timer.Start()

    def _on_preview_timer(self, sender, e):
        try:
            self._preview_timer.Stop()
        except Exception:
            pass
        self._refresh_preview()

    def _res_brush(self, key):
        """A T3 token brush (rule 21: dot-notation, never raises)."""
        try:
            return self.FindResource(key)
        except Exception:
            return None

    def _token_color(self, key):
        brush = self._res_brush(key)
        try:
            return brush.Color
        except Exception:
            return None

    def _refresh_preview(self):
        raw = (self.json_tb.Text or "").strip()
        if not raw or raw == "Paste your JSON schema here...":
            self._render_preview(None, None, [], [])
            return
        try:
            schema = json.loads(raw)
        except ValueError as ex:
            self._show_preview_issues(
                ["The JSON cannot be read yet ({}). The preview keeps the last valid model."
                 .format(ex)])
            return
        if not isinstance(schema, dict):
            self._render_preview(None, None, ["$: must be a JSON object with a geometry array"], [])
            return
        errors, warnings = validate_family_schema(schema)
        try:
            model = preview_mesh.build_preview(schema)
        except Exception as ex:
            logger.warning("preview build: {}".format(traceback.format_exc()))
            model = None
            warnings = list(warnings) + ["Preview failed: {}".format(ex)]
        self._render_preview(schema, model, errors, warnings + (model.warnings if model else []))

    def _material_brush(self, rgb, alpha):
        color = MediaColor.FromArgb(int(alpha), int(rgb[0]), int(rgb[1]), int(rgb[2]))
        brush = SolidColorBrush(color)
        brush.Freeze()
        return brush

    def _render_preview(self, schema, model, errors, warnings):
        """Scene, materials legend and summary for one schema (None clears)."""
        self._preview_model = model
        group = Model3DGroup()
        ambient = self._token_color('T3.TextMuted')
        key = self._token_color('T3.Border')
        if ambient is not None:
            group.Children.Add(AmbientLight(ambient))
        # Lights take their colours from T3 tokens (no hex in code): a key light
        # from the front-right, a dimmer fill from behind, and the ambient.
        fill = self._token_color('T3.TextDisabled')
        if key is not None:
            group.Children.Add(DirectionalLight(key, Vector3D(-0.45, 0.6, -0.65)))
        if fill is not None:
            group.Children.Add(DirectionalLight(fill, Vector3D(0.6, -0.35, 0.4)))
        materials = {}
        for mat in (schema or {}).get('materials') or []:
            if isinstance(mat, dict) and isinstance(mat.get('name'), str):
                rgb = parse_color(mat.get('color'))
                if rgb is not None:
                    materials[mat['name']] = (rgb, mat.get('transparency') or 0)
        # Parts without a material use the T3.BorderStrong token (stock grey only
        # if the stylesheet were missing); voids use the danger accent, translucent.
        fallback = self._token_color('T3.BorderStrong')
        if fallback is None:
            fallback = Colors.Gray
        fallback_rgb = (fallback.R, fallback.G, fallback.B)
        void_color = self._token_color('T3.Danger.Accent')
        void_rgb = (void_color.R, void_color.G, void_color.B) if void_color is not None else fallback_rgb
        opaque, translucent = [], []
        if model is not None:
            for mesh in model.meshes:
                if not mesh.triangles:
                    continue
                if not mesh.is_solid:
                    rgb, alpha = void_rgb, 70
                elif mesh.material in materials:
                    rgb, transparency = materials[mesh.material]
                    try:
                        alpha = max(60, 255 - int(round(2.55 * float(transparency))))
                    except (TypeError, ValueError):
                        alpha = 255
                else:
                    rgb, alpha = fallback_rgb, 255
                (opaque if alpha >= 255 else translucent).append((mesh, rgb, alpha))
            # WPF 3D blends in draw order: opaque parts first, translucent last.
            for mesh, rgb, alpha in opaque + translucent:
                group.Children.Add(self._mesh_model(mesh, self._material_brush(rgb, alpha)))
        visual = ModelVisual3D()
        visual.Content = group
        viewport = self.preview_viewport
        viewport.Children.Clear()
        viewport.Children.Add(visual)
        has_geometry = model is not None and not model.is_empty
        self.preview_empty.Visibility = WinVis.Collapsed if has_geometry else WinVis.Visible
        if self._preview_fit_next and has_geometry:
            self._preview_yaw = preview_mesh.DEFAULT_YAW
            self._preview_pitch = preview_mesh.DEFAULT_PITCH
            self._preview_zoom = 1.0
            self._preview_fit_next = False
        self._update_preview_camera()
        self._fill_legend(schema, model)
        self._fill_summary(schema, model, errors)
        self._show_preview_issues(list(errors) + list(warnings))

    def _mesh_model(self, mesh, brush):
        """One schema part -> GeometryModel3D (meters, Z up)."""
        positions = Point3DCollection(len(mesh.positions))
        for x, y, z in mesh.positions:
            positions.Add(Point3D(x / 1000.0, y / 1000.0, z / 1000.0))
        indices = Int32Collection(len(mesh.triangles) * 3)
        for a, b, c in mesh.triangles:
            indices.Add(a)
            indices.Add(b)
            indices.Add(c)
        geometry = MeshGeometry3D()
        geometry.Positions = positions
        geometry.TriangleIndices = indices
        geometry.Freeze()
        material = DiffuseMaterial(brush)
        model = GeometryModel3D(geometry, material)
        model.BackMaterial = material   # the preview never hides a mis-wound face
        model.Freeze()
        return model

    def _update_preview_camera(self):
        camera = self._preview_camera
        if camera is None:
            return
        model = self._preview_model
        bmin = model.bbox_min if model is not None else None
        bmax = model.bbox_max if model is not None else None
        position, look, up = preview_mesh.camera_pose(
            bmin, bmax, self._preview_yaw, self._preview_pitch, self._preview_zoom)
        camera.Position = Point3D(position[0] / 1000.0, position[1] / 1000.0, position[2] / 1000.0)
        camera.LookDirection = Vector3D(look[0] / 1000.0, look[1] / 1000.0, look[2] / 1000.0)
        camera.UpDirection = Vector3D(up[0], up[1], up[2])

    def _fill_legend(self, schema, model):
        counts = model.material_counts() if model is not None else {}
        rows = []
        for mat in (schema or {}).get('materials') or []:
            if not isinstance(mat, dict) or not isinstance(mat.get('name'), str):
                continue
            rgb = parse_color(mat.get('color'))
            rows.append(_LegendRow(mat, rgb, counts.get(mat['name'], 0)))
        self.preview_legend.ItemsSource = to_items_source(rows)
        self.preview_legend_empty.Visibility = WinVis.Collapsed if rows else WinVis.Visible

    def _fill_summary(self, schema, model, errors):
        if not schema:
            self.preview_summary.Text = "Category, part count and size appear here."
            return
        info = schema_summary(schema)
        size = model.size_mm() if model is not None else (0, 0, 0)
        unit = getattr(self, '_unit', None) or MILLIMETERS
        size_text = u"{} × {} × {}".format(
            unit.default_text(size[0]), unit.default_text(size[1]),
            unit.show(MILLIMETERS.to_feet(size[2])))
        lines = [
            "{} · {}".format(info.get('family_name') or "(no family_name)",
                             info.get('family_category') or "(no category)"),
            "{} solid(s), {} void(s), {} material(s), {} parameter(s)".format(
                info['solids'], info['voids'], len(info['materials']), info['parameters']),
            "Size {} (W × D × H)".format(size_text),
            ("Ready to create." if not errors else
             "{} problem(s) must be fixed before Create Family.".format(len(errors))),
        ]
        self.preview_summary.Text = "\n".join(lines)

    def _show_preview_issues(self, issues):
        issues = [i for i in issues if i]
        if not issues:
            self.preview_issues.Visibility = WinVis.Collapsed
            self.preview_issue_text.Text = ""
            self.preview_issue_text.ToolTip = None
            return
        shown = issues[:4]
        if len(issues) > 4:
            shown.append("... and {} more (hover to see all)".format(len(issues) - 4))
        self.preview_issue_text.Text = "\n".join(shown)
        self.preview_issue_text.ToolTip = "\n".join(issues)
        self.preview_issues.Visibility = WinVis.Visible

    def preview_fit_clicked(self, sender, e):
        self._preview_yaw = preview_mesh.DEFAULT_YAW
        self._preview_pitch = preview_mesh.DEFAULT_PITCH
        self._preview_zoom = 1.0
        self._update_preview_camera()

    def preview_mouse_down(self, sender, e):
        try:
            self._preview_drag = e.GetPosition(self.preview_host)
            self.preview_host.CaptureMouse()
        except Exception:
            self._preview_drag = None

    def preview_mouse_up(self, sender, e):
        self._preview_drag = None
        try:
            self.preview_host.ReleaseMouseCapture()
        except Exception:
            pass

    def preview_mouse_move(self, sender, e):
        start = self._preview_drag
        if start is None:
            return
        point = e.GetPosition(self.preview_host)
        self._preview_yaw -= (point.X - start.X) * 0.01
        self._preview_pitch = preview_mesh.clamp_pitch(
            self._preview_pitch + (point.Y - start.Y) * 0.01)
        self._preview_drag = point
        self._update_preview_camera()

    def preview_mouse_wheel(self, sender, e):
        factor = 0.88 if e.Delta > 0 else 1.0 / 0.88
        self._preview_zoom = max(0.1, min(8.0, self._preview_zoom * factor))
        self._update_preview_camera()

    # ── MCP proposals ────────────────────────────────────────────────────────

    def load_proposal(self, schema, proposal_id, note=u""):
        """Show an externally proposed schema in the JSON panel and preview.

        The current draft is kept for Undo AI. Nothing is created in Revit.
        """
        self._show_panel('json')
        category = schema.get('family_category') if isinstance(schema, dict) else None
        try:
            if category in SUPPORTED_CATEGORIES:
                self.json_category_combo.SelectedItem = category
        except Exception:
            pass
        previous = self.json_tb.Text or ""
        formatted = json.dumps(schema, indent=2, ensure_ascii=False)
        if formatted != previous:
            self._prev_json_backup = previous
            self.btn_ai_undo.Visibility = WinVis.Visible
        self._preview_fit_next = True
        self.json_tb.Text = formatted
        timer = getattr(self, '_preview_timer', None)
        if timer is not None:
            timer.Stop()            # the TextChanged debounce would only redo this
        self._refresh_preview()
        info = schema_summary(schema)
        self.lbl_status.Text = (
            u"Proposal {} received: {} part(s), {} material(s). Review the model, then "
            u"Create Family.{}".format(proposal_id, info.get('parts', 0),
                                       len(info.get('materials') or []),
                                       u" Note: " + note if note else u""))
        try:
            if self.WindowState == WindowState.Minimized:
                self.WindowState = WindowState.Normal
            self.Activate()
        except Exception:
            pass

    # ── Batch mode ───────────────────────────────────────────────────────────

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_blocks_grid_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua blocks_grid."""
        self.toggle_all_rows(self.blocks_grid, "IsSelected", sender.IsChecked)


# ==============================================================================
# ENTRY POINTS
# ==============================================================================

def show_family_creator(revit_doc, revit_app, initial_mode='cad'):
    FamilyCreatorDialog(revit_doc, revit_app, initial_mode).ShowDialog()


def show_proposal(revit_doc, revit_app, schema, proposal_id, note=u""):
    """Open (or reuse) the modeless FamiGen review window and load a proposal.

    Called by the MCP server inside ExternalEvent.Execute, i.e. on Revit's main
    thread with API context. Returns 'opened' or 'updated' immediately - it
    never waits for the user (the window is shown with Show(), not ShowDialog()).
    """
    window = family_proposals.get_active_window()
    state = 'updated'
    if window is not None:
        try:
            if not window.IsLoaded and not window.IsVisible:
                window = None
        except Exception:
            window = None
    if window is None:
        window = FamilyCreatorDialog(revit_doc, revit_app, initial_mode='json', modeless=True)
        family_proposals.set_active_window(window)
        window.Show()
        state = 'opened'
    window.load_proposal(schema, proposal_id, note)
    return state
