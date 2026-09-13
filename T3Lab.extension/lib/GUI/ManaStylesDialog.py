# -*- coding: utf-8 -*-
"""Visual & Style Manager — consolidated event handling and logic."""

import os
import sys
import re
import random
import time
import json
import System
from collections import OrderedDict

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    Transaction,
    TransactionGroup,
    FilteredElementCollector,
    FillPatternElement,
    LinePatternElement,
    CurveElement,
    BuiltInParameter,
    GraphicsStyleType,
    ElementId,
    BuiltInCategory,
    TextNote,
    TextNoteType,
    FilledRegion,
    FilledRegionType,
    OverrideGraphicSettings,
    Color as RevitColor,
    FillPatternTarget,
    LinkElementId
)

from pyrevit import forms, revit
from GUI.WPF_Base import T3WPFWindow
from System.Windows import WindowState, Visibility, Thickness, CornerRadius, GridLength, GridUnitType, MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult, Rect, Point
from System.Windows.Media import SolidColorBrush, Color, DoubleCollection, DrawingBrush, GeometryDrawing, GeometryGroup, LineGeometry, Pen, TileMode, BrushMappingMode
from System.Windows.Controls import (
    Grid,
    RowDefinition,
    ColumnDefinition,
    Border,
    TextBlock,
    Button,
    ComboBox,
    ListBox,
    TextBox
)
from System.Collections.ObjectModel import ObservableCollection
from System import Object
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs

# Global references
doc = None
uidoc = None

def _eid_int(element_id):
    if not element_id:
        return -1
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue

def _get_invalid_element_id():
    return ElementId.InvalidElementId

def _make_double_collection(values):
    """Safely create a System.Windows.Media.DoubleCollection from an iterable of numbers in PythonNet."""
    if not values:
        return None
    try:
        dc = DoubleCollection()
        for v in values:
            dc.Add(float(v))
        try:
            dc.Freeze()
        except Exception:
            pass
        return dc
    except Exception:
        return None

# ============================================================================
# STYLE MANAGER WORKER ENTITIES
# ============================================================================

try:
    _Reactive = getattr(forms, 'Reactive', object)
except Exception:
    _Reactive = object


class FillPatternItem(_Reactive):
    def __init__(self, element):
        self._handlers = []
        self._element = element
        self._is_selected = False
        try:
            self._name = element.Name if element.Name else "Unnamed"
        except:
            self._name = "Unnamed"
        self._id = _eid_int(element.Id)
        
        try:
            fill_pattern = element.GetFillPattern()
            if fill_pattern:
                target = fill_pattern.Target
                self._pattern_type = "Drafting" if target == FillPatternTarget.Drafting else "Model"
                grids = fill_pattern.GetFillGrids()
                self._grid_count = len(grids) if grids else 0
                
                if not grids or len(grids) == 0:
                    self._settings = "Solid fill"
                elif len(grids) == 1:
                    self._settings = "Parallel lines"
                else:
                    self._settings = "Crosshatch" if len(grids) == 2 else "Custom"
            else:
                self._pattern_type = "Unknown"
                self._grid_count = 0
                self._settings = "N/A"
        except:
            self._pattern_type = "Unknown"
            self._grid_count = 0
            self._settings = "N/A"
            
        self._is_system = "Solid fill" in self._name or self._name.startswith("<") or self._name.startswith("Solid")

        # Construct wpf_brush
        self._wpf_brush = None
        is_solid = False
        try:
            fill_pattern = element.GetFillPattern()
            if fill_pattern:
                is_solid = fill_pattern.IsSolidFill
        except:
            pass
            
        if is_solid or "solid" in self._name.lower():
            self._wpf_brush = SolidColorBrush(Color.FromRgb(161, 161, 170)) # Zinc-400
            try:
                self._wpf_brush.Freeze()
            except Exception:
                pass
        else:
            try:
                db = DrawingBrush()
                db.TileMode = TileMode.Tile
                db.Viewport = Rect(0, 0, 10, 10)
                db.ViewportUnits = BrushMappingMode.Absolute
                
                group = GeometryGroup()
                pen = Pen(SolidColorBrush(Color.FromRgb(161, 161, 170)), 1)
                try:
                    pen.Freeze()
                except Exception:
                    pass
                
                if self._settings == "Parallel lines":
                    group.Children.Add(LineGeometry(Point(0, 5), Point(10, 5)))
                elif self._settings == "Crosshatch":
                    group.Children.Add(LineGeometry(Point(0, 5), Point(10, 5)))
                    group.Children.Add(LineGeometry(Point(5, 0), Point(5, 10)))
                else:
                    group.Children.Add(LineGeometry(Point(0, 0), Point(10, 10)))
                    
                db.Drawing = GeometryDrawing(None, pen, group)
                try:
                    db.Freeze()
                except Exception:
                    pass
                self._wpf_brush = db
            except Exception:
                self._wpf_brush = SolidColorBrush(Color.FromRgb(228, 228, 231))
                try:
                    self._wpf_brush.Freeze()
                except Exception:
                    pass

    @property
    def wpf_brush(self): return self._wpf_brush

    @property
    def element(self): return self._element
    @property
    def id(self): return self._id
    @property
    def name(self): return self._name
    @name.setter
    def name(self, v):
        if self._name != v:
            self._name = v
            self._notify("name")
            
    @property
    def pattern_type(self): return self._pattern_type
    @property
    def grid_count(self): return self._grid_count
    @property
    def settings(self): return self._settings
    @property
    def is_system(self): return self._is_system
    
    @property
    def is_selected(self): return self._is_selected
    @is_selected.setter
    def is_selected(self, v):
        if self._is_selected != v:
            self._is_selected = v
            self._notify("is_selected")

    def add_PropertyChanged(self, h): self._handlers.append(h)
    def remove_PropertyChanged(self, h):
        if h in self._handlers: self._handlers.remove(h)
    def _notify(self, prop):
        for h in self._handlers: h(self, PropertyChangedEventArgs(prop))


class LineStyleItem(_Reactive):
    def __init__(self, category):
        self._handlers = []
        self._category = category
        self._is_selected = False
        try:
            self._name = category.Name if category.Name else "Unnamed"
        except Exception:
            self._name = "Unnamed"
        self._id = _eid_int(category.Id)
        
        c_red = 24
        c_green = 24
        c_blue = 27
        has_color = False
        try:
            color = category.LineColor
            if color and (not hasattr(color, 'IsValid') or color.IsValid):
                c_red = color.Red
                c_green = color.Green
                c_blue = color.Blue
                self._color = "RGB({},{},{})".format(c_red, c_green, c_blue)
                has_color = True
            else:
                self._color = "N/A"
        except Exception:
            self._color = "N/A"
            
        try:
            weight = category.GetLineWeight(GraphicsStyleType.Projection)
            self._weight = str(weight) if weight else "N/A"
        except Exception:
            self._weight = "N/A"
            
        try:
            pattern_id = category.GetLinePatternId(GraphicsStyleType.Projection)
            if pattern_id and _eid_int(pattern_id) != _eid_int(_get_invalid_element_id()):
                pat = doc.GetElement(pattern_id)
                self._pattern = pat.Name if pat else "Solid"
            else:
                self._pattern = "Solid"
        except Exception:
            self._pattern = "Solid"
            
        self._is_system = self._name.startswith('<') and self._name.endswith('>')
        self._usage_count = 0

        # Construct color_hex & wpf_brush
        if has_color:
            if c_red > 240 and c_green > 240 and c_blue > 240:
                self._color_hex = "#A1A1AA"
            else:
                self._color_hex = "#{:02X}{:02X}{:02X}".format(c_red, c_green, c_blue)
        else:
            self._color_hex = "#18181B"

        try:
            r = int(self._color_hex[1:3], 16)
            g = int(self._color_hex[3:5], 16)
            b = int(self._color_hex[5:7], 16)
            brush = SolidColorBrush(Color.FromRgb(r, g, b))
            try:
                brush.Freeze()
            except Exception:
                pass
            self._wpf_brush = brush
        except Exception:
            self._wpf_brush = None
            
        # Construct thickness_val
        try:
            w = float(self._weight)
            self._thickness_val = max(1.0, min(8.0, 0.75 * w + 0.5))
        except Exception:
            self._thickness_val = 1.5
            
        # Construct dash_array & dash_str
        self._dash_array = None
        self._dash_str = ""
        try:
            pattern_id = category.GetLinePatternId(GraphicsStyleType.Projection)
            if pattern_id and _eid_int(pattern_id) != _eid_int(_get_invalid_element_id()):
                pat = doc.GetElement(pattern_id)
                if pat:
                    lp = pat.GetLinePattern()
                    if lp:
                        segs = lp.GetSegments()
                        if segs:
                            dash_list = []
                            for seg in segs:
                                val = float(seg.Length * 304.8) # mm
                                val = max(1.0, round(val * 1.5, 2))
                                dash_list.append(val)
                            if len(dash_list) % 2 == 1:
                                dash_list.append(2.0)
                            self._dash_array = _make_double_collection(dash_list)
                            self._dash_str = " ".join(str(v) for v in dash_list)
        except Exception:
            pass
            
        if not self._dash_str and self._pattern != "Solid":
            p = self._pattern.lower()
            vals = None
            if "dash dot dot" in p:
                vals = [6.0, 3.0, 1.5, 3.0, 1.5, 3.0]
            elif "dash dot" in p:
                vals = [6.0, 3.0, 1.5, 3.0]
            elif "dash" in p:
                vals = [6.0, 3.0]
            elif "dot" in p:
                vals = [1.5, 3.0]
            if vals:
                self._dash_array = _make_double_collection(vals)
                self._dash_str = " ".join(str(v) for v in vals)

    @property
    def color_hex(self): return self._color_hex
    @property
    def dash_str(self): return self._dash_str
    @property
    def wpf_brush(self): return self._wpf_brush
    @property
    def thickness_val(self): return self._thickness_val
    @property
    def dash_array(self): return self._dash_array

    @property
    def category(self): return self._category
    @property
    def id(self): return self._id
    @property
    def name(self): return self._name
    @name.setter
    def name(self, v):
        if self._name != v:
            self._name = v
            self._notify("name")
            
    @property
    def color(self): return self._color
    @property
    def weight(self): return self._weight
    @property
    def pattern(self): return self._pattern
    @property
    def is_system(self): return self._is_system
    
    @property
    def usage_count(self): return self._usage_count
    @usage_count.setter
    def usage_count(self, v):
        if self._usage_count != v:
            self._usage_count = v
            self._notify("usage_count")
            
    @property
    def is_selected(self): return self._is_selected
    @is_selected.setter
    def is_selected(self, v):
        if self._is_selected != v:
            self._is_selected = v
            self._notify("is_selected")

    def add_PropertyChanged(self, h): self._handlers.append(h)
    def remove_PropertyChanged(self, h):
        if h in self._handlers: self._handlers.remove(h)
    def _notify(self, prop):
        for h in self._handlers: h(self, PropertyChangedEventArgs(prop))


class LinePatternItem(_Reactive):
    SYSTEM_PATTERNS = ["Solid", "Dash", "Dot", "Dash dot", "Dash dot dot"]
    
    def __init__(self, element):
        self._handlers = []
        self._element = element
        self._is_selected = False
        try:
            self._name = element.Name if element.Name else "Unnamed"
        except Exception:
            self._name = "Unnamed"
        self._id = _eid_int(element.Id)
        
        try:
            line_pattern = element.GetLinePattern()
            if line_pattern:
                segments = line_pattern.GetSegments()
                self._segment_count = len(list(segments)) if segments else 0
                
                types = []
                values = []
                for seg in segments:
                    types.append(seg.Type.ToString())
                    values.append("{:.2f}mm".format(seg.Length * 304.8))
                self._segments_type = ", ".join(types) if types else "Solid"
                self._segments_value = ", ".join(values) if values else "-"
            else:
                self._segment_count = 0
                self._segments_type = "Solid"
                self._segments_value = "-"
        except Exception:
            self._segment_count = 0
            self._segments_type = "Solid"
            self._segments_value = "-"
            
        self._is_system = self._name in self.SYSTEM_PATTERNS

        # Construct color_hex, wpf_brush, thickness_val & dash_array / dash_str
        self._color_hex = "#18181B"
        try:
            brush = SolidColorBrush(Color.FromRgb(24, 24, 27))
            try:
                brush.Freeze()
            except Exception:
                pass
            self._wpf_brush = brush
        except Exception:
            self._wpf_brush = None

        self._thickness_val = 2.0
        self._dash_array = None
        self._dash_str = ""
        if element:
            try:
                line_pattern = element.GetLinePattern()
                if line_pattern:
                    segments = line_pattern.GetSegments()
                    if segments:
                        dash_list = []
                        for seg in segments:
                            val = float(seg.Length * 304.8) # mm
                            val = max(1.0, round(val * 1.5, 2))
                            dash_list.append(val)
                        if len(dash_list) % 2 == 1:
                            dash_list.append(2.0)
                        self._dash_array = _make_double_collection(dash_list)
                        self._dash_str = " ".join(str(v) for v in dash_list)
            except Exception:
                pass

        if not self._dash_str and self._name != "Solid":
            p = self._name.lower()
            vals = None
            if "dash dot dot" in p:
                vals = [6.0, 3.0, 1.5, 3.0, 1.5, 3.0]
            elif "dash dot" in p:
                vals = [6.0, 3.0, 1.5, 3.0]
            elif "dash" in p:
                vals = [6.0, 3.0]
            elif "dot" in p:
                vals = [1.5, 3.0]
            if vals:
                self._dash_array = _make_double_collection(vals)
                self._dash_str = " ".join(str(v) for v in vals)

    @property
    def color_hex(self): return self._color_hex
    @property
    def thickness_val(self): return self._thickness_val
    @property
    def dash_str(self): return self._dash_str
    @property
    def wpf_brush(self): return self._wpf_brush
    @property
    def dash_array(self): return self._dash_array

    @property
    def element(self): return self._element
    @property
    def id(self): return self._id
    @property
    def name(self): return self._name
    @name.setter
    def name(self, v):
        if self._name != v:
            self._name = v
            self._notify("name")
            
    @property
    def segment_count(self): return self._segment_count
    @property
    def segments_type(self): return self._segments_type
    @property
    def segments_value(self): return self._segments_value
    @property
    def is_system(self): return self._is_system
    
    @property
    def is_selected(self): return self._is_selected
    @is_selected.setter
    def is_selected(self, v):
        if self._is_selected != v:
            self._is_selected = v
            self._notify("is_selected")

    def add_PropertyChanged(self, h): self._handlers.append(h)
    def remove_PropertyChanged(self, h):
        if h in self._handlers: self._handlers.remove(h)
    def _notify(self, prop):
        for h in self._handlers: h(self, PropertyChangedEventArgs(prop))


# ============================================================================
# COLOR SPLASHER UTILITIES
# ============================================================================

SPECIAL_PARAMS = [
    "Family and Type",
    "Type Name (System)",
    "Family Name (System)",
    "Category Name",
    "Level Name",
]

def get_link_instances():
    try:
        return list(FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance).ToElements())
    except:
        return []

def collect_categories():
    cats = {}
    active_view = doc.ActiveView
    SKIP_NAMES = {
        'Views', 'Sheets', 'Schedules', 'Schedule Graphics', 'Viewports',
        'Scope Boxes', 'Matchline', 'Reference Planes', 'Grids', 'Levels',
        'Section Boxes', 'Cameras', 'Title Blocks', 'Revision Clouds',
        'Detail Items', 'Lines', 'Text Notes', 'Dimensions', 'Tags',
        'Keynote Tags', 'Multi-Category Tags', 'Generic Annotations',
        'Spot Elevations', 'Spot Coordinates', 'Spot Slopes',
        'Curtain Grid Lines', 'Curtain Grid Mullions',
        'Area Tags', 'Room Tags', 'Space Tags',
        'Project Information', 'Project Base Point', 'Survey Point',
        'Mass', 'Mass Floor', 'Analytical Links', 'Analytical Nodes',
    }
    
    try:
        for elem in FilteredElementCollector(doc, active_view.Id).WhereElementIsNotElementType():
            try:
                cat = elem.Category
                if cat is None: continue
                cname = cat.Name
                if not cname or cname.startswith('<') or cname.startswith('_') or cname in SKIP_NAMES:
                    continue
                try:
                    if cat.CategoryType != DB.CategoryType.Model: continue
                except:
                    pass
                if cname not in cats:
                    cats[cname] = cat
            except:
                continue
    except:
        pass
        
    if not cats:
        try:
            for elem in FilteredElementCollector(doc).WhereElementIsNotElementType():
                try:
                    cat = elem.Category
                    if cat is None: continue
                    cname = cat.Name
                    if not cname or cname.startswith('<') or cname.startswith('_') or cname in SKIP_NAMES:
                        continue
                    try:
                        if cat.CategoryType != DB.CategoryType.Model: continue
                    except:
                        pass
                    if cname not in cats:
                        cats[cname] = cat
                except:
                    continue
        except:
            pass
    return sorted(cats.items(), key=lambda x: x[0])


def collect_categories_from_doc(source_doc):
    cats = {}
    SKIP_NAMES = {
        'Views', 'Sheets', 'Schedules', 'Schedule Graphics', 'Viewports',
        'Scope Boxes', 'Matchline', 'Reference Planes', 'Grids', 'Levels',
        'Section Boxes', 'Cameras', 'Title Blocks', 'Revision Clouds',
        'Detail Items', 'Lines', 'Text Notes', 'Dimensions', 'Tags',
        'Keynote Tags', 'Multi-Category Tags', 'Generic Annotations',
        'Spot Elevations', 'Spot Coordinates', 'Spot Slopes',
        'Curtain Grid Lines', 'Curtain Grid Mullions',
        'Area Tags', 'Room Tags', 'Space Tags',
        'Project Information', 'Project Base Point', 'Survey Point',
        'Mass', 'Mass Floor', 'Analytical Links', 'Analytical Nodes',
    }
    try:
        col = FilteredElementCollector(source_doc).WhereElementIsNotElementType()
        for elem in col:
            try:
                cat = elem.Category
                if cat is None: continue
                cname = cat.Name
                if not cname or cname.startswith('<') or cname.startswith('_') or cname in SKIP_NAMES:
                    continue
                try:
                    if cat.CategoryType != DB.CategoryType.Model: continue
                except:
                    pass
                if cname not in cats:
                    cats[cname] = cat
            except:
                continue
    except:
        pass
    return sorted(cats.items(), key=lambda x: x[0])


def collect_elements(category):
    active_view = doc.ActiveView
    target = category.Name
    try:
        cid = _eid_int(category.Id)
        if cid > 0:
            bic = System.Enum.ToObject(DB.BuiltInCategory, cid)
            elems = list(FilteredElementCollector(doc, active_view.Id)
                         .OfCategory(bic).WhereElementIsNotElementType().ToElements())
            if elems: return elems
    except:
        pass

    results = []
    try:
        for elem in FilteredElementCollector(doc, active_view.Id).WhereElementIsNotElementType():
            try:
                if elem.Category and elem.Category.Name == target:
                    results.append(elem)
            except:
                continue
    except:
        pass
    if results:
        return results

    # active_view doesn't support a view-scoped collector (schedule, sheet,
    # legend, rendering) — fall back to a doc-wide scan, same as collect_categories().
    try:
        for elem in FilteredElementCollector(doc).WhereElementIsNotElementType():
            try:
                if elem.Category and elem.Category.Name == target:
                    results.append(elem)
            except:
                continue
    except:
        pass
    return results


def collect_elements_from_doc(source_doc, category_name):
    results = []
    try:
        for elem in FilteredElementCollector(source_doc).WhereElementIsNotElementType():
            try:
                if elem.Category and elem.Category.Name == category_name:
                    results.append(elem)
            except:
                continue
    except:
        pass
    return results


def get_special_value(elem, param_name):
    try:
        if param_name == "Family and Type":
            p = elem.get_Parameter(BuiltInParameter.ELEM_FAMILY_AND_TYPE_PARAM)
            if p and p.HasValue:
                return p.AsValueString() or "<Empty>"
            tid = elem.GetTypeId()
            if tid:
                etype = doc.GetElement(tid)
                if etype:
                    fname = ""
                    tname = etype.Name or ""
                    fp = etype.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                    if fp and fp.HasValue:
                        fname = fp.AsString() or ""
                    if not fname:
                        fp = etype.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
                        if fp and fp.HasValue:
                            fname = fp.AsString() or ""
                    if fname:
                        return "{} : {}".format(fname, tname)
                    return tname
            return "<No Type>"
            
        elif param_name == "Type Name (System)":
            tid = elem.GetTypeId()
            if tid:
                etype = doc.GetElement(tid)
                if etype: return etype.Name or "<Unnamed>"
            return "<No Type>"
            
        elif param_name == "Family Name (System)":
            tid = elem.GetTypeId()
            if tid:
                etype = doc.GetElement(tid)
                if etype:
                    fp = etype.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                    if fp and fp.HasValue:
                        return fp.AsString() or "<Unknown>"
                    fp = etype.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
                    if fp and fp.HasValue:
                        return fp.AsString() or "<Unknown>"
            return "<No Family>"
            
        elif param_name == "Category Name":
            if elem.Category: return elem.Category.Name
            return "<No Category>"
            
        elif param_name == "Level Name":
            lp = elem.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
            if lp and lp.HasValue:
                lev = doc.GetElement(lp.AsElementId())
                if lev: return lev.Name
            lp = elem.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
            if lp and lp.HasValue:
                lev = doc.GetElement(lp.AsElementId())
                if lev: return lev.Name
            lp = elem.get_Parameter(BuiltInParameter.SCHEDULE_LEVEL_PARAM)
            if lp and lp.HasValue:
                lev = doc.GetElement(lp.AsElementId())
                if lev: return lev.Name
            return "<No Level>"
    except:
        pass
    return "<Error>"


def get_special_value_linked(elem, param_name, source_doc):
    try:
        if param_name == "Family and Type":
            p = elem.get_Parameter(BuiltInParameter.ELEM_FAMILY_AND_TYPE_PARAM)
            if p and p.HasValue:
                return p.AsValueString() or "<Empty>"
            tid = elem.GetTypeId()
            if tid:
                etype = source_doc.GetElement(tid)
                if etype:
                    fname = ""
                    tname = etype.Name or ""
                    fp = etype.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                    if fp and fp.HasValue:
                        fname = fp.AsString() or ""
                    if fname:
                        return "{} : {}".format(fname, tname)
                    return tname
            return "<No Type>"
        elif param_name == "Type Name (System)":
            tid = elem.GetTypeId()
            if tid:
                etype = source_doc.GetElement(tid)
                if etype: return etype.Name or "<Unnamed>"
            return "<No Type>"
        elif param_name == "Family Name (System)":
            tid = elem.GetTypeId()
            if tid:
                etype = source_doc.GetElement(tid)
                if etype:
                    fp = etype.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                    if fp and fp.HasValue:
                        return fp.AsString() or "<Unknown>"
            return "<No Family>"
        elif param_name == "Category Name":
            if elem.Category: return elem.Category.Name
            return "<No Category>"
        elif param_name == "Level Name":
            for bip in [BuiltInParameter.WALL_BASE_CONSTRAINT,
                        BuiltInParameter.FAMILY_LEVEL_PARAM,
                        BuiltInParameter.SCHEDULE_LEVEL_PARAM]:
                lp = elem.get_Parameter(bip)
                if lp and lp.HasValue:
                    lev = source_doc.GetElement(lp.AsElementId())
                    if lev: return lev.Name
            return "<No Level>"
    except:
        pass
    return "<Error>"


def get_param_value(elem, param_name):
    if param_name in SPECIAL_PARAMS:
        return get_special_value(elem, param_name)
        
    def _read(param):
        if param is None or not param.HasValue:
            return None
        st = param.StorageType
        if st == DB.StorageType.String:
            v = param.AsString()
            return v if v else "<Empty>"
        elif st == DB.StorageType.Double:
            return param.AsValueString() or str(round(param.AsDouble(), 4))
        elif st == DB.StorageType.Integer:
            return param.AsValueString() or str(param.AsInteger())
        elif st == DB.StorageType.ElementId:
            eid = param.AsElementId()
            ev = _eid_int(eid)
            if ev > 0:
                el = doc.GetElement(eid)
                if el:
                    try:
                        return el.Name
                    except:
                        return str(ev)
            return param.AsValueString() or "<None>"
        return None
        
    try:
        val = _read(elem.LookupParameter(param_name))
        if val is not None: return val
    except:
        pass
        
    try:
        tid = elem.GetTypeId()
        if tid:
            etype = doc.GetElement(tid)
            if etype:
                val = _read(etype.LookupParameter(param_name))
                if val is not None: return val
    except:
        pass
    return "<No Value>"


def get_param_value_linked(elem, param_name, source_doc):
    if param_name in SPECIAL_PARAMS:
        return get_special_value_linked(elem, param_name, source_doc)
        
    def _read(param):
        if param is None or not param.HasValue:
            return None
        st = param.StorageType
        if st == DB.StorageType.String:
            v = param.AsString()
            return v if v else "<Empty>"
        elif st == DB.StorageType.Double:
            return param.AsValueString() or str(round(param.AsDouble(), 4))
        elif st == DB.StorageType.Integer:
            return param.AsValueString() or str(param.AsInteger())
        elif st == DB.StorageType.ElementId:
            eid = param.AsElementId()
            ev = _eid_int(eid)
            if ev > 0:
                el = source_doc.GetElement(eid)
                if el:
                    try:
                        return el.Name
                    except:
                        return str(ev)
            return param.AsValueString() or "<None>"
        return None
        
    try:
        val = _read(elem.LookupParameter(param_name))
        if val is not None: return val
    except:
        pass
        
    try:
        tid = elem.GetTypeId()
        if tid:
            etype = source_doc.GetElement(tid)
            if etype:
                val = _read(etype.LookupParameter(param_name))
                if val is not None: return val
    except:
        pass
    return "<No Value>"


def get_solid_fill():
    try:
        for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
            try:
                if fp.GetFillPattern().IsSolidFill: return fp
            except:
                continue
    except:
        pass
    return None


VIEW_TYPES_WITHOUT_OVERRIDES = [
    DB.ViewType.Schedule, DB.ViewType.DrawingSheet,
    DB.ViewType.Legend, DB.ViewType.Rendering,
]


def _active_view_blocks_overrides():
    """True if the active view can't take SetElementOverrides/AddFilter calls
    (schedules, sheets, legends, renderings aren't graphical model views)."""
    view = doc.ActiveView
    return view is None or view.ViewType in VIEW_TYPES_WITHOUT_OVERRIDES


def _build_override(val, color_map, solid):
    """OverrideGraphicSettings for one parameter value, or None if val has no color."""
    if val not in color_map:
        return None
    r, g, b = color_map[val]
    color = RevitColor(r, g, b)
    ogs = OverrideGraphicSettings()

    try:
        ogs.SetSurfaceForegroundPatternColor(color)
        ogs.SetSurfaceForegroundPatternVisible(True)
        if solid: ogs.SetSurfaceForegroundPatternId(solid.Id)
    except:
        try:
            ogs.SetProjectionFillColor(color)
            ogs.SetProjectionFillPatternVisible(True)
            if solid: ogs.SetProjectionFillPatternId(solid.Id)
        except:
            pass
    try:
        ogs.SetCutForegroundPatternColor(color)
        ogs.SetCutForegroundPatternVisible(True)
        if solid: ogs.SetCutForegroundPatternId(solid.Id)
    except:
        try:
            ogs.SetCutFillColor(color)
            ogs.SetCutFillPatternVisible(True)
        except:
            pass
    return ogs


def apply_overrides(elements_by_value, color_map):
    active_view = doc.ActiveView
    t = Transaction(doc, "T3Lab - Color Splasher")
    t.Start()
    count = 0
    solid = get_solid_fill()
    try:
        for val, elems in elements_by_value.items():
            ogs = _build_override(val, color_map, solid)
            if ogs is None: continue
            for elem in elems:
                try:
                    active_view.SetElementOverrides(elem.Id, ogs)
                    count += 1
                except:
                    continue
        t.Commit()
    except Exception as ex:
        if t.HasStarted(): t.RollBack()
        print("Error applying overrides: {}".format(ex))
    return count


def apply_overrides_linked(elements_by_value, color_map, link_instance):
    """Same as apply_overrides, but elems live in a linked document — overrides
    on a linked element are set on the HOST view via a LinkElementId pairing
    the host RevitLinkInstance with the element's id inside the linked doc."""
    active_view = doc.ActiveView
    t = Transaction(doc, "T3Lab - Color Splasher (Linked)")
    t.Start()
    count = 0
    solid = get_solid_fill()
    try:
        for val, elems in elements_by_value.items():
            ogs = _build_override(val, color_map, solid)
            if ogs is None: continue
            for elem in elems:
                try:
                    link_eid = LinkElementId(link_instance.Id, elem.Id)
                    active_view.SetElementOverrides(link_eid, ogs)
                    count += 1
                except:
                    continue
        t.Commit()
    except Exception as ex:
        if t.HasStarted(): t.RollBack()
        print("Error applying linked overrides: {}".format(ex))
    return count


def clear_overrides():
    active_view = doc.ActiveView
    t = Transaction(doc, "T3Lab - Clear Color Overrides")
    t.Start()
    count = 0
    try:
        blank = OverrideGraphicSettings()
        for elem in FilteredElementCollector(doc, active_view.Id).WhereElementIsNotElementType():
            try:
                active_view.SetElementOverrides(elem.Id, blank)
                count += 1
            except:
                continue
        t.Commit()
    except Exception as ex:
        if t.HasStarted(): t.RollBack()
        print("Error clearing overrides: {}".format(ex))
    return count


def generate_gradient(n):
    colors = []
    for i in range(n):
        t = float(i) / max(n - 1, 1)
        if t < 0.5:
            r = int(0 + (0) * (t * 2))
            g = int(0 + (200) * (t * 2))
            b = int(200 + (-200) * (t * 2))
        else:
            t2 = (t - 0.5) * 2
            r = int(220 * t2)
            g = int(200 * (1 - t2))
            b = 0
        colors.append((max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))))
    return colors


def generate_random(n):
    colors = []
    random.seed(42)
    for _ in range(n):
        r = random.randint(30, 220)
        g = random.randint(30, 220)
        b = random.randint(30, 220)
        colors.append((r, g, b))
    return colors


# ============================================================================
# MAIN WINDOW CLASS
# ============================================================================

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'ManaStyles.xaml')

class ManaStylesWindow(T3WPFWindow):
    def __init__(self, script_dir, revit):
        T3WPFWindow.__init__(self, _XAML)
        self._script_dir = script_dir
        self._revit = revit
        
        global doc, uidoc
        uidoc = revit.ActiveUIDocument
        doc = uidoc.Document
        
        # Style Manager Caches
        self.fill_patterns = []
        self.line_styles = []
        self.line_patterns = []
        self.filtered_fill_patterns = ObservableCollection[Object]()
        self.filtered_line_styles = ObservableCollection[Object]()
        self.filtered_line_patterns = ObservableCollection[Object]()
        
        # Color Splasher Caches
        self.categories = []
        self.elements_by_value = OrderedDict()
        self.color_map = {}
        self.sorted_values = []
        self.selected_link = None
        self.selected_link_doc = None
        self.param_names = []
        self.link_instances = []
        
        # Sidebar nav is wired via Click="on_sidebar_clicked" in XAML
        self.main_tab_control.SelectionChanged += self._on_main_tab_changed
        self._go_to_main_tab(0)
        
        self.btn_sub_line_styles.Checked += self._on_style_sub_tab_changed
        self.btn_sub_line_patterns.Checked += self._on_style_sub_tab_changed
        self.btn_sub_fill_patterns.Checked += self._on_style_sub_tab_changed

        # Force initial sub-tab content to render: btn_sub_line_styles.IsChecked was
        # already True when the XAML was parsed, so its Checked event fired before
        # the += wiring above and style_tab_control.SelectedIndex was never
        # explicitly set (same fix as ManaSheets/ManaViews/ManaAnno/ManaPara/ManaContains).
        self.style_tab_control.SelectedIndex = 0

        # Bind Style Manager UI actions
        self.txt_search_style.TextChanged += self._on_style_filter_changed
        self.btn_style_calc_usage.Click += self._on_style_calc_usage
        self.btn_style_refresh.Click += self._on_style_refresh
        self.btn_style_select_all.Click += self._on_style_select_all
        self.btn_style_select_custom.Click += self._on_style_select_custom
        self.btn_style_clear_all.Click += self._on_style_clear_all
        self.btn_style_rename.Click += self._on_style_rename
        self.btn_style_delete.Click += self._on_style_delete
        
        self.txt_search_pattern.TextChanged += self._on_pattern_filter_changed
        self.btn_pattern_refresh.Click += self._on_pattern_refresh
        self.btn_pattern_select_all.Click += self._on_pattern_select_all
        self.btn_pattern_select_custom.Click += self._on_pattern_select_custom
        self.btn_pattern_clear_all.Click += self._on_pattern_clear_all
        self.btn_pattern_rename.Click += self._on_pattern_rename
        self.btn_pattern_duplicate.Click += self._on_pattern_duplicate
        self.btn_pattern_delete.Click += self._on_pattern_delete
        
        self.txt_search_fill.TextChanged += self._on_fill_filter_changed
        self.cmb_type_fill.SelectionChanged += self._on_fill_filter_changed
        self.btn_fill_refresh.Click += self._on_fill_refresh
        self.btn_fill_select_all.Click += self._on_fill_select_all
        self.btn_fill_select_custom.Click += self._on_fill_select_custom
        self.btn_fill_clear_all.Click += self._on_fill_clear_all
        self.btn_fill_rename.Click += self._on_fill_rename
        self.btn_fill_duplicate.Click += self._on_fill_duplicate
        self.btn_fill_delete.Click += self._on_fill_delete
        
        # Bind Color Splasher UI actions
        self.cbSource.SelectionChanged += self._on_source_changed
        self.cbCat.SelectionChanged += self._on_cat_changed
        self.txtSearch.TextChanged += self._on_param_search_changed
        self.lbParams.SelectionChanged += self._on_param_selection_changed
        self.btnGradient.Click += self._on_gradient_clicked
        self.btnRandom.Click += self._on_random_clicked
        self.btnLegend.Click += self._on_legend_clicked
        self.btnFilters.Click += self._on_filters_clicked
        self.btnReset.Click += self._on_reset_clicked
        self.btnApply.Click += self._on_apply_clicked
        
        # Chrome actions
        self.btn_minimize.Click += self._minimize
        self.btn_maximize.Click += self._maximize
        self.btn_close_chrome.Click += self._close_chrome
        
        # Load style data and initialize splasher
        self._load_style_manager_data()
        self._init_color_splasher()
        self._init_ai_mode()

    def _init_ai_mode(self):
        try:
            if hasattr(self, 'is_ai_mode_active') and self.is_ai_mode_active():
                if hasattr(self, 'ai_mode_badge') and self.ai_mode_badge:
                    self.ai_mode_badge.Visibility = Visibility.Visible
                if hasattr(self, 'txt_ai_status') and self.txt_ai_status:
                    info = self.get_ai_status_info()
                    self.txt_ai_status.Text = "AI Mode: {}".format(info.get('model', 'Ready'))
        except Exception:
            pass

    # ========================================================================
    # MAIN WINDOW NAVIGATION & CHROME CONTROLS
    # ========================================================================
    
    _NAV_STATUS = [
        "Style Manager — Rename and manage Line Styles, Patterns, and Fill Patterns",
        "Color Splasher — Dynamic parameter-based element color override",
    ]

    _SIDEBAR_MAP = {'btn_tab_styles': 0, 'btn_tab_splasher': 1}

    def on_sidebar_clicked(self, sender, e):
        idx = self._SIDEBAR_MAP.get(sender.Name, -1)
        if idx >= 0:
            self._go_to_main_tab(idx)

    def _on_main_tab_changed(self, sender, e):
        """Sync sidebar toggle buttons when tab changes via keyboard/programmatic switch."""
        try:
            from System.Windows.Controls import TabControl as _TC
            if not isinstance(e.Source, _TC):
                return
            idx = self.main_tab_control.SelectedIndex
            self.btn_tab_styles.IsChecked    = (idx == 0)
            self.btn_tab_splasher.IsChecked  = (idx == 1)
            self.status_text.Text = self._NAV_STATUS[idx]
        except Exception:
            pass

    def _go_to_main_tab(self, index):
        self.main_tab_control.SelectedIndex = index
        self.btn_tab_styles.IsChecked    = (index == 0)
        self.btn_tab_splasher.IsChecked  = (index == 1)
        self.status_text.Text = self._NAV_STATUS[index]
            
    def _on_style_sub_tab_changed(self, sender, e):
        if sender == self.btn_sub_line_styles:
            self.style_tab_control.SelectedIndex = 0
        elif sender == self.btn_sub_line_patterns:
            self.style_tab_control.SelectedIndex = 1
        elif sender == self.btn_sub_fill_patterns:
            self.style_tab_control.SelectedIndex = 2

    def _minimize(self, sender, e):
        self.WindowState = WindowState.Minimized

    def _maximize(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def _close_chrome(self, sender, e):
        self.Close()

    # ========================================================================
    # STYLE MANAGER LOADING & FILTERING
    # ========================================================================
    
    def _load_style_manager_data(self):
        self._load_fill_patterns()
        self._load_line_styles()
        self._load_line_patterns()

    def _load_fill_patterns(self):
        try:
            col = FilteredElementCollector(doc).OfClass(FillPatternElement)
            self.fill_patterns = [FillPatternItem(e) for e in col]
            self.fill_patterns.sort(key=lambda x: x.name)
            self._filter_fill_patterns()
        except Exception as ex:
            print("Error loading fill patterns: {}".format(str(ex)))

    def _filter_fill_patterns(self):
        query = self.txt_search_fill.Text.lower() if self.txt_search_fill.Text else ""
        type_filter = "All"
        if self.cmb_type_fill.SelectedItem:
            if hasattr(self.cmb_type_fill.SelectedItem, "Content"):
                type_filter = str(self.cmb_type_fill.SelectedItem.Content)
            else:
                type_filter = str(self.cmb_type_fill.SelectedItem)
                
        self.filtered_fill_patterns.Clear()
        for item in self.fill_patterns:
            if query and query not in item.name.lower():
                continue
            if type_filter == "Drafting" and item.pattern_type != "Drafting":
                continue
            if type_filter == "Model" and item.pattern_type != "Model":
                continue
            self.filtered_fill_patterns.Add(item)
            
        self.grid_fill.ItemsSource = self.filtered_fill_patterns
        self.txt_stats_fill.Text = "Showing {} / {} items".format(len(self.filtered_fill_patterns), len(self.fill_patterns))

    def _load_line_styles(self):
        try:
            lines_category = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
            self.line_styles = []
            if lines_category and lines_category.SubCategories:
                for subcat in lines_category.SubCategories:
                    self.line_styles.append(LineStyleItem(subcat))
            self.line_styles.sort(key=lambda x: x.name)
            self._filter_line_styles()
        except Exception as ex:
            print("Error loading line styles: {}".format(str(ex)))

    def _filter_line_styles(self):
        query = self.txt_search_style.Text.lower() if self.txt_search_style.Text else ""
        self.filtered_line_styles.Clear()
        for item in self.line_styles:
            if query and query not in item.name.lower():
                continue
            self.filtered_line_styles.Add(item)
            
        self.grid_style.ItemsSource = self.filtered_line_styles
        self.txt_stats_style.Text = "Showing {} / {} items".format(len(self.filtered_line_styles), len(self.line_styles))

    def _load_line_patterns(self):
        try:
            col = FilteredElementCollector(doc).OfClass(LinePatternElement)
            self.line_patterns = [LinePatternItem(e) for e in col]
            self.line_patterns.sort(key=lambda x: x.name)
            self._filter_line_patterns()
        except Exception as ex:
            print("Error loading line patterns: {}".format(str(ex)))

    def _filter_line_patterns(self):
        query = self.txt_search_pattern.Text.lower() if self.txt_search_pattern.Text else ""
        self.filtered_line_patterns.Clear()
        for item in self.line_patterns:
            if query and query not in item.name.lower():
                continue
            self.filtered_line_patterns.Add(item)
            
        self.grid_pattern.ItemsSource = self.filtered_line_patterns
        self.txt_stats_pattern.Text = "Showing {} / {} items".format(len(self.filtered_line_patterns), len(self.line_patterns))

    def _on_fill_filter_changed(self, s, e): self._filter_fill_patterns()
    def _on_style_filter_changed(self, s, e): self._filter_line_styles()
    def _on_pattern_filter_changed(self, s, e): self._filter_line_patterns()

    # ========================================================================
    # ACTIONS HANDLERS (LINE STYLES)
    # ========================================================================
    def _on_style_select_all(self, s, e):
        for item in self.filtered_line_styles: item.is_selected = True
    def _on_style_clear_all(self, s, e):
        for item in self.line_styles: item.is_selected = False
    def _on_style_select_custom(self, s, e):
        for item in self.filtered_line_styles: item.is_selected = not item.is_system

    def _on_style_refresh(self, s, e):
        self._load_line_styles()

    def ai_clean_styles_clicked(self, sender, e):
        """AI Clean: Detect CAD junk line styles ($0$*) and suggest consolidation to standard line styles."""
        btn = getattr(self, 'btn_ai_clean_styles', None)
        orig_content = "✨ AI Clean CAD Styles"

        def _restore_btn():
            if btn:
                btn.Content = orig_content
                btn.IsEnabled = True

        try:
            cad_patterns = [r'^\$0\$', r'^A-', r'^C-', r'^S-', r'^M-', r'^E-', r'^P-', r'^I-', r'^DEFPOINTS', r'^0_']
            cad_items = []
            for item in self.line_styles:
                if getattr(item, 'is_system', False):
                    continue
                name = getattr(item, 'name', '')
                if any(re.search(pat, name, re.IGNORECASE) for pat in cad_patterns):
                    cad_items.append(item)

            if not cad_items:
                forms.alert(
                    "No imported CAD line styles detected (patterns: $0$*, A-*, DEFPOINTS, etc.).",
                    title="AI Clean Styles"
                )
                return

            def _apply_classic():
                for item in cad_items:
                    item.is_selected = True
                self._filter_line_styles()
                forms.alert(
                    "Detected {} CAD line styles (e.g. $0$, DEFPOINTS, CAD layer prefixes).\n\n"
                    "They have been selected in the DataGrid for batch deletion or renaming.".format(len(cad_items)),
                    title="Clean CAD Styles"
                )
                _restore_btn()

            if not hasattr(self, 'is_ai_mode_active') or not self.is_ai_mode_active():
                _apply_classic()
                return

            if btn:
                btn.Content = "⏳ Analyzing..."
                btn.IsEnabled = False

            cad_names = [getattr(it, 'name', '') for it in cad_items[:25]]
            standard_styles = ["<Thin Lines>", "<Medium Lines>", "<Wide Lines>", "<Hidden>", "<Overhead>", "<Centerline>", "<Demolished>"]

            prompt = (
                "You are an expert BIM Specialist auditing AutoCAD imported line styles in Autodesk Revit.\n"
                "The following CAD line styles were imported from DWGs:\n{}\n\n"
                "Target Revit standard styles:\n{}\n\n"
                "For each CAD style, propose the best Revit standard style mapping, or 'DELETE' if obsolete/junk.\n"
                "Return JSON ONLY with keys: {{\"mappings\": [{{\"source\": string, \"target\": string, \"reason\": string}}], \"summary\": string}}"
            ).format(json.dumps(cad_names), json.dumps(standard_styles))

            def _worker():
                return self.ai_bridge.ask_json(prompt, fast=True)

            def _callback(res, err):
                try:
                    if err or not res or not isinstance(res, dict) or 'mappings' not in res:
                        _apply_classic()
                        return

                    mappings = res.get('mappings', [])
                    summary = res.get('summary', 'CAD style consolidation recommended.')

                    # Select detected items in the grid
                    for item in cad_items:
                        item.is_selected = True
                    self._filter_line_styles()

                    lines_msg = ["AI CAD Line Styles Consolidation Plan:"]
                    lines_msg.append(summary)
                    lines_msg.append("")
                    for m in mappings[:12]:
                        lines_msg.append(u"• {} → {} ({})".format(
                            m.get('source', ''), m.get('target', ''), m.get('reason', '')
                        ))
                    if len(mappings) > 12:
                        lines_msg.append(u"... and {} more".format(len(mappings) - 12))
                    lines_msg.append("\nAll {} CAD line styles are now selected in the table.".format(len(cad_items)))

                    forms.alert("\n".join(lines_msg), title="AI Clean Styles Recommendation")
                finally:
                    _restore_btn()

            self.run_ai_async(_worker, _callback)

        except Exception as ex:
            _restore_btn()
            forms.alert("Error analyzing CAD line styles: {}".format(ex), title="AI Clean Styles Error")

    def _on_style_calc_usage(self, s, e):
        for item in self.line_styles:
            item.usage_count = 0
            
        lookup = {}
        for item in self.line_styles:
            if item.category:
                lookup[_eid_int(item.category.Id)] = item
                
        try:
            col = FilteredElementCollector(doc).OfClass(CurveElement)
            usage_dict = {}
            for curve in col:
                try:
                    style_param = curve.get_Parameter(BuiltInParameter.BUILDING_CURVE_GSTYLE)
                    if style_param:
                        style_id = style_param.AsElementId()
                        if style_id and _eid_int(style_id) > 0:
                            style_elem = doc.GetElement(style_id)
                            if style_elem and hasattr(style_elem, 'GraphicsStyleCategory'):
                                style_cat = style_elem.GraphicsStyleCategory
                                if style_cat:
                                    cat_id = _eid_int(style_cat.Id)
                                    if cat_id in lookup:
                                        usage_dict[cat_id] = usage_dict.get(cat_id, 0) + 1
                except:
                    pass
            
            for cat_id, count in usage_dict.items():
                item = lookup.get(cat_id)
                if item: item.usage_count = count
                
            self._filter_line_styles()
            MessageBox.Show("Usage calculation completed!", "Success", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            MessageBox.Show("Error calculating usage: {}".format(str(ex)), "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_style_rename(self, s, e):
        selected = [item for item in self.line_styles if item.is_selected]
        if len(selected) != 1:
            MessageBox.Show("Please select exactly one item to rename!", "Warning", MessageBoxButton.OK, MessageBoxImage.Warning)
            return
            
        item = selected[0]
        if item.is_system:
            MessageBox.Show("Cannot rename system line styles!", "Error", MessageBoxButton.OK, MessageBoxImage.Error)
            return
            
        new_name = forms.ask_for_string(prompt="Enter new name:", default=item.name, title="Rename Line Style")
        if not new_name or new_name.strip() == "" or new_name == item.name:
            return
            
        new_name = re.sub(r'[\/:*?"<>|\\\[\]]', '', new_name).strip()
        for style in self.line_styles:
            if style.name == new_name:
                MessageBox.Show("A line style with this name already exists!", "Error", MessageBoxButton.OK, MessageBoxImage.Error)
                return
                
        old_style = item.category
        lines_category = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
        
        tg = TransactionGroup(doc, "Rename Line Style")
        tg.Start()
        try:
            t1 = Transaction(doc, "Create New Line Style")
            t1.Start()
            new_subcategory = doc.Settings.Categories.NewSubcategory(lines_category, new_name)
            new_subcategory.LineColor = old_style.LineColor
            new_subcategory.SetLineWeight(old_style.GetLineWeight(GraphicsStyleType.Projection), GraphicsStyleType.Projection)
            pat_id = old_style.GetLinePatternId(GraphicsStyleType.Projection)
            if pat_id and _eid_int(pat_id) > 0:
                new_subcategory.SetLinePatternId(pat_id, GraphicsStyleType.Projection)
            t1.Commit()
            
            lines_to_change = []
            col = FilteredElementCollector(doc).OfClass(CurveElement)
            for curve in col:
                try:
                    style_param = curve.get_Parameter(BuiltInParameter.BUILDING_CURVE_GSTYLE)
                    if style_param:
                        style_id = style_param.AsElementId()
                        if style_id:
                            style_elem = doc.GetElement(style_id)
                            if style_elem and hasattr(style_elem, 'GraphicsStyleCategory'):
                                style_cat = style_elem.GraphicsStyleCategory
                                if style_cat and _eid_int(style_cat.Id) == _eid_int(old_style.Id):
                                    lines_to_change.append(curve)
                except:
                    pass
            
            lines_changed = 0
            if lines_to_change:
                t2 = Transaction(doc, "Transfer Lines to New Style")
                t2.Start()
                new_graphics_style = new_subcategory.GetGraphicsStyle(GraphicsStyleType.Projection)
                for line in lines_to_change:
                    try:
                        line.LineStyle = new_graphics_style
                        lines_changed += 1
                    except:
                        pass
                t2.Commit()
                
            t3 = Transaction(doc, "Delete Old Line Style")
            t3.Start()
            doc.Delete(old_style.Id)
            t3.Commit()
            
            tg.Assimilate()
            self._load_line_styles()
            MessageBox.Show("Line style renamed successfully!\nLines transferred: {}".format(lines_changed), "Success", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            tg.RollBack()
            MessageBox.Show("Error renaming line style:\n\n{}".format(str(ex)), "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_style_delete(self, s, e):
        selected = [item for item in self.line_styles if item.is_selected]
        if not selected:
            MessageBox.Show("Please select at least one item to delete!", "Warning", MessageBoxButton.OK, MessageBoxImage.Warning)
            return
            
        custom_selected = [i for i in selected if not i.is_system]
        if not custom_selected:
            MessageBox.Show("None of the selected items can be deleted (system elements).", "Error", MessageBoxButton.OK, MessageBoxImage.Error)
            return
            
        res = MessageBox.Show("Delete {} custom line style(s)?".format(len(custom_selected)), "Confirm Delete", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if res != MessageBoxResult.Yes:
            return
            
        t = Transaction(doc, "Delete Line Styles")
        t.Start()
        try:
            deleted = 0
            failed = 0
            for item in custom_selected:
                try:
                    doc.Delete(item.category.Id)
                    deleted += 1
                except:
                    failed += 1
            t.Commit()
            self._load_line_styles()
            MessageBox.Show("Deleted: {}\nFailed: {} (may be in use)".format(deleted, failed), "Result", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(ex)), "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    # ========================================================================
    # ACTIONS HANDLERS (LINE PATTERNS)
    # ========================================================================
    def _on_pattern_select_all(self, s, e):
        for item in self.filtered_line_patterns: item.is_selected = True
    def _on_pattern_clear_all(self, s, e):
        for item in self.line_patterns: item.is_selected = False
    def _on_pattern_select_custom(self, s, e):
        for item in self.filtered_line_patterns: item.is_selected = not item.is_system

    def _on_pattern_refresh(self, s, e):
        self._load_line_patterns()

    def _on_pattern_rename(self, s, e):
        selected = [item for item in self.line_patterns if item.is_selected]
        if len(selected) != 1:
            MessageBox.Show("Please select exactly one item to rename!", "Warning", MessageBoxButton.OK, MessageBoxImage.Warning)
            return
            
        item = selected[0]
        if item.is_system:
            MessageBox.Show("Cannot rename system line patterns!", "Error", MessageBoxButton.OK, MessageBoxImage.Error)
            return
            
        new_name = forms.ask_for_string(prompt="Enter new name:", default=item.name, title="Rename Line Pattern")
        if not new_name or new_name.strip() == "" or new_name == item.name:
            return
            
        new_name = re.sub(r'[\/:*?"<>|\\\[\]]', '', new_name).strip()
        for pat in self.line_patterns:
            if pat.name == new_name:
                MessageBox.Show("A line pattern with this name already exists!", "Error", MessageBoxButton.OK, MessageBoxImage.Error)
                return
                
        t = Transaction(doc, "Rename Line Pattern")
        t.Start()
        try:
            item.element.Name = new_name
            t.Commit()
            self._load_line_patterns()
            MessageBox.Show("Line pattern renamed successfully!", "Success", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(ex)), "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_pattern_duplicate(self, s, e):
        selected = [item for item in self.line_patterns if item.is_selected]
        if not selected:
            MessageBox.Show("Please select at least one item to duplicate!", "Warning", MessageBoxButton.OK, MessageBoxImage.Warning)
            return
            
        t = Transaction(doc, "Duplicate Line Patterns")
        t.Start()
        try:
            success = 0
            for item in selected:
                try:
                    new_name = "Copy of " + item.name
                    item.element.Duplicate(new_name)
                    success += 1
                except:
                    pass
            t.Commit()
            self._load_line_patterns()
            MessageBox.Show("Duplicated {} line patterns!".format(success), "Result", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(ex)), "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_pattern_delete(self, s, e):
        selected = [item for item in self.line_patterns if item.is_selected]
        if not selected:
            MessageBox.Show("Please select at least one item to delete!", "Warning", MessageBoxButton.OK, MessageBoxImage.Warning)
            return
            
        custom_selected = [i for i in selected if not i.is_system]
        if not custom_selected:
            MessageBox.Show("None of the selected items can be deleted (system elements).", "Error", MessageBoxButton.OK, MessageBoxImage.Error)
            return
            
        res = MessageBox.Show("Delete {} custom line pattern(s)?".format(len(custom_selected)), "Confirm Delete", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if res != MessageBoxResult.Yes:
            return
            
        t = Transaction(doc, "Delete Line Patterns")
        t.Start()
        try:
            deleted = 0
            failed = 0
            for item in custom_selected:
                try:
                    doc.Delete(item.element.Id)
                    deleted += 1
                except:
                    failed += 1
            t.Commit()
            self._load_line_patterns()
            MessageBox.Show("Deleted: {}\nFailed: {} (may be in use)".format(deleted, failed), "Result", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(ex)), "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    # ========================================================================
    # ACTIONS HANDLERS (FILL PATTERNS)
    # ========================================================================
    def _on_fill_select_all(self, s, e):
        for item in self.filtered_fill_patterns: item.is_selected = True
    def _on_fill_clear_all(self, s, e):
        for item in self.fill_patterns: item.is_selected = False
    def _on_fill_select_custom(self, s, e):
        for item in self.filtered_fill_patterns: item.is_selected = not item.is_system

    def _on_fill_refresh(self, s, e):
        self._load_fill_patterns()

    def _on_fill_rename(self, s, e):
        selected = [item for item in self.fill_patterns if item.is_selected]
        if len(selected) != 1:
            MessageBox.Show("Please select exactly one item to rename!", "Warning", MessageBoxButton.OK, MessageBoxImage.Warning)
            return
            
        item = selected[0]
        if item.is_system:
            MessageBox.Show("Cannot rename system fill patterns!", "Error", MessageBoxButton.OK, MessageBoxImage.Error)
            return
            
        new_name = forms.ask_for_string(prompt="Enter new name:", default=item.name, title="Rename Fill Pattern")
        if not new_name or new_name.strip() == "" or new_name == item.name:
            return
            
        new_name = re.sub(r'[\/:*?"<>|\\\[\]]', '', new_name).strip()
        for pat in self.fill_patterns:
            if pat.name == new_name:
                MessageBox.Show("A fill pattern with this name already exists!", "Error", MessageBoxButton.OK, MessageBoxImage.Error)
                return
                
        t = Transaction(doc, "Rename Fill Pattern")
        t.Start()
        try:
            item.element.Name = new_name
            t.Commit()
            self._load_fill_patterns()
            MessageBox.Show("Fill pattern renamed successfully!", "Success", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(ex)), "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_fill_duplicate(self, s, e):
        selected = [item for item in self.fill_patterns if item.is_selected]
        if not selected:
            MessageBox.Show("Please select at least one item to duplicate!", "Warning", MessageBoxButton.OK, MessageBoxImage.Warning)
            return
            
        t = Transaction(doc, "Duplicate Fill Patterns")
        t.Start()
        try:
            success = 0
            for item in selected:
                try:
                    new_name = "Copy of " + item.name
                    item.element.Duplicate(new_name)
                    success += 1
                except:
                    pass
            t.Commit()
            self._load_fill_patterns()
            MessageBox.Show("Duplicated {} patterns!", "Result", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(ex)), "Error", MessageBoxButton.OK, MessageBoxImage.Error)

    def _on_fill_delete(self, s, e):
        selected = [item for item in self.fill_patterns if item.is_selected]
        if not selected:
            MessageBox.Show("Please select at least one item to delete!", "Warning", MessageBoxButton.OK, MessageBoxImage.Warning)
            return
            
        custom_selected = [i for i in selected if not i.is_system]
        if not custom_selected:
            MessageBox.Show("None of the selected items can be deleted (system elements).", "Error", MessageBoxButton.OK, MessageBoxImage.Error)
            return
            
        res = MessageBox.Show("Delete {} custom fill pattern(s)?".format(len(custom_selected)), "Confirm Delete", MessageBoxButton.YesNo, MessageBoxImage.Question)
        if res != MessageBoxResult.Yes:
            return
            
        t = Transaction(doc, "Delete Fill Patterns")
        t.Start()
        try:
            deleted = 0
            failed = 0
            for item in custom_selected:
                try:
                    doc.Delete(item.element.Id)
                    deleted += 1
                except:
                    failed += 1
            t.Commit()
            self._load_fill_patterns()
            MessageBox.Show("Deleted: {}\nFailed: {} (may be in use)".format(deleted, failed), "Result", MessageBoxButton.OK, MessageBoxImage.Information)
        except Exception as ex:
            t.RollBack()
            MessageBox.Show("Error: {}".format(str(ex)), "Error", MessageBoxButton.OK, MessageBoxImage.Error)


    # ========================================================================
    # COLOR SPLASHER BACKEND LOGIC
    # ========================================================================
    
    def _init_color_splasher(self):
        try:
            vn = doc.ActiveView.Name
            if len(vn) > 40: vn = vn[:37] + "..."
            self.txtFooterStatus.Text = "Active View: " + vn
        except:
            self.txtFooterStatus.Text = "Active View: -"
            
        # Fill sources
        self.cbSource.Items.Clear()
        self.cbSource.Items.Add("<Current Project>")
        self.link_instances = get_link_instances()
        for li in self.link_instances:
            try:
                ld = li.GetLinkDocument()
                if ld:
                    self.cbSource.Items.Add("Link: " + ld.Title)
                else:
                    self.cbSource.Items.Add("Link: Unloaded")
            except:
                self.cbSource.Items.Add("Link: Unknown")
        self.cbSource.SelectedIndex = 0

    def _on_source_changed(self, sender, e):
        idx = self.cbSource.SelectedIndex
        if idx <= 0:
            self.selected_link = None
            self.selected_link_doc = None
        else:
            link_idx = idx - 1
            if link_idx < len(self.link_instances):
                self.selected_link = self.link_instances[link_idx]
                try:
                    self.selected_link_doc = self.selected_link.GetLinkDocument()
                except:
                    self.selected_link_doc = None
            else:
                self.selected_link = None
                self.selected_link_doc = None
                
        self._load_categories()

    def _load_categories(self):
        src_doc = self.selected_link_doc if self.selected_link_doc else doc
        if self.selected_link_doc:
            self.categories = collect_categories_from_doc(self.selected_link_doc)
        else:
            self.categories = collect_categories()
            
        self.cbCat.Items.Clear()
        for name, _ in self.categories:
            self.cbCat.Items.Add(name)
            
        if self.cbCat.Items.Count > 0:
            self.cbCat.SelectedIndex = 0

    def _on_cat_changed(self, sender, e):
        idx = self.cbCat.SelectedIndex
        if idx < 0:
            self.lbParams.Items.Clear()
            return
        cat_name, cat_obj = self.categories[idx]
        self.param_names = self._collect_params(cat_obj)
        self._filter_params()

    def _on_param_search_changed(self, sender, e):
        self._filter_params()

    def _filter_params(self):
        query = self.txtSearch.Text.lower() if self.txtSearch.Text else ""
        self.lbParams.Items.Clear()
        for name in self.param_names:
            if query and query not in name.lower(): continue
            self.lbParams.Items.Add(name)

    def _collect_params(self, category):
        src_doc = self.selected_link_doc if self.selected_link_doc else doc
        elems = self._collect_elems(category)
        if not elems:
            return SPECIAL_PARAMS[:]
        names = set()
        for elem in elems[:20]:
            try:
                for p in elem.Parameters:
                    try:
                        if p and p.Definition and p.Definition.Name:
                            names.add(p.Definition.Name)
                    except:
                        continue
            except:
                pass
            try:
                tid = elem.GetTypeId()
                if tid:
                    etype = src_doc.GetElement(tid)
                    if etype:
                        for p in etype.Parameters:
                            try:
                                if p and p.Definition and p.Definition.Name:
                                    names.add(p.Definition.Name)
                            except:
                                continue
            except:
                pass
        result = list(SPECIAL_PARAMS)
        for n in sorted(names):
            if n not in result: result.append(n)
        return result

    def _collect_elems(self, category):
        if self.selected_link_doc:
            return collect_elements_from_doc(self.selected_link_doc, category.Name)
        else:
            return collect_elements(category)

    def _get_val(self, elem, pname):
        if self.selected_link_doc:
            return get_param_value_linked(elem, pname, self.selected_link_doc)
        else:
            return get_param_value(elem, pname)

    def _on_param_selection_changed(self, sender, e):
        sel = self.lbParams.SelectedItem
        if not sel: return
        pname = str(sel)
        self._analyze_param(pname)
        self._assign_palette_colors(is_gradient=True)
        self._rebuild_legend()

    def _analyze_param(self, pname):
        idx = self.cbCat.SelectedIndex
        if idx < 0: return
        cat_name, cat_obj = self.categories[idx]
        elems = self._collect_elems(cat_obj)
        self.elements_by_value = OrderedDict()
        for e in elems:
            v = self._get_val(e, pname)
            if v not in self.elements_by_value:
                self.elements_by_value[v] = []
            self.elements_by_value[v].append(e)
        self.sorted_values = sorted(self.elements_by_value.keys(), key=lambda x: str(x))

    def _assign_palette_colors(self, is_gradient=True):
        n = len(self.sorted_values)
        if n == 0:
            self.color_map = {}
            return
        colors = generate_gradient(n) if is_gradient else generate_random(n)
        self.color_map = {}
        for idx, val in enumerate(self.sorted_values):
            self.color_map[val] = colors[idx]

    def _rebuild_legend(self):
        self.spLeg.Children.Clear()
        total_elems = sum(len(self.elements_by_value[v]) for v in self.sorted_values)
        
        for val in self.sorted_values:
            cnt = len(self.elements_by_value[val])
            r, g, b = self.color_map.get(val, (128, 128, 128))
            
            row = Border()
            row.Margin = Thickness(0, 0, 0, 4)
            row.Padding = Thickness(10, 8, 10, 8)
            row.Background = SolidColorBrush(Color.FromRgb(r, g, b))
            row.CornerRadius = CornerRadius(4)
            
            gr = Grid()
            col_text = ColumnDefinition()
            col_text.Width = GridLength(1, GridUnitType.Star)
            col_count = ColumnDefinition()
            col_count.Width = GridLength.Auto
            gr.ColumnDefinitions.Add(col_text)
            gr.ColumnDefinitions.Add(col_count)
            
            brightness = (r * 299 + g * 587 + b * 114) / 1000
            txt_color = Color.FromRgb(255, 255, 255) if brightness < 140 else Color.FromRgb(24, 24, 27)
            
            tv = TextBlock()
            tv.Text = str(val)
            tv.FontSize = 12.5
            tv.FontWeight = System.Windows.FontWeights.SemiBold
            tv.Foreground = SolidColorBrush(txt_color)
            tv.VerticalAlignment = System.Windows.VerticalAlignment.Center
            Grid.SetColumn(tv, 0)
            gr.Children.Add(tv)
            
            tc = TextBlock()
            tc.Text = "{} elements".format(cnt)
            tc.FontSize = 11.5
            tc.Foreground = SolidColorBrush(txt_color)
            tc.Opacity = 0.8
            tc.VerticalAlignment = System.Windows.VerticalAlignment.Center
            tc.Margin = Thickness(8, 0, 0, 0)
            Grid.SetColumn(tc, 1)
            gr.Children.Add(tc)
            
            row.Child = gr
            self.spLeg.Children.Add(row)
            
        u = len(self.sorted_values)
        self.txtVC.Text = "{} unique values".format(u)
        self.txtStatus.Text = "Analyzed {} elements".format(total_elems)
        self.txtSum.Text = "Selected Parameter: '{}' | Elements Count: {}".format(
            self.lbParams.SelectedItem or "-", total_elems
        )

    def _on_gradient_clicked(self, s, e):
        self._assign_palette_colors(is_gradient=True)
        self._rebuild_legend()

    def _on_random_clicked(self, s, e):
        self._assign_palette_colors(is_gradient=False)
        self._rebuild_legend()

    def _on_legend_clicked(self, s, e):
        if not self.sorted_values or not self.color_map:
            forms.alert("No data to create legend.", title="Color Splasher")
            return
            
        sel = self.lbParams.SelectedItem
        pname = str(sel) if sel else "Parameter"
        idx = self.cbCat.SelectedIndex
        cat_name = self.categories[idx][0] if idx >= 0 else "Category"
        
        # Find emptiest legend view
        existing_legend = None
        min_elements = 999999
        for v in FilteredElementCollector(doc).OfClass(DB.View):
            try:
                if v.ViewType == DB.ViewType.Legend and not v.IsTemplate:
                    elem_count = FilteredElementCollector(doc, v.Id).GetElementCount()
                    if existing_legend is None or elem_count < min_elements:
                        existing_legend = v
                        min_elements = elem_count
            except:
                continue
                
        if not existing_legend:
            forms.alert("No Legend view found in project.\nPlease create one manually first.", title="Color Splasher")
            return
            
        t = Transaction(doc, "T3Lab - Create Color Legend")
        t.Start()
        try:
            new_id = existing_legend.Duplicate(DB.ViewDuplicateOption.Duplicate)
            new_legend = doc.GetElement(new_id)
            
            base_name = "Legend - {} - {}".format(cat_name, pname)
            renamed = False
            try:
                new_legend.Name = base_name
                renamed = True
            except:
                pass
            if not renamed:
                for i in range(1, 100):
                    try:
                        new_legend.Name = "{} - {}".format(base_name, i)
                        break
                    except:
                        continue
                        
            txt_type_id = None
            try:
                for ele in FilteredElementCollector(doc, existing_legend.Id).ToElements():
                    if isinstance(ele, TextNote):
                        txt_type_id = ele.GetTypeId()
                        break
            except:
                pass
            if not txt_type_id:
                for tnt in FilteredElementCollector(doc).OfClass(TextNoteType):
                    txt_type_id = tnt.Id
                    break
            if not txt_type_id:
                t.RollBack()
                forms.alert("No TextNote type found.", title="Color Splasher")
                return
                
            filled_type = None
            for frt in FilteredElementCollector(doc).OfClass(FilledRegionType):
                try:
                    pat = doc.GetElement(frt.ForegroundPatternId)
                    if pat and pat.GetFillPattern().IsSolidFill:
                        filled_type = frt
                        break
                except:
                    continue
            if not filled_type:
                all_frt = list(FilteredElementCollector(doc).OfClass(FilledRegionType))
                if all_frt:
                    for idx in range(100):
                        try:
                            filled_type = all_frt[0].Duplicate("T3Lab Swatch {}".format(idx))
                            break
                        except:
                            continue
                    if filled_type:
                        solid = get_solid_fill()
                        if solid: filled_type.ForegroundPatternId = solid.Id
            if not filled_type:
                t.RollBack()
                forms.alert("No FilledRegion type available.", title="Color Splasher")
                return
                
            from pyrevit.framework import List as FWList
            y_pos = 0.0
            text_data = []
            max_x_list = []
            
            for val in self.sorted_values:
                r, g, b = self.color_map.get(val, (128, 128, 128))
                cnt = len(self.elements_by_value[val])
                text_line = "{} / {} - {} ({})".format(cat_name, pname, val, cnt)
                
                pt = DB.XYZ(0, y_pos, 0)
                try:
                    tn = TextNote.Create(doc, new_legend.Id, pt, text_line, txt_type_id)
                    doc.Regenerate()
                    bbox = tn.get_BoundingBox(new_legend)
                    if bbox:
                        height = bbox.Max.Y - bbox.Min.Y
                        spacing = height * 0.25
                        max_x_list.append(bbox.Max.X)
                        text_data.append((bbox.Min.Y, height, r, g, b))
                        y_pos = bbox.Min.Y - (height + spacing)
                    else:
                        text_data.append((y_pos, 0.01, r, g, b))
                        y_pos -= 0.02
                except:
                    text_data.append((y_pos, 0.01, r, g, b))
                    y_pos -= 0.02
                    
            ini_x = (max(max_x_list) if max_x_list else 0.3) + 0.005
            solid_fill = get_solid_fill()
            
            for td in text_data:
                y_min, height, r, g, b = td
                if height < 0.001: height = 0.01
                rect_w = height * 2
                
                try:
                    p0 = DB.XYZ(ini_x, y_min, 0)
                    p1 = DB.XYZ(ini_x, y_min + height, 0)
                    p2 = DB.XYZ(ini_x + rect_w, y_min + height, 0)
                    p3 = DB.XYZ(ini_x + rect_w, y_min, 0)
                    
                    loop = DB.CurveLoop()
                    loop.Append(DB.Line.CreateBound(p0, p1))
                    loop.Append(DB.Line.CreateBound(p1, p2))
                    loop.Append(DB.Line.CreateBound(p2, p3))
                    loop.Append(DB.Line.CreateBound(p3, p0))
                    
                    loops = FWList[DB.CurveLoop]()
                    loops.Add(loop)
                    region = FilledRegion.Create(doc, filled_type.Id, new_legend.Id, loops)
                    
                    color = RevitColor(r, g, b)
                    ogs = OverrideGraphicSettings()
                    ogs.SetSurfaceForegroundPatternColor(color)
                    ogs.SetCutForegroundPatternColor(color)
                    if solid_fill:
                        ogs.SetSurfaceForegroundPatternId(solid_fill.Id)
                        ogs.SetCutForegroundPatternId(solid_fill.Id)
                    new_legend.SetElementOverrides(region.Id, ogs)
                except:
                    continue
            t.Commit()
            
            # Request open view
            try:
                uidoc.ActiveView = new_legend
            except:
                try:
                    uidoc.RequestViewChange(new_legend)
                except:
                    pass
            forms.alert("Legend created: '{}'".format(new_legend.Name), title="Color Splasher")
        except Exception as ex:
            if t.HasStarted(): t.RollBack()
            forms.alert("Error creating legend:\n{}".format(str(ex)), title="Color Splasher")

    def _on_filters_clicked(self, s, e):
        if _active_view_blocks_overrides():
            forms.alert("Not supported in schedule, sheet, legend, or rendering views — "
                        "switch to a plan/section/3D view first.", title="Color Splasher")
            return

        if not self.sorted_values or not self.color_map:
            forms.alert("No data to create filters.", title="Color Splasher")
            return

        sel = self.lbParams.SelectedItem
        pname = str(sel) if sel else ""
        idx = self.cbCat.SelectedIndex
        cat = self.categories[idx][1] if idx >= 0 else None
        
        if not cat or not pname:
            forms.alert("Select a category and parameter first.", title="Color Splasher")
            return
            
        if pname in SPECIAL_PARAMS:
            forms.alert("Cannot create View Filters for computed parameter '{}'.".format(pname), title="Color Splasher")
            return
            
        active_view = doc.ActiveView
        t = Transaction(doc, "T3Lab - Create View Filters")
        t.Start()
        created = 0
        
        try:
            cat_id = cat.Id
            cat_id_list = System.Collections.Generic.List[DB.ElementId]()
            cat_id_list.Add(cat_id)
            solid = get_solid_fill()
            
            for val in self.sorted_values:
                r, g, b = self.color_map.get(val, (128, 128, 128))
                filter_name = "{} - {}".format(pname, val)
                if len(filter_name) > 200: filter_name = filter_name[:200]
                
                existing = None
                try:
                    for pfe in FilteredElementCollector(doc).OfClass(DB.ParameterFilterElement):
                        if pfe.Name == filter_name:
                            existing = pfe
                            break
                except:
                    pass
                    
                pfilter = existing
                if not pfilter:
                    try:
                        elems = self.elements_by_value.get(val, [])
                        if not elems: continue
                        sample = elems[0]
                        param = sample.LookupParameter(pname)
                        if param is None:
                            tid = sample.GetTypeId()
                            if tid:
                                etype = doc.GetElement(tid)
                                if etype: param = etype.LookupParameter(pname)
                        if param is None: continue
                        
                        param_id = param.Id
                        rule = None
                        st = param.StorageType
                        if st == DB.StorageType.String:
                            str_val = param.AsString() or ""
                            try:
                                rule = DB.ParameterFilterRuleFactory.CreateEqualsRule(param_id, str_val, True)
                            except:
                                rule = DB.ParameterFilterRuleFactory.CreateEqualsRule(param_id, str_val)
                        elif st == DB.StorageType.Integer:
                            rule = DB.ParameterFilterRuleFactory.CreateEqualsRule(param_id, param.AsInteger())
                        elif st == DB.StorageType.Double:
                            rule = DB.ParameterFilterRuleFactory.CreateEqualsRule(param_id, param.AsDouble(), 0.001)
                        elif st == DB.StorageType.ElementId:
                            rule = DB.ParameterFilterRuleFactory.CreateEqualsRule(param_id, param.AsElementId())
                            
                        if rule is None: continue
                        elem_filter = DB.ElementParameterFilter(rule)
                        pfilter = DB.ParameterFilterElement.Create(doc, filter_name, cat_id_list, elem_filter)
                    except:
                        continue
                        
                if pfilter:
                    try:
                        active_view.AddFilter(pfilter.Id)
                        active_view.SetFilterVisibility(pfilter.Id, True)
                        
                        color = RevitColor(r, g, b)
                        ogs = OverrideGraphicSettings()
                        try:
                            ogs.SetSurfaceForegroundPatternColor(color)
                            ogs.SetSurfaceForegroundPatternVisible(True)
                            if solid: ogs.SetSurfaceForegroundPatternId(solid.Id)
                        except:
                            try:
                                ogs.SetProjectionFillColor(color)
                                ogs.SetProjectionFillPatternVisible(True)
                                if solid: ogs.SetProjectionFillPatternId(solid.Id)
                            except:
                                pass
                        try:
                            ogs.SetCutForegroundPatternColor(color)
                            ogs.SetCutForegroundPatternVisible(True)
                            if solid: ogs.SetCutForegroundPatternId(solid.Id)
                        except:
                            pass
                            
                        active_view.SetFilterOverrides(pfilter.Id, ogs)
                        created += 1
                    except:
                        continue
            t.Commit()
            forms.alert("Created/Applied {} view filters in active view.".format(created), title="Color Splasher")
        except Exception as ex:
            if t.HasStarted(): t.RollBack()
            forms.alert("Error creating filters: {}".format(str(ex)), title="Color Splasher")

    def _on_reset_clicked(self, s, e):
        if _active_view_blocks_overrides():
            forms.alert("Not supported in schedule, sheet, legend, or rendering views — "
                        "switch to a plan/section/3D view first.", title="Color Splasher")
            return
        if forms.alert("Clear ALL overrides in active view?", title="Color Splasher", yes=True, no=True):
            count = clear_overrides()
            forms.alert("Cleared overrides for {} elements.".format(count), title="Color Splasher")

    def _on_apply_clicked(self, s, e):
        if not self.elements_by_value:
            forms.alert("Select a category and parameter first.", title="Color Splasher")
            return

        if _active_view_blocks_overrides():
            forms.alert("Not supported in schedule, sheet, legend, or rendering views — "
                        "switch to a plan/section/3D view first.", title="Color Splasher")
            return

        if self.selected_link:
            count = apply_overrides_linked(self.elements_by_value, self.color_map, self.selected_link)
            src = "linked"
            note = "\n(Applied via LinkElementId overrides on the host view)"
        else:
            count = apply_overrides(self.elements_by_value, self.color_map)
            src = "model"
            note = ""
            
        sel = self.lbParams.SelectedItem
        pn = str(sel) if sel else ""
        msg = "Applied overrides to {} {} elements".format(count, src)
        if pn: msg += " by '{}'".format(pn)
        forms.alert(msg + "." + note, title="Color Splasher")

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_grid_style_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua grid_style."""
        self.toggle_all_rows(self.grid_style, "is_selected", sender.IsChecked)

    def select_all_grid_pattern_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua grid_pattern."""
        self.toggle_all_rows(self.grid_pattern, "is_selected", sender.IsChecked)

    def select_all_grid_fill_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua grid_fill."""
        self.toggle_all_rows(self.grid_fill, "is_selected", sender.IsChecked)


def show_visual_settings(script_dir, revit):
    # Fill Pattern / Line Pattern / Line Style tabs are document-level (not
    # view-scoped) and Color Splasher's category/element browsing already
    # falls back to a doc-wide collector when the active view doesn't
    # support FilteredElementCollector(doc, view.Id) — so the window itself
    # works in any view. Only the actions that call SetElementOverrides /
    # AddFilter on the active view (Apply, Clear, View Filters) genuinely
    # require a graphical view; those are gated individually where used.
    try:
        win = ManaStylesWindow(script_dir, revit)
        win.ShowDialog()
    except Exception as e:
        import traceback
        MessageBox.Show("Fatal Error starting Visual & Style Manager:\n\n{}\n\n{}".format(str(e), traceback.format_exc()), "Fatal Error", MessageBoxButton.OK, MessageBoxImage.Error)
