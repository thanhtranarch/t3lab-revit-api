# -*- coding: utf-8 -*-
"""
TeklaBridgeDialog.py
====================
WPF dialog of the Tekla Bridge pushbutton: type a Tekla command name, see the
Revit command or T3Lab tool that does the same job and a one-line note on what
is different, then open it.

The dialog never touches Revit. It only records *what the user chose* in
``self.action`` - ``("post", row)`` | ``("tool", row)`` | ``("ribbon", row)`` -
and closes. The pushbutton script carries the action out after ``ShowDialog()``
returns, because ``UIApplication.PostCommand`` only runs once the current
pyRevit command has finished.

All data and Revit calls live in ``Snippets/_tekla_bridge.py``.

Part of T3Lab Extension.
Author: Tran Tien Thanh
"""

import os

import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from System.Windows import Visibility
from System.Windows.Input import Key
from System.Windows.Media import VisualTreeHelper

from GUI.WPF_Base import T3WPFWindow
from GUI.T3Dialog import show_info, show_warning

from Snippets import _tekla_bridge

GUI_DIR = os.path.dirname(__file__)
XAML_FILE = os.path.join(GUI_DIR, 'Tools', 'TeklaBridge.xaml')

ALL_GROUPS_LABEL = "All groups"
DIALOG_TITLE = "Tekla Bridge"


class BridgeRow(object):
    """One command line in the list. Plain attributes only: the XAML template
    binds ``tekla``, ``revit`` and ``layer_label`` through the string bridge."""

    def __init__(self, row):
        self.row = row
        self.tekla = row["tekla"]
        self.revit = row["revit"]
        self.layer_label = _tekla_bridge.layer_label(row)


class TeklaBridgeDialog(T3WPFWindow):
    """Main window of Tekla Bridge. Works with or without an open model."""

    def __init__(self, doc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self.doc = doc
        self.action = None
        self._loading = True
        self._closing = False
        self._year = _tekla_bridge.revit_year()
        self._all_rows = []
        self._shown = []
        self._current = None
        self._state_path = None
        self._seen = set()

        self._init_state()
        self._load_rows()
        self._init_groups()
        self._apply_filter()
        self._loading = False
        try:
            self.tb_search.Focus()
        except Exception:
            pass

    # ── SETUP ────────────────────────────────────────────────────────────────

    def _init_state(self):
        """Read which tips were shown and the tip-once setting. No file = defaults."""
        try:
            self._state_path = _tekla_bridge.seen_tips_path()
            self._seen = _tekla_bridge.load_seen(self._state_path)
            self.chk_tip_once.IsChecked = _tekla_bridge.load_tip_once(self._state_path)
        except Exception:
            self._state_path = None
            self._seen = set()

    def _load_rows(self):
        """Load and validate the command list; on failure say what, where and what next."""
        try:
            self._all_rows = _tekla_bridge.load_rows()
        except (OSError, ValueError) as exc:
            self._all_rows = []
            first = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            self._set_status("Command list not loaded", "danger")
            self.txt_tip.Text = ("The command list could not be loaded from %s: %s. "
                                 "Update the T3Lab extension and restart Revit."
                                 % (_tekla_bridge.DATA_FILE, first))

    def _init_groups(self):
        labels = [ALL_GROUPS_LABEL] + [label for _gid, label in _tekla_bridge.GROUPS]
        self.set_items_source(self.cb_group, labels)
        self.cb_group.SelectedIndex = 0

    # ── LIST ─────────────────────────────────────────────────────────────────

    def _group_id(self):
        index = self.cb_group.SelectedIndex
        if index is None or index <= 0:
            return None
        return _tekla_bridge.GROUPS[index - 1][0]

    def _apply_filter(self):
        """Refill the list from the search box and the group filter; select the first match."""
        rows = _tekla_bridge.search(self._all_rows, self.tb_search.Text, self._group_id())
        self._shown = [BridgeRow(r) for r in rows]
        self.set_items_source(self.lst_rows, self._shown)
        self.lst_rows_empty.Visibility = (Visibility.Visible if not rows
                                          else Visibility.Collapsed)
        if self._shown:
            # Enter in the search box opens the first match, so keep it selected.
            self.lst_rows.SelectedIndex = 0
        else:
            self._show_row(None)
        if self._all_rows:
            groups = len(set(r["group"] for r in rows))
            self._set_status(_tekla_bridge.summary_text(len(rows), len(self._all_rows), groups))

    def _move_selection(self, step):
        count = len(self._shown)
        if not count:
            return
        index = self.lst_rows.SelectedIndex
        index = 0 if index is None or index < 0 else index + step
        index = max(0, min(count - 1, index))
        self.lst_rows.SelectedIndex = index
        try:
            self.lst_rows.ScrollIntoView(self.lst_rows.SelectedItem)
        except Exception:
            pass

    def _show_row(self, row):
        """Fill the tip callout and the footer buttons for ``row`` (None = nothing selected)."""
        self._current = row
        if row is None:
            self.txt_tip.Text = "Select a command to see what is different in Revit."
            self.txt_where.Text = ""
            self.txt_open_label.Text = "Open in Revit"
            self.btn_open.IsEnabled = False
            self.btn_tool.Visibility = Visibility.Collapsed
            return

        self.txt_tip.Text = row["tip_en"]
        self.txt_where.Text = self._where_text(row)

        _kind, label = _tekla_bridge.primary_action(row)
        self.txt_open_label.Text = label
        self.btn_open.IsEnabled = _kind is not None
        second = _tekla_bridge.secondary_action(row)
        self.btn_tool.Visibility = Visibility.Visible if second else Visibility.Collapsed

    def _where_text(self, row):
        parts = []
        ribbon = _tekla_bridge.ribbon_path_for(row, self._year)
        if ribbon:
            head = "Ribbon (Revit %d)" % self._year if self._year else "Ribbon"
            parts.append("%s: %s" % (head, ribbon))
        name = _tekla_bridge.tool_name(row)
        if name:
            parts.append("T3Lab tool: %s" % name)
        return "   ·   ".join(parts) if parts else "Not on the Revit ribbon."

    def _set_status(self, text, state="ok"):
        self.status_text.Text = text
        key = {"ok": "T3.Success.Accent", "warning": "T3.Warning.Accent",
               "danger": "T3.Danger.Accent"}.get(state, "T3.Success.Accent")
        try:
            brush = self.TryFindResource(key)
            if brush is not None:
                self.dot_status.Fill = brush
        except Exception:
            pass

    # ── RESULT ───────────────────────────────────────────────────────────────

    def _remember_tip(self, row_id):
        self._seen.add(row_id)
        if self._state_path:
            _tekla_bridge.mark_seen(self._state_path, row_id)

    def _finish(self, kind, row):
        """Apply tip-once, store the chosen action and close the window."""
        if self._closing:
            return
        if bool(self.chk_tip_once.IsChecked) and row["id"] not in self._seen:
            show_info(row["tip_en"], title="%s - %s" % (DIALOG_TITLE, row["tekla"]), owner=self)
            self._remember_tip(row["id"])
        self.action = (kind, row)
        self._closing = True
        self.Close()

    @staticmethod
    def _is_on_list_item(source):
        """True when a mouse event started on a list row, not on the scrollbar or blank space."""
        try:
            from System.Windows.Controls import ListBoxItem
            node = source
            while node is not None:
                if isinstance(node, ListBoxItem):
                    return True
                node = VisualTreeHelper.GetParent(node)
            return False
        except Exception:
            return True

    # ── HANDLERS ─────────────────────────────────────────────────────────────

    def search_text_changed(self, sender, e):
        if self._loading:
            return
        self._apply_filter()

    def search_key_down(self, sender, e):
        if e.Key == Key.Enter:
            e.Handled = True
            self.open_clicked(sender, e)
        elif e.Key == Key.Down:
            e.Handled = True
            self._move_selection(1)
        elif e.Key == Key.Up:
            e.Handled = True
            self._move_selection(-1)

    def group_changed(self, sender, e):
        if self._loading:
            return
        self._apply_filter()

    def row_selected(self, sender, e):
        item = self.lst_rows.SelectedItem
        self._show_row(getattr(item, "row", None) if item is not None else None)

    def row_double_clicked(self, sender, e):
        if not self._is_on_list_item(getattr(e, "OriginalSource", None)):
            return
        self.open_clicked(sender, e)

    def tip_once_clicked(self, sender, e):
        if self._state_path:
            _tekla_bridge.save_tip_once(self._state_path, bool(self.chk_tip_once.IsChecked))

    def open_tool_clicked(self, sender, e):
        row = self._current
        if row is None or not row.get("tool"):
            return
        self._finish(_tekla_bridge.ACTION_TOOL, row)

    def open_guide_clicked(self, sender, e):
        path = _tekla_bridge.guide_path()
        if not os.path.isfile(path):
            show_warning("Guide not found at %s — it ships with the repository under docs/." % path,
                         title=DIALOG_TITLE,
                         details="Update or re-clone the T3Lab repository so docs/tekla-to-revit-2027.md is present.",
                         owner=self)
            return
        try:
            os.startfile(path)
        except Exception as exc:
            show_warning("The guide could not be opened: %s" % exc,
                         title=DIALOG_TITLE,
                         details="Open %s with any Markdown viewer or text editor." % path,
                         owner=self)

    def open_clicked(self, sender, e):
        row = self._current
        if row is None:
            return
        kind, _label = _tekla_bridge.primary_action(row)
        if kind is None:
            self._set_status("Nothing to open for \"%s\" - read the note below." % row["tekla"], "warning")
            return
        self._finish(kind, row)


def show_tekla_bridge(doc=None):
    """Entry point used by the pushbutton. Returns the chosen action or None."""
    dialog = TeklaBridgeDialog(doc)
    dialog.ShowDialog()
    return dialog.action
