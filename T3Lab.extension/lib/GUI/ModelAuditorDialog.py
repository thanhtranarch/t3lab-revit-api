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
from System.Windows import (WindowState, MessageBox, MessageBoxButton, MessageBoxImage, Visibility, Thickness)
from System.Windows.Controls import (TabControl, TabItem, RadioButton, ComboBox, ListBox, DataGrid)
from System.Collections.ObjectModel import ObservableCollection
from System import Object
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs

from pyrevit import forms, DB, script, revit
from GUI.WPF_Base import T3WPFWindow, to_items_source
from GUI.ProgressPauseMixin import ProgressPauseMixin

logger = script.get_logger()
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter, ElementId,
    Transaction, TransactionGroup, FamilyInstance, ImportInstance, RevitLinkInstance,
    View, ViewSheet, Group, DesignOption, ReferencePlane, CurveElement, FilledRegion,
    Material, BasePoint, StartingViewSettings, ElementClassFilter, BoundingBoxIntersectsFilter,
    Outline, IndependentTag, Dimension
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


def _rag_status(score):
    """Red/Orange/Green classification matching Autodesk Model Analytics' health-check indicators."""
    if score >= 75:
        return "Green", "#10B981", "#DCFCE7"
    elif score >= 40:
        return "Orange", "#F59E0B", "#FFFBEB"
    else:
        return "Red", "#EF4444", "#FEE2E2"


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
    def __init__(self, doc):
        self.doc = doc
        self.metrics = OrderedDict()
        self.element_ids = {}

    def analyze(self, progress_callback=None, cancel_check=None):
        methods = [
            (self._file_size, "Calculating File Size"),
            (self._warnings, "Collecting Warnings"),
            (self._cad_imports, "Scanning CAD Imports"),
            (self._in_place_families, "Scanning In-Place Families"),
            (self._rvt_links, "Scanning Revit Links"),
            (self._worksets, "Scanning Worksets"),
            (self._cad_links, "Scanning CAD Links"),
            (self._views, "Scanning Views"),
            (self._sheets, "Scanning Sheets"),
            (self._groups, "Scanning Groups"),
            (self._design_options, "Scanning Design Options"),
            (self._reference_planes, "Scanning Reference Planes"),
            (self._detail_lines, "Scanning Detail Lines"),
            (self._filled_regions, "Scanning Filled Regions"),
            (self._unplaced_rooms, "Scanning Unplaced Rooms"),
            (self._unpinned_links, "Scanning Unpinned Links"),
            (self._duplicate_elements, "Scanning Duplicate Elements")
        ]
        
        total = len(methods)
        for idx, (m, name) in enumerate(methods):
            if cancel_check and cancel_check():
                break
            if progress_callback:
                progress_callback(int((idx / float(total)) * 100), name)
            m()

        if progress_callback:
            progress_callback(100, "Done")
        return self.metrics

    def _store_ids(self, key, elements):
        self.element_ids[key] = [_eid_int(el.Id) for el in elements if el]

    def _file_size(self):
        try:
            path = self.doc.PathName
            if path and os.path.exists(path):
                self.metrics["file_size_mb"] = round(os.path.getsize(path) / (1024.0 * 1024.0), 1)
            else:
                self.metrics["file_size_mb"] = 0
        except:
            self.metrics["file_size_mb"] = 0

    def _warnings(self):
        try:
            w = self.doc.GetWarnings()
            self.metrics["warnings"] = len(w) if w else 0
        except:
            self.metrics["warnings"] = 0

    def _cad_imports(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(ImportInstance).WhereElementIsNotElementType()
            elems = [inst for inst in col if not inst.IsLinked]
            self.metrics["cad_imports"] = len(elems)
            self._store_ids("cad_imports", elems)
        except:
            self.metrics["cad_imports"] = 0

    def _in_place_families(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(FamilyInstance).WhereElementIsNotElementType()
            elems = [fi for fi in col if fi.Symbol and fi.Symbol.Family and fi.Symbol.Family.IsInPlace]
            self.metrics["in_place_families"] = len(elems)
            self._store_ids("in_place_families", elems)
        except:
            self.metrics["in_place_families"] = 0

    def _rvt_links(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(RevitLinkInstance).WhereElementIsNotElementType()
            elems = list(col)
            self.metrics["rvt_links"] = len(elems)
            self._store_ids("rvt_links", elems)
        except:
            self.metrics["rvt_links"] = 0

    def _worksets(self):
        try:
            if self.doc.IsWorkshared:
                from Autodesk.Revit.DB import FilteredWorksetCollector, WorksetKind
                ws = FilteredWorksetCollector(self.doc).OfKind(WorksetKind.UserWorkset).ToWorksets()
                self.metrics["worksets"] = ws.Count
            else:
                self.metrics["worksets"] = 0
        except:
            self.metrics["worksets"] = 0

    def _cad_links(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(ImportInstance).WhereElementIsNotElementType()
            elems = [inst for inst in col if inst.IsLinked]
            self.metrics["cad_links"] = len(elems)
            self._store_ids("cad_links", elems)
        except:
            self.metrics["cad_links"] = 0

    def _views(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(View).WhereElementIsNotElementType()
            elems = [v for v in col if not v.IsTemplate and v.ViewType != DB.ViewType.Internal]
            self.metrics["views"] = len(elems)
            self._store_ids("views", elems)
        except:
            self.metrics["views"] = 0

    def _sheets(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(ViewSheet).WhereElementIsNotElementType()
            elems = list(col)
            self.metrics["sheets"] = len(elems)
            self._store_ids("sheets", elems)
        except:
            self.metrics["sheets"] = 0

    def _groups(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(Group).WhereElementIsNotElementType()
            elems = list(col)
            self.metrics["groups"] = len(elems)
            self._store_ids("groups", elems)
        except:
            self.metrics["groups"] = 0

    def _design_options(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(DesignOption).WhereElementIsNotElementType()
            elems = list(col)
            self.metrics["design_options"] = len(elems)
            self._store_ids("design_options", elems)
        except:
            self.metrics["design_options"] = 0

    def _reference_planes(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(ReferencePlane).WhereElementIsNotElementType()
            elems = list(col)
            self.metrics["reference_planes"] = len(elems)
            self._store_ids("reference_planes", elems)
        except:
            self.metrics["reference_planes"] = 0

    def _detail_lines(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(CurveElement).WhereElementIsNotElementType()
            elems = []
            for ce in col:
                try:
                    cat = ce.Category
                    if cat and "Lines" in cat.Name:
                        elems.append(ce)
                except: pass
            self.metrics["detail_lines"] = len(elems)
            self._store_ids("detail_lines", elems)
        except:
            self.metrics["detail_lines"] = 0

    def _filled_regions(self):
        try:
            col = FilteredElementCollector(self.doc).OfClass(FilledRegion).WhereElementIsNotElementType()
            elems = list(col)
            self.metrics["filled_regions"] = len(elems)
            self._store_ids("filled_regions", elems)
        except:
            self.metrics["filled_regions"] = 0

    def _unplaced_rooms(self):
        try:
            col = FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType()
            elems = [rm for rm in col if rm.Location is None]
            self.metrics["rooms_unplaced"] = len(elems)
            self._store_ids("rooms_unplaced", elems)
        except:
            self.metrics["rooms_unplaced"] = 0

    def _unpinned_links(self):
        try:
            elems = []
            for link in FilteredElementCollector(self.doc).OfClass(RevitLinkInstance).WhereElementIsNotElementType():
                if not link.Pinned:
                    elems.append(link)
            for inst in FilteredElementCollector(self.doc).OfClass(ImportInstance).WhereElementIsNotElementType():
                if inst.IsLinked and not inst.Pinned:
                    elems.append(inst)
            self.metrics["linked_dwg_not_pinned"] = len(elems)
            self._store_ids("linked_dwg_not_pinned", elems)
        except:
            self.metrics["linked_dwg_not_pinned"] = 0

    def _duplicate_elements(self):
        try:
            warnings = self.doc.GetWarnings()
            dupe_ids_set = set()
            if warnings:
                for warning in warnings:
                    try:
                        desc = warning.GetDescriptionText()
                        if "identical instances" in desc.lower() and "same place" in desc.lower():
                            failing = warning.GetFailingElements()
                            additional = warning.GetAdditionalElements()
                            if failing:
                                for eid in failing: dupe_ids_set.add(_eid_int(eid))
                            if additional:
                                for eid in additional: dupe_ids_set.add(_eid_int(eid))
                    except: pass
            self.metrics["duplicate_elements"] = len(dupe_ids_set)
            self.element_ids["duplicate_elements"] = list(dupe_ids_set)
        except:
            self.metrics["duplicate_elements"] = 0

# ============================================================================
# COMPLIANCE CHECKER RULE ENGINE
# ============================================================================
class RuleEngine(object):
    def __init__(self, doc):
        self.doc = doc
        self.app = doc.Application

    def run_checkset(self, checkset_data, progress_callback=None, cancel_check=None):
        results = []
        rules = checkset_data.get("rules", [])
        total = len(rules)
        for idx, rule in enumerate(rules):
            if cancel_check and cancel_check():
                break
            if progress_callback:
                progress_callback(idx, total)
            if not rule.get("enabled", True):
                results.append({
                    "id": rule.get("id", ""),
                    "category": rule.get("category", ""),
                    "severity": rule.get("severity", ""),
                    "name": rule.get("name", ""),
                    "status": "Skipped",
                    "message": "Rule is disabled"
                })
                continue
            try:
                result = self._execute_rule(rule)
                results.append(result)
            except Exception as e:
                results.append({
                    "id": rule.get("id", ""),
                    "category": rule.get("category", ""),
                    "severity": rule.get("severity", ""),
                    "name": rule.get("name", ""),
                    "status": "Error",
                    "message": str(e)
                })
        return results

    def _execute_rule(self, rule):
        rule_type = rule.get("type", "")
        params = rule.get("params", {})
        
        status = "Pass"
        message = ""

        if rule_type == "value_match":
            expected = params.get("expected_version", "")
            actual = str(self.app.VersionNumber)
            if expected in actual:
                message = "Revit version matches: {}".format(actual)
            else:
                status = "Fail"
                message = "Expected {}, found {}".format(expected, actual)

        elif rule_type == "not_empty":
            fields = params.get("fields", [])
            pi = self.doc.ProjectInformation
            empty = []
            for field in fields:
                param = pi.LookupParameter(field)
                if not param or not param.HasValue or not str(param.AsString()).strip():
                    empty.append(field)
            if empty:
                status = "Fail"
                message = "Empty fields: {}".format(", ".join(empty))
            else:
                message = "All required fields are filled"

        elif rule_type == "coordinate_match":
            point_type = params.get("point_type", "survey")
            axis = params.get("axis", "NS")
            expected = params.get("expected_value", 0.0)
            tolerance = params.get("tolerance", 0.001)

            collector = FilteredElementCollector(self.doc).OfClass(BasePoint)
            actual_value = None
            for bp in collector:
                is_survey = bp.IsShared
                if (point_type == "survey" and is_survey) or (point_type == "base" and not is_survey):
                    pos = bp.Position
                    if axis == "NS": actual_value = pos.Y
                    elif axis == "EW": actual_value = pos.X
                    elif axis == "Elev": actual_value = pos.Z
                    break
            
            if actual_value is None:
                status = "Fail"
                message = "Could not find point"
            else:
                diff = abs(actual_value - expected)
                if diff <= tolerance:
                    message = "Coord {} matches within tolerance ({:.4f})".format(axis, actual_value)
                else:
                    status = "Fail"
                    message = "Coord {} is {:.4f} (expected: {}, diff: {:.4f})".format(axis, actual_value, expected, diff)

        elif rule_type == "count_check":
            target = params.get("target", "")
            max_count = params.get("max_count", 0)
            
            count = 0
            if target == "design_options":
                count = FilteredElementCollector(self.doc).OfClass(DesignOption).GetElementCount()
            elif target == "warnings":
                count = len(self.doc.GetWarnings())
                
            if count > max_count:
                status = "Warning" if rule.get("severity") == "warning" else "Fail"
                message = "Found {} items (max allowed: {})".format(count, max_count)
            else:
                message = "Count: {} (max: {})".format(count, max_count)

        elif rule_type == "exists_check":
            target = params.get("target", "")
            if target == "starting_view":
                try:
                    sv = StartingViewSettings.GetStartingViewSettings(self.doc)
                    if sv and sv.ViewId != ElementId.InvalidElementId:
                        view = self.doc.GetElement(sv.ViewId)
                        message = "Starting view set to: {}".format(view.Name if view else "Unknown")
                    else:
                        status = "Fail"
                        message = "No starting view is configured"
                except:
                    status = "Fail"
                    message = "No starting view is configured"

        elif rule_type == "value_match_numeric":
            target = params.get("target", "")
            expected = params.get("expected_value", 0.0)
            tolerance = params.get("tolerance", 0.01)
            if target == "true_north":
                try:
                    pl = self.doc.ActiveProjectLocation
                    pos = pl.GetProjectPosition(DB.XYZ.Zero)
                    angle = pos.Angle * 180.0 / 3.14159265358979
                    diff = abs(angle - expected)
                    if diff <= tolerance:
                        message = "True North angle matches: {:.2f} deg".format(angle)
                    else:
                        status = "Fail"
                        message = "True North angle is {:.2f} deg (expected: {})".format(angle, expected)
                except:
                    status = "Fail"
                    message = "Failed to retrieve True North"

        else:
            status = "Skipped"
            message = "Rule type '{}' not implemented".format(rule_type)

        return {
            "id": rule.get("id", ""),
            "category": rule.get("category", ""),
            "severity": rule.get("severity", ""),
            "name": rule.get("name", ""),
            "status": status,
            "message": message
        }

# ============================================================================
# SMART DELETE DEPENDENCY ANALYSIS HELPERS
# ============================================================================
class DepInfo(object):
    def __init__(self, eid, name, dep_type, severity, desc="", view_name=""):
        self.eid = eid
        self.name = name
        self.dep_type = dep_type
        self.severity = severity
        self.desc = desc
        self.view_name = view_name

    def to_string(self):
        v = " [{}]".format(self.view_name) if self.view_name else ""
        return "{:<10} | {:<20} | {:<20} | {:<10} | {}{}".format(
            self.eid, self.name[:20], self.dep_type[:20], self.severity, self.desc, v
        )

def analyze_element(element, document):
    deps = []
    el_id = element.Id

    # 1. Group
    try:
        gid = element.GroupId
        if gid and gid != ElementId.InvalidElementId:
            g = document.GetElement(gid)
            if g:
                deps.append(DepInfo(_eid_int(gid), g.Name or "Group", "Group", "Critical", "In group"))
    except: pass

    # 2. Hosted elements
    try:
        bb = element.get_BoundingBox(None)
        if bb:
            ol = Outline(bb.Min, bb.Max)
            bbf = BoundingBoxIntersectsFilter(ol)
            nearby = FilteredElementCollector(document).OfClass(FamilyInstance).WherePasses(bbf)
            n = 0
            for fi in nearby:
                if n >= 15 or fi.Id == el_id: continue
                try:
                    h = fi.Host
                    if h and h.Id == el_id:
                        cn = fi.Category.Name if fi.Category else "Hosted"
                        deps.append(DepInfo(_eid_int(fi.Id), fi.Name or "Hosted", "Hosted ({})".format(cn), "Critical", "DELETED with host"))
                        n += 1
                except: continue
    except: pass

    # 3. Dimensions
    try:
        dim_filter = ElementClassFilter(Dimension)
        dim_ids = element.GetDependentElements(dim_filter)
        if dim_ids:
            for did in dim_ids:
                if did == el_id: continue
                d = document.GetElement(did)
                if d:
                    val = d.ValueString or ""
                    vn = ""
                    if d.OwnerViewId != ElementId.InvalidElementId:
                        v = document.GetElement(d.OwnerViewId)
                        if v: vn = v.Name
                    deps.append(DepInfo(_eid_int(did), "Dim: {}".format(val) if val else "Dim", "Dimension", "High", "Will be deleted", vn))
    except: pass

    # 4. Tags
    try:
        tag_filter = ElementClassFilter(IndependentTag)
        tag_ids = element.GetDependentElements(tag_filter)
        if tag_ids:
            for tid in tag_ids:
                if tid == el_id: continue
                t = document.GetElement(tid)
                if t:
                    tt = t.TagText or ""
                    vn = ""
                    if t.OwnerViewId != ElementId.InvalidElementId:
                        v = document.GetElement(t.OwnerViewId)
                        if v: vn = v.Name
                    deps.append(DepInfo(_eid_int(tid), tt or "Tag", "Tag", "Medium", "Will be deleted", vn))
    except: pass

    return deps

# ============================================================================
# UTILITIES
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

def parse_slog_time(time_str):
    try:
        return datetime.datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M:%S.%f")
    except:
        try:
            return datetime.datetime.strptime(time_str.split(".")[0].strip(), "%Y-%m-%d %H:%M:%S")
        except:
            return None

# ============================================================================
# MAIN AUDITOR WINDOW
# ============================================================================
class ModelAuditorWindow(T3WPFWindow):

    # ProgressPauseMixin — ModelAuditor.xaml footer progress panel
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
        
        # Sidebar nav is wired via Click="on_sidebar_clicked" in XAML
        self.main_tab_control.SelectionChanged += self._on_main_tab_changed
        self._go_to_main_tab(0)

        # Connect sub-tab navigation events
        self.btn_sub_inplace.Checked += self._on_sub_tab_changed
        self.btn_sub_material.Checked += self._on_sub_tab_changed
        self.btn_sub_smart_purge.Checked += self._on_sub_tab_changed
        self.btn_sub_advanced_purge.Checked += self._on_sub_tab_changed
        self.btn_sub_smart_delete.Checked += self._on_sub_tab_changed

        # Chrome actions
        self.btn_minimize.Click += self._minimize
        self.btn_maximize.Click += self._maximize
        self.btn_close.Click += self._close_chrome

        # Hook button events
        # 1. Health tab
        self.btn_health_run.Click += self.on_health_run
        btn_ai = getattr(self, 'btn_health_ai_summary', None) or self.FindName('btn_health_ai_summary')
        if btn_ai is not None:
            btn_ai.Click += self.ai_health_summary_clicked
        self.btn_health_export.Click += self.on_health_export
        self._update_ai_status()
        
        # 2. Compliance tab
        self.btn_checker_run.Click += self.on_checker_run
        self.btn_checker_export.Click += self.on_checker_export
        self._init_checksets()

        # Progress Pause/Stop (ProgressPauseMixin) — wire footer buttons
        if getattr(self, "ma_btn_pause", None) is not None:
            self.ma_btn_pause.Click += self.pause_resume_clicked
        if getattr(self, "ma_btn_stop", None) is not None:
            self.ma_btn_stop.Click += self.stop_clicked

        # 3. Warnings tab
        self.btn_warning_reload.Click += self.on_warning_reload
        self.btn_warning_autofix.Click += self.on_warning_autofix
        self.btn_warning_export.Click += self.on_warning_export
        self.btn_warning_select_elements.Click += self.on_warning_select_elements
        self.dg_warning_groups.SelectionChanged += self.on_warning_group_changed

        # 4. Cleanup tab
        self.btn_smart_purge_check_all.Click += self.on_smart_purge_check_all
        self.btn_smart_purge_uncheck_all.Click += self.on_smart_purge_uncheck_all
        self.btn_smart_purge_run.Click += self.on_smart_purge_run
        self.btn_adv_purge_run.Click += self.on_adv_purge_run
        self.btn_delete_analyze.Click += self.on_delete_analyze
        self.btn_delete_run.Click += self.on_delete_run

        # 5. Special Audits tab
        self.btn_special_inplace_reload.Click += self.on_inplace_reload
        self.btn_special_inplace_select.Click += self.on_inplace_select
        self.btn_special_inplace_delete.Click += self.on_inplace_delete
        self.btn_special_materials_reload.Click += self.on_materials_reload
        self.btn_special_materials_export.Click += self.on_materials_export

        # Ready state - diagnostics run on user action via Re-Analyze button
        self.status_text.Text = "Ready. Click 'Re-Analyze' to start model diagnostics."

    # ========================================================================
    # WINDOW CONTROL ACTIONS
    # ========================================================================
    def _minimize(self, sender, e):
        self.WindowState = WindowState.Minimized

    def _maximize(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def _close_chrome(self, sender, e):
        self.Close()

    def close_button_clicked(self, sender, e):
        self._close_chrome(sender, e)

    _NAV_STATUS = [
        "Model Health Dashboard — Health score and summary metrics",
        "Compliance Checker — JSON rule-based BEP auditing",
        "Warning Manager — Warnings impact analysis and auto-fixing",
        "",
        "",
    ]

    _SIDEBAR_MAP = {
        'btn_tab_health': 0, 'btn_tab_compliance': 1,
        'btn_tab_warning': 2, 'btn_tab_cleanup': 3, 'btn_tab_elements': 4,
    }

    def on_sidebar_clicked(self, sender, e):
        idx = self._SIDEBAR_MAP.get(sender.Name, -1)
        if idx >= 0:
            self._go_to_main_tab(idx)

    def _flush_ui(self):
        """Force WPF to process pending render operations so progress shows in realtime."""
        try:
            from System.Windows.Threading import DispatcherPriority
            from System import Action
            self.Dispatcher.Invoke(DispatcherPriority.Render, Action(lambda: None))
        except Exception:
            pass

    def _set_progress(self, percent, text=None):
        """Legacy simple progress (no Pause/Stop). Toggles the whole
        ma_progress_panel so its bar stays visible; batch loops now use the
        ProgressPauseMixin (begin_progress/step_progress/end_progress)."""
        try:
            import System
            panel = getattr(self, "ma_progress_panel", None)
            if percent is None or percent < 0:
                if panel is not None:
                    panel.Visibility = System.Windows.Visibility.Collapsed
                else:
                    self.progress_bar.Visibility = System.Windows.Visibility.Collapsed
            else:
                if panel is not None:
                    panel.Visibility = System.Windows.Visibility.Visible
                self.progress_bar.Visibility = System.Windows.Visibility.Visible
                self.progress_bar.Value = percent
            if text:
                self.status_text.Text = text
            self._flush_ui()
        except Exception:
            pass

    def _on_main_tab_changed(self, sender, e):
        """Sync sidebar toggle buttons when tab changes via keyboard/programmatic switch."""
        try:
            from System.Windows.Controls import TabControl as _TC
            if not isinstance(e.Source, _TC):
                return
            idx = self.main_tab_control.SelectedIndex
            self.btn_tab_health.IsChecked     = (idx == 0)
            self.btn_tab_compliance.IsChecked = (idx == 1)
            self.btn_tab_warning.IsChecked    = (idx == 2)
            self.btn_tab_cleanup.IsChecked    = (idx == 3)
            self.btn_tab_elements.IsChecked   = (idx == 4)
            if self._NAV_STATUS[idx]:
                self.status_text.Text = self._NAV_STATUS[idx]
        except Exception:
            pass

    def _go_to_main_tab(self, index):
        self.main_tab_control.SelectedIndex = index
        self.btn_tab_health.IsChecked     = (index == 0)
        self.btn_tab_compliance.IsChecked = (index == 1)
        self.btn_tab_warning.IsChecked    = (index == 2)
        self.btn_tab_cleanup.IsChecked    = (index == 3)
        self.btn_tab_elements.IsChecked   = (index == 4)
        if self._NAV_STATUS[index]:
            self.status_text.Text = self._NAV_STATUS[index]
        if index == 3:
            self._update_cleanup_status()
        elif index == 4:
            self._update_special_status()

    def _on_sub_tab_changed(self, sender, e):
        """Switch sub-tab for Special Audits and Cleanup."""
        if sender == self.btn_sub_smart_purge:
            self.cleanup_tab_control.SelectedIndex = 0
            self._update_cleanup_status()
        elif sender == self.btn_sub_advanced_purge:
            self.cleanup_tab_control.SelectedIndex = 1
            self._update_cleanup_status()
        elif sender == self.btn_sub_smart_delete:
            self.cleanup_tab_control.SelectedIndex = 2
            self._update_cleanup_status()
        elif sender == self.btn_sub_inplace:
            self.sub_tab_control.SelectedIndex = 0
            self._update_special_status()
        elif sender == self.btn_sub_material:
            self.sub_tab_control.SelectedIndex = 1
            self._update_special_status()

    def _update_cleanup_status(self):
        if self.btn_sub_smart_purge.IsChecked:
            self.status_text.Text = "Smart Purge — Scan and safely delete unused model families/elements"
            if not self.dg_smart_purge.ItemsSource:
                self.load_smart_purge()
        elif self.btn_sub_advanced_purge.IsChecked:
            self.status_text.Text = "Advanced Purge — Select deep model types to purge"
        elif self.btn_sub_smart_delete.IsChecked:
            self.status_text.Text = "Smart Delete — Analyze dependencies of elements before deleting"

    def _update_special_status(self):
        if self.btn_sub_inplace.IsChecked:
            self.status_text.Text = "In-Place Model Auditor — Manage in-place family instances in the project"
            if not self.dg_special_inplace.ItemsSource:
                self.on_inplace_reload(None, None)
        elif self.btn_sub_material.IsChecked:
            self.status_text.Text = "Material List Auditor — Volume and area calculation for project materials"
            if not self.dg_special_materials.ItemsSource:
                self.on_materials_reload(None, None)

    # ========================================================================
    # TAB 1: HEALTH DASHBOARD
    # ========================================================================
    def _get_indicator_percent(self, val, thresholds):
        t0, t1, t2, t3, t4 = thresholds
        if val <= t0:
            pct = 10
        elif val <= t1:
            pct = 10 + 20 * float(val - t0) / max(t1 - t0, 1)
        elif val <= t2:
            pct = 30 + 20 * float(val - t1) / max(t2 - t1, 1)
        elif val <= t3:
            pct = 50 + 20 * float(val - t2) / max(t3 - t2, 1)
        elif val <= t4:
            pct = 70 + 20 * float(val - t3) / max(t4 - t3, 1)
        else:
            pct = 100
        return int(pct)

    def _update_ai_status(self):
        try:
            txt = getattr(self, 'txt_ai_status', None) or self.FindName('txt_ai_status')
            if txt is not None:
                if self.is_ai_mode_active("ModelAuditor"):
                    info = self.get_ai_status_info()
                    txt.Text = "AI Mode: " + info.get("label", "Active")
                else:
                    txt.Text = "AI Mode: Offline"
        except Exception:
            pass

    def ai_health_summary_clicked(self, sender=None, e=None):
        """Generate an AI Executive Health Summary based on current model audit metrics."""
        if not self.is_ai_mode_active("ModelAuditor"):
            forms.alert(
                "AI Mode is currently offline or no API Key is configured.\n\n"
                "Please configure an API Key in LLMs Setting to enable AI Executive Summaries.",
                title="AI Mode Offline"
            )
            return

        grade_tb = getattr(self, 'txt_health_grade', None) or self.FindName('txt_health_grade')
        grade = grade_tb.Text if grade_tb else "--"

        score_tb = getattr(self, 'txt_health_score', None) or self.FindName('txt_health_score')
        score = score_tb.Text if score_tb else "--"

        doc = getattr(self, 'doc', None) or getattr(self, '_doc', None)
        doc_name = doc.Title if (doc and hasattr(doc, 'Title')) else "Active Model"

        # Collect metrics summary and top warnings
        sum_tb = getattr(self, 'txt_health_summary_metrics', None) or self.FindName('txt_health_summary_metrics')
        summary_text = sum_tb.Text if sum_tb else ""
        warning_count = len(doc.GetWarnings()) if (doc and hasattr(doc, 'GetWarnings')) else 0

        top_warnings = []
        try:
            if doc and hasattr(doc, 'GetWarnings'):
                raw_warnings = doc.GetWarnings()
                warn_counts = {}
                for w in raw_warnings:
                    desc = w.GetDescriptionText()
                    if desc:
                        first_line = desc.split('\n')[0].strip()
                        warn_counts[first_line] = warn_counts.get(first_line, 0) + 1
                sorted_warns = sorted(warn_counts.items(), key=lambda x: x[1], reverse=True)[:5]
                top_warnings = ["- {} ({} instances)".format(t, c) for t, c in sorted_warns]
        except Exception as warn_ex:
            try:
                logger.warning("Error collecting warning frequencies: {}".format(warn_ex))
            except Exception:
                pass

        top_warn_text = "\n".join(top_warnings) if top_warnings else "None detected"

        prompt = (
            "Analyze the following Revit BIM Model Health metrics and produce a concise, professional "
            "Executive Health Summary in Vietnamese (with English section headers) for the BIM Manager.\n\n"
            "Model Name: {}\n"
            "Overall Grade: {}\n"
            "Health Score: {}\n"
            "Total Revit Warnings: {}\n"
            "Top Warning Categories by Frequency:\n{}\n\n"
            "Diagnostic Summary:\n{}\n\n"
            "Structure your response with:\n"
            "1. TỔNG QUAN SỨC KHỎE MÔ HÌNH (Executive Health Rating)\n"
            "2. TOP 3 NGUY CƠ ẢNH HƯỞNG HIỆU NĂNG (Top 3 Performance Risks & Root Causes)\n"
            "3. HÀNH ĐỘNG KHẮC PHỤC ƯU TIÊN (Immediate Recommended Actions)\n"
            "Keep it crisp, actionable and technical."
        ).format(doc_name, grade, score, warning_count, top_warn_text, summary_text)

        st = getattr(self, 'status_text', None) or self.FindName('status_text')
        if st:
            st.Text = "✨ AI is synthesizing executive health report..."

        btn_ai = getattr(self, 'btn_health_ai_summary', None) or self.FindName('btn_health_ai_summary')
        orig_content = "✨ AI Summary"

        def _restore_btn():
            if btn_ai:
                btn_ai.Content = orig_content
                btn_ai.IsEnabled = True

        if btn_ai:
            btn_ai.Content = "⏳ Synthesizing..."
            btn_ai.IsEnabled = False

        def _bg():
            b = self.ai_bridge
            if not b:
                return None
            return b.ask(prompt, max_tokens=1500)

        def _on_done(res):
            _restore_btn()
            s_el = getattr(self, 'status_text', None) or self.FindName('status_text')
            if s_el:
                s_el.Text = "✨ AI Health Summary completed."
            if res:
                forms.alert(res, title="✨ AI Executive Model Health Summary")
                main_sum = getattr(self, 'txt_health_summary', None) or self.FindName('txt_health_summary')
                if main_sum:
                    main_sum.Text = "✨ AI Analysis Complete. Click '✨ AI Summary' to re-read."
            else:
                forms.alert("AI could not generate summary. Check internet connection and API Key.", title="AI Summary Error")

        def _on_err(err):
            _restore_btn()
            s_el = getattr(self, 'status_text', None) or self.FindName('status_text')
            if s_el:
                s_el.Text = "AI Error: " + str(err)
            forms.alert("AI Error:\n" + str(err), title="AI Error")

        self.run_ai_async(_bg, _on_done, _on_err)

    def on_health_run(self, sender, e):
        self.status_text.Text = "Running model health analysis..."
        # Progress bar + Pause/Stop while the 17-step analysis runs
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
        
        # Update circular score badge and middle profile info
        from System.Windows.Media import BrushConverter
        if score >= 90:
            bg_color, fg_color = "#E6F4EA", "#10B981"
        elif score >= 75:
            bg_color, fg_color = "#F7FEE7", "#84CC16"
        elif score >= 60:
            bg_color, fg_color = "#FFFBEB", "#F59E0B"
        elif score >= 40:
            bg_color, fg_color = "#FFEDD5", "#F97316"
        else:
            bg_color, fg_color = "#FEE2E2", "#EF4444"
            
        brush_bg = BrushConverter().ConvertFromString(bg_color)
        brush_fg = BrushConverter().ConvertFromString(fg_color)
        
        self.ellipse_health_bg.Fill = brush_bg
        self.ellipse_health_color.Fill = brush_fg
        
        self.txt_health_grade.Text = grade
        self.txt_health_score.Text = str(score)
        self.txt_health_badge_label.Text = "{} - {}".format(grade, desc)
        self.txt_health_badge_label.Foreground = brush_fg
        
        self.txt_health_title.Text = "Model Health: {}".format(desc)
        self.txt_health_weighted_score.Text = "Weighted Score: {}/100".format(score)
        self.txt_health_weighted_grade.Text = "Grade: {} ({})".format(grade, desc)

        # RAG (Red/Orange/Green) status — Autodesk Model Analytics style
        rag_label, rag_fg, rag_bg = _rag_status(score)
        self.txt_health_rag.Text = "RAG: {}".format(rag_label)
        self.txt_health_rag.Foreground = BrushConverter().ConvertFromString(rag_fg)
        self.border_health_rag.Background = BrushConverter().ConvertFromString(rag_bg)

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
            self.txt_health_trend.Foreground = BrushConverter().ConvertFromString("#94A3B8")
        else:
            delta = round(score - prev_score, 1)
            if delta > 0:
                self.txt_health_trend.Text = u"▲ +{} vs last run".format(delta)
                self.txt_health_trend.Foreground = BrushConverter().ConvertFromString("#10B981")
            elif delta < 0:
                self.txt_health_trend.Text = u"▼ {} vs last run".format(delta)
                self.txt_health_trend.Foreground = BrushConverter().ConvertFromString("#EF4444")
            else:
                self.txt_health_trend.Text = u"— No change vs last run"
                self.txt_health_trend.Foreground = BrushConverter().ConvertFromString("#64748B")

        # Update text info
        doc_name = os.path.basename(self.doc.PathName) if self.doc.PathName else "Unsaved Project"
        self.txt_health_doc_name.Text = "Model: {}".format(doc_name)
        self.txt_health_last_run.Text = "Last analyzed: {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Load metrics data to DataGrid and collect counts & recommendations
        grid_data = []
        counts = {"Good": 0, "Acceptable": 0, "Warning": 0, "Concerning": 0, "Critical": 0, "Severe": 0}
        recs_data = []
        
        for key, value in self.health_results.items():
            if key in METRIC_THRESHOLDS:
                m_info = METRIC_THRESHOLDS[key]
                t = m_info["thresholds"]
                
                status_str = "Good"
                value_bg = "#10B981"
                status_fg = "#10B981"
                if value <= t[0]:
                    status_str = "Good"
                    value_bg = "#10B981"
                    status_fg = "#10B981"
                elif value <= t[1]:
                    status_str = "Acceptable"
                    value_bg = "#84CC16"
                    status_fg = "#84CC16"
                elif value <= t[2]:
                    status_str = "Warning"
                    value_bg = "#F59E0B"
                    status_fg = "#D97706"
                elif value <= t[3]:
                    status_str = "Concerning"
                    value_bg = "#F97316"
                    status_fg = "#EA580C"
                elif value <= t[4]:
                    status_str = "Critical"
                    value_bg = "#EF4444"
                    status_fg = "#DC2626"
                else:
                    status_str = "Severe"
                    value_bg = "#B91C1C"
                    status_fg = "#991B1B"
                
                counts[status_str] += 1
                
                val_str = "{}{}".format(value, " " + m_info["unit"] if m_info["unit"] else "")
                
                # Visual bar calculations
                indicator_pct = self._get_indicator_percent(value, t)
                indicator_width = int((indicator_pct / 100.0) * 180)
                
                # Stars calculation
                stars = u"★" * m_info["weight"] + u"☆" * (5 - m_info["weight"])
                weight_stars = "Weight: {} ({}/5)".format(stars, m_info["weight"])
                
                thresholds_text = "Thresholds: " + " | ".join(str(x) for x in t)
                
                # Element selectability
                has_elements = len(self.health_analyzer.element_ids.get(key, [])) > 0
                select_visibility = "Visible" if (m_info["selectable"] and has_elements) else "Collapsed"
                
                grid_row = GridRow(
                    key=key,
                    label=m_info["label"],
                    value_str=val_str,
                    value_bg=value_bg,
                    indicator_color=value_bg,
                    indicator_width=indicator_width,
                    status_title_full="Status: {}".format(status_str),
                    status_fg=status_fg,
                    weight_stars=weight_stars,
                    thresholds_text=thresholds_text,
                    select_visibility=select_visibility,
                    recommendation=m_info["recommendation"]
                )
                grid_data.append(grid_row)
                
                # Collect recommendations if status is concerning or worse
                if status_str in ["Warning", "Concerning", "Critical", "Severe"]:
                    recs_data.append(GridRow(
                        bullet_brush=BrushConverter().ConvertFromString(value_bg),
                        text="{} ({}): {}".format(m_info["label"], val_str, m_info["recommendation"])
                    ))
        
        # Build health summary text
        crit_severe = counts["Critical"] + counts["Severe"]
        warn_concern = counts["Warning"] + counts["Concerning"]
        summary_text = "{} critical/severe issues, {} warnings. {}".format(
            crit_severe, warn_concern,
            "Immediate attention needed." if crit_severe > 0 else "Model is in good shape."
        )
        self.txt_health_summary.Text = summary_text
        
        total_metrics = len(grid_data)
        metrics_breakdown = "Total: {} metrics | Good: {} | Acceptable: {} | Warning: {} | Concerning: {} | Critical: {} | Severe: {}".format(
            total_metrics, counts["Good"], counts["Acceptable"], counts["Warning"], counts["Concerning"], counts["Critical"], counts["Severe"]
        )
        self.txt_health_summary_metrics.Text = metrics_breakdown
        
        self.dg_health_metrics.ItemsSource = to_items_source(grid_data)
        self.lst_health_recommendations.ItemsSource = to_items_source(recs_data)

        self.status_text.Text = "Health analysis complete. Score: {}".format(score)

    def on_health_metric_select(self, sender, e):
        row = sender.DataContext
        if not row:
            return
        
        key = row.key
        ids_list = self.health_analyzer.element_ids.get(key, [])
        if not ids_list:
            forms.alert("No elements to select for metric: {}".format(row.label), title="Model Health Check")
            return
        
        elem_ids = [_make_eid(eid) for eid in ids_list]
        try:
            self.uidoc.Selection.SetElementIds(System.Collections.Generic.List[ElementId](elem_ids))
            self.uidoc.ShowElements(System.Collections.Generic.List[ElementId](elem_ids))
            self.status_text.Text = "Selected {} elements for {}".format(len(elem_ids), row.label)
        except Exception as ex:
            forms.alert("Failed to select elements:\n{}".format(ex))

    def on_health_export(self, sender, e):
        if not hasattr(self, 'health_results') or not self.health_results:
            forms.alert("Please run the diagnostics first.", title="Model Health Check")
            return
        
        try:
            default_name = "Model_Health_Report_{}".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            filepath = forms.save_file(filesfilter="CSV Files (*.csv)|*.csv", default_name=default_name)
            if not filepath:
                return
            
            with _open_csv_write(filepath) as f:
                import csv
                writer = csv.writer(f)
                writer.writerow(["Metric", "Value", "Status", "Recommendation"])
                for row in self.dg_health_metrics.ItemsSource:
                    writer.writerow([
                        _csv_cell(getattr(row, 'label', '')),
                        _csv_cell(getattr(row, 'value_str', '')),
                        _csv_cell(getattr(row, 'status_title_full', '')),
                        _csv_cell(getattr(row, 'recommendation', ''))
                    ])
            forms.alert("Health report exported successfully to:\n\n{}".format(filepath), title="Model Health Check")
        except Exception as ex:
            forms.alert("Failed to export health report:\n{}".format(ex))

    # ========================================================================
    # TAB 2: COMPLIANCE CHECKER
    # ========================================================================
    def _init_checksets(self):
        try:
            checksets_dir = os.path.join(os.path.dirname(__file__), "..", "Services", "ModelAuditor", "checksets")
            checksets_dir = os.path.normpath(checksets_dir)
            if os.path.exists(checksets_dir):
                checksets = [f for f in os.listdir(checksets_dir) if f.endswith(".json")]
                self.cb_checkset.ItemsSource = to_items_source([os.path.splitext(f)[0] for f in checksets])
                self.cb_checkset.SelectedIndex = 0
        except Exception as ex:
            print("Error initializing checksets: {}".format(ex))

    def on_checker_run(self, sender, e):
        checkset_name = self.cb_checkset.SelectedItem
        if not checkset_name:
            forms.alert("Please select a checkset.", title="Compliance Checker")
            return
        
        self.status_text.Text = "Running compliance audits on checkset '{}'...".format(checkset_name)
        self.begin_progress(100, disable=[sender])
        try:
            checksets_dir = os.path.join(os.path.dirname(__file__), "..", "Services", "ModelAuditor", "checksets")
            checksets_dir = os.path.normpath(checksets_dir)
            checkset_path = os.path.join(checksets_dir, checkset_name + ".json")

            with codecs.open(checkset_path, "r", "utf-8") as f:
                checkset_data = json.load(f)

            def progress_cb(current, total):
                pct = int(float(current) / float(total) * 100) if total > 0 else 0
                self.step_progress(pct, "Running rule {}/{}...".format(current, total))

            engine = RuleEngine(self.doc)
            results = engine.run_checkset(
                checkset_data,
                progress_callback=progress_cb,
                cancel_check=lambda: self.is_cancelled)
            self.dg_checker_results.ItemsSource = to_items_source([GridRow(**r) for r in results])

            cancelled = self.is_cancelled
            fails = sum(1 for r in results if r["status"] == "Fail")
            if cancelled:
                self.status_text.Text = "Compliance check cancelled. Rules run: {}, Fails: {}".format(len(results), fails)
            else:
                self.status_text.Text = "Compliance check complete. Rules run: {}, Fails: {}".format(len(results), fails)
        except Exception as ex:
            forms.alert("Failed to run compliance check:\n{}".format(ex), title="Error")
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass
        finally:
            self.end_progress()

    def on_checker_export(self, sender, e):
        items = self.dg_checker_results.ItemsSource
        if not items:
            forms.alert("No check results to export.", title="Compliance Checker")
            return
        
        filepath = forms.save_file(file_ext="csv", default_name="compliance_report.csv")
        if not filepath:
            return
        
        try:
            with _open_csv_write(filepath) as f:
                writer = csv.writer(f)
                writer.writerow(["ID", "Category", "Severity", "Rule Name", "Result", "Details"])
                for item in items:
                    writer.writerow([
                        _csv_cell(item.get("id", "")),
                        _csv_cell(item.get("category", "")),
                        _csv_cell(item.get("severity", "")),
                        _csv_cell(item.get("name", "")),
                        _csv_cell(item.get("status", "")),
                        _csv_cell(item.get("message", ""))
                    ])
            forms.alert("Report exported successfully to:\n\n{}".format(filepath), title="Compliance Checker")
        except Exception as ex:
            forms.alert("Failed to export report:\n{}".format(ex))

    # ========================================================================
    # TAB 3: WARNING MANAGER
    # ========================================================================
    def on_warning_reload(self, sender, e):
        self.status_text.Text = "Collecting Revit warnings..."
        try:
            warnings = self.doc.GetWarnings()
            self.warning_groups_dict = defaultdict(list)
            
            if warnings:
                for w in warnings:
                    desc = w.GetDescriptionText()
                    elements = list(w.GetFailingElements())
                    self.warning_groups_dict[desc].extend(elements)
            
            # Format for DataGrid
            grid_data = []
            for desc, elements in self.warning_groups_dict.items():
                grid_data.append(GridRow(
                    description=desc,
                    count=len(elements),
                    element_ids=[_eid_int(eid) for eid in elements]
                ))
            
            self.dg_warning_groups.ItemsSource = to_items_source(sorted(grid_data, key=lambda x: x.count, reverse=True))
            self.lst_warning_elements.ItemsSource = None
            self.status_text.Text = "Loaded {} warnings in {} unique groups".format(len(warnings), len(grid_data))
        except Exception as ex:
            print("Error loading warnings: {}".format(ex))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass

    def on_warning_group_changed(self, sender, e):
        selected = self.dg_warning_groups.SelectedItem
        if not selected:
            return
        
        ids = selected.get("element_ids", [])
        list_items = []
        for id_val in ids:
            try:
                el = self.doc.GetElement(_make_eid(id_val))
                name = el.Name if el else "Unknown"
                cat_name = el.Category.Name if el and el.Category else "Element"
                list_items.append("{} : {} [{}]".format(cat_name, name, id_val))
            except:
                list_items.append("Element [{}]".format(id_val))
                
        self.lst_warning_elements.ItemsSource = to_items_source(list_items)

    def on_warning_select_elements(self, sender, e):
        selected_items = self.lst_warning_elements.SelectedItems
        if not selected_items:
            forms.alert("Please select one or more elements in the list first.", title="Warning Manager")
            return
        
        elem_ids = []
        for item in selected_items:
            # Extract id in brackets [12345]
            match = re.search(r'\[(\d+)\]', item)
            if match:
                elem_ids.append(_make_eid(int(match.group(1))))
        
        if elem_ids:
            self.uidoc.Selection.SetElementIds(System.Collections.Generic.List[ElementId](elem_ids))
            self.uidoc.ShowElements(System.Collections.Generic.List[ElementId](elem_ids))
            self.status_text.Text = "Selected {} elements in model".format(len(elem_ids))

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
            
            if to_delete:
                t = Transaction(self.doc, "Resolve Overlapping Duplicates")
                t.Start()
                count = 0
                for eid in to_delete:
                    try:
                        self.doc.Delete(eid)
                        count += 1
                    except: pass
                t.Commit()
                
                forms.alert("Successfully deleted {} duplicate elements.".format(count), title="Warning Manager")
                self.on_warning_reload(None, None)
            else:
                forms.alert("No duplicate elements could be collected for deletion.", title="Warning Manager")
        except Exception as ex:
            forms.alert("Error resolving duplicates:\n{}".format(ex))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass

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
                                self.purge_items.append(item_row)
                except Exception as ex:
                    pass

            cancelled = self.is_cancelled
            self.end_progress()
            self.dg_smart_purge.ItemsSource = to_items_source(self.purge_items)
            if cancelled:
                self.status_text.Text = "Smart Purge scan cancelled. Unused items found so far: {}".format(len(self.purge_items))
            else:
                self.status_text.Text = "Smart Purge scan complete. Unused items found: {}".format(len(self.purge_items))
        except Exception as ex:
            self.end_progress()
            forms.alert("Failed to load smart purge elements:\n{}".format(ex))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass

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
        if not hasattr(self, 'purge_items') or not self.purge_items:
            return
        
        selected_ids = []
        for item in self.purge_items:
            if item.get("is_selected", False) and item.get("can_delete", True):
                selected_ids.append(_make_eid(item["id"]))
        
        if not selected_ids:
            forms.alert("No valid elements selected for purging.", title="Smart Purge")
            return
        
        try:
            t = Transaction(self.doc, "Smart Purge Unused Elements")
            t.Start()
            deleted = self.doc.Delete(System.Collections.Generic.List[ElementId](selected_ids))
            t.Commit()
            
            forms.alert("Successfully purged {} elements (including sub-elements).".format(len(deleted)), title="Smart Purge")
            self.load_smart_purge()
        except Exception as ex:
            forms.alert("Error executing purge:\n{}".format(ex))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass

    def on_adv_purge_run(self, sender, e):
        # Gather selections
        purge_views = self.chk_adv_views.IsChecked
        purge_templates = self.chk_adv_templates.IsChecked
        purge_filters = self.chk_adv_filters.IsChecked
        purge_materials = self.chk_adv_materials.IsChecked
        purge_dwg = self.chk_adv_dwg.IsChecked
        purge_styles = self.chk_adv_styles.IsChecked

        if not any([purge_views, purge_templates, purge_filters, purge_materials, purge_dwg, purge_styles]):
            forms.alert("Please check at least one deep category to purge.", title="Advanced Purge")
            return
        
        self.status_text.Text = "Deep scanning advanced categories..."
        to_delete_ids = []
        
        try:
            # 1. Views
            if purge_views:
                views = FilteredElementCollector(self.doc).OfClass(View).WhereElementIsNotElementType().ToElements()
                for v in views:
                    if not v.IsTemplate and v.ViewType != DB.ViewType.Internal and not v.ViewType == DB.ViewType.ProjectBrowser:
                        if hasattr(v, "ViewSheetId") and v.ViewSheetId == ElementId.InvalidElementId:
                            to_delete_ids.append(v.Id)
            
            # 2. View Templates
            if purge_templates:
                views = FilteredElementCollector(self.doc).OfClass(View).WhereElementIsNotElementType().ToElements()
                used_templates = set()
                templates = []
                for v in views:
                    if v.IsTemplate:
                        templates.append(v)
                    elif v.ViewTemplateId != ElementId.InvalidElementId:
                        used_templates.add(_eid_int(v.ViewTemplateId))
                for t in templates:
                    if _eid_int(t.Id) not in used_templates:
                        to_delete_ids.append(t.Id)

            # 3. View Filters
            if purge_filters:
                from Services.ModelAuditor.smart_purge.purge_scanner import FilterScanner
                scanner = FilterScanner(self.doc)
                with SilenceOutput():
                    result = scanner.scan()
                if result:
                    to_delete_ids.extend([_make_eid(item["id"]) for item in result])

            # 4. Materials
            if purge_materials:
                from Services.ModelAuditor.smart_purge.purge_scanner import MaterialScanner
                scanner = MaterialScanner(self.doc)
                with SilenceOutput():
                    result = scanner.scan()
                if result:
                    to_delete_ids.extend([_make_eid(item["id"]) for item in result])

            # 5. DWG Imports / Links
            if purge_dwg:
                dwgs = FilteredElementCollector(self.doc).OfClass(ImportInstance).WhereElementIsNotElementType().ToElements()
                to_delete_ids.extend([el.Id for el in dwgs])

            # 6. Line Styles / Fill Patterns
            if purge_styles:
                from Services.ModelAuditor.smart_purge.purge_scanner import FillPatternScanner
                scanner = FillPatternScanner(self.doc)
                with SilenceOutput():
                    result = scanner.scan()
                if result:
                    to_delete_ids.extend([_make_eid(item["id"]) for item in result])

            if not to_delete_ids:
                forms.alert("No unused items found in the selected categories.", title="Advanced Purge")
                return
            
            # Delete elements
            t = Transaction(self.doc, "Advanced Purge")
            t.Start()
            deleted = self.doc.Delete(System.Collections.Generic.List[ElementId](to_delete_ids))
            t.Commit()
            
            forms.alert("Deep Purge Complete!\n\nDeleted elements: {}".format(len(deleted)), title="Advanced Purge")
            self.status_text.Text = "Deep Purge complete. Deleted: {}".format(len(deleted))
        except Exception as ex:
            forms.alert("Advanced Purge failed:\n{}".format(ex))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass

    def on_delete_analyze(self, sender, e):
        id_str = self.tb_delete_ids.Text.strip()
        if not id_str:
            forms.alert("Please input one or more element IDs to analyze.", title="Smart Delete")
            return
        
        self.status_text.Text = "Analyzing element dependency tree..."
        self.delete_elements_ids = []
        deps_list = []
        
        try:
            ids = [int(x.strip()) for x in id_str.split(",") if x.strip().isdigit()]
            for id_val in ids:
                el = self.doc.GetElement(_make_eid(id_val))
                if el:
                    self.delete_elements_ids.append(el.Id)
                    name = el.Name or "Element"
                    cat = el.Category.Name if el.Category else "System"
                    deps_list.append("TARGET: {} [{}] ({})".format(name, id_val, cat))
                    
                    children = analyze_element(el, self.doc)
                    for dep in children:
                        deps_list.append("  ↳ " + dep.to_string())
                        self.delete_elements_ids.append(_make_eid(dep.eid))
            
            self.lst_delete_dependencies.ItemsSource = to_items_source(deps_list)
            self.btn_delete_run.IsEnabled = len(self.delete_elements_ids) > 0
            self.status_text.Text = "Dependency check complete. Found {} total elements (including targets).".format(len(self.delete_elements_ids))
        except Exception as ex:
            forms.alert("Dependency check failed:\n{}".format(ex))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass

    def on_delete_run(self, sender, e):
        if not hasattr(self, 'delete_elements_ids') or not self.delete_elements_ids:
            return
        
        confirm = forms.MessageBox.show(
            "Are you sure you want to delete these {} elements?\nThis action cannot be selectively undone.".format(len(self.delete_elements_ids)),
            title="Confirm Safe Delete",
            yes=True, no=True
        )
        if not confirm:
            return
            
        try:
            t = Transaction(self.doc, "Smart Delete Elements")
            t.Start()
            deleted = self.doc.Delete(System.Collections.Generic.List[ElementId](self.delete_elements_ids))
            t.Commit()
            
            forms.alert("Smart Delete complete! Deleted: {} elements".format(len(deleted)), title="Smart Delete")
            self.tb_delete_ids.Text = ""
            self.lst_delete_dependencies.ItemsSource = None
            self.btn_delete_run.IsEnabled = False
        except Exception as ex:
            forms.alert("Delete failed:\n{}".format(ex))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass

    # ========================================================================
    # TAB 5: SPECIAL AUDITS
    # ========================================================================
    def on_inplace_reload(self, sender, e):
        self.status_text.Text = "Auditing In-Place families..."
        self.inplace_items = []
        try:
            for el in FilteredElementCollector(self.doc).OfClass(FamilyInstance).WhereElementIsNotElementType():
                try:
                    if el.Symbol and el.Symbol.Family and el.Symbol.Family.IsInPlace:
                        self.inplace_items.append(GridRow(
                            id=_eid_int(el.Id),
                            category=el.Category.Name if el.Category else "N/A",
                            family_name=el.Symbol.Family.Name,
                            type_name=el.Name
                        ))
                except: pass
            
            self.dg_special_inplace.ItemsSource = to_items_source(self.inplace_items)
            self.status_text.Text = "In-Place Model audit complete. Found: {}".format(len(self.inplace_items))
        except Exception as ex:
            print("Error loading in-place models: {}".format(ex))

    def on_inplace_select(self, sender, e):
        selected = self.dg_special_inplace.SelectedItems
        if not selected:
            forms.alert("Please select in-place models in the list first.", title="In-Place Auditor")
            return
        
        ids = [_make_eid(item["id"]) for item in selected]
        self.uidoc.Selection.SetElementIds(System.Collections.Generic.List[ElementId](ids))
        self.uidoc.ShowElements(System.Collections.Generic.List[ElementId](ids))

    def on_inplace_delete(self, sender, e):
        selected = self.dg_special_inplace.SelectedItems
        if not selected:
            return
        
        confirm = forms.MessageBox.show("Delete these {} selected in-place families?".format(len(selected)), yes=True, no=True)
        if not confirm:
            return
            
        try:
            ids = [_make_eid(item["id"]) for item in selected]
            t = Transaction(self.doc, "Delete In-Place Families")
            t.Start()
            self.doc.Delete(System.Collections.Generic.List[ElementId](ids))
            t.Commit()
            
            self.on_inplace_reload(None, None)
        except Exception as ex:
            forms.alert("Failed to delete families:\n{}".format(ex))

    def on_materials_reload(self, sender, e):
        self.status_text.Text = "Calculating material volume & area usage..."
        self.material_items = []
        try:
            materials = FilteredElementCollector(self.doc).OfClass(Material).ToElements()
            material_map = {_eid_int(m.Id): m for m in materials}
            
            material_volume = {}
            material_area = {}
            
            categories = [
                BuiltInCategory.OST_Walls,
                BuiltInCategory.OST_Floors,
                BuiltInCategory.OST_Roofs,
                BuiltInCategory.OST_Ceilings,
                BuiltInCategory.OST_StructuralColumns,
                BuiltInCategory.OST_StructuralFraming,
                BuiltInCategory.OST_Stairs,
                BuiltInCategory.OST_Ramps
            ]
            
            for cat in categories:
                try:
                    col = FilteredElementCollector(self.doc).OfCategory(cat).WhereElementIsNotElementType().ToElements()
                    for el in col:
                        el_materials = el.GetMaterialIds(False)
                        for mat_id in el_materials:
                            mat_val = _eid_int(mat_id)
                            try:
                                vol = el.GetMaterialVolume(mat_id)
                                area = el.GetMaterialArea(mat_id)
                                material_volume[mat_val] = material_volume.get(mat_val, 0) + vol
                                material_area[mat_val] = material_area.get(mat_val, 0) + area
                            except: pass
                except: pass
            
            for mat_id_val, mat in material_map.items():
                vol = material_volume.get(mat_id_val, 0)
                area = material_area.get(mat_id_val, 0)
                if vol > 0 or area > 0:
                    vol_m3 = round(vol * 0.0283168, 3)
                    area_m2 = round(area * 0.092903, 2)
                    self.material_items.append(GridRow(
                        category=mat.MaterialCategory or "General",
                        name=mat.Name,
                        volume_str=str(vol_m3),
                        area_str=str(area_m2)
                    ))
            
            self.dg_special_materials.ItemsSource = to_items_source(sorted(self.material_items, key=lambda x: x.name))
            self.status_text.Text = "Material list generated successfully."
        except Exception as ex:
            print("Error loading materials: {}".format(ex))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass

    def on_materials_export(self, sender, e):
        items = self.dg_special_materials.ItemsSource
        if not items:
            forms.alert("No materials to export.", title="Material List")
            return
        
        filepath = forms.save_file(file_ext="csv", default_name="material_quantity_report.csv")
        if not filepath:
            return
            
        try:
            with _open_csv_write(filepath) as f:
                writer = csv.writer(f)
                writer.writerow(["Category", "Material Name", "Volume (m3)", "Area (m2)"])
                for item in items:
                    writer.writerow([
                        _csv_cell(item.get("category", "")),
                        _csv_cell(item.get("name", "")),
                        _csv_cell(item.get("volume_str", "")),
                        _csv_cell(item.get("area_str", ""))
                    ])
            forms.alert("Material list exported successfully.", title="Material List")
        except Exception as ex:
            forms.alert("Export failed:\n{}".format(ex))

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_dg_smart_purge_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua dg_smart_purge."""
        self.toggle_all_rows(self.dg_smart_purge, "is_selected", sender.IsChecked)

def show_model_auditor(script_dir, revit):
    ModelAuditorWindow(script_dir, revit).ShowDialog()
