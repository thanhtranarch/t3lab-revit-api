# -*- coding: utf-8 -*-
"""
Auto Join

Automatically join intersecting Revit elements by category rules.
Define priority categories (which cut) and join-with categories (which get cut).
Inspired by Alpha BIM Auto Join workflow.

- Click       : Open Auto Join Manager (WPF rule-based dialog)
- Shift+Click : Quick join with default rules (Walls ↔ Floors, Columns)

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Auto\nJoin"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==================================================
import os
import sys
import json
import time

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

from System.Windows import WindowState, Visibility
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind

from Autodesk.Revit.DB import (
    Transaction, FilteredElementCollector,
    BuiltInCategory, ElementCategoryFilter,
    JoinGeometryUtils, BoundingBoxIntersectsFilter,
    Outline, ElementId,
)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult
from pyrevit import revit, forms, script

# Path setup
SCRIPT_DIR    = os.path.dirname(__file__)
EXT_DIR       = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))
lib_dir       = os.path.join(EXT_DIR, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()
output = script.get_output()

doc   = revit.doc
uidoc = revit.uidoc

XAML_FILE        = os.path.join(EXT_DIR, 'lib', 'GUI', 'Tools', 'AutoJoin.xaml')
RULES_FILE       = os.path.join(SCRIPT_DIR, "join_rules.json")

# ==================================================
# CATEGORY DEFINITIONS
# ==================================================

# Map of user-friendly names to BuiltInCategory enum values
JOINABLE_CATEGORIES = {
    "Walls":                BuiltInCategory.OST_Walls,
    "Floors":               BuiltInCategory.OST_Floors,
    "Structural Columns":   BuiltInCategory.OST_StructuralColumns,
    "Columns":              BuiltInCategory.OST_Columns,
    "Structural Framing":   BuiltInCategory.OST_StructuralFraming,
    "Structural Foundations": BuiltInCategory.OST_StructuralFoundation,
    "Roofs":                BuiltInCategory.OST_Roofs,
    "Ceilings":             BuiltInCategory.OST_Ceilings,
    "Generic Models":       BuiltInCategory.OST_GenericModel,
}

# Reverse lookup: BuiltInCategory int -> display name
BIC_INT_TO_NAME = {}
for name, bic in JOINABLE_CATEGORIES.items():
    BIC_INT_TO_NAME[int(bic)] = name

CATEGORY_NAMES = sorted(JOINABLE_CATEGORIES.keys())

# Default rules for Shift+Click quick-join
DEFAULT_RULES = [
    {"priority": "Floors",              "join_with": "Walls"},
    {"priority": "Structural Columns",  "join_with": "Walls"},
    {"priority": "Structural Columns",  "join_with": "Floors"},
    {"priority": "Structural Framing",  "join_with": "Walls"},
    {"priority": "Structural Framing",  "join_with": "Floors"},
    {"priority": "Columns",             "join_with": "Walls"},
]


# ==================================================
# RULE ITEM MODEL
# ==================================================

class RuleItem(object):
    """View-model for one join rule row."""

    def __init__(self, index, priority_name, join_with_name):
        self.Number       = index
        self.PriorityName = priority_name
        self.JoinWithName = join_with_name


# ==================================================
# CORE JOIN ENGINE
# ==================================================

def _collect_elements(bic, scope, view_id=None, selected_ids=None):
    """Collect elements of a BuiltInCategory based on scope."""
    if scope == "Selected Elements" and selected_ids:
        all_els = FilteredElementCollector(doc)\
            .OfCategory(bic)\
            .WhereElementIsNotElementType()\
            .ToElements()
        id_set = set(eid.IntegerValue for eid in selected_ids)
        return [el for el in all_els if el.Id.IntegerValue in id_set]
    elif scope == "Active View" and view_id:
        return list(
            FilteredElementCollector(doc, view_id)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    else:  # Entire Project
        return list(
            FilteredElementCollector(doc)
            .OfCategory(bic)
            .WhereElementIsNotElementType()
            .ToElements()
        )


def _get_intersecting_elements(el, target_bic, scope, view_id=None):
    """Find elements of target_bic whose bounding boxes intersect el."""
    bb = el.get_BoundingBox(None)
    if not bb:
        return []

    try:
        outline = Outline(bb.Min, bb.Max)
        bb_filter = BoundingBoxIntersectsFilter(outline)
    except Exception:
        return []

    try:
        if scope == "Active View" and view_id:
            collector = FilteredElementCollector(doc, view_id)
        else:
            collector = FilteredElementCollector(doc)

        candidates = collector\
            .OfCategory(target_bic)\
            .WherePasses(bb_filter)\
            .WhereElementIsNotElementType()\
            .ToElements()
        return [c for c in candidates if c.Id.IntegerValue != el.Id.IntegerValue]
    except Exception:
        return []


def run_join(rules, scope, mode, switch_order, progress_callback=None):
    """
    Execute join/unjoin operations based on rules.

    Args:
        rules: list of dicts with 'priority' and 'join_with' keys (category names)
        scope: "Entire Project", "Active View", or "Selected Elements"
        mode: "Join" or "Unjoin"
        switch_order: bool, if True, switch join order so priority cuts
        progress_callback: function(current, total, message)

    Returns:
        (joined_count, skipped_count, error_count, error_msg)
    """
    view_id = doc.ActiveView.Id if scope == "Active View" else None
    selected_ids = None
    if scope == "Selected Elements":
        sel = uidoc.Selection.GetElementIds()
        if not sel or sel.Count == 0:
            return (0, 0, 0, "No elements selected.")
        selected_ids = list(sel)

    total_joined  = 0
    total_skipped = 0
    total_errors  = 0

    # Calculate total work for progress
    total_rules = len(rules)

    t = Transaction(doc, "Auto {} Elements".format(mode))
    t.Start()

    try:
        for rule_idx, rule in enumerate(rules):
            priority_name = rule.get("priority", "")
            joinwith_name = rule.get("join_with", "")

            priority_bic = JOINABLE_CATEGORIES.get(priority_name)
            joinwith_bic = JOINABLE_CATEGORIES.get(joinwith_name)

            if not priority_bic or not joinwith_bic:
                total_errors += 1
                continue

            if progress_callback:
                progress_callback(
                    rule_idx, total_rules,
                    "Processing: {} → {} ...".format(priority_name, joinwith_name)
                )

            # Collect priority elements
            priority_elements = _collect_elements(
                priority_bic, scope, view_id, selected_ids
            )

            for el in priority_elements:
                # Find intersecting join-with elements
                candidates = _get_intersecting_elements(
                    el, joinwith_bic, scope, view_id
                )

                for cand in candidates:
                    try:
                        if mode == "Join":
                            if not JoinGeometryUtils.AreElementsJoined(doc, el, cand):
                                JoinGeometryUtils.JoinGeometry(doc, el, cand)
                                # Switch join order so priority element cuts the other
                                if switch_order:
                                    try:
                                        JoinGeometryUtils.SwitchJoinOrder(doc, el, cand)
                                    except Exception:
                                        pass  # Some elements don't support order switch
                                total_joined += 1
                            else:
                                total_skipped += 1
                        else:  # Unjoin
                            if JoinGeometryUtils.AreElementsJoined(doc, el, cand):
                                JoinGeometryUtils.UnjoinGeometry(doc, el, cand)
                                total_joined += 1
                            else:
                                total_skipped += 1
                    except Exception as e:
                        total_errors += 1

        t.Commit()
    except Exception as e:
        t.RollBack()
        return (total_joined, total_skipped, total_errors,
                "Transaction failed: {}".format(str(e)))

    if progress_callback:
        progress_callback(total_rules, total_rules, "Done!")

    return (total_joined, total_skipped, total_errors, None)


# ==================================================
# FILE HELPERS (Save/Load Rules)
# ==================================================

def save_rules_to_file(rules, filepath=None):
    """Save rules list to JSON file."""
    filepath = filepath or RULES_FILE
    with open(filepath, "w") as f:
        json.dump(rules, f, indent=2)


def load_rules_from_file(filepath=None):
    """Load rules list from JSON file."""
    filepath = filepath or RULES_FILE
    if os.path.isfile(filepath):
        with open(filepath, "r") as f:
            return json.load(f)
    return list(DEFAULT_RULES)


# ==================================================
# WPF WINDOW
# ==================================================

class AutoJoinWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self._load_logo()
        self._rules = []

        try:
            fname = os.path.basename(doc.PathName) if doc.PathName else "Unsaved Document"
            self.doc_name.Text = fname
        except Exception:
            pass

        # Load saved rules or defaults
        saved = load_rules_from_file()
        for r in saved:
            self._rules.append(r)

        self._refresh_rules()

    def _load_logo(self):
        try:
            logo_path = os.path.join(EXT_DIR, 'lib', 'GUI', 'T3Lab_logo.png')
            if os.path.exists(logo_path):
                bitmap = BitmapImage()
                bitmap.BeginInit()
                bitmap.UriSource = Uri(logo_path, UriKind.Absolute)
                bitmap.EndInit()
                self.Icon = bitmap
        except Exception as icon_ex:
            logger.warning("Could not set window icon: {}".format(icon_ex))

    # --------------------------------------------------
    # Grid helpers
    # --------------------------------------------------

    def _refresh_rules(self):
        items = [
            RuleItem(i + 1, r["priority"], r["join_with"])
            for i, r in enumerate(self._rules)
        ]
        self.rules_grid.ItemsSource = None
        self.rules_grid.ItemsSource = items
        self.progress_text.Text = "{} rule(s) defined".format(len(self._rules))

    # --------------------------------------------------
    # Window chrome handlers
    # --------------------------------------------------

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def close_button_clicked(self, sender, e):
        self.Close()

    # --------------------------------------------------
    # Rule management handlers
    # --------------------------------------------------

    def btn_add_rule_click(self, sender, args):
        """Add a new join rule by selecting priority and join-with categories."""
        # Pick priority category
        priority = forms.SelectFromList.show(
            CATEGORY_NAMES,
            title="Select Priority Category (Cuts other)",
            button_name="Select Priority",
            multiselect=False,
        )
        if not priority:
            return

        # Pick join-with category (exclude the priority one)
        joinwith_options = [c for c in CATEGORY_NAMES if c != priority]
        join_with = forms.SelectFromList.show(
            joinwith_options,
            title="Select Join-with Category (Cut by '{}')".format(priority),
            button_name="Select Join-with",
            multiselect=False,
        )
        if not join_with:
            return

        # Check for duplicates
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
        """Remove selected rule from the list."""
        selected = self.rules_grid.SelectedItem
        if not selected:
            forms.alert("Select a rule to remove.", title="Remove Rule")
            return

        idx = selected.Number - 1
        if 0 <= idx < len(self._rules):
            self._rules.pop(idx)
            self._refresh_rules()

    def btn_switch_order_click(self, sender, args):
        """Swap priority and join-with of the selected rule."""
        selected = self.rules_grid.SelectedItem
        if not selected:
            forms.alert("Select a rule to switch.", title="Switch Order")
            return

        idx = selected.Number - 1
        if 0 <= idx < len(self._rules):
            rule = self._rules[idx]
            rule["priority"], rule["join_with"] = rule["join_with"], rule["priority"]
            self._refresh_rules()

    # --------------------------------------------------
    # Save / Load handlers
    # --------------------------------------------------

    def btn_save_rules_click(self, sender, args):
        """Save current rules to JSON file."""
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
        """Load rules from a JSON file."""
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

    # --------------------------------------------------
    # Run handler
    # --------------------------------------------------

    def btn_run_click(self, sender, args):
        """Execute the auto join/unjoin operation."""
        if not self._rules:
            forms.alert("Please add at least one join rule.", title="Auto Join")
            return

        # Get scope
        scope_item = self.cb_scope.SelectedItem
        scope = scope_item.Content if scope_item else "Entire Project"

        # Get mode
        mode_item = self.cb_mode.SelectedItem
        mode = mode_item.Content if mode_item else "Join"

        # Get switch order option
        switch_order = self.chk_switch_order.IsChecked

        # Confirm
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

        # Update UI
        self.btn_run.IsEnabled = False
        self.status_text.Text = "Running..."
        self.progress_bar.Visibility = Visibility.Visible
        self.progress_bar.Value = 0

        def progress_cb(current, total, message):
            if total > 0:
                pct = int(float(current) / float(total) * 100)
                self.progress_bar.Value = pct
            self.progress_text.Text = message

        # Run
        start = time.time()
        joined, skipped, errors, err_msg = run_join(
            self._rules, scope, mode, switch_order, progress_cb
        )
        elapsed = time.time() - start

        # Save rules for next time
        save_rules_to_file(self._rules)

        # Show results
        self.btn_run.IsEnabled = True
        self.progress_bar.Visibility = Visibility.Collapsed

        result_msg = (
            "Auto {} completed in {:.1f}s\n\n"
            "✓ {}ed: {}\n"
            "⊘ Already {}ed (skipped): {}\n"
            "✗ Errors: {}"
        ).format(
            mode, elapsed,
            mode, joined,
            mode.lower(), skipped,
            errors
        )

        if err_msg:
            result_msg += "\n\n⚠ {}".format(err_msg)

        self.status_text.Text = "Done – {}ed {} element pair(s)".format(
            mode.lower(), joined
        )
        self.progress_text.Text = "{} rule(s) | Last run: {} {}ed".format(
            len(self._rules), joined, mode.lower()
        )

        forms.alert(result_msg, title="Auto {} Results".format(mode))


# ==================================================
# QUICK JOIN (Shift+Click)
# ==================================================

def quick_join():
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
        DEFAULT_RULES, "Entire Project", "Join", True
    )
    elapsed = time.time() - start

    msg = (
        "Quick Auto Join completed in {:.1f}s\n\n"
        "✓ Joined: {}\n"
        "⊘ Already joined (skipped): {}\n"
        "✗ Errors: {}"
    ).format(elapsed, joined, skipped, errors)

    if err_msg:
        msg += "\n\n⚠ {}".format(err_msg)

    TaskDialog.Show("Auto Join Results", msg)


# ==================================================
# MAIN ENTRY POINT
# ==================================================

if __shiftclick__:
    quick_join()
else:
    AutoJoinWindow().ShowDialog()
