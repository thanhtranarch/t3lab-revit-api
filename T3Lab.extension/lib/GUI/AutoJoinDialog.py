# -*- coding: utf-8 -*-
"""AutoJoin — Rule-based element joining and unjoining manager."""

import os
import json
import time

from pyrevit import revit, script
from GUI.WPF_Base import T3WPFWindow, to_items_source
from GUI.T3Dialog import show_info, show_warning, show_error, confirm

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

from System.Windows import WindowState, Visibility

XAML_FILE = os.path.join(os.path.dirname(__file__), 'Tools', 'AutoJoin.xaml')
# The rules the user last ran are per-user state: they live in
# %APPDATA%\T3LabAI\autojoin, never next to this file in the extension (the
# clone is shared and updated by git). The old in-extension file is copied
# over once so nobody loses their rules.
_LEGACY_RULES_FILE = os.path.join(os.path.dirname(__file__), 'join_rules.json')
logger = script.get_logger()

# Progress pumps the WPF dispatcher; once per element is wasted work on a big
# model, so the window repaints at most this often (seconds).
_PROGRESS_INTERVAL = 0.1


def _rules_file():
    from core.paths import user_data_path
    return user_data_path('autojoin', 'join_rules.json',
                          legacy=[_LEGACY_RULES_FILE])


from Services.join_service import (
    CATEGORY_NAMES,
    DEFAULT_RULES,
    JOIN_CANCELLED_MESSAGE,
    run_join,
)


def _join_result_text(mode, elapsed, joined, skipped, errors, err_msg, quick=False,
                      protected=0, duplicates=0):
    """Describe the service result, including a stop whose partial commit succeeded."""
    stopped = err_msg == JOIN_CANCELLED_MESSAGE
    prefix = "Quick Auto" if quick else "Auto"
    if err_msg and not stopped:
        outcome = "stopped with an error"
        status = "Needs attention — review the result before retrying."
    elif stopped:
        outcome = "stopped by request"
        status = "Stopped — {} pair(s) committed, {} error(s).".format(joined, errors)
    elif errors:
        outcome = "completed with errors"
        status = "Completed with errors — {} pair(s) committed, {} error(s).".format(joined, errors)
    else:
        outcome = "completed"
        status = "Done — {} pair(s) committed.".format(joined)

    message = (
        "{} {} {} in {:.1f}s\n\n"
        "Confirmed {}ed pairs: {}\n"
        "Skipped pairs: {}\n"
        "Errors: {}"
    ).format(prefix, mode, outcome, elapsed, mode.lower(), joined, skipped, errors)
    if protected:
        message += ("\nEmbedded elements kept visible: {} pair(s) — the inner "
                    "element cuts the outer one.").format(protected)
    if duplicates:
        message += ("\nExact overlaps not joined: {} pair(s) — joining would hide "
                    "one of them; check for duplicate elements.").format(duplicates)
    if stopped:
        message += ("\n\nThe changes completed before the stop were committed. "
                    "Use Undo to revert this run.")
    elif err_msg:
        message += ("\n\n{}\n\nResolve any Revit failure dialog and check the model "
                    "before retrying.").format(err_msg)
    return status, message


class RuleItem(object):
    """View-model for one join rule row."""
    def __init__(self, index, priority_name, join_with_name):
        self.Number       = index
        self.PriorityName = priority_name
        self.JoinWithName = join_with_name


def save_rules_to_file(rules, filepath=None):
    filepath = filepath or _rules_file()
    try:
        with open(filepath, "w") as f:
            json.dump(rules, f, indent=2)
    except Exception as ex:
        logger.warning("Could not save rules to file: {}".format(ex))


def load_rules_from_file(filepath=None):
    filepath = filepath or _rules_file()
    if os.path.isfile(filepath):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return list(DEFAULT_RULES)


def _valid_rules(loaded):
    """Keep only well-formed rules whose categories this tool knows."""
    rules = []
    if not isinstance(loaded, list):
        return rules
    for r in loaded:
        if not isinstance(r, dict):
            continue
        cuts, cut = r.get("priority"), r.get("join_with")
        if cuts in CATEGORY_NAMES and cut in CATEGORY_NAMES:
            rules.append({"priority": cuts, "join_with": cut})
    return rules


def _file_dialog(save, owner=None):
    """Ask for a rule-set .json path. Returns None when cancelled."""
    from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
    dlg = SaveFileDialog() if save else OpenFileDialog()
    dlg.Filter = "Join rule set (*.json)|*.json|All files|*.*"
    dlg.DefaultExt = "json"
    if save:
        dlg.FileName = "join_rules"
    dlg.Title = "Save join rules" if save else "Load join rules"
    try:
        ok = dlg.ShowDialog(owner) if owner is not None else dlg.ShowDialog()
    except Exception:
        ok = dlg.ShowDialog()
    return dlg.FileName if ok == True else None  # noqa: E712 - Nullable<bool>


class AutoJoinWindow(T3WPFWindow):
    PP_STOP_MSG = u"Stopping… finishing current element"

    def __init__(self, doc=None, uidoc=None):
        T3WPFWindow.__init__(self, XAML_FILE)
        self._doc = doc or revit.doc
        self._uidoc = uidoc
        if self._uidoc is None:
            try:
                self._uidoc = revit.uidoc
            except Exception:
                self._uidoc = None

        for name in CATEGORY_NAMES:
            self.cb_new_cuts.Items.Add(name)
            self.cb_new_cut_by.Items.Add(name)
        self._select_category(self.cb_new_cuts, "Structural Framing")
        self._select_category(self.cb_new_cut_by, "Floors")

        self._rules = _valid_rules(load_rules_from_file())
        self._refresh_rules()

    # ── helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _select_category(combo, name):
        if name in CATEGORY_NAMES:
            combo.SelectedIndex = CATEGORY_NAMES.index(name)

    def _set_status(self, text):
        self.status_text.Text = text

    def _refresh_rules(self, select=None):
        items = [
            RuleItem(i + 1, r["priority"], r["join_with"])
            for i, r in enumerate(self._rules)
        ]
        self.rules_grid.ItemsSource = None
        self.rules_grid.ItemsSource = to_items_source(items)
        self.rule_count_text.Text = "{} rule(s) defined".format(len(self._rules))
        self.rules_grid_empty.Visibility = Visibility.Collapsed if items else Visibility.Visible
        if select is not None and 0 <= select < len(items):
            try:
                self.rules_grid.SelectedIndex = select
                self.rules_grid.ScrollIntoView(self.rules_grid.SelectedItem)
            except Exception:
                pass

    def _selected_indexes(self):
        """Rule indexes of the selected rows (several when Ctrl/Shift-selected)."""
        rows = []
        selected = getattr(self.rules_grid, "SelectedItems", None)
        if selected is not None:
            rows = [row for row in selected]
        elif getattr(self.rules_grid, "SelectedItem", None) is not None:
            rows = [self.rules_grid.SelectedItem]
        return sorted({row.Number - 1 for row in rows
                       if 0 <= row.Number - 1 < len(self._rules)})

    def _options(self):
        scope_item = self.cb_scope.SelectedItem
        mode_item = self.cb_mode.SelectedItem
        return (scope_item.Content if scope_item else "Entire Project",
                mode_item.Content if mode_item else "Join",
                bool(self.chk_switch_order.IsChecked),
                bool(self.chk_protect_embedded.IsChecked))

    # ── chrome ──────────────────────────────────────────────────────────────
    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def close_button_clicked(self, sender, e):
        self.Close()

    # ── settings ────────────────────────────────────────────────────────────
    def cb_mode_changed(self, sender, args):
        """Join order only means something when joining."""
        item = self.cb_mode.SelectedItem
        mode = item.Content if item else "Join"
        joining = mode == "Join"
        for ctrl in (self.chk_switch_order, self.chk_protect_embedded,
                     self.txt_switch_order_note, self.txt_protect_note):
            ctrl.IsEnabled = joining
        self.btn_run_label.Text = "Run Auto {}".format(mode)

    # ── rule list ───────────────────────────────────────────────────────────
    def btn_add_rule_click(self, sender, args):
        cuts = self.cb_new_cuts.SelectedItem
        cut = self.cb_new_cut_by.SelectedItem
        if not cuts or not cut:
            self._set_status("Pick a CUTS category and a GETS CUT category, then click Add Rule.")
            return
        cuts, cut = str(cuts), str(cut)
        for i, r in enumerate(self._rules):
            if r["priority"] == cuts and r["join_with"] == cut:
                self._refresh_rules(select=i)
                self._set_status("Rule {} already says {} cuts {}.".format(i + 1, cuts, cut))
                return
        self._rules.append({"priority": cuts, "join_with": cut})
        self._refresh_rules(select=len(self._rules) - 1)
        self._set_status("Added rule {}: {} cuts {}.".format(len(self._rules), cuts, cut))

    def btn_remove_rule_click(self, sender, args):
        indexes = self._selected_indexes()
        if not indexes:
            self._set_status("Select one or more rules in the list to remove them.")
            return
        for idx in reversed(indexes):
            self._rules.pop(idx)
        self._refresh_rules(select=min(indexes[0], len(self._rules) - 1))
        self._set_status("Removed {} rule(s).".format(len(indexes)))

    def btn_switch_order_click(self, sender, args):
        indexes = self._selected_indexes()
        if not indexes:
            self._set_status("Select one or more rules in the list to switch them.")
            return
        for idx in indexes:
            rule = self._rules[idx]
            rule["priority"], rule["join_with"] = rule["join_with"], rule["priority"]
        self._refresh_rules(select=indexes[0])
        self._set_status("Switched {} rule(s): the other category now cuts.".format(len(indexes)))

    def btn_reset_rules_click(self, sender, args):
        if self._rules and self._rules != DEFAULT_RULES:
            if not confirm("Replace the {} rule(s) in the list with the {} default rules?"
                           .format(len(self._rules), len(DEFAULT_RULES)),
                           title="Reset Join Rules", ok_text="Reset", owner=self):
                return
        self._rules = [dict(r) for r in DEFAULT_RULES]
        self._refresh_rules()
        self._set_status("Loaded the {} default rules.".format(len(self._rules)))

    def btn_save_rules_click(self, sender, args):
        if not self._rules:
            self._set_status("There are no rules to save — add a rule first.")
            return
        filepath = _file_dialog(save=True, owner=self)
        if filepath:
            save_rules_to_file(self._rules, filepath)
            self._set_status("Saved {} rule(s) to {}.".format(
                len(self._rules), os.path.basename(filepath)))

    def btn_load_rules_click(self, sender, args):
        filepath = _file_dialog(save=False, owner=self)
        if not filepath:
            return
        try:
            with open(filepath, "r") as f:
                loaded = _valid_rules(json.load(f))
        except Exception as e:
            show_error("Could not read the rule file.", title="Load Rules",
                       details="{}\n\n{}\n\nPick a .json file saved by Auto Join.".format(
                           filepath, e), owner=self)
            return
        if not loaded:
            show_warning("No valid rules found in this file.", title="Load Rules",
                         details="{}\n\nEach rule needs two known categories "
                                 "(\"priority\" and \"join_with\").".format(filepath),
                         owner=self)
            return
        self._rules = loaded
        self._refresh_rules()
        self._set_status("Loaded {} rule(s) from {}.".format(
            len(loaded), os.path.basename(filepath)))

    # ── run ─────────────────────────────────────────────────────────────────
    def btn_run_click(self, sender, args):
        if not self._rules:
            self._set_status("Add at least one join rule before running.")
            return

        scope, mode, switch_order, protect = self._options()
        joining = mode == "Join"
        details = "Scope: {}\n".format(scope)
        if joining:
            details += "Rule order: {}\nKeep embedded elements visible: {}\n".format(
                "applied" if switch_order else "Revit decides",
                "on" if protect else "off")
        details += "\nRules (CUTS → GETS CUT):\n" + "\n".join(
            "  {}. {} → {}".format(i + 1, r["priority"], r["join_with"])
            for i, r in enumerate(self._rules))
        if not confirm("Run Auto {} with {} rule(s) on {}?".format(
                mode, len(self._rules), scope.lower()),
                title="Auto {}".format(mode), ok_text="Run Auto {}".format(mode),
                details=details, owner=self):
            return

        self.begin_progress(100, disable=[self.btn_run, self.btn_add_rule,
                                          self.btn_remove_rule, self.btn_switch_order,
                                          self.btn_reset_rules, self.btn_load_rules])
        self._set_status(u"Running…")
        last_paint = [0.0]

        def progress_cb(current, total, message):
            now = time.time()
            if now - last_paint[0] < _PROGRESS_INTERVAL and current < total:
                return
            last_paint[0] = now
            pct = int(float(current) / float(total) * 100) if total > 0 else 0
            self.status_text.Text = message
            self._update_progress(pct, 100)

        stats = {}
        start = time.time()
        try:
            joined, skipped, errors, err_msg = run_join(
                self._rules, scope, mode, switch_order, progress_cb,
                cancel_check=lambda: self.is_cancelled,
                doc=self._doc, uidoc=self._uidoc,
                protect_embedded=protect, stats=stats
            )
        finally:
            self.end_progress()
        elapsed = time.time() - start

        save_rules_to_file(self._rules)

        status, result_msg = _join_result_text(
            mode, elapsed, joined, skipped, errors, err_msg,
            protected=stats.get("protected", 0), duplicates=stats.get("duplicates", 0)
        )
        self._set_status(status)
        self.rule_count_text.Text = "{} rule(s) | Last run: {} pair(s) confirmed".format(
            len(self._rules), joined
        )

        headline = status.split(" — ")[0]
        if err_msg and err_msg != JOIN_CANCELLED_MESSAGE:
            show_error(headline, title="Auto {} Results".format(mode),
                       details=result_msg, owner=self)
        else:
            show_info(headline, title="Auto {} Results".format(mode),
                      details=result_msg, owner=self)


def quick_join(doc=None, uidoc=None):
    """Run auto join with default rules on entire project."""
    rules_text = "\n".join(
        "  {}. {} → {}".format(i + 1, r["priority"], r["join_with"])
        for i, r in enumerate(DEFAULT_RULES))
    if not confirm("Run Auto Join with the {} default rules on the entire project?".format(
            len(DEFAULT_RULES)),
            title="Quick Auto Join", ok_text="Run Auto Join",
            details="Rule order applied; embedded elements kept visible.\n\n"
                    "Rules (CUTS → GETS CUT):\n" + rules_text):
        return

    stats = {}
    start = time.time()
    joined, skipped, errors, err_msg = run_join(
        DEFAULT_RULES, "Entire Project", "Join", True, doc=doc, uidoc=uidoc,
        protect_embedded=True, stats=stats
    )
    elapsed = time.time() - start

    status, msg = _join_result_text(
        "Join", elapsed, joined, skipped, errors, err_msg, quick=True,
        protected=stats.get("protected", 0), duplicates=stats.get("duplicates", 0)
    )
    if err_msg and err_msg != JOIN_CANCELLED_MESSAGE:
        show_error(status.split(" — ")[0], title="Quick Auto Join Results", details=msg)
    else:
        show_info(status.split(" — ")[0], title="Quick Auto Join Results", details=msg)


def show_auto_join_dialog(doc=None, uidoc=None):
    d = doc or revit.doc
    if not d:
        show_warning("No active Revit document found.", title="Auto Join",
                     details="Open a project, then run Auto Join again.")
        return
    u = uidoc
    if u is None:
        try:
            u = revit.uidoc
        except Exception:
            u = None
    win = AutoJoinWindow(d, u)
    win.ShowDialog()
