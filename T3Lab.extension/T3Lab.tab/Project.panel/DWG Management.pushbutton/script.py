# -*- coding: utf-8 -*-
"""
DWG Management

Manage CAD imports and CAD links in the current Revit project.
List, rename, and delete DWG imports and links directly from a single interface.

--------------------------------------------------------
Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
--------------------------------------------------------
"""

__author__  = "Tran Tien Thanh"
__title__   = "DWG\nManagement"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==================================================
import os
import sys

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

from System.Windows import WindowState
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ImportInstance,
    Transaction,
    ElementId,
)

from pyrevit import revit, forms, script

# PATH SETUP
# ==================================================
SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
LIB_DIR    = os.path.join(EXT_DIR, 'lib')
XAML_FILE  = os.path.join(LIB_DIR, 'GUI', 'Tools', 'DWGManagement.xaml')

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()

doc   = revit.doc
uidoc = revit.uidoc

# FILTER CONSTANTS
# ==================================================
FILTER_ALL     = "All"
FILTER_IMPORTS = "Imports Only"
FILTER_LINKS   = "Links Only"


# ==================================================
# DATA MODEL
# ==================================================

class DWGItem(object):
    """View-model for one CAD import or CAD link instance."""

    def __init__(self, instance, link_type, is_link, name, view_names, file_path):
        self.Instance   = instance    # ImportInstance element
        self.LinkType   = link_type   # CADLinkType element (may be None for imports)
        self.IsLink     = is_link     # bool
        self.DWGType    = "Link" if is_link else "Import"   # XAML binding: {Binding DWGType}
        self.Name       = name
        self.ViewNames  = view_names                         # XAML binding: {Binding ViewNames}
        self.ViewCount  = 1
        self.FilePath   = file_path
        self.IsSelected = False       # checkbox column binding


# ==================================================
# DATA COLLECTION HELPER
# ==================================================

def _collect_dwg_items(document):
    """Return a list of DWGItem for all ImportInstance elements in the document."""
    items = []
    try:
        instances = FilteredElementCollector(document)\
            .OfClass(ImportInstance)\
            .ToElements()
    except Exception as ex:
        logger.warning("Could not collect ImportInstances: {}".format(ex))
        return items

    for inst in instances:
        try:
            is_link   = inst.IsLinked
            type_id   = inst.GetTypeId()
            link_type = document.GetElement(type_id) if type_id != ElementId.InvalidElementId else None

            if link_type is not None:
                try:
                    name = link_type.Name
                except Exception:
                    name = "Unknown"
            else:
                try:
                    name = inst.Name
                except Exception:
                    name = "Unknown"

            file_path = ""
            if is_link and link_type is not None:
                try:
                    ext_ref = link_type.GetExternalFileReference()
                    if ext_ref is not None:
                        file_path = str(ext_ref.GetAbsolutePath())
                except Exception:
                    pass

            try:
                owner_view_id = inst.OwnerViewId
                if owner_view_id == ElementId.InvalidElementId:
                    view_names = "All Views (3D)"
                else:
                    view_elem  = document.GetElement(owner_view_id)
                    view_names = view_elem.Name if view_elem is not None else "Unknown View"
            except Exception:
                view_names = "Unknown View"

            items.append(DWGItem(inst, link_type, is_link, name, view_names, file_path))

        except Exception as item_ex:
            logger.warning("Skipped one ImportInstance: {}".format(item_ex))

    return items


# ==================================================
# WPF WINDOW
# ==================================================

class DWGManagementWindow(forms.WPFWindow):
    """WPF dialog for listing, renaming, and deleting CAD imports/links."""

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self._load_logo()

        self._all_items      = []
        self._filtered_items = []
        self._active_filter  = FILTER_ALL

        self._load_data()

    # --------------------------------------------------
    # Logo / Icon
    # --------------------------------------------------

    def _load_logo(self):
        try:
            logo_path = os.path.join(LIB_DIR, 'GUI', 'T3Lab_logo.png')
            if os.path.exists(logo_path):
                bmp = BitmapImage()
                bmp.BeginInit()
                bmp.UriSource = Uri(logo_path, UriKind.Absolute)
                bmp.EndInit()
                self.LogoImage.Source = bmp
                self.Icon = bmp
        except Exception as ex:
            logger.warning("Could not load logo: {}".format(ex))

    # --------------------------------------------------
    # Data loading
    # --------------------------------------------------

    def _load_data(self):
        try:
            self._all_items = _collect_dwg_items(doc)
        except Exception as ex:
            forms.alert("Error collecting CAD data:\n{}".format(ex), title="DWG Management")
            self._all_items = []

        self._apply_filter()
        self._update_status()

    def _apply_filter(self):
        search_text = ""
        try:
            search_text = (self.SearchBox.Text or "").strip().lower()
        except Exception:
            pass

        result = []
        for item in self._all_items:
            if self._active_filter == FILTER_IMPORTS and item.IsLink:
                continue
            if self._active_filter == FILTER_LINKS and not item.IsLink:
                continue
            if search_text:
                haystack = (item.Name + " " + item.ViewNames + " " + item.FilePath).lower()
                if search_text not in haystack:
                    continue
            result.append(item)

        self._filtered_items = result

        try:
            self.DWGDataGrid.ItemsSource = None
            self.DWGDataGrid.ItemsSource = self._filtered_items
        except Exception as ex:
            logger.warning("Could not bind grid: {}".format(ex))

    def _update_status(self):
        try:
            total   = len(self._all_items)
            imports = sum(1 for i in self._all_items if not i.IsLink)
            links   = sum(1 for i in self._all_items if i.IsLink)
            showing = len(self._filtered_items)

            msg = "Showing {} of {} item(s)   |   {} Import(s)   |   {} Link(s)".format(
                showing, total, imports, links)
            self.StatusText.Text  = msg
            self.StatusBarText.Text = msg
        except Exception:
            pass

    # --------------------------------------------------
    # Filter button handler (shared by all 3 toggle buttons)
    # --------------------------------------------------

    def filter_button_clicked(self, sender, e):
        # Enforce radio-button behavior: one filter active at a time
        sender.IsChecked = True  # prevent de-checking on re-click
        label = sender.Content if sender.Content else ""

        try:
            self.FilterAll.IsChecked     = (label == "All")
            self.FilterImports.IsChecked = (label == "Imports Only")
            self.FilterLinks.IsChecked   = (label == "Links Only")
        except Exception:
            pass

        if label == "Imports Only":
            self._active_filter = FILTER_IMPORTS
        elif label == "Links Only":
            self._active_filter = FILTER_LINKS
        else:
            self._active_filter = FILTER_ALL

        self._apply_filter()
        self._update_status()

    # --------------------------------------------------
    # Search handler
    # --------------------------------------------------

    def search_text_changed(self, sender, e):
        self._apply_filter()
        self._update_status()

    # --------------------------------------------------
    # Header checkbox: select / deselect all visible rows
    # --------------------------------------------------

    def header_checkbox_clicked(self, sender, e):
        checked = sender.IsChecked
        for item in self._filtered_items:
            item.IsSelected = checked if checked is not None else False
        # Rebind to refresh checkbox states
        try:
            self.DWGDataGrid.ItemsSource = None
            self.DWGDataGrid.ItemsSource = self._filtered_items
        except Exception:
            pass

    # --------------------------------------------------
    # Refresh
    # --------------------------------------------------

    def refresh_button_clicked(self, sender, e):
        self._load_data()

    # --------------------------------------------------
    # Rename
    # --------------------------------------------------

    def rename_button_clicked(self, sender, e):
        selected = self.DWGDataGrid.SelectedItem
        if selected is None:
            forms.alert("Please select a CAD item in the list.", title="Rename")
            return

        if not selected.IsLink:
            forms.alert(
                "Rename is only supported for CAD Links, not CAD Imports.\n\n"
                "Selected item '{}' is a CAD Import.".format(selected.Name),
                title="Rename"
            )
            return

        if selected.LinkType is None:
            forms.alert("Cannot rename this item — its type element could not be found.", title="Rename")
            return

        new_name = forms.ask_for_string(
            prompt="Enter a new name for the CAD link:",
            default=selected.Name,
            title="Rename CAD Link"
        )

        if new_name is None:
            return

        new_name = new_name.strip()
        if not new_name:
            forms.alert("Name cannot be empty.", title="Rename")
            return

        if new_name == selected.Name:
            return

        t = Transaction(doc, "T3Lab: Rename CAD Link")
        t.Start()
        try:
            selected.LinkType.Name = new_name
            t.Commit()
        except Exception as ex:
            t.RollBack()
            forms.alert("Failed to rename CAD link:\n{}".format(ex), title="Rename Error")
            return

        self._load_data()

    # --------------------------------------------------
    # Delete
    # --------------------------------------------------

    def delete_button_clicked(self, sender, e):
        # Prefer checkbox selection; fall back to DataGrid row selection
        selected_items = [item for item in self._filtered_items if item.IsSelected]

        if not selected_items:
            grid_sel = self.DWGDataGrid.SelectedItem
            if grid_sel is not None:
                selected_items = [grid_sel]

        if not selected_items:
            forms.alert(
                "Please select one or more CAD items to delete.\n\n"
                "Use the checkboxes or click a row to select.",
                title="Delete"
            )
            return

        count = len(selected_items)
        names = "\n".join("  - {}  [{}]".format(i.Name, i.DWGType) for i in selected_items[:10])
        if count > 10:
            names += "\n  ... and {} more".format(count - 10)

        confirmed = forms.alert(
            "Delete {} CAD item(s)?\n\n{}\n\n"
            "For CAD Links, the link type will also be removed if no other views reference it.\n\n"
            "This action cannot be undone.".format(count, names),
            title="Confirm Delete",
            yes=True,
            cancel=True
        )

        if not confirmed:
            return

        instance_ids  = list(set(i.Instance.Id for i in selected_items if i.Instance is not None))
        link_type_ids = list(set(i.LinkType.Id for i in selected_items if i.IsLink and i.LinkType is not None))

        t = Transaction(doc, "T3Lab: Delete CAD Items")
        t.Start()
        try:
            for eid in instance_ids:
                try:
                    doc.Delete(eid)
                except Exception as del_ex:
                    logger.warning("Could not delete element {}: {}".format(eid.IntegerValue, del_ex))

            # Delete orphaned CADLinkType elements (no remaining instances use them)
            remaining = list(FilteredElementCollector(doc).OfClass(ImportInstance).ToElements())
            for type_id in link_type_ids:
                still_used = any(
                    inst.GetTypeId().IntegerValue == type_id.IntegerValue
                    for inst in remaining
                )
                if not still_used:
                    try:
                        doc.Delete(type_id)
                    except Exception:
                        pass

            t.Commit()

        except Exception as ex:
            t.RollBack()
            forms.alert("Delete operation failed:\n{}".format(ex), title="Delete Error")
            return

        self._load_data()

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


# ==================================================
# ENTRY POINT
# ==================================================

if not os.path.isfile(XAML_FILE):
    forms.alert(
        "XAML file not found:\n{}\n\nCannot open DWG Management window.".format(XAML_FILE),
        title="DWG Management"
    )
    script.exit()

try:
    window = DWGManagementWindow()
    window.ShowDialog()
except Exception as launch_ex:
    forms.alert(
        "Failed to open DWG Management window:\n{}".format(launch_ex),
        title="DWG Management"
    )
