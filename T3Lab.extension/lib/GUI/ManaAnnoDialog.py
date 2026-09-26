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

# ManaAnnoDialog.py lives in lib/GUI; keep lib first so all absolute imports are
# stable regardless of the pyRevit clone name or current working directory.
lib_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

import clr
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('System')
clr.AddReference('System.Data')

from System import TimeSpan
from System.Collections.Generic import List
from System.Data import DataTable
from System.Windows import Visibility, WindowState
from System.Windows.Threading import DispatcherTimer
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Dimension, DimensionType,
    TextNote, TextNoteType,
    Transaction, ElementId,
    BuiltInParameter,
)
from pyrevit import revit, script
from GUI import T3Dialog
from GUI.WPF_Base import T3WPFWindow

if os.path.dirname(__file__) not in sys.path:
    sys.path.insert(0, os.path.dirname(__file__))

try:
    from GUI import DimTextDialog
    from GUI import CopyAnnotationDialog
    from GUI import TagCheckerDialog
except Exception:
    import DimTextDialog
    import CopyAnnotationDialog
    import TagCheckerDialog
import Utils.UpperAll as UpperAll
import Utils.RenumberAlongSpline as RenumberAlongSpline

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()
# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
try:
    from Snippets._host import resolve_doc, resolve_uidoc
except ImportError:
    try:
        import importlib
        import Snippets._host
        importlib.reload(Snippets._host)
        from Snippets._host import resolve_doc, resolve_uidoc
    except Exception:
        from Snippets._host import resolve_doc
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
    value = re.sub(r'[\\/:{}\[\]|;<>?`~=\r\n\t"]', '', str(v)).strip()
    return value[:240] or "N/A"

def _mm(param):
    return "{:.2f}mm".format(round(param.AsDouble() * 304.8, 2))


def _param_text(param, default=""):
    if param is None:
        return default
    try:
        return param.AsString() or default
    except Exception:
        return default


def _type_name(element, default=""):
    try:
        value = element.Name
        if value:
            return str(value)
    except Exception:
        pass
    try:
        return _param_text(
            element.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME), default
        )
    except Exception:
        return default


def _is_grouped(element):
    try:
        return element.GroupId != ElementId.InvalidElementId
    except Exception:
        return False


def _run_transaction(label, action):
    """Run one atomic Revit write and never leave a started transaction open."""
    transaction = Transaction(doc, "T3Lab: " + label)
    try:
        if transaction.Start() != DB.TransactionStatus.Started:
            raise RuntimeError("Revit did not start the transaction.")
        options = transaction.GetFailureHandlingOptions()
        options.SetForcedModalHandling(True)
        transaction.SetFailureHandlingOptions(options)
        result = action()
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise RuntimeError("Revit did not commit the transaction.")
        return result
    except Exception:
        if transaction.GetStatus() == DB.TransactionStatus.Started:
            transaction.RollBack()
        raise
    finally:
        if transaction.GetStatus() != DB.TransactionStatus.Pending:
            transaction.Dispose()

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
    font  = _sanitize(_param_text(p, "N/A"))
    p = gp(BuiltInParameter.TEXT_WIDTH_SCALE)
    factor = "{:.2f}".format(p.AsDouble()).rstrip('0').rstrip('.') if p else "N/A"
    p = gp(BuiltInParameter.DIM_TEXT_BACKGROUND)
    bg    = _sanitize(p.AsValueString()) if p else "N/A"
    p = gp(BuiltInParameter.LINE_COLOR)
    color = _DIM_COLORS.get(_rgb(p.AsInteger()), "RGB") if p else "N/A"
    p = gp(BuiltInParameter.DIM_PREFIX)
    pref  = _sanitize(_param_text(p)) if p else "N/A"
    p = gp(BuiltInParameter.DIM_STYLE_CENTERLINE_SYMBOL)
    ctr   = "Center" if (p and p.AsElementId() != ElementId.InvalidElementId) else "N/A"
    p = gp(BuiltInParameter.SPOT_ELEV_IND_ELEVATION)
    elev  = _sanitize(_param_text(p)) if p else "N/A"
    p = gp(BuiltInParameter.SPOT_ELEV_IND_TOP)
    top   = _sanitize(_param_text(p)) if p else "N/A"
    p = gp(BuiltInParameter.SPOT_ELEV_IND_BOTTOM)
    bot   = _sanitize(_param_text(p)) if p else "N/A"

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
    font   = _sanitize(_param_text(p, "N/A").replace(" ", ""))
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
    AI_TOOL = "ManaAnno"


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

            # Debounce live search.  Rebuilding thousands of DataRows on every
            # keypress was the source of the Journal BIG_GAP on large models.
            self._dim_search_timer = DispatcherTimer()
            self._dim_search_timer.Interval = TimeSpan.FromMilliseconds(250)
            self._dim_search_timer.Tick += self._run_queued_dim_search
            self._txt_search_timer = DispatcherTimer()
            self._txt_search_timer.Interval = TimeSpan.FromMilliseconds(250)
            self._txt_search_timer.Tick += self._run_queued_txt_search

            # Sidebar list event bindings
            self.lb_dim_types.SelectionChanged += self.dim_sidebar_select_changed
            self.lb_txt_types.SelectionChanged += self.txt_sidebar_select_changed
            self.dim_sidebar_search.TextChanged += self.dim_sidebar_search_changed
            self.txt_sidebar_search.TextChanged += self.txt_sidebar_search_changed

            # Main search text changed event bindings (for placeholder behavior and instant search)
            self.dim_kw.TextChanged += self.dim_kw_changed
            self.txt_kw.TextChanged += self.txt_kw_changed

            # Four collectors total at startup (instances/types for each tool).
            # All search and sidebar operations below work from these snapshots.
            self._refresh_dim_cache()
            self._refresh_txt_cache()
            self._load_all_dims()
            self._load_all_txts()
            self._load_sidebar_lists()

            # Bind after initial population so selection sync does not run for
            # thousands of transient rows while the DataTables are built.
            self.dg_dim.SelectionChanged += self._dim_grid_selection_changed
            self.dg_txt.SelectionChanged += self._txt_grid_selection_changed

            include_groups = getattr(self, 'chk_include_groups', None)
            if include_groups is not None:
                include_groups.Click += self.settings_filter_changed

            # DimText state
            self._dimtext_rules = []

            # Register utility button click events
            self.btn_util_copy_anno.Click += self._on_launch_copier
            self.btn_util_renumber_spline.Click += self._on_launch_renumber
            self.btn_util_upper_all.Click += self._on_launch_upper_all
            tag_button = getattr(self, 'btn_util_tag_checker', None)
            if tag_button is not None:
                tag_button.Click += self._on_launch_tag_checker

            # Force initial tab content to render: nav_dim.IsChecked was already
            # True when the XAML was parsed, so no explicit SelectedIndex was ever
            # set on the headerless TabControl (same fix as ManaSheets/ManaViews).
            self.main_tabs.SelectedIndex = 0
            self._init_ai_mode()
        except Exception as ex:
            logger.error("Error initializing window: {}".format(ex))
            raise

    def _init_ai_mode(self):
        self.init_ai_badge()

    def on_txt_ai_qa_clicked(self, sender, args):
        """AI Spellcheck: typo / casing / viết tắt trong Text Note — chỉ STAGE, Apply mới ghi.

        Không có fallback bằng luật: bản cũ thay 'dam'→'DẦM', 'san'→'SÀN'… bất kể
        ngữ cảnh nên phá cả chữ tiếng Anh. AI tắt thì báo, không tự sửa gì.
        """
        if not self.ai_require():
            return
        if self._txt_submode != "notes":
            self._status("AI Spellcheck is available in Find Notes mode only.")
            return
        notes = [{"id": str(r["_id"]), "text": str(r["Name"])}
                 for r in self._txt_dt.Rows if r["Selected"]]
        if not notes:
            notes = [{"id": str(r["_id"]), "text": str(r["Name"])}
                     for r in list(self._txt_dt.Rows)[:30]]
        if not notes:
            self._status("No text notes to check.")
            return

        import json
        prompt = (
            "You are a BIM quality-assurance specialist for architectural, structural and MEP "
            "drawing annotations (Vietnamese and English).\n"
            "Review these text notes for spelling errors, irregular casing, abbreviations and "
            "inconsistencies. Keep each note's language; only fix real mistakes.\n"
            "Return JSON ONLY: {\"corrections\": [{\"id\": \"...\", \"original\": \"...\", "
            "\"suggested\": \"...\", \"issue\": \"...\"}], \"summary\": \"<one English sentence>\"}\n"
            "Notes:\n" + json.dumps(notes, ensure_ascii=False)
        )
        btn = getattr(self, 'btn_txt_ai_qa', None)
        self.ai_busy(btn, True)
        self._status("AI checking {} text note(s)...".format(len(notes)))

        def _worker():
            return self.ai_bridge.ask_json(prompt, fast=True)

        def _callback(res, err):
            self.ai_busy(btn, False)
            if err or not isinstance(res, dict) or 'corrections' not in res:
                self._status("AI returned no usable result - nothing was changed. Try again.")
                return
            fixes = {}
            for correction in res.get('corrections', []):
                if not isinstance(correction, dict):
                    continue
                elem_id = str(correction.get('id', ''))
                suggestion = correction.get('suggested')
                if elem_id in self._txt_record_by_id and isinstance(suggestion, str) and suggestion.strip():
                    fixes[elem_id] = suggestion
            updated = 0
            for row in self._txt_dt.Rows:
                rid = str(row["_id"])
                if rid in fixes and fixes[rid] != str(row["Name"]):
                    row["Name"] = fixes[rid]
                    row["Status"] = "AI Fix"
                    row["Selected"] = True
                    updated += 1
            if updated:
                self.btn_txt_apply.Visibility = Visibility.Visible
                self._status("AI staged {} fix(es) (Status 'AI Fix') - review, then Apply Changes.".format(updated))
            else:
                self._status("AI found nothing to fix in {} note(s).".format(len(notes)))

        self.run_ai_async(_worker, _callback)


    # ── helpers ─────────────────────────────────────────────────────────

    def _status(self, msg):
        self.status.Text = msg

    def _setting_enabled(self, control_name, default):
        control = getattr(self, control_name, None)
        if control is None or control.IsChecked is None:
            return default
        return bool(control.IsChecked)

    def _include_grouped(self):
        return self._setting_enabled('chk_include_groups', False)

    def _auto_select_enabled(self):
        return self._setting_enabled('chk_auto_select', True)

    def _confirm_delete_enabled(self):
        return self._setting_enabled('chk_confirm_delete', True)

    @staticmethod
    def _view_name(owner_view_id, cache):
        key = str(owner_view_id)
        if key not in cache:
            view = doc.GetElement(owner_view_id)
            cache[key] = view.Name if view is not None else ""
        return cache[key]

    def _refresh_dim_cache(self):
        self._dim_types = list(
            FilteredElementCollector(doc).OfClass(DimensionType)
            .WhereElementIsElementType()
        )
        self._dim_type_by_id = {str(item.Id): item for item in self._dim_types}
        self._dim_type_names = {
            key: _type_name(item, "<unnamed style>")
            for key, item in self._dim_type_by_id.items()
        }
        self._dim_records = []
        self._dim_record_by_id = {}
        self._dim_counts_all = {}
        self._dim_counts_ungrouped = {}
        view_names = {}
        for element in FilteredElementCollector(doc).OfClass(Dimension).WhereElementIsNotElementType():
            elem_id = str(element.Id)
            type_id = str(element.GetTypeId())
            grouped = _is_grouped(element)
            self._dim_counts_all[type_id] = self._dim_counts_all.get(type_id, 0) + 1
            if not grouped:
                self._dim_counts_ungrouped[type_id] = self._dim_counts_ungrouped.get(type_id, 0) + 1
            record = {
                "element": element,
                "id": elem_id,
                "type_id": type_id,
                "type_name": self._dim_type_names.get(type_id, "<unnamed style>"),
                "view_id": str(element.OwnerViewId),
                "view_name": self._view_name(element.OwnerViewId, view_names),
                "grouped": grouped,
            }
            self._dim_records.append(record)
            self._dim_record_by_id[elem_id] = record

    def _refresh_txt_cache(self):
        self._txt_types = list(
            FilteredElementCollector(doc).OfClass(TextNoteType)
            .WhereElementIsElementType()
        )
        self._txt_type_by_id = {str(item.Id): item for item in self._txt_types}
        self._txt_type_names = {
            key: _type_name(item, "<unnamed>")
            for key, item in self._txt_type_by_id.items()
        }
        self._txt_records = []
        self._txt_record_by_id = {}
        self._txt_counts_all = {}
        self._txt_counts_ungrouped = {}
        view_names = {}
        for element in FilteredElementCollector(doc).OfClass(TextNote).WhereElementIsNotElementType():
            elem_id = str(element.Id)
            type_id = str(element.GetTypeId())
            grouped = _is_grouped(element)
            self._txt_counts_all[type_id] = self._txt_counts_all.get(type_id, 0) + 1
            if not grouped:
                self._txt_counts_ungrouped[type_id] = self._txt_counts_ungrouped.get(type_id, 0) + 1
            record = {
                "element": element,
                "id": elem_id,
                "type_id": type_id,
                "type_name": self._txt_type_names.get(type_id, "<unnamed>"),
                "text": element.Text or "",
                "view_id": str(element.OwnerViewId),
                "view_name": self._view_name(element.OwnerViewId, view_names),
                "grouped": grouped,
            }
            self._txt_records.append(record)
            self._txt_record_by_id[elem_id] = record

    def _record_is_visible(self, record):
        return self._include_grouped() or not record["grouped"]

    def _dim_counts(self):
        return self._dim_counts_all if self._include_grouped() else self._dim_counts_ungrouped

    def _txt_counts(self):
        return self._txt_counts_all if self._include_grouped() else self._txt_counts_ungrouped

    def _replace_table(self, table, grid, populate):
        """Bulk replace rows without a WPF notification/layout pass per row."""
        was_updating = getattr(self, '_updating_grids', False)
        self._updating_grids = True
        grid.ItemsSource = None
        table.BeginLoadData()
        try:
            table.Clear()
            populate()
        finally:
            table.EndLoadData()
            grid.ItemsSource = table.DefaultView
            self._updating_grids = was_updating
        if grid is getattr(self, 'dg_dim', None):
            self._set_empty_state('dg_dim_empty', len(table.Rows) == 0)
        elif grid is getattr(self, 'dg_txt', None):
            self._set_empty_state('dg_txt_empty', len(table.Rows) == 0)

    def _set_empty_state(self, control_name, is_empty):
        control = getattr(self, control_name, None)
        if control is not None:
            control.Visibility = Visibility.Visible if is_empty else Visibility.Collapsed

    @staticmethod
    def _selected_rows(table, grid):
        selected = {}
        for row in table.Rows:
            if bool(row["Selected"]):
                selected[str(row["_id"])] = row
        if not selected:
            for row in grid.SelectedItems:
                selected[str(row["_id"])] = row
        return list(selected.values())

    @staticmethod
    def _set_header_checkbox(control, value):
        if control is not None:
            control.IsChecked = value

    def _confirm_delete(self, count, label):
        if not self._confirm_delete_enabled():
            return True
        return T3Dialog.confirm(
            "Delete {} selected {}?".format(count, label),
            title="Confirm Delete",
            ok_text="Delete",
            cancel_text="Cancel",
            danger=True,
            details="This model change can be restored with one Undo.",
            owner=self,
        )

    def _run_queued_dim_search(self, sender, args):
        self._dim_search_timer.Stop()
        self.dim_search(None, None)

    def _run_queued_txt_search(self, sender, args):
        self._txt_search_timer.Stop()
        self.txt_search(None, None)

    def settings_filter_changed(self, sender, args):
        self._dim_search_timer.Stop()
        self._txt_search_timer.Stop()
        self.dim_search(None, None)
        self.txt_search(None, None)

    def _set_revit_selection(self, element_ids):
        if uidoc is None or not element_ids:
            return
        ids = List[ElementId]()
        for element_id in element_ids:
            ids.Add(element_id)
        uidoc.Selection.SetElementIds(ids)

    def _dim_grid_selection_changed(self, sender, args):
        if getattr(self, '_updating_grids', False) or not self._auto_select_enabled():
            return
        row = self.dg_dim.SelectedItem
        if row is None or uidoc is None:
            return
        active_view_id = str(uidoc.ActiveView.Id)
        cat_code = str(row["_cat"])
        elem_id = str(row["_id"])
        if cat_code == "DimInst":
            record = self._dim_record_by_id.get(elem_id)
            matches = [record] if record and record["view_id"] == active_view_id else []
        else:
            matches = [record for record in self._dim_records
                       if record["type_id"] == elem_id
                       and record["view_id"] == active_view_id
                       and self._record_is_visible(record)]
        self._set_revit_selection([record["element"].Id for record in matches])

    def _txt_grid_selection_changed(self, sender, args):
        if getattr(self, '_updating_grids', False) or not self._auto_select_enabled():
            return
        row = self.dg_txt.SelectedItem
        if row is None or uidoc is None:
            return
        active_view_id = str(uidoc.ActiveView.Id)
        cat_code = str(row["_cat"])
        elem_id = str(row["_id"])
        if cat_code == "TxtInst":
            record = self._txt_record_by_id.get(elem_id)
            matches = [record] if record and record["view_id"] == active_view_id else []
        else:
            matches = [record for record in self._txt_records
                       if record["type_id"] == elem_id
                       and record["view_id"] == active_view_id
                       and self._record_is_visible(record)]
        self._set_revit_selection([record["element"].Id for record in matches])

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
        font = _param_text(p)
        p = gp(BuiltInParameter.DIM_TEXT_BACKGROUND)
        bg = (p.AsValueString() or "") if p else ""
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
        font = _param_text(p)
        p = gp(BuiltInParameter.TEXT_BACKGROUND)
        bg = ("Opaque" if p.AsInteger() == 0 else "Transparent") if p else ""
        p = gp(BuiltInParameter.LINE_COLOR)
        color = _TXT_COLORS.get(_rgb(p.AsInteger()), "RGB") if p else ""
        return size, font, bg, color

    def _load_all_dims(self):
        self._dim_map = {}
        counts = self._dim_counts()

        def populate():
            if self._dim_submode == "instances":
                for record in self._dim_records:
                    if not self._record_is_visible(record) or not record["view_name"]:
                        continue
                    self._dt_add(self._dim_dt, record["id"], "DimInst",
                                 record["type_name"], record["view_name"],
                                 selected=False, count="1", status="Active")
                    self._dim_map[record["id"]] = record["element"]
            else:
                for item in self._dim_types:
                    elem_id = str(item.Id)
                    try:
                        size, font, bg, color = self._get_dim_params(item)
                    except Exception:
                        size, font, bg, color = "", "", "", ""
                    count = counts.get(elem_id, 0)
                    all_count = self._dim_counts_all.get(elem_id, 0)
                    status = "Active" if count else ("Grouped only" if all_count else "Unused")
                    self._dt_add(self._dim_dt, elem_id, "DimType",
                                 self._dim_type_names.get(elem_id, "<unnamed>"),
                                 "Dimension Type", size, font, bg, color,
                                 selected=False, count=str(count), status=status)
                    self._dim_map[elem_id] = item

        self._replace_table(self._dim_dt, self.dg_dim, populate)
        self._set_header_checkbox(getattr(self, 'chk_all_dg_dim', None), False)

        n = len(self._dim_map)
        self.dim_count.Text = "{} found".format(n)
        kind = "dimension(s)" if self._dim_submode == "instances" else "type(s)"
        self._status("Loaded {} {}.".format(n, kind))

    def _load_all_txts(self):
        self._txt_map = {}
        counts = self._txt_counts()

        def populate():
            if self._txt_submode == "notes":
                for record in self._txt_records:
                    if not self._record_is_visible(record) or not record["view_name"]:
                        continue
                    # Keep the complete string.  The previous 60-character
                    # preview was later written back by edit/AI Apply, silently
                    # truncating real annotations.
                    self._dt_add(self._txt_dt, record["id"], "TxtInst",
                                 record["text"], record["view_name"],
                                 selected=False, count="1", status="Active")
                    self._txt_map[record["id"]] = record["element"]
            else:
                for item in self._txt_types:
                    elem_id = str(item.Id)
                    try:
                        size, font, bg, color = self._get_txt_params(item)
                    except Exception:
                        size, font, bg, color = "", "", "", ""
                    count = counts.get(elem_id, 0)
                    all_count = self._txt_counts_all.get(elem_id, 0)
                    status = "Active" if count else ("Grouped only" if all_count else "Unused")
                    self._dt_add(self._txt_dt, elem_id, "TxtType",
                                 self._txt_type_names.get(elem_id, "<unnamed>"),
                                 "Text Note Type", size, font, bg, color,
                                 selected=False, count=str(count), status=status)
                    self._txt_map[elem_id] = item

        self._replace_table(self._txt_dt, self.dg_txt, populate)
        self._set_header_checkbox(getattr(self, 'chk_all_dg_txt', None), False)

        n = len(self._txt_map)
        self.txt_count.Text = "{} found".format(n)
        kind = "note(s)" if self._txt_submode == "notes" else "type(s)"
        self._status("Loaded {} {}.".format(n, kind))


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
        self._dim_search_timer.Stop()
        self._txt_search_timer.Stop()
        self.Close()

    # ── Dimension sub-mode ───────────────────────────────────────────────

    def _toggle_param_cols(self, dg, show):
        vis = Visibility.Visible if show else Visibility.Collapsed
        for col in dg.Columns:
            h = str(col.Header).strip().lower() if col.Header else ""
            if h in ("size", "font", "background", "color"):
                col.Visibility = vis

    def dim_submode(self, sender, args):
        if not hasattr(self, '_dim_dt'):
            return
        self._dim_search_timer.Stop()
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

        self._dim_map = {}
        counts = self._dim_counts()

        def populate():
            if self._dim_submode == "instances":
                for record in self._dim_records:
                    if (not self._record_is_visible(record)
                            or not record["view_name"]
                            or kw not in record["type_name"].lower()):
                        continue
                    self._dt_add(self._dim_dt, record["id"], "DimInst",
                                 record["type_name"], record["view_name"],
                                 selected=False, count="1", status="Active")
                    self._dim_map[record["id"]] = record["element"]
            else:
                for item in self._dim_types:
                    elem_id = str(item.Id)
                    name = self._dim_type_names.get(elem_id, "<unnamed>")
                    if kw not in name.lower():
                        continue
                    try:
                        size, font, bg, color = self._get_dim_params(item)
                    except Exception:
                        size, font, bg, color = "", "", "", ""
                    count = counts.get(elem_id, 0)
                    all_count = self._dim_counts_all.get(elem_id, 0)
                    status = "Active" if count else ("Grouped only" if all_count else "Unused")
                    self._dt_add(self._dim_dt, elem_id, "DimType", name,
                                 "Dimension Type", size, font, bg, color,
                                 selected=False, count=str(count), status=status)
                    self._dim_map[elem_id] = item

        self._replace_table(self._dim_dt, self.dg_dim, populate)
        self._set_header_checkbox(getattr(self, 'chk_all_dg_dim', None), False)

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

        try:
            _run_transaction("Rename Dimension Type", lambda: setattr(elem, "Name", new_name))
            self._status(u"Renamed: '{}' \u2192 '{}'.".format(old_name[:40], new_name[:40]))
        except Exception as e:
            args.Cancel = True
            self._status("Rename failed: {}".format(e))
            return
        self._refresh_dim_cache()
        self._load_sidebar_lists()

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
        if view and uidoc is not None:
            try:
                uidoc.ActiveView = view
                uidoc.ShowElements(d.Id)
                self._status("Jumped to view '{}' — dimension '{}'.".format(
                    view.Name, str(row["Name"])[:40]))
            except Exception as ex:
                self._status("Could not open the dimension view: {}".format(ex))

    def dim_delete(self, sender, args):
        selected_rows = self._selected_rows(self._dim_dt, self.dg_dim)
        if not selected_rows:
            self._status("Nothing selected. Check boxes or select rows to delete.")
            return

        candidates = []
        skipped_in_use = 0
        for row in selected_rows:
            elem_id = str(row["_id"])
            if str(row["_cat"]) == "DimType" and self._dim_counts_all.get(elem_id, 0):
                skipped_in_use += 1
                continue
            elem = self._dim_map.get(elem_id)
            if elem is not None:
                candidates.append(elem)
        if not candidates:
            self._status("No elements were deleted. {} selected type(s) are still in use.".format(
                skipped_in_use))
            return
        label = "dimension element(s)" if self._dim_submode == "instances" else "unused DimensionType(s)"
        if not self._confirm_delete(len(candidates), label):
            self._status("Delete cancelled.")
            return

        result = {"deleted": 0, "errors": 0}

        def delete_elements():
            for elem in candidates:
                try:
                    doc.Delete(elem.Id)
                    result["deleted"] += 1
                except Exception:
                    result["errors"] += 1

        try:
            _run_transaction("Delete Selected Dimensions", delete_elements)
        except Exception as ex:
            self._status("Delete failed; no dimensions were changed: {}".format(ex))
            return

        self._refresh_dim_cache()
        self.dim_search(None, None)
        msg = "Deleted {} element(s).".format(result["deleted"])
        if result["errors"]:
            msg += "  ({} failed.)".format(result["errors"])
        if skipped_in_use:
            msg += "  ({} in-use type(s) skipped.)".format(skipped_in_use)
        self._status(msg)
        self._load_sidebar_lists()

    def dim_select_all(self, sender, args):
        for row in self._dim_dt.Rows:
            row["Selected"] = True
        self._set_header_checkbox(getattr(self, 'chk_all_dg_dim', None), True)

    def dim_clear_sel(self, sender, args):
        for row in self._dim_dt.Rows:
            row["Selected"] = False
        self._set_header_checkbox(getattr(self, 'chk_all_dg_dim', None), False)

    def dim_apply(self, sender, args):
        """Apply edited Name back to DimensionType elements."""
        changes = []
        for row in self._dim_dt.Rows:
            elem_id = str(row["_id"])
            elem = self._dim_map.get(elem_id)
            if not elem or str(row["_cat"]) != "DimType":
                continue
            new_name = str(row["Name"]).strip()
            if not new_name:
                continue
            if _type_name(elem) != new_name:
                changes.append((elem, new_name))
        if not changes:
            self._status("No DimensionType names needed changes.")
            return
        result = {"count": 0, "errors": 0}

        def apply_changes():
            for elem, new_name in changes:
                try:
                    elem.Name = new_name
                    result["count"] += 1
                except Exception:
                    result["errors"] += 1

        try:
            _run_transaction("Apply Dimension Type Changes", apply_changes)
        except Exception as ex:
            self._status("Apply failed; no DimensionTypes were renamed: {}".format(ex))
            return
        msg = "Applied {} rename(s).".format(result["count"])
        if result["errors"]:
            msg += "  ({} failed.)".format(result["errors"])
        self._status(msg)
        self._refresh_dim_cache()
        self._load_all_dims()
        self._load_sidebar_lists()

    def dim_rename_all(self, sender, args):
        if not T3Dialog.confirm(
                "Auto-rename all DimensionTypes in this document?",
                title="Confirm Rename", ok_text="Rename All", danger=True,
                details="All successful renames are recorded as one Undo step.", owner=self):
            return
        used_names = {name.lower() for name in self._dim_type_names.values()}
        plans = []
        for item in self._dim_types:
            origin = self._dim_type_names.get(str(item.Id), "")
            if not origin:
                continue
            used_names.discard(origin.lower())
            base_name = _dim_name(item, origin)
            new_name = base_name
            suffix = 2
            while new_name.lower() in used_names:
                new_name = "{}_{}".format(base_name, suffix)
                suffix += 1
            used_names.add(new_name.lower())
            if origin != new_name:
                plans.append((item, origin, new_name))
        if not plans:
            self._status("All DimensionTypes already match the naming standard.")
            return
        result = {"count": 0, "errors": 0}

        def rename_all():
            for item, origin, new_name in plans:
                try:
                    item.Name = new_name
                    result["count"] += 1
                except Exception as ex:
                    result["errors"] += 1
                    logger.warning("DimensionType '{}' could not be renamed to '{}': {}".format(
                        origin, new_name, ex))

        try:
            _run_transaction("Rename Dimension Types", rename_all)
        except Exception as ex:
            self._status("Rename failed; no DimensionTypes were changed: {}".format(ex))
            return
        self._status("Renamed {} DimensionType(s); {} failed.".format(
            result["count"], result["errors"]))
        self._refresh_dim_cache()
        self._load_all_dims()
        self._load_sidebar_lists()


    # ── TextNote sub-mode ────────────────────────────────────────────────

    def txt_submode(self, sender, args):
        if not hasattr(self, '_txt_dt'):
            return
        self._txt_search_timer.Stop()
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

        self._txt_map = {}
        counts = self._txt_counts()

        def populate():
            if self._txt_submode == "notes":
                for record in self._txt_records:
                    if (not self._record_is_visible(record)
                            or not record["view_name"]
                            or kw not in record["text"].lower()):
                        continue
                    self._dt_add(self._txt_dt, record["id"], "TxtInst",
                                 record["text"], record["view_name"],
                                 selected=False, count="1", status="Active")
                    self._txt_map[record["id"]] = record["element"]
            else:
                for item in self._txt_types:
                    elem_id = str(item.Id)
                    name = self._txt_type_names.get(elem_id, "<unnamed>")
                    if kw not in name.lower():
                        continue
                    try:
                        size, font, bg, color = self._get_txt_params(item)
                    except Exception:
                        size, font, bg, color = "", "", "", ""
                    count = counts.get(elem_id, 0)
                    all_count = self._txt_counts_all.get(elem_id, 0)
                    status = "Active" if count else ("Grouped only" if all_count else "Unused")
                    self._dt_add(self._txt_dt, elem_id, "TxtType", name,
                                 "Text Note Type", size, font, bg, color,
                                 selected=False, count=str(count), status=status)
                    self._txt_map[elem_id] = item

        self._replace_table(self._txt_dt, self.dg_txt, populate)
        self._set_header_checkbox(getattr(self, 'chk_all_dg_txt', None), False)

        n = len(self._txt_map)
        self.txt_count.Text = "{} found".format(n)
        self._status("Found {} {} matching '{}'.".format(
            n, "note(s)" if self._txt_submode == "notes" else "type(s)", kw))

    def txt_cell_edit_ending(self, sender, args):
        col_header = str(args.Column.Header)
        if col_header != "TEXT / NAME":
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

        try:
            attr_name = "Name" if cat_code == "TxtType" else "Text"
            _run_transaction(
                "Rename Text Note Type" if cat_code == "TxtType" else "Edit Text Note",
                lambda: setattr(elem, attr_name, new_name),
            )
            self._status(u"Renamed: '{}' \u2192 '{}'.".format(old_name[:40], new_name[:40]))
        except Exception as e:
            args.Cancel = True
            self._status("Rename failed: {}".format(e))
            return
        self._refresh_txt_cache()
        self._load_sidebar_lists()

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
        if view and uidoc is not None:
            try:
                uidoc.ActiveView = view
                uidoc.ShowElements(tn.Id)
                note_text = str(row["Name"]).replace("\r", " ").replace("\n", " ")
                self._status("Jumped to view '{}' — note: '{}'.".format(
                    view.Name, note_text[:40]))
            except Exception as ex:
                self._status("Could not open the Text Note view: {}".format(ex))

    def txt_delete(self, sender, args):
        selected_rows = self._selected_rows(self._txt_dt, self.dg_txt)
        if not selected_rows:
            self._status("Nothing selected. Check boxes or select rows to delete.")
            return
        label = "note instance(s)" if self._txt_submode == "notes" else "TextNoteType(s)"
        candidates = []
        skipped_in_use = 0
        for row in selected_rows:
            elem_id = str(row["_id"])
            if str(row["_cat"]) == "TxtType" and self._txt_counts_all.get(elem_id, 0):
                skipped_in_use += 1
                continue
            elem = self._txt_map.get(elem_id)
            if elem is not None:
                candidates.append(elem)
        if not candidates:
            self._status("No elements were deleted. {} selected type(s) are still in use.".format(
                skipped_in_use))
            return
        if not self._confirm_delete(len(candidates), label):
            self._status("Delete cancelled.")
            return
        result = {"deleted": 0, "errors": 0}

        def delete_elements():
            for elem in candidates:
                try:
                    doc.Delete(elem.Id)
                    result["deleted"] += 1
                except Exception:
                    result["errors"] += 1

        try:
            _run_transaction("Delete Selected Text Notes", delete_elements)
        except Exception as ex:
            self._status("Delete failed; no Text Notes were changed: {}".format(ex))
            return
        self._refresh_txt_cache()
        self.txt_search(None, None)
        msg = "Deleted {} {}.".format(result["deleted"], label)
        if result["errors"]:
            msg += "  ({} could not be deleted.)".format(result["errors"])
        if skipped_in_use:
            msg += "  ({} in-use type(s) skipped.)".format(skipped_in_use)
        self._status(msg)
        self._load_sidebar_lists()

    def txt_select_all(self, sender, args):
        for row in self._txt_dt.Rows:
            row["Selected"] = True
        self._set_header_checkbox(getattr(self, 'chk_all_dg_txt', None), True)

    def txt_clear_sel(self, sender, args):
        for row in self._txt_dt.Rows:
            row["Selected"] = False
        self._set_header_checkbox(getattr(self, 'chk_all_dg_txt', None), False)

    def txt_apply(self, sender, args):
        """Apply edited Name/Text back to TextNoteType or TextNote instances."""
        changes = []
        for row in self._txt_dt.Rows:
            elem_id = str(row["_id"])
            elem = self._txt_map.get(elem_id)
            if not elem:
                continue
            new_name = str(row["Name"]).strip()
            if not new_name:
                continue
            cat_code = str(row["_cat"])
            current = _type_name(elem) if cat_code == "TxtType" else (elem.Text or "")
            if current != new_name:
                changes.append((elem, cat_code, new_name))
        if not changes:
            self._status("No Text Note changes needed to be applied.")
            return
        result = {"count": 0, "errors": 0}

        def apply_changes():
            for elem, cat_code, new_name in changes:
                try:
                    if cat_code == "TxtType":
                        elem.Name = new_name
                    else:
                        elem.Text = new_name
                    result["count"] += 1
                except Exception:
                    result["errors"] += 1

        try:
            _run_transaction("Apply Text Note Changes", apply_changes)
        except Exception as ex:
            self._status("Apply failed; no Text Notes were changed: {}".format(ex))
            return
        msg = "Applied {} text change(s).".format(result["count"])
        if result["errors"]:
            msg += "  ({} failed.)".format(result["errors"])
        self._status(msg)
        self._refresh_txt_cache()
        self._load_all_txts()
        self._load_sidebar_lists()
        self.btn_txt_apply.Visibility = (
            Visibility.Visible if self._txt_submode == "types" else Visibility.Collapsed
        )

    def txt_rename_all(self, sender, args):
        if not T3Dialog.confirm(
                "Auto-rename all TextNoteTypes in this document?",
                title="Confirm Rename", ok_text="Rename All", danger=True,
                details="All successful renames are recorded as one Undo step.", owner=self):
            return
        used_names = {name.lower() for name in self._txt_type_names.values()}
        plans = []
        for item in self._txt_types:
            origin = self._txt_type_names.get(str(item.Id), "")
            if not origin:
                continue
            used_names.discard(origin.lower())
            base_name = _txt_name(item, origin)
            new_name = base_name
            suffix = 2
            while new_name.lower() in used_names:
                new_name = "{}_{}".format(base_name, suffix)
                suffix += 1
            used_names.add(new_name.lower())
            if origin != new_name:
                plans.append((item, origin, new_name))
        if not plans:
            self._status("All TextNoteTypes already match the naming standard.")
            return
        result = {"count": 0, "errors": 0}

        def rename_all():
            for item, origin, new_name in plans:
                try:
                    item.Name = new_name
                    result["count"] += 1
                except Exception as ex:
                    result["errors"] += 1
                    logger.warning("TextNoteType '{}' could not be renamed to '{}': {}".format(
                        origin, new_name, ex))

        try:
            _run_transaction("Rename Text Note Types", rename_all)
        except Exception as ex:
            self._status("Rename failed; no TextNoteTypes were changed: {}".format(ex))
            return
        self._status("Renamed {} TextNoteType(s); {} failed.".format(
            result["count"], result["errors"]))
        self._refresh_txt_cache()
        self._load_all_txts()
        self._load_sidebar_lists()


    # ── Header Checkbox Toggle Event Handlers ─────────────────────────────────

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

    def _launch_utility(self, callback, label, refresh_annotations=True):
        self.Hide()
        succeeded = False
        try:
            callback()
            succeeded = True
        except Exception as ex:
            logger.exception("{} failed".format(label))
            self._status("{} failed: {}".format(label, ex))
        finally:
            self.Show()
        if succeeded and refresh_annotations:
            self._refresh_dim_cache()
            self._refresh_txt_cache()
            self.dim_search(None, None)
            self.txt_search(None, None)
            self._load_sidebar_lists()

    def _on_launch_copier(self, sender, e):
        self._launch_utility(CopyAnnotationDialog.show_dialog, "Annotation Copier")

    def _on_launch_renumber(self, sender, e):
        self._launch_utility(RenumberAlongSpline.run, "Renumber Along Spline")

    def _on_launch_upper_all(self, sender, e):
        self._launch_utility(UpperAll.run, "Uppercase Converter")

    def _on_launch_tag_checker(self, sender, e):
        self._launch_utility(TagCheckerDialog.show_dialog, "Tag Checker", False)

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
        rd = DimTextDialog.create_rule_row(self, self._dimtext_remove_rule)
        self._dimtext_rules.append(rd)
        self.sp_rules.Children.Add(rd["panel"])

    def _dimtext_remove_rule(self, rd):
        self.sp_rules.Children.Remove(rd["panel"])
        if rd in self._dimtext_rules:
            self._dimtext_rules.remove(rd)

    def _dimtext_build_filter_fn(self):
        if not self.chk_filter_enable.IsChecked or not self._dimtext_rules:
            return None
        return DimTextDialog.build_filter_fn(self._dimtext_rules,
                                             self.combo_combine.SelectedIndex == 0)

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
            self._status("DimText: no dimensions found in {}. Select dimensions or "
                         "choose 'All dimensions in active view'.".format(scope))
            return

        try:
            DimTextDialog.apply_dim_text(dims, prefix, suffix, above, below, override,
                                         leader_off, filter_fn)
        except Exception as ex:
            self._status("DimText: failed ({}). Nothing was modified.".format(ex))
            return

        note = " (filter active)" if filter_fn else ""
        self._status("DimText: processed {} dim(s) in {}{}.".format(len(dims), scope, note))

    # ── Sidebar Browsing & Live Filtering List Event Handlers ────────────────

    def _load_sidebar_lists(self):
        self._all_dim_type_names = sorted(set(
            name for name in self._dim_type_names.values() if name
        ), key=lambda value: value.lower())
        self._all_txt_type_names = sorted(set(
            name for name in self._txt_type_names.values() if name
        ), key=lambda value: value.lower())

        self.filter_sidebar_dim_list()
        self.filter_sidebar_txt_list()

    def filter_sidebar_dim_list(self):
        kw = self.dim_sidebar_search.Text.strip().lower()
        self.lb_dim_types.Items.Clear()
        for name in self._all_dim_type_names:
            if not kw or kw in name.lower():
                self.lb_dim_types.Items.Add(name)
        self._set_empty_state('lb_dim_types_empty', self.lb_dim_types.Items.Count == 0)

    def filter_sidebar_txt_list(self):
        kw = self.txt_sidebar_search.Text.strip().lower()
        self.lb_txt_types.Items.Clear()
        for name in self._all_txt_type_names:
            if not kw or kw in name.lower():
                self.lb_txt_types.Items.Add(name)
        self._set_empty_state('lb_txt_types_empty', self.lb_txt_types.Items.Count == 0)

    def dim_sidebar_select_changed(self, sender, args):
        selected_item = self.lb_dim_types.SelectedItem
        if selected_item:
            self.dim_kw.Text = selected_item
            self.dim_kw_placeholder.Visibility = Visibility.Collapsed

    def txt_sidebar_select_changed(self, sender, args):
        selected_item = self.lb_txt_types.SelectedItem
        if selected_item:
            self.txt_kw.Text = selected_item
            self.txt_kw_placeholder.Visibility = Visibility.Collapsed

    def dim_sidebar_search_changed(self, sender, args):
        self.dim_sidebar_placeholder.Visibility = Visibility.Collapsed if self.dim_sidebar_search.Text else Visibility.Visible
        self.filter_sidebar_dim_list()

    def txt_sidebar_search_changed(self, sender, args):
        self.txt_sidebar_placeholder.Visibility = Visibility.Collapsed if self.txt_sidebar_search.Text else Visibility.Visible
        self.filter_sidebar_txt_list()

    # ── Keyword Instant Search Changed Event Handlers ───────────────────────

    def dim_kw_changed(self, sender, args):
        self.dim_kw_placeholder.Visibility = Visibility.Collapsed if self.dim_kw.Text else Visibility.Visible
        self._dim_search_timer.Stop()
        self._dim_search_timer.Start()

    def txt_kw_changed(self, sender, args):
        self.txt_kw_placeholder.Visibility = Visibility.Collapsed if self.txt_kw.Text else Visibility.Visible
        self._txt_search_timer.Stop()
        self._txt_search_timer.Start()

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
    if doc is None or uidoc is None:
        T3Dialog.show_error(
            "No active Revit document is available.",
            title="ManaAnno",
            details="Open a project and a graphical view, then run ManaAnno again.",
        )
        return
    logger.info("Annotation Manager started")
    win = AnnotationManagerWindow()
    win.ShowDialog()

if __name__ == '__main__':
    show_dialog()
