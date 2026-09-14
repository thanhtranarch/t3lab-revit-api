# -*- coding: utf-8 -*-
"""AutoJoin — Rule-based element joining and unjoining manager."""

import os
import sys
import json
import time

from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Snippets._compat import eid_value

try:
    from GUI import RevitTheme as _theme
except Exception:
    _theme = None

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

from System.Windows import WindowState, Visibility
from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    BuiltInCategory,
    JoinGeometryUtils,
    BoundingBoxIntersectsFilter,
    Outline,
    IFailuresPreprocessor,
    FailureProcessingResult,
    FailureSeverity,
)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult

XAML_FILE = os.path.join(os.path.dirname(__file__), 'Tools', 'AutoJoin.xaml')
RULES_FILE = os.path.join(os.path.dirname(__file__), 'join_rules.json')
logger = script.get_logger()

from Services.join_service import (
    JOINABLE_CATEGORIES,
    BIC_INT_TO_NAME,
    CATEGORY_NAMES,
    DEFAULT_RULES,
    JOIN_CANCELLED_MESSAGE,
    run_join,
)


def _join_result_text(mode, elapsed, joined, skipped, errors, err_msg, quick=False):
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
    filepath = filepath or RULES_FILE
    try:
        with open(filepath, "w") as f:
            json.dump(rules, f, indent=2)
    except Exception as ex:
        logger.warning("Could not save rules to file: {}".format(ex))


def load_rules_from_file(filepath=None):
    filepath = filepath or RULES_FILE
    if os.path.isfile(filepath):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return list(DEFAULT_RULES)


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

        self._rules = []
        self._adopt_host_font()
        self._apply_theme()

        saved = load_rules_from_file()
        for r in saved:
            self._rules.append(r)

        self._refresh_rules()

    def _adopt_host_font(self):
        if _theme is None:
            return
        family, size = _theme.host_font()
        if family:
            try:
                self.FontFamily = family
                if size and size > 0:
                    self.FontSize = size
            except Exception:
                pass

    def _apply_theme(self, theme=None):
        if _theme is None:
            return
        try:
            _theme.apply(self, theme)
        except Exception:
            pass

    def _refresh_rules(self):
        items = [
            RuleItem(i + 1, r["priority"], r["join_with"])
            for i, r in enumerate(self._rules)
        ]
        self.rules_grid.ItemsSource = None
        self.rules_grid.ItemsSource = to_items_source(items)
        self.rule_count_text.Text = "{} rule(s) defined".format(len(self._rules))
        self.rules_grid_empty.Visibility = Visibility.Collapsed if items else Visibility.Visible

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def close_button_clicked(self, sender, e):
        self.Close()

    def btn_add_rule_click(self, sender, args):
        priority = forms.SelectFromList.show(
            CATEGORY_NAMES,
            title="Select Priority Category (Cuts other)",
            button_name="Select Priority",
            multiselect=False,
        )
        if not priority:
            return

        join_with = forms.SelectFromList.show(
            CATEGORY_NAMES,
            title="Select Join-with Category (Cut by '{}')".format(priority),
            button_name="Select Join-with",
            multiselect=False,
        )
        if not join_with:
            return

        for r in self._rules:
            if r["priority"] == priority and r["join_with"] == join_with:
                forms.alert(
                    "Rule already exists:\n{} → {}".format(priority, join_with),
                    title="Duplicate Rule"
                )
                return

        self._rules.append({"priority": priority, "join_with": join_with})
        self._refresh_rules()

    def btn_remove_rule_click(self, sender, args):
        selected = self.rules_grid.SelectedItem
        if not selected:
            forms.alert("Select a rule to remove.", title="Remove Rule")
            return

        idx = selected.Number - 1
        if 0 <= idx < len(self._rules):
            self._rules.pop(idx)
            self._refresh_rules()

    def btn_switch_order_click(self, sender, args):
        selected = self.rules_grid.SelectedItem
        if not selected:
            forms.alert("Select a rule to switch.", title="Switch Order")
            return

        idx = selected.Number - 1
        if 0 <= idx < len(self._rules):
            rule = self._rules[idx]
            rule["priority"], rule["join_with"] = rule["join_with"], rule["priority"]
            self._refresh_rules()

    def btn_save_rules_click(self, sender, args):
        if not self._rules:
            forms.alert("No rules to save.", title="Save Rules")
            return

        filepath = forms.save_file(
            file_ext="json",
            default_name="join_rules",
        )
        if filepath:
            save_rules_to_file(self._rules, filepath)
            forms.alert(
                "Saved {} rule(s) to:\n{}".format(len(self._rules), filepath),
                title="Rules Saved"
            )

    def btn_load_rules_click(self, sender, args):
        filepath = forms.pick_file(file_ext="json")
        if filepath:
            try:
                loaded = load_rules_from_file(filepath)
                if loaded:
                    self._rules = loaded
                    self._refresh_rules()
                    forms.alert(
                        "Loaded {} rule(s).".format(len(loaded)),
                        title="Rules Loaded"
                    )
                else:
                    forms.alert("No valid rules found in file.", title="Load Rules")
            except Exception as e:
                forms.alert("Error loading file:\n{}".format(e), title="Load Error")

    def btn_run_click(self, sender, args):
        if not self._rules:
            forms.alert("Please add at least one join rule.", title="Auto Join")
            return

        scope_item = self.cb_scope.SelectedItem
        scope = scope_item.Content if scope_item else "Entire Project"

        mode_item = self.cb_mode.SelectedItem
        mode = mode_item.Content if mode_item else "Join"

        switch_order = self.chk_switch_order.IsChecked

        msg = (
            "Run Auto {} with {} rule(s)?\n\n"
            "Scope: {}\n"
            "Switch join order: {}\n\n"
            "Rules:\n{}"
        ).format(
            mode, len(self._rules), scope,
            "Yes" if switch_order else "No",
            "\n".join(
                "  {} → {}".format(r["priority"], r["join_with"])
                for r in self._rules
            )
        )

        td = TaskDialog("Confirm Auto {}".format(mode))
        td.MainContent = msg
        td.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
        if td.Show() != TaskDialogResult.Yes:
            return

        self.begin_progress(100, disable=[self.btn_run])
        self.status_text.Text = u"Running…"

        def progress_cb(current, total, message):
            pct = int(float(current) / float(total) * 100) if total > 0 else 0
            self._update_progress(pct, 100)
            self.status_text.Text = message

        start = time.time()
        joined, skipped, errors, err_msg = run_join(
            self._rules, scope, mode, switch_order, progress_cb,
            cancel_check=lambda: self.is_cancelled,
            doc=self._doc, uidoc=self._uidoc
        )
        elapsed = time.time() - start

        save_rules_to_file(self._rules)

        self.end_progress()
        status, result_msg = _join_result_text(
            mode, elapsed, joined, skipped, errors, err_msg
        )
        self.status_text.Text = status
        self.rule_count_text.Text = "{} rule(s) | Last run: {} pair(s) confirmed".format(
            len(self._rules), joined
        )

        forms.alert(result_msg, title="Auto {} Results".format(mode))


def quick_join(doc=None, uidoc=None):
    """Run auto join with default rules on entire project."""
    td = TaskDialog("Quick Auto Join")
    td.MainContent = (
        "Run Auto Join with default rules on the entire project?\n\n"
        "Default rules:\n"
        + "\n".join(
            "  {} → {}".format(r["priority"], r["join_with"])
            for r in DEFAULT_RULES
        )
    )
    td.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    if td.Show() != TaskDialogResult.Yes:
        return

    start = time.time()
    joined, skipped, errors, err_msg = run_join(
        DEFAULT_RULES, "Entire Project", "Join", True, doc=doc, uidoc=uidoc
    )
    elapsed = time.time() - start

    _status, msg = _join_result_text(
        "Join", elapsed, joined, skipped, errors, err_msg, quick=True
    )

    TaskDialog.Show("Auto Join Results", msg)


def show_auto_join_dialog(doc=None, uidoc=None):
    d = doc or revit.doc
    if not d:
        forms.alert("No active Revit document found.", title="Auto Join")
        return
    u = uidoc
    if u is None:
        try:
            u = revit.uidoc
        except Exception:
            u = None
    win = AutoJoinWindow(d, u)
    win.ShowDialog()
