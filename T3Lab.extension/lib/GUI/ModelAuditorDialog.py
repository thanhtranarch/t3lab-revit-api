# -*- coding: utf-8 -*-
"""Model Auditor — Consolidated logic and event handlers for the Model Auditor dashboard."""

import os
import sys
import json
import codecs
import datetime
import re
import csv
import traceback
from collections import OrderedDict, defaultdict

try:
    _unicode = unicode
except NameError:
    _unicode = str

def _open_csv_write(filepath):
    if sys.version_info[0] >= 3:
        return open(filepath, "w", newline="", encoding="utf-8")
    return open(filepath, "wb")

def _csv_cell(val):
    if val is None:
        return ""
    if sys.version_info[0] >= 3:
        return str(val)
    return val.encode("utf-8") if isinstance(val, _unicode) else str(val)

import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

import System

from pyrevit import forms, DB, script, revit
from GUI.WPF_Base import T3WPFWindow, to_items_source

logger = script.get_logger()
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, ElementId, Transaction,
    Family, FamilyInstance, FamilyInstanceFilter, ImportInstance, RevitLinkInstance,
    View, ViewSheet, Group, DesignOption, ReferencePlane, CurveElement, FilledRegion
)

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'ModelAuditor.xaml')
_XAML = os.path.normpath(_XAML)

# Ensure lib/ is on sys.path so `from Services.ModelAuditor...` resolves
_lib_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

# ============================================================================
# REVIT VERSION COMPATIBILITY (2020 - 2027+)
# ============================================================================
from Snippets._compat import make_eid, eid_value

def _eid_int(eid):
    """Get integer value from ElementId - compatible with Revit 2020-2027+"""
    return eid_value(eid)

def _make_eid(value):
    """Construct an ElementId safely for Revit 2020-2027+."""
    return make_eid(value)

def _eid_list(ids):
    """Build a .NET List[ElementId] from a python iterable.

    Under PythonNet 3 (CPython) a plain python list no longer converts to
    IEnumerable<ElementId> during overload resolution, so
    `List[ElementId](some_python_list)` raises:
        No method matches given arguments for List`1..ctor: (<class 'list'>)
    Build it empty and Add() each item instead.
    """
    out = System.Collections.Generic.List[ElementId]()
    for eid in ids:
        out.Add(eid)
    return out

# ============================================================================
# METRIC THRESHOLDS FOR HEALTH CHECK
# ============================================================================
METRIC_THRESHOLDS = OrderedDict([
    ("file_size_mb", {
        "label": "File Size (MB)",
        "thresholds": [100, 250, 500, 750, 1000],
        "tooltip": "Model file size. Large files slow loading and sync.",
        "unit": "MB",
        "selectable": False,
        "weight": 4,
        "recommendation": "Purge unused families, remove imported CAD files, audit model."
    }),
    ("warnings", {
        "label": "Warnings",
        "thresholds": [100, 500, 1000, 2000, 5000],
        "tooltip": "Total warnings. High count = model instability.",
        "unit": "",
        "selectable": False,
        "weight": 5,
        "recommendation": "Review and resolve warnings. Start with most frequent types."
    }),
    ("cad_imports", {
        "label": "CAD Imports",
        "thresholds": [0, 2, 5, 7, 10],
        "tooltip": "Imported CAD (not linked). Bloats file size significantly.",
        "unit": "",
        "selectable": True,
        "weight": 5,
        "recommendation": "Delete imported CAD. Use linked CAD instead."
    }),
    ("in_place_families", {
        "label": "In-Place Families",
        "thresholds": [5, 15, 30, 60, 100],
        "tooltip": "In-Place families can't be reused, increase file size.",
        "unit": "",
        "selectable": True,
        "weight": 4,
        "recommendation": "Convert In-Place to loadable families."
    }),
    ("rvt_links", {
        "label": "RVT Links",
        "thresholds": [10, 20, 35, 50, 80],
        "tooltip": "Linked Revit files. Too many = slow performance.",
        "unit": "",
        "selectable": True,
        "weight": 2,
        "recommendation": "Review if all RVT links are necessary. Unload unused."
    }),
    ("worksets", {
        "label": "Worksets",
        "thresholds": [10, 20, 30, 40, 50],
        "tooltip": "User worksets. Excessive worksets complicate management.",
        "unit": "",
        "selectable": False,
        "weight": 1,
        "recommendation": "Consolidate worksets if possible."
    }),
    ("cad_links", {
        "label": "CAD Links",
        "thresholds": [10, 25, 50, 80, 120],
        "tooltip": "Linked CAD files. Many links degrade navigation.",
        "unit": "",
        "selectable": True,
        "weight": 3,
        "recommendation": "Minimize CAD links. Convert to native Revit elements."
    }),
    ("views", {
        "label": "Views",
        "thresholds": [200, 500, 1000, 2000, 4000],
        "tooltip": "Total views. Too many slow file open/save.",
        "unit": "",
        "selectable": True,
        "weight": 3,
        "recommendation": "Delete unused views. Use View Templates."
    }),
    ("sheets", {
        "label": "Sheets",
        "thresholds": [100, 200, 400, 600, 1000],
        "tooltip": "Total sheets with placed views increase file size.",
        "unit": "",
        "selectable": True,
        "weight": 2,
        "recommendation": "Archive completed sheets. Remove test sheets."
    }),
    ("groups", {
        "label": "Groups",
        "thresholds": [20, 50, 100, 200, 500],
        "tooltip": "Model and Detail Groups cause performance issues.",
        "unit": "",
        "selectable": True,
        "weight": 3,
        "recommendation": "Ungroup where possible. Use families instead."
    }),
    ("design_options", {
        "label": "Design Options",
        "thresholds": [3, 5, 8, 15, 20],
        "tooltip": "Design Options add complexity and memory usage.",
        "unit": "",
        "selectable": True,
        "weight": 1,
        "recommendation": "Finalize and accept primary design options."
    }),
    ("reference_planes", {
        "label": "Ref. Planes",
        "thresholds": [100, 200, 500, 800, 1500],
        "tooltip": "Leftover reference planes clutter the model.",
        "unit": "",
        "selectable": True,
        "weight": 1,
        "recommendation": "Delete unnamed/unnecessary reference planes."
    }),
    ("detail_lines", {
        "label": "Detail Lines",
        "thresholds": [1000, 5000, 10000, 25000, 50000],
        "tooltip": "Excessive detail lines = drafting overuse.",
        "unit": "",
        "selectable": True,
        "weight": 2,
        "recommendation": "Review detail lines. Use line-based detail components."
    }),
    ("filled_regions", {
        "label": "Filled Regions",
        "thresholds": [100, 500, 1000, 3000, 5000],
        "tooltip": "Many filled regions slow view rendering.",
        "unit": "",
        "selectable": True,
        "weight": 2,
        "recommendation": "Minimize filled regions. Use material hatching."
    }),
    ("rooms_unplaced", {
        "label": "Unplaced Rooms",
        "thresholds": [0, 5, 15, 30, 50],
        "tooltip": "Unplaced rooms cause errors in schedules.",
        "unit": "",
        "selectable": True,
        "weight": 2,
        "recommendation": "Place or delete unplaced rooms."
    }),
    ("linked_dwg_not_pinned", {
        "label": "Unpinned Links",
        "thresholds": [0, 3, 8, 15, 30],
        "tooltip": "Unpinned links can be accidentally moved.",
        "unit": "",
        "selectable": True,
        "weight": 2,
        "recommendation": "Pin all linked files to prevent accidental movement."
    }),
    ("duplicate_elements", {
        "label": "Duplicate Elements",
        "thresholds": [0, 10, 30, 60, 100],
        "tooltip": "Elements of same type overlapping at same location.",
        "unit": "",
        "selectable": True,
        "weight": 4,
        "recommendation": "Review and delete overlapping duplicate elements."
    })
])

# ============================================================================
# CONFIGURABLE THRESHOLDS/WEIGHTS + RAG STATUS + HISTORY
# (Mirrors Autodesk Model Analytics: company/project-configurable health
#  indicators, red/orange/green health status, and historical score trends.
#  https://help.autodesk.com/view/MODALY/ENU/?guid=MODALY_Understanding_Data_ama_reports_html)
# ============================================================================
_CONFIG_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), 'Resources', 'model_auditor_thresholds.json'))
_HISTORY_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), 'Resources', 'ModelAuditorHistory'))


def _save_metric_config():
    """Write current thresholds/weights to disk so they can be tuned per company/project."""
    data = OrderedDict()
    for key, info in METRIC_THRESHOLDS.items():
        data[key] = {"thresholds": info["thresholds"], "weight": info["weight"]}
    try:
        with codecs.open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _load_metric_config():
    """Overlay user-editable thresholds/weights from model_auditor_thresholds.json onto the defaults.
    If the file doesn't exist yet, seed it from the built-in defaults above."""
    if not os.path.isfile(_CONFIG_PATH):
        _save_metric_config()
        return
    try:
        with codecs.open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            overrides = json.load(f)
    except Exception:
        return
    for key, cfg in overrides.items():
        if key not in METRIC_THRESHOLDS:
            continue
        if "thresholds" in cfg and len(cfg["thresholds"]) == 5:
            METRIC_THRESHOLDS[key]["thresholds"] = cfg["thresholds"]
        if "weight" in cfg:
            METRIC_THRESHOLDS[key]["weight"] = cfg["weight"]


_load_metric_config()


# ============================================================================
# STATUS COLOURS — three T3 token families, never a hand-mixed gradient
# ----------------------------------------------------------------------------
# The six status names carry the detail; the colour only has to say "fine /
# look at this / act now". Painting them from T3.Success/Warning/Danger keeps
# the table, the score badge and the legend strip above the table in step —
# they used to drift because the legend read the stylesheet while the rows
# were painted from hardcoded hexes.
# ============================================================================
_STATUS_TOKENS = OrderedDict([
    ("Good",       ("T3.Success.Fill", "T3.Success.Text")),
    ("Acceptable", ("T3.Success.Fill", "T3.Success.Text")),
    ("Warning",    ("T3.Warning.Fill", "T3.Warning.Text")),
    ("Concerning", ("T3.Warning.Fill", "T3.Warning.Text")),
    ("Critical",   ("T3.Danger.Fill",  "T3.Danger.Text")),
    ("Severe",     ("T3.Danger.Fill",  "T3.Danger.Text")),
])
# Ordered from best to worst; index N is the status for value <= thresholds[N].
_STATUS_ORDER = list(_STATUS_TOKENS)
_STATUS_ATTENTION = ("Warning", "Concerning", "Critical", "Severe")

# Tên họ màu cho DataTrigger trong XAML. Pill của bảng và của danh sách
# khuyến nghị tô màu qua chuỗi này chứ không nhận Brush qua binding: dưới
# PythonNet, Brush đặt trên thuộc tính của một đối tượng Python không đi qua
# được binding tới Background/Foreground (chuỗi thì đi qua được), nên pill
# hiện ra chữ mà không có nền.
_STATUS_SEVERITY = OrderedDict([
    ("Good",       "Success"),
    ("Acceptable", "Success"),
    ("Warning",    "Warning"),
    ("Concerning", "Warning"),
    ("Critical",   "Danger"),
    ("Severe",     "Danger"),
])

_GRADE_TOKENS = {
    "A": ("T3.Success.Fill", "T3.Success.Text"),
    "B": ("T3.Success.Fill", "T3.Success.Text"),
    "C": ("T3.Warning.Fill", "T3.Warning.Text"),
    "D": ("T3.Danger.Fill",  "T3.Danger.Text"),
    "F": ("T3.Danger.Fill",  "T3.Danger.Text"),
}


def _status_for(value, thresholds):
    """Map a metric value onto one of the six status names."""
    for idx, limit in enumerate(thresholds):
        if value <= limit:
            return _STATUS_ORDER[idx]
    return _STATUS_ORDER[-1]


def _rag_status(score):
    """Red/Amber/Green classification matching Autodesk Model Analytics'
    health-check indicators. Returns (label, fill token, text token)."""
    if score >= 75:
        return "Green", "T3.Success.Fill", "T3.Success.Text"
    elif score >= 40:
        return "Amber", "T3.Warning.Fill", "T3.Warning.Text"
    return "Red", "T3.Danger.Fill", "T3.Danger.Text"


def _history_file_for_doc(doc):
    name = os.path.basename(doc.PathName) if doc.PathName else doc.Title
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', name) or "UnsavedProject"
    if not os.path.isdir(_HISTORY_DIR):
        try:
            os.makedirs(_HISTORY_DIR)
        except Exception:
            pass
    return os.path.join(_HISTORY_DIR, safe + '.json')


def _load_history(doc):
    path = _history_file_for_doc(doc)
    if not os.path.isfile(path):
        return []
    try:
        with codecs.open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _append_history(doc, record):
    """Append a health-run snapshot (timestamp, score, grade, RAG) and keep the last 50 runs."""
    history = _load_history(doc)
    history.append(record)
    history = history[-50:]
    try:
        with codecs.open(_history_file_for_doc(doc), 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2)
    except Exception:
        pass
    return history


# ============================================================================
# MODEL HEALTH ANALYZER
# ============================================================================
class ModelHealthAnalyzer(object):
    """Collects the 17 health metrics with one pass per Revit class.

    Each collection is fetched once and shared between the metrics that need
    it. ImportInstance used to be walked three times (CAD imports, CAD links,
    unpinned links), RevitLinkInstance twice, View twice and GetWarnings()
    twice; on a large model those repeat passes dominated the run.
    """

    def __init__(self, doc):
        self.doc = doc
        self.metrics = OrderedDict()
        self.element_ids = {}
        self._cache = {}

    # ── shared collections ───────────────────────────────────────────────
    def _of_class(self, cls):
        """Full elements of `cls`, collected once per analyze() run.

        Only for metrics that must read a property off each element. Anything
        that just needs a count and a selection list uses _ids_of_class(),
        which never builds the managed wrappers.
        """
        key = cls.__name__
        if key not in self._cache:
            try:
                self._cache[key] = list(
                    FilteredElementCollector(self.doc)
                    .OfClass(cls).WhereElementIsNotElementType())
            except Exception:
                self._cache[key] = []
        return self._cache[key]

    def _ids_of_class(self, cls, category=None):
        """Element ids only — Revit answers from its own index and never
        marshals Element objects into Python. This is the cheap path and most
        metrics are on it: they need a number and a list to select, nothing
        that has to be read off the element itself."""
        try:
            col = FilteredElementCollector(self.doc)
            if category is not None:
                col = col.OfCategory(category)
            return list(col.OfClass(cls).WhereElementIsNotElementType().ToElementIds())
        except Exception as ex:
            logger.debug("Id collect for %s failed: %s", cls.__name__, ex)
            return []

    def _warning_list(self):
        if "warnings" not in self._cache:
            try:
                found = self.doc.GetWarnings()
                self._cache["warnings"] = list(found) if found else []
            except Exception:
                self._cache["warnings"] = []
        return self._cache["warnings"]

    def _record(self, key, elements):
        """Store a metric and the element ids behind it in one step.

        The ElementId objects are kept as they come off the element: converting
        each one to an int here cost an extra interop call per element across a
        dozen metrics, and 'Select in model' needs ElementIds back anyway.

        The count comes from the ids actually captured, so a dead element whose
        Id cannot be read is left out of both — otherwise the metric would
        claim rows that 'Select in model' has no ids for.
        """
        ids = []
        for el in elements:
            try:
                if el is not None:
                    ids.append(el.Id)
            except Exception:
                continue
        self._record_ids(key, ids)

    def _record_ids(self, key, ids):
        """Store a metric straight from a list of ElementIds."""
        ids = list(ids)
        self.metrics[key] = len(ids)
        self.element_ids[key] = ids

    # ── driver ───────────────────────────────────────────────────────────
    def analyze(self, progress_callback=None, cancel_check=None):
        self._cache = {}
        steps = [
            (self._file_size, "Calculating file size"),
            (self._warnings, "Collecting warnings"),
            (self._imports_and_links, "Scanning CAD imports and links"),
            (self._in_place_families, "Scanning in-place families"),
            (self._revit_links, "Scanning Revit links"),
            (self._worksets, "Scanning worksets"),
            (self._views_and_sheets, "Scanning views and sheets"),
            (self._groups, "Scanning groups"),
            (self._design_options, "Scanning design options"),
            (self._reference_planes, "Scanning reference planes"),
            (self._detail_lines, "Scanning detail lines"),
            (self._filled_regions, "Scanning filled regions"),
            (self._unplaced_rooms, "Scanning unplaced rooms"),
            (self._duplicate_elements, "Scanning duplicate elements"),
        ]

        total = len(steps)
        for idx, (step, name) in enumerate(steps):
            if cancel_check and cancel_check():
                break
            if progress_callback:
                progress_callback(int((idx / float(total)) * 100), name)
            try:
                step()
            except Exception as ex:
                logger.debug("Health metric '%s' failed: %s", name, ex)

        # A metric that never ran - cancelled, or raised - still needs a value,
        # otherwise the weighted score is computed from a partial set.
        for key in METRIC_THRESHOLDS:
            self.metrics.setdefault(key, 0)

        # Drop the shared collections. Only the ElementIds are worth keeping
        # between runs; holding the Element objects would pin every wrapper the
        # scan touched for as long as the window stays open.
        self._cache = {}

        if progress_callback:
            progress_callback(100, "Done")
        return self.metrics

    # ── metrics ──────────────────────────────────────────────────────────
    def _file_size(self):
        path = self.doc.PathName
        self.metrics["file_size_mb"] = (
            round(os.path.getsize(path) / (1024.0 * 1024.0), 1)
            if path and os.path.exists(path) else 0)

    def _warnings(self):
        self.metrics["warnings"] = len(self._warning_list())

    def _imports_and_links(self):
        """CAD imports, CAD links and unpinned links share one collector pass."""
        imports, cad_links, unpinned = [], [], []
        for inst in self._of_class(ImportInstance):
            try:
                if inst.IsLinked:
                    cad_links.append(inst)
                    if not inst.Pinned:
                        unpinned.append(inst)
                else:
                    imports.append(inst)
            except Exception:
                continue
        for link in self._of_class(RevitLinkInstance):
            try:
                if not link.Pinned:
                    unpinned.append(link)
            except Exception:
                continue
        self._record("cad_imports", imports)
        self._record("cad_links", cad_links)
        self._record("linked_dwg_not_pinned", unpinned)

    def _in_place_families(self):
        """In-place instances, asked for by family symbol.

        The obvious version — walk every FamilyInstance and test
        `fi.Symbol.Family.IsInPlace` — is the single most expensive thing this
        class used to do: three interop hops on what is often the largest
        collection in the model, just to find a handful of elements.

        Families are few (hundreds at most), so the in-place ones are found
        first, then Revit is asked directly for their instances through
        FamilyInstanceFilter. A model with no in-place families — the normal
        case — never touches the instance table at all.
        """
        try:
            symbol_ids = []
            for family in (FilteredElementCollector(self.doc)
                           .OfClass(Family).ToElements()):
                try:
                    if family.IsInPlace:
                        symbol_ids.extend(family.GetFamilySymbolIds())
                except Exception:
                    continue

            if not symbol_ids:
                # Nothing in-place: the instance table is never touched.
                self._record_ids("in_place_families", [])
                return

            ids = []
            for symbol_id in symbol_ids:
                ids.extend(FilteredElementCollector(self.doc)
                           .WherePasses(FamilyInstanceFilter(self.doc, symbol_id))
                           .ToElementIds())
            self._record_ids("in_place_families", ids)
        except Exception as ex:
            logger.debug("In-place family scan fell back to the slow path: %s", ex)
            self._record("in_place_families", [
                fi for fi in self._of_class(FamilyInstance)
                if self._is_in_place(fi)])

    @staticmethod
    def _is_in_place(family_instance):
        try:
            symbol = family_instance.Symbol
            return bool(symbol and symbol.Family and symbol.Family.IsInPlace)
        except Exception:
            return False

    def _revit_links(self):
        # Reuses the cached elements rather than asking for ids: links are few,
        # and _imports_and_links has to read .Pinned off them anyway. Taking
        # the id path here would query RevitLinkInstance a second time.
        self._record("rvt_links", self._of_class(RevitLinkInstance))

    def _worksets(self):
        if not self.doc.IsWorkshared:
            self.metrics["worksets"] = 0
            return
        from Autodesk.Revit.DB import FilteredWorksetCollector, WorksetKind
        worksets = FilteredWorksetCollector(self.doc).OfKind(WorksetKind.UserWorkset).ToWorksets()
        self.metrics["worksets"] = worksets.Count

    def _views_and_sheets(self):
        """ViewSheet derives from View, so one View pass covers both metrics."""
        views, sheets = [], []
        for v in self._of_class(View):
            try:
                if isinstance(v, ViewSheet):
                    sheets.append(v)
                elif not v.IsTemplate and v.ViewType != DB.ViewType.Internal:
                    views.append(v)
            except Exception:
                continue
        self._record("views", views)
        self._record("sheets", sheets)

    def _groups(self):
        self._record_ids("groups", self._ids_of_class(Group))

    def _design_options(self):
        self._record_ids("design_options", self._ids_of_class(DesignOption))

    def _reference_planes(self):
        self._record_ids("reference_planes", self._ids_of_class(ReferencePlane))

    def _detail_lines(self):
        """Detail and model lines.

        Revit does the category filtering natively here. The old code pulled
        every CurveElement in the model into Python and read `.Category.Name`
        on each one — two interop calls per element, on the category that is
        usually the most numerous in a drafting-heavy model.
        """
        self._record_ids("detail_lines",
                         self._ids_of_class(CurveElement, BuiltInCategory.OST_Lines))

    def _filled_regions(self):
        self._record_ids("filled_regions", self._ids_of_class(FilledRegion))

    def _unplaced_rooms(self):
        try:
            col = (FilteredElementCollector(self.doc)
                   .OfCategory(BuiltInCategory.OST_Rooms)
                   .WhereElementIsNotElementType())
            self._record("rooms_unplaced", [rm for rm in col if rm.Location is None])
        except Exception:
            self._record("rooms_unplaced", [])

    def _duplicate_elements(self):
        dupe_ids = set()
        for warning in self._warning_list():
            try:
                desc = warning.GetDescriptionText().lower()
                if "identical instances" not in desc or "same place" not in desc:
                    continue
                for getter in (warning.GetFailingElements, warning.GetAdditionalElements):
                    ids = getter()
                    if ids:
                        for eid in ids:
                            dupe_ids.add(_eid_int(eid))
            except Exception:
                continue
        self.metrics["duplicate_elements"] = len(dupe_ids)
        self.element_ids["duplicate_elements"] = list(dupe_ids)


# ============================================================================
# COMPLIANCE CHECKER RULE ENGINE
# ============================================================================
class SilenceOutput(object):
    def __enter__(self):
        self._old_out = sys.stdout
        self._old_err = sys.stderr
        class NullWriter(object):
            def write(self, *args, **kwargs): pass
            def flush(self, *args, **kwargs): pass
        sys.stdout = NullWriter()
        sys.stderr = NullWriter()
        return self
    def __exit__(self, et, ev, tb):
        sys.stdout = self._old_out
        sys.stderr = self._old_err

# ============================================================================
# UTILITIES
# ============================================================================
class GridRow(object):
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    def get(self, key, default=None):
        return getattr(self, key, default)
    def __getitem__(self, key):
        return getattr(self, key)
    def __setitem__(self, key, value):
        setattr(self, key, value)
    def __contains__(self, key):
        return hasattr(self, key)

# ============================================================================
# METRIC DETAIL WINDOW
# ============================================================================
_DETAIL_XAML = os.path.normpath(
    os.path.join(os.path.dirname(__file__), 'Tools', 'ModelAuditorDetail.xaml'))

# Naming an element costs three interop calls (GetElement, Name, Category.Name).
# A metric can hold tens of thousands of ids, so only this many are resolved —
# 'Select in Model' still reaches the whole set through "select everything".
_DETAIL_ROW_CAP = 1000


class MetricDetailWindow(T3WPFWindow):
    """The elements behind one health metric, pickable so they can be fixed.

    Opened from the Detail button on a metric row, or from a recommendation.
    Ids come in already collected by the health scan; this window is the only
    place that turns them into names, and only for the rows it shows.
    """

    def __init__(self, doc, uidoc, metric_key, element_ids, status, brushes):
        T3WPFWindow.__init__(self, _DETAIL_XAML)
        self.doc = doc
        self.uidoc = uidoc
        self.rows = []

        info = METRIC_THRESHOLDS.get(metric_key, {})
        label = info.get("label", metric_key)
        total = len(element_ids)

        self.txt_detail_title.Text = label
        self.txt_detail_subtitle.Text = "{} element{} behind this metric".format(
            total, "" if total == 1 else "s")
        self.txt_detail_status.Text = status or "--"
        self.txt_detail_recommendation.Text = info.get(
            "recommendation", "No recommendation recorded for this metric.")
        thresholds = info.get("thresholds")
        if thresholds:
            self.txt_detail_thresholds.Text = (
                "Thresholds: " + " | ".join(str(x) for x in thresholds) +
                "   ·   Weight: {}/5".format(info.get("weight", 1)))

        fill, text = brushes
        if fill is not None:
            self.border_detail_status.Background = fill
        if text is not None:
            self.txt_detail_status.Foreground = text

        self.btn_detail_check_all.Click += self.on_check_all
        self.btn_detail_uncheck_all.Click += self.on_uncheck_all
        self.btn_detail_select.Click += self.on_select_in_model
        self.btn_detail_export.Click += self.on_export

        self._load(element_ids)

    # ------------------------------------------------------------------
    def _load(self, element_ids):
        shown = list(element_ids[:_DETAIL_ROW_CAP])
        self.hidden_ids = list(element_ids[_DETAIL_ROW_CAP:])

        for eid in shown:
            row = GridRow(eid=eid, id=_eid_int(eid),
                          category="Element", name="Unknown")
            try:
                el = self.doc.GetElement(eid)
                if el is not None:
                    row.name = el.Name or "Unnamed"
                    if el.Category:
                        row.category = el.Category.Name
            except Exception:
                pass
            row.is_selected = False
            self.rows.append(row)

        self._set_rows(self.dg_detail_elements, 'empty_detail_elements', self.rows)

        if self.hidden_ids:
            self.status_text.Text = (
                "Showing the first {} of {} elements. "
                "'Select in Model' with nothing ticked picks up all {}.".format(
                    len(shown), len(shown) + len(self.hidden_ids),
                    len(shown) + len(self.hidden_ids)))
        else:
            self.status_text.Text = "{} element{} listed.".format(
                len(shown), "" if len(shown) == 1 else "s")

    def _set_rows(self, control, empty_name, rows):
        rows = list(rows) if rows else []
        control.ItemsSource = to_items_source(rows) if rows else None
        placeholder = getattr(self, empty_name, None)
        if placeholder is not None:
            visibility = System.Windows.Visibility
            placeholder.Visibility = visibility.Collapsed if rows else visibility.Visible

    # ------------------------------------------------------------------
    def select_all_dg_detail_elements_clicked(self, sender, e):
        self.toggle_all_rows(self.dg_detail_elements, "is_selected", sender.IsChecked)

    def on_check_all(self, sender, e):
        for row in self.rows:
            row["is_selected"] = True
        self.dg_detail_elements.Items.Refresh()
        self.sync_header_checkbox(self.chk_all_dg_detail_elements,
                                  self.dg_detail_elements, "is_selected")

    def on_uncheck_all(self, sender, e):
        for row in self.rows:
            row["is_selected"] = False
        self.dg_detail_elements.Items.Refresh()
        self.sync_header_checkbox(self.chk_all_dg_detail_elements,
                                  self.dg_detail_elements, "is_selected")

    def on_select_in_model(self, sender, e):
        picked = [row.eid for row in self.rows if row.get("is_selected", False)]
        scope = "ticked"
        if not picked:
            picked = [row.eid for row in self.rows] + self.hidden_ids
            scope = "all"

        if not picked:
            forms.alert("There are no elements to select.", title="Metric Detail")
            return

        try:
            self.uidoc.Selection.SetElementIds(_eid_list(picked))
            self.uidoc.ShowElements(_eid_list(picked))
        except Exception as ex:
            self.status_text.Text = "Could not select those elements."
            forms.alert("Could not select the elements:\n\n{}".format(ex),
                        title="Metric Detail")
            return

        self.status_text.Text = "Selected {} {} element{} in the model.".format(
            len(picked), scope, "" if len(picked) == 1 else "s")
        self.Close()

    def on_export(self, sender, e):
        if not self.rows:
            forms.alert("Nothing to export.", title="Metric Detail")
            return
        filepath = forms.save_file(file_ext="csv", default_name="metric_elements.csv")
        if not filepath:
            return
        try:
            with _open_csv_write(filepath) as f:
                writer = csv.writer(f)
                writer.writerow(["Element ID", "Category", "Name"])
                for row in self.rows:
                    writer.writerow([_csv_cell(row.get("id", "")),
                                     _csv_cell(row.get("category", "")),
                                     _csv_cell(row.get("name", ""))])
                for eid in self.hidden_ids:
                    writer.writerow([_csv_cell(_eid_int(eid)), "", "(not listed)"])
            self.status_text.Text = "Exported to {}".format(os.path.basename(filepath))
        except Exception as ex:
            forms.alert("Export failed:\n\n{}".format(ex), title="Metric Detail")


# ============================================================================
# MAIN AUDITOR WINDOW
# ============================================================================
class ModelAuditorWindow(T3WPFWindow):
    # Nút "Detail" nằm trong DataTemplate của bảng metric và của danh sách
    # khuyến nghị. Element do template sinh ra không có trong namescope của
    # window nên T3WPFWindow không nối được `Click=` cho chúng — bấm vào là
    # không có gì xảy ra. Bật cờ này để T3WPFWindow nối qua routed event.
    WIRE_TEMPLATED_CLICKS = True

    # Progress/Pause panel names consumed by T3WPFWindow
    PP_PANEL      = "ma_progress_panel"
    PP_BAR        = "progress_bar"
    PP_PAUSE      = "ma_btn_pause"
    PP_STOP       = "ma_btn_stop"
    PP_PAUSE_ICON = "ma_btn_pause_icon"
    PP_PAUSE_TEXT = "ma_btn_pause_label"
    PP_STATUS     = "status_text"
    PP_STOP_MSG   = u"Stopping… finishing current step"

    def __init__(self, script_dir, revit):
        T3WPFWindow.__init__(self, _XAML)
        self._script_dir = script_dir
        self._revit = revit
        self.doc = revit.ActiveUIDocument.Document if (revit and getattr(revit, 'ActiveUIDocument', None)) else None
        self.uidoc = revit.ActiveUIDocument if revit else None
        self._doc = self.doc
        self._uidoc = self.uidoc

        # Save instances of health and warning collectors
        self.health_analyzer = ModelHealthAnalyzer(self.doc) if self.doc else None
        self.health_results = {}
        self.purge_items = []
        self._brush_cache = {}
        # Set once each tab has run its scan, so revisiting a tab that
        # legitimately found nothing does not rescan the whole model.
        self._warnings_loaded = False
        self._purge_scanned = False
        
        # Sidebar nav is wired via Click="on_sidebar_clicked" in XAML
        self.main_tab_control.SelectionChanged += self._on_main_tab_changed
        self._go_to_main_tab(0)

        # Hook button events
        # 1. Health tab
        self.btn_health_run.Click += self.on_health_run
        self.btn_health_export.Click += self.on_health_export

        # Progress Pause/Stop — wire footer buttons
        if getattr(self, "ma_btn_pause", None) is not None:
            self.ma_btn_pause.Click += self.pause_resume_clicked
        if getattr(self, "ma_btn_stop", None) is not None:
            self.ma_btn_stop.Click += self.stop_clicked

        # 2. Warnings tab
        self.btn_warning_reload.Click += self.on_warning_reload
        self.btn_warning_autofix.Click += self.on_warning_autofix
        self.btn_warning_export.Click += self.on_warning_export
        self.btn_warning_select_elements.Click += self.on_warning_select_elements
        self.dg_warning_groups.SelectionChanged += self.on_warning_group_changed

        # 3. Smart Purge tab
        self.btn_smart_purge_check_all.Click += self.on_smart_purge_check_all
        self.btn_smart_purge_uncheck_all.Click += self.on_smart_purge_uncheck_all
        self.btn_smart_purge_run.Click += self.on_smart_purge_run

        # The health scan starts by itself, but only once the window has
        # actually painted: running it inline here would leave the user staring
        # at a frozen ribbon with no window and no progress bar. ContentRendered
        # fires after the first frame, so the dashboard is on screen with its
        # progress bar and Stop button live while the model is read.
        self.status_text.Text = "Starting model health analysis…"
        self.ContentRendered += self._on_first_render

    # ========================================================================
    # WINDOW CONTROL ACTIONS
    # ------------------------------------------------------------------------
    # minimize_button_clicked / maximize_button_clicked / close_button_clicked
    # live in T3WPFWindow and are wired there by _wire_window_controls().
    # Re-declaring or re-binding them here makes every chrome click fire twice.
    # ========================================================================

    # Class-level defaults so a handler that runs before (or instead of)
    # __init__ finishing still finds the attribute.
    purge_items = ()
    health_results = {}
    _warnings_loaded = False
    _purge_scanned = False
    _brush_cache = {}

    # The three tabs, in sidebar order. Index is the TabControl index.
    _TABS = (
        ('btn_tab_health',  "Model Health — weighted score across 17 model metrics"),
        ('btn_tab_warning', "Warnings — group, inspect and resolve the Revit warning list"),
        ('btn_tab_purge', "Smart Purge — scan the model for unused items and delete them"),
    )
    _SIDEBAR_MAP = {name: idx for idx, (name, _) in enumerate(_TABS)}

    def _on_first_render(self, sender=None, e=None):
        """Run the health scan once, right after the window first paints."""
        try:
            self.ContentRendered -= self._on_first_render
        except Exception:
            pass
        if self.health_analyzer is None:
            self.status_text.Text = "No Revit project is open — nothing to analyse."
            return
        try:
            self.on_health_run(self.btn_health_run, None)
        except Exception as ex:
            # An exception thrown out of a WPF event handler takes the window
            # with it, and this one runs before the user has touched anything.
            self.end_progress()
            self.status_text.Text = "Health analysis failed — click Re-Analyze to retry."
            forms.alert("The health analysis could not finish:\n\n{}\n\n"
                        "The other tabs still work; click 'Re-Analyze' to try "
                        "again.".format(ex), title="Model Health Check")
            logger.debug(traceback.format_exc())

    def on_sidebar_clicked(self, sender, e):
        idx = self._SIDEBAR_MAP.get(sender.Name, -1)
        if idx >= 0:
            self._go_to_main_tab(idx)

    def _brush(self, token):
        """Resolve a `T3.*` brush from the embedded stylesheet.

        Colours are never written as hex here: the XAML legend and these
        rows have to come from the same source or they drift apart.
        """
        if token in self._brush_cache:
            return self._brush_cache[token]
        brush = None
        try:
            brush = self.FindResource(token)
        except Exception:
            logger.debug("Missing brush resource %s", token)
        self._brush_cache[token] = brush
        return brush

    def _set_rows(self, control, empty_name, rows):
        """Fill a grid/list and show its empty-state message when there is
        nothing to show. Every ItemsSource assignment goes through here so no
        view can end up as a blank white rectangle."""
        rows = list(rows) if rows else []
        control.ItemsSource = to_items_source(rows) if rows else None
        placeholder = getattr(self, empty_name, None)
        if placeholder is not None:
            visibility = System.Windows.Visibility
            placeholder.Visibility = visibility.Collapsed if rows else visibility.Visible
        return rows

    def _on_main_tab_changed(self, sender, e):
        """Sync sidebar toggle buttons when tab changes via keyboard/programmatic switch."""
        try:
            from System.Windows.Controls import TabControl as _TC
            if not isinstance(e.Source, _TC):
                return
            self._sync_tab_chrome(self.main_tab_control.SelectedIndex)
        except Exception:
            pass

    def _go_to_main_tab(self, index):
        if not 0 <= index < len(self._TABS):
            return
        # Assigning SelectedIndex raises SelectionChanged, which lands in
        # _on_main_tab_changed -> _sync_tab_chrome. Sync explicitly too, for the
        # case where the index is already the selected one and no event fires.
        self.main_tab_control.SelectedIndex = index
        self._sync_tab_chrome(index)

    def _sync_tab_chrome(self, index):
        """Match the sidebar toggles and status line to the visible tab."""
        if not 0 <= index < len(self._TABS):
            return
        for idx, (name, _) in enumerate(self._TABS):
            getattr(self, name).IsChecked = (idx == index)
        self.status_text.Text = self._TABS[index][1]

        # Warnings and Smart Purge fill themselves on first visit so neither
        # tab ever opens blank. Health stays manual — its scan is the expensive
        # one and belongs behind the Re-Analyze button.
        #
        # "First visit" is tracked with a flag, not by looking at ItemsSource:
        # a clean model legitimately produces zero rows, and testing the empty
        # ItemsSource made every single click on that rail tile re-run the
        # whole scan.
        # The flag is raised here, before the scan, so a scan that fails does
        # not turn every later click on the rail tile into another attempt.
        # Both scans can still be re-run from their own buttons.
        if index == 1 and not self._warnings_loaded:
            self._warnings_loaded = True
            self.on_warning_reload(None, None)
        elif index == 2 and not self._purge_scanned:
            self._purge_scanned = True
            self.load_smart_purge()

    # ========================================================================

    def on_health_run(self, sender, e):
        if self.health_analyzer is None:
            forms.alert("No Revit project is open, so there is nothing to analyse.\n\n"
                        "Open a project and run Model Auditor again.",
                        title="Model Health Check")
            return
        self.status_text.Text = "Running model health analysis..."
        # Progress bar + Pause/Stop while the scan runs
        self.begin_progress(100, disable=[sender])
        def progress_cb(pct, name):
            self.step_progress(pct, "Running diagnostics: {}...".format(name))
        self.health_results = self.health_analyzer.analyze(
            progress_callback=progress_cb,
            cancel_check=lambda: self.is_cancelled)
        cancelled = self.is_cancelled
        self.end_progress()
        if cancelled:
            self.status_text.Text = "Health analysis cancelled."
            return

        # Calculate score and grade
        weighted_total = 0
        weight_sum = 0
        for key, value in self.health_results.items():
            if key in METRIC_THRESHOLDS:
                t = METRIC_THRESHOLDS[key]["thresholds"]
                w = METRIC_THRESHOLDS[key].get("weight", 1)
                if value <= t[0]: s = 100
                elif value <= t[1]: s = 80
                elif value <= t[2]: s = 60
                elif value <= t[3]: s = 40
                elif value <= t[4]: s = 20
                else: s = 0
                weighted_total += s * w
                weight_sum += w
        
        score = round(weighted_total / max(weight_sum, 1), 1)
        
        grade = "F"
        desc = "Critical"
        if score >= 90: grade, desc = "A", "Excellent"
        elif score >= 75: grade, desc = "B", "Good"
        elif score >= 60: grade, desc = "C", "Fair"
        elif score >= 40: grade, desc = "D", "Poor"
        
        # Score badge, RAG chip and trend all read from the same three T3
        # token families as the legend above the table.
        fill, text = _GRADE_TOKENS[grade]
        brush_fill = self._brush(fill)
        brush_text = self._brush(text)

        self.ellipse_health_bg.Fill = brush_fill
        self.ellipse_health_color.Fill = brush_text

        self.txt_health_grade.Text = grade
        self.txt_health_score.Text = str(score)
        # Grade chip beside the title. The word for the grade ("Excellent")
        # is already in the title and the number is already in the circle, so
        # the chip carries the letter and nothing else — the old "Weighted
        # Score: x/100" and "Grade: A (Excellent)" lines said the same thing
        # a third and fourth time.
        self.txt_health_badge_label.Text = "Grade {}".format(grade)
        self.txt_health_badge_label.Foreground = brush_text
        self.border_health_grade.Background = brush_fill

        self.txt_health_title.Text = "Model Health: {}".format(desc)

        # RAG (Red/Amber/Green) status — Autodesk Model Analytics style
        rag_label, rag_fill, rag_text = _rag_status(score)
        self.txt_health_rag.Text = "RAG: {}".format(rag_label)
        self.txt_health_rag.Foreground = self._brush(rag_text)
        self.border_health_rag.Background = self._brush(rag_fill)

        # Historical trend — compare against the previous run for this model
        history = _load_history(self.doc)
        prev_score = history[-1]["score"] if history else None
        _append_history(self.doc, {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "score": score,
            "grade": grade,
            "rag": rag_label,
        })
        if prev_score is None:
            self.txt_health_trend.Text = "No previous run to compare"
            self.txt_health_trend.Foreground = self._brush("T3.TextMuted")
        else:
            delta = round(score - prev_score, 1)
            if delta > 0:
                self.txt_health_trend.Text = "Up {} vs last run".format(delta)
                self.txt_health_trend.Foreground = self._brush("T3.Success.Text")
            elif delta < 0:
                self.txt_health_trend.Text = "Down {} vs last run".format(abs(delta))
                self.txt_health_trend.Foreground = self._brush("T3.Danger.Text")
            else:
                self.txt_health_trend.Text = "No change vs last run"
                self.txt_health_trend.Foreground = self._brush("T3.TextMuted")

        # Update text info
        doc_name = os.path.basename(self.doc.PathName) if self.doc.PathName else "Unsaved Project"
        self.txt_health_doc_name.Text = "Model: {}".format(doc_name)
        self.txt_health_last_run.Text = "Last analyzed: {}".format(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Load metrics data to DataGrid and collect counts & recommendations
        grid_data = []
        counts = OrderedDict((name, 0) for name in _STATUS_ORDER)
        recs_data = []

        for key, value in self.health_results.items():
            m_info = METRIC_THRESHOLDS.get(key)
            if not m_info:
                continue
            thresholds = m_info["thresholds"]
            status = _status_for(value, thresholds)
            counts[status] += 1

            unit = m_info["unit"]
            value_display = "{}{}".format(value, " " + unit if unit else "")
            stars = u"★" * m_info["weight"] + u"☆" * (5 - m_info["weight"])

            # Element selectability
            has_elements = len(self.health_analyzer.element_ids.get(key, [])) > 0
            selectable = m_info["selectable"] and has_elements

            # Names here must match the bindings in ModelAuditor.xaml exactly —
            # a typo shows up as a silently empty column, not as an error.
            grid_data.append(GridRow(
                key=key,
                label=m_info["label"],
                value_display=value_display,
                status=status,
                severity=_STATUS_SEVERITY[status],
                # Bare values: the column headers already say WEIGHT and
                # THRESHOLDS, and each cell is one 26px line high.
                weight_stars="{} {}/5".format(stars, m_info["weight"]),
                thresholds_text=" | ".join(str(x) for x in thresholds),
                select_visibility="Visible" if selectable else "Collapsed",
                recommendation=m_info["recommendation"],
            ))

            # Collect recommendations if status is concerning or worse
            if status in _STATUS_ATTENTION:
                recs_data.append(GridRow(
                    key=key,
                    status=status,
                    severity=_STATUS_SEVERITY[status],
                    headline="{} — {}".format(m_info["label"], value_display),
                    recommendation=m_info["recommendation"],
                    select_visibility="Visible" if selectable else "Collapsed",
                    # How much this metric is dragging the score down: how far
                    # past the thresholds it is, times how much it counts for.
                    impact=_STATUS_ORDER.index(status) * m_info["weight"],
                ))

        # Build health summary text
        crit_severe = counts["Critical"] + counts["Severe"]
        warn_concern = counts["Warning"] + counts["Concerning"]
        self.txt_health_summary.Text = "{} critical/severe issues, {} warnings. {}".format(
            crit_severe, warn_concern,
            "Immediate attention needed." if crit_severe > 0 else "Model is in good shape.")

        # The per-status numbers live in the coloured tally strip now; this
        # line only carries the total.
        self.txt_health_summary_metrics.Text = "{} metrics analysed".format(len(grid_data))
        self.txt_tally_good.Text = str(counts["Good"] + counts["Acceptable"])
        self.txt_tally_warning.Text = str(warn_concern)
        self.txt_tally_critical.Text = str(crit_severe)

        # Worst first: a list you read top-down and stop when you run out of
        # time is worth more than one in metric order.
        recs_data.sort(key=lambda row: row.impact, reverse=True)

        self._set_rows(self.dg_health_metrics, 'empty_health_metrics', grid_data)
        self._set_rows(self.lst_health_recommendations,
                       'empty_health_recommendations', recs_data)
        self.txt_health_rec_count.Text = str(len(recs_data))

        self.status_text.Text = "Health analysis complete. Score: {}".format(score)

    # ── metric detail ────────────────────────────────────────────────────
    def on_health_metric_detail(self, sender, e):
        """Open the element table for the metric on this row."""
        row = getattr(sender, 'DataContext', None)
        if row is None:
            return
        self.show_metric_detail(row.get("key"), row.get("status"))

    def on_recommendation_detail(self, sender, e):
        row = getattr(sender, 'DataContext', None)
        if row is None:
            return
        self.show_metric_detail(row.get("key"), row.get("status"))

    def show_metric_detail(self, metric_key, status=None):
        if not metric_key:
            return
        info = METRIC_THRESHOLDS.get(metric_key, {})
        label = info.get("label", metric_key)
        element_ids = []
        if self.health_analyzer is not None:
            element_ids = self.health_analyzer.element_ids.get(metric_key, [])

        if not element_ids:
            forms.alert("{} has no elements to list.\n\n"
                        "{}".format(label, info.get("recommendation", "")),
                        title="Model Health Check")
            return

        fill_token, text_token = _STATUS_TOKENS.get(
            status, ("T3.SurfaceSunken", "T3.TextMuted"))
        try:
            window = MetricDetailWindow(
                self.doc, self.uidoc, metric_key, element_ids, status,
                (self._brush(fill_token), self._brush(text_token)))
            window.Owner = self
            window.ShowDialog()
            self.status_text.Text = window.status_text.Text
        except Exception as ex:
            self.status_text.Text = "Could not open the detail view."
            forms.alert("Could not open the detail view for {}:\n\n{}".format(label, ex),
                        title="Model Health Check")
            logger.debug(traceback.format_exc())

    def on_health_export(self, sender, e):
        if not hasattr(self, 'health_results') or not self.health_results:
            forms.alert("Please run the diagnostics first.", title="Model Health Check")
            return
        
        try:
            default_name = "Model_Health_Report_{}".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            # `filesfilter` is not a parameter of save_file (it is `files_filter`),
            # so the old spelling fell into **kwargs and the report was written
            # without a .csv extension.
            filepath = forms.save_file(file_ext="csv", default_name=default_name)
            if not filepath:
                return
            
            with _open_csv_write(filepath) as f:
                import csv
                writer = csv.writer(f)
                writer.writerow(["Metric", "Value", "Status", "Recommendation"])
                for row in self.dg_health_metrics.ItemsSource:
                    writer.writerow([
                        _csv_cell(getattr(row, 'label', '')),
                        _csv_cell(getattr(row, 'value_display', '')),
                        _csv_cell(getattr(row, 'status', '')),
                        _csv_cell(getattr(row, 'recommendation', ''))
                    ])
            forms.alert("Health report exported successfully to:\n\n{}".format(filepath), title="Model Health Check")
        except Exception as ex:
            forms.alert("Failed to export health report:\n{}".format(ex))

    # ========================================================================
    # TAB 2: COMPLIANCE CHECKER
    # ========================================================================
    # A warning group can hold thousands of elements. Naming them all costs
    # three interop calls each (GetElement, Name, Category.Name) to fill a list
    # nobody scrolls to the end of, so only the head of the group is rendered.
    # The full id list stays on the row, and 'Select in Model' uses that.
    _MAX_WARNING_ROWS = 200

    def on_warning_reload(self, sender, e):
        self.status_text.Text = "Collecting Revit warnings..."
        try:
            warnings = self.doc.GetWarnings()
            groups = defaultdict(list)
            total = 0

            if warnings:
                for w in warnings:
                    total += 1
                    # The ElementIds are kept as they come out of Revit.
                    # Converting every failing id to an int up front cost one
                    # interop call per element on a list that is usually never
                    # opened — the group counts alone drive the grid.
                    groups[w.GetDescriptionText()].extend(w.GetFailingElements())

            grid_data = [GridRow(description=desc, count=len(ids), element_ids=ids)
                         for desc, ids in groups.items()]
            grid_data.sort(key=lambda row: row.count, reverse=True)

            self._set_rows(self.dg_warning_groups, 'empty_warning_groups', grid_data)
            self._set_rows(self.lst_warning_elements, 'empty_warning_elements', [])
            self._warnings_loaded = True
            self.status_text.Text = "Loaded {} warnings in {} unique groups".format(
                total, len(grid_data))
        except Exception as ex:
            self.status_text.Text = "Failed to read model warnings."
            forms.alert("Failed to read the warning list from the model:\n\n{}".format(ex),
                        title="Warning Manager")
            logger.debug(traceback.format_exc())

    def on_warning_group_changed(self, sender, e):
        selected = self.dg_warning_groups.SelectedItem
        if not selected:
            self._set_rows(self.lst_warning_elements, 'empty_warning_elements', [])
            return

        ids = selected.get("element_ids", [])
        shown = ids[:self._MAX_WARNING_ROWS]
        list_items = []
        for eid in shown:
            id_val = _eid_int(eid)
            try:
                el = self.doc.GetElement(eid)
                name = el.Name if el else "Unknown"
                cat_name = el.Category.Name if (el and el.Category) else "Element"
                list_items.append("{} : {} [{}]".format(cat_name, name, id_val))
            except Exception:
                list_items.append("Element [{}]".format(id_val))

        hidden = len(ids) - len(shown)
        if hidden > 0:
            list_items.append(
                "... and {} more — 'Select in Model' picks up all {} "
                "without selecting rows here".format(hidden, len(ids)))

        self._set_rows(self.lst_warning_elements, 'empty_warning_elements', list_items)

    def on_warning_elements_selection_changed(self, sender, e):
        """Live-select the highlighted row(s) in the Revit view as the user browses the list."""
        try:
            elem_ids = []
            for item in (self.lst_warning_elements.SelectedItems or []):
                found = re.findall(r'\[(\d+)\]', item)
                if found:
                    elem_ids.append(_make_eid(int(found[-1])))
            if not elem_ids:
                return
            self.uidoc.Selection.SetElementIds(_eid_list(elem_ids))
            self.uidoc.ShowElements(_eid_list(elem_ids))
        except Exception:
            pass

    def on_warning_select_elements(self, sender, e):
        """Select the rows ticked in the list, or the whole group if none are."""
        elem_ids = []
        for item in (self.lst_warning_elements.SelectedItems or []):
            # Rows read "Category : Name [id]", and the id is always last —
            # a family called "Door [100]" would fool a first-match search.
            found = re.findall(r'\[(\d+)\]', item)
            if found:
                elem_ids.append(_make_eid(int(found[-1])))

        scope = "from the list"
        if not elem_ids:
            group = self.dg_warning_groups.SelectedItem
            if not group:
                forms.alert("Select a warning group, or one or more elements in "
                            "the list on the right.", title="Warning Manager")
                return
            elem_ids = [_make_eid(eid) for eid in group.get("element_ids", [])]
            scope = "from the whole group"

        if not elem_ids:
            forms.alert("That warning has no elements to select.",
                        title="Warning Manager")
            return

        self.uidoc.Selection.SetElementIds(_eid_list(elem_ids))
        self.uidoc.ShowElements(_eid_list(elem_ids))
        self.status_text.Text = "Selected {} elements in model {}".format(
            len(elem_ids), scope)

    def on_warning_autofix(self, sender, e):
        self.status_text.Text = "Resolving identical instances in same place warnings..."
        try:
            warnings = self.doc.GetWarnings()
            overlapping_pairs = []
            if warnings:
                for w in warnings:
                    desc = w.GetDescriptionText()
                    if "identical instances" in desc.lower() and "same place" in desc.lower():
                        overlapping_pairs.append(list(w.GetFailingElements()))
            
            if not overlapping_pairs:
                forms.alert("No overlapping duplicate warnings found in model.", title="Warning Manager")
                return
            
            to_delete = set()
            for pair in overlapping_pairs:
                if len(pair) >= 2:
                    # Keep the first, delete the rest
                    for i in range(1, len(pair)):
                        to_delete.add(pair[i])
            
            if not to_delete:
                forms.alert("No duplicate elements could be collected for deletion.", title="Warning Manager")
                return

            # Destructive, so it asks first: the previous build deleted straight
            # off the button click with no way back except Ctrl+Z.
            if not forms.alert(
                    "Delete {} duplicated elements from {} overlap warnings?\n\n"
                    "One element of each overlapping pair is kept, the rest are "
                    "deleted. Undo restores the whole operation in one step.".format(
                        len(to_delete), len(overlapping_pairs)),
                    title="Warning Manager", yes=True, no=True):
                self.status_text.Text = "Duplicate resolution cancelled — model unchanged."
                return

            t = Transaction(self.doc, "Resolve Overlapping Duplicates")
            try:
                t.Start()
                count = 0
                for eid in to_delete:
                    try:
                        self.doc.Delete(eid)
                        count += 1
                    except Exception:
                        pass
                t.Commit()
            except Exception:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise

            skipped = len(to_delete) - count
            msg = "Deleted {} duplicate elements.".format(count)
            if skipped:
                msg += " {} could not be deleted (pinned, in a group, or already gone).".format(skipped)
            forms.alert(msg, title="Warning Manager")
            self.status_text.Text = msg
            self.on_warning_reload(None, None)
        except Exception as ex:
            self.status_text.Text = "Failed to resolve duplicates — see the message for details."
            forms.alert("Error resolving duplicates:\n\n{}".format(ex), title="Warning Manager")
            logger.debug(traceback.format_exc())

    def on_warning_export(self, sender, e):
        groups = self.dg_warning_groups.ItemsSource
        if not groups:
            forms.alert("No warnings to export.", title="Warning Manager")
            return
        
        filepath = forms.save_file(file_ext="csv", default_name="warnings_report.csv")
        if not filepath:
            return
        
        try:
            with _open_csv_write(filepath) as f:
                writer = csv.writer(f)
                writer.writerow(["Warning Description", "Count", "Element IDs"])
                for g in groups:
                    ids_str = ";".join([str(eid) for eid in g.get("element_ids", [])])
                    writer.writerow([
                        _csv_cell(g.get("description", "")),
                        g.get("count", 0),
                        ids_str
                    ])
            forms.alert("Warnings report exported successfully.", title="Warning Manager")
        except Exception as ex:
            forms.alert("Failed to export report:\n{}".format(ex))

    # ========================================================================
    # TAB 4: MODEL CLEANUP
    # ========================================================================
    def load_smart_purge(self):
        self.status_text.Text = "Scanning unused families and styles..."
        try:
            # Dynamically import scanners from local packages
            from Services.ModelAuditor.smart_purge.purge_categories_v2 import create_purge_categories
            from Services.ModelAuditor.smart_purge.purge_scanner import create_scanner
            
            self.purge_items = []
            failed_scanners = []
            categories = create_purge_categories()
            total_cats = len(categories)
            self.begin_progress(100)
            for idx, cat in enumerate(categories):
                pct = int((idx / float(total_cats)) * 100)
                if not self.step_progress(pct, "Scanning unused components: {} ({} of {})".format(cat.name, idx + 1, total_cats)):
                    break
                try:
                    scanner = create_scanner(cat.scanner_class, self.doc)
                    if scanner:
                        with SilenceOutput():
                            result = scanner.scan()
                        if result:
                            for item in result:
                                item_row = GridRow(**item)
                                item_row.is_selected = False
                                item_row.count = 1
                                # Which purge category found it, so the purge run
                                # can hand PurgeExecutor one transaction per
                                # category instead of one flat Delete() call.
                                item_row.purge_category = cat.name
                                self.purge_items.append(item_row)
                except Exception as ex:
                    logger.debug("Purge scanner %s failed: %s", cat.scanner_class, ex)
                    failed_scanners.append(cat.name)

            cancelled = self.is_cancelled
            self.end_progress()
            if cancelled:
                # An incomplete scan should not count as this tab's first visit.
                self._purge_scanned = False
            self._set_rows(self.dg_smart_purge, 'empty_smart_purge', self.purge_items)
            msg = "Smart Purge scan {}. Unused items found: {}".format(
                "cancelled" if cancelled else "complete", len(self.purge_items))
            if failed_scanners:
                msg += " — {} categories could not be scanned ({}).".format(
                    len(failed_scanners), ", ".join(failed_scanners[:3]))
            self.status_text.Text = msg
        except Exception as ex:
            self.end_progress()
            self.status_text.Text = "Smart Purge scan failed."
            forms.alert("Failed to scan the model for unused items:\n\n{}".format(ex),
                        title="Smart Purge")
            logger.debug(traceback.format_exc())

    def on_smart_purge_check_all(self, sender, e):
        if hasattr(self, 'purge_items') and self.purge_items:
            for item in self.purge_items:
                item["is_selected"] = True
            self.dg_smart_purge.Items.Refresh()

    def on_smart_purge_uncheck_all(self, sender, e):
        if hasattr(self, 'purge_items') and self.purge_items:
            for item in self.purge_items:
                item["is_selected"] = False
            self.dg_smart_purge.Items.Refresh()

    def on_smart_purge_run(self, sender, e):
        if not self.purge_items:
            forms.alert("Nothing to purge yet.\n\n"
                        "{}".format("The last scan found no unused items."
                                    if self._purge_scanned else
                                    "Open the Smart Purge tab to scan the model first."),
                        title="Smart Purge")
            return

        # Group the ticked rows back under the category that found them.
        # PurgeExecutor runs one transaction per category inside a single
        # TransactionGroup, so a category that fails rolls back on its own and
        # the whole purge is still one Ctrl+Z step.
        buckets = OrderedDict()
        blocked = 0
        for item in self.purge_items:
            if not item.get("is_selected", False):
                continue
            if not item.get("can_delete", True):
                blocked += 1
                continue
            buckets.setdefault(item.get("purge_category", "Unused items"), []).append(item)

        total = sum(len(v) for v in buckets.values())
        if not total:
            forms.alert("No deletable items are ticked.{}".format(
                " {} ticked items are marked as protected.".format(blocked) if blocked else ""),
                title="Smart Purge")
            return

        if not forms.alert(
                "Purge {} items across {} categories?\n\n"
                "Undo restores the whole operation in one step.".format(total, len(buckets)),
                title="Smart Purge", yes=True, no=True):
            self.status_text.Text = "Purge cancelled — model unchanged."
            return

        class _Bucket(object):
            def __init__(self, name, items):
                self.name = name
                self.unused_items = items

        msg = None
        self.begin_progress(100, disable=[sender])
        try:
            from Services.ModelAuditor.smart_purge.purge_executor import PurgeExecutor

            executor = PurgeExecutor(self.doc)
            executor.progress_callback = lambda cur, tot, text: self.step_progress(
                int(float(cur) / float(tot) * 100) if tot else 0, text)
            deleted_count, failed_count, _, failed_items = executor.execute_purge(
                [_Bucket(name, items) for name, items in buckets.items()])

            msg = "Purged {} items.".format(deleted_count)
            if failed_count:
                reasons = "; ".join(str(f.get("reason", "unknown")) for f in failed_items[:3])
                msg += " {} could not be deleted ({}).".format(failed_count, reasons)
        except Exception as ex:
            self.end_progress()
            self.status_text.Text = "Purge failed — model unchanged."
            forms.alert("Purge failed, nothing was deleted:\n\n{}".format(ex), title="Smart Purge")
            logger.debug(traceback.format_exc())
            return

        # end_progress() must finish before load_smart_purge() starts its own
        # progress run, otherwise its begin_progress() drops the record of the
        # controls disabled here and the Purge button never re-enables.
        self.end_progress()
        forms.alert(msg, title="Smart Purge")
        self.load_smart_purge()
        self.status_text.Text = msg

    def select_all_dg_smart_purge_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua dg_smart_purge."""
        self.toggle_all_rows(self.dg_smart_purge, "is_selected", sender.IsChecked)

def show_model_auditor(script_dir, revit):
    doc = revit.ActiveUIDocument.Document if (revit and getattr(revit, 'ActiveUIDocument', None)) else None
    if doc is None:
        forms.alert("Model Auditor needs an open Revit project.\n\n"
                    "Open a project and run the tool again.", title="Model Auditor")
        return
    if doc.IsFamilyDocument:
        forms.alert("Model Auditor audits project files, not family files.\n\n"
                    "Open a project (.rvt) and run the tool again.", title="Model Auditor")
        return
    ModelAuditorWindow(script_dir, revit).ShowDialog()
