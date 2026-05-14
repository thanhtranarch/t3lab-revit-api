# -*- coding: utf-8 -*-
"""
Bulk Family Export

Export CAD blocks from imported DWG/DXF files as individual Revit families,
with an option to load the exported families directly into the project.

Parametric door/window families use reference planes + dimensions linked to
Width/Height parameters, following the same pattern as the JSONtoFamily tool.

--------------------------------------------------------
Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/

--------------------------------------------------------
"""
__author__  = "Tran Tien Thanh"
__title__   = "CAD To Family"
__version__ = "2.0.0"

# IMPORT LIBRARIES
# ==================================================
import os
import re
import sys
import math
import traceback
import datetime
import time
import tempfile

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

# from __future__ import unicode_literals

from System.Windows import WindowState, Visibility as System_Windows_Visibility
from System.Windows.Controls import DataGridComboBoxColumn, DataGridLength
from System.Windows.Data import Binding, BindingMode, UpdateSourceTrigger
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind

from pyrevit import revit, DB, forms, script

# Path setup
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

from Autodesk.Revit.DB import (
    ImportInstance, FilteredElementCollector,
    Options, GeometryInstance,
    Line, Arc, XYZ, Plane,
    CurveArray, CurveArrArray,
    SketchPlane, SaveAsOptions,
    Transaction, ElementId,
    View, ViewType, ReferencePlane, ReferenceArray,
    PlanarFace, Solid, IFailuresPreprocessor, FailureProcessingResult, FailureSeverity,
)

from Utils.DWGFamilyHelpers import get_xy_bounds, _project_curve_to_z as _dwg_project_curve
class WarningSwallower(IFailuresPreprocessor):
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

# DEBUG LOGGING
# ==================================================
SCRIPT_NAME = os.path.splitext(os.path.basename(__file__))[0].replace('_script', '')
LOG_FILE = os.path.join(os.path.dirname(__file__), '{}_log.txt'.format(SCRIPT_NAME))

def init_log():
    """Initialize log file (clear previous run)"""
    with open(LOG_FILE, 'w') as f:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write("="*60 + "\n")
        f.write("{} - Script Started\n".format(timestamp))
        f.write("="*60 + "\n\n")

def write_log(message, level="INFO"):
    """Write message to log file with timestamp and level"""
    safe_message = message.encode('utf-8', errors='ignore').decode('utf-8')
    with open(LOG_FILE, 'a') as f:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write("[{}] [{}] {}\n".format(timestamp, level, safe_message))

def write_error(message, exception=None):
    """Write error with full traceback"""
    write_log(message, "ERROR")
    if exception:
        tb = traceback.format_exc()
        safe_tb = tb.encode('utf-8', errors='ignore').decode('utf-8')
        with open(LOG_FILE, 'a') as f:
            f.write(safe_tb + "\n")

# DEFINE VARIABLES
# ==================================================
doc   = revit.doc
uidoc = revit.uidoc
app   = revit.doc.Application

logger = script.get_logger()
output = script.get_output()
REVIT_VERSION = int(revit.doc.Application.VersionNumber)

# Category-to-template mapping
CATEGORY_TEMPLATES = [
    ("Generic Model",        ["Generic Model.rft", "Metric Generic Model.rft"]),
    ("Door",                 ["Door.rft", "Metric Door.rft"]),
    ("Window",               ["Window.rft", "Metric Window.rft"]),
    ("Furniture",            ["Furniture.rft", "Metric Furniture.rft"]),
    ("Plumbing Fixture",     ["Plumbing Fixture.rft", "Metric Plumbing Fixture.rft"]),
    ("Electrical Equipment", ["Electrical Equipment.rft"]),
    ("Mechanical Equipment", ["Mechanical Equipment.rft"]),
    ("Specialty Equipment",  ["Specialty Equipment.rft", "Metric Specialty Equipment.rft"]),
    ("Casework",             ["Casework.rft", "Metric Casework.rft"]),
    ("Columns",              ["Column.rft", "Metric Column.rft"]),
    ("Lighting Fixture",     ["Lighting Fixture.rft", "Metric Lighting Fixture.rft"]),
    ("Site",                 ["Site.rft", "Metric Site.rft"]),
    ("Entourage",            ["Entourage.rft", "Metric Entourage.rft"]),
]

DISCIPLINES = [
    "Architecture",
    "Structure",
    "Mechanical",
    "Electrical",
    "Plumbing",
    "Fire Protection",
    "General"
]

# Door Preset Definitions
# Source: T3Lab_Door_location_Not_Shared_2.rfa (W=810, H=2200, FW=65, PE=25, PI=25, LT=40)
DOOR_PRESETS = [
    # (label, width_mm, height_mm, frame_w_mm, proj_ext_mm, proj_int_mm, leaf_t_mm, door_count)
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

SCL = 1.0 / 304.8   # mm to Revit internal feet (confirmed working)

# Max individual curves written as ModelCurves for non-door/window blocks.
# Blocks exceeding this use bounding-box extrusion only (faster, more stable).
MAX_DETAIL_CURVES = 150

# If fewer than this fraction of curves are written successfully as model lines,
# fall back to embedding the source DWG directly into the family document.
MIN_CURVE_RATIO = 0.30

# Window Preset Definitions
# (label, width_mm, height_mm)

WINDOW_PRESETS = [
    ("Fixed_600x1200",          600, 1200),
    ("Fixed_600x1500",          600, 1500),
    ("Fixed_900x1200",          900, 1200),
    ("Fixed_900x1500",          900, 1500),
    ("Fixed_1200x1500",        1200, 1500),
    ("Fixed_1500x1500",        1500, 1500),
    ("Fixed_1800x1500",        1800, 1500),
    ("Casement_1200x1500",     1200, 1500),
    ("Casement_1500x1500",     1500, 1500),
    ("Casement_1800x1500",     1800, 1500),
    ("Casement_2400x1500",     2400, 1500),
    ("Sliding_1800x1500",      1800, 1500),
    ("Sliding_2400x1500",      2400, 1500),
]

# Generic Preset Definitions (label, width_mm, depth_mm, height_mm)

FURNITURE_PRESETS = [
    ("Chair_Office_600x600x900",          600,   600,  900),
    ("Chair_Dining_500x500x800",          500,   500,  800),
    ("Table_Dining_4pax_1200x800",       1200,   800,  750),
    ("Table_Dining_6pax_1600x800",       1600,   800,  750),
    ("Table_Conference_2400x1000",       2400,  1000,  750),
    ("Table_Conference_3600x1200",       3600,  1200,  750),
    ("Desk_Office_1200x600",             1200,   600,  750),
    ("Desk_Office_1600x700",             1600,   700,  750),
    ("Sofa_2Seat_1400x850",             1400,   850,  800),
    ("Sofa_3Seat_2000x850",             2000,   850,  800),
    ("Bed_Single_1000x2000",            1000,  2000,  500),
    ("Bed_Double_1600x2000",            1600,  2000,  500),
    ("Bed_King_1800x2000",              1800,  2000,  500),
    ("Wardrobe_2Door_1200x600",         1200,   600, 2200),
    ("Wardrobe_3Door_1800x600",         1800,   600, 2200),
    ("Bookcase_900x300x2100",            900,   300, 2100),
]

CASEWORK_PRESETS = [
    ("Cabinet_Base_600x600x850",          600,   600,  850),
    ("Cabinet_Base_800x600x850",          800,   600,  850),
    ("Cabinet_Wall_600x300x600",          600,   300,  600),
    ("Cabinet_Wall_800x300x600",          800,   300,  600),
    ("Counter_Kitchen_1200x600",         1200,   600,  900),
    ("Counter_Kitchen_1800x600",         1800,   600,  900),
    ("Counter_Kitchen_2400x600",         2400,   600,  900),
    ("Island_Kitchen_1200x900",          1200,   900,  900),
    ("Island_Kitchen_1500x900",          1500,   900,  900),
    ("Vanity_Unit_900x500x850",           900,   500,  850),
    ("Vanity_Unit_1200x500x850",         1200,   500,  850),
    ("Shelf_Unit_900x300x2100",           900,   300, 2100),
    ("Display_Cabinet_1200x400x2200",    1200,   400, 2200),
]

PLUMBING_PRESETS = [
    ("WC_Toilet_Std_380x700",             380,   700,  400),
    ("WC_Toilet_Compact_360x650",         360,   650,  400),
    ("WC_Wall_Hung_380x560",              380,   560,  390),
    ("Sink_Vanity_600x500",               600,   500,  150),
    ("Sink_Vanity_800x500",               800,   500,  150),
    ("Sink_Kitchen_800x500",              800,   500,  200),
    ("Sink_Kitchen_1000x500",            1000,   500,  200),
    ("Bath_Builtin_1500x700",            1500,   700,  600),
    ("Bath_Builtin_1700x700",            1700,   700,  600),
    ("Bath_Freestanding_1700x800",       1700,   800,  600),
    ("Shower_Tray_900x900",               900,   900,  150),
    ("Shower_Tray_1200x800",             1200,   800,  150),
    ("Shower_Tray_1200x900",             1200,   900,  150),
    ("Urinal_Std_360x330",                360,   330,  560),
    ("Floor_Drain_150x150",               150,   150,   80),
]

LIGHTING_PRESETS = [
    ("Downlight_D100",                    100,   100,   80),
    ("Downlight_D150",                    150,   150,   80),
    ("Downlight_D200",                    200,   200,  100),
    ("Panel_300x600",                     300,   600,   80),
    ("Panel_300x1200",                    300,  1200,   80),
    ("Panel_600x600",                     600,   600,   80),
    ("Ceiling_Round_D300",                300,   300,  120),
    ("Ceiling_Round_D400",                400,   400,  150),
    ("Ceiling_Round_D600",                600,   600,  180),
    ("Linear_Strip_1200",                 100,  1200,   60),
    ("Linear_Strip_2400",                 100,  2400,   60),
    ("Pendant_D200",                      200,   200,  300),
    ("Pendant_D400",                      400,   400,  300),
    ("Wall_Sconce_200x100",               200,   100,  250),
    ("Floodlight_300x200",                300,   200,  150),
]

MECHANICAL_PRESETS = [
    ("FCU_Cassette_600x600",              600,   600,  280),
    ("FCU_Cassette_900x900",              900,   900,  280),
    ("FCU_Cassette_1200x1200",           1200,  1200,  280),
    ("FCU_Wall_Mount_900x300",            900,   300,  250),
    ("FCU_Wall_Mount_1200x300",          1200,   300,  250),
    ("AHU_Floor_800x600x1500",            800,   600, 1500),
    ("AHU_Floor_1200x800x1800",          1200,   800, 1800),
    ("AHU_Ceiling_1500x800x500",         1500,   800,  500),
    ("Chiller_2000x1000x1500",           2000,  1000, 1500),
    ("Cooling_Tower_2000x2000x3000",     2000,  2000, 3000),
    ("Pump_600x400x500",                  600,   400,  500),
    ("Boiler_900x700x1200",               900,   700, 1200),
    ("Expansion_Tank_D600x900",           600,   600,  900),
]

ELECTRICAL_PRESETS = [
    ("Panel_DB_500x200x600",              500,   200,  600),
    ("Panel_DB_600x250x1000",             600,   250, 1000),
    ("Panel_MDB_800x400x1200",            800,   400, 1200),
    ("Cabinet_Control_800x400x1800",      800,   400, 1800),
    ("Cabinet_Control_1000x500x2000",    1000,   500, 2000),
    ("UPS_600x600x1000",                  600,   600, 1000),
    ("UPS_800x700x1200",                  800,   700, 1200),
    ("Transformer_1000x700x1400",        1000,   700, 1400),
    ("Switchgear_800x600x2000",           800,   600, 2000),
    ("Switchgear_1200x800x2200",         1200,   800, 2200),
    ("Socket_Outlet_86x86",               86,    86,   60),
    ("Junction_Box_150x150x100",          150,   150,  100),
]

SPECIALTY_PRESETS = [
    ("Counter_Reception_2000x800x1100",  2000,   800, 1100),
    ("Counter_Reception_3000x800x1100",  3000,   800, 1100),
    ("Counter_Service_1500x700x900",     1500,   700,  900),
    ("ATM_Machine_500x500x1800",          500,   500, 1800),
    ("Vending_Machine_700x800x1900",      700,   800, 1900),
    ("Safe_600x500x800",                  600,   500,  800),
    ("Server_Rack_600x1000x2000",         600,  1000, 2000),
    ("Server_Rack_800x1200x2200",         800,  1200, 2200),
    ("Kiosk_Self_Service_700x700x1500",   700,   700, 1500),
    ("Turnstile_1000x600x1000",          1000,   600, 1000),
    ("Fire_Extinguisher_D200x550",        200,   200,  550),
    ("Fire_Hose_Cabinet_700x250x900",     700,   250,  900),
]

COLUMN_PRESETS = [
    ("Col_Square_200x200x3000",   200,  200, 3000),
    ("Col_Square_250x250x3000",   250,  250, 3000),
    ("Col_Square_300x300x3000",   300,  300, 3000),
    ("Col_Square_400x400x3000",   400,  400, 3000),
    ("Col_Square_500x500x3000",   500,  500, 3000),
    ("Col_Square_600x600x3000",   600,  600, 3000),
    ("Col_Rect_200x400x3000",     200,  400, 3000),
    ("Col_Rect_250x500x3000",     250,  500, 3000),
    ("Col_Rect_300x600x3000",     300,  600, 3000),
    ("Col_Rect_350x700x3000",     350,  700, 3000),
    ("Col_Round_D300x3000",       300,  300, 3000),
    ("Col_Round_D400x3000",       400,  400, 3000),
    ("Col_Round_D500x3000",       500,  500, 3000),
]

GENERIC_PRESETS = [
    ("Box_100x100x100",    100,  100,  100),
    ("Box_200x200x200",    200,  200,  200),
    ("Box_500x500x500",    500,  500,  500),
    ("Box_1000x500x500",  1000,  500,  500),
    ("Box_1000x1000x500", 1000, 1000,  500),
    ("Box_1000x1000x1000",1000, 1000, 1000),
    ("Slab_2000x1000x200",2000, 1000,  200),
    ("Slab_3000x2000x300",3000, 2000,  300),
    ("Wall_3000x200x3000",3000,  200, 3000),
]

ENTOURAGE_PRESETS = [
    ("Tree_Small_D2000x4000",   2000, 2000, 4000),
    ("Tree_Medium_D3000x6000",  3000, 3000, 6000),
    ("Tree_Large_D5000x8000",   5000, 5000, 8000),
    ("Shrub_D1000x600",         1000, 1000,  600),
    ("Person_Standing_600x300x1750",  600,  300, 1750),
    ("Person_Seated_600x700x1200",    600,  700, 1200),
    ("Car_Sedan_4500x1900x1450",     4500, 1900, 1450),
    ("Car_SUV_4700x2000x1700",       4700, 2000, 1700),
    ("Bicycle_1800x600x1100",        1800,  600, 1100),
    ("Motorcycle_2200x800x1200",     2200,  800, 1200),
]

SITE_PRESETS = [
    ("Parking_Space_2500x5000",  2500, 5000,   50),
    ("Parking_Space_2700x5500",  2700, 5500,   50),
    ("Disabled_Parking_3500x5000",3500,5000,   50),
    ("Bike_Stand_600x200x1000",   600,  200, 1000),
    ("Bollard_D200x800",          200,  200,  800),
    ("Planter_Box_1000x500x600", 1000,  500,  600),
    ("Planter_Box_2000x500x600", 2000,  500,  600),
    ("Bench_1800x500x450",       1800,  500,  450),
    ("Bench_1200x500x450",       1200,  500,  450),
    ("Sign_Post_100x100x3000",    100,  100, 3000),
    ("Waste_Bin_D400x800",        400,  400,  800),
    ("Light_Pole_D200x5000",      200,  200, 5000),
]

# Unified Category â†’ Preset mapping
# mode: "door" | "window" | "generic"

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




# Data Model
# Keywords searched in (block_name + layer/graphicsstyle_category), lowercased.
# English terms, Vietnamese no-diacritic, and common DWG layer prefixes are all included.
_CAT_HINTS = [
    ("Door", [
        # English
        "door", "swing", "sliding door", "folding door",
        # Vietnamese no-diacritic
        "cua di", "cuadi", "cua ra", "cua vao",
        # Layer prefixes
        "a-door", "kl-door", "arch-door", "-door",
    ]),
    ("Window", [
        # English
        "window", "casement", "skylight",
        # Vietnamese no-diacritic
        "cua so", "cuaso",
        # Layer prefixes
        "a-wind", "kl-wind", "-wind",
    ]),
    ("Furniture", [
        # English
        "furnitur", "chair", "table", "desk", "sofa", "bed",
        "armchair", "bookcase", "wardrobe", "lounge", "seating",
        # Vietnamese no-diacritic
        "noi that", "noithat", "ban ghe", "banghe",
        "ban lam viec", "ghe ngoi", "tu quan ao",
        # Layer prefixes
        "a-furn", "kl-furn", "-furn", "ff&e", "ff-e",
        "-furniture", "a-furniture",
    ]),
    ("Casework", [
        # English
        "casework", "counter", "kitchen", "shelv", "cabinet",
        "cupboard", "joinery",
        # Vietnamese no-diacritic
        "tu bep", "tubep", "tu am tuong", "tu bep duoi",
        "quay bep", "quay le tan", "bep",
        # Layer prefixes
        "a-case", "kl-case", "-casework",
    ]),
    ("Plumbing Fixture", [
        # English
        "plumb", "sanitary", "toilet", "wc", "sink", "basin",
        "shower", "bath", "urinal", "lavatory", "bidet",
        # Vietnamese no-diacritic
        "thiet bi ve sinh", "thietbivesinnh", "bon tam", "bon cau",
        "chau rua", "chau lavabo", "thiet bi nuoc", "ve sinh",
        # Layer prefixes (including project-specific conventions)
        "p-fixt", "m-plmb", "kl-plmb", "-plumb", "-sanitary",
        "eqpm-fixd", "eqpm-fix", "eqpm",
    ]),
    ("Lighting Fixture", [
        # English
        "light", "lamp", "luminaire", "led", "spotlight",
        "downlight", "pendant", "sconce", "chandelier",
        # Vietnamese no-diacritic
        "den", "den chieu sang", "chieu sang", "den treo",
        "den am tran", "den tuong",
        # Layer prefixes
        "e-lite", "e-lght", "kl-lght", "-light", "-lite",
        "-lighting", "a-lighting",
    ]),
    ("Mechanical Equipment", [
        # English
        "mechanical", "hvac", "ahu", "fcu", "fahu", "chiller",
        "cooling", "boiler", "pump", "fan", "duct", "damper",
        # Vietnamese no-diacritic
        "may lanh", "maylanh", "dieu hoa", "dieuhoa",
        "thong gio", "cap nhiet", "bom nhiet",
        # Layer prefixes
        "m-equip", "m-mech", "kl-mech", "-mech", "-hvac",
    ]),
    ("Electrical Equipment", [
        # English
        "electrical", "switchgear", "transformer", "ups",
        "panel", "mdb", "smdb", "db", "mcb", "busbar",
        # Vietnamese no-diacritic
        "dien", "tu dien", "tudien", "bang dien", "thiet bi dien",
        # Layer prefixes
        "e-equip", "e-powr", "kl-elec", "-elec", "-electr",
    ]),
    ("Specialty Equipment", [
        # English
        "machine", "appliance", "kiosk", "atm",
        "vending", "server", "rack",
        # Vietnamese no-diacritic
        "may moc",
        # Layer prefixes
        "a-equip", "kl-equip",
    ]),
    ("Columns", [
        # English
        "column", " col ", "pillar", "pier", "post",
        # Structural / cáº¥u kiá»‡n káº¿t cáº¥u
        "struc", "structural", "ket cau", "ketcau",
        "beam", "slab", "footing", "foundation",
        # Vietnamese no-diacritic
        "cot", "tru", "dam", "san", "mong",
        # Layer prefixes (S- = structural discipline)
        "s-col", "a-col", "kl-col", "-col-", "-column",
        "kc-", "s-beam", "s-slab", "s-wall", "s-str",
    ]),
    ("Site", [
        # English
        "site", "parking", "landscape", "paving", "bollard",
        "tree", "bench", "pavement",
        # Vietnamese no-diacritic
        "san vuon", "cay xanh", "bai xe", "he thong ngoai that",
        # Layer prefixes
        "l-site", "a-site", "kl-site", "-site", "-land",
    ]),
    ("Entourage", [
        # English
        "entourage", "person", "people", "car", "vehicle",
        "bicycle", "human", "figure",
        # Vietnamese no-diacritic
        "nguoi", "xe hoi", "xe dap",
        # Layer prefixes
        "-entour", "a-entour",
    ]),
    # â”€â”€ Generic Model catch-all â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Matched BEFORE the geometry heuristic (arc-count â†’ Door/Window) so that
    # walls, glazing, title-block and annotation layers are never mis-suggested
    # as Door or Window based on curved lines alone.
    ("Generic Model", [
        # Wall / tÆ°á»ng (system families â†’ closest loadable = Generic Model)
        "wall", "tuong", "a-wall", "kl-wall", "s-wall-",
        # Glass / curtain wall panels
        "glass", "glazing", "curtain", "kinh",
        # Title block / khung tÃªn / detail items
        "title block", "titleblock", "title blk", "title-blk",
        "khung ten", "khungten", "khung-ten",
        "border", "sheet border", "annotation", "detailitem",
        "detail item", "tb-", "-tblock",
    ]),
]

# CLASS/FUNCTIONS
# ==================================================
def _suggest_category(name, arc_count, width_mm, depth_mm, layer=""):
    """Auto-suggest a Revit family category using block name + GraphicsStyle layer.

    Searches keywords in the combined lowercase string of (block_name + layer),
    so both DWG layer conventions (A-FURN, KL-A-FURNITURE) and block names
    (Vietnamese no-diacritic / English) are matched simultaneously.
    """
    # Combine block name and full layer string for matching
    combined = (name + " " + layer).lower()

    for cat, keywords in _CAT_HINTS:
        if any(k in combined for k in keywords):
            return "Generic Model" if cat == "Door" else cat

    # Geometry fallback when no keyword matched
    if arc_count == 0 and 0 < depth_mm < 350 and width_mm >= 400:
        return "Window"
    return "Generic Model"


def _graphicstyle_layer(geom_elem):
    """Walk a geometry tree and return the first GraphicsStyleCategory name found.

    Uses GraphicsStyle.GraphicsStyleCategory.Name (confirmed via RevitLookup)
    which holds the full DWG layer path, e.g. 'KL-A-FURNITURE'.
    Falls back to GraphicsStyle.Name if the Category is unavailable.
    """
    for item in geom_elem:
        try:
            sid = getattr(item, 'GraphicsStyleId', None)
            if sid and sid != ElementId.InvalidElementId:
                style = doc.GetElement(sid)
                if style:
                    # Primary: GraphicsStyleCategory.Name
                    try:
                        cat = style.GraphicsStyleCategory
                        if cat and cat.Name:
                            return cat.Name
                    except Exception:
                        pass
                    # Fallback: style.Name
                    try:
                        if style.Name:
                            return style.Name
                    except Exception:
                        pass
        except Exception:
            pass
        # Recurse into nested GeometryInstances
        if isinstance(item, GeometryInstance):
            try:
                nested = item.GetInstanceGeometry()
                if nested:
                    result = _graphicstyle_layer(nested)
                    if result:
                        return result
            except Exception:
                pass
    return ""


class BlockItem(object):
    """Represents a discovered CAD block for DataGrid binding."""

    def __init__(self, name, curve_count, instance_count, curves,
                 layer_level="", placements=None, import_inst=None):
        self.IsSelected    = True
        self.BlockName     = name
        self.CurveCount    = curve_count
        self.InstanceCount = instance_count
        self.LayerLevel    = layer_level
        self._curves       = curves          # internal use only
        # list of (XYZ centroid, float angle_rad) â€“ one entry per CAD instance
        self._placements   = placements if placements is not None else []
        # source ImportInstance â€“ used by _embed_dwg_into_family fallback
        self._import_inst  = import_inst

        # â”€â”€ Geometry detail properties (shown in DataGrid) â”€â”€
        arc_count = sum(1 for c in curves if isinstance(c, Arc))
        self.ArcCount = arc_count

        try:
            min_x, max_x, min_y, max_y = get_xy_bounds(curves)
            w = (max_x - min_x) * 304.8   # feet â†’ mm
            d = (max_y - min_y) * 304.8
            self.WidthMM = "{:.0f}".format(w)
            self.DepthMM = "{:.0f}".format(d)
        except Exception:
            w, d = 0.0, 0.0
            self.WidthMM = "-"
            self.DepthMM = "-"

        # layer_level = GraphicsStyleCategory name â†’ used for keyword matching
        self.SuggestedCat = _suggest_category(name, arc_count, w, d, layer=layer_level)
        self.Category     = self.SuggestedCat   # user-editable per-row category


# Main Window
class BulkFamilyExportWindow(forms.WPFWindow):

    def __init__(self):
        # script.py â†’ pushbutton â†’ stack â†’ panel â†’ tab â†’ extension (5 levels)
        ext_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.dirname(__file__)))))
        xaml_path = os.path.join(ext_dir, 'lib', 'GUI', 'Tools', 'BulkFamilyExport.xaml')
        forms.WPFWindow.__init__(self, xaml_path)

        self._ext_dir       = ext_dir
        self._cad_instances = []
        self._block_items   = []   # full list after scan
        self._filter_text   = ""   # current search string
        self._filter_cat    = ""   # current suggested-category filter ("" = All)

        self._cancel_requested = False
        self._pause_requested  = False

        self._init_cad_files()
        self._init_disciplines()
        self._init_categories()
        self._init_filter_bar()
        self._update_status("Ready")


    # Initialisation
    def _init_cad_files(self):
        """Populate the CAD file ComboBox with all ImportInstances."""
        collector = FilteredElementCollector(doc).OfClass(ImportInstance)
        self.cad_file_combo.Items.Add("<All Imported CAD Files>")
        for inst in collector:
            name = self._get_cad_name(inst)
            self._cad_instances.append(inst)
            self.cad_file_combo.Items.Add(name)
        if self._cad_instances:
            self.cad_file_combo.SelectedIndex = 0

    @staticmethod
    def _read_symbol_name(inst):
        """Read the importSymbolName parameter ('Name') from an ImportInstance.

        Priority:
          1. importSymbolName BuiltInParameter  (confirmed via RevitLookup)
          2. Parameter looked up by definition name 'Name'
          3. Type element Name fallback
          4. inst.Name / 'Unknown'
        """
        # 1 â”€â”€ BuiltInParameter (most reliable, works Revit 2019-2025)
        try:
            p = inst.get_Parameter(DB.BuiltInParameter.IMPORT_SYMBOL_NAME)
            if p and p.HasValue:
                val = p.AsString()
                if val:
                    return val
        except Exception:
            pass

        # 2 â”€â”€ Lookup by definition name "Name" (covers schema importSymbolName)
        try:
            for p in inst.Parameters:
                if p.Definition.Name == "Name" and p.StorageType.ToString() == "String":
                    val = p.AsString()
                    if val:
                        return val
        except Exception:
            pass

        # 3 â”€â”€ Type element name
        try:
            type_id = inst.GetTypeId()
            if type_id and type_id != ElementId.InvalidElementId:
                elem_type = doc.GetElement(type_id)
                if elem_type and hasattr(elem_type, 'Name') and elem_type.Name:
                    return elem_type.Name
        except Exception:
            pass

        return inst.Name if hasattr(inst, 'Name') else "Unknown"

    def _get_cad_name(self, inst):
        """Return the import symbol name for an ImportInstance."""
        return self._read_symbol_name(inst)

    def _init_disciplines(self):
        """Populate the discipline ComboBox."""
        for name in DISCIPLINES:
            self.discipline_combo.Items.Add(name)
        self.discipline_combo.SelectedIndex = 6

    def _init_categories(self):
        """Populate the category ComboBox and add per-row Category column to the DataGrid."""
        cat_names = [name for name, _ in CATEGORY_TEMPLATES]
        for name in cat_names:
            self.category_combo.Items.Add(name)
        self.category_combo.SelectedIndex = 0

        col = DataGridComboBoxColumn()
        col.Header = "Category"
        col.Width = DataGridLength(140)
        col.ItemsSource = cat_names
        b = Binding("Category")
        b.Mode = BindingMode.TwoWay
        b.UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged
        col.SelectedItemBinding = b
        self.blocks_grid.Columns.Add(col)

    def _init_filter_bar(self):
        """Seed the Suggested filter ComboBox with 'All' as the only item at startup."""
        self.combo_filter_suggested.Items.Add("All Categories")
        self.combo_filter_suggested.SelectedIndex = 0

    def _refresh_suggested_combo(self):
        """Rebuild the Suggested filter dropdown from the current block list.

        Always has 'All Categories' at index 0, then sorted unique SuggestedCat values.
        Preserves the previously selected value if it still exists.
        """
        prev = self._filter_cat  # remember current selection

        # Suppress SelectionChanged during rebuild to avoid spurious filter calls
        self.combo_filter_suggested.SelectionChanged -= self.filter_suggested_changed

        self.combo_filter_suggested.Items.Clear()
        self.combo_filter_suggested.Items.Add("All Categories")

        cats = sorted(set(
            item.Category for item in self._block_items
            if item.Category))
        for c in cats:
            self.combo_filter_suggested.Items.Add(c)

        # Restore selection
        if prev and prev in cats:
            self.combo_filter_suggested.SelectedItem = prev
        else:
            self.combo_filter_suggested.SelectedIndex = 0
            self._filter_cat = ""

        self.combo_filter_suggested.SelectionChanged += self.filter_suggested_changed

    def _apply_filter(self):
        """Filter `_block_items` by search text + suggested category,
        then rebind the DataGrid and update the count label."""
        txt = self._filter_text.lower().strip()
        cat = self._filter_cat  # "" means All

        if not txt and not cat:
            visible = self._block_items
        else:
            visible = []
            for item in self._block_items:
                # Text: match BlockName or LayerLevel (case-insensitive)
                if txt and txt not in item.BlockName.lower() \
                        and txt not in item.LayerLevel.lower():
                    continue
                # Category: exact match on per-row Category
                if cat and item.Category != cat:
                    continue
                visible.append(item)

        self.blocks_grid.ItemsSource = visible

        total   = len(self._block_items)
        showing = len(visible)
        if total == 0:
            self.txt_filter_count.Text = ""
        elif showing == total:
            self.txt_filter_count.Text = "{} items".format(total)
        else:
            self.txt_filter_count.Text = "{} / {} items".format(showing, total)

    # Filter event handlers
    def search_text_changed(self, sender, e):
        """Live-filter as the user types in the search box."""
        self._filter_text = self.txt_search.Text or ""

        # Show/hide placeholder and clear button
        has_text = bool(self._filter_text)
        self.txt_search_placeholder.Visibility = (
            System_Windows_Visibility.Collapsed if has_text
            else System_Windows_Visibility.Visible)
        self.btn_clear_search.Visibility = (
            System_Windows_Visibility.Visible if has_text
            else System_Windows_Visibility.Collapsed)

        self._apply_filter()

    def clear_search_clicked(self, sender, e):
        """Clear the search box."""
        self.txt_search.Text = ""  # triggers search_text_changed automatically

    def filter_suggested_changed(self, sender, e):
        """Filter by the selected Suggested category."""
        sel = self.combo_filter_suggested.SelectedItem
        if sel is None or sel == "All Categories":
            self._filter_cat = ""
        else:
            self._filter_cat = sel
        self._apply_filter()

    def _update_status(self, text):
        try:
            self.status_text.Text = text
        except Exception:
            pass

    # â”€â”€ Progress bar + Stop / Pause helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _do_events(self):
        """Pump the WPF dispatcher so the UI repaints and queued button clicks fire."""
        try:
            from System.Windows.Threading import DispatcherPriority, DispatcherFrame
            import System
            frame = DispatcherFrame()
            def _stop(f=frame):
                f.Continue = False
            self.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                System.Action(_stop))
            self.Dispatcher.PushFrame(frame)
        except Exception:
            pass

    def _update_progress(self, value, maximum):
        """Update the progress panel and pump the dispatcher.

        If the user clicked Pause, spin here (pumping events) until Resume or Stop.
        """
        try:
            from System.Windows import Visibility as WinVisibility
            self.pb_export.Maximum = maximum
            self.pb_export.Value   = value
            self.progress_panel.Visibility = WinVisibility.Visible
        except Exception:
            pass

        self._do_events()

        # Cooperative pause: keep pumping events until resumed or cancelled
        while self._pause_requested and not self._cancel_requested:
            self._do_events()

    def _hide_progress(self):
        """Collapse the progress panel and reset all control state."""
        try:
            from System.Windows import Visibility as WinVisibility
            self.progress_panel.Visibility = WinVisibility.Collapsed
            self.pb_export.Value = 0
            self._cancel_requested = False
            self._pause_requested  = False
            self.btn_pause_export.Content   = u"â¸  Pause"
            self.btn_pause_export.IsEnabled = True
            self.btn_stop_export.IsEnabled  = True
        except Exception:
            pass

    def stop_export_clicked(self, sender, e):
        """Signal the running export to abort after the current block."""
        self._cancel_requested = True
        self._pause_requested  = False   # unblock a paused loop immediately
        try:
            self.btn_stop_export.IsEnabled = False
            self._update_status(u"Stoppingâ€¦ finishing current block")
        except Exception:
            pass

    def pause_resume_clicked(self, sender, e):
        """Toggle pause / resume of the running export."""
        if self._pause_requested:
            self._pause_requested = False
            try:
                self.btn_pause_export.Content = u"â¸  Pause"
            except Exception:
                pass
        else:
            self._pause_requested = True
            try:
                self.btn_pause_export.Content = u"â–¶  Resume"
                self._update_status(u"Paused â€” click Resume to continue")
            except Exception:
                pass

    # Block Scanning
    def scan_blocks_clicked(self, sender, e):
        """Event handler: scan the selected CAD file for blocks."""
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
                # All imported CAD files
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
                    # Fallback: treat the entire CAD as one block
                    item = self._scan_entire_cad(import_inst)
                    if item:
                        blocks.append(item)
        except Exception as ex:
            logger.error("Scan error:\n{}".format(traceback.format_exc()))
            forms.alert("Error scanning blocks:\n{}".format(str(ex)))
            self._update_status("Scan failed")
            return

        if not blocks:
            forms.alert(
                "No blocks or curves found in the selected CAD file(s).\n\n"
                "Make sure the CAD files contain geometry.")
            self._update_status("No geometry found")
            return

        self._block_items  = blocks
        self._filter_text  = ""
        self._filter_cat   = ""
        self.txt_search.Text = ""

        # Rebuild the Suggested filter dropdown then apply (shows all items)
        self._refresh_suggested_combo()
        self._apply_filter()

        self._update_status("Found {} unique item(s)".format(len(blocks)))
        self.block_count_text.Text = "{} items found".format(len(blocks))

    def _scan_entire_cad(self, import_inst):
        """Treat an ImportInstance as a single block and return a BlockItem."""
        opt = Options()
        opt.ComputeReferences = True
        opt.IncludeNonVisibleObjects = True

        geom = import_inst.get_Geometry(opt)
        if not geom:
            return None

        min_len = getattr(app, 'ShortCurveTolerance', 0.00256)

        def is_curve(item):
            try:
                from Autodesk.Revit.DB import Curve as _Curve
                return (isinstance(item, _Curve)
                        and item.IsBound
                        and item.Length >= min_len)
            except Exception:
                return False

        def collect_curves(geo_elem):
            """Collect all valid curves recursively, including Solid edges and PolyLines."""
            curves = []
            from Autodesk.Revit.DB import PolyLine, Curve as _Curve
            
            for item in geo_elem:
                if is_curve(item):
                    curves.append(item)
                # Bá»” SUNG 1: Láº¥y cÃ¡c curve khÃ´ng giá»›i háº¡n (vÃ­ dá»¥: hÃ¬nh trÃ²n hoÃ n chá»‰nh)
                elif isinstance(item, _Curve) and not item.IsBound:
                    curves.append(item)
                # Bá»” SUNG 2: Xá»­ lÃ½ PolyLine tá»« CAD
                elif isinstance(item, PolyLine):
                    pts = item.GetCoordinates()
                    for i in range(item.NumberOfCoordinates - 1):
                        try:
                            p1 = pts[i]
                            p2 = pts[i+1]
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

            # Layer = GraphicsStyleCategory name (DWG layer, e.g. "KL-A-FURNITURE")
            layer_name = _graphicstyle_layer(geom)

            # Placement: centroid of all curves in world space, no rotation
            try:
                min_x, max_x, min_y, max_y = get_xy_bounds(curves)
                centroid = XYZ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, 0.0)
            except Exception:
                centroid = XYZ.Zero
            placements = [(centroid, 0.0)]

            return BlockItem(name, len(curves), 1, curves,
                             layer_level=layer_name, placements=placements,
                             import_inst=import_inst)
        return None

    def _scan_blocks(self, import_inst):
        """Walk the geometry tree of an ImportInstance and return unique blocks
        as a list of BlockItem objects."""
        opt = Options()
        opt.ComputeReferences = True
        opt.IncludeNonVisibleObjects = True

        geom = import_inst.get_Geometry(opt)
        if not geom:
            return []

        min_len = getattr(app, 'ShortCurveTolerance', 0.00256)
        found   = {}      # fingerprint -> {name, curves, count}
        counter = [0]     # mutable counter (IronPython 2 â€“ no nonlocal)

        # helpers
        def is_curve(item):
            try:
                from Autodesk.Revit.DB import Curve as _Curve
                return (isinstance(item, _Curve)
                        and item.IsBound
                        and item.Length >= min_len)
            except Exception:
                return False

        def collect_curves(geo_elem):
            """Recursively collect all valid curves from a geometry element.

            Handles:
              â€¢ Direct Curve objects (Line, Arc, NurbSpline, Ellipse, â€¦)
              â€¢ Nested GeometryInstances (sub-blocks)
              â€¢ Solid edges â€“ extracts the edge curves for DWG imports
                that the API represents as thin solids instead of wires.
            """
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
                    # Some DWG geometry surfaces arrive as thin Solids.
                    # Extract their edges so we still capture the outlines.
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
            """Rotation- and position-invariant fingerprint for a set of curves."""
            n     = len(curves)
            total = round(sum(c.Length for c in curves), 1)
            return (n, total)

        def style_name(geo_inst):
            """Return the DWG layer name via GraphicsStyle.GraphicsStyleCategory.Name.

            Falls back to GraphicsStyle.Name if the Category is unavailable.
            """
            try:
                sid = geo_inst.GraphicsStyleId
                if sid and sid != ElementId.InvalidElementId:
                    style = doc.GetElement(sid)
                    if style:
                        # Primary: GraphicsStyleCategory.Name (confirmed via RevitLookup)
                        try:
                            cat = style.GraphicsStyleCategory
                            if cat and cat.Name:
                                return cat.Name
                        except Exception:
                            pass
                        # Fallback: style.Name
                        try:
                            if style.Name:
                                return style.Name
                        except Exception:
                            pass
            except Exception:
                pass
            return None

        def _instance_placement(curves, geo_inst):
            """Return (XYZ centroid, float angle_rad) for a single block instance.

            Centroid comes from the world-space bounding box of the instance curves
            (already transformed by GetInstanceGeometry).
            Rotation comes from the X-axis of the GeometryInstance transform.
            """
            try:
                min_x, max_x, min_y, max_y = get_xy_bounds(curves)
                cx = (min_x + max_x) / 2.0
                cy = (min_y + max_y) / 2.0
                centroid = XYZ(cx, cy, 0.0)
            except Exception:
                centroid = XYZ.Zero
            try:
                bx = geo_inst.Transform.BasisX
                angle = math.atan2(bx.Y, bx.X)
            except Exception:
                angle = 0.0
            return (centroid, angle)

        def register(curves, geo_inst):
            """Register a block (or accumulate its placements for duplicate instances)."""
            fp = fingerprint(curves)
            placement = _instance_placement(curves, geo_inst)

            if fp in found:
                found[fp]['count'] += 1
                found[fp]['placements'].append(placement)
                return

            layer = style_name(geo_inst)
            counter[0] += 1

            # Resolve block name
            # Priority:
            #   1. GeometryInstance.Symbol.Name  (DWG block definition name)
            #   2. importSymbolName parameter on parent ImportInstance
            #   3. Layer-derived fallback
            block_name = ""
            try:
                if hasattr(geo_inst, 'Symbol') and geo_inst.Symbol:
                    block_name = (geo_inst.Symbol.Name or "").strip()
            except Exception:
                pass

            if not block_name:
                block_name = (BulkFamilyExportWindow._read_symbol_name(import_inst) or "").strip()

            if block_name:
                name = block_name
            elif layer:
                name = "{}_Block_{:03d}".format(layer, counter[0])
            else:
                name = "Block_{:03d}".format(counter[0])

            found[fp] = {
                'name':       name,
                'curves':     curves,
                'count':      1,
                'layer':      layer or "",
                'placements': [placement],
            }

        # Walk geometry tree
        def walk(geo_elem, depth):
            for item in geo_elem:
                if not isinstance(item, GeometryInstance):
                    continue

                inst_geom = item.GetInstanceGeometry()
                if not inst_geom:
                    continue

                if depth == 0:
                    # Top-level container (DWG model space) â€“ dive in
                    walk(inst_geom, depth + 1)
                else:
                    # Block reference at depth >= 1
                    curves = collect_curves(inst_geom)
                    if curves:
                        register(curves, item)

        walk(geom, 0)

        # Convert to sorted list of BlockItem
        items = []
        for data in sorted(found.values(), key=lambda d: d['name']):
            items.append(BlockItem(
                data['name'], len(data['curves']), data['count'], data['curves'],
                layer_level=data.get('layer', ""),
                placements=data.get('placements', []),
                import_inst=import_inst))
        return items

    # Folder picker
    def browse_folder_clicked(self, sender, e):
        folder = forms.pick_folder()
        if folder:
            self.output_path.Text = folder

    # Select / Deselect
    def select_all_clicked(self, sender, e):
        for item in self._block_items:
            item.IsSelected = True
        self.blocks_grid.Items.Refresh()

    def deselect_all_clicked(self, sender, e):
        for item in self._block_items:
            item.IsSelected = False
        self.blocks_grid.Items.Refresh()

    # Export
    def export_clicked(self, sender, e):
        """Export each selected block as a .rfa family file."""
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

        # Pre-read UI flag once â€” avoids COM/UI calls inside the tight loop
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
        saved_paths = []   # collected for optional batch-load at the end

        import System as _System
        for i, item in enumerate(selected):
            if self._cancel_requested:
                break

            try:
                category_name = item.Category or "Generic Model"
                template_path = self._find_template_by_name(category_name)
                if not template_path:
                    write_log("No template for '{}', skipping '{}'".format(
                        category_name, item.BlockName), "WARN")
                    failed += 1
                    self._update_progress(i + 1, len(selected))
                    continue

                self._update_status(
                    "Exporting [{}/{}]: {}".format(i + 1, len(selected), item.BlockName))
                self._update_progress(i, len(selected))
                if self._cancel_requested:
                    break

                save_path = self._export_block(
                    item, template_path, output_folder,
                    discipline_name, category_name,
                    load_to_project=False,   # batch-loaded below
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

            # Release accumulated memory every 10 blocks
            if (i + 1) % 10 == 0:
                try:
                    _System.GC.Collect()
                    _System.GC.WaitForPendingFinalizers()
                except Exception:
                    pass

        was_cancelled = self._cancel_requested

        # Batch-load all exported families in one pass
        loaded_count = 0
        if not was_cancelled and load_to_project and saved_paths:
            self._update_status("Loading {} families to project...".format(len(saved_paths)))
            loaded_count = self._batch_load_families(saved_paths)

        if was_cancelled:
            self._update_status(
                "Stopped: {} exported, {} failed".format(success, failed))
        else:
            self._update_status(
                "Done: {} exported, {} failed".format(success, failed))
        self._hide_progress()

        load_note = (
            "\nLoaded to project: {}".format(loaded_count)
            if load_to_project and saved_paths else "")
        cancelled_note = "\n\nExport was stopped early by user." if was_cancelled else ""
        forms.alert(
            "Export complete!\n\n"
            "Exported : {}\nFailed   : {}{}{}\n\n"
            "Output folder:\n{}".format(
                success, failed, load_note, cancelled_note, output_folder))

    # Template lookup
    def _find_template(self, cat_idx):
        """Locate the .rft family template for the chosen category."""
        _, template_names = CATEGORY_TEMPLATES[cat_idx]

        search_dirs = []

        # Revit-configured path (most reliable)
        try:
            tdir = app.FamilyTemplatePath
            if tdir and os.path.isdir(tdir):
                search_dirs.append(tdir)
        except Exception:
            pass

        # Standard fallback paths
        ver  = app.VersionNumber
        base = r"C:\ProgramData\Autodesk\RVT {}".format(ver)
        for sub in ("English", "", "English-Imperial", "English_I"):
            if sub:
                search_dirs.append(
                    os.path.join(base, "Family Templates", sub))
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
        """Locate the .rft template by category name string."""
        idx = next((i for i, (n, _) in enumerate(CATEGORY_TEMPLATES) if n == cat_name), 0)
        return self._find_template(idx)

    # Parametric reference planes (JSONtoFamily approach)
    def _find_family_views(self, fam_doc):
        """Return (plan_view, elev_view) from a family document, or (None, None)."""
        plan_view = None
        elev_view = None
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
                                 plan_view, elev_view,
                                 param_width_fp, param_height_fp):
        """Create reference planes + dimensions linked to Width/Height parameters.

        This mirrors the JSONtoFamily approach: reference planes are created at
        the geometry edges, then dimensioned and labelled so that changing
        Width/Height in the family drives the geometry.

        All failures are silenced so a bad template view never breaks export.
        """
        rp_left = rp_right = rp_top = None

        # Plan: Left / Right planes for Width
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
                        XYZ(-half_w * 1.5, 2, 0),
                        XYZ(half_w * 1.5, 2, 0))
                    dim = fam_doc.FamilyCreate.NewDimension(plan_view, dim_line, ref_arr)
                    if dim:
                        dim.FamilyLabel = param_width_fp
            except Exception:
                pass

        # Elevation: Top plane for Height
        if elev_view is not None:
            try:
                rp_top = fam_doc.FamilyCreate.NewReferencePlane(
                    XYZ(-3, 0, height), XYZ(3, 0, height), XYZ.BasisY, elev_view)
                rp_top.Name = "Top"

                if param_height_fp is not None:
                    # Find an existing Level / Ref-Level plane
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
                        dim_line = Line.CreateBound(
                            XYZ(0, 0, -0.1),
                            XYZ(0, 0, height + 0.1))
                        dim = fam_doc.FamilyCreate.NewDimension(elev_view, dim_line, ref_arr)
                        if dim:
                            dim.FamilyLabel = param_height_fp
            except Exception:
                pass

        return rp_left, rp_right, rp_top

    def _lock_faces_to_planes(self, fam_doc, solid_elem,
                               plan_view, elev_view,
                               rp_left, rp_right, rp_top):
        """Align + lock the extrusion side faces to the parametric reference planes."""
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

    # Window geometry (JSONtoFamily hollow-extrusion approach)
    def _create_window_body(self, fam_doc, sketch_plane, half_w, half_depth, height,
                            param_height_fp, param_material):
        """Create proper window geometry using the JSONtoFamily inner-loop technique.

        Frame  : hollow extrusion in plan (outer rect + inner rect as second loop).
                 The inner loop punches a hole, exactly like JSONtoFamily 'inner_loops'.
        Glass  : thin slab centred at Y=0, hidden in plan/ceiling views.

        Returns (frame_ext, glass_ext); glass_ext may be None on failure.
        """
        from Autodesk.Revit.DB import (
            FamilyElementVisibility, FamilyElementVisibilityType, BuiltInParameter,
        )

        FRAME_W = max(min(half_w * 0.12, 0.1312), 0.0492)   # 12 % of half-width, ~15-40 mm
        half_d  = max(half_depth, 0.2461)                     # min ~75 mm (wall reveal)

        # â”€â”€ helper: closed rectangle as CurveArray in the Z=0 plane â”€â”€
        def rect_loop(xmin, xmax, ymin, ymax):
            arr = CurveArray()
            pts = [XYZ(xmin, ymin, 0), XYZ(xmax, ymin, 0),
                   XYZ(xmax, ymax, 0), XYZ(xmin, ymax, 0)]
            for i in range(4):
                arr.Append(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
            return arr

        # Outer boundary
        outer = rect_loop(-half_w,           half_w,           -half_d, half_d)
        # Inner boundary â†’ punches hole through frame (JSONtoFamily inner_loops pattern)
        inner = rect_loop(-(half_w - FRAME_W), (half_w - FRAME_W),
                          -(half_d - FRAME_W), (half_d - FRAME_W))

        frame_profile = CurveArrArray()
        frame_profile.Append(outer)
        frame_profile.Append(inner)   # second loop = hollow centre

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

        # Glass pane: thin slab centred at depth mid-point
        # Tiny Y extent (â‰ˆ5 mm) in plan, extruded in Z from sill+FRAME_W to head-FRAME_W.
        # Hidden in plan/ceiling views â€“ model-only visibility (JSONtoFamily visible_param).
        glass_ext = None
        try:
            iw    = half_w - FRAME_W
            GLASS = 0.0082   # â‰ˆ2.5 mm half-thickness â†’ 5 mm total pane

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

    # Single-block export
    def _export_block(self, block_item, template_path, output_folder,
                      discipline_name, category_name, load_to_project=False,
                      mode_2d_only=False):
        """Create a .rfa family from a block's curves and save it.

        Returns the saved .rfa file path on success, or None on failure.

        For Door and Window categories the family is fully parametric:
        reference planes are created at the geometry edges and dimensioned
        with the Width / Height family parameters (JSONtoFamily technique).
        """
        curves = block_item._curves
        if not curves:
            return None

        # Create a fresh family document â€” assigned before try so finally can close it
        fam_doc = None
        fam_doc = app.NewFamilyDocument(template_path)
        try:
            # Bounding box via shared helper
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
                # Find or create a Z-up sketch plane
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
                        fam_doc,
                        Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))

                from Autodesk.Revit.DB import Transform

                if mode_2d_only:
                    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
                    # 2-D Model-Curve export
                    #
                    # Strategy:
                    #   1. Compute the median Z of all curve endpoints so even
                    #      slightly tilted 2-D DWGs are correctly flattened.
                    #   2. Translate every curve by (-cx, -cy, -median_z).
                    #   3. Rebuild each curve with forced Z=0 endpoints:
                    #        â€¢ Line  â†’ Line.CreateBound with Z=0 pts
                    #        â€¢ Arc   â†’ Arc.Create(start, end, ON-ARC mid)
                    #                  using Evaluate() for the true mid-point
                    #        â€¢ All other types (NurbSpline, HermiteSpline,
                    #          Ellipse, â€¦) â†’ tessellate â†’ write as Line segs
                    #   4. Any individual failure is logged at DEBUG and
                    #      skipped without aborting the whole block.
                    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

                    # â”€â”€ 1. Median Z across all endpoints (robust for DWGs
                    #       with minor Z variation) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

                    # â”€â”€ 2. Helper: write one projected line segment â”€â”€â”€â”€â”€â”€â”€â”€
                    def _seg(pa, pb):
                        """Project pa/pb to Z=0, create Line, call NewModelCurve.
                        Returns True on success."""
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

                    # â”€â”€ 3. Write each curve â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                    ok_2d = fail_2d = 0

                    for curve in curves:
                        try:
                            new_c = curve.CreateTransformed(translator)

                            if isinstance(new_c, Line):
                                # Äoáº¡n tháº³ng: váº½ trá»±c tiáº¿p
                                p0 = new_c.GetEndPoint(0)
                                p1 = new_c.GetEndPoint(1)
                                if _seg(p0, p1):
                                    ok_2d += 1
                                else:
                                    fail_2d += 1
                            else:
                                # Bá»” SUNG Tá»ª SCRIPT_TEST: 
                                # Má»i Ä‘Æ°á»ng cong phá»©c táº¡p (Arc, Spline, Ellipse...) Ä‘á»u báº» thÃ nh Ä‘oáº¡n tháº³ng
                                written = False
                                pts = new_c.Tessellate()
                                
                                for i in range(len(pts) - 1):
                                    if _seg(pts[i], pts[i + 1]):
                                        written = True
                                
                                # QUAN TRá»ŒNG: Ná»‘i Ä‘iá»ƒm cuá»‘i vá»›i Ä‘iá»ƒm Ä‘áº§u náº¿u lÃ  Ä‘Æ°á»ng khÃ©p kÃ­n (nhÆ° hÃ¬nh trÃ²n)
                                if not new_c.IsBound:
                                    if _seg(pts[-1], pts[0]):
                                        written = True

                                if written:
                                    ok_2d += 1
                                else:
                                    fail_2d += 1

                        except Exception:
                            logger.debug("2D curve skip [{}]: {}".format(
                                type(curve).__name__, traceback.format_exc()))
                            fail_2d += 1

                    logger.info("2D '{}': {} ok / {} skipped  (cz={:.4f} ft)".format(
                        block_item.BlockName, ok_2d, fail_2d, cz))

                    logger.info("2D '{}': {} ok / {} skipped  (cz={:.4f} ft)".format(
                        block_item.BlockName, ok_2d, fail_2d, cz))
                    if ok_2d == 0 and curves:
                        logger.warning(
                            "2D '{}': ZERO curves written â€“ all {} curves failed. "
                            "cz={:.4f} ft. Check CAD import elevation.".format(
                                block_item.BlockName, len(curves), cz))
                else:
                    from Autodesk.Revit.DB import (
                        FamilyElementVisibility, FamilyElementVisibilityType,
                        GraphicsStyleType, BuiltInParameter,
                    )

                    THICKNESS      = 0.1312
                    HEIGHT         = 7.2178   # â‰ˆ2195 mm â€“ default door height
                    WINDOW_HEIGHT  = 4.9213   # â‰ˆ1500 mm â€“ default window height
                    extrusion_depth = HEIGHT if is_door else (WINDOW_HEIGHT if is_window else 1.0)

                    # â”€â”€ Door subcategories â”€â”€
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

                    # â”€â”€ Family parameters â”€â”€
                    param_height_fp = param_width_fp = param_material = None
                    try:
                        fam_mgr = fam_doc.FamilyManager
                        for param in fam_mgr.Parameters:
                            pname = param.Definition.Name.lower()
                            if pname in ("height", "chiá»u cao"):
                                fam_mgr.Set(param, extrusion_depth)
                                param_height_fp = param
                            elif pname in ("width", "chiá»u rá»™ng"):
                                if door_width:
                                    fam_mgr.Set(param, door_width)
                                param_width_fp = param
                            elif pname in ("depth", "chiá»u sÃ¢u", "length", "chiá»u dÃ i"):
                                if not is_door and half_h * 2.0 > 0.01:
                                    fam_mgr.Set(param, half_h * 2.0)
                            elif pname in ("material", "váº­t liá»‡u"):
                                param_material = param
                    except Exception:
                        pass

                    # Geometry
                    ext_box = None

                    if is_window:
                        # Window: hollow frame + glass pane via JSONtoFamily inner-loop approach.
                        # _create_window_body() uses CurveArrArray with outer + inner loops so the
                        # centre is cut away (same as JSONtoFamily 'inner_loops' on an Extrusion).
                        window_frame_ext, _window_glass = self._create_window_body(
                            fam_doc, sketch_plane,
                            half_w, half_h, extrusion_depth,
                            param_height_fp, param_material)
                        ext_box = window_frame_ext   # used below for face-locking

                    elif not is_door:
                        # Bounding-rectangle extrusion (solid base)
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

                        # Model lines on top surface (DWGFamilyHelpers approach)
                        # Uses _dwg_project_curve() which:
                        #   â€¢ Line  â†’ rebuild endpoints at (X-cx, Y-cy, extrusion_depth)
                        #   â€¢ Arc   â†’ Arc.Create(center, r, t0, t1, BasisX, BasisY)
                        #             always horizontal in family space, ignores world Z
                        #   â€¢ Other â†’ Line between translated endpoints (safe fallback)
                        # This correctly handles DWG imports at any Level elevation
                        # without needing to compute or subtract a world-Z offset.
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
                                # Tessellate fallback for curves rejected by Revit
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

                        logger.info("3D '{}': {} ok / {} skipped on top face".format(
                            block_item.BlockName, ok_3d, fail_3d))

                        # Fallback: embed source DWG if too many lines lost
                        threshold = max(5, int(len(curves) * MIN_CURVE_RATIO))
                        if ok_3d < threshold:
                            logger.warning(
                                "3D '{}': only {}/{} curves written (threshold {}). "
                                "Trying DWG embed fallback...".format(
                                    block_item.BlockName, ok_3d, len(curves), threshold))
                            self._embed_dwg_into_family(
                                fam_doc, block_item._import_inst, cx, cy)

                    # Door: symbolic lines + panel extrusion
                    panel_ext = None
                    for curve in curves:
                        if not is_door:
                            break   # non-door handled above; skip this loop
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

                                # 3D panel extrusion in the "closed" direction
                                ctr = curve.Center
                                nc  = ctr + XYZ(-cx, -cy, 0.0)

                                p0_orig = curve.GetEndPoint(0)
                                p1_orig = curve.GetEndPoint(1)
                                p_closed_orig = (
                                    p0_orig if abs(p0_orig.Y - ctr.Y) < abs(p1_orig.Y - ctr.Y)
                                    else p1_orig)
                                np_closed = p_closed_orig + XYZ(-cx, -cy, 0.0)

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

                    # Parametric reference planes + dimensions (JSONtoFamily approach)
                    # Applied to Door and Window so Width/Height params drive the geometry.
                    if is_door or is_window:
                        fam_doc.Regenerate()
                        plan_view, elev_view = self._find_family_views(fam_doc)
                        rp_left, rp_right, rp_top = self._create_parametric_refs(
                            fam_doc,
                            half_w if not is_door else (door_width / 2.0 if door_width else half_w),
                            HEIGHT if is_door else extrusion_depth,
                            plan_view, elev_view,
                            param_width_fp, param_height_fp)

                        # Lock geometry faces to the new reference planes
                        fam_doc.Regenerate()
                        target_solid = panel_ext if is_door else ext_box
                        if target_solid and (rp_left or rp_right or rp_top):
                            self._lock_faces_to_planes(
                                fam_doc, target_solid,
                                plan_view, elev_view,
                                rp_left, rp_right, rp_top)

                t.Commit()
            except Exception:
                try:
                    t.RollBack()
                except Exception:
                    pass
                raise

            # Save .rfa to output folder
            safe_cad_name = block_item.BlockName.strip() or "Family"
            w_str = getattr(block_item, 'WidthMM', '-')
            d_str = getattr(block_item, 'DepthMM', '-')
            dim_suffix = "_{}x{}".format(w_str, d_str) if (w_str != '-' and d_str != '-') else ""
            base_name = "T3Lab_{}_{}{}" .format(
                category_name.replace(" ", "_"),
                safe_cad_name.replace(" ", "_"),
                dim_suffix)
            base_name = re.sub(r'[\\/*?:"<>|]', "", base_name)

            save_path = os.path.join(output_folder, "{}.rfa".format(base_name))
            counter = 1
            while os.path.exists(save_path):
                save_path = os.path.join(output_folder, "{}_{}.rfa".format(base_name, counter))
                counter += 1

            opts = SaveAsOptions()
            opts.OverwriteExistingFile = True
            fam_doc.SaveAs(save_path, opts)

            logger.info("Exported: {}".format(save_path))

            # Load to Project (absorbed from DWGtoFamily)
            if load_to_project:
                try:
                    t_load = Transaction(doc, 'Load Family - {}'.format(safe_cad_name))
                    start_transaction(t_load)
                    try:
                        doc.LoadFamily(save_path)
                        t_load.Commit()
                        logger.info("Loaded to project: {}".format(safe_cad_name))
                    except Exception:
                        try:
                            t_load.RollBack()
                        except Exception:
                            pass
                        logger.warning("Could not load family to project: {}".format(save_path))
                except Exception:
                    logger.warning("Load-to-project transaction failed: {}".format(
                        traceback.format_exc()))

            return save_path
        finally:
            if fam_doc is not None:
                try:
                    fam_doc.Close(False)
                except Exception:
                    pass



    # Unified Preset Generator (no CAD)
    def presets_clicked(self, sender, e):
        """Generate families from presets for the currently selected category."""
        output_folder = self.output_path.Text
        if not output_folder or not os.path.isdir(output_folder):
            forms.alert("Please select a valid output folder (Browse...) first.")
            return

        cat_name = self.category_combo.SelectedItem
        if cat_name is None:
            forms.alert("Please select a category from the dropdown first.")
            return

        if cat_name not in CATEGORY_PRESETS:
            forms.alert("No presets available for category: {}".format(cat_name))
            return

        mode, preset_list = CATEGORY_PRESETS[cat_name]
        labels = [p[0] for p in preset_list]

        selected_labels = forms.SelectFromList.show(
            labels,
            title="T3Lab \u2013 {} Presets".format(cat_name),
            multiselect=True,
            button_name="Generate")
        if not selected_labels:
            return

        selected = [p for p in preset_list if p[0] in selected_labels]
        load_to_project = (self.chk_load_to_project.IsChecked == True)

        # Resolve template index from CATEGORY_TEMPLATES
        cat_idx = next(
            (i for i, (n, _) in enumerate(CATEGORY_TEMPLATES) if n == cat_name), 0)
        template_path = self._find_template(cat_idx)
        if not template_path:
            forms.alert(
                "Template (.rft) for '{}' not found.\n"
                "Please install Revit family templates.".format(cat_name))
            return

        ok_count = fail_count = 0
        self._cancel_requested = False
        self._pause_requested  = False
        self._update_progress(0, len(selected))
        for i, preset in enumerate(selected):
            if self._cancel_requested:
                break
            self._update_status("[{}/{}] Generating: {}".format(i + 1, len(selected), preset[0]))
            self._update_progress(i, len(selected))
            if self._cancel_requested:
                break
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
                logger.error("Preset error: {}\n{}".format(
                    preset[0], traceback.format_exc()))
                fail_count += 1
            self._update_progress(i + 1, len(selected))

        was_cancelled = self._cancel_requested
        if was_cancelled:
            self._update_status("Stopped: {} ok, {} failed".format(ok_count, fail_count))
        else:
            self._update_status("Done: {} ok, {} failed".format(ok_count, fail_count))
        self._hide_progress()
        forms.alert(
            "{} Preset Export\n\n"
            "Generated: {}\nFailed: {}\n\n"
            "Folder: {}".format(cat_name, ok_count, fail_count, output_folder))

    def _generate_window_from_preset(self, preset, template_path, output_folder, load_to_project):
        """Build a parametric window .rfa from preset sizes. No CAD required."""
        label, width_mm, height_mm = preset

        half_w  = (width_mm  / 2.0) * SCL
        height  = height_mm          * SCL
        half_d  = 0.3937             # ~120 mm default wall reveal depth

        fam_doc = app.NewFamilyDocument(template_path)
        t = Transaction(fam_doc, "T3Lab Window \u2013 " + label)
        start_transaction(t)
        try:
            # Find sketch plane (Z-up)
            sketch_plane = None
            for sp in FilteredElementCollector(fam_doc).OfClass(SketchPlane):
                try:
                    n = sp.GetPlane().Normal
                    if abs(n.Z - 1.0) < 0.001:
                        sketch_plane = sp
                        break
                except Exception:
                    pass
            if not sketch_plane:
                sketch_plane = SketchPlane.Create(
                    fam_doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))

            # Set Width / Height family parameters
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

            # Window geometry (hollow frame + glass)
            self._create_window_body(
                fam_doc, sketch_plane, half_w, half_d, height,
                param_height_fp, param_material)

            # Parametric reference planes
            fam_doc.Regenerate()
            plan_view, elev_view = self._find_family_views(fam_doc)
            self._create_parametric_refs(
                fam_doc, half_w, height, plan_view, elev_view,
                param_width_fp, param_height_fp)

            t.Commit()
        except Exception:
            try: t.RollBack()
            except Exception: pass
            fam_doc.Close(False)
            raise

        # Save
        safe = re.sub(r'[/*?:"<>|]', "_", label)
        save_path = os.path.join(output_folder, "T3Lab_Window_{}.rfa".format(safe))
        ctr = 1
        while os.path.exists(save_path):
            save_path = os.path.join(
                output_folder, "T3Lab_Window_{}_{}.rfa".format(safe, ctr))
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
                t2 = Transaction(doc, "Load " + label)
                start_transaction(t2)
                try: doc.LoadFamily(save_path); t2.Commit()
                except Exception:
                    try: t2.RollBack()
                    except Exception: pass
            except Exception:
                pass

        return True

    def _generate_generic_from_preset(self, preset, template_path, output_folder,
                                       category_name, load_to_project):
        """Build a simple solid-box .rfa from (label, W, D, H) preset. No CAD required."""
        label, w_mm, d_mm, h_mm = preset

        w = w_mm * SCL
        d = d_mm * SCL
        h = h_mm * SCL

        fam_doc = app.NewFamilyDocument(template_path)
        t = Transaction(fam_doc, "T3Lab {} \u2013 {}".format(category_name, label))
        start_transaction(t)
        try:
            # Sketch plane (Z-up)
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

            # Try to set Width / Depth / Height family parameters
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

            # Solid box extrusion centred at origin in plan
            half_w = w / 2.0
            half_d = d / 2.0
            arr = CurveArray()
            pts = [XYZ(-half_w, -half_d, 0), XYZ( half_w, -half_d, 0),
                   XYZ( half_w,  half_d, 0), XYZ(-half_w,  half_d, 0)]
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

        # Save
        cat_prefix = re.sub(r'\s+', '_', category_name)
        safe = re.sub(r'[/*?:"<>|]', "_", label)
        save_path = os.path.join(
            output_folder, "T3Lab_{}_{}.rfa".format(cat_prefix, safe))
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
                t2 = Transaction(doc, "Load " + label)
                start_transaction(t2)
                try: doc.LoadFamily(save_path); t2.Commit()
                except Exception:
                    try: t2.RollBack()
                    except Exception: pass
            except Exception:
                pass

        return True

    # Door Preset Generator (no CAD)
    def door_presets_clicked(self, sender, e):
        """Generate parametric door families from preset sizes. No CAD file needed."""
        output_folder = self.output_path.Text
        if not output_folder or not os.path.isdir(output_folder):
            forms.alert("Please select a valid output folder (Browse...) first.")
            return

        labels = [p[0] for p in DOOR_PRESETS]
        selected_labels = forms.SelectFromList.show(
            labels,
            title="T3Lab - Select Door Types to Generate",
            multiselect=True,
            button_name="Generate")
        if not selected_labels:
            return

        selected = [p for p in DOOR_PRESETS if p[0] in selected_labels]
        load_to_project = (self.chk_load_to_project.IsChecked == True)

        door_template = self._find_template(1)   # index 1 = "Door" in CATEGORY_TEMPLATES
        if not door_template:
            forms.alert("Door template (.rft) not found. Install Revit family templates.")
            return

        ok_count = fail_count = 0
        for preset in selected:
            self._update_status("Generating: " + preset[0])
            try:
                if self._generate_door_from_preset(preset, door_template, output_folder, load_to_project):
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception:
                logger.error("Door preset error: {}\n{}".format(preset[0], traceback.format_exc()))
                fail_count += 1

        self._update_status("Done: {} ok, {} failed".format(ok_count, fail_count))
        forms.alert(
            "Door Preset Export\n\n"
            "Generated: {}\nFailed: {}\n\n"
            "Folder: {}".format(ok_count, fail_count, output_folder))

    def _generate_door_from_preset(self, preset, template_path, output_folder, load_to_project):
        """Build a parametric door .rfa purely from geometry (no CAD).

        Frame geometry (tested & working):
          - Uses the exterior sketch plane (normal = -Y, at origin) found in Metric Door.rft.
          - 3 separate rectangular extrusions per side:
              left jamb  : x[-half_fw..-half_w], full height including head
              right jamb : x[half_w..half_fw],   full height including head
              top head   : x[-half_fw..half_fw],  z[door_h..total_h]
          - positive depth  => exterior projection (depth = proj_ext_mm)
          - negative depth  => interior projection (depth = -proj_int_mm)
        Leaf geometry:
          - Plan sketch plane (normal = +Z)
          - Rectangle profile in XY, extruded in Z to full door height
          - Single: one leaf; Double: two leaves with 1mm gap
        """
        label, width_mm, height_mm, frame_w_mm, proj_ext_mm, proj_int_mm, leaf_t_mm, door_count = preset

        half_w   = (width_mm  / 2.0) * SCL
        h        = height_mm         * SCL
        fw       = frame_w_mm        * SCL
        fpe      = proj_ext_mm       * SCL
        fpi      = proj_int_mm       * SCL
        dt       = leaf_t_mm         * SCL
        half_fw  = half_w + fw
        total_fh = h + fw

        fam_doc = app.NewFamilyDocument(template_path)
        t = Transaction(fam_doc, "T3Lab Door - " + label)
        start_transaction(t)
        try:
            # Find sketch planes from template
            plan_sp = elev_sp = None
            for sp in FilteredElementCollector(fam_doc).OfClass(SketchPlane):
                n, org = sp.GetPlane().Normal, sp.GetPlane().Origin
                if abs(n.Z - 1.0) < 0.001 and abs(org.X) < 0.01 and abs(org.Y) < 0.01 and plan_sp is None:
                    plan_sp = sp
                if abs(n.Y + 1.0) < 0.001 and abs(org.X) < 0.01 and abs(org.Y) < 0.01 and elev_sp is None:
                    elev_sp = sp

            if plan_sp is None:
                plan_sp = SketchPlane.Create(fam_doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))
            if elev_sp is None:
                elev_sp = SketchPlane.Create(fam_doc, Plane.CreateByNormalAndOrigin(XYZ(0.0,-1.0,0.0), XYZ.Zero))

            # Set Width / Height parameters
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

            # Subcategories
            frame_gs = leaf_gs = None
            try:
                from Autodesk.Revit.DB import GraphicsStyleType
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

            # Helpers
            def _extrude_xz(x0, x1, z0, z1, depth, gs=None):
                """Rect profile in XZ plane, extruded in Y (+depth=ext, -depth=int)."""
                arr = CurveArray()
                arr.Append(Line.CreateBound(XYZ(x0,0,z0), XYZ(x1,0,z0)))
                arr.Append(Line.CreateBound(XYZ(x1,0,z0), XYZ(x1,0,z1)))
                arr.Append(Line.CreateBound(XYZ(x1,0,z1), XYZ(x0,0,z1)))
                arr.Append(Line.CreateBound(XYZ(x0,0,z1), XYZ(x0,0,z0)))
                prof = CurveArrArray()
                prof.Append(arr)
                ext = fam_doc.FamilyCreate.NewExtrusion(True, prof, elev_sp, depth)
                if gs:
                    try: ext.Subcategory = gs
                    except Exception: pass
                return ext

            def _extrude_xy_leaf(x0, x1, y0, y1):
                """Leaf profile in XY plan, extruded in Z to full door height."""
                arr = CurveArray()
                pts = [XYZ(x0,y0,0), XYZ(x1,y0,0), XYZ(x1,y1,0), XYZ(x0,y1,0)]
                for i in range(4): arr.Append(Line.CreateBound(pts[i], pts[(i+1)%4]))
                prof = CurveArrArray()
                prof.Append(arr)
                ext = fam_doc.FamilyCreate.NewExtrusion(True, prof, plan_sp, h)
                if leaf_gs:
                    try: ext.Subcategory = leaf_gs
                    except Exception: pass
                try:
                    if param_height_fp:
                        from Autodesk.Revit.DB import BuiltInParameter
                        ep = ext.get_Parameter(BuiltInParameter.EXTRUSION_END_PARAM)
                        if ep: fam_doc.FamilyManager.AssociateElementParameterToFamilyParameter(ep, param_height_fp)
                except Exception:
                    pass
                return ext

            # Frame pieces â€” 3 per side (left jamb, right jamb, top head)
            frame_pieces = [
                (-half_fw, -half_w, 0.0, total_fh),   # left jamb (full height incl. head)
                ( half_w,  half_fw, 0.0, total_fh),   # right jamb
                (-half_fw,  half_fw, h,   total_fh),   # top head only
            ]
            for x0, x1, z0, z1 in frame_pieces:
                _extrude_xz(x0, x1, z0, z1,  fpe, frame_gs)   # exterior
                _extrude_xz(x0, x1, z0, z1, -fpi, frame_gs)   # interior

            # Door leaf(ves)
            GAP = 0.00328   # 1 mm in feet
            if door_count == 1:
                _extrude_xy_leaf(-half_w, half_w, 0.0, dt)
            else:
                _extrude_xy_leaf(-half_w, -GAP/2.0, 0.0, dt)
                _extrude_xy_leaf( GAP/2.0,  half_w, 0.0, dt)

            # Parametric reference planes
            fam_doc.Regenerate()
            plan_view, elev_view = self._find_family_views(fam_doc)
            self._create_parametric_refs(
                fam_doc, half_w, h, plan_view, elev_view,
                param_width_fp, param_height_fp)

            t.Commit()
        except Exception:
            try: t.RollBack()
            except Exception: pass
            fam_doc.Close(False)
            raise

        # Save
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
                t2 = Transaction(doc, "Load " + label)
                start_transaction(t2)
                try: doc.LoadFamily(save_path); t2.Commit()
                except Exception:
                    try: t2.RollBack()
                    except Exception: pass
            except Exception:
                pass

        return True


    # Export & Place

    def export_and_place_clicked(self, sender, e):
        """Export each selected block as .rfa then place every CAD instance
        at its exact world-space position in the active Revit view."""
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

        load_to_project = True   # placement always requires loading

        # â”€â”€ Resolve active level for placement â”€â”€
        place_level = None
        try:
            place_level = doc.GetElement(uidoc.ActiveView.GenLevel.Id)
        except Exception:
            pass
        if not place_level:
            for lv in FilteredElementCollector(doc).OfClass(DB.Level):
                place_level = lv
                break

        # Pre-read UI flag once
        mode_2d = False
        try:
            mode_2d = bool(getattr(self, 'rb_2d_lines', None) and self.rb_2d_lines.IsChecked)
        except Exception:
            pass

        exported, placed_total, failed = 0, 0, 0

        self._cancel_requested = False
        self._pause_requested  = False
        self._update_progress(0, len(selected))
        t_place = Transaction(doc, "T3Lab â€“ Export & Place Families")
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
                        write_log("No template for '{}', skipping '{}'".format(
                            category_name, item.BlockName), "WARN")
                        failed += 1
                        self._update_progress(i + 1, len(selected))
                        continue
                    save_path = self._export_block(
                        item, template_path, output_folder,
                        discipline_name, category_name,
                        load_to_project=False,
                        mode_2d_only=mode_2d)
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
            logger.error("Export & Place transaction failed:\n{}".format(
                traceback.format_exc()))
            forms.alert("Transaction failed â€“ check the pyRevit log.")
            return

        was_cancelled = self._cancel_requested
        if was_cancelled:
            self._update_status(
                "Stopped: {} exported, {} instances placed, {} failed".format(
                    exported, placed_total, failed))
        else:
            self._update_status(
                "Done: {} exported, {} instances placed, {} failed".format(
                    exported, placed_total, failed))
        self._hide_progress()
        forms.alert(
            "Export & Place complete!\n\n"
            "Families exported : {}\n"
            "Instances placed  : {}\n"
            "Failed            : {}\n\n"
            "Output folder:\n{}".format(exported, placed_total, failed, output_folder))

    def _place_family_instances(self, rfa_path, block_item, level):
        """Load an .rfa and place one FamilyInstance per stored placement.

        Each placement is (XYZ centroid, float angle_rad) captured at scan time
        from the world-space bounding box centroid of each CAD block instance.

        Returns the number of instances successfully placed.
        """
        from Autodesk.Revit.DB import ElementTransformUtils
        from Autodesk.Revit.DB.Structure import StructuralType

        if not block_item._placements:
            return 0

        # Load or find the family
        family = None
        try:
            loaded_ref = clr.Reference[DB.Family]()
            if doc.LoadFamily(rfa_path, loaded_ref):
                family = loaded_ref.Value
        except Exception:
            pass

        if not family:
            # Family may already be loaded; match by file stem
            stem = os.path.splitext(os.path.basename(rfa_path))[0]
            for f in FilteredElementCollector(doc).OfClass(DB.Family):
                if f.Name == stem:
                    family = f
                    break

        if not family:
            logger.warning("Could not load family: {}".format(rfa_path))
            return 0

        # Activate first symbol
        symbol = None
        for sid in family.GetFamilySymbolIds():
            symbol = doc.GetElement(sid)
            break

        if not symbol:
            return 0

        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()

        # Place one instance per recorded CAD position
        placed = 0
        for (centroid, angle) in block_item._placements:
            try:
                # Use the Z from the level elevation so the family sits on the level
                z = level.Elevation if level else centroid.Z
                pt = XYZ(centroid.X, centroid.Y, z)

                inst = doc.Create.NewFamilyInstance(
                    pt, symbol, level, StructuralType.NonStructural)

                # Apply rotation if the block was rotated in the CAD drawing
                if inst and abs(angle) > 0.001:
                    axis = DB.Line.CreateBound(pt, XYZ(pt.X, pt.Y, pt.Z + 1.0))
                    ElementTransformUtils.RotateElement(doc, inst.Id, axis, angle)

                placed += 1
            except Exception:
                logger.warning("Could not place instance of '{}': {}".format(
                    block_item.BlockName, traceback.format_exc()))

        return placed

    def _batch_load_families(self, save_paths):
        """Load a list of .rfa files into the project, one transaction each.

        Returns the count of successfully loaded families.
        Individual failures are logged and skipped â€” one bad file
        will not abort the rest of the batch.
        """
        loaded = 0
        total  = len(save_paths)
        for i, path in enumerate(save_paths):
            self._update_status(
                "Loading [{}/{}]: {}".format(i + 1, total, os.path.basename(path)))
            try:
                t = Transaction(doc, "T3Lab â€“ Load {}".format(
                    os.path.splitext(os.path.basename(path))[0]))
                start_transaction(t)
                try:
                    doc.LoadFamily(path)
                    t.Commit()
                    loaded += 1
                except Exception:
                    try:
                        t.RollBack()
                    except Exception:
                        pass
                    logger.warning("Could not load: {}".format(path))
            except Exception:
                logger.warning("Transaction failed for: {}\n{}".format(
                    path, traceback.format_exc()))
        return loaded

    # Window chrome handlers

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


# MAIN SCRIPT
# ==================================================
if __name__ == '__main__':
    try:
        window = BulkFamilyExportWindow()
        window.ShowDialog()
    except Exception:
        logger.error(traceback.format_exc())
        forms.alert("Unexpected error. Check the pyRevit log for details.")
