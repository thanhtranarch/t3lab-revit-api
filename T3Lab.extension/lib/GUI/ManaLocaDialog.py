# -*- coding: utf-8 -*-
"""ManaLoca — Modeless dialog and logic for inspecting and adjusting element locations."""

import os
import sys
import json

from pyrevit import forms, revit, script
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Snippets._compat import eid_value

import clr
clr.AddReference('System')
clr.AddReference('WindowsBase')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from System import Action
from System.Collections.Generic import List
from System.Windows import WindowState, Visibility

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ElementLevelFilter,
    Level,
    Transaction,
    XYZ,
    LocationPoint,
    LocationCurve,
    ElementTransformUtils,
    ElementId,
    BuiltInParameter,
    BuiltInCategory,
    OverrideGraphicSettings,
    FillPatternElement,
    Color as RColor,
    Group,
)
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent
from Autodesk.Revit.UI.Selection import ObjectType

XAML_FILE = os.path.join(os.path.dirname(__file__), 'Tools', 'ManaLoca.xaml')
LIB_DIR = os.path.dirname(os.path.dirname(__file__))
EXT_DIR = os.path.dirname(LIB_DIR)
SETTINGS_FILE = os.path.join(
    EXT_DIR, 'T3Lab.tab', 'Standards & Settings.panel', 'ManaLoca.pushbutton', 'session.json'
)
if not os.path.exists(os.path.dirname(SETTINGS_FILE)):
    SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'ManaLoca_session.json')

logger = script.get_logger()


# ==================================================
# DATA MODEL
# ==================================================

class LevelItem(object):
    """One entry in the level ComboBox — holds only plain Python values."""
    def __init__(self, level):
        self.level_id  = level.Id
        self.Name      = level.Name
        self.Elevation = level.Elevation

    def __str__(self):
        return self.Name


class CategoryItem(object):
    """One row in the category filter panel."""
    def __init__(self, name):
        self.Name      = name
        self.IsChecked = True
        self.CountText = "(0)"

    def set_count(self, count):
        self.CountText = "({})".format(count)


class ElementData(object):
    """Snapshot of a Revit element — stores only plain Python values."""

    def __init__(self, element, doc, t):
        self._t        = t   # _TypeCache instance
        self.elem_id   = element.Id
        self.id_val    = t.eid_value(element.Id)
        try:
            self.category  = element.Category.Name if element.Category else "No Category"
        except Exception:
            self.category  = "No Category"
        self.type_name = self._get_type_name(element)
        self.level_name, self.level_elev = self._get_level_info(element, doc)

        pos = self._get_position(element)
        self.x = pos.X * 304.8
        self.y = pos.Y * 304.8
        self.z = pos.Z * 304.8
        self._original_pos = pos

    def _get_type_name(self, element):
        try:
            if hasattr(element, 'Symbol') and element.Symbol:
                p = element.Symbol.get_Parameter(self._t.BIP.ALL_MODEL_TYPE_NAME)
                return p.AsString() if p and p.AsString() else element.Symbol.Name
            return element.Name
        except Exception:
            return "Unknown"

    def _get_level_info(self, element, doc):
        try:
            lvl_id = element.LevelId
            if lvl_id == self._t.EId.InvalidElementId:
                p = (element.get_Parameter(self._t.BIP.FAMILY_LEVEL_PARAM) or
                     element.get_Parameter(self._t.BIP.SCHEDULE_LEVEL_PARAM))
                if p:
                    lvl_id = p.AsElementId()
            if lvl_id and lvl_id != self._t.EId.InvalidElementId:
                lvl = doc.GetElement(lvl_id)
                if lvl:
                    return lvl.Name, lvl.Elevation
        except Exception:
            pass
        return "N/A", -1e9

    def _get_position(self, element):
        try:
            loc = element.Location
            if hasattr(loc, "Point"):
                return loc.Point
            if hasattr(loc, "Curve"):
                return loc.Curve.Evaluate(0.5, True)
            bb = element.get_BoundingBox(None)
            if bb:
                return (bb.Min + bb.Max) * 0.5
        except Exception:
            pass
        return self._t.XYZ.Zero

    @property
    def Id(self):       return self.id_val
    @property
    def Category(self): return self.category
    @property
    def Type(self):     return self.type_name
    @property
    def Level(self):    return self.level_name

    @property
    def X(self): return self.x
    @X.setter
    def X(self, value): self.x = float(value)

    @property
    def Y(self): return self.y
    @Y.setter
    def Y(self, value): self.y = float(value)

    @property
    def Z(self): return self.z
    @Z.setter
    def Z(self, value): self.z = float(value)

    def get_new_xyz(self):
        return self._t.XYZ(self.x / 304.8, self.y / 304.8, self.z / 304.8)

    def has_changed(self):
        return self.get_new_xyz().DistanceTo(self._original_pos) > 0.0001


# ==================================================
# TYPE CACHE
# ==================================================

class _TypeCache(object):
    def __init__(self):
        self.FEC   = FilteredElementCollector
        self.ELF   = ElementLevelFilter
        self.Level = Level
        self.Tx    = Transaction
        self.XYZ   = XYZ
        self.LP    = LocationPoint
        self.LC    = LocationCurve
        self.Group = Group
        self.ETU   = ElementTransformUtils
        self.EId   = ElementId
        self.BIP   = BuiltInParameter
        self.OT    = ObjectType
        self.List  = List
        self.OGS   = OverrideGraphicSettings
        self.Color = RColor
        self.FPE   = FillPatternElement
        self.LevelItem    = LevelItem
        self.CategoryItem = CategoryItem
        self.ElementData  = ElementData
        self.eid_value    = eid_value

        self.os         = os
        self.json       = json
        self.SETTINGS   = SETTINGS_FILE
        self.Visibility = Visibility
        self.WindowState= WindowState

        skip_cat_names = [
            'OST_Views',
            'OST_Cameras',
            'OST_Elev',
            'OST_Viewers',
            'OST_VolumeOfInterest',
            'OST_SectionBox',
            'OST_CropBoundary'
        ]
        skip_cats = set()
        for name in skip_cat_names:
            try:
                skip_cats.add(int(getattr(BuiltInCategory, name)))
            except Exception:
                pass

        self.SKIP_VIEW = frozenset(skip_cats)

        try:
            skip_cats.add(int(BuiltInCategory.OST_Levels))
            skip_cats.add(int(BuiltInCategory.OST_Grids))
        except Exception:
            pass
        self.SKIP_LEVEL = frozenset(skip_cats)

_T = _TypeCache()


# ==================================================
# EXTERNAL EVENT HANDLER
# ==================================================

class LocationManagerHandler(IExternalEventHandler):
    __namespace__ = "T3Lab.ManaLoca"

    def __init__(self, window):
        self.window             = window
        self.action             = None
        self.level_id_to_load   = None
        self.element_id_to_show = None
        self._cached_uidoc      = None
        self._t                 = _T

    def Execute(self, app):
        try:
            uidoc = revit.uidoc
            doc = revit.doc

            if uidoc is None or doc is None:
                if app:
                    uidoc = app.ActiveUIDocument
                    if uidoc:
                        doc = uidoc.Document

            if uidoc is None or doc is None:
                self._report_error("no active document.")
                return

            self._cached_uidoc = uidoc

            fn = {
                "RefreshView":    lambda: self._refresh_view(uidoc, doc),
                "RefreshByLevel": lambda: self._refresh_by_level(doc),
                "GetSelection":   lambda: self._get_selection(uidoc, doc),
                "PopulateLevels": lambda: self._populate_levels(doc),
                "ApplyChanges":   lambda: self._apply_changes(doc),
                "PickElements":   lambda: self._pick_elements(uidoc, doc),
                "ShowElement":    lambda: self._show_element(uidoc),
                "OverrideOdd":    lambda: self._override_odd(uidoc, doc),
                "ClearOverrides": lambda: self._clear_overrides(uidoc, doc),
            }.get(self.action)
            if fn:
                fn()
        except Exception as ex:
            self._report_error(str(ex))

    def GetName(self):
        return "Location Manager Handler"

    def _invoke(self, fn):
        try:
            if self.window and self.window.Dispatcher:
                self.window.Dispatcher.Invoke(Action(fn))
        except Exception as ex:
            logger.debug("LocationManager _invoke error: {}".format(ex))

    def _report_error(self, msg):
        full = "LocationManager error [{}]: {}".format(self.action, msg)
        logger.error(full)
        self._invoke(lambda: self.window._set_status(full))

    def _unpack_groups(self, doc, elements):
        unpacked = []
        for e in elements:
            if not e:
                continue
            if hasattr(e, "GetMemberIds"):
                try:
                    for m_id in e.GetMemberIds():
                        unpacked.append(doc.GetElement(m_id))
                except Exception:
                    unpacked.append(e)
            else:
                unpacked.append(e)
        return unpacked

    def _build_data(self, elements, skip_cats, doc):
        t = self._t
        data_list = []

        total_count = 0
        no_loc_count = 0
        skip_cat_count = 0
        error_count = 0
        first_error = ""

        for e in elements:
            if not e:
                continue
            total_count += 1
            try:
                loc = e.Location
            except Exception:
                loc = None
            if not loc:
                no_loc_count += 1
                continue
            try:
                cat_int = t.eid_value(e.Category.Id) if e.Category else -1
            except Exception:
                cat_int = -1
            if cat_int in skip_cats:
                skip_cat_count += 1
                continue
            try:
                data_list.append(t.ElementData(e, doc, t))
            except Exception as ex:
                error_count += 1
                if not first_error:
                    import traceback
                    first_error = "{}: {}".format(str(ex), traceback.format_exc())
                continue

        if error_count > 0 or first_error:
            diag_msg = "Collected: {}, No Loc: {}, Skipped: {}, Errors: {}".format(
                total_count, no_loc_count, skip_cat_count, error_count
            )
            if first_error:
                diag_msg += " | Error: " + first_error[:80].replace("\n", " ")
            self._invoke(lambda: self.window._set_status(diag_msg))

        data_list.sort(key=lambda x: (x.level_elev, x.category, x.id_val))
        return data_list

    def _send_data(self, data_list):
        self._invoke(lambda: self.window._set_data(data_list))

    def _refresh_view(self, uidoc, doc):
        t    = self._t
        view = uidoc.ActiveView
        try:
            collector = t.FEC(doc, view.Id).WhereElementIsNotElementType()
        except Exception:
            self._invoke(
                lambda: self.window._set_status(
                    "Active view ({}) doesn't support element listing — "
                    "switch to a plan/section/3D view, or use 'By Level' mode."
                    .format(view.ViewType)))
            return
        self._send_data(self._build_data(collector, t.SKIP_VIEW, doc))

    def _refresh_by_level(self, doc):
        if not self.level_id_to_load:
            return
        t = self._t
        collector = (t.FEC(doc)
                     .WherePasses(t.ELF(self.level_id_to_load))
                     .WhereElementIsNotElementType())
        self._send_data(self._build_data(collector, t.SKIP_LEVEL, doc))

    def _get_selection(self, uidoc, doc):
        t   = self._t
        ids = uidoc.Selection.GetElementIds()
        if not ids:
            self._invoke(
                lambda: self.window._set_status("No elements selected in Revit."))
            return
        elements = [doc.GetElement(i) for i in ids]
        self._send_data(self._build_data(elements, set(), doc))

    def _populate_levels(self, doc):
        t      = self._t
        raw    = t.FEC(doc).OfClass(t.Level).WhereElementIsNotElementType()
        levels = sorted([t.LevelItem(l) for l in raw], key=lambda l: l.Elevation)
        self._invoke(lambda: self.window._set_level_items(levels))

    def _show_element(self, uidoc):
        t   = self._t
        eid = self.element_id_to_show
        if not eid:
            return
        try:
            uidoc.ShowElements(eid)
            ids = t.List[t.EId]()
            ids.Add(eid)
            uidoc.Selection.SetElementIds(ids)
        except Exception:
            pass

    def _apply_changes(self, doc):
        t       = self._t
        changed = [item for item in self.window.all_elements if item.has_changed()]
        if not changed:
            self._invoke(
                lambda: self.window._set_status("No coordinate changes detected."))
            return

        count = 0
        last_error = ""

        try:
            with t.Tx(doc, "Move Elements") as tx:
                tx.Start()
                for item in changed:
                    try:
                        new_pos     = item.get_new_xyz()
                        translation = new_pos.Subtract(item._original_pos)
                        if translation.GetLength() > 0.0001:
                            t.ETU.MoveElement(doc, item.elem_id, translation)
                            item._original_pos = new_pos
                            count += 1
                    except Exception as ex:
                        last_error = str(ex)
                        continue
                tx.Commit()

            if count > 0:
                msg = "Moved {} element(s).".format(count)
            else:
                msg = "Failed to move. Error: {}".format(last_error) if last_error else "No elements moved."

        except Exception as tx_ex:
            msg = "Revit API Error: " + str(tx_ex)

        self._invoke(lambda: self.window._set_status(msg))

    def _pick_elements(self, uidoc, doc):
        t = self._t
        try:
            choices = uidoc.Selection.PickObjects(t.OT.Element, "Select elements")
            if choices:
                elements = [doc.GetElement(r.ElementId) for r in choices]
                elements = self._unpack_groups(doc, elements)
                self._send_data(self._build_data(elements, set(), doc))
        except Exception:
            pass
        finally:
            self._invoke(lambda: self.window.Show())

    @staticmethod
    def _is_odd(item, threshold=0.01):
        """Return True if any coordinate (mm) has a fractional part > threshold."""
        return (abs(item.x - round(item.x)) > threshold or
                abs(item.y - round(item.y)) > threshold or
                abs(item.z - round(item.z)) > threshold)

    def _get_solid_fill_id(self, doc):
        try:
            for fp in self._t.FEC(doc).OfClass(self._t.FPE):
                if fp.GetFillPattern().IsSolidFill:
                    return fp.Id
        except Exception:
            pass
        return None

    def _override_odd(self, uidoc, doc):
        t    = self._t
        view = uidoc.ActiveView
        odd  = [item for item in self.window.all_elements if self._is_odd(item)]
        if not odd:
            self._invoke(
                lambda: self.window._set_status(
                    "No elements with fractional mm coordinates found."))
            return

        solid_id = self._get_solid_fill_id(doc)
        red      = t.Color(255, 0, 0)

        ogs = t.OGS()
        try:
            ogs.SetCutForegroundPatternColor(red)
            ogs.SetCutForegroundPatternVisible(True)
            if solid_id:
                ogs.SetCutForegroundPatternId(solid_id)
            ogs.SetSurfaceForegroundPatternColor(red)
            ogs.SetSurfaceForegroundPatternVisible(True)
            if solid_id:
                ogs.SetSurfaceForegroundPatternId(solid_id)
        except Exception:
            try:
                ogs.SetCutFillColor(red)
                if solid_id:
                    ogs.SetCutFillPatternId(solid_id)
            except Exception:
                pass

        with t.Tx(doc, "Override Odd-Coordinate Elements") as tx:
            tx.Start()
            count = 0
            for item in odd:
                try:
                    view.SetElementOverrides(item.elem_id, ogs)
                    count += 1
                except Exception:
                    continue
            tx.Commit()

        msg = "Overridden {}/{} elements with fractional coordinates (red).".format(
            count, len(odd))
        self._invoke(lambda: self.window._set_status(msg))

    def _clear_overrides(self, uidoc, doc):
        t         = self._t
        view      = uidoc.ActiveView
        empty_ogs = t.OGS()
        with t.Tx(doc, "Clear Element Overrides") as tx:
            tx.Start()
            count = 0
            for item in self.window.all_elements:
                try:
                    view.SetElementOverrides(item.elem_id, empty_ogs)
                    count += 1
                except Exception:
                    continue
            tx.Commit()
        msg = "Cleared overrides for {} elements.".format(count)
        self._invoke(lambda: self.window._set_status(msg))


# ==================================================
# UI WINDOW
# ==================================================

class LocationManagerWindow(T3WPFWindow):
    def __init__(self, xaml_file_path=None):
        xaml_path = xaml_file_path or XAML_FILE
        self.all_elements    = []
        self._category_items = []
        self._updating       = False
        self._ready          = False

        T3WPFWindow.__init__(self, xaml_path)

        self.handler   = LocationManagerHandler(self)
        self.ext_event = ExternalEvent.Create(self.handler)
        self._ready    = True
        self._trigger("RefreshView")

    # ── ExternalEvent helper ──────────────────────────────────────────────────

    def _trigger(self, action, **kwargs):
        self.handler.action = action
        for k, v in kwargs.items():
            setattr(self.handler, k, v)
        self.ext_event.Raise()

    # ── Data entry points (Dispatcher.Invoke targets) ─────────────────────────

    def _set_data(self, data_list):
        self.all_elements = data_list
        self._build_category_list()
        self._update_display()
        self._set_status("Ready — {} elements loaded.".format(len(data_list)))

    def _set_level_items(self, levels):
        if hasattr(self, 'cmb_level') and self.cmb_level:
            self.cmb_level.ItemsSource = None
            self.cmb_level.ItemsSource = to_items_source(levels)
            if levels:
                self.cmb_level.SelectedIndex = 0

    # ── Category persistence ──────────────────────────────────────────────────

    def _load_saved_unchecked(self):
        t = self.handler._t
        try:
            if t.os.path.exists(t.SETTINGS):
                with open(t.SETTINGS, 'r') as f:
                    return set(t.json.load(f).get('unchecked_categories', []))
        except Exception:
            pass
        return set()

    def _save_category_state(self):
        t = self.handler._t
        try:
            unchecked = [c.Name for c in self._category_items if not c.IsChecked]
            with open(t.SETTINGS, 'w') as f:
                t.json.dump({'unchecked_categories': unchecked}, f)
        except Exception:
            pass

    # ── Category management ───────────────────────────────────────────────────

    def _build_category_list(self):
        t = self.handler._t
        saved_unchecked  = self._load_saved_unchecked()
        existing_checked = {c.Name: c.IsChecked for c in self._category_items}

        cat_counts = {}
        for item in self.all_elements:
            cat_counts[item.category] = cat_counts.get(item.category, 0) + 1

        new_items = []
        for cat in sorted(cat_counts.keys()):
            ci = t.CategoryItem(cat)
            if cat in existing_checked:
                ci.IsChecked = existing_checked[cat]
            else:
                ci.IsChecked = cat not in saved_unchecked
            ci.set_count(cat_counts[cat])
            new_items.append(ci)

        self._category_items = new_items
        if hasattr(self, 'lst_categories') and self.lst_categories:
            self.lst_categories.ItemsSource = None
            self.lst_categories.ItemsSource = to_items_source(self._category_items)

    def _get_active_categories(self):
        return set(c.Name for c in self._category_items if c.IsChecked)

    # ── Display ───────────────────────────────────────────────────────────────

    def _update_display(self):
        search_text = self.txt_search.Text.strip().lower() if hasattr(self, 'txt_search') and self.txt_search else ""
        active_cats = self._get_active_categories()

        filtered = []
        for data in self.all_elements:
            if data.category not in active_cats:
                continue
            if search_text:
                if not (search_text in data.category.lower()   or
                        search_text in data.type_name.lower()  or
                        search_text in data.level_name.lower() or
                        search_text in str(data.id_val)):
                    continue
            filtered.append(data)

        self._updating = True
        try:
            if hasattr(self, 'elem_datagrid') and self.elem_datagrid:
                self.elem_datagrid.ItemsSource = to_items_source(filtered)
        finally:
            self._updating = False
        if hasattr(self, 'status_count') and self.status_count:
            self.status_count.Text = "{} / {} elements".format(len(filtered), len(self.all_elements))

    def _set_status(self, msg):
        if hasattr(self, 'status_text') and self.status_text:
            self.status_text.Text = msg

    # ── Toolbar event handlers (WPF thread — NO Revit API here) ──────────────

    def mode_changed(self, sender, e):
        if not self._ready:
            return
        t = self.handler._t
        is_level = self.rb_by_level.IsChecked if hasattr(self, 'rb_by_level') else False
        if hasattr(self, 'cmb_level') and self.cmb_level:
            self.cmb_level.Visibility = t.Visibility.Visible if is_level else t.Visibility.Collapsed
        if is_level:
            self._trigger("PopulateLevels")
        else:
            self._trigger("RefreshView")

    def level_selection_changed(self, sender, e):
        if not self._ready:
            return
        if hasattr(self, 'cmb_level') and self.cmb_level:
            level_item = self.cmb_level.SelectedItem
            if hasattr(self, 'rb_by_level') and self.rb_by_level.IsChecked and level_item:
                self._trigger("RefreshByLevel", level_id_to_load=level_item.level_id)

    def refresh_clicked(self, sender, e):
        if hasattr(self, 'rb_by_level') and self.rb_by_level.IsChecked:
            level_item = self.cmb_level.SelectedItem if hasattr(self, 'cmb_level') else None
            if level_item:
                self._trigger("RefreshByLevel", level_id_to_load=level_item.level_id)
            else:
                self._set_status("Select a level first.")
        else:
            self._trigger("RefreshView")

    def get_selection_clicked(self, sender, e):
        self._trigger("GetSelection")

    def pick_elements_clicked(self, sender, e):
        self.Hide()
        self._trigger("PickElements")

    def sync_selection_clicked(self, sender, e):
        self.datagrid_selection_changed(sender, e)

    def round_to_5mm_clicked(self, sender, e):
        if not hasattr(self, 'elem_datagrid') or not self.elem_datagrid:
            return
        rows = self.elem_datagrid.SelectedItems
        if not rows or len(rows) == 0:
            rows = self.elem_datagrid.Items

        count = 0
        for item in rows:
            if hasattr(item, 'X'):
                item.X = round(item.X / 5.0) * 5.0
                item.Y = round(item.Y / 5.0) * 5.0
                item.Z = round(item.Z / 5.0) * 5.0
                count += 1

        if count > 0:
            self.elem_datagrid.Items.Refresh()
            self._set_status("Rounded {} elements to 5mm. Click 'Apply Changes' to save.".format(count))
        else:
            self._set_status("No elements available to round.")

    def override_odd_clicked(self, sender, e):
        if not self.all_elements:
            self._set_status("Load elements first.")
            return
        self._trigger("OverrideOdd")

    def clear_overrides_clicked(self, sender, e):
        if not self.all_elements:
            self._set_status("Load elements first.")
            return
        self._trigger("ClearOverrides")

    def apply_clicked(self, sender, e):
        self._trigger("ApplyChanges")

    def search_changed(self, sender, e):
        self._update_display()

    # ── DataGrid handlers ─────────────────────────────────────────────────────

    def datagrid_selection_changed(self, sender, e):
        if self._updating:
            return
        uidoc = self.handler._cached_uidoc
        if uidoc is None:
            try:
                uidoc = revit.uidoc
            except Exception:
                pass
        if uidoc is None:
            return
        if not hasattr(self, 'elem_datagrid') or not self.elem_datagrid:
            return
        rows = self.elem_datagrid.SelectedItems
        if not rows:
            return
        t = self.handler._t
        try:
            ids = t.List[t.EId]()
            for item in rows:
                ids.Add(item.elem_id)
            uidoc.Selection.SetElementIds(ids)
        except Exception:
            pass

    def id_clicked(self, sender, e):
        data = sender.DataContext
        if data:
            self._trigger("ShowElement", element_id_to_show=data.elem_id)

    # ── Category filter handlers ──────────────────────────────────────────────

    def category_filter_changed(self, sender, e):
        item = sender.DataContext
        if item is not None:
            item.IsChecked = bool(sender.IsChecked)
        self._save_category_state()
        self._update_display()

    def select_all_categories_clicked(self, sender, e):
        for item in self._category_items:
            item.IsChecked = True
        if hasattr(self, 'lst_categories') and self.lst_categories:
            self.lst_categories.ItemsSource = None
            self.lst_categories.ItemsSource = to_items_source(self._category_items)
        self._save_category_state()
        self._update_display()

    def clear_all_categories_clicked(self, sender, e):
        for item in self._category_items:
            item.IsChecked = False
        if hasattr(self, 'lst_categories') and self.lst_categories:
            self.lst_categories.ItemsSource = None
            self.lst_categories.ItemsSource = to_items_source(self._category_items)
        self._save_category_state()
        self._update_display()

    # ── Window chrome handlers ────────────────────────────────────────────────

    def minimize_button_clicked(self, sender, e):
        t = self.handler._t
        self.WindowState = t.WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        t = self.handler._t
        self.WindowState = (t.WindowState.Normal
                            if self.WindowState == t.WindowState.Maximized
                            else t.WindowState.Maximized)

    def close_button_clicked(self, sender, e):
        self.Close()


def detect_persistent_engine():
    """Detect whether persistent engine is enabled for this command."""
    try:
        from pyrevit import EXEC_PARAMS
        try:
            return bool(EXEC_PARAMS.needs_persistent_engine)
        except Exception:
            pass
        try:
            cfg_json = EXEC_PARAMS.script_runtime.ScriptRuntimeConfigs.EngineConfigs
            if cfg_json:
                return bool(json.loads(cfg_json).get('persistent', False))
        except Exception:
            pass
    except Exception:
        pass
    return False


def show_location_manager_dialog(modal=False):
    """Launch the Location Manager window."""
    win = LocationManagerWindow(XAML_FILE)
    if modal:
        win.show(modal=True)
    else:
        win.show(modal=False)
    return win
