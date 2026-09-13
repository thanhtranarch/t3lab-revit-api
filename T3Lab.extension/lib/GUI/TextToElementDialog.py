# -*- coding: utf-8 -*-
"""Text to Element Dialog — transfers text note content to element parameters
via bounding-box spatial intersection in the active view.

Author: Tran Tien Thanh
"""

import os
import sys
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from pyrevit import forms
from GUI.WPF_Base import T3WPFWindow, to_items_source

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter,
    TextNote, Transaction, StorageType, ElementId
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter

import System
import System.Windows


# =============================================================================
# CONFIGURATION
# =============================================================================

TARGET_CATEGORIES = [
    ("Walls", BuiltInCategory.OST_Walls),
    ("Floors", BuiltInCategory.OST_Floors),
    ("Ceilings", BuiltInCategory.OST_Ceilings),
    ("Roofs", BuiltInCategory.OST_Roofs),
    ("Rooms", BuiltInCategory.OST_Rooms),
    ("Areas", BuiltInCategory.OST_Areas),
    ("Doors", BuiltInCategory.OST_Doors),
    ("Windows", BuiltInCategory.OST_Windows),
    ("Furniture", BuiltInCategory.OST_Furniture),
    ("Generic Models", BuiltInCategory.OST_GenericModel),
    ("Columns", BuiltInCategory.OST_Columns),
    ("Structural Columns", BuiltInCategory.OST_StructuralColumns),
    ("Structural Framing", BuiltInCategory.OST_StructuralFraming),
    ("Mechanical Equipment", BuiltInCategory.OST_MechanicalEquipment),
    ("Plumbing Fixtures", BuiltInCategory.OST_PlumbingFixtures),
    ("Casework", BuiltInCategory.OST_Casework),
    ("Detail Items", BuiltInCategory.OST_DetailComponents),
]

MM_TO_FEET = 1.0 / 304.8


# =============================================================================
# DATA MODELS
# =============================================================================

class PreviewRow(object):
    """Data row for DataGrid binding."""
    def __init__(self, text_content, element_name, element_id):
        self.TextContent = text_content
        self.ElementName = element_name
        self.ElementId = str(element_id)


class _CategoryItem(object):
    """Wrapper to display category names in ComboBox."""
    def __init__(self, name, bic):
        self.Name = name
        self.Bic = bic

    def __str__(self):
        return self.Name


# =============================================================================
# SELECTION FILTER
# =============================================================================

class TextNoteSelectionFilter(ISelectionFilter):
    __namespace__ = "T3Lab.TextToElement"
    """ISelectionFilter that only allows TextNote elements."""
    def AllowElement(self, element):
        return isinstance(element, TextNote)

    def AllowReference(self, reference, position):
        return False


# =============================================================================
# DIALOG CLASS
# =============================================================================

class TextToElementDialog(T3WPFWindow):
    """WPF dialog controller for Text to Element."""

    def __init__(self, revit_obj):
        self._app = revit_obj
        self._doc = revit_obj.ActiveUIDocument.Document
        self._uidoc = revit_obj.ActiveUIDocument
        self._text_notes = []
        self._transfer_list = []  # list of (text_content, element, param_name)

        xaml_path = os.path.join(
            os.path.dirname(__file__), 'Tools', 'TextToElement.xaml'
        )
        T3WPFWindow.__init__(self, xaml_path)

        # Wire up window chrome handlers
        self.btn_minimize.Click += self._on_minimize
        self.btn_maximize.Click += self._on_maximize
        self.btn_close_chrome.Click += self._on_close

        # Wire up control handlers
        self.btn_preview.Click += self._on_find_intersections
        self.btn_run.Click += self._on_run
        self.cmb_category.SelectionChanged += self._on_category_changed
        self.rb_from_view.Checked += self._on_source_mode_changed
        self.rb_pick_items.Checked += self._on_source_mode_changed

        if hasattr(self, "btn_ai_detect_target"):
            self.btn_ai_detect_target.Click += self._on_ai_detect_target

        self._populate_categories()
        self._update_source_info()
        self._init_ai_mode()

    # -------------------------------------------------------------------------
    # Window chrome
    # -------------------------------------------------------------------------

    def _on_minimize(self, sender, args):
        self.WindowState = System.Windows.WindowState.Minimized  # noqa

    def _on_maximize(self, sender, args):
        if self.WindowState == System.Windows.WindowState.Maximized:
            self.WindowState = System.Windows.WindowState.Normal
        else:
            self.WindowState = System.Windows.WindowState.Maximized

    def _on_close(self, sender, args):
        self.Close()

    # -------------------------------------------------------------------------
    # Initialisation helpers
    # -------------------------------------------------------------------------

    def _populate_categories(self):
        """Populate the category ComboBox from TARGET_CATEGORIES."""
        self.cmb_category.Items.Clear()
        for name, bic in TARGET_CATEGORIES:
            item = _CategoryItem(name, bic)
            self.cmb_category.Items.Add(item)

    def _update_source_info(self):
        """Refresh the source info label based on current radio selection."""
        if self.rb_from_view.IsChecked:
            try:
                view = self._doc.ActiveView
                collector = FilteredElementCollector(self._doc, view.Id)\
                    .OfClass(TextNote)\
                    .ToElements()
                count = len(list(collector))
                self.txt_source_info.Text = "{} text note(s) found in active view".format(count)
            except Exception as ex:
                self.txt_source_info.Text = "Could not count text notes: {}".format(str(ex))
        else:
            self.txt_source_info.Text = "Click 'Find Intersections' to pick text notes"

    def _init_ai_mode(self):
        """Initialize AI Mode status pill if AI is active."""
        try:
            if hasattr(self, "ai_mode_badge"):
                if self.is_ai_mode_active():
                    self.ai_mode_badge.Visibility = System.Windows.Visibility.Visible
                    info = self.get_ai_status_info()
                    provider = info.get("provider", "Ready")
                    model = info.get("model", "")
                    label = "AI: {}".format(provider)
                    if model:
                        label = "AI: {} ({})".format(provider, model)
                    if hasattr(self, "txt_ai_status"):
                        self.txt_ai_status.Text = label
                else:
                    self.ai_mode_badge.Visibility = System.Windows.Visibility.Collapsed
        except Exception:
            pass

    def _on_ai_detect_target(self, sender, args):
        """Analyze sample text notes from the active view and recommend target category and parameter."""
        if not self.is_ai_mode_active():
            forms.alert("AI Mode is disabled or not configured. Please enable AI Mode in LLMs Setting.", title="AI Mode Inactive")
            return

        try:
            view = self._doc.ActiveView
            collector = FilteredElementCollector(self._doc, view.Id).OfClass(TextNote).ToElements()
            samples = []
            for tn in list(collector)[:20]:
                txt = tn.Text.strip()
                if txt and len(txt) > 1 and txt not in samples:
                    samples.append(txt)
        except Exception as ex:
            samples = []

        if not samples:
            forms.alert("No text notes found in the active view to analyze.", title="No Text Notes")
            return

        self.txt_status.Text = "AI analyzing {} sample text notes in view...".format(len(samples))
        btn = getattr(self, "btn_ai_detect_target", None)
        orig_content = "✨ AI Detect Target"

        def _restore_btn():
            if btn:
                btn.Content = orig_content
                btn.IsEnabled = True

        if btn:
            btn.Content = "⏳ Detecting..."
            btn.IsEnabled = False

        system_prompt = (
            "You are an expert Autodesk Revit BIM specialist. "
            "Given sample text note annotations from an architectural or engineering drawing view, "
            "recommend the most likely Revit target category from: "
            "['Walls', 'Floors', 'Doors', 'Windows', 'Rooms', 'Structural Framing', 'Structural Columns', "
            "'Mechanical Equipment', 'Plumbing Fixtures', 'Generic Models', 'Detail Items'] "
            "and the target parameter name (e.g. 'Comments', 'Description', 'Mark', 'Type Mark'). "
            "Return JSON: {\"category\": \"<CategoryName>\", \"parameter\": \"<ParamName>\", \"confidence\": 0.0-1.0, \"reasoning\": \"<1-sentence explanation>\"}."
        )
        prompt = (
            "Sample text annotations in active view:\n{}\n\n"
            "Identify the target Revit category and parameter for these text annotations."
        ).format("\n".join("- " + s for s in samples[:15]))

        def _worker():
            return self.ai_bridge.ask_json(prompt, system_prompt=system_prompt)

        def _on_success(result):
            try:
                if not result or not isinstance(result, dict) or "category" not in result:
                    self.txt_status.Text = "AI analysis completed: No confident target match."
                    return

                cat_name = result.get("category", "")
                param_name = result.get("parameter", "")
                confidence = result.get("confidence", 0.0)
                reasoning = result.get("reasoning", "")

                matched_cat = False
                for item in self.cmb_category.Items:
                    if str(item).lower() == str(cat_name).lower():
                        self.cmb_category.SelectedItem = item
                        matched_cat = True
                        break

                if matched_cat and param_name:
                    for p in self.cmb_parameter.Items:
                        if str(p).lower() == str(param_name).lower():
                            self.cmb_parameter.SelectedItem = p
                            break

                try:
                    conf_pct = float(confidence) * 100
                except:
                    conf_pct = 90.0
                self.txt_status.Text = "AI suggested: {} -> {} ({:.0f}% - {})".format(
                    cat_name, param_name, conf_pct, reasoning
                )
            finally:
                _restore_btn()

        def _on_error(err):
            _restore_btn()
            self.txt_status.Text = "AI detection error: {}".format(err)

        self.run_ai_async(_worker, on_success=_on_success, on_error=_on_error)

    # -------------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------------

    def _on_source_mode_changed(self, sender, args):
        self._update_source_info()

    def _on_category_changed(self, sender, args):
        """When category changes, load parameters from sampled elements."""
        item = self.cmb_category.SelectedItem
        if item is None:
            self.cmb_parameter.IsEnabled = False
            self.cmb_parameter.Items.Clear()
            self.txt_param_hint.Text = "Select a category first"
            return

        self.txt_status.Text = "Loading parameters for {}...".format(item.Name)
        try:
            view = self._doc.ActiveView
            elements = self._get_elements_for_category(item.Bic, view)
            if not elements:
                self.cmb_parameter.IsEnabled = False
                self.cmb_parameter.Items.Clear()
                self.txt_param_hint.Text = "No {} found in active view".format(item.Name)
                self.txt_status.Text = "No elements found for selected category"
                return

            params = self._get_text_parameters(elements)
            self.cmb_parameter.Items.Clear()
            for p in params:
                self.cmb_parameter.Items.Add(p)

            if params:
                self.cmb_parameter.IsEnabled = True
                self.txt_param_hint.Text = "{} writable text parameter(s) found".format(len(params))
                self.txt_status.Text = "Category loaded — {} element(s) found".format(len(elements))
            else:
                self.cmb_parameter.IsEnabled = False
                self.txt_param_hint.Text = "No writable text parameters found for this category"
                self.txt_status.Text = "No writable text parameters found"
        except Exception as ex:
            self.txt_param_hint.Text = "Error loading parameters"
            self.txt_status.Text = "Error: {}".format(str(ex))

    def _on_find_intersections(self, sender, args):
        """Collect text notes, find intersecting elements, populate preview grid."""
        # Validate category and parameter selection
        cat_item = self.cmb_category.SelectedItem
        if cat_item is None:
            self._set_status("Select a target category first")
            return

        param_name = self.cmb_parameter.SelectedItem
        if param_name is None:
            self._set_status("Select a target parameter first")
            return

        # Get tolerance
        tolerance_feet = self._get_tolerance_feet()

        # Collect text notes
        if self.rb_pick_items.IsChecked:
            text_notes = self._pick_text_notes()
            if text_notes is None:
                # User cancelled pick
                return
        else:
            text_notes = self._get_text_notes_from_view()

        if not text_notes:
            self._set_status("No text notes found")
            self.txt_content_status.Text = "No text notes found in the active view"
            return

        # Get target elements
        try:
            view = self._doc.ActiveView
            elements = self._get_elements_for_category(cat_item.Bic, view)
        except Exception as ex:
            self._set_status("Error collecting elements: {}".format(str(ex)))
            return

        if not elements:
            self._set_status("No {} in active view".format(cat_item.Name))
            self.txt_content_status.Text = "No {} found in the active view".format(cat_item.Name)
            return

        # Find intersections
        rows = []
        self._transfer_list = []
        try:
            view = self._doc.ActiveView
            for tn in text_notes:
                text_content = self._get_text_content(tn)
                if not text_content:
                    continue
                for elem in elements:
                    if self._text_note_intersects_element(tn, elem, view, tolerance_feet):
                        elem_name = self._get_element_name(elem)
                        elem_id = self._get_element_id_str(elem)
                        rows.append(PreviewRow(text_content, elem_name, elem_id))
                        self._transfer_list.append((text_content, elem, str(param_name)))
        except Exception as ex:
            self._set_status("Error finding intersections: {}".format(str(ex)))
            return

        # Populate DataGrid
        self.dg_preview.ItemsSource = to_items_source(rows)

        if rows:
            self.btn_run.IsEnabled = True
            self._set_status("{} intersection(s) found".format(len(rows)))
            self.txt_content_status.Text = "{} intersection(s) ready to transfer".format(len(rows))
        else:
            self.btn_run.IsEnabled = False
            self._set_status("No intersections found — try a larger tolerance")
            self.txt_content_status.Text = "No intersections found. Try increasing the tolerance value."

    def _on_run(self, sender, args):
        """Execute the parameter transfer inside a transaction."""
        if not self._transfer_list:
            self._set_status("Nothing to transfer — run Find Intersections first")
            return

        success = 0
        fail = 0

        try:
            t = Transaction(self._doc, "T3Lab: Transfer Text to Elements")
            t.Start()
            try:
                for text_content, elem, param_name in self._transfer_list:
                    if self._set_parameter_value(elem, param_name, text_content):
                        success += 1
                    else:
                        fail += 1
                t.Commit()
            except Exception as ex:
                t.RollBack()
                self._set_status("Transaction failed: {}".format(str(ex)))
                self.txt_content_status.Text = "Transfer failed — transaction rolled back"
                return
        except Exception as ex:
            self._set_status("Error: {}".format(str(ex)))
            return

        msg = "Done — {} succeeded, {} failed".format(success, fail)
        self._set_status(msg)
        self.txt_content_status.Text = msg

    # -------------------------------------------------------------------------
    # Revit API helpers
    # -------------------------------------------------------------------------

    def _get_text_notes_from_view(self):
        """Return all TextNote instances visible in the active view."""
        try:
            view = self._doc.ActiveView
            collector = FilteredElementCollector(self._doc, view.Id)\
                .OfClass(TextNote)\
                .ToElements()
            return list(collector)
        except Exception:
            return []

    def _pick_text_notes(self):
        """Let user pick text notes interactively. Returns list or None on cancel."""
        try:
            refs = self._uidoc.Selection.PickObjects(
                ObjectType.Element,
                TextNoteSelectionFilter(),
                "Select text notes — press Finish (green check) or Escape to cancel"
            )
            result = []
            for ref in refs:
                elem = self._doc.GetElement(ref.ElementId)
                if elem is not None:
                    result.append(elem)
            return result
        except Exception:
            # User pressed Escape
            return None

    def _get_elements_for_category(self, bic, view):
        """Collect non-type elements of a BuiltInCategory in the given view."""
        try:
            collector = FilteredElementCollector(self._doc, view.Id)\
                .OfCategory(bic)\
                .WhereElementIsNotElementType()\
                .ToElements()
            return list(collector)
        except Exception:
            return []

    def _get_text_parameters(self, elements):
        """Sample first 20 elements and return sorted list of writable string param names."""
        param_names = set()
        for elem in elements[:20]:
            try:
                for param in elem.Parameters:
                    try:
                        if param.StorageType == StorageType.String and not param.IsReadOnly:
                            name = param.Definition.Name
                            if name:
                                param_names.add(name)
                    except Exception:
                        continue
            except Exception:
                continue
        return sorted(list(param_names))

    def _boxes_intersect(self, bb1, bb2, tolerance_feet):
        """Return True if the two bounding boxes overlap (2D, XY plane) within tolerance."""
        if bb1 is None or bb2 is None:
            return False
        x_overlap = (
            bb1.Min.X - tolerance_feet <= bb2.Max.X and
            bb1.Max.X + tolerance_feet >= bb2.Min.X
        )
        y_overlap = (
            bb1.Min.Y - tolerance_feet <= bb2.Max.Y and
            bb1.Max.Y + tolerance_feet >= bb2.Min.Y
        )
        return x_overlap and y_overlap

    def _point_in_box(self, pt, bb, tolerance_feet):
        """Return True if 2D point (X, Y) falls within bounding box plus tolerance."""
        if pt is None or bb is None:
            return False
        return (
            bb.Min.X - tolerance_feet <= pt.X <= bb.Max.X + tolerance_feet and
            bb.Min.Y - tolerance_feet <= pt.Y <= bb.Max.Y + tolerance_feet
        )

    def _text_note_intersects_element(self, tn, elem, view, tolerance_feet):
        """Check if a TextNote touches or points to an element via BB, Coord, or Leaders."""
        elem_bb = elem.get_BoundingBox(view)
        if elem_bb is None:
            return False

        # 1. Direct bounding box overlap
        tn_bb = tn.get_BoundingBox(view)
        if tn_bb is not None and self._boxes_intersect(tn_bb, elem_bb, tolerance_feet):
            return True

        # 2. Text Note insertion coordinate
        if hasattr(tn, "Coord") and tn.Coord is not None:
            if self._point_in_box(tn.Coord, elem_bb, tolerance_feet):
                return True

        # 3. Leader arrowheads pointing to the element
        try:
            leaders = tn.GetLeaders()
            if leaders:
                for leader in leaders:
                    if hasattr(leader, "End") and leader.End is not None:
                        if self._point_in_box(leader.End, elem_bb, tolerance_feet):
                            return True
        except Exception:
            pass

        return False

    def _get_text_content(self, text_note):
        """Return stripped text content of a TextNote."""
        try:
            return text_note.Text.strip()
        except Exception:
            return ""

    def _get_element_name(self, element):
        """Return a human-readable name for an element."""
        try:
            if element.Name:
                return element.Name
        except Exception:
            pass
        try:
            elem_type = self._doc.GetElement(element.GetTypeId())
            if elem_type is not None:
                param = elem_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                if param is not None:
                    return param.AsString()
        except Exception:
            pass
        return "Element {}".format(self._get_element_id_str(element))

    def _get_element_id_str(self, element):
        """Return element id as string — handles both IntegerValue and Value APIs."""
        try:
            return str(element.Id.IntegerValue)
        except Exception:
            try:
                return str(element.Id.Value)
            except Exception:
                return str(element.Id)

    def _set_parameter_value(self, element, param_name, value):
        """Set a writable string parameter by name on the element."""
        try:
            param = element.LookupParameter(param_name)
            if param is not None and not param.IsReadOnly:
                if param.StorageType == StorageType.String:
                    param.Set(value)
                    return True
        except Exception:
            pass
        return False

    def _get_tolerance_feet(self):
        """Parse tolerance from txt_tolerance (mm) and convert to feet."""
        try:
            val = float(self.txt_tolerance.Text.strip())
        except Exception:
            val = 150.0
        return val * MM_TO_FEET

    def _set_status(self, message):
        """Update the status bar label."""
        self.txt_status.Text = message


# =============================================================================
# PUBLIC API
# =============================================================================

def show_text_to_element(revit_obj):
    """Create and show the TextToElement dialog."""
    dlg = TextToElementDialog(revit_obj)
    dlg.ShowDialog()


# Allow direct execution for quick testing (will fail without Revit context)
if __name__ == '__main__':
    pass
