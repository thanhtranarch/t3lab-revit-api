# -*- coding: utf-8 -*-
"""
RebarWizardDialog.py
====================
Rebar Wizard window (spec dev/plan/rebar-tekla-implementation-spec.md 5.6).

Three chip tabs share one host list:

* **Beam**        — bottom / top bars + stirrups in three zones.
* **Column**      — corner + side verticals, ties dense top/bottom, cross ties.
* **Pad footing** — two-layer bottom mesh, optional 90° end hooks.

Hosts come from the Revit selection (rule A1: rebar and assemblies count as
their hosts) or from Pick in model. Every host is measured once
(``_rebar_wizard.read_section``); anything that is not a rectangular straight
beam, a rectangular vertical column or a rectangular pad footing becomes an
"Out of scope" row with its reason — never an exception (decision D7).

Create runs one TransactionGroup "T3Lab: Rebar Wizard" with one Transaction
per host inside ``_rebar_wizard.create_plans`` (A6, D9), so Ctrl+Z undoes the
whole click. The window is modal (D8). Layout math and Revit calls live in
``Snippets/_rebar_wizard.py``; this module is UI only.

Part of T3Lab Extension.
"""

__author__ = "Tran Tien Thanh"

import os

import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from System import Uri, UriKind
from System.Windows import Visibility
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption

from GUI.WPF_Base import T3WPFWindow, to_items_source
from GUI.T3Dialog import confirm as t3_confirm, show_warning as t3_warning

from Snippets import _rebar_wizard as wiz
from Snippets._assembly import select_in_revit, STATUS_FAILED, STATUS_OK
from Snippets._compat import disposing, eid_value, elem_name, make_eid, short_error

GUI_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'RebarWizard.xaml')
TITLE = "Rebar Wizard"

TAB_KINDS = (wiz.KIND_BEAM, wiz.KIND_COLUMN, wiz.KIND_FOOTING)

# Form controls of each tab: WizardInput field -> x:Name.
FORM = {
    wiz.KIND_BEAM: (
        ("bot_dia", "cb_bm_bot_dia"), ("bot_n", "tb_bm_bot_n"),
        ("top_dia", "cb_bm_top_dia"), ("top_n", "tb_bm_top_n"),
        ("stir_dia", "cb_bm_stir_dia"), ("s_end", "tb_bm_s_end"),
        ("l_end", "tb_bm_l_end"), ("s_mid", "tb_bm_s_mid"),
        ("cover", "tb_bm_cover"), ("hook", "cb_bm_hook"),
        ("add_assembly", "chk_bm_add_assembly"),
    ),
    wiz.KIND_COLUMN: (
        ("vert_dia", "cb_col_vert_dia"), ("side_n", "tb_col_side_n"),
        ("tie_dia", "cb_col_tie_dia"), ("s_dense", "tb_col_s_dense"),
        ("l_dense", "tb_col_l_dense"), ("s_mid", "tb_col_s_mid"),
        ("cover", "tb_col_cover"), ("hook", "cb_col_hook"),
        ("cross_tie", "chk_col_cross_tie"), ("add_assembly", "chk_col_add_assembly"),
    ),
    wiz.KIND_FOOTING: (
        ("x_dia", "cb_ft_x_dia"), ("x_s", "tb_ft_x_s"),
        ("y_dia", "cb_ft_y_dia"), ("y_s", "tb_ft_y_s"),
        ("cover", "tb_ft_cover"), ("hooks", "chk_ft_hooks"),
        ("add_assembly", "chk_ft_add_assembly"),
    ),
}

# Results rows that describe the host, not a rebar set.
_INFO_ROWS = (u"Assembly", u"Warnings")


def _plural(count, kind):
    one, many = wiz.KIND_LABELS.get(kind, ("host", "hosts"))
    return u"%d %s" % (count, one if count == 1 else many)


# ── ROW ITEMS ────────────────────────────────────────────────────────────────

class HostRow(object):
    """One host in grid_hosts. Every cell is a str (string bridge, spec 4.3)."""

    def __init__(self, element_id, name, section, assembly_mark, in_assembly):
        self.element_id = element_id
        self.section = section
        self.kind = section.kind
        self.in_assembly = bool(in_assembly)
        self.plans = []
        self.done = False               # rebar already created by this window
        self.problem = u""              # LayoutError text, shown in txt_validation
        self._name = name
        self._assembly = assembly_mark or (u"—" if not in_assembly else u"(unnamed)")
        self._status = u"Ready"
        self._severity = "Success"
        if not section.in_scope:
            self.set_status(u"Out of scope: %s" % section.reason, "Danger")

    @property
    def in_scope(self):
        return self.section.in_scope

    @property
    def HostName(self):
        return self._name

    @property
    def SectionText(self):
        return self.section.label() if self.in_scope else u"—"

    @property
    def LengthText(self):
        if not self.in_scope or self.kind == wiz.KIND_FOOTING:
            return u"—"
        return wiz.fmt_mm(round(self.section.length))

    @property
    def AssemblyText(self):
        return self._assembly

    @property
    def StatusText(self):
        return self._status

    @property
    def Severity(self):
        return self._severity

    def set_status(self, text, severity="Success"):
        self._status = text
        self._severity = severity


# ── WINDOW ───────────────────────────────────────────────────────────────────

class RebarWizardDialog(T3WPFWindow):
    """Main window class for Rebar Wizard."""

    def __init__(self, doc, types=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self._loading = True
        self._busy = False
        self.doc = doc
        self._kind = wiz.KIND_BEAM
        self._rows = []
        self._created_ids = []
        self._bars, self._hooks = types if types is not None else wiz.resolve_types(doc)
        self._dia_values = []           # nominal Ø (mm) behind every diameter combo
        self._hook_names = []           # names behind every hook combo
        self._presets = {}              # stored presets {name: {kind: dict}}
        self._preset_names = []
        self._preset_path = None

        self._load_logo()
        self._load_types()
        self._load_presets()
        self._write_all(wiz.default_preset())
        ids = self._selection_ids()
        note = self._load_hosts(ids, source=u"selection") if ids else None
        self._loading = False
        self._recompute()
        if note and self._rows:
            self._set_status(*note)

    # ── SETUP ────────────────────────────────────────────────────────────────

    def _load_logo(self):
        try:
            logo_path = os.path.join(GUI_DIR, 'T3Lab_logo.png')
            if os.path.exists(logo_path):
                bitmap = BitmapImage()
                bitmap.BeginInit()
                bitmap.CacheOption = BitmapCacheOption.OnLoad
                bitmap.UriSource = Uri(logo_path, UriKind.Absolute)
                bitmap.EndInit()
                bitmap.Freeze()
                if getattr(self, 'logo_image', None) is not None:
                    self.logo_image.Source = bitmap
                self.Icon = bitmap
        except Exception:
            pass

    def _brush(self, key):
        try:
            return self.FindResource(key)
        except Exception:
            return None

    def _control(self, name):
        try:
            return self.FindName(name)
        except Exception:
            return getattr(self, name, None)

    def _load_types(self):
        """Fill every diameter combo and hook combo from the model's types."""
        self._dia_values = sorted(self._bars)
        dia_labels = [u"Ø%s — %s" % (wiz.fmt_mm(d), elem_name(self._bars[d]))
                      for d in self._dia_values]
        self._hook_names = [wiz.NO_HOOK] + sorted(self._hooks)
        for kind in TAB_KINDS:
            for field, name in FORM[kind]:
                control = self._control(name)
                if control is None:
                    continue
                if field.endswith("_dia"):
                    control.ItemsSource = to_items_source(list(dia_labels))
                elif field == "hook":
                    control.ItemsSource = to_items_source(list(self._hook_names))

    # ── FORM <-> WizardInput ─────────────────────────────────────────────────

    def _nearest_dia_index(self, diameter):
        if not self._dia_values:
            return -1
        try:
            target = float(diameter)
        except (TypeError, ValueError):
            return 0
        best = min(range(len(self._dia_values)),
                   key=lambda i: abs(self._dia_values[i] - target))
        return best

    def _hook_index(self, name):
        if name in self._hook_names:
            return self._hook_names.index(name)
        default = wiz.default_hook_name(self._hooks)
        return self._hook_names.index(default) if default in self._hook_names else 0

    def _read_form(self, kind):
        """WizardInput from the controls of one tab (unparsable text is kept for validate)."""
        inp = wiz.WizardInput(kind)
        for field, name in FORM[kind]:
            control = self._control(name)
            if control is None:
                continue
            if field.endswith("_dia"):
                index = control.SelectedIndex
                inp.set(field, self._dia_values[index]
                        if 0 <= index < len(self._dia_values) else u"")
            elif field == "hook":
                index = control.SelectedIndex
                inp.set(field, self._hook_names[index]
                        if 0 <= index < len(self._hook_names) else wiz.NO_HOOK)
            elif name.startswith("chk_"):
                inp.set(field, control.IsChecked is True)
            else:
                inp.set(field, control.Text)
        return inp

    def _write_form(self, kind, values):
        """Put a {field: value} dict into the controls of one tab."""
        inp = wiz.WizardInput.from_dict(kind, values)
        for field, name in FORM[kind]:
            control = self._control(name)
            if control is None:
                continue
            value = getattr(inp, field)
            if field.endswith("_dia"):
                control.SelectedIndex = self._nearest_dia_index(value)
            elif field == "hook":
                control.SelectedIndex = self._hook_index(value)
            elif name.startswith("chk_"):
                control.IsChecked = bool(value)
            else:
                control.Text = wiz.fmt_mm(value) if value is not None else u""

    def _write_all(self, preset):
        was = self._loading
        self._loading = True
        try:
            for kind in TAB_KINDS:
                self._write_form(kind, (preset or {}).get(kind, {}))
        finally:
            self._loading = was

    # ── PRESETS ──────────────────────────────────────────────────────────────

    def _load_presets(self, select=None):
        try:
            self._preset_path = wiz.preset_path()
            self._presets = wiz.load_presets(self._preset_path)
        except Exception:
            self._preset_path = None
            self._presets = {}
        stored = sorted(n for n in self._presets if n != wiz.DEFAULT_PRESET)
        self._preset_names = [wiz.DEFAULT_PRESET] + stored
        was = self._loading
        self._loading = True
        try:
            self.cb_preset.ItemsSource = to_items_source(list(self._preset_names))
            self.cb_preset.SelectedIndex = (self._preset_names.index(select)
                                            if select in self._preset_names else 0)
        finally:
            self._loading = was

    def _preset_values(self, name):
        if name in self._presets:
            return self._presets[name]
        return wiz.default_preset()

    def _selected_preset(self):
        index = self.cb_preset.SelectedIndex
        if 0 <= index < len(self._preset_names):
            return self._preset_names[index]
        return wiz.DEFAULT_PRESET

    # ── HOSTS ────────────────────────────────────────────────────────────────

    def _uidoc(self):
        try:
            from Snippets._host import resolve_uidoc
            return resolve_uidoc()
        except Exception:
            return None

    def _selection_ids(self):
        uidoc = self._uidoc()
        if uidoc is None:
            return []
        try:
            return list(uidoc.Selection.GetElementIds())
        except Exception:
            return []

    def _load_hosts(self, element_ids, source):
        """Measure every host behind `element_ids` and rebuild the rows.

        Returns (status text, severity) for the caller to show after _recompute.
        """
        try:
            elements, ignored = wiz.hosts_from_ids(self.doc, element_ids)
        except Exception as exc:
            return (u"Could not read the %s: %s. Select the hosts again."
                    % (source, short_error(exc)), "Danger")
        rows = []
        for element in elements:
            section = wiz.read_section(self.doc, element)
            try:
                in_assembly = eid_value(element.AssemblyInstanceId) >= 0
            except Exception:
                in_assembly = False
            name = u"%s [%d]" % (elem_name(element), eid_value(element.Id))
            rows.append(HostRow(eid_value(element.Id), name, section,
                                wiz.assembly_mark(self.doc, element), in_assembly))
        if not rows:
            # Keep the current list: an empty pick should not wipe the user's hosts.
            return (u"No beam, column or footing in the %s (%d other element(s) ignored). "
                    u"Select hosts, their rebar or their assemblies." % (source, ignored),
                    "Warning")
        self._rows = rows
        self._created_ids = []
        self.btn_select.IsEnabled = False
        # Open the tab that has the most hosts in scope.
        counts = dict((k, sum(1 for r in rows if r.kind == k and r.in_scope))
                      for k in TAB_KINDS)
        best = max(TAB_KINDS, key=lambda k: counts[k])
        if counts[best] and counts.get(self._kind, 0) == 0:
            self._switch_tab(best)
        out = sum(1 for r in rows if not r.in_scope)
        note = u" · %d out of scope" % out if out else u""
        note += u" · %d other element(s) ignored" % ignored if ignored else u""
        return (u"Loaded %d host(s) from the %s%s." % (len(rows), source, note),
                "Warning" if out else "Success")

    def _visible_rows(self):
        return [r for r in self._rows if r.kind == self._kind or r.kind not in TAB_KINDS]

    def _ready_rows(self):
        return [r for r in self._rows
                if r.kind == self._kind and r.in_scope and not r.done and r.plans]

    def _hosts_text(self):
        if not self._rows:
            return u"No hosts yet"
        parts = []
        for kind in TAB_KINDS:
            n = sum(1 for r in self._rows if r.kind == kind and r.in_scope)
            if n:
                parts.append(_plural(n, kind))
        out = sum(1 for r in self._rows if not r.in_scope)
        if out:
            parts.append(u"%d out of scope" % out)
        return u" · ".join(parts)

    # ── RECOMPUTE ────────────────────────────────────────────────────────────

    def _recompute(self):
        """Form -> WizardInput -> plans per host -> statuses, summary, primary label."""
        if getattr(self, '_loading', True):
            return
        kind = self._kind
        inp = self._read_form(kind)
        problems = inp.validate()
        first_fit = u""
        for row in self._rows:
            if row.kind != kind or not row.in_scope or row.done:
                continue
            row.plans = []
            row.problem = u""
            if problems:
                row.set_status(u"Fix the form values", "Warning")
                continue
            try:
                row.plans = wiz.plan_for(row.section, inp)
                row.set_status(u"Ready — %d sets" % len(row.plans), "Success")
            except wiz.LayoutError as exc:
                row.problem = u"%s" % exc
                row.set_status(u"Does not fit", "Danger")
                if not first_fit:
                    first_fit = u"%s: %s" % (row.HostName, row.problem)

        message = u""
        if problems:
            more = len(problems) - 1
            message = problems[0] + (u" (+%d more)" % more if more > 0 else u"")
        elif first_fit:
            message = first_fit
        elif not any(r.plans for r in self._rows if r.kind == kind and not r.done):
            outside = [r for r in self._visible_rows() if not r.in_scope]
            if outside:
                # E-SCOPE-WIZ (spec 4.4)
                message = (u"%s is out of the V1 scope: %s. Rectangular straight beams and "
                           u"columns, and pad footings, are supported."
                           % (outside[0].HostName, outside[0].section.reason))
        self.txt_validation.Text = message
        self.txt_validation.Visibility = Visibility.Visible if message else Visibility.Collapsed

        ready = self._ready_rows()
        n_sets = sum(len(r.plans) for r in ready)
        if ready:
            first = ready[0]
            size = first.section.label()
            if kind != wiz.KIND_FOOTING:
                size += u" × %s" % wiz.fmt_mm(round(first.section.length))
            summary = u"Per %s (%s mm): %s" % (wiz.KIND_LABELS[kind][0], size,
                                              wiz.summarize(first.plans))
            if len(ready) > 1:
                summary += u"\nTotal: %d rebar sets, %d bars in %s." % (
                    n_sets, sum(wiz.total_bars(r.plans) for r in ready),
                    _plural(len(ready), kind))
        elif any(r.kind == kind and r.done for r in self._rows):
            summary = u"Every %s in the list already has its rebar. Use selection or Pick " \
                      u"in model to load more hosts." % wiz.KIND_LABELS[kind][0]
        else:
            summary = u"Pick %s to see the bars this preset creates." % wiz.KIND_LABELS[kind][1]
        self.txt_summary.Text = summary

        assembly_box = self._control(dict(FORM[kind])["add_assembly"])
        if assembly_box is not None:
            assembly_box.IsEnabled = any(r.in_assembly for r in self._rows if r.kind == kind)

        label = u"Create rebar for %s" % _plural(len(ready), kind) if ready else u"Create rebar"
        self.txt_create_label.Text = label
        self.btn_create.IsEnabled = bool(ready) and not problems and not self._busy

        self.txt_hosts.Text = self._hosts_text()
        self._refresh_grid()
        if not self._busy and ready:
            self._set_status(u"Ready — %s, %d rebar sets" % (_plural(len(ready), kind), n_sets),
                             "Success")
        elif not self._busy and not self._rows:
            self._set_status(u"Ready — select %s in Revit or press Pick in model."
                             % wiz.KIND_LABELS[kind][1], "Success")

    def _refresh_grid(self):
        rows = self._visible_rows()
        self.set_items_source(self.grid_hosts, rows)
        try:
            self.grid_hosts.Items.Refresh()
        except Exception:
            pass
        words = wiz.KIND_LABELS[self._kind]
        self.grid_hosts_empty.Text = (u"No %s yet.\nSelect %s in the model or press Pick in model."
                                      % (words[1], words[1]))
        self.grid_hosts_empty.Visibility = Visibility.Collapsed if rows else Visibility.Visible

    def _set_status(self, text, severity=None):
        self.status_text.Text = text
        if severity:
            brush = self._brush("T3.%s.Accent" % severity)
            if brush is not None:
                self.dot_status.Fill = brush

    def _switch_tab(self, kind):
        chip = {wiz.KIND_BEAM: self.chip_tab_beam, wiz.KIND_COLUMN: self.chip_tab_column,
                wiz.KIND_FOOTING: self.chip_tab_footing}[kind]
        self._kind = kind
        self.tab_control.SelectedIndex = TAB_KINDS.index(kind)
        chip.IsChecked = True

    # ── CREATE ───────────────────────────────────────────────────────────────

    def _host_outcome(self, row, results):
        """(status text, severity, sets created) for one host from its Result rows."""
        plan_rows = [r for r in results if r.name not in _INFO_ROWS]
        failed = [r for r in plan_rows if r.status == STATUS_FAILED]
        if failed:
            # The row that caused the rollback carries the Revit message.
            cause = [r for r in failed if r.detail != u"rolled back with the host"]
            return u"Failed: %s" % (cause or failed)[0].detail, "Danger", 0
        sets = sum(1 for r in plan_rows if r.status == STATUS_OK)
        assembly = [r for r in results if r.name == u"Assembly"]
        if assembly and assembly[0].status == STATUS_FAILED:
            return u"Created %d sets · not in assembly" % sets, "Warning", sets
        if assembly:
            return u"Created %d sets · in assembly" % sets, "Success", sets
        return u"Created %d sets" % sets, "Success", sets

    def _run_create(self, rows, inp):
        from Autodesk.Revit.DB import TransactionGroup
        types = (self._bars, self._hooks)
        created = []
        totals = {"hosts": 0, "sets": 0, "failed": 0, "skipped": 0}
        disable = [self.btn_create, self.btn_select, self.btn_pick, self.btn_use_selection,
                   self.btn_preset_save, self.btn_preset_delete, self.cb_preset]
        self._busy = True
        self.begin_progress(len(rows), disable=disable)
        try:
            with disposing(TransactionGroup(self.doc, u"T3Lab: Rebar Wizard")) as group:
                group.Start()
                stopped_at = None
                for index, row in enumerate(rows):
                    message = u"Creating rebar — %d / %d · %s" % (index + 1, len(rows), row.HostName)
                    if not self.step_progress(index, message):
                        stopped_at = index
                        break
                    host = self.doc.GetElement(make_eid(row.element_id))
                    if host is None:
                        row.set_status(u"Failed: host no longer exists", "Danger")
                        totals["failed"] += 1
                        continue
                    results = wiz.create_plans(self.doc, host, row.section, row.plans, types,
                                               add_to_assembly=inp.add_assembly,
                                               created=created, host_label=row.HostName)
                    text, severity, sets = self._host_outcome(row, results)
                    row.set_status(text, severity)
                    if severity == "Danger":
                        totals["failed"] += 1
                    else:
                        row.done = True
                        totals["hosts"] += 1
                        totals["sets"] += sets
                if stopped_at is not None:
                    for row in rows[stopped_at:]:
                        row.set_status(u"Skipped: stopped", "Warning")
                        totals["skipped"] += 1
                self.step_progress(len(rows))
                if created:
                    group.Assimilate()
                else:
                    group.RollBack()
        except Exception as exc:
            self.end_progress()
            self._busy = False
            self._set_status(u"Rebar Wizard stopped: %s. Nothing was kept - check the model "
                             u"and try again." % short_error(exc), "Danger")
            t3_warning(u"Rebar Wizard could not finish, so the whole run was undone.",
                       title=TITLE, details=short_error(exc), owner=self)
            self._recompute()
            return
        self.end_progress()
        self._busy = False
        self._created_ids = created
        self.btn_select.IsEnabled = bool(created)
        severity = "Danger" if totals["failed"] else ("Warning" if totals["skipped"] else "Success")
        self._recompute()
        self._set_status(u"Created %d rebar sets in %s · %d skipped · %d failed"
                         % (totals["sets"], _plural(totals["hosts"], self._kind),
                            totals["skipped"], totals["failed"]), severity)

    # ── EVENT HANDLERS ───────────────────────────────────────────────────────

    def close_button_clicked(self, sender=None, e=None):
        # The title-bar X (and Esc) is the only close control: never under a run.
        if self._busy:
            return
        self.Close()

    def tab_chip_checked(self, sender, e):
        if getattr(self, '_loading', True) and not hasattr(self, '_rows'):
            return
        try:
            index = int(sender.Tag)
        except Exception:
            index = 0
        self._kind = TAB_KINDS[index]
        self.tab_control.SelectedIndex = index
        self._recompute()

    def field_changed(self, sender, e):
        self._recompute()

    def use_selection_clicked(self, sender, e):
        if self._busy:
            return
        ids = self._selection_ids()
        if not ids:
            self._set_status(u"Nothing is selected in Revit. Select beams, columns or footings "
                             u"(or their rebar or assemblies), then press Use selection.",
                             "Warning")
            return
        note = self._load_hosts(ids, source=u"selection")
        self._recompute()
        self._set_status(*note)

    def pick_clicked(self, sender, e):
        if self._busy:
            return
        uidoc = self._uidoc()
        if uidoc is None:
            self._set_status(u"Cannot reach the Revit UI to pick elements. Close this window, "
                             u"select the hosts, then open Rebar Wizard again.", "Danger")
            return
        from Autodesk.Revit.UI.Selection import ObjectType
        picked = None
        self.Hide()
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, wiz.host_filter(),
                "Pick beams, columns or pad footings, then press Finish")
            picked = [r.ElementId for r in refs]
        except Exception as exc:
            # OperationCanceledException (Esc) keeps the current list.
            if type(exc).__name__ != "OperationCanceledException":
                self._set_status(u"Pick in model failed: %s" % short_error(exc), "Danger")
        finally:
            self.Show()
        if picked:
            note = self._load_hosts(picked, source=u"pick")
            self._recompute()
            self._set_status(*note)

    def preset_changed(self, sender, e):
        if getattr(self, '_loading', True):
            return
        name = self._selected_preset()
        self._write_all(self._preset_values(name))
        self._recompute()
        self._set_status(u"Preset \"%s\" loaded." % name)

    def preset_save_clicked(self, sender, e):
        if self._busy:
            return
        values = {}
        for kind in TAB_KINDS:
            inp = self._read_form(kind)
            problems = inp.validate()
            if problems:
                self._set_status(u"Not saved: %s tab — %s" % (
                    wiz.KIND_LABELS[kind][0].capitalize(), problems[0]), "Danger")
                return
            values[kind] = inp.to_dict()
        current = self._selected_preset()
        try:
            from pyrevit import forms
            name = forms.ask_for_string(
                default=current if current != wiz.DEFAULT_PRESET else u"My preset",
                prompt=u"Preset name (saves the Beam, Column and Pad footing tabs):",
                title=u"Save preset")
        except Exception as exc:
            self._set_status(u"Could not ask for a preset name: %s" % short_error(exc), "Danger")
            return
        name = (name or u"").strip()
        if not name:
            return
        if name == wiz.DEFAULT_PRESET:
            self._set_status(u"\"Default\" is the built-in preset and cannot be overwritten. "
                             u"Save under another name.", "Warning")
            return
        if self._preset_path is None:
            self._set_status(u"Presets cannot be saved: the T3Lab settings folder is not "
                             u"available on this machine.", "Danger")
            return
        if name in self._presets and not t3_confirm(
                u"Replace the preset \"%s\"?" % name, title=u"Save preset",
                ok_text=u"Replace preset", owner=self):
            return
        presets = dict(self._presets)
        presets[name] = values
        error = wiz.save_presets(self._preset_path, presets)
        if error:
            self._set_status(u"Preset not saved: %s. Check that %s is writable."
                             % (error, self._preset_path), "Danger")
            return
        self._load_presets(select=name)
        self._set_status(u"Saved preset \"%s\" to %s." % (name, self._preset_path), "Success")

    def preset_delete_clicked(self, sender, e):
        if self._busy:
            return
        name = self._selected_preset()
        if name not in self._presets:
            self._set_status(u"\"%s\" is the built-in preset and cannot be deleted." % name,
                             "Warning")
            return
        if not t3_confirm(u"Delete the preset \"%s\"?" % name, title=u"Delete preset",
                          ok_text=u"Delete preset", danger=True,
                          details=u"Only the saved values are removed - no rebar in the model "
                                  u"changes.", owner=self):
            return
        presets = dict(self._presets)
        presets.pop(name, None)
        error = wiz.save_presets(self._preset_path, presets)
        if error:
            self._set_status(u"Preset not deleted: %s." % error, "Danger")
            return
        self._load_presets(select=wiz.DEFAULT_PRESET)
        self._write_all(self._preset_values(wiz.DEFAULT_PRESET))
        self._recompute()
        self._set_status(u"Deleted preset \"%s\"." % name, "Success")

    def select_clicked(self, sender, e):
        if not self._created_ids:
            self._set_status(u"Nothing to select yet - create rebar first.", "Warning")
            return
        uidoc = self._uidoc()
        count = select_in_revit(uidoc, self._created_ids) if uidoc is not None else 0
        if count:
            self._set_status(u"Selected %d rebar sets in Revit - close this window to see them."
                             % count, "Success")
        else:
            self._set_status(u"Could not change the Revit selection. Close this window and "
                             u"select the rebar from the host instead.", "Danger")

    def create_clicked(self, sender, e):
        if self._busy:
            return
        self._recompute()
        kind = self._kind
        inp = self._read_form(kind)
        if inp.validate():
            return
        rows = self._ready_rows()
        if not rows:
            self._set_status(u"Nothing to create. Load %s that are in scope, then try again."
                             % wiz.KIND_LABELS[kind][1], "Warning")
            return
        n_sets = sum(len(r.plans) for r in rows)
        hosts = _plural(len(rows), kind)
        joins = inp.add_assembly and any(r.in_assembly for r in rows)
        note = (u"Bars are added to each %s's assembly." % wiz.KIND_LABELS[kind][0]
                if joins else u"Bars are not added to any assembly.")
        if not t3_confirm(u"Create %d rebar sets in %s? %s" % (n_sets, hosts, note),
                          title=TITLE, ok_text=u"Create rebar for %s" % hosts,
                          details=u"One Ctrl+Z undoes the whole run. A host that fails is "
                                  u"rolled back on its own; the others are kept.",
                          owner=self):
            return
        self._run_create(rows, inp)


def show_rebar_wizard(doc):
    """Entry point used by the pushbutton."""
    try:
        types = wiz.resolve_types(doc)
    except Exception as exc:
        t3_warning(u"Rebar Wizard could not read the rebar types of this model.",
                   title=TITLE, details=short_error(exc))
        return
    if not types[0]:
        t3_warning(u"This model has no Rebar Bar Types. Load a rebar family (Structure › Rebar) "
                   u"and try again.", title=TITLE,
                   details=u"Rebar Wizard maps every diameter of a preset to a Rebar Bar Type "
                           u"of the same nominal diameter.")
        return
    RebarWizardDialog(doc, types).ShowDialog()
