# -*- coding: utf-8 -*-
"""
CAD to Elements — convert CAD linework into Revit elements.

One window, nine modes (rail tiles), ONE fixed layout:

    A  CAD SOURCE | LEVEL          shared by every mode, selection persists
    B  mode title + description
    C  options card                same row skeleton in every mode
    D  CAD layers                  same toolbar / list / tally in every mode

Switching mode only swaps which `opt_<mode>` grid is visible inside the fixed
options card and re-ticks that mode's own layer selection — nothing moves.

Modes: Walls · Floors · Ceilings · Rooms · Columns · Beams · Grids · Lines ·
MEP Runs (ducts, pipes, cable trays, conduits).

Rules deciding WHAT gets created live in `Snippets/_cad_geometry.py` (pure,
unit-tested); Revit calls in `Snippets/_cad_revit.py` (one transaction per
run). Every run shows the count first (P5 confirm), then a result with counts.

Copyright (c) 2026 T3Lab
"""
import os
import re

from System.Windows import Visibility, RoutedEventHandler
from System.Windows.Controls import CheckBox, ComboBoxItem
from System.Windows.Input import Key

import Autodesk.Revit.DB as DB
from pyrevit import revit

from GUI.WPF_Base import T3WPFWindow
from GUI.T3Dialog import show_info, show_warning, show_error, confirm
from Snippets import _cad_geometry as geo
from Snippets import _cad_revit as cr
from Snippets._compat import eid_value

_XAML_HUB = os.path.join(os.path.dirname(__file__), "Tools", "CADToElements.xaml")
TITLE = "CAD to Elements"

ROUND_SKIP = {"element": None, "name": "(Skip circles)"}
UNCONNECTED = {"element": None, "name": "Unconnected (use height)"}


class _InputError(Exception):
    """A field the user has to fix; the message is shown as-is."""


class LayerRow(object):
    """One CAD layer in the shared list. `count` is text for the grid."""

    def __init__(self, geom):
        self.geom = geom
        self.name = geom.name
        self.is_selected = False
        self.count = "0"


def _counted(n, noun):
    """'1 wall' / '3 walls' — nouns are given in the plural."""
    if n == 1 and noun.endswith("s"):
        noun = noun[:-1]
    return u"{} {}".format(n, noun)


_ONE_PLURAL = re.compile(r"\b1 ([A-Za-z][A-Za-z ]*?)s\b")


class Plan(object):
    """What a run will do — shown in the confirm dialog before anything changes."""

    def __init__(self, count, noun, question, ok_text, details, execute, empty_msg=None):
        if count == 1:
            question = _ONE_PLURAL.sub(r"1 \1", question, count=1)
            ok_text = _ONE_PLURAL.sub(r"1 \1", ok_text, count=1)
        self.count = count
        self.noun = noun
        self.question = question
        self.ok_text = ok_text
        self.details = details
        self.execute = execute
        self.empty_msg = empty_msg


class CADToElementsWindow(T3WPFWindow):
    AI_TOOL = "CADToElements"

    # ------------------------------------------------------------------
    # Construction — everything is loaded here (rule S7), not on Loaded
    # ------------------------------------------------------------------

    def __init__(self):
        T3WPFWindow.__init__(self, _XAML_HUB)
        self._doc = revit.doc
        self._uidoc = revit.uidoc
        self._mode = "wall"
        self._mep_key = "duct"
        self._mep_values = {}
        self._mep_types = {}
        self._mep_systems = {}
        self._column_structural = None
        self._cad_list = []
        self._levels = []
        self._rows = []
        self._selected = dict((k, set()) for k in geo.MODE_KEYS)
        self._lists = {}
        self._busy = True

        self._tiles = dict((k, getattr(self, "btn_mode_" + k)) for k in geo.MODE_KEYS)
        self._panels = dict((k, getattr(self, "opt_" + k)) for k in geo.MODE_KEYS)

        # Row checkbox clicks bubble to the grid; the bridge has already
        # written the row by then, so the tally reads the new state.
        self._row_click_handler = RoutedEventHandler(self._on_row_checkbox_click)
        self.grid_layers.AddHandler(CheckBox.ClickEvent, self._row_click_handler, True)
        self.PreviewKeyDown += self._on_key_down

        self._load_all()
        self._busy = False
        self._apply_mode("wall")
        self.init_ai_badge()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_all(self):
        keep_cad = self._current_cad_id()
        keep_level = self._current_level_id()
        self._cad_list = cr.get_cad_instances(self._doc)
        self._fill("cmb_cad_files", self._cad_list, "No CAD file in this model",
                   default=self._index_of(self._cad_list, keep_cad))
        self._levels = cr.get_levels(self._doc)
        for lv in self._levels:
            lv["label"] = u"{} ({} mm)".format(lv["name"], int(round(geo.to_mm(lv["elevation"]))))
        self._fill("cmb_levels", self._levels, "No level in this model", label="label",
                   default=self._default_level_index(keep_level))
        self._fill_types()
        self._fill_level_dependent()
        self._scan_current_cad()

    def _fill_types(self):
        doc = self._doc
        self._fill("cmb_wall_type", cr.get_basic_wall_types(doc), "No basic wall type loaded",
                   prefer=("generic",))
        self._fill("cmb_floor_type", cr.get_floor_types(doc), "No floor type loaded")
        self._fill("cmb_ceiling_type", cr.get_ceiling_types(doc), "No ceiling type loaded")
        cats = cr.get_ds_categories()
        for name, prefer in (("cmb_wall_part_category", "walls"),
                             ("cmb_part_category", "floors"),
                             ("cmb_ceiling_part_category", "ceilings"),
                             ("cmb_beam_part_category", "structural framing")):
            self._fill(name, cats, "No category", prefer=(prefer,), exact=True)
        self._fill("cb_beam_types",
                   cr.get_symbols(doc, DB.BuiltInCategory.OST_StructuralFraming),
                   "No structural framing family loaded")
        self._fill("cmb_grid_type", cr.get_grid_types(doc), "No grid type loaded")
        self._fill("cmb_line_style", cr.get_line_styles(doc), "No line style found",
                   prefer=("thin",))
        for c in geo.MEP_CATEGORIES:
            self._mep_types[c["key"]] = cr.get_mep_types(doc, c["key"])
            self._mep_systems[c["key"]] = cr.get_mep_systems(doc, c["key"])
        self._column_structural = None
        self._fill_column_types()
        self._fill_mep(self._mep_key, restore=True)

    def _fill_column_types(self):
        structural = self._checked(self.rb_column_structural)
        if structural == self._column_structural:
            return
        self._column_structural = structural
        bic = (DB.BuiltInCategory.OST_StructuralColumns if structural
               else DB.BuiltInCategory.OST_Columns)
        syms = cr.get_symbols(self._doc, bic)
        self._fill("cmb_column_type", syms,
                   "No {} column family loaded".format("structural" if structural else "architectural"),
                   prefer=("rectangular", "concrete", "square"))
        round_default = 0
        for i, s in enumerate(syms):
            low = s["name"].lower()
            if "round" in low or "circular" in low or "pipe" in low:
                round_default = i + 1
                break
        self._fill("cmb_column_round_type", [ROUND_SKIP] + syms, "No column family loaded",
                   default=round_default)

    def _fill_level_dependent(self):
        level = self._pick("cmb_levels")
        views = cr.get_plan_views(self._doc, level["id"]) if level else []
        active = 0
        try:
            av_id = eid_value(self._doc.ActiveView.Id)
            for i, v in enumerate(views):
                if eid_value(v["id"]) == av_id:
                    active = i
        except Exception:
            pass
        self._fill("cmb_room_view", views, "No floor plan of this level", default=active)
        above = []
        if level is not None:
            above = [lv for lv in self._levels if lv["elevation"] > level["elevation"] + 1e-6]
        self._fill("cmb_column_top_level", [UNCONNECTED] + above, "No level",
                   default=1 if above else 0)
        self._update_enabling()

    def _scan_current_cad(self):
        cad = self._pick("cmb_cad_files")
        layers = {}
        if cad is not None:
            self._set_status(u"Reading layers of {}…".format(cad["name"]))
            self._do_events()
            try:
                layers = cr.scan_cad(self._doc, cad["element"])
            except Exception as ex:
                self._set_status(u"Could not read {}: {}".format(cad["name"], ex), "danger")
                layers = {}
        self._rows = [LayerRow(layers[n]) for n in sorted(layers, key=lambda s: s.lower())]
        self._restore_selection()
        self._update_counts()
        self._apply_filter()
        if cad is None:
            self._set_status("No CAD file in this model — import or link a DWG first.", "warning")
        elif cad is not None and self._rows:
            self._set_status(u"Read {} layers from {}.".format(len(self._rows), cad["name"]))

    # ------------------------------------------------------------------
    # Combo helpers
    # ------------------------------------------------------------------

    def _fill(self, name, items, empty_text, label="name", default=0, prefer=None, exact=False):
        combo = getattr(self, name)
        combo.Items.Clear()
        self._lists[name] = list(items)
        if not items:
            hint = ComboBoxItem()
            hint.Content = empty_text
            hint.IsEnabled = False
            combo.Items.Add(hint)
            combo.SelectedIndex = 0
            return
        for it in items:
            combo.Items.Add(it[label])
        if prefer:
            for i, it in enumerate(items):
                low = it[label].lower()
                if any((low == p) if exact else (p in low) for p in prefer):
                    default = i
                    break
        combo.SelectedIndex = default if 0 <= default < len(items) else 0

    def _pick(self, name):
        items = self._lists.get(name) or []
        idx = getattr(self, name).SelectedIndex
        return items[idx] if 0 <= idx < len(items) else None

    @staticmethod
    def _index_of(items, item_id):
        for i, it in enumerate(items):
            if item_id is not None and it.get("id") == item_id:
                return i
        return 0

    def _current_cad_id(self):
        cad = self._pick("cmb_cad_files") if "cmb_cad_files" in self._lists else None
        return cad["id"] if cad else None

    def _current_level_id(self):
        lv = self._pick("cmb_levels") if "cmb_levels" in self._lists else None
        return eid_value(lv["id"]) if lv else None

    def _default_level_index(self, keep_id):
        wanted = keep_id
        if wanted is None:
            try:
                gen = self._doc.ActiveView.GenLevel
                wanted = eid_value(gen.Id) if gen is not None else None
            except Exception:
                wanted = None
        for i, lv in enumerate(self._levels):
            if wanted is not None and eid_value(lv["id"]) == wanted:
                return i
        return 0

    @staticmethod
    def _checked(ctrl):
        try:
            return bool(ctrl.IsChecked)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Status line
    # ------------------------------------------------------------------

    def _set_status(self, msg, state="ok"):
        try:
            self.txt_status.Text = msg
        except Exception:
            pass
        key = {"ok": "T3.Success.Accent", "warning": "T3.Warning.Accent",
               "danger": "T3.Danger.Accent"}.get(state, "T3.Success.Accent")
        try:
            brush = self.TryFindResource(key)
            if brush is not None:
                self.dot_status.Fill = brush
        except Exception:
            pass

    def _update_status(self, text):
        """Progress messages from T3WPFWindow.step_progress land here."""
        self._set_status(text)

    # ------------------------------------------------------------------
    # Modes
    # ------------------------------------------------------------------

    def mode_tile_clicked(self, sender, e):
        self._apply_mode(str(sender.Tag))

    def _apply_mode(self, key):
        if key not in self._panels:
            return
        self._store_selection()
        self._mode = key
        for k, tile in self._tiles.items():
            tile.IsChecked = (k == key)
        for k, panel in self._panels.items():
            panel.Visibility = Visibility.Visible if k == key else Visibility.Collapsed
        m = geo.mode(key)
        self.txt_mode_title.Text = m["title"]
        self.txt_mode_desc.Text = m["desc"]
        try:
            self.grid_layers.Columns[2].Header = geo.COUNT_HEADERS[m["count_kind"]]
        except Exception:
            pass
        self._restore_selection()
        self._update_counts()
        self._apply_filter()
        self._update_run_label()
        self._update_enabling()

    def _update_run_label(self):
        verb = geo.mode(self._mode)["verb"]
        if self._mode == "mep":
            verb = geo.mep_category(self._mep_key)["verb"]
        self.btn_run.Content = verb

    def create_as_changed(self, sender, e):
        if self._busy:
            return
        if self._mode == "column":
            self._fill_column_types()
        self._update_enabling()

    def column_top_level_changed(self, sender, e):
        self._update_enabling()

    def _update_enabling(self):
        """Inside a mode, fields that do not apply are disabled — never hidden."""
        c = self._checked
        wall_part = c(self.rb_wall_part_mode)
        for ctrl in (self.cmb_wall_type, self.chk_wall_match_thickness, self.chk_structural):
            ctrl.IsEnabled = not wall_part
        self.cmb_wall_part_category.IsEnabled = wall_part

        floor_part = c(self.rb_part_mode)
        self.cmb_floor_type.IsEnabled = not floor_part
        self.chk_floor_structural.IsEnabled = not floor_part
        self.txt_part_thickness.IsEnabled = floor_part
        self.cmb_part_category.IsEnabled = floor_part

        ceil_part = c(self.rb_ceiling_part_mode)
        self.cmb_ceiling_type.IsEnabled = not ceil_part
        self.txt_ceiling_part_thickness.IsEnabled = ceil_part
        self.cmb_ceiling_part_category.IsEnabled = ceil_part

        lines_only = c(self.rb_room_lines)
        self.cmb_room_view.IsEnabled = not c(self.rb_room_only)
        self.txt_room_name.IsEnabled = not lines_only
        self.chk_room_skip_existing.IsEnabled = not lines_only

        top = self._pick("cmb_column_top_level")
        self.txt_column_height.IsEnabled = top is None or top.get("element") is None

        beam_part = c(self.rb_beam_part_mode)
        self.cb_beam_types.IsEnabled = not beam_part
        self.chk_beam_match_size.IsEnabled = not beam_part
        self.cmb_beam_part_category.IsEnabled = beam_part

        auto = c(self.rb_grid_autoname)
        self.txt_grid_start_number.IsEnabled = auto
        self.txt_grid_start_letter.IsEnabled = auto

        self.txt_lines_offset.IsEnabled = not c(self.rb_lines_detail)

        cat = geo.mep_category(self._mep_key)
        double = cat["double"] and self.cmb_mep_line_mode.SelectedIndex == 1
        self.cmb_mep_system.IsEnabled = cat["system"]
        self.cmb_mep_line_mode.IsEnabled = cat["double"]
        self.txt_mep_width.IsEnabled = not double
        self.txt_mep_height.IsEnabled = cat["height"]

    # ── MEP category (ducts / pipes / cable trays / conduits) ──

    def mep_category_changed(self, sender, e):
        if self._busy:
            return
        key = str(sender.Tag)
        if key != self._mep_key:
            self._save_mep(self._mep_key)
            self._fill_mep(key, restore=True)
            self._update_run_label()
            self._update_counts()
            self._update_tally()

    def mep_line_mode_changed(self, sender, e):
        if not self._busy:
            self._update_enabling()

    def _save_mep(self, key):
        self._mep_values[key] = dict(
            type=self.cmb_mep_type.SelectedIndex, system=self.cmb_mep_system.SelectedIndex,
            line=self.cmb_mep_line_mode.SelectedIndex, width=self.txt_mep_width.Text,
            height=self.txt_mep_height.Text, offset=self.txt_mep_offset.Text)

    def _fill_mep(self, key, restore):
        self._mep_key = key
        cat = geo.mep_category(key)
        busy, self._busy = self._busy, True
        try:
            self.lbl_mep_type.Text = cat["type_label"].upper()
            self.lbl_mep_width.Text = cat["width_label"]
            self._fill("cmb_mep_type", self._mep_types.get(key, []),
                       "No {} loaded — load an MEP template".format(cat["type_label"]))
            if cat["system"]:
                self._fill("cmb_mep_system", self._mep_systems.get(key, []),
                           "No system type loaded")
            else:
                self._fill("cmb_mep_system", [], "Not used for {}".format(cat["noun"]))
            saved = self._mep_values.get(key) if restore else None
            if saved:
                for name, idx in (("cmb_mep_type", saved["type"]), ("cmb_mep_system", saved["system"])):
                    combo = getattr(self, name)
                    if 0 <= idx < combo.Items.Count:
                        combo.SelectedIndex = idx
                self.cmb_mep_line_mode.SelectedIndex = saved["line"] if cat["double"] else 0
                self.txt_mep_width.Text = saved["width"]
                self.txt_mep_height.Text = saved["height"]
                self.txt_mep_offset.Text = saved["offset"]
            else:
                self.cmb_mep_line_mode.SelectedIndex = 0
                self.txt_mep_width.Text = "{:g}".format(cat["width"])
                self.txt_mep_height.Text = "{:g}".format(cat["height_mm"]) if cat["height_mm"] else ""
                self.txt_mep_offset.Text = "{:g}".format(cat["offset"])
        finally:
            self._busy = busy
        self._update_enabling()

    # ------------------------------------------------------------------
    # Source / level
    # ------------------------------------------------------------------

    def cad_file_changed(self, sender, e):
        if not self._busy:
            self._scan_current_cad()

    def level_changed(self, sender, e):
        if not self._busy:
            self._fill_level_dependent()

    def refresh_clicked(self, sender, e):
        self._rescan()

    def _rescan(self):
        self._store_selection()
        self._busy = True
        try:
            self._load_all()
        finally:
            self._busy = False
        self._apply_mode(self._mode)

    def _on_key_down(self, sender, e):
        if e.Key == Key.F5:
            self._rescan()
            e.Handled = True

    # ------------------------------------------------------------------
    # Layer list (shared by every mode)
    # ------------------------------------------------------------------

    def _store_selection(self):
        if self._rows:
            self._selected[self._mode] = set(r.name for r in self._rows if r.is_selected)

    def _restore_selection(self):
        chosen = self._selected.get(self._mode, set())
        for r in self._rows:
            r.is_selected = r.name in chosen

    def _count_kind(self):
        return geo.mode(self._mode)["count_kind"]

    def _update_counts(self):
        kind = self._count_kind()
        for r in self._rows:
            try:
                r.count = str(r.geom.count(kind))
            except Exception:
                r.count = "0"

    def _visible_rows(self):
        try:
            text = (self.txt_layer_search.Text or "").strip().lower()
        except Exception:
            text = ""
        if not text:
            return list(self._rows)
        return [r for r in self._rows if text in r.name.lower()]

    def _apply_filter(self):
        shown = self._visible_rows()
        self.set_items_source(self.grid_layers, shown)
        self.txt_layers_empty.Visibility = Visibility.Collapsed if shown else Visibility.Visible
        self._sync_header()
        self._update_tally()

    def _refresh_rows(self):
        try:
            self.grid_layers.Items.Refresh()
        except Exception:
            pass
        self._sync_header()
        self._update_tally()

    def _sync_header(self):
        try:
            self.sync_header_checkbox(self.chk_all_grid_layers, self.grid_layers, "is_selected")
        except Exception:
            pass

    def _update_tally(self):
        kind = self._count_kind()
        unit = geo.COUNT_HEADERS[kind].lower()
        selected = [r for r in self._rows if r.is_selected]
        total = 0
        for r in selected:
            try:
                total += int(r.count)
            except ValueError:
                pass
        text = u"{} of {} layers selected · {} {}".format(len(selected), len(self._rows), total, unit)
        shown = len(self._visible_rows())
        if shown != len(self._rows):
            text += u" · showing {}".format(shown)
        self.txt_layer_tally.Text = text

    def layer_search_changed(self, sender, e):
        self._apply_filter()

    def select_all_grid_layers_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_layers, "is_selected", sender.IsChecked)
        self._store_selection()
        self._refresh_rows()

    def layers_select_all_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_layers, "is_selected", True)
        self._store_selection()
        self._refresh_rows()

    def layers_clear_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_layers, "is_selected", False)
        self._store_selection()
        self._refresh_rows()

    def _on_row_checkbox_click(self, sender, e):
        self._store_selection()
        self._sync_header()
        self._update_tally()

    # ------------------------------------------------------------------
    # AI Select — semantic match of layer names (T3 AI Mode)
    # ------------------------------------------------------------------

    def ai_select_clicked(self, sender, e):
        if not self.ai_require():
            return
        names = [r.name for r in self._rows]
        if not names:
            show_info("There are no CAD layers to choose from.", title="AI Select",
                      details="Pick a CAD file under CAD SOURCE first.", owner=self)
            return
        mode_key = self._mode
        label = geo.mode(mode_key)["ai_label"]
        if mode_key == "mep":
            label = geo.mep_category(self._mep_key)["ai_label"]
        system_prompt = (
            "You are an expert BIM manager and CAD/Revit specialist. You read AutoCAD/DWG "
            "layer names and decide which layers hold a given building element category. "
            "Return JSON: {\"matched_layers\": [\"LAYER1\"], \"confidence\": 0.0-1.0, "
            "\"reasoning\": \"<one short English sentence>\"}.")
        prompt = ("Element category: {}\nCAD layer names in the drawing:\n{}\n\n"
                  "List every layer name that holds {}. Return only names from the list."
                  ).format(label, "\n".join("- " + n for n in names[:200]), label)
        self.ai_busy(self.btn_ai_select, True)
        self._set_status(u"AI is reading {} layer names for {}…".format(len(names), label))

        def worker():
            return self.ai_bridge.ask_json(prompt, system_prompt=system_prompt)

        def on_success(result):
            try:
                matched = set()
                if isinstance(result, dict):
                    matched = set(str(m).strip().lower() for m in result.get("matched_layers") or [])
                hits = [n for n in names if n.strip().lower() in matched]
                if self._mode == mode_key:
                    for r in self._rows:
                        if r.name in hits:
                            r.is_selected = True
                    self._store_selection()
                    self._refresh_rows()
                else:
                    self._selected[mode_key].update(hits)
                reason = result.get("reasoning", "") if isinstance(result, dict) else ""
                if hits:
                    self._set_status(u"AI ticked {} layer(s) for {}. {}".format(len(hits), label, reason))
                else:
                    self._set_status(u"AI found no layer that looks like {}. Tick the layers by hand."
                                     .format(label), "warning")
            finally:
                self.ai_busy(self.btn_ai_select, False)

        def on_error(err):
            self.ai_busy(self.btn_ai_select, False)
            self._set_status(u"AI Select failed: {}. Tick the layers by hand.".format(err), "danger")

        self.run_ai_async(worker, on_success=on_success, on_error=on_error)

    # ------------------------------------------------------------------
    # Run — plan, confirm (P5), create, report
    # ------------------------------------------------------------------

    def run_clicked(self, sender, e):
        self._store_selection()
        try:
            ctx = self._context()
            planners = {"wall": self._plan_wall, "floor": self._plan_floor,
                        "ceiling": self._plan_ceiling, "room": self._plan_room,
                        "column": self._plan_column, "beam": self._plan_beam,
                        "grid": self._plan_grid, "lines": self._plan_lines,
                        "mep": self._plan_mep}
            plan = planners[self._mode](ctx)
        except _InputError as ex:
            show_warning(str(ex), title=TITLE, owner=self)
            self._set_status(str(ex).splitlines()[0], "warning")
            return
        if plan.count <= 0:
            show_warning(u"Nothing to create: no {} found in the ticked layers.".format(plan.noun),
                         title=TITLE, details=plan.empty_msg, owner=self)
            self._set_status(u"No {} found in the ticked layers.".format(plan.noun), "warning")
            return
        if not confirm(plan.question, title=TITLE, ok_text=plan.ok_text,
                       details=plan.details, owner=self):
            self._set_status("Cancelled — nothing was created.", "warning")
            return
        self.begin_progress(max(plan.count, 1), disable=[self.btn_run, self.btn_refresh])
        try:
            result = plan.execute(self._progress)
        except Exception as ex:
            self.end_progress()
            self._set_status(u"Failed — nothing was created: {}".format(ex), "danger")
            show_error(u"Creating {} failed and the change was rolled back.".format(plan.noun),
                       title=TITLE, owner=self,
                       details=u"{}\n\nCheck the chosen type and level, then run again. If it "
                               u"keeps failing, read the latest Revit journal for the stack trace."
                               .format(ex))
            return
        self.end_progress()
        self._report(result)

    def _progress(self, i, total):
        step = max(1, total // 100)
        if i % step == 0 or i == total - 1:
            self.step_progress(i, u"Creating {} of {}…".format(i + 1, total))

    def _report(self, result):
        lines = [u"Created: {}".format(_counted(result.created, result.noun))]
        if result.failed:
            lines.append(u"Failed: {}".format(result.failed))
        if result.skipped:
            lines.append(u"Skipped: {}".format(result.skipped))
        if result.warnings:
            lines.append(u"Revit warnings dismissed: {}".format(result.warnings))
        lines.extend(result.notes)
        if result.errors:
            lines.append(u"First error: {}".format(result.errors[0]))
        details = u"\n".join(lines)
        level = self._pick("cmb_levels")
        where = u" on {}".format(level["name"]) if level and self._mode not in ("grid", "lines") else u""
        if result.created <= 0:
            self._set_status(u"No {} were created{} — nothing changed.".format(result.noun, where), "danger")
            show_warning(u"No {} were created; the model is unchanged.".format(result.noun),
                         title=TITLE, owner=self,
                         details=details + u"\n\nCheck the chosen type and the ticked layers, then run again.")
        elif result.failed:
            self._set_status(u"Created {}{} · {} failed.".format(
                _counted(result.created, result.noun), where, result.failed), "warning")
            show_warning(u"Created {}{}; {} could not be created.".format(
                _counted(result.created, result.noun), where, result.failed),
                title=TITLE, details=details, owner=self)
        else:
            self._set_status(u"Created {}{}. Ctrl+Z undoes the whole run.".format(
                _counted(result.created, result.noun), where))
            show_info(u"Created {}{}.".format(_counted(result.created, result.noun), where),
                      title=TITLE, details=details, owner=self)

    # ── shared inputs ──

    def _context(self):
        cad = self._pick("cmb_cad_files")
        if cad is None:
            raise _InputError(
                "No CAD file is selected.\nImport or link a DWG into this model, then pick it "
                "under CAD SOURCE.")
        layers = [r.geom for r in self._rows if r.is_selected]
        title = geo.mode(self._mode)["title"]
        if not layers:
            raise _InputError(
                u"No CAD layer is ticked for {}.\nTick the layers that hold the {} in the list "
                u"on the right, or use AI Select.".format(title, title.lower()))
        level = self._pick("cmb_levels")
        if level is None:
            raise _InputError("The model has no level.\nCreate a level, then run again.")
        return dict(cad=cad, layers=layers, level=level["element"], level_name=level["name"])

    def _num(self, box, label, default, minimum=None, maximum=None):
        value, ok = geo.parse_number(box.Text, default, minimum, maximum)
        if not ok:
            rng = ""
            if minimum is not None and maximum is not None:
                rng = " between {:g} and {:g}".format(minimum, maximum)
            elif minimum is not None:
                rng = " of at least {:g}".format(minimum)
            raise _InputError(u"{} is \"{}\", which is not a usable number.\nEnter a number{} "
                              u"in the options card, then run again.".format(label, box.Text, rng))
        return value

    def _need(self, name, what):
        item = self._pick(name)
        if item is None or item.get("element") is None:
            raise _InputError(u"No {} is available.\nLoad one into the model (or pick one in the "
                              u"options card), then run again.".format(what))
        return item

    @staticmethod
    def _segments(layers, merge):
        segs = [s for lg in layers for s in lg.segments]
        return geo.merge_collinear(segs) if merge else segs

    @staticmethod
    def _outline_groups(layers, with_holes):
        loops = [lp for lg in layers for lp in lg.loops()]
        polys = [geo.loop_polygon(lp) for lp in loops]
        keep = geo.dedupe_polygons(polys)
        loops = [loops[i] for i in keep]
        polys = [polys[i] for i in keep]
        if not with_holes:
            return [(lp, []) for lp in loops], polys, [[] for _ in loops]
        groups = geo.nest_loops(polys)
        return ([(loops[o], [loops[h] for h in holes]) for o, holes in groups],
                [polys[o] for o, _h in groups],
                [[polys[h] for h in holes] for _o, holes in groups])

    def _category(self, combo_name):
        cat = self._pick(combo_name)
        if cat is None:
            raise _InputError("Pick a part category in the options card, then run again.")
        return cat

    # ── Walls ──

    def _plan_wall(self, ctx):
        doc, level = self._doc, ctx["level"]
        height = self._num(self.txt_wall_height, "Height", 3000, 1)
        offset = self._num(self.txt_wall_offset, "Base offset", 0)
        default_thk = self._num(self.txt_wall_thickness, "Unpaired thickness", 200, 1, 3000)
        include = self._checked(self.chk_include_unpaired)
        segs = self._segments(ctx["layers"], self._checked(self.chk_merge_collinear))
        pairs, unpaired = geo.find_parallel_pairs(segs)
        items = [(c[0], c[1], c[2], c[3], c[4]) for c in pairs]
        if include:
            items += [(s[0], s[1], s[2], s[3], geo.mm(default_thk)) for s in unpaired]
        sizes = sorted(geo.group_by_size(items, lambda it: it[4]))
        details = [u"Wall pairs found: {}".format(len(pairs)),
                   u"Single lines: {} ({})".format(len(unpaired), "included" if include else "ignored"),
                   u"Thicknesses (mm): {}".format(", ".join(str(s) for s in sizes[:12]) or "—"),
                   u"Height {:g} mm · base offset {:g} mm".format(height, offset)]
        empty = ("No two parallel lines lie within 610 mm of each other. Tick the layers that hold "
                 "both faces of each wall, or turn on Include unpaired lines.")
        n = len(items)
        if self._checked(self.rb_wall_part_mode):
            cat = self._category("cmb_wall_part_category")
            profiles = []
            for (x0, y0, x1, y1, t) in items:
                rect = geo.centerline_rect(x0, y0, x1, y1, t / 2.0)
                lp = geo.loop_from_polygon(rect) if rect else None
                if lp:
                    profiles.append((lp, [], level.Elevation + geo.mm(offset), geo.mm(height)))
            details.append(u"Category: {}".format(cat["name"]))
            return Plan(len(profiles), "wall parts",
                        u"Create {} wall parts on {}?".format(len(profiles), ctx["level_name"]),
                        u"Create {} Parts".format(len(profiles)), u"\n".join(details),
                        lambda p: cr.create_parts(doc, profiles, cat["bic"],
                                                  "T3Lab: CAD to Wall Parts", p), empty)
        wtype = self._need("cmb_wall_type", "basic wall type")
        match = self._checked(self.chk_wall_match_thickness)
        details.append(u"Base type: {}{}".format(wtype["name"], " (copied per thickness)" if match else ""))
        structural = self._checked(self.chk_structural)
        return Plan(n, "walls", u"Create {} walls on {}?".format(n, ctx["level_name"]),
                    u"Create {} Walls".format(n), u"\n".join(details),
                    lambda p: cr.create_walls(doc, items, level, wtype["element"], geo.mm(height),
                                              geo.mm(offset), structural, match, p), empty)

    # ── Floors / Ceilings ──

    def _plan_floor(self, ctx):
        doc, level = self._doc, ctx["level"]
        offset = self._num(self.txt_floor_offset, "Height offset", 0)
        groups, _p, _h = self._outline_groups(ctx["layers"], self._checked(self.chk_floor_holes))
        n = len(groups)
        holes = sum(len(h) for _o, h in groups)
        details = [u"Outlines: {} · openings: {}".format(n, holes),
                   u"Height offset {:g} mm".format(offset)]
        empty = ("The ticked layers hold no closed outline. Use closed polylines or lines whose "
                 "ends meet, or tick the layer that holds the slab edges.")
        if self._checked(self.rb_part_mode):
            thk = self._num(self.txt_part_thickness, "Part thickness", 200, 1)
            cat = self._category("cmb_part_category")
            profiles = [(o, h, level.Elevation + geo.mm(offset), geo.mm(thk)) for o, h in groups]
            details.append(u"Part thickness {:g} mm · category {}".format(thk, cat["name"]))
            return Plan(n, "floor parts", u"Create {} floor parts on {}?".format(n, ctx["level_name"]),
                        u"Create {} Parts".format(n), u"\n".join(details),
                        lambda p: cr.create_parts(doc, profiles, cat["bic"],
                                                  "T3Lab: CAD to Floor Parts", p), empty)
        ftype = self._need("cmb_floor_type", "floor type")
        structural = self._checked(self.chk_floor_structural)
        details.append(u"Floor type: {}".format(ftype["name"]))
        return Plan(n, "floors", u"Create {} floors on {}?".format(n, ctx["level_name"]),
                    u"Create {} Floors".format(n), u"\n".join(details),
                    lambda p: cr.create_floors(doc, groups, ftype["id"], level, geo.mm(offset),
                                               structural, p), empty)

    def _plan_ceiling(self, ctx):
        doc, level = self._doc, ctx["level"]
        offset = self._num(self.txt_ceiling_offset, "Height offset", 2700)
        groups, _p, _h = self._outline_groups(ctx["layers"], self._checked(self.chk_ceiling_holes))
        n = len(groups)
        details = [u"Outlines: {} · openings: {}".format(n, sum(len(h) for _o, h in groups)),
                   u"Height offset {:g} mm".format(offset)]
        empty = ("The ticked layers hold no closed outline. Tick the layer that holds the ceiling "
                 "outlines (closed polylines or lines whose ends meet).")
        if self._checked(self.rb_ceiling_part_mode):
            thk = self._num(self.txt_ceiling_part_thickness, "Part thickness", 25, 1)
            cat = self._category("cmb_ceiling_part_category")
            profiles = [(o, h, level.Elevation + geo.mm(offset), geo.mm(thk)) for o, h in groups]
            details.append(u"Part thickness {:g} mm · category {}".format(thk, cat["name"]))
            return Plan(n, "ceiling parts", u"Create {} ceiling parts on {}?".format(n, ctx["level_name"]),
                        u"Create {} Parts".format(n), u"\n".join(details),
                        lambda p: cr.create_parts(doc, profiles, cat["bic"],
                                                  "T3Lab: CAD to Ceiling Parts", p), empty)
        ctype = self._need("cmb_ceiling_type", "ceiling type")
        details.append(u"Ceiling type: {}".format(ctype["name"]))
        return Plan(n, "ceilings", u"Create {} ceilings on {}?".format(n, ctx["level_name"]),
                    u"Create {} Ceilings".format(n), u"\n".join(details),
                    lambda p: cr.create_ceilings(doc, groups, ctype["id"], level, geo.mm(offset), p),
                    empty)

    # ── Rooms ──

    def _plan_room(self, ctx):
        doc, level = self._doc, ctx["level"]
        make_lines = not self._checked(self.rb_room_only)
        make_rooms = not self._checked(self.rb_room_lines)
        view = None
        if make_lines:
            item = self._pick("cmb_room_view")
            if item is None:
                raise _InputError(u"{} has no floor plan to hold room separation lines.\nCreate a "
                                  u"floor plan for this level, or choose Rooms only."
                                  .format(ctx["level_name"]))
            view = item["element"]
        edges = [e for lg in ctx["layers"] for e in lg.all_edges()] if make_lines else []
        points, too_small = [], 0
        if make_rooms:
            _g, polys, _h = self._outline_groups(ctx["layers"], False)
            points, too_small = geo.room_points(polys)
        name = (self.txt_room_name.Text or "").strip()
        skip = self._checked(self.chk_room_skip_existing)
        if make_rooms:
            n, noun = len(points), "rooms"
            question = u"Create {} rooms on {}{}?".format(
                n, ctx["level_name"],
                u" with {} room separation lines".format(len(edges)) if make_lines else u"")
            ok = u"Create {} Rooms".format(n)
        else:
            n, noun = len(edges), "room separation lines"
            question = u"Draw {} room separation lines on {}?".format(n, ctx["level_name"])
            ok = u"Draw {} Lines".format(n)
        details = [u"Closed outlines (one room each): {}{}".format(
                       len(points), u" · too small or too narrow for a room: {}".format(too_small)
                       if too_small else u"") if make_rooms else u"Rooms: none (lines only)",
                   u"Separation lines: {}{}".format(len(edges), u" in view " + view.Name if view else "")]
        empty = ("The ticked layers hold no closed outline to put a room in. Tick the wall or room "
                 "boundary layers, or choose Lines only.") if make_rooms else \
            "The ticked layers hold no linework."
        return Plan(n, noun, question, ok, u"\n".join(details),
                    lambda p: cr.create_rooms(doc, level, view, edges, points, make_lines,
                                              make_rooms, name, skip, p), empty)

    # ── Columns ──

    def _plan_column(self, ctx):
        doc, level = self._doc, ctx["level"]
        step = int(self._num(self.txt_column_rounding, "Size rounding", 10, 1, 500))
        height = self._num(self.txt_column_height, "Height", 3000, 1)
        rect_item = self._pick("cmb_column_type")
        round_item = self._pick("cmb_column_round_type")
        rect_sym = rect_item["element"] if rect_item else None
        round_sym = round_item["element"] if round_item else None
        fps = geo.filter_footprints([fp for lg in ctx["layers"] for fp in lg.footprints()])
        rects = [f for f in fps if f["shape"] == "rect"]
        rounds = [f for f in fps if f["shape"] == "round"]
        if rects and rect_sym is None:
            raise _InputError("No column type is loaded for rectangles.\nLoad a column family "
                              "(for example Concrete-Rectangular-Column), then run again.")
        items = (rects if rect_sym is not None else []) + (rounds if round_sym is not None else [])
        top = self._pick("cmb_column_top_level")
        top_level = top["element"] if top else None
        sizes = {}
        for f in items:
            nm = geo.footprint_type_name(f, step)
            sizes[nm] = sizes.get(nm, 0) + 1
        details = [u"Rectangles: {} · circles: {}{}".format(
            len(rects), len(rounds), u" (skipped — no round type chosen)" if rounds and round_sym is None else u""),
            u"Sizes: {}".format(u", ".join(u"{} ×{}".format(k, v) for k, v in sorted(sizes.items())[:8]) or u"—"),
            u"Top: {}".format(top["name"] if top_level is not None else u"unconnected, {:g} mm".format(height))]
        structural = self._checked(self.rb_column_structural)
        match = self._checked(self.chk_column_match_size)
        rotate = self._checked(self.chk_column_rotate)
        n = len(items)
        empty = ("The ticked layers hold no closed rectangle or circle 100–3000 mm across. Tick the "
                 "layer that holds the column outlines.")
        return Plan(n, "columns", u"Place {} columns on {}?".format(n, ctx["level_name"]),
                    u"Place {} Columns".format(n), u"\n".join(details),
                    lambda p: cr.create_columns(doc, items, level, top_level, geo.mm(height),
                                                rect_sym, round_sym, structural, match, rotate,
                                                step, p), empty)

    # ── Beams ──

    def _plan_beam(self, ctx):
        doc, level = self._doc, ctx["level"]
        offset = self._num(self.txt_beam_offset, "Top offset", -50)
        w_min = self._num(self.txt_beam_min_width, "Min width", 50, 1)
        w_max = self._num(self.txt_beam_max_width, "Max width", 1500, w_min)
        segs = self._segments(ctx["layers"], self._checked(self.chk_beam_merge))
        pairs, _unpaired = geo.find_parallel_pairs(segs, max_sep=geo.mm(w_max),
                                                   min_sep=geo.mm(w_min), min_overlap=0.7)
        items = []
        for (x0, y0, x1, y1, sep, _layer) in pairs:
            w = max(50, geo.round_to(geo.to_mm(sep), 50))
            items.append((x0, y0, x1, y1, w, geo.beam_height_for_width(w)))
        sizes = sorted(set("{}x{}".format(w, h) for *_xy, w, h in items))
        details = [u"Beam pairs found: {}".format(len(items)),
                   u"Sizes (mm): {}".format(", ".join(sizes[:10]) or "—"),
                   u"Top offset {:g} mm".format(offset)]
        empty = ("No two parallel lines {:g}–{:g} mm apart overlap enough to be a beam. Tick the "
                 "layer that holds both edges of each beam.".format(w_min, w_max))
        n = len(items)
        if self._checked(self.rb_beam_part_mode):
            cat = self._category("cmb_beam_part_category")
            top = level.Elevation + geo.mm(offset)
            profiles = []
            for (x0, y0, x1, y1, w, h) in items:
                rect = geo.centerline_rect(x0, y0, x1, y1, geo.mm(w) / 2.0)
                lp = geo.loop_from_polygon(rect) if rect else None
                if lp:
                    profiles.append((lp, [], top - geo.mm(h), geo.mm(h)))
            details.append(u"Category: {}".format(cat["name"]))
            return Plan(len(profiles), "beam parts",
                        u"Create {} beam parts on {}?".format(len(profiles), ctx["level_name"]),
                        u"Create {} Parts".format(len(profiles)), u"\n".join(details),
                        lambda p: cr.create_parts(doc, profiles, cat["bic"],
                                                  "T3Lab: CAD to Beam Parts", p), empty)
        sym = self._need("cb_beam_types", "structural framing type")
        match = self._checked(self.chk_beam_match_size)
        details.append(u"Type: {}{}".format(sym["name"], " (copied per size)" if match else ""))
        return Plan(n, "beams", u"Create {} beams on {}?".format(n, ctx["level_name"]),
                    u"Create {} Beams".format(n), u"\n".join(details),
                    lambda p: cr.create_beams(doc, items, level, sym["element"], geo.mm(offset),
                                              match, p), empty)

    # ── Grids ──

    def _plan_grid(self, ctx):
        doc = self._doc
        extend = self._num(self.txt_grid_extend, "Extend ends", 0, 0, 100000)
        min_len = self._num(self.txt_grid_min_length, "Min length", 1000, 0)
        auto = self._checked(self.rb_grid_autoname)
        start_number = int(self._num(self.txt_grid_start_number, "First number", 1, 0, 100000)) if auto else 1
        start_letter = (self.txt_grid_start_letter.Text or "A").strip() or "A"
        if auto and geo.letter_index(start_letter) == 0 and start_letter.upper() != "A":
            raise _InputError(u"First letter is \"{}\", which is not a grid letter.\nUse letters "
                              u"A–Z without I and O (for example A, C or AA).".format(start_letter))
        axes = geo.collapse_axis_lines(self._segments(ctx["layers"], False), min_len=geo.mm(min_len))
        skipped = 0
        existing, taken = cr.existing_grid_lines(doc)
        if self._checked(self.chk_grid_skip_existing):
            kept = [a for a in axes if not any(geo.same_axis(a, ex) for ex in existing)]
            skipped = len(axes) - len(kept)
            axes = kept
        names = geo.name_grids(axes, start_number, start_letter) if auto else None
        lines = [geo.extend_line(a[0], a[1], a[2], a[3], geo.mm(extend)) for a in axes]
        gtype = self._pick("cmb_grid_type")
        type_id = gtype["id"] if gtype else None
        numbers = [nm for nm in (names or []) if nm.isdigit()]
        letters = [nm for nm in (names or []) if not nm.isdigit()]
        details = [u"Axes found: {} · already in the model (skipped): {}".format(len(axes) + skipped, skipped)]
        if auto:
            if numbers:
                details.append(u"Numbered {}–{}".format(numbers and min(numbers, key=int), max(numbers, key=int)))
            if letters:
                details.append(u"Lettered {}–{}".format(geo.grid_letter(geo.letter_index(start_letter)),
                                                          geo.grid_letter(geo.letter_index(start_letter) + len(letters) - 1)))
        n = len(lines)
        empty = ("The ticked layers hold no straight axis line of at least {:g} mm{}. Tick the axis "
                 "layer.".format(min_len, ", or every axis already has a grid" if skipped else ""))
        return Plan(n, "grids", u"Create {} grids?".format(n), u"Create {} Grids".format(n),
                    u"\n".join(details),
                    lambda p: cr.create_grids(doc, lines, names, type_id, set(taken), p), empty)

    # ── Lines ──

    def _plan_lines(self, ctx):
        doc, level = self._doc, ctx["level"]
        detail = self._checked(self.rb_lines_detail)
        view, z = None, level.Elevation
        if detail:
            view = doc.ActiveView
            allowed = (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan,
                       DB.ViewType.EngineeringPlan, DB.ViewType.AreaPlan,
                       DB.ViewType.DraftingView)
            if view is None or view.ViewType not in allowed:
                raise _InputError(u"Detail lines need a plan or drafting view, but the active view "
                                  u"\"{}\" is not one.\nClose this window, open a floor plan or "
                                  u"drafting view, and run again — or choose Model lines."
                                  .format(view.Name if view is not None else "?"))
            z = 0.0
            try:
                if view.GenLevel is not None:
                    z = view.GenLevel.Elevation
            except Exception:
                pass
        else:
            z = level.Elevation + geo.mm(self._num(self.txt_lines_offset, "Offset from level", 0))
        merge = self._checked(self.chk_lines_merge)
        edges = []
        for lg in ctx["layers"]:
            segs = geo.merge_collinear(lg.segments) if merge else lg.segments
            edges.extend(("L", (s[0], s[1]), (s[2], s[3]), None) for s in segs)
            edges.extend(lg.arc_edges)
            edges.extend(lg.spline_edges)
            for (cx, cy, r) in lg.circles:
                edges.extend(geo.loop_from_circle(cx, cy, r))
        style = self._pick("cmb_line_style")
        gs = style["element"] if style else None
        noun = "detail lines" if detail else "model lines"
        where = u"in view {}".format(view.Name) if detail else u"on {}".format(ctx["level_name"])
        n = len(edges)
        details = [u"Curves: {}".format(n), u"Line style: {}".format(style["name"] if style else "default")]
        return Plan(n, noun, u"Create {} {} {}?".format(n, noun, where),
                    u"Create {} Lines".format(n), u"\n".join(details),
                    lambda p: cr.create_lines(doc, edges, view, z, gs, p),
                    "The ticked layers hold no linework.")

    # ── MEP runs ──

    def _plan_mep(self, ctx):
        doc, level = self._doc, ctx["level"]
        key = self._mep_key
        cat = geo.mep_category(key)
        tp = self._need("cmb_mep_type", cat["type_label"])
        system_id = None
        if cat["system"]:
            system_id = self._need("cmb_mep_system", "{} system type".format(cat["label"].lower()))["id"]
        width = self._num(self.txt_mep_width, cat["width_label"].title(), cat["width"], 1)
        height = None
        if cat["height"]:
            height = self._num(self.txt_mep_height, "Height", cat["height_mm"], 1)
        offset = self._num(self.txt_mep_offset, "Offset from level", cat["offset"])
        double = cat["double"] and self.cmb_mep_line_mode.SelectedIndex == 1
        segs = self._segments(ctx["layers"], self._checked(self.chk_mep_merge))
        unpaired = []
        if double:
            pairs, unpaired = geo.find_parallel_pairs(segs, max_sep=geo.mm(2500))
            segments = [(c[0], c[1], c[2], c[3], c[4]) for c in pairs]
        else:
            segments = [(s[0], s[1], s[2], s[3], None) for s in segs]
        n = len(segments)
        details = [u"{}: {}".format("Line pairs" if double else "Lines", n),
                   u"Type: {}".format(tp["name"]),
                   u"Size: {}".format("from the pair spacing" if double else u"{:g} mm".format(width)) +
                   (u" × {:g} mm".format(height) if height else u""),
                   u"Offset from level {:g} mm".format(offset)]
        if double and unpaired:
            details.append(u"Single lines ignored: {}".format(len(unpaired)))
        auto_elbow = self._checked(self.chk_mep_elbows)
        noun = cat["noun"]
        return Plan(n, noun, u"Create {} {} on {}?".format(n, noun, ctx["level_name"]),
                    u"Create {} {}".format(n, cat["verb"][len("Create "):]), u"\n".join(details),
                    lambda p: cr.create_mep_runs(doc, key, segments, level, geo.mm(offset), tp["id"],
                                                 system_id, geo.mm(width),
                                                 geo.mm(height) if height else None, auto_elbow,
                                                 noun, "T3Lab: CAD to {}".format(cat["label"] + "s"), p),
                    "The ticked layers hold no straight line." if not double else
                    "No parallel line pairs found. Switch to Single line or tick the layer with both edges.")


# ===========================================================================
# PUBLIC ENTRY POINT
# ===========================================================================

def show_cad_to_elements(script_dir=None, revit_app=None):
    """Called by the pushbutton script.py (arguments kept for compatibility)."""
    try:
        CADToElementsWindow().ShowDialog()
    except Exception as ex:
        import traceback
        show_error(u"CAD to Elements could not open.", title=TITLE,
                   details=u"{}\n\n{}".format(ex, traceback.format_exc()))
