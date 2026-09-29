# -*- coding: utf-8 -*-
"""
FamilyTransferDialog
====================
Controller for Family Transfer: pick loadable families (and, per family, which
of its types) in one open project and copy them into another open project.

Revit logic lives in Snippets/family_transfer.py; this module only drives the
window. Modal (ShowDialog), so every handler runs inside the Revit API context.

Author: T3Lab
"""
import os

import System.Windows

from GUI.WPF_Base import T3WPFWindow, to_items_source
from GUI.T3Dialog import show_info, show_warning, show_error, confirm
from Snippets import family_transfer as ft

XAML_FILE = os.path.join(os.path.dirname(__file__), "Tools", "FamilyTransfer.xaml")
TITLE = "Family Transfer"

SHOW_ALL = "All families"
SHOW_NEW = "Not in target"
SHOW_EXISTING = "Already in target"
ALL_CATEGORIES = "All categories"


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _plural(n, word, many=None):
    return u"{} {}".format(n, word if n == 1 else (many or word + "s"))


def _families(n):
    return _plural(n, "family", "families")


class TypeRow(object):
    """One FamilySymbol of a family, shown in the types grid."""

    def __init__(self, name):
        self.name = name
        self.target_status = u"New"
        self._selected = True

    @property
    def is_selected(self):
        return self._selected

    @is_selected.setter
    def is_selected(self, value):
        self._selected = _as_bool(value)


class FamilyRow(object):
    """One loadable family of the source project, shown in the families grid."""

    def __init__(self, info):
        self.info = info
        self.name = info.name
        self.category = info.category
        self.types = [TypeRow(n) for n in info.type_names]
        self.target_status = u"New"
        self.exists_in_target = False
        self._selected = False

    @property
    def is_selected(self):
        return self._selected

    @is_selected.setter
    def is_selected(self, value):
        value = _as_bool(value)
        # Ticking a family whose types were all unticked means "all of it".
        if value and not any(t._selected for t in self.types):
            for t in self.types:
                t._selected = True
        self._selected = value

    @property
    def selected_type_names(self):
        return [t.name for t in self.types if t._selected]

    @property
    def types_label(self):
        total = len(self.types)
        if not self._selected:
            return str(total)
        return u"{} / {}".format(len(self.selected_type_names), total)

    def set_target(self, existing):
        """existing: set of type names in the target family, or None."""
        self.exists_in_target = existing is not None
        missing = 0
        for t in self.types:
            present = existing is not None and t.name in existing
            t.target_status = u"Exists" if present else u"New"
            if not present:
                missing += 1
        if existing is None:
            self.target_status = u"New"
        elif missing:
            self.target_status = u"Exists · {}".format(_plural(missing, "new type"))
        else:
            self.target_status = u"Exists"


class FamilyTransferDialog(T3WPFWindow):
    # Row checkboxes live in DataTemplates; without this their Click never fires.
    WIRE_TEMPLATED_CLICKS = True

    def __init__(self, sources, projects, source_index=0):
        T3WPFWindow.__init__(self, XAML_FILE)
        self._projects = list(projects)           # open projects: the possible targets
        self._sources = list(sources)             # open projects + loaded links
        self._source_entry = self._sources[source_index]
        self._source = self._source_entry.doc
        # A link's natural target is the project that holds it.
        self._target = self._source_entry.host
        self._targets = []
        self._index = {}
        self._rows = []
        self._visible = []
        self._focused = None
        self._loading = True

        for label in (SHOW_ALL, SHOW_NEW, SHOW_EXISTING):
            self.cmb_show.Items.Add(label)
        self.cmb_show.SelectedIndex = 0

        self._fill_source_combo()
        self._fill_target_combo()
        self._load_source()
        self._loading = False
        self._apply_filters()

    # ── data ────────────────────────────────────────────────────────────────
    def _fill_source_combo(self):
        self.cmb_source.Items.Clear()
        for entry in self._sources:
            self.cmb_source.Items.Add(entry.label)
        for i, entry in enumerate(self._sources):
            if entry is self._source_entry:
                self.cmb_source.SelectedIndex = i
                break

    def _fill_target_combo(self):
        self._targets = [d for d in self._projects if not ft.same_doc(d, self._source)]
        previous = self._target
        self.cmb_target.Items.Clear()
        for d in self._targets:
            self.cmb_target.Items.Add(d.Title)
        self._target = None
        if self._targets:
            pick = 0
            for i, d in enumerate(self._targets):
                if ft.same_doc(d, previous):
                    pick = i
            self.cmb_target.SelectedIndex = pick
            self._target = self._targets[pick]
        self._index = ft.target_index(self._target) if self._target is not None else {}

    def _load_source(self):
        infos = ft.collect_families(self._source)
        self._rows = [FamilyRow(info) for info in infos]
        self._focused = None
        self._apply_target()

        cats = sorted(set(r.category for r in self._rows), key=lambda c: c.lower())
        self.cmb_category.Items.Clear()
        self.cmb_category.Items.Add(ALL_CATEGORIES)
        for c in cats:
            self.cmb_category.Items.Add(c)
        self.cmb_category.SelectedIndex = 0

        if not self._rows:
            self.txt_families_empty.Text = (
                u"'{}' has no loadable families. Choose another project or link."
                .format(self._source.Title))
        else:
            self.txt_families_empty.Text = (
                u"No families match. Clear the search or choose another category.")
        self._show_types(None)

    def _apply_target(self):
        for row in self._rows:
            row.set_target(self._index.get(row.name))

    # ── view ────────────────────────────────────────────────────────────────
    def _apply_filters(self):
        if self._loading:
            return
        text = (self.txt_search.Text or u"").strip().lower()
        category = str(self.cmb_category.SelectedItem or ALL_CATEGORIES)
        show = str(self.cmb_show.SelectedItem or SHOW_ALL)

        visible = []
        for row in self._rows:
            if text and text not in row.name.lower() and text not in row.category.lower():
                continue
            if category != ALL_CATEGORIES and row.category != category:
                continue
            if show == SHOW_NEW and row.exists_in_target:
                continue
            if show == SHOW_EXISTING and not row.exists_in_target:
                continue
            visible.append(row)
        self._visible = visible
        self.dg_families.ItemsSource = to_items_source(visible)
        self.sync_header_checkbox(self.chk_all_dg_families, self.dg_families, "is_selected")
        self._refresh_summary()

    def _show_types(self, row):
        self._focused = row
        if row is None:
            self.txt_types_family.Text = u"No family selected"
            self.dg_types.ItemsSource = to_items_source([])
        else:
            self.txt_types_family.Text = u"{} · {}".format(row.name, _plural(len(row.types), "type"))
            self.dg_types.ItemsSource = to_items_source(row.types)
        self.sync_header_checkbox(self.chk_all_dg_types, self.dg_types, "is_selected")

    def _refresh_rows(self):
        self.dg_families.Items.Refresh()
        self.sync_header_checkbox(self.chk_all_dg_families, self.dg_families, "is_selected")
        self._refresh_summary()

    def _refresh_summary(self):
        picked = [r for r in self._rows if r._selected]
        n_types = sum(len(r.selected_type_names) for r in picked)
        if self._target is None:
            self.status_text.Text = (u"Open the target project in this Revit session "
                                     u"to transfer families into it.")
        else:
            self.status_text.Text = u"Selected {} of {} · {}".format(
                len(picked), _families(len(self._rows)),
                _plural(n_types, "type"))
        count = len(picked)
        self.btn_transfer.Content = (u"Transfer {}".format(
            _families(count)) if count else u"Transfer")
        self.btn_transfer.IsEnabled = bool(count) and self._target is not None

    def _sync_focused_family(self, ticked):
        """After a type tick: ticking a type selects its family, unticking the
        last one deselects it. Unticking one of several leaves the family as is."""
        row = self._focused
        if row is None:
            return
        if not row.selected_type_names:
            row._selected = False
        elif ticked:
            row._selected = True
        self._refresh_rows()

    def _mode(self):
        if self.rb_overwrite.IsChecked:
            return ft.OVERWRITE
        if self.rb_add.IsChecked:
            return ft.ADD
        return ft.SKIP

    # ── handlers: filters & selection ────────────────────────────────────────
    def filter_changed(self, sender, args):
        self._apply_filters()

    def source_changed(self, sender, args):
        if self._loading:
            return
        idx = self.cmb_source.SelectedIndex
        if not (0 <= idx < len(self._sources)):
            return
        entry = self._sources[idx]
        if entry is self._source_entry:
            return
        self._loading = True
        try:
            self._source_entry = entry
            self._source = entry.doc
            if entry.is_link:
                self._target = entry.host        # copy the link's families into its host
            self._fill_target_combo()
            self._load_source()
        finally:
            self._loading = False
        self._apply_filters()

    def target_changed(self, sender, args):
        if self._loading:
            return
        idx = self.cmb_target.SelectedIndex
        if not (0 <= idx < len(self._targets)):
            return
        self._target = self._targets[idx]
        self._index = ft.target_index(self._target)
        self._apply_target()
        self._apply_filters()
        self.dg_types.Items.Refresh()

    def family_row_selected(self, sender, args):
        item = self.dg_families.SelectedItem
        if item is None or item is self._focused:
            return
        self._show_types(item)

    def family_check_clicked(self, sender, args):
        # T3WPFWindow already wrote the tick into the row (checkbox bridge).
        self._refresh_rows()
        if self._focused is not None:
            self.dg_types.Items.Refresh()
            self.sync_header_checkbox(self.chk_all_dg_types, self.dg_types, "is_selected")

    def select_all_dg_families_clicked(self, sender, args):
        self.toggle_all_rows(self.dg_families, "is_selected", sender.IsChecked)
        self._refresh_summary()
        if self._focused is not None:
            self.dg_types.Items.Refresh()
            self.sync_header_checkbox(self.chk_all_dg_types, self.dg_types, "is_selected")

    def type_check_clicked(self, sender, args):
        self.sync_header_checkbox(self.chk_all_dg_types, self.dg_types, "is_selected")
        self._sync_focused_family(bool(sender.IsChecked))

    def select_all_dg_types_clicked(self, sender, args):
        self.toggle_all_rows(self.dg_types, "is_selected", sender.IsChecked)
        self._sync_focused_family(bool(sender.IsChecked))

    def conflict_mode_changed(self, sender, args):
        box = getattr(self, "chk_overwrite_values", None)
        rb = getattr(self, "rb_overwrite", None)
        if box is not None and rb is not None:
            box.IsEnabled = bool(rb.IsChecked)

    # ── handlers: transfer ───────────────────────────────────────────────────
    def transfer_clicked(self, sender, args):
        if self._target is None:
            show_warning(u"There is no target project to copy into.",
                         title=TITLE,
                         details=u"Open the target project in this Revit session, then run Family Transfer again.")
            return
        picked = [r for r in self._rows if r._selected]
        if not picked:
            show_info(u"Tick at least one family to transfer.", title=TITLE)
            return
        if self._target.IsReadOnly:
            show_warning(u"'{}' is read-only, so nothing can be copied into it.".format(self._target.Title),
                         title=TITLE,
                         details=u"Open an editable copy of the target project and try again.")
            return

        mode = self._mode()
        actions = ft.plan_transfer([(r.info, r.selected_type_names) for r in picked],
                                   self._index, mode)
        copies = [a for a in actions if a.kind == ft.COPY_KIND]
        overwrites = [a for a in actions if a.kind == ft.OVERWRITE_KIND]
        skips = [a for a in actions if a.kind == ft.SKIP_KIND]
        work = len(copies) + len(overwrites)
        target = self._target.Title

        if not work:
            show_info(u"Nothing to transfer: all {} selected are skipped.".format(
                          _families(len(skips))),
                      title=TITLE,
                      details=u"\n".join(u"{} — {}".format(a.family.name, a.reason) for a in skips))
            return

        n_types = sum(len(a.types) for a in copies + overwrites)
        families = _families(work)
        lines = []
        if copies:
            lines.append(u"Copy: {} ({})".format(
                _families(len(copies)),
                _plural(sum(len(a.types) for a in copies), "type")))
        if overwrites:
            lines.append(u"Overwrite: {} — replaces the definition in the target project".format(
                _families(len(overwrites))))
        if skips:
            lines.append(u"Skip: {}".format(
                _families(len(skips))))
        lines.append(u"")
        lines.append(u"The whole transfer is one undo step in '{}'.".format(target))
        if not confirm(u"Transfer {} ({}) from '{}' to '{}'?".format(
                           families, _plural(n_types, "type"), self._source.Title, target),
                       title=TITLE, ok_text=u"Transfer {}".format(families),
                       danger=bool(overwrites), details=u"\n".join(lines)):
            return

        overwrite_values = bool(self.chk_overwrite_values.IsChecked) and mode == ft.OVERWRITE
        self.begin_progress(work, disable=[self.btn_transfer, self.cmb_source, self.cmb_target,
                                           self.dg_families, self.dg_types])

        def step(index, total, label):
            return self.step_progress(index, u"Transferring {} / {} · {}".format(index, total, label))

        try:
            results = ft.execute_plan(self._source, self._target, actions,
                                      overwrite_values=overwrite_values, step=step)
        except Exception as exc:
            self.end_progress()
            show_error(u"The transfer failed and nothing was changed in '{}'.".format(target),
                       title=TITLE, details=ft._error_text(exc))
            self._refresh_summary()
            return
        self.end_progress()
        self._report(results, target)

        # The target changed: re-read it so IN TARGET is current.
        self._index = ft.target_index(self._target)
        self._apply_target()
        self._apply_filters()
        self.dg_types.Items.Refresh()

    def _report(self, results, target):
        tally = ft.summarize(results)
        head = u"Transferred {} ({}) to '{}'.".format(
            _families(tally["ok"]),
            _plural(tally["types"], "type"), target)
        extra = []
        if tally["skipped"]:
            extra.append(u"{} skipped".format(tally["skipped"]))
        if tally["failed"]:
            extra.append(u"{} failed".format(tally["failed"]))
        message = head + (u" " + u" · ".join(extra) + u"." if extra else u"")
        details = u"\n".join(u"{:<8} {} — {}".format(r.status, r.family, r.detail)
                             for r in results if r.status != "ok")
        if not details:
            details = u"Switch to '{}' and press Ctrl+Z to undo the whole transfer.".format(target)
        if tally["failed"]:
            show_warning(message, title=TITLE, details=details)
        else:
            show_info(message, title=TITLE, details=details)
        self._refresh_summary()
        self.status_text.Text = message

    # ── window chrome ────────────────────────────────────────────────────────
    def minimize_button_clicked(self, sender, args):
        self.WindowState = System.Windows.WindowState.Minimized

    def maximize_button_clicked(self, sender, args):
        if self.WindowState == System.Windows.WindowState.Maximized:
            self.WindowState = System.Windows.WindowState.Normal
        else:
            self.WindowState = System.Windows.WindowState.Maximized

    def close_button_clicked(self, sender, args):
        self.Close()


def pick_source_index(sources, projects, active):
    """Which source to preselect.

    Another open project exists -> the active project (copy out of it, the
    original behaviour). Only one project -> its first loaded link (copy the
    link's families into it). Pure logic, tested in dev/test_family_transfer.py.
    """
    active_is_project = active is not None and any(ft.same_doc(p, active) for p in projects)
    if len(projects) >= 2 and active_is_project:
        for i, entry in enumerate(sources):
            if not entry.is_link and ft.same_doc(entry.doc, active):
                return i
    for i, entry in enumerate(sources):
        if entry.is_link and ft.same_doc(entry.host, active):
            return i
    for i, entry in enumerate(sources):
        if entry.is_link:
            return i
    return 0


def usable_sources(sources, projects):
    """Sources with at least one open project, other than themselves, to copy into."""
    return [e for e in sources
            if any(not ft.same_doc(p, e.doc) for p in projects)]


def show_family_transfer(doc=None):
    """Entry point: copy from another open project, or from a loaded link, into a project."""
    from Snippets._host import resolve_doc
    doc, error = resolve_doc(doc)
    if doc is None:
        show_warning(error, title=TITLE)
        return

    projects = ft.open_projects(doc.Application)
    if not projects:
        show_info(u"Open the project the families should be copied into, then run Family Transfer again.",
                  title=TITLE)
        return

    sources = usable_sources(ft.list_sources(projects), projects)
    if not sources:
        show_info(u"There is nothing to copy families from.",
                  title=TITLE,
                  details=(u"Family Transfer copies from a Revit link loaded in this project, "
                           u"or from a second open project. Load a link (Insert > Link Revit) "
                           u"or open the other project, then run it again."))
        return

    FamilyTransferDialog(sources, projects, pick_source_index(sources, projects, doc)).ShowDialog()
