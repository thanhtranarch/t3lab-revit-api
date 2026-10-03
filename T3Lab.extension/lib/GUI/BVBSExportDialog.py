# -*- coding: utf-8 -*-
"""
BVBSExportDialog.py
===================
WPF dialog for BVBS Export: write ``.abs`` files (BVBS BF2D) for bending
machines from shape-driven rebar.

A wrong leg length is a wrong bar on the machine, so the window works in this
order and never skips a step:

1. **Self-test** when it opens - the writer must reproduce the BVBS guideline's
   reference lines byte for byte, otherwise Export stays disabled (E-BVBS-01).
2. **Preview** - every rebar set is read (centre-line -> legs -> outer legs, see
   ``Snippets/_rebar.py``) and listed per mark with the legs that WILL be
   written, so they can be compared with the bending schedule. Bars that cannot
   be written (free-form 3D, curved legs, no diameter, no mark) are listed as
   ``Skipped`` with the reason, never dropped silently.
3. **Write + verify** - each file goes to a temporary name, is read back and
   checked (checksum, structure, identical to the record it came from), and only
   then moved into place. "Verified" is only reported for files read back clean.

Read-only with respect to the model: no transaction is ever opened. The writer,
checksum, weight chain and file naming are pure Python in ``Snippets/_bvbs.py``
(unit-tested outside Revit); this module is the Revit and WPF glue.

Part of T3Lab Extension.
"""

import json
import os
import re

import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from System.Windows import Visibility, RoutedEventHandler
from System.Windows.Controls import CheckBox
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
from System import Uri, UriKind

from GUI.WPF_Base import T3WPFWindow
from GUI.T3Dialog import confirm as t3_confirm, show_error, show_info, show_warning

from Snippets import _assembly, _bvbs, _rebar
from Snippets._compat import bar_mass_per_metre, eid_value, make_eid, short_error, to_mm
from Snippets._host import resolve_uidoc

GUI_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'BVBSExport.xaml')
DIALOG_TITLE = "BVBS Export"

DEFAULT_PLAN = "MODEL"
DEFAULT_REVISION = "a"
DEFAULT_GRADE = "B500B"

SCOPE_MODEL = "model"
SCOPE_SELECTION = "selection"
SCOPE_ASSEMBLY = "assembly"
SCOPE_PARTITION = "partition"

SEV_OK = "Success"
SEV_WARN = "Warning"
SEV_FAIL = "Danger"

PROGRESS_EVERY = 5            # pump the UI every N bars while reading centre-lines
LENGTH_TOLERANCE_MM = 1.0     # sets sharing a mark must agree on length within this


def _natural_key(text):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", u"%s" % text)]


def _plural(count, word):
    return u"%d %s%s" % (count, word, u"" if count == 1 else u"s")


def _settings_path():
    from core.paths import user_data_path
    return user_data_path("bvbs_export.json")


# ── ROW ──────────────────────────────────────────────────────────────────────

class BarRow(object):
    """One preview line: a distinct leg chain under one mark, or a skipped set.

    Plain attributes only - the grid reads them through WPF bindings and the
    checkbox column through the string bridge (rule 24).
    """

    def __init__(self, mark, status, severity, record=None, diameter_mm=0.0, quantity=0,
                 length_mm=None, legs=u"", detail=u"", group=u"", ids=(), reason=u""):
        self.is_selected = False
        self.mark = mark
        self.dia_text = u"%d" % int(round(diameter_mm)) if diameter_mm else u"—"
        self.qty_text = u"%d" % quantity
        self.length_text = u"%d" % int(round(length_mm)) if length_mm else u"—"
        self.legs_text = legs or u"—"
        self.StatusText = status
        self.Severity = severity
        self.record = record
        self.quantity = quantity
        self.detail = detail
        self.group = group
        self.rebar_ids = list(ids)
        self.reason = reason

    @property
    def exportable(self):
        return self.record is not None and self.Severity != SEV_FAIL


# ── DIALOG ───────────────────────────────────────────────────────────────────

class BVBSExportDialog(T3WPFWindow):
    """Main window class for BVBS Export."""

    def __init__(self, doc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc
        self._loading = True
        self._is_busy = False
        self._loaded_once = False
        self._partial = False             # the last preview was stopped half-way
        self._index = None                # RebarIndex, built once per window
        self._assemblies = []
        self._asm_choices = []            # AssemblyRecord behind each cb_assembly item
        self._partition_choices = []      # partition text behind each cb_partition item
        self._asm_marks = {}
        self._by_assembly = {}            # assembly id -> [RebarRecord]
        self._rows = []
        self._notes = []
        self._weight_counts = {}
        self._weight_table = _bvbs.load_weight_table()
        self._selftest_ok = True
        self._settings = self._read_settings()

        self._load_logo()
        self._wire_row_events()
        self._run_selftest()
        self._init_fields()
        self._loading = False
        self._update_summary()
        self._arm_first_load()

    # ── SETUP ────────────────────────────────────────────────────────────────

    def _load_logo(self):
        """Load and bind the T3Lab logo to title bar and window icon."""
        try:
            logo_path = os.path.join(GUI_DIR, 'T3Lab_logo.png')
            if os.path.exists(logo_path):
                bitmap = BitmapImage()
                bitmap.BeginInit()
                bitmap.CacheOption = BitmapCacheOption.OnLoad
                bitmap.UriSource = Uri(logo_path, UriKind.Absolute)
                bitmap.EndInit()
                bitmap.Freeze()
                if hasattr(self, 'logo_image') and self.logo_image:
                    self.logo_image.Source = bitmap
                self.Icon = bitmap
        except Exception:
            pass

    def _wire_row_events(self):
        """Row checkbox clicks bubble to the grid; a handler inside the
        DataTemplate would never be wired (own namescope)."""
        try:
            self.grid_preview.AddHandler(
                CheckBox.ClickEvent, RoutedEventHandler(self.grid_preview_checkbox_clicked), True)
        except Exception:
            pass

    def _run_selftest(self):
        """E-BVBS-01: refuse to export when the writer drifted from the guideline."""
        ok, message = _bvbs.self_test()
        self._selftest_ok = ok
        if not ok:
            self.txt_selftest.Text = message
            self.selftest_box.Visibility = Visibility.Visible
            self.btn_export.IsEnabled = False
            self.btn_preview.IsEnabled = False
            self._set_status(u"Export is disabled — the BVBS self-test failed.", SEV_FAIL)

    def _read_settings(self):
        try:
            with open(_settings_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_settings(self):
        """Per-user convenience only: a failure here must never stop an export."""
        try:
            data = {"folder": self.tb_folder.Text.strip(),
                    "grade": self.tb_grade.Text.strip(),
                    "revision": self.tb_revision.Text.strip()}
            with open(_settings_path(), "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
        except Exception:
            pass

    def _default_folder(self):
        saved = self._settings.get("folder")
        if saved and os.path.isdir(saved):
            return saved
        try:
            model_path = self.doc.PathName
            if model_path and os.path.isdir(os.path.dirname(model_path)):
                return os.path.dirname(model_path)
        except Exception:
            pass
        documents = os.path.join(os.path.expanduser("~"), "Documents")
        return documents if os.path.isdir(documents) else os.path.expanduser("~")

    def _init_fields(self):
        project = u""
        try:
            project = self.doc.ProjectInformation.Number or u""
        except Exception:
            pass
        self.tb_project.Text = project
        self.tb_plan.Text = DEFAULT_PLAN
        self.tb_revision.Text = self._settings.get("revision") or DEFAULT_REVISION
        self.tb_grade.Text = self._settings.get("grade") or DEFAULT_GRADE
        self.tb_folder.Text = self._default_folder()
        self.chk_only_shape_driven.IsChecked = True
        self.chk_per_assembly.IsChecked = False
        self.set_items_source(self.grid_preview, [])

    def _arm_first_load(self):
        """Show the window first, then read the model: a progress bar nobody can
        see is no progress bar. Falls back to loading straight away."""
        if not self._selftest_ok:
            return
        try:
            self.ContentRendered += self._on_content_rendered
        except Exception:
            self._first_load()

    def _on_content_rendered(self, sender, e):
        self._first_load()

    # ── SHARED HELPERS ───────────────────────────────────────────────────────

    def _set_status(self, text, level=SEV_OK):
        self.status_text.Text = text
        key = {SEV_OK: "T3.Success.Accent", SEV_WARN: "T3.Warning.Accent",
               SEV_FAIL: "T3.Danger.Accent"}.get(level, "T3.Success.Accent")
        try:
            self.dot_status.Fill = self.FindResource(key)
        except Exception:
            pass

    def _scope(self):
        if self.rb_scope_selection.IsChecked:
            return SCOPE_SELECTION
        if self.rb_scope_assembly.IsChecked:
            return SCOPE_ASSEMBLY
        if self.rb_scope_partition.IsChecked:
            return SCOPE_PARTITION
        return SCOPE_MODEL

    def _busy_controls(self):
        return [self.btn_export, self.btn_preview, self.btn_folder, self.cb_assembly,
                self.cb_partition, self.rb_scope_model, self.rb_scope_selection,
                self.rb_scope_assembly, self.rb_scope_partition,
                self.chk_only_shape_driven, self.chk_per_assembly]

    def _progress_callback(self, label):
        """progress(index, total, text) for build_rebar_index; False stops it."""
        state = {"total": None}

        def callback(index, total, text=None):
            if state["total"] != total:
                state["total"] = total
                self._update_progress(0, maximum=max(total, 1))
            return self.step_progress(index, u"%s — %d / %d" % (label, index, total))
        return callback

    # ── FIRST LOAD: INDEX, ASSEMBLIES, PARTITIONS ────────────────────────────

    def _first_load(self):
        if self._loaded_once or self._is_busy:
            return
        self._loaded_once = True
        if self.doc is None:
            show_warning(u"Open a Revit project before running BVBS Export.", title=DIALOG_TITLE)
            return
        self._is_busy = True
        self.begin_progress(maximum=1, disable=self._busy_controls())
        try:
            self._index = _rebar.build_rebar_index(
                self.doc, progress=self._progress_callback(u"Reading reinforcement"))
            try:
                self._assemblies = _assembly.collect_assemblies(self.doc)
            except Exception as exc:
                self._assemblies = []
                show_warning(u"Assemblies could not be read, so the Assembly scope is empty. %s"
                             % short_error(exc), title=DIALOG_TITLE)
            self._build_maps()
        except Exception as exc:
            self._index = None
            show_error(u"The reinforcement could not be read from the model. %s Close this "
                       u"window and try again." % short_error(exc), title=DIALOG_TITLE)
        finally:
            self.end_progress()
            self._is_busy = False
        if self._index is None:
            self._update_summary()
            return
        if self._index.stopped:
            self._partial = True
            self._set_status(u"Reading was stopped — refresh the preview to read the whole "
                             u"model.", SEV_WARN)
        self._fill_scope_combos()
        self._pick_initial_scope()
        self._rebuild_preview()

    def _build_maps(self):
        self._asm_marks = {a.id: a.mark for a in self._assemblies}
        self._by_assembly = {}
        for row in self._index.rows:
            if row.assembly_id is not None and row.assembly_id >= 0:
                self._by_assembly.setdefault(row.assembly_id, []).append(row)

    def _fill_scope_combos(self):
        self._loading = True
        try:
            self._asm_choices = sorted(self._assemblies,
                                       key=lambda a: (_natural_key(a.mark), a.id))
            labels = [u"%s (%d)" % (a.mark or u"(no mark)", a.id) for a in self._asm_choices]
            self.set_items_source(self.cb_assembly, labels)
            if labels:
                self.cb_assembly.SelectedIndex = 0

            counts = {}
            for row in self._index.rows:
                if row.kind == "Rebar" and row.partition:
                    counts[row.partition] = counts.get(row.partition, 0) + 1
            self._partition_choices = sorted(counts, key=_natural_key)
            self.set_items_source(self.cb_partition, [u"%s (%d)" % (p, counts[p])
                                                      for p in self._partition_choices])
            if self._partition_choices:
                self.cb_partition.SelectedIndex = 0
        finally:
            self._loading = False

    def _selection_ids(self):
        try:
            uidoc = resolve_uidoc()
            return [eid_value(item) for item in uidoc.Selection.GetElementIds()]
        except Exception:
            return []

    def _pick_initial_scope(self):
        """Open on the selection when something rebar-related is selected."""
        if self._selection_rows():
            self._loading = True
            try:
                self.rb_scope_selection.IsChecked = True
            finally:
                self._loading = False
        self._update_scope_controls()

    def _update_scope_controls(self):
        scope = self._scope()
        self.cb_assembly.IsEnabled = scope == SCOPE_ASSEMBLY and bool(self._asm_choices)
        self.cb_partition.IsEnabled = scope == SCOPE_PARTITION and bool(self._partition_choices)

    # ── SCOPE ────────────────────────────────────────────────────────────────

    def _assembly_rows(self, assembly):
        """Rebar that are members of the assembly plus rebar hosted by its members (A1)."""
        out, seen = [], set()
        candidates = list(self._by_assembly.get(assembly.id, ()))
        for member in assembly.members:
            candidates.extend(self._index.by_host.get(member, ()))
        for row in candidates:
            if row.id not in seen:
                seen.add(row.id)
                out.append(row)
        return out

    def _selection_rows(self):
        """Rebar for the selection: rebar itself, the rebar of selected hosts, the
        rebar of selected assemblies. Unique, in selection order."""
        if self._index is None:
            return []
        assemblies = {a.id: a for a in self._assemblies}
        out, seen = [], set()

        def add(rows):
            for row in rows:
                if row.id not in seen:
                    seen.add(row.id)
                    out.append(row)

        for eid in self._selection_ids():
            row = self._index.by_id.get(eid)
            if row is not None:
                add([row])
            if eid in self._index.by_host:
                add(self._index.by_host[eid])
            assembly = assemblies.get(eid)
            if assembly is not None:
                add(self._assembly_rows(assembly))
        return out

    def _scope_rows(self):
        """(rows in scope, group label forced for all of them or None)."""
        if self._index is None:
            return [], None
        scope = self._scope()
        if scope == SCOPE_SELECTION:
            return self._selection_rows(), None
        if scope == SCOPE_ASSEMBLY:
            at = self.cb_assembly.SelectedIndex
            if 0 <= at < len(self._asm_choices):
                assembly = self._asm_choices[at]
                return self._assembly_rows(assembly), (assembly.mark or u"")
            return [], None
        if scope == SCOPE_PARTITION:
            at = self.cb_partition.SelectedIndex
            if 0 <= at < len(self._partition_choices):
                wanted = self._partition_choices[at]
                return [r for r in self._index.rows if r.partition == wanted], None
            return [], None
        return list(self._index.rows), None

    # ── PREVIEW: READ EVERY BAR ──────────────────────────────────────────────

    def _existing_positions(self, element):
        try:
            count = int(element.NumberOfBarPositions)
        except Exception:
            count = 0
        if count <= 0:
            return [0]
        out = []
        for position in range(count):
            try:
                if element.DoesBarExistAtPosition(position):
                    out.append(position)
            except Exception:
                out.append(position)
        return out or [0]

    def _shape_at(self, element, position, diameter_mm):
        """BarShape of one bar position; an unreadable centre-line is a skip reason."""
        try:
            curves = _rebar.centerline_points(element, position)
        except Exception as exc:
            return _bvbs.BarShape(reason=u"centre-line unreadable", error=short_error(exc))
        return _bvbs.chain_to_shape(curves, diameter_mm)

    def _read_shapes(self, element, diameter_mm, quantity):
        """[(BarShape, count)] for a rebar set.

        A small set is read bar by bar. A big one is sampled (first / middle /
        last, R11) and counted as uniform when the samples agree; when they
        differ every bar is read and grouped, so a set with varying lengths
        becomes one record per distinct shape with its own count.
        Returns (groups, positions read, positions in the set).
        """
        existing = self._existing_positions(element)
        sample = _bvbs.sample_positions(existing)
        shapes = [self._shape_at(element, p, diameter_mm) for p in sample]
        agree = len({(s.key(), s.reason) for s in shapes}) == 1
        if len(sample) < len(existing) and not agree:
            shapes = [self._shape_at(element, p, diameter_mm) for p in existing]
            return self._group_with_failures(shapes), len(existing), len(existing)
        if len(sample) < len(existing):
            count = quantity if quantity and quantity > 0 else len(existing)
            return [(shapes[0], count)], len(sample), len(existing)
        return self._group_with_failures(shapes), len(existing), len(existing)

    @staticmethod
    def _group_with_failures(shapes):
        """Distinct good shapes with their counts, then one entry per failure reason."""
        groups = _bvbs.group_distinct([s for s in shapes if s.ok])
        failed, order = {}, []
        for shape in shapes:
            if shape.ok:
                continue
            if shape.reason not in failed:
                failed[shape.reason] = [shape, 0]
                order.append(shape.reason)
            failed[shape.reason][1] += 1
        groups.extend((failed[reason][0], failed[reason][1]) for reason in order)
        return groups

    @staticmethod
    def _revit_length_mm(element):
        """Per-bar length Revit schedules ("Bar Length"), mm; None when unreadable."""
        try:
            from Autodesk.Revit.DB import BuiltInParameter
            built_in = getattr(BuiltInParameter, "REBAR_ELEM_LENGTH", None)
            if built_in is None:
                return None
            param = element.get_Parameter(built_in)
            if param is None or not param.HasValue:
                return None
            value = to_mm(param.AsDouble())
            return value if value > 0 else None
        except Exception:
            return None

    def _bar_type_info(self, element, bar_type, cache):
        """(mandrel mm or None, kg/m or None, weight source or None), cached per type + style."""
        style = getattr(element, "Style", None)
        style_name = u"%s" % style if style is not None else u""
        key = (eid_value(bar_type.Id) if bar_type is not None else -1, style_name)
        if key not in cache:
            roll = None
            if bar_type is not None:
                roll = _rebar.mandrel_diameter_mm(bar_type, style)
            value, source = bar_mass_per_metre(bar_type)
            cache[key] = (roll, value, source)
        return cache[key]

    @staticmethod
    def _skip_row(row, mark, reason, detail=u""):
        return BarRow(mark or (u"(id %d)" % row.id), u"Skipped: %s" % reason, SEV_FAIL,
                      diameter_mm=row.diameter_mm, quantity=max(int(row.quantity or 1), 1),
                      detail=detail or (u"Element %d — %s." % (row.id, reason)),
                      ids=[row.id], reason=reason)

    def _read_one(self, row, group, cache):
        """(entries, skip rows) for one rebar set.

        A set whose bars differ becomes one entry per distinct shape; bars that
        cannot be written come back as skip rows, never silently dropped.
        """
        mark = _bvbs.build_mark(row.partition, row.number, row.mark)
        if not row.diameter_mm or row.diameter_mm <= 0:
            return [], [self._skip_row(row, mark, u"no diameter")]
        if not mark:
            return [], [self._skip_row(
                row, u"", u"no mark",
                u"Element %d has no Rebar Number and no Schedule Mark — number the "
                u"reinforcement in Revit first." % row.id)]
        element = self.doc.GetElement(make_eid(row.id))
        if element is None:
            return [], [self._skip_row(row, mark, u"element missing")]

        bar_type = _rebar.bar_type_of(self.doc, element)
        roll, rev_value, rev_source = self._bar_type_info(element, bar_type, cache)
        roll_assumed = not roll or roll <= 0
        if roll_assumed:
            roll = 4.0 * row.diameter_mm       # BVBS standard mandrel: 4 x diameter
        revit_length = self._revit_length_mm(element)

        groups, read, total = self._read_shapes(element, row.diameter_mm, row.quantity)
        bars_in_groups = sum(count for _, count in groups)
        entries, skips = [], []
        for shape, count in groups:
            if not shape.ok:
                detail = u"Element %d — %s" % (row.id, shape.reason)
                if shape.error:
                    detail += u": %s" % shape.error
                if len(groups) > 1:
                    detail += u" (%d of %d bars)" % (count, bars_in_groups)
                skip = self._skip_row(row, mark, shape.reason, detail + u".")
                skip.quantity = count
                skip.qty_text = u"%d" % count
                skips.append(skip)
                continue
            entries.append({
                "mark": mark, "group": group, "diameter": row.diameter_mm, "shape": shape,
                "count": count, "ids": [row.id], "roll": roll, "roll_assumed": roll_assumed,
                "rev_value": rev_value, "rev_source": rev_source,
                "lengths": [revit_length] if (revit_length and len(groups) == 1) else [],
                "read": read, "total": total})
        return entries, skips

    def _merge_entries(self, entries):
        """One entry per (assembly, mark, diameter, shape): quantities add up, as on a schedule."""
        merged, order = {}, []
        for entry in entries:
            key = (entry["group"], entry["mark"], int(round(entry["diameter"] * 10)),
                   entry["shape"].key())
            if key not in merged:
                merged[key] = entry
                order.append(key)
                continue
            target = merged[key]
            target["count"] += entry["count"]
            target["ids"].extend(entry["ids"])
            target["lengths"].extend(entry["lengths"])
            target["read"] += entry["read"]
            target["total"] += entry["total"]
        return [merged[key] for key in order]

    def _entry_to_row(self, entry, reused):
        """BarRow + BarRecord for one merged entry."""
        shape = entry["shape"]
        diameter = entry["diameter"]
        lengths = entry["lengths"]
        revit_length = None
        if lengths and max(lengths) - min(lengths) <= LENGTH_TOLERANCE_MM:
            revit_length = sum(lengths) / len(lengths)
        length_ok, length_note = _bvbs.length_check(
            revit_length, shape.outer_sum, shape.bends, diameter, entry["roll"])
        if revit_length and length_ok:
            length_mm, length_source = revit_length, u"Revit bar length"
        else:
            length_mm, length_source = shape.outer_sum, u"sum of the outer legs"

        kg_per_m, source = _bvbs.resolve_weight(
            diameter, entry["rev_value"], entry["rev_source"], self._weight_table)
        weight = _bvbs.bar_weight_kg(length_mm, kg_per_m)
        self._weight_counts[source] = self._weight_counts.get(source, 0) + entry["count"]

        record = _bvbs.BarRecord(
            entry["mark"], diameter, entry["count"], shape.segments,
            weight_kg=weight, roll_diameter_mm=entry["roll"], total_length_mm=length_mm,
            group=entry["group"])
        status, severity, selected = u"Ready", SEV_OK, True
        if not length_ok:
            status, severity, selected = u"Check legs", SEV_WARN, False
        elif reused:
            status, severity, selected = u"Same mark, other shape", SEV_WARN, False

        ids = sorted(set(entry["ids"]))
        parts = [u"Element%s %s" % (u"" if len(ids) == 1 else u"s",
                                    u", ".join(str(i) for i in ids[:5])
                                    + (u" +%d more" % (len(ids) - 5) if len(ids) > 5 else u"")),
                 u"centre-line legs %s" % _bvbs.legs_text(shape.centre_legs),
                 u"outer legs %s" % _bvbs.legs_text(shape.segments),
                 u"length %d mm (%s)" % (int(round(length_mm)), length_source),
                 u"Ø%d, mandrel %d mm%s" % (
                     int(round(diameter)), int(round(entry["roll"])),
                     u" (assumed 4 x Ø: the bar type has no bend diameter)"
                     if entry["roll_assumed"] else u""),
                 u"%.3f kg/m from %s" % (kg_per_m, _bvbs.WEIGHT_SOURCE_LABELS.get(source, source))]
        if entry["read"] < entry["total"]:
            parts.append(u"read %d of %d bars, the rest assumed identical"
                         % (entry["read"], entry["total"]))
        if not length_ok:
            parts.append(length_note + u" — compare with the bending schedule, then tick it")
        if reused:
            parts.append(u"this mark has more than one shape in the model — check the "
                         u"numbering, then tick it")
        row = BarRow(entry["mark"], status, severity, record=record, diameter_mm=diameter,
                     quantity=entry["count"], length_mm=length_mm,
                     legs=_bvbs.legs_text(shape.segments), detail=u" · ".join(parts),
                     group=entry["group"], ids=ids)
        row.is_selected = selected
        return row

    def _rebuild_preview(self):
        """Read every bar in the current scope and fill the grid. Never raises."""
        if self._index is None or self._is_busy or not self._selftest_ok:
            return
        self._is_busy = True
        self._partial = False
        self._weight_counts = {}
        self._notes = []
        try:
            self._rows = self._read_scope()
        except Exception as exc:
            self._rows = []
            show_error(u"The preview could not be built. %s Choose another scope or refresh and "
                       u"try again." % short_error(exc), title=DIALOG_TITLE)
        finally:
            self._is_busy = False
        self._show_rows()

    def _read_scope(self):
        rows_in_scope, forced_group = self._scope_rows()
        only_shape_driven = bool(self.chk_only_shape_driven.IsChecked)

        sets = [r for r in rows_in_scope if r.kind == "Rebar"]
        others = len(rows_in_scope) - len(sets)
        hidden = 0
        if only_shape_driven:
            hidden = sum(1 for r in sets if not r.is_shape_driven)
            sets = [r for r in sets if r.is_shape_driven]
        self._notes = []
        if others:
            self._notes.append(
                u"%s (area, path, fabric, coupler or in-system rebar) are outside BVBS BF2D and "
                u"are not read." % _plural(others, u"element"))
        if hidden:
            self._notes.append(
                u"%s hidden by “Shape-driven rebar only”." % _plural(hidden, u"free-form set"))

        entries, skips = [], []
        cache = {}
        self.begin_progress(maximum=max(len(sets), 1), disable=self._busy_controls())
        try:
            for number, row in enumerate(sets, 1):
                if number % PROGRESS_EVERY == 1:
                    if not self.step_progress(
                            number, u"Reading centre-lines — %d / %d" % (number, len(sets))):
                        self._partial = True
                        break
                group = forced_group if forced_group is not None \
                    else self._asm_marks.get(row.assembly_id, u"")
                try:
                    found, skipped = self._read_one(row, group, cache)
                except Exception as exc:
                    found, skipped = [], [self._skip_row(
                        row, _bvbs.build_mark(row.partition, row.number, row.mark),
                        u"unexpected error", u"Element %d — %s" % (row.id, short_error(exc)))]
                entries.extend(found)
                skips.extend(skipped)
        finally:
            self.end_progress()

        merged = self._merge_entries(entries)
        shapes_per_mark = {}
        for entry in merged:
            shapes_per_mark.setdefault((entry["group"], entry["mark"]), set()).add(
                (int(round(entry["diameter"] * 10)), entry["shape"].key()))
        ready = [self._entry_to_row(e, len(shapes_per_mark[(e["group"], e["mark"])]) > 1)
                 for e in merged]
        ready.sort(key=lambda r: (_natural_key(r.group), _natural_key(r.mark)))
        skips.sort(key=lambda r: (_natural_key(r.mark), r.reason))
        return ready + skips

    # ── GRID & SUMMARY ───────────────────────────────────────────────────────

    def _show_rows(self):
        self.set_items_source(self.grid_preview, self._rows)
        self.grid_preview_empty.Visibility = Visibility.Collapsed if self._rows \
            else Visibility.Visible
        self.txt_weight_source.Text = _bvbs.weight_caption(self._weight_counts)
        notes = list(self._notes)
        if self._partial:
            notes.insert(0, u"The preview was stopped before every bar was read — refresh it "
                            u"before exporting.")
        self.txt_note.Text = u" ".join(notes)
        self.txt_note.Visibility = Visibility.Visible if notes else Visibility.Collapsed
        self.txt_row_detail.Text = u"Select a row to see where its numbers come from."
        self._update_summary()

    def _ticked(self):
        return [r for r in self._rows if r.is_selected and r.exportable]

    def _update_summary(self, keep_status=False):
        """Counts, button label and status line from the current rows.

        ``keep_status`` leaves the status line alone - used right after an
        export, whose result must not be overwritten by "Ready".
        """
        ticked = self._ticked()
        bars = sum(r.quantity for r in ticked)
        exportable = [r for r in self._rows if r.exportable]
        skipped = sum(r.quantity for r in self._rows if not r.exportable)
        to_check = sum(1 for r in exportable if not r.is_selected)
        self.btn_export_label.Text = u"Export %s" % _plural(bars, u"bar")
        self.btn_export.IsEnabled = (bool(ticked) and self._selftest_ok and not self._partial
                                     and not self._is_busy)
        self.btn_preview.IsEnabled = self._selftest_ok and not self._is_busy
        self._sync_header()
        if self._index is None or not self._selftest_ok or self._is_busy:
            return

        if self._rows:
            parts = [u"%s in %s" % (_plural(sum(r.quantity for r in exportable), u"bar"),
                                    _plural(len(exportable), u"mark"))]
            if skipped:
                parts.append(u"%d skipped" % skipped)
            if to_check:
                parts.append(u"%d to check" % to_check)
            self.txt_preview_count.Text = u" · ".join(parts)
        else:
            self.txt_preview_count.Text = u"Nothing to preview"

        if keep_status:
            return
        if self._partial:
            self._set_status(u"Preview incomplete — refresh it before exporting.", SEV_WARN)
        elif not self._rows:
            self._set_status(u"Ready — no rebar in this scope.", SEV_WARN)
        else:
            text = u"Ready — %s in %s ticked" % (_plural(bars, u"bar"),
                                                  _plural(len(ticked), u"mark"))
            if skipped:
                text += u" · %d skipped" % skipped
            self._set_status(text, SEV_WARN if (skipped or to_check) else SEV_OK)

    def _sync_header(self):
        """Header checkbox: ticked when every writable row is, unticked when none is."""
        try:
            writable = [r for r in self._rows if r.exportable]
            ticked = sum(1 for r in writable if r.is_selected)
            if not writable or ticked == 0:
                state = False
            elif ticked == len(writable):
                state = True
            else:
                state = None
            header = getattr(self, "chk_all_grid_preview", None) \
                or self.FindName("chk_all_grid_preview")
            header.IsChecked = state
        except Exception:
            pass

    # ── EVENT HANDLERS: WINDOW ───────────────────────────────────────────────

    def close_button_clicked(self, sender=None, e=None):
        # The title-bar X (and Esc) is the only close control: it must not close
        # the window under a running scan or export.
        if self._is_busy:
            return
        self.Close()

    # ── EVENT HANDLERS: SCOPE & OPTIONS ──────────────────────────────────────

    def scope_changed(self, sender, e):
        if self._loading:
            return
        self._update_scope_controls()
        self._rebuild_preview()

    def assembly_changed(self, sender, e):
        if self._loading:
            return
        if self._scope() == SCOPE_ASSEMBLY:
            at = self.cb_assembly.SelectedIndex
            if 0 <= at < len(self._asm_choices) and self._plan_is_default_text():
                self.tb_plan.Text = self._asm_choices[at].mark or DEFAULT_PLAN
            self._rebuild_preview()

    def partition_changed(self, sender, e):
        if self._loading:
            return
        if self._scope() == SCOPE_PARTITION:
            self._rebuild_preview()

    def _plan_is_default_text(self):
        """True while tb_plan still holds the default or an assembly mark we put there."""
        text = self.tb_plan.Text.strip()
        return text in ("", DEFAULT_PLAN) or text in {a.mark for a in self._assemblies}

    def shape_filter_changed(self, sender, e):
        if self._loading:
            return
        self._rebuild_preview()

    def option_changed(self, sender, e):
        # One file per assembly only changes file naming; the preview stays valid.
        self._update_summary()

    def preview_clicked(self, sender, e):
        if self._is_busy:
            return
        self._rebuild_preview()

    # ── EVENT HANDLERS: GRID ─────────────────────────────────────────────────

    def grid_preview_checkbox_clicked(self, sender, e):
        # A row that cannot be written must never stay ticked.
        stale = [row for row in self._rows if not row.exportable and row.is_selected]
        for row in stale:
            row.is_selected = False
        if stale:
            try:
                self.grid_preview.Items.Refresh()
            except Exception:
                pass
        self._update_summary()

    def select_all_grid_preview_clicked(self, sender, e):
        self.toggle_all_rows(self.grid_preview, "is_selected", sender.IsChecked)
        for row in self._rows:
            if not row.exportable:
                row.is_selected = False
        try:
            self.grid_preview.Items.Refresh()
        except Exception:
            pass
        self._update_summary()

    def grid_preview_selection_changed(self, sender, e):
        row = self.grid_preview.SelectedItem
        if row is None:
            self.txt_row_detail.Text = u"Select a row to see where its numbers come from."
            return
        try:
            self.txt_row_detail.Text = row.detail
        except Exception:
            pass

    # ── EVENT HANDLERS: FOLDER & EXPORT ──────────────────────────────────────

    def folder_clicked(self, sender, e):
        try:
            import _cpython_bootstrap
            _cpython_bootstrap.install_forms_shim()
        except Exception:
            pass
        try:
            from pyrevit import forms
            folder = forms.pick_folder(title=u"Choose the folder for the .abs files")
        except Exception as exc:
            show_warning(u"The folder picker could not open. %s Type the folder path into the "
                         u"Output folder box instead." % short_error(exc), title=DIALOG_TITLE)
            return
        if folder:
            self.tb_folder.Text = folder

    def _apply_fields(self, records):
        project = self.tb_project.Text.strip()
        plan = self.tb_plan.Text.strip()
        revision = self.tb_revision.Text.strip()
        grade = self.tb_grade.Text.strip()
        for record in records:
            record.project = project
            record.plan = plan
            record.index = revision
            record.grade = grade
        return plan

    def _skipped_summary(self):
        """' · 4 skipped (free-form 3D, curved leg)' or '' when nothing was skipped."""
        counts = {}
        for row in self._rows:
            if not row.exportable:
                reason = row.reason or u"skipped"
                counts[reason] = counts.get(reason, 0) + row.quantity
        if not counts:
            return u""
        return u" · %d skipped (%s)" % (sum(counts.values()), u", ".join(sorted(counts)))

    def export_clicked(self, sender, e):
        if self._is_busy or not self._selftest_ok:
            return
        rows = self._ticked()
        if not rows:
            show_warning(u"Nothing to export. Tick at least one ready row in the preview, or "
                         u"choose another scope.", title=DIALOG_TITLE)
            return
        if self._partial:
            show_warning(u"The preview was stopped before every bar was read. Refresh the preview "
                         u"first so the file holds the whole scope.", title=DIALOG_TITLE)
            return

        folder = self.tb_folder.Text.strip()
        if not folder or not os.path.isdir(folder):
            show_warning(u"The output folder does not exist: %s. Choose an existing folder with "
                         u"“Choose folder…”." % (folder or u"(empty)"),
                         title=DIALOG_TITLE)
            return

        records = [r.record for r in rows]
        plan = self._apply_fields(records)
        files = _bvbs.plan_files(records, bool(self.chk_per_assembly.IsChecked),
                                 plan or DEFAULT_PLAN)
        paths = [(os.path.join(folder, name), recs) for name, recs in files]
        existing = [path for path, _ in paths if os.path.exists(path)]
        if existing:
            names = u"\n".join(os.path.basename(p) for p in existing[:8])
            if len(existing) > 8:
                names += u"\n… and %d more" % (len(existing) - 8)
            if not t3_confirm(
                    u"Overwrite %s in %s?" % (_plural(len(existing), u"existing file"), folder),
                    title=DIALOG_TITLE, details=names,
                    ok_text=u"Overwrite %s" % _plural(len(existing), u"file"), danger=True):
                return
        self._write_all(paths, folder)

    def _write_all(self, paths, folder):
        """Write each file (tmp -> verify -> move) and report what really happened."""
        self._is_busy = True
        self.begin_progress(maximum=max(len(paths), 1), disable=self._busy_controls())
        written_files, failed_files, not_written = [], [], []
        bars = 0
        try:
            for number, (path, recs) in enumerate(paths, 1):
                if not self.step_progress(
                        number - 1, u"Writing %s — %d / %d"
                        % (os.path.basename(path), number, len(paths))):
                    not_written = [os.path.basename(p) for p, _ in paths[number - 1:]]
                    break
                try:
                    _, _, problems = _bvbs.write_verified(path, recs)
                except Exception as exc:
                    problems = [u"unexpected error: %s" % short_error(exc)]
                if problems:
                    failed_files.append((os.path.basename(path), problems))
                else:
                    written_files.append(path)
                    bars += sum(r.quantity for r in recs)
        finally:
            self.end_progress()
            self._is_busy = False

        if written_files:
            self._save_settings()
        self._report_export(written_files, failed_files, not_written, bars, folder)
        self._update_summary(keep_status=True)

    def _report_export(self, written_files, failed_files, not_written, bars, folder):
        if failed_files:
            lines = []
            for name, problems in failed_files:
                lines.append(u"%s:" % name)
                lines.extend(u"  • %s" % problem for problem in problems[:4])
            count = _plural(len(failed_files), u"file")
            self._set_status(u"%s failed read-back verification — not written." % count, SEV_FAIL)
            show_error(
                u"%s failed the read-back check, so it was not written and any earlier file of "
                u"the same name was left untouched. Do not send these files to a machine — "
                u"report this to T3Lab with the details below." % count,
                title=DIALOG_TITLE, details=u"\n".join(lines))
            return
        if not written_files:
            self._set_status(u"Nothing was written — export stopped.", SEV_WARN)
            return
        skipped = self._skipped_summary()
        status = u"Wrote %s to %s%s · verified" % (
            _plural(bars, u"bar"), _plural(len(written_files), u"file"), skipped)
        if not_written:
            status += u" · stopped before %s" % _plural(len(not_written), u"file")
        self._set_status(status, SEV_WARN if (skipped or not_written) else SEV_OK)
        details = u"\n".join(written_files)
        if not_written:
            details += u"\n\nNot written (stopped): " + u", ".join(not_written)
        show_info(
            u"%s. Every file was read back from %s and its checksums verified." % (status, folder),
            title=DIALOG_TITLE, details=details)


def show_bvbs_export(doc):
    """Entry point used by the pushbutton."""
    BVBSExportDialog(doc).ShowDialog()
