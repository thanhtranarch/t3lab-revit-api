# -*- coding: utf-8 -*-
"""
Annotation Manager
------------------
Unified tool combining Dimension and Text Note management:
  - Find elements by keyword → jump to view
  - Delete selected instances / types
  - Double-click Name cell to rename inline (types and text note content)
  - Auto-rename all types based on their properties
  - Collaborative tool by T3Lab & Dang Quoc Truong.

--------------------------------------------------------
Author: Tran Tien Thanh & Dang Quoc Truong
--------------------------------------------------------
"""

__title__   = "Annotation Manager"
__author__  = "Tran Tien Thanh & Dang Quoc Truong"
__version__ = "1.1.0"

# IMPORT LIBRARIES
# ==================================================
import os
import re
import sys
import clr
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('System')
clr.AddReference('System.Data')

from System.Windows import Visibility, WindowState
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind
from System.Data import DataTable
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Dimension, DimensionType,
    TextNote, TextNoteType,
    Transaction, ElementId,
    BuiltInParameter,
)
from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow

# Path setup
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)
if os.path.dirname(__file__) not in sys.path:
    sys.path.append(os.path.dirname(__file__))

try:
    from GUI import DimTextDialog
    from GUI import CopyAnnotationDialog
except Exception:
    import DimTextDialog
    import CopyAnnotationDialog
import Utils.UpperAll as UpperAll
import Utils.RenumberAlongSpline as RenumberAlongSpline

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()
# CPython has no ScriptOutput.GetDefault; safe_output()
# returns a no-op window instead of killing the tool.
try:
    from _cpython_bootstrap import safe_output
    output = safe_output()
except Exception:
    output = script.get_output()
# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
try:
    from Snippets._host import resolve_doc, resolve_uidoc, get_revit_version
except ImportError:
    try:
        import importlib
        import Snippets._host
        importlib.reload(Snippets._host)
        from Snippets._host import resolve_doc, resolve_uidoc, get_revit_version
    except Exception:
        from Snippets._host import resolve_doc, get_revit_version
        def resolve_uidoc(candidate=None):
            if candidate is not None and hasattr(candidate, 'Document') and candidate.Document is not None:
                return candidate
            try:
                from pyrevit import revit
                return revit.uidoc
            except Exception:
                return None

try:
    doc = resolve_doc(getattr(revit, 'doc', None)).doc
except Exception:
    doc = None
try:
    uidoc = resolve_uidoc(getattr(revit, 'uidoc', None))
except Exception:
    uidoc = None
try:
    REVIT_VERSION = get_revit_version(doc)
except Exception:
    REVIT_VERSION = 2024

# ============================================================
# NAMING STRUCTURE CONFIGURATION — ISO 19650 COMPLIANT
# ============================================================
# Format: {Discipline}_{TypeIndicator}_{Size}_{Font}_{WidthScale}_{Background}_[optional fields]
# Separator: '_' per ISO 19650-2:2018 Annex A field delimiter convention.
# Unused tokens (value is None or empty/"N/A") are omitted automatically.
NAMING_TEMPLATES = {
    "Dimension": {
        "Separator": "_",
        "Fields": [
            "Discipline",      # "ARC" or "STR"
            "TypeIndicator",   # Always "DIM"
            "Size",            # E.g. "2.50mm"
            "Font",            # E.g. "Arial"
            "WidthScale",      # E.g. "0.7"
            "Background",      # "Transparent" or "Opaque"
            "Color",           # E.g. "Red" (omitted if Black)
            "CenterSymbol",    # "Center" if center-line symbol present
            "PrefixText",      # Prefix parameter of Dimension
            "ElevationText",   # Elevation indicators
            "Rounding"         # "Round1" or "Round0.1"
        ]
    },
    "TextNote": {
        "Separator": "_",
        "Fields": [
            "Discipline",      # "ARC" or "STR"
            "TypeIndicator",   # Always "TXT"
            "Size",            # E.g. "2.50mm"
            "Font",            # E.g. "Arial"
            "WidthScale",      # E.g. "0.7"
            "Background",      # "Transparent" or "Opaque"
            "Color",           # E.g. "Red" (omitted if Black)
            "Border",          # "Border" if text box visible
            "TextStyles"       # Bold/Underline/Italic → "BUI"
        ]
    }
}

# ============================================================
# SHARED COLOR TABLE
# ============================================================
_DIM_COLORS = {
    (255,128,128):"Light Coral", (255,255,128):"Light Yellow", (128,255,128):"Pale Green",
    (128,255,255):"Pale Cyan", (128,128,255):"Light Slate Blue", (255,128,255):"Orchid",
    (255,0,0):"Red", (255,255,0):"Yellow", (0,255,0):"Lime", (0,255,255):"Cyan",
    (0,0,255):"Blue", (255,0,255):"Magenta", (128,64,64):"Brown", (255,192,128):"Light Salmon",
    (128,255,192):"Aquamarine", (192,192,255):"Lavender", (192,128,255):"Medium Orchid",
    (128,0,0):"Maroon", (255,128,0):"Orange", (0,128,0):"Green", (0,128,128):"Teal",
    (0,0,128):"Navy", (128,0,128):"Purple", (128,64,0):"Saddle Brown", (192,128,64):"Peru",
    (0,128,64):"Dark Sea Green", (0,128,192):"Steel Blue", (64,128,255):"Dodger Blue",
    (128,0,192):"Dark Orchid", (0,0,0):"Black", (128,128,0):"Olive", (128,128,128):"Gray",
    (0,192,192):"Medium Turquoise", (192,192,192):"Silver", (255,255,255):"White",
    (70,70,70):"Gray70", (128,0,64):"Dark Raspberry", (77,77,77):"Gray77",
}
_TXT_COLORS = _DIM_COLORS

def _rgb(color_int):
    return (color_int & 255, (color_int >> 8) & 255, (color_int >> 16) & 255)

def _sanitize(v):
    if not v:
        return "N/A"
    return re.sub(r'[\\/:?"<>|=]', '', v).strip() or "N/A"

def _mm(param):
    return "{:.2f}mm".format(round(param.AsDouble() * 304.8, 2))

# ============================================================
# DIMENSION RENAME HELPERS
# ============================================================
def _dim_name(dt, origin):
    def gp(bip):
        try: return dt.get_Parameter(bip)
        except: return None

    discipline = "STR" if "STR" in origin.upper() else "ARC"
    p = gp(BuiltInParameter.TEXT_SIZE)
    size  = _mm(p) if p else "N/A"
    p = gp(BuiltInParameter.TEXT_FONT)
    font  = p.AsString() if p else "N/A"
    p = gp(BuiltInParameter.TEXT_WIDTH_SCALE)
    factor = "{:.2f}".format(p.AsDouble()).rstrip('0').rstrip('.') if p else "N/A"
    p = gp(BuiltInParameter.DIM_TEXT_BACKGROUND)
    bg    = p.AsValueString() if p else "N/A"
    p = gp(BuiltInParameter.LINE_COLOR)
    color = _DIM_COLORS.get(_rgb(p.AsInteger()), "RGB") if p else "N/A"
    p = gp(BuiltInParameter.DIM_PREFIX)
    pref  = _sanitize(p.AsString()) if p else "N/A"
    p = gp(BuiltInParameter.DIM_STYLE_CENTERLINE_SYMBOL)
    ctr   = "Center" if (p and p.AsElementId() != ElementId.InvalidElementId) else "N/A"
    p = gp(BuiltInParameter.SPOT_ELEV_IND_ELEVATION)
    elev  = _sanitize(p.AsString()) if p else "N/A"
    p = gp(BuiltInParameter.SPOT_ELEV_IND_TOP)
    top   = _sanitize(p.AsString()) if p else "N/A"
    p = gp(BuiltInParameter.SPOT_ELEV_IND_BOTTOM)
    bot   = _sanitize(p.AsString()) if p else "N/A"

    # Extract custom rounding if available
    rounding_str = ""
    try:
        if hasattr(dt, "GetUnitsFormatOptions"):
            opts = dt.GetUnitsFormatOptions()
            if opts and not opts.UseDefault:
                acc = opts.Accuracy
                if acc != 1 and acc != 1.0:
                    acc_str = "{:.12f}".format(acc).rstrip('0').rstrip('.')
                    rounding_str = "Round{}".format(acc_str)
    except Exception:
        pass

    # Map variable values to structure configuration fields
    field_values = {
        "Discipline": discipline,
        "TypeIndicator": "DIM",
        "Size": size,
        "Font": font,
        "WidthScale": factor,
        "Background": bg,
        "Color": color if color != "Black" else "N/A",
        "CenterSymbol": ctr,
        "PrefixText": pref,
        "ElevationText": elev if elev != "N/A" else (top if top != "N/A" else (bot if bot != "N/A" else "N/A")),
        "Rounding": rounding_str or "N/A"
    }

    # Construct naming parts based on defined Field structure
    parts = []
    for field in NAMING_TEMPLATES["Dimension"]["Fields"]:
        val = field_values.get(field, "N/A")
        if val != "N/A" and val:
            parts.append(val)

    return NAMING_TEMPLATES["Dimension"]["Separator"].join(parts)

# ============================================================
# TEXTNOTE RENAME HELPERS
# ============================================================
def _txt_name(tt, origin):
    def gp(bip):
        try: return tt.get_Parameter(bip)
        except: return None

    discipline = "STR" if "STR" in origin.upper() else "ARC"
    p = gp(BuiltInParameter.TEXT_SIZE)
    size   = _mm(p) if p else "N/A"
    p = gp(BuiltInParameter.TEXT_FONT)
    font   = p.AsString().replace(" ", "") if p else "N/A"
    p = gp(BuiltInParameter.TEXT_BACKGROUND)
    bg     = ("Opaque" if p.AsInteger() == 0 else "Transparent") if p else "N/A"
    p = gp(BuiltInParameter.TEXT_WIDTH_SCALE)
    factor = "{:.2f}".format(p.AsDouble()).rstrip('0').rstrip('.') if p else "N/A"
    p = gp(BuiltInParameter.LINE_COLOR)
    color  = _TXT_COLORS.get(_rgb(p.AsInteger()), "RGB") if p else "N/A"
    p = gp(BuiltInParameter.TEXT_BOX_VISIBILITY)
    border = "Border" if (p and p.AsInteger() == 1) else "N/A"
    p = gp(BuiltInParameter.TEXT_STYLE_BOLD)
    bold   = p and p.AsInteger() == 1
    p = gp(BuiltInParameter.TEXT_STYLE_UNDERLINE)
    uline  = p and p.AsInteger() == 1
    p = gp(BuiltInParameter.TEXT_STYLE_ITALIC)
    italic = p and p.AsInteger() == 1

    styles_list = []
    if bold:   styles_list.append("B")
    if uline:  styles_list.append("U")
    if italic: styles_list.append("I")
    styles_str = "".join(styles_list) if styles_list else "N/A"

    # Map variable values to structure configuration fields
    field_values = {
        "Discipline": discipline,
        "TypeIndicator": "TXT",
        "Size": size,
        "Font": font,
        "WidthScale": factor,
        "Background": bg,
        "Color": color if color != "Black" else "N/A",
        "Border": border,
        "TextStyles": styles_str
    }

    # Construct naming parts based on defined Field structure
    parts = []
    for field in NAMING_TEMPLATES["TextNote"]["Fields"]:
        val = field_values.get(field, "N/A")
        if val != "N/A" and val:
            parts.append(val)

    return NAMING_TEMPLATES["TextNote"]["Separator"].join(parts)

# ============================================================
# XAML PATH
# ============================================================
_GUI_DIR = os.path.dirname(__file__)
_XAML_PATH = os.path.join(_GUI_DIR, 'Tools', 'ManaAnno.xaml')

# ============================================================
# WINDOW CLASS
# ============================================================
class AnnotationManagerWindow(T3WPFWindow):

    def __init__(self):
        try:
            T3WPFWindow.__init__(self, _XAML_PATH)
            self._dim_submode = "instances"  # "instances" | "types"
            self._txt_submode = "notes"      # "notes"     | "types"

            import System
            # ── Dimension DataTable ──────────────────────────────────────────
            self._dim_dt = DataTable()
            self._dim_dt.Columns.Add("Selected", System.Boolean)
            for col in ["_id", "_cat", "Name", "Size", "Font", "Background", "Color", "Details", "Type", "Count", "Status"]:
                self._dim_dt.Columns.Add(col)
            self.dg_dim.ItemsSource = self._dim_dt.DefaultView
            self._dim_map = {}   # id-str → Revit element
            self.dg_dim.CellEditEnding += self.dim_cell_edit_ending

            # ── TextNote DataTable ───────────────────────────────────────────
            self._txt_dt = DataTable()
            self._txt_dt.Columns.Add("Selected", System.Boolean)
            for col in ["_id", "_cat", "Name", "Size", "Font", "Background", "Color", "Details", "Type", "Count", "Status"]:
                self._txt_dt.Columns.Add(col)
            self.dg_txt.ItemsSource = self._txt_dt.DefaultView
            self._txt_map = {}   # id-str → Revit element
            self.dg_txt.CellEditEnding += self.txt_cell_edit_ending

            # Sidebar list event bindings
            self.lb_dim_types.SelectionChanged += self.dim_sidebar_select_changed
            self.lb_txt_types.SelectionChanged += self.txt_sidebar_select_changed
            self.dim_sidebar_search.TextChanged += self.dim_sidebar_search_changed
            self.txt_sidebar_search.TextChanged += self.txt_sidebar_search_changed

            # Main search text changed event bindings (for placeholder behavior and instant search)
            self.dim_kw.TextChanged += self.dim_kw_changed
            self.txt_kw.TextChanged += self.txt_kw_changed

            # Auto-load all elements on startup
            self._load_all_dims()
            self._load_all_txts()
            self._load_sidebar_lists()

            # DimText state
            self._dimtext_rules = []

            # Register utility button click events
            self.btn_util_copy_anno.Click += self._on_launch_copier
            self.btn_util_renumber_spline.Click += self._on_launch_renumber
            self.btn_util_upper_all.Click += self._on_launch_upper_all

            # Force initial tab content to render: nav_dim.IsChecked was already
            # True when the XAML was parsed, so no explicit SelectedIndex was ever
            # set on the headerless TabControl (same fix as ManaSheets/ManaViews).
            self.main_tabs.SelectedIndex = 0
            self._init_ai_mode()
        except Exception as ex:
            logger.error("Error initializing window: {}".format(ex))
            raise

    # ── AI Mode Support ──────────────────────────────────────────────────

    def _init_ai_mode(self):
        try:
            if hasattr(self, 'is_ai_mode_active') and self.is_ai_mode_active():
                if hasattr(self, 'ai_mode_badge') and self.ai_mode_badge:
                    self.ai_mode_badge.Visibility = Visibility.Visible
                if hasattr(self, 'txt_ai_status') and self.txt_ai_status:
                    info = self.get_ai_status_info()
                    self.txt_ai_status.Text = "AI Mode: {}".format(info.get('model', 'Ready'))
        except Exception as ex:
            logger.warning("AI Mode init failed: {}".format(ex))

    def on_txt_ai_qa_clicked(self, sender, args):
        """AI Quality Check: Detect typos, formatting issues, casing in Text Notes."""
        try:
            notes_to_check = []
            for row in self._txt_dt.Rows:
                if row["Selected"]:
                    notes_to_check.append({"id": str(row["_id"]), "text": str(row["Name"])})
            if not notes_to_check:
                for row in self._txt_dt.Rows:
                    notes_to_check.append({"id": str(row["_id"]), "text": str(row["Name"])})
                    if len(notes_to_check) >= 30:
                        break

            if not notes_to_check:
                self._status("No text notes found to check.")
                return

            self._status("AI inspecting {} text note(s) for typos & BIM standards...".format(len(notes_to_check)))
            if hasattr(self, 'btn_txt_ai_qa'):
                self.btn_txt_ai_qa.IsEnabled = False
                self.btn_txt_ai_qa.Content = "⏳ Checking..."

            def _restore_txt_btn():
                if hasattr(self, 'btn_txt_ai_qa'):
                    self.btn_txt_ai_qa.IsEnabled = True
                    self.btn_txt_ai_qa.Content = "✨ AI Spellcheck & Fix"

            def _apply_classic():
                _restore_txt_btn()
                changed = 0
                for row in self._txt_dt.Rows:
                    orig = str(row["Name"])
                    cleaned = " ".join(orig.split())
                    terms = {"wc": "WC", "nvs": "NVS", "dam": "DẦM", "cot": "CỘT", "san": "SÀN", "tang": "TẦNG"}
                    for t_low, t_up in terms.items():
                        cleaned = re.sub(r'\b' + re.escape(t_low) + r'\b', t_up, cleaned, flags=re.IGNORECASE)
                    if cleaned != orig:
                        row["Name"] = cleaned
                        row["Status"] = "Rule Fix"
                        row["Selected"] = True
                        changed += 1
                if changed:
                    self.btn_txt_apply.Visibility = Visibility.Visible
                    self._status("Rule-based QA: Standardized {} note(s) (marked 'Rule Fix'). Click Apply to save.".format(changed))
                else:
                    self._status("Rule-based QA: All checked notes conform to basic formatting.")

            if not hasattr(self, 'is_ai_mode_active') or not self.is_ai_mode_active():
                _apply_classic()
                return

            import json
            prompt = (
                "You are an expert BIM Quality Assurance specialist for architectural, structural, and MEP drawing annotations (Vietnamese & English).\n"
                "Review these drawing text notes for spelling errors, irregular casing, abbreviations, and inconsistencies.\n"
                "Return JSON ONLY with this structure:\n"
                "{\n"
                "  \"corrections\": [\n"
                "    {\"id\": \"...\", \"original\": \"...\", \"suggested\": \"...\", \"issue\": \"...\"}\n"
                "  ],\n"
                "  \"summary\": \"...\"\n"
                "}\n"
                "Notes to inspect:\n" + json.dumps(notes_to_check)
            )

            def _worker():
                return self.ai_bridge.ask_json(prompt, fast=True)

            def _callback(res, err):
                _restore_txt_btn()
                if err or not res or not isinstance(res, dict) or 'corrections' not in res:
                    _apply_classic()
                    return

                corrections = res.get('corrections', [])
                summary = res.get('summary', 'Inspection complete')
                if not corrections:
                    self._status("AI QA: All notes look standard. No corrections needed.")
                    return

                corr_map = {c['id']: c['suggested'] for c in corrections if 'id' in c and 'suggested' in c}
                updated = 0
                for row in self._txt_dt.Rows:
                    rid = str(row["_id"])
                    if rid in corr_map:
                        row["Name"] = corr_map[rid]
                        row["Status"] = "AI Fix"
                        row["Selected"] = True
                        updated += 1

                if updated > 0:
                    self.btn_txt_apply.Visibility = Visibility.Visible
                    self._status("AI QA: {}. {} note(s) updated (marked as 'AI Fix'). Click Apply Changes to commit.".format(
                        summary, updated))
                else:
                    self._status("AI QA: {}".format(summary))

            self.run_ai_async(_worker, _callback)

        except Exception as ex:
            if hasattr(self, 'btn_txt_ai_qa'):
                self.btn_txt_ai_qa.IsEnabled = True
                self.btn_txt_ai_qa.Content = "✨ AI Spellcheck & Fix"
            logger.error("Error in on_txt_ai_qa_clicked: {}".format(ex))
            self._status("Error in AI QA: {}".format(ex))

    def on_dimtext_ai_suggest_clicked(self, sender, args):
        """Suggest standard engineering dimension text overrides (Prefix, Suffix, Above, Below)."""
        try:
            self._status("AI suggesting standard dimension override annotations...")
            if hasattr(self, 'btn_dimtext_ai_suggest'):
                self.btn_dimtext_ai_suggest.IsEnabled = False
                self.btn_dimtext_ai_suggest.Content = "⏳ Suggesting..."

            def _restore_dim_btn():
                if hasattr(self, 'btn_dimtext_ai_suggest'):
                    self.btn_dimtext_ai_suggest.IsEnabled = True
                    self.btn_dimtext_ai_suggest.Content = "✨ AI Suggest Overrides"

            def _apply_suggestions(pfx, sfx, abv, blw, rationale):
                _restore_dim_btn()
                if hasattr(self, 'txt_prefix') and pfx and not self.txt_prefix.Text:
                    self.txt_prefix.Text = pfx
                if hasattr(self, 'txt_suffix') and sfx:
                    self.txt_suffix.Text = sfx
                if hasattr(self, 'txt_above') and abv:
                    self.txt_above.Text = abv
                if hasattr(self, 'txt_below') and blw:
                    self.txt_below.Text = blw
                self._status("AI Suggest: Applied overrides ({})".format(rationale))

            if not hasattr(self, 'is_ai_mode_active') or not self.is_ai_mode_active():
                _apply_suggestions("", " (TYP.)", "VERIFY ON SITE", "F.F.L.", "Standard BIM drawing convention")
                return

            prompt = (
                "Suggest standard architectural/engineering BIM dimension override text for typical floor plan annotations.\n"
                "Return JSON ONLY with keys:\n"
                "{\n"
                "  \"prefix\": string,\n"
                "  \"suffix\": string (e.g. ' (TYP.)' or ' (E.Q.)'),\n"
                "  \"above\": string (e.g. 'VERIFY' or 'CLEAR'),\n"
                "  \"below\": string (e.g. 'F.F.L.' or 'C.H.'),\n"
                "  \"rationale\": string\n"
                "}"
            )

            def _worker():
                return self.ai_bridge.ask_json(prompt, fast=True)

            def _callback(res, err):
                if err or not res or not isinstance(res, dict):
                    _apply_suggestions("", " (TYP.)", "VERIFY ON SITE", "F.F.L.", "Standard drawing practice")
                else:
                    pfx = res.get('prefix', '')
                    sfx = res.get('suffix', ' (TYP.)')
                    abv = res.get('above', 'VERIFY')
                    blw = res.get('below', 'F.F.L.')
                    rat = res.get('rationale', 'AEC drawing standard')
                    _apply_suggestions(pfx, sfx, abv, blw, rat)

            self.run_ai_async(_worker, _callback)

        except Exception as ex:
            if hasattr(self, 'btn_dimtext_ai_suggest'):
                self.btn_dimtext_ai_suggest.IsEnabled = True
                self.btn_dimtext_ai_suggest.Content = "✨ AI Suggest Overrides"
            logger.error("Error in on_dimtext_ai_suggest_clicked: {}".format(ex))
            self._status("Error in AI Suggest: {}".format(ex))

    # ── helpers ─────────────────────────────────────────────────────────

    def _status(self, msg):
        self.status.Text = msg

    def _dt_add(self, dt, elem_id, cat_code, name, details,
                size="", font="", bg="", color="", selected=False, count="1", status="Active"):
        row = dt.NewRow()
        row["Selected"]   = selected
        row["_id"]        = elem_id
        row["_cat"]       = cat_code
        row["Name"]       = name
        row["Size"]       = size
        row["Font"]       = font
        row["Background"] = bg
        row["Color"]      = color
        row["Details"]    = details
        row["Type"]       = "Dimension Style" if "Dim" in cat_code else "Text Note Style"
        row["Count"]      = count
        row["Status"]     = status
        dt.Rows.Add(row)

    @staticmethod
    def _get_dim_params(dt):
        """Extract common params from a DimensionType element."""
        def gp(bip):
            try: return dt.get_Parameter(bip)
            except: return None
        p = gp(BuiltInParameter.TEXT_SIZE)
        size = _mm(p) if p else ""
        p = gp(BuiltInParameter.TEXT_FONT)
        font = p.AsString() if p else ""
        p = gp(BuiltInParameter.DIM_TEXT_BACKGROUND)
        bg = p.AsValueString() if p else ""
        p = gp(BuiltInParameter.LINE_COLOR)
        color = _DIM_COLORS.get(_rgb(p.AsInteger()), "RGB") if p else ""
        return size, font, bg, color

    @staticmethod
    def _get_txt_params(tt):
        """Extract common params from a TextNoteType element."""
        def gp(bip):
            try: return tt.get_Parameter(bip)
            except: return None
        p = gp(BuiltInParameter.TEXT_SIZE)
        size = _mm(p) if p else ""
        p = gp(BuiltInParameter.TEXT_FONT)
        font = p.AsString() if p else ""
        p = gp(BuiltInParameter.TEXT_BACKGROUND)
        bg = ("Opaque" if p.AsInteger() == 0 else "Transparent") if p else ""
        p = gp(BuiltInParameter.LINE_COLOR)
        color = _TXT_COLORS.get(_rgb(p.AsInteger()), "RGB") if p else ""
        return size, font, bg, color

    def _load_all_dims(self):
        self._dim_dt.Clear()
        self._dim_map = {}

        # Pre-calculate counts of instances per type
        all_dims = FilteredElementCollector(doc).OfClass(Dimension)\
                   .WhereElementIsNotElementType().ToElements()
        dim_counts = {}
        for d in all_dims:
            tid = str(d.DimensionType.Id)
            dim_counts[tid] = dim_counts.get(tid, 0) + 1

        if self._dim_submode == "instances":
            dims = FilteredElementCollector(doc).OfClass(Dimension)\
                   .WhereElementIsNotElementType().ToElements()
            for d in dims:
                view = doc.GetElement(d.OwnerViewId)
                if view:
                    try:
                        _p = d.DimensionType.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                        d_name = (_p.AsString() if _p else "") or "<unnamed style>"
                    except Exception:
                        d_name = "<unnamed style>"
                    self._dt_add(self._dim_dt, str(d.Id), "DimInst",
                                 d_name, view.Name,
                                 selected=False, count="1", status="Active")
                    self._dim_map[str(d.Id)] = d
        else:
            types = FilteredElementCollector(doc).OfClass(DimensionType)\
                    .WhereElementIsElementType().ToElements()
            for dt in types:
                try:
                    name = dt.Name or ""
                except Exception:
                    name = ""
                try:
                    size, font, bg, color = self._get_dim_params(dt)
                except Exception:
                    size, font, bg, color = "", "", "", ""
                count = dim_counts.get(str(dt.Id), 0)
                status = "Active" if count > 0 else "Unused"
                self._dt_add(self._dim_dt, str(dt.Id), "DimType",
                             name or "<unnamed>", "Dimension Type",
                             size, font, bg, color,
                             selected=False, count=str(count), status=status)
                self._dim_map[str(dt.Id)] = dt

        n = len(self._dim_map)
        self.dim_count.Text = "{} found".format(n)
        kind = "dimension(s)" if self._dim_submode == "instances" else "type(s)"
        self._status("Loaded {} {}.".format(n, kind))

    def _load_all_txts(self):
        self._txt_dt.Clear()
        self._txt_map = {}

        # Pre-calculate counts of instances per type
        all_notes = FilteredElementCollector(doc).OfClass(TextNote)\
                    .WhereElementIsNotElementType().ToElements()
        note_counts = {}
        for tn in all_notes:
            tid = str(tn.TextNoteType.Id)
            note_counts[tid] = note_counts.get(tid, 0) + 1

        if self._txt_submode == "notes":
            notes = FilteredElementCollector(doc).OfClass(TextNote)\
                    .WhereElementIsNotElementType().ToElements()
            for tn in notes:
                view = doc.GetElement(tn.OwnerViewId)
                if view:
                    preview = (tn.Text or "")[:60].replace("\n", " ").replace("\r", "")
                    self._dt_add(self._txt_dt, str(tn.Id), "TxtInst",
                                 preview, view.Name,
                                 selected=False, count="1", status="Active")
                    self._txt_map[str(tn.Id)] = tn
        else:
            types = FilteredElementCollector(doc).OfClass(TextNoteType)\
                    .WhereElementIsElementType().ToElements()
            for tt in types:
                try:
                    p = tt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    name = p.AsString() if p else (tt.Name or "")
                except Exception:
                    name = ""
                try:
                    size, font, bg, color = self._get_txt_params(tt)
                except Exception:
                    size, font, bg, color = "", "", "", ""
                count = note_counts.get(str(tt.Id), 0)
                status = "Active" if count > 0 else "Unused"
                self._dt_add(self._txt_dt, str(tt.Id), "TxtType",
                             name or "<unnamed>", "Text Note Type",
                             size, font, bg, color,
                             selected=False, count=str(count), status=status)
                self._txt_map[str(tt.Id)] = tt

        n = len(self._txt_map)
        self.txt_count.Text = "{} found".format(n)
        kind = "note(s)" if self._txt_submode == "notes" else "type(s)"
        self._status("Loaded {} {}.".format(n, kind))

    def dim_refresh(self, sender, args):
        self._load_all_dims()
        self._load_sidebar_lists()

    def txt_refresh(self, sender, args):
        self._load_all_txts()
        self._load_sidebar_lists()

    def _remove_rows(self, dt, elem_map, ok_ids):
        ok_set = set(ok_ids)
        to_del = list(r for r in dt.Rows if str(r["_id"]) in ok_set)
        for r in to_del:
            dt.Rows.Remove(r)
        for eid in ok_ids:
            elem_map.pop(eid, None)

    # ── Window controls ──────────────────────────────────────────────────

    def minimize_button_clicked(self, sender, args):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, args):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, args):
        self.Close()

    # ── Dimension sub-mode ───────────────────────────────────────────────

    def _toggle_param_cols(self, dg, show):
        vis = Visibility.Visible if show else Visibility.Collapsed
        for col in dg.Columns:
            h = str(col.Header) if col.Header else ""
            if h in ("Size", "Font", "Background", "Color"):
                col.Visibility = vis

    def dim_submode(self, sender, args):
        if not hasattr(self, '_dim_dt'):
            return
        self._dim_submode = "instances" if self.rb_dim_inst.IsChecked else "types"
        is_type = self._dim_submode == "types"
        self.btn_dim_jump.IsEnabled = not is_type
        if hasattr(self, 'dim_lbl'):
            self.dim_lbl.Text = "Style name:" if not is_type else "Type name:"
        self._toggle_param_cols(self.dg_dim, is_type)
        vis = Visibility.Visible if is_type else Visibility.Collapsed
        self.btn_dim_apply.Visibility = vis
        if hasattr(self, 'btn_dim_del_unused'):
            self.btn_dim_del_unused.Visibility = vis
        self.dim_search(None, None)

    # ── DIMENSION operations ─────────────────────────────────────────────

    def dim_search(self, sender, args):
        kw = self.dim_kw.Text.strip().lower()
        if not kw:
            self._load_all_dims()
            return

        self._dim_dt.Clear()
        self._dim_map = {}

        # Pre-calculate counts of instances per type
        all_dims = FilteredElementCollector(doc).OfClass(Dimension)\
                   .WhereElementIsNotElementType().ToElements()
        dim_counts = {}
        for d in all_dims:
            tid = str(d.DimensionType.Id)
            dim_counts[tid] = dim_counts.get(tid, 0) + 1

        if self._dim_submode == "instances":
            dims = FilteredElementCollector(doc).OfClass(Dimension)\
                   .WhereElementIsNotElementType().ToElements()
            for d in dims:
                try:
                    _p = d.DimensionType.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    d_name = (_p.AsString() if _p else "") or "<unnamed style>"
                except Exception:
                    d_name = "<unnamed style>"
                if kw in d_name.lower():
                    view = doc.GetElement(d.OwnerViewId)
                    if view:
                        self._dt_add(self._dim_dt, str(d.Id), "DimInst",
                                     d_name, view.Name,
                                     selected=False, count="1", status="Active")
                        self._dim_map[str(d.Id)] = d
        else:  # types
            types = FilteredElementCollector(doc).OfClass(DimensionType)\
                    .WhereElementIsElementType().ToElements()
            for dt in types:
                try:
                    name = dt.Name or ""
                except Exception:
                    try:
                        _p = dt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                        name = _p.AsString() if _p else ""
                    except Exception:
                        name = ""
                if kw in name.lower():
                    size, font, bg, color = self._get_dim_params(dt)
                    count = dim_counts.get(str(dt.Id), 0)
                    status = "Active" if count > 0 else "Unused"
                    self._dt_add(self._dim_dt, str(dt.Id), "DimType",
                                 name or "<unnamed>", "Dimension Type",
                                 size, font, bg, color,
                                 selected=False, count=str(count), status=status)
                    self._dim_map[str(dt.Id)] = dt

        n = len(self._dim_map)
        self.dim_count.Text = "{} found".format(n)
        kind = "dimension(s)" if self._dim_submode == "instances" else "type(s)"
        self._status("Found {} {} matching '{}'.".format(n, kind, kw))

    def dim_cell_edit_ending(self, sender, args):
        if str(args.Column.Header) != "NAME":
            return
        if str(args.EditAction) != "Commit":
            return

        tb       = args.EditingElement
        new_name = tb.Text.strip()
        if not new_name:
            args.Cancel = True
            return

        row      = args.Row.Item
        elem_id  = str(row["_id"])
        cat_code = str(row["_cat"])
        old_name = str(row["Name"])

        if new_name == old_name:
            return

        if cat_code != "DimType":
            args.Cancel = True
            self._status("Dimension instances cannot be renamed. Switch to 'Find Types' mode.")
            return

        elem = self._dim_map.get(elem_id)
        if not elem:
            return

        t = Transaction(doc, "Rename Dimension Type")
        t.Start()
        try:
            elem.Name = new_name
            t.Commit()
            self._status(u"Renamed: '{}' \u2192 '{}'.".format(old_name[:40], new_name[:40]))
        except Exception as e:
            t.RollBack()
            args.Cancel = True
            self._status("Rename failed: {}".format(e))

    def dim_jump(self, sender, args):
        selected_rows = []
        for row in self._dim_dt.Rows:
            if row["Selected"]:
                selected_rows.append(row)
        if not selected_rows:
            selected_rows = list(self.dg_dim.SelectedItems)

        if not selected_rows:
            self._status("Select a dimension first.")
            return
        row     = selected_rows[0]
        elem_id = str(row["_id"])
        d       = self._dim_map.get(elem_id)
        if not d:
            return
        view = doc.GetElement(d.OwnerViewId)
        if view:
            uidoc.ActiveView = view
            uidoc.ShowElements(d.Id)
            self._status("Jumped to view '{}' — dimension '{}'.".format(
                view.Name, str(row["Name"])[:40]))

    def dim_delete(self, sender, args):
        selected_rows = []
        for row in self._dim_dt.Rows:
            if row["Selected"]:
                selected_rows.append(row)
        if not selected_rows:
            selected_rows = list(self.dg_dim.SelectedItems)

        if not selected_rows:
            self._status("Nothing selected. Check boxes or select rows to delete.")
            return

        t = Transaction(doc, "Delete Selected Dimensions")
        t.Start()
        ok_ids = []
        errors = 0
        for row in selected_rows:
            elem_id = str(row["_id"])
            elem    = self._dim_map.get(elem_id)
            if elem:
                try:
                    doc.Delete(elem.Id)
                    ok_ids.append(elem_id)
                except Exception:
                    errors += 1
        t.Commit()
        self._remove_rows(self._dim_dt, self._dim_map, ok_ids)
        self.dim_count.Text = "{} found".format(len(self._dim_map))
        msg = "Deleted {} elements.".format(len(ok_ids))
        if errors:
            msg += "  ({} failed.)".format(errors)
        self._status(msg)
        self._load_sidebar_lists()

    def dim_select_all(self, sender, args):
        for row in self._dim_dt.Rows:
            row["Selected"] = True

    def dim_clear_sel(self, sender, args):
        for row in self._dim_dt.Rows:
            row["Selected"] = False

    def dim_apply(self, sender, args):
        """Apply edited Name back to DimensionType elements."""
        t = Transaction(doc, "Apply Dimension Type Changes")
        t.Start()
        count = 0
        errors = 0
        for row in self._dim_dt.Rows:
            elem_id = str(row["_id"])
            elem = self._dim_map.get(elem_id)
            if not elem or str(row["_cat"]) != "DimType":
                continue
            new_name = str(row["Name"]).strip()
            if not new_name:
                continue
            try:
                _p = elem.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                cur_name = _p.AsString() if _p else ""
                if cur_name != new_name:
                    elem.Name = new_name
                    count += 1
            except Exception:
                errors += 1
        t.Commit()
        msg = "Applied {} rename(s).".format(count)
        if errors:
            msg += "  ({} failed.)".format(errors)
        self._status(msg)
        self._load_all_dims()
        self._load_sidebar_lists()

    def dim_rename_all(self, sender, args):
        from pyrevit import forms as pf
        if not pf.alert("Auto-rename ALL DimensionTypes in this document?\nThis cannot be undone.",
                        title="Confirm Rename", yes=True, no=True):
            return
        t = Transaction(doc, "Rename Dimension Types")
        t.Start()
        count = 0
        try:
            for dt in FilteredElementCollector(doc).OfClass(DimensionType)\
                      .WhereElementIsElementType().ToElements():
                new_name = None
                try:
                    p = dt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    if p is None:
                        continue
                    origin = p.AsString()
                    if not origin:
                        continue
                    new_name = _dim_name(dt, origin)
                    if origin != new_name:
                        dt.Name = new_name
                        count += 1
                except Exception as ex:
                    try:
                        _p = dt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                        _cur = _p.AsString() if _p else str(dt.Id)
                    except Exception:
                        _cur = str(dt.Id)
                    print("DEBUG: Failed to rename DimensionType '{}' to '{}': {}".format(
                        _cur, new_name, ex
                    ))
        finally:
            t.Commit()
        self._status("Renamed {} DimensionType(s).".format(count))
        self._load_all_dims()
        self._load_sidebar_lists()

    def dim_delete_unused(self, sender, args):
        from pyrevit import forms as pf
        all_dims = FilteredElementCollector(doc).OfClass(Dimension)\
                   .WhereElementIsNotElementType().ToElements()
        used_ids = set()
        for d in all_dims:
            try:
                used_ids.add(str(d.DimensionType.Id))
            except Exception:
                pass
        all_types = FilteredElementCollector(doc).OfClass(DimensionType)\
                    .WhereElementIsElementType().ToElements()
        unused = [dt for dt in all_types if str(dt.Id) not in used_ids]
        if not unused:
            self._status("No unused Dimension Types found.")
            return
        if not pf.alert("Purge {} unused Dimension Type(s)?\nThis cannot be undone.".format(len(unused)),
                        title="Confirm Purge", yes=True, no=True):
            return
        t = Transaction(doc, "Purge Unused Dimension Types")
        t.Start()
        ok = 0
        for dt in unused:
            try:
                doc.Delete(dt.Id)
                ok += 1
            except Exception:
                pass
        t.Commit()
        self._status("Purged {} unused Dimension Type(s).".format(ok))
        self._load_all_dims()
        self._load_sidebar_lists()

    # ── TextNote sub-mode ────────────────────────────────────────────────

    def txt_submode(self, sender, args):
        if not hasattr(self, '_txt_dt'):
            return
        if self.rb_notes.IsChecked:
            self._txt_submode = "notes"
            if hasattr(self, 'txt_lbl'):
                self.txt_lbl.Text = "Content:"
            self.btn_txt_jump.IsEnabled = True
        else:
            self._txt_submode = "types"
            if hasattr(self, 'txt_lbl'):
                self.txt_lbl.Text = "Type name:"
            self.btn_txt_jump.IsEnabled = False
        is_type = self._txt_submode == "types"
        self._toggle_param_cols(self.dg_txt, is_type)
        vis = Visibility.Visible if is_type else Visibility.Collapsed
        self.btn_txt_apply.Visibility = vis
        if hasattr(self, 'btn_txt_del_unused'):
            self.btn_txt_del_unused.Visibility = vis
        self.txt_search(None, None)

    # ── TEXTNOTE operations ──────────────────────────────────────────────

    def txt_search(self, sender, args):
        kw = self.txt_kw.Text.strip().lower()
        if not kw:
            self._load_all_txts()
            return

        self._txt_dt.Clear()
        self._txt_map = {}

        # Pre-calculate counts of instances per type
        all_notes = FilteredElementCollector(doc).OfClass(TextNote)\
                    .WhereElementIsNotElementType().ToElements()
        note_counts = {}
        for tn in all_notes:
            tid = str(tn.TextNoteType.Id)
            note_counts[tid] = note_counts.get(tid, 0) + 1

        if self._txt_submode == "notes":
            notes = FilteredElementCollector(doc).OfClass(TextNote)\
                    .WhereElementIsNotElementType().ToElements()
            for tn in notes:
                if kw in (tn.Text or "").lower():
                    view = doc.GetElement(tn.OwnerViewId)
                    if view:
                        preview = (tn.Text or "")[:60].replace("\n", " ").replace("\r", "")
                        self._dt_add(self._txt_dt, str(tn.Id), "TxtInst",
                                     preview, view.Name,
                                     selected=False, count="1", status="Active")
                        self._txt_map[str(tn.Id)] = tn
        else:  # types
            types = FilteredElementCollector(doc).OfClass(TextNoteType)\
                    .WhereElementIsElementType().ToElements()
            for tt in types:
                try:
                    _p = tt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    name = _p.AsString() if _p else (tt.Name or "")
                except Exception:
                    name = ""
                if kw in name.lower():
                    size, font, bg, color = self._get_txt_params(tt)
                    count = note_counts.get(str(tt.Id), 0)
                    status = "Active" if count > 0 else "Unused"
                    self._dt_add(self._txt_dt, str(tt.Id), "TxtType",
                                 name or "<unnamed>", "Text Note Type",
                                 size, font, bg, color,
                                 selected=False, count=str(count), status=status)
                    self._txt_map[str(tt.Id)] = tt

        n = len(self._txt_map)
        self.txt_count.Text = "{} found".format(n)
        self._status("Found {} {} matching '{}'.".format(
            n, "note(s)" if self._txt_submode == "notes" else "type(s)", kw))

    def txt_cell_edit_ending(self, sender, args):
        col_header = str(args.Column.Header)
        if col_header not in ("NAME"):
            return
        if str(args.EditAction) != "Commit":
            return

        tb       = args.EditingElement
        new_name = tb.Text.strip()
        if not new_name:
            args.Cancel = True
            return

        row      = args.Row.Item
        elem_id  = str(row["_id"])
        cat_code = str(row["_cat"])
        old_name = str(row["Name"])

        if new_name == old_name:
            return

        elem = self._txt_map.get(elem_id)
        if not elem:
            return

        t = Transaction(doc, "Rename Text Note")
        t.Start()
        try:
            if cat_code == "TxtType":
                elem.Name = new_name
            else:  # TxtInst — edit the text content
                elem.Text = new_name
            t.Commit()
            self._status(u"Renamed: '{}' \u2192 '{}'.".format(old_name[:40], new_name[:40]))
        except Exception as e:
            t.RollBack()
            args.Cancel = True
            self._status("Rename failed: {}".format(e))

    def txt_jump(self, sender, args):
        if self._txt_submode != "notes":
            self._status("Jump to View is only available in Find Notes mode.")
            return
        selected_rows = []
        for row in self._txt_dt.Rows:
            if row["Selected"]:
                selected_rows.append(row)
        if not selected_rows:
            selected_rows = list(self.dg_txt.SelectedItems)

        if not selected_rows:
            self._status("Select a text note first.")
            return
        row     = selected_rows[0]
        elem_id = str(row["_id"])
        tn      = self._txt_map.get(elem_id)
        if not tn:
            return
        view = doc.GetElement(tn.OwnerViewId)
        if view:
            uidoc.ActiveView = view
            uidoc.ShowElements(tn.Id)
            self._status("Jumped to view '{}' — note: '{}'.".format(
                view.Name, str(row["Name"])[:40]))

    def txt_delete(self, sender, args):
        selected_rows = []
        for row in self._txt_dt.Rows:
            if row["Selected"]:
                selected_rows.append(row)
        if not selected_rows:
            selected_rows = list(self.dg_txt.SelectedItems)

        if not selected_rows:
            self._status("Nothing selected. Check boxes or select rows to delete.")
            return
        label = "note instance(s)" if self._txt_submode == "notes" else "TextNoteType(s)"
        t = Transaction(doc, "Delete Selected Text {}".format(label))
        t.Start()
        ok_ids = []
        errors = 0
        for row in selected_rows:
            elem_id = str(row["_id"])
            elem    = self._txt_map.get(elem_id)
            if elem:
                try:
                    doc.Delete(elem.Id)
                    ok_ids.append(elem_id)
                except Exception:
                    errors += 1
        t.Commit()
        self._remove_rows(self._txt_dt, self._txt_map, ok_ids)
        self.txt_count.Text = "{} found".format(len(self._txt_map))
        msg = "Deleted {} {}.".format(len(ok_ids), label)
        if errors:
            msg += "  ({} could not be deleted — may be in use.)".format(errors)
        self._status(msg)
        self._load_sidebar_lists()

    def txt_select_all(self, sender, args):
        for row in self._txt_dt.Rows:
            row["Selected"] = True

    def txt_clear_sel(self, sender, args):
        for row in self._txt_dt.Rows:
            row["Selected"] = False

    def txt_apply(self, sender, args):
        """Apply edited Name/Text back to TextNoteType or TextNote instances."""
        t = Transaction(doc, "Apply Text Note Changes")
        t.Start()
        count = 0
        errors = 0
        for row in self._txt_dt.Rows:
            elem_id = str(row["_id"])
            elem = self._txt_map.get(elem_id)
            if not elem:
                continue
            new_name = str(row["Name"]).strip()
            if not new_name:
                continue
            cat_code = str(row["_cat"])
            try:
                if cat_code == "TxtType":
                    _p = elem.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    cur_name = _p.AsString() if _p else ""
                    if cur_name != new_name:
                        elem.Name = new_name
                        count += 1
                else:
                    if elem.Text != new_name:
                        elem.Text = new_name
                        count += 1
            except Exception:
                errors += 1
        t.Commit()
        msg = "Applied {} text change(s).".format(count)
        if errors:
            msg += "  ({} failed.)".format(errors)
        self._status(msg)
        self.btn_txt_apply.Visibility = Visibility.Collapsed
        self._load_all_txts()
        self._load_sidebar_lists()

    def txt_rename_all(self, sender, args):
        from pyrevit import forms as pf
        if not pf.alert("Auto-rename ALL TextNoteTypes in this document?\nThis cannot be undone.",
                        title="Confirm Rename", yes=True, no=True):
            return
        t = Transaction(doc, "Rename TextNote Types")
        t.Start()
        count = 0
        try:
            for tt in FilteredElementCollector(doc).OfClass(TextNoteType)\
                      .WhereElementIsElementType().ToElements():
                try:
                    _p = tt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                    if _p is None:
                        continue
                    origin = _p.AsString()
                    if not origin:
                        continue
                    tt.Name = _txt_name(tt, origin)
                    count += 1
                except Exception:
                    pass
        finally:
            t.Commit()
        self._status("Renamed {} TextNoteType(s).".format(count))
        self._load_all_txts()
        self._load_sidebar_lists()

    def txt_delete_unused(self, sender, args):
        from pyrevit import forms as pf
        all_notes = FilteredElementCollector(doc).OfClass(TextNote)\
                    .WhereElementIsNotElementType().ToElements()
        used_ids = set()
        for tn in all_notes:
            try:
                used_ids.add(str(tn.TextNoteType.Id))
            except Exception:
                pass
        all_types = FilteredElementCollector(doc).OfClass(TextNoteType)\
                    .WhereElementIsElementType().ToElements()
        unused = [tt for tt in all_types if str(tt.Id) not in used_ids]
        if not unused:
            self._status("No unused Text Note Types found.")
            return
        if not pf.alert("Purge {} unused Text Note Type(s)?\nThis cannot be undone.".format(len(unused)),
                        title="Confirm Purge", yes=True, no=True):
            return
        t = Transaction(doc, "Purge Unused Text Note Types")
        t.Start()
        ok = 0
        for tt in unused:
            try:
                doc.Delete(tt.Id)
                ok += 1
            except Exception:
                pass
        t.Commit()
        self._status("Purged {} unused Text Note Type(s).".format(ok))
        self._load_all_txts()
        self._load_sidebar_lists()

    # ── Header Checkbox Toggle Event Handlers ─────────────────────────────────

    def dim_header_select_all_clicked(self, sender, args):
        is_checked = sender.IsChecked
        for row in self._dim_dt.Rows:
            row["Selected"] = is_checked

    def txt_header_select_all_clicked(self, sender, args):
        is_checked = sender.IsChecked
        for row in self._txt_dt.Rows:
            row["Selected"] = is_checked

    # ── Top Horizontal Navigation Tab Event Handlers ─────────────────────────

    def _update_nav_states(self, active_btn):
        for btn in (self.nav_dim, self.nav_txt, self.nav_dimtext, self.nav_utils, self.nav_settings):
            try:
                btn.IsChecked = (btn == active_btn)
            except Exception:
                pass

    def nav_dimensions_checked(self, sender, args):
        self._update_nav_states(self.nav_dim)
        if hasattr(self, 'main_tabs'):
            self.main_tabs.SelectedIndex = 0

    def nav_textnotes_checked(self, sender, args):
        self._update_nav_states(self.nav_txt)
        if hasattr(self, 'main_tabs'):
            self.main_tabs.SelectedIndex = 1

    def nav_dimtext_checked(self, sender, args):
        self._update_nav_states(self.nav_dimtext)
        if hasattr(self, 'main_tabs'):
            self.main_tabs.SelectedIndex = 2

    def nav_utils_checked(self, sender, args):
        self._update_nav_states(self.nav_utils)
        if hasattr(self, 'main_tabs'):
            self.main_tabs.SelectedIndex = 3

    def nav_settings_checked(self, sender, args):
        self._update_nav_states(self.nav_settings)
        if hasattr(self, 'main_tabs'):
            self.main_tabs.SelectedIndex = 4

    def _on_launch_copier(self, sender, e):
        self.Hide()
        try:
            CopyAnnotationDialog.show_dialog()
        finally:
            self.Show()

    def _on_launch_renumber(self, sender, e):
        self.Hide()
        try:
            RenumberAlongSpline.run()
        finally:
            self.Show()

    def _on_launch_upper_all(self, sender, e):
        self.Hide()
        try:
            UpperAll.run()
        finally:
            self.Show()

    # ── DimText tab handlers ─────────────────────────────────────────────────

    def dimtext_preset_below(self, sender, args):
        self.txt_below.Text = sender.Tag

    def dimtext_clear_fields(self, sender, args):
        for n in ("txt_prefix", "txt_suffix", "txt_above", "txt_below", "txt_override"):
            ctrl = self.FindName(n)
            if ctrl is not None:
                ctrl.Text = ""
        self._status("DimText: fields cleared.")

    def dimtext_filter_toggle(self, sender, args):
        from System.Windows import Visibility
        vis = Visibility.Visible if self.chk_filter_enable.IsChecked else Visibility.Collapsed
        self.sp_filter_config.Visibility = vis

    def dimtext_add_rule(self, sender, args):
        rd = self._dimtext_create_rule_row()
        self._dimtext_rules.append(rd)
        self.sp_rules.Children.Add(rd["panel"])

    def dimtext_apply(self, sender, args):
        prefix   = self.txt_prefix.Text.strip()
        suffix   = self.txt_suffix.Text.strip()
        above    = self.txt_above.Text.strip()
        below    = self.txt_below.Text.strip()
        override = self.txt_override.Text.strip()
        leader_off = bool(self.chk_leader.IsChecked)
        filter_fn  = self._dimtext_build_filter_fn()

        if self.rb_view.IsChecked:
            dims = DimTextDialog._get_dims_in_view()
            scope = "view"
        else:
            dims = DimTextDialog._get_selected_dims()
            scope = "selection"

        if not dims:
            self._status("DimText: no dimensions found in {}.".format(scope))
            return

        from Autodesk.Revit.DB import Transaction
        with Transaction(doc, "Dim Text Override") as t:
            t.Start()
            for dim in dims:
                DimTextDialog._set_dim_text(dim, prefix, suffix, above, below, override, filter_fn)
                if leader_off:
                    DimTextDialog._turn_off_leader(dim)
            t.Commit()

        note = " (filter active)" if filter_fn else ""
        self._status("DimText: applied to {} dim(s) in {}{}.".format(len(dims), scope, note))

    def _dimtext_build_filter_fn(self):
        if not self.chk_filter_enable.IsChecked or not self._dimtext_rules:
            return None
        parsed = []
        for rd in self._dimtext_rules:
            op = rd["combo"].SelectedItem.Content if rd["combo"].SelectedItem else None
            if op is None:
                continue
            v1 = v2 = 0.0
            if op not in DimTextDialog._NO_VALUE_OPS:
                try:
                    v1 = float(rd["txt1"].Text.strip() or "0")
                except ValueError:
                    v1 = 0.0
            if op in DimTextDialog._TWO_VALUE_OPS:
                try:
                    v2 = float(rd["txt2"].Text.strip() or "0")
                except ValueError:
                    v2 = 0.0
            parsed.append((op, v1, v2))
        if not parsed:
            return None
        use_and = (self.combo_combine.SelectedIndex == 0)
        def filter_fn(length_mm):
            if length_mm is None:
                return False
            results = []
            for op, v1, v2 in parsed:
                if   op == "equals":                      results.append(abs(length_mm - v1) < 0.5)
                elif op == "does not equal":              results.append(abs(length_mm - v1) >= 0.5)
                elif op == "is greater than":             results.append(length_mm >  v1)
                elif op == "is greater than or equal to": results.append(length_mm >= v1)
                elif op == "is less than":                results.append(length_mm <  v1)
                elif op == "is less than or equal to":    results.append(length_mm <= v1)
                elif op == "between":                     results.append(min(v1, v2) <= length_mm <= max(v1, v2))
                elif op == "has a value":                 results.append(True)
                elif op == "has no value":                results.append(False)
            if not results:
                return True
            return all(results) if use_and else any(results)
        return filter_fn

    def _dimtext_create_rule_row(self):
        from System.Windows import Thickness, Visibility, VerticalAlignment
        from System.Windows.Controls import (
            StackPanel, ComboBox, ComboBoxItem, TextBox, Button, TextBlock
        )
        from System.Windows.Controls import Orientation as WPFOrientation
        from System.Windows.Media import SolidColorBrush, Color
        from System.Windows.Media import FontFamily as WPFFontFamily

        rd = {}
        row = StackPanel()
        row.Orientation = WPFOrientation.Horizontal
        row.Margin = Thickness(0, 0, 0, 6)
        rd["panel"] = row

        combo = ComboBox()
        combo.Width = 185; combo.Height = 28
        combo.FontFamily = WPFFontFamily("Inter"); combo.FontSize = 12
        combo.Margin = Thickness(0, 0, 6, 0)
        for op in DimTextDialog._OPERATORS:
            item = ComboBoxItem(); item.Content = op; combo.Items.Add(item)
        combo.SelectedIndex = 0
        combo.SelectionChanged += self._dimtext_make_op_handler(rd)
        rd["combo"] = combo; row.Children.Add(combo)

        txt1 = TextBox()
        txt1.Width = 72; txt1.Height = 28; txt1.FontSize = 12
        txt1.Padding = Thickness(6, 4, 6, 4); txt1.Margin = Thickness(0, 0, 4, 0)
        txt1.BorderBrush = SolidColorBrush(Color.FromRgb(0xCB, 0xD5, 0xE1))
        txt1.BorderThickness = Thickness(1)
        rd["txt1"] = txt1; row.Children.Add(txt1)

        lbl_mm = TextBlock()
        lbl_mm.Text = "mm"; lbl_mm.FontSize = 11
        lbl_mm.Foreground = SolidColorBrush(Color.FromRgb(0x64, 0x74, 0x8B))
        lbl_mm.Margin = Thickness(0, 0, 8, 0); lbl_mm.VerticalAlignment = VerticalAlignment.Center
        rd["lbl_mm"] = lbl_mm; row.Children.Add(lbl_mm)

        lbl_and = TextBlock()
        lbl_and.Text = "and"; lbl_and.FontSize = 11
        lbl_and.Foreground = SolidColorBrush(Color.FromRgb(0x0F, 0x17, 0x2A))
        lbl_and.Margin = Thickness(0, 0, 6, 0); lbl_and.VerticalAlignment = VerticalAlignment.Center
        lbl_and.Visibility = Visibility.Collapsed
        rd["lbl_and"] = lbl_and; row.Children.Add(lbl_and)

        txt2 = TextBox()
        txt2.Width = 72; txt2.Height = 28; txt2.FontSize = 12
        txt2.Padding = Thickness(6, 4, 6, 4); txt2.Margin = Thickness(0, 0, 4, 0)
        txt2.BorderBrush = SolidColorBrush(Color.FromRgb(0xCB, 0xD5, 0xE1))
        txt2.BorderThickness = Thickness(1); txt2.Visibility = Visibility.Collapsed
        rd["txt2"] = txt2; row.Children.Add(txt2)

        lbl_mm2 = TextBlock()
        lbl_mm2.Text = "mm"; lbl_mm2.FontSize = 11
        lbl_mm2.Foreground = SolidColorBrush(Color.FromRgb(0x64, 0x74, 0x8B))
        lbl_mm2.Margin = Thickness(0, 0, 8, 0); lbl_mm2.VerticalAlignment = VerticalAlignment.Center
        lbl_mm2.Visibility = Visibility.Collapsed
        rd["lbl_mm2"] = lbl_mm2; row.Children.Add(lbl_mm2)

        btn = Button()
        btn.Content = u"−"; btn.Width = 26; btn.Height = 26; btn.FontSize = 14
        btn.Background = SolidColorBrush(Color.FromArgb(0, 0, 0, 0))
        btn.BorderThickness = Thickness(1)
        btn.BorderBrush = SolidColorBrush(Color.FromRgb(0xEF, 0x44, 0x44))
        btn.Foreground = SolidColorBrush(Color.FromRgb(0xEF, 0x44, 0x44))
        btn.Click += self._dimtext_make_remove_handler(rd)
        row.Children.Add(btn)

        return rd

    def _dimtext_make_op_handler(self, rd):
        from System.Windows import Visibility
        def handler(sender, args):
            op = sender.SelectedItem.Content if sender.SelectedItem else ""
            no_val  = op in DimTextDialog._NO_VALUE_OPS
            two_val = op in DimTextDialog._TWO_VALUE_OPS
            v1_vis = Visibility.Collapsed if no_val else Visibility.Visible
            rd["txt1"].Visibility   = v1_vis
            rd["lbl_mm"].Visibility = v1_vis
            v2_vis = Visibility.Visible if two_val else Visibility.Collapsed
            rd["lbl_and"].Visibility = v2_vis
            rd["txt2"].Visibility    = v2_vis
            rd["lbl_mm2"].Visibility = v2_vis
        return handler

    def _dimtext_make_remove_handler(self, rd):
        def handler(sender, args):
            self.sp_rules.Children.Remove(rd["panel"])
            if rd in self._dimtext_rules:
                self._dimtext_rules.remove(rd)
        return handler

    # ── Sidebar Browsing & Live Filtering List Event Handlers ────────────────

    def _load_sidebar_lists(self):
        dim_types = FilteredElementCollector(doc).OfClass(DimensionType)\
                    .WhereElementIsElementType().ToElements()
        dim_names = []
        for dt in dim_types:
            try:
                n = dt.Name
                if n:
                    dim_names.append(n)
            except Exception:
                pass
        self._all_dim_type_names = sorted(list(set(dim_names)))

        txt_types = FilteredElementCollector(doc).OfClass(TextNoteType)\
                    .WhereElementIsElementType().ToElements()
        txt_names = []
        for tt in txt_types:
            try:
                n = tt.Name
                if n:
                    txt_names.append(n)
            except Exception:
                pass
        self._all_txt_type_names = sorted(list(set(txt_names)))

        self.filter_sidebar_dim_list()
        self.filter_sidebar_txt_list()

    def filter_sidebar_dim_list(self):
        kw = self.dim_sidebar_search.Text.strip().lower()
        self.lb_dim_types.Items.Clear()
        for name in self._all_dim_type_names:
            if not kw or kw in name.lower():
                self.lb_dim_types.Items.Add(name)

    def filter_sidebar_txt_list(self):
        kw = self.txt_sidebar_search.Text.strip().lower()
        self.lb_txt_types.Items.Clear()
        for name in self._all_txt_type_names:
            if not kw or kw in name.lower():
                self.lb_txt_types.Items.Add(name)

    def dim_sidebar_select_changed(self, sender, args):
        selected_item = self.lb_dim_types.SelectedItem
        if selected_item:
            self.dim_kw.Text = selected_item
            self.dim_kw_placeholder.Visibility = Visibility.Collapsed
            self.dim_search(sender, args)

    def txt_sidebar_select_changed(self, sender, args):
        selected_item = self.lb_txt_types.SelectedItem
        if selected_item:
            self.txt_kw.Text = selected_item
            self.txt_kw_placeholder.Visibility = Visibility.Collapsed
            self.txt_search(sender, args)

    def dim_sidebar_search_changed(self, sender, args):
        self.dim_sidebar_placeholder.Visibility = Visibility.Collapsed if self.dim_sidebar_search.Text else Visibility.Visible
        self.filter_sidebar_dim_list()

    def txt_sidebar_search_changed(self, sender, args):
        self.txt_sidebar_placeholder.Visibility = Visibility.Collapsed if self.txt_sidebar_search.Text else Visibility.Visible
        self.filter_sidebar_txt_list()

    # ── Keyword Instant Search Changed Event Handlers ───────────────────────

    def dim_kw_changed(self, sender, args):
        self.dim_kw_placeholder.Visibility = Visibility.Collapsed if self.dim_kw.Text else Visibility.Visible
        self.dim_search(sender, args)

    def txt_kw_changed(self, sender, args):
        self.txt_kw_placeholder.Visibility = Visibility.Collapsed if self.txt_kw.Text else Visibility.Visible
        self.txt_search(sender, args)

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_dg_dim_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua dg_dim."""
        self.toggle_all_rows(self.dg_dim, "Selected", sender.IsChecked)

    def select_all_dg_txt_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua dg_txt."""
        self.toggle_all_rows(self.dg_txt, "Selected", sender.IsChecked)


# MAIN SCRIPT
# ==================================================
def show_dialog():
    if not revit.doc:
        forms.alert("Please open a Revit document first.", exitscript=True)
    logger.info("Annotation Manager started")
    win = AnnotationManagerWindow()
    win.ShowDialog()

if __name__ == '__main__':
    show_dialog()
