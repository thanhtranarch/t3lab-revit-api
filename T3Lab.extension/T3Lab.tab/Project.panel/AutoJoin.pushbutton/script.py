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
EXT_DIR       = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
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
