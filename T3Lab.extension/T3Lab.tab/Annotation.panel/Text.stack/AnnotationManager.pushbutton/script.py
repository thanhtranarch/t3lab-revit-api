# -*- coding: utf-8 -*-
"""
Annotation Manager
------------------
Unified tool combining Dimension and Text Note management:
  - Find elements by keyword → jump to view
  - Delete selected instances / types
  - Double-click Name cell to rename inline (types and text note content)
  - Auto-rename all types based on their properties

--------------------------------------------------------
Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
--------------------------------------------------------
"""

__title__   = "Annotation Manager"
__author__  = "Tran Tien Thanh"
__version__ = "1.0.1"

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

# Path setup
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()
output = script.get_output()
doc    = revit.doc
uidoc  = revit.uidoc
REVIT_VERSION = int(revit.doc.Application.VersionNumber)

# ============================================================
# NAMING STRUCTURE CONFIGURATION (EASILY CUSTOMIZED)
# ============================================================
# Tokens will be replaced with their evaluated values. Unused tokens (value is None or empty) are removed.
NAMING_TEMPLATES = {
    "Dimension": {
        "Prefix": "LB",
        "Separator": "_",
        "Fields": [
            "Prefix",          # E.g. "LB"
            "Discipline",      # E.g. "ARC" or "STR"
            "Size",            # E.g. "2.50mm"
            "Font",            # E.g. "Arial"
            "WidthScale",      # E.g. "0.7"
            "Background",      # E.g. "Transparent" or "Opaque"
            "Color",           # E.g. "Red" (skipped if Black)
            "CenterSymbol",    # E.g. "Center"
            "PrefixText",      # E.g. Prefix parameter of Dimension
            "ElevationText",   # E.g. Elevation indicators
            "Rounding"         # E.g. "Round1" or "Round0.1"
        ]
    },
    "TextNote": {
        "Prefix": "LB",
        "Separator": "_",
        "Fields": [
            "Prefix",          # E.g. "LB"
            "Discipline",      # E.g. "ARC" or "STR"
            "Size",            # E.g. "2.50mm"
            "Font",            # E.g. "Arial"
            "WidthScale",      # E.g. "0.7"
            "Background",      # E.g. "Transparent" or "Opaque"
            "Color",           # E.g. "Red" (skipped if Black)
            "Border",          # E.g. "Border"
            "TextStyles"       # E.g. Bold/Underline/Italic "BUI"
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
        "Prefix": NAMING_TEMPLATES["Dimension"]["Prefix"],
        "Discipline": discipline,
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
        "Prefix": NAMING_TEMPLATES["TextNote"]["Prefix"],
        "Discipline": discipline,
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
_GUI_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
    'lib', 'GUI'
)
_XAML_PATH = os.path.join(_GUI_DIR, 'Tools', 'AnnotationManager.xaml')

# ============================================================
# WINDOW CLASS
# ============================================================
class AnnotationManagerWindow(forms.WPFWindow):

    def __init__(self):
        try:
            forms.WPFWindow.__init__(self, _XAML_PATH)
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
        except Exception as ex:
            logger.error("Error initializing window: {}".format(ex))
            raise

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
                        d_name = d.Name or "<unnamed>"
                    except Exception:
                        try:
                            _p = d.DimensionType.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                            d_name = _p.AsString() if _p else "<unnamed>"
                        except Exception:
                            d_name = "<unnamed>"
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
        self._dim_submode = "instances" if self.rb_dim_inst.IsChecked else "types"
        is_type = self._dim_submode == "types"
        self.btn_dim_jump.IsEnabled = not is_type
        if hasattr(self, 'dim_lbl'):
            self.dim_lbl.Text = "Name:" if not is_type else "Type name:"
        self._toggle_param_cols(self.dg_dim, is_type)
        self.btn_dim_apply.Visibility = Visibility.Visible if is_type else Visibility.Collapsed
        self._load_all_dims()

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
                    d_name = d.Name or ""
                except Exception:
                    try:
                        _p = d.DimensionType.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
                        d_name = _p.AsString() if _p else ""
                    except Exception:
                        d_name = ""
                if kw in d_name.lower():
                    view = doc.GetElement(d.OwnerViewId)
                    if view:
                        self._dt_add(self._dim_dt, str(d.Id), "DimInst",
                                     d_name or "<unnamed>", view.Name,
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
                if elem.Name != new_name:
                    elem.Name = new_name
                    count += 1
            except Exception:
                errors += 1
        t.Commit()
        msg = "Applied {} rename(s).".format(count)
        if errors:
            msg += "  ({} failed.)".format(errors)
        self._status(msg)
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

    # ── TextNote sub-mode ────────────────────────────────────────────────

    def txt_submode(self, sender, args):
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
        self.btn_txt_apply.Visibility = Visibility.Visible if is_type else Visibility.Collapsed
        self._load_all_txts()

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
        """Apply edited Name back to TextNoteType elements."""
        t = Transaction(doc, "Apply Text Note Type Changes")
        t.Start()
        count = 0
        errors = 0
        for row in self._txt_dt.Rows:
            elem_id = str(row["_id"])
            elem = self._txt_map.get(elem_id)
            if not elem or str(row["_cat"]) != "TxtType":
                continue
            new_name = str(row["Name"]).strip()
            if not new_name:
                continue
            try:
                if elem.Name != new_name:
                    elem.Name = new_name
                    count += 1
            except Exception:
                errors += 1
        t.Commit()
        msg = "Applied {} rename(s).".format(count)
        if errors:
            msg += "  ({} failed.)".format(errors)
        self._status(msg)
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
                    origin = tt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
                    tt.Name = _txt_name(tt, origin)
                    count += 1
                except Exception:
                    pass
        finally:
            t.Commit()
        self._status("Renamed {} TextNoteType(s).".format(count))
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

    def nav_dimensions_checked(self, sender, args):
        if hasattr(self, 'main_tabs'):
            self.main_tabs.SelectedIndex = 0

    def nav_textnotes_checked(self, sender, args):
        if hasattr(self, 'main_tabs'):
            self.main_tabs.SelectedIndex = 1

    def nav_settings_checked(self, sender, args):
        if hasattr(self, 'main_tabs'):
            self.main_tabs.SelectedIndex = 2

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


# MAIN SCRIPT
# ==================================================
if __name__ == '__main__':
    if not revit.doc:
        forms.alert("Please open a Revit document first.", exitscript=True)
    logger.info("Annotation Manager started")
    win = AnnotationManagerWindow()
    win.ShowDialog()
