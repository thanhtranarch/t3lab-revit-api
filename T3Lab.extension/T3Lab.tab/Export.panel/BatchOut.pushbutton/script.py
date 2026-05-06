# -*- coding: utf-8 -*-
"""
Batch Out

Batch export sheets to PDF, DWG, DWF and other formats.

--------------------------------------------------------
Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/

--------------------------------------------------------
"""

__author__  = "Tran Tien Thanh"
__title__   = "Batch Out"
__version__ = "1.0.0"

# IMPORT LIBRARIES
# ==================================================
import os
import sys
import clr
import json
from datetime import datetime
from collections import defaultdict

clr.AddReference('System.Windows.Forms')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')
from System.Windows.Forms import FolderBrowserDialog, DialogResult
from System.Windows import Visibility, WindowState
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind, Action
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Threading import Thread, ThreadStart
from System.Windows.Threading import DispatcherPriority

from pyrevit import revit, DB, UI, forms, script
from Autodesk.Revit.DB import (
    Transaction, FilteredElementCollector, BuiltInCategory,
    ViewSheet, ViewSet, ViewSheetSet, DWGExportOptions, DWFExportOptions,
    DGNExportOptions, ExportDWGSettings, ACADVersion, PDFExportOptions,
    ImageExportOptions, ImageFileType, ImageResolution,
    PropOverrideMode, View, ViewPlan, ViewSection, View3D,
    ViewSchedule, ViewDrafting, ViewType,
)

from System.Collections.Generic import List


# Import API learner and updater modules
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

try:
    from api_learner import SmartAPIAdapter, RevitAPILearner
    from api_updater import auto_check_and_update
    HAS_API_LEARNER = True
except:
    HAS_API_LEARNER = False


# Try to import IFC export
try:
    from Autodesk.Revit.DB import IFCExportOptions, IFCVersion
    HAS_IFC = True
except:
    HAS_IFC = False

# Try to import Navisworks export
try:
    from Autodesk.Revit.DB import NavisworksExportOptions
    HAS_NAVISWORKS = True
except:
    HAS_NAVISWORKS = False

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()
output = script.get_output()

# Get Revit version information
REVIT_VERSION = int(revit.doc.Application.VersionNumber)  # e.g., 2023, 2024, 2025, 2026

# CLASS/FUNCTIONS
# ==================================================
class SheetItem(forms.Reactive):
    """Represents a sheet item in the list - optimized for performance."""
    def __init__(self, sheet, is_selected=False, lazy=False):
        self.Sheet = sheet
        self.IsSelected = is_selected
        self.SheetNumber = sheet.SheetNumber
        self.SheetName = sheet.Name
        self.Status = "Ready"
        self.Progress = 0
        self.Size = "-"
        self.Orientation = "-"
        self.Revision = ""
        self.RevisionDate = ""
        self.RevisionDescription = ""
        self.DrawnBy = ""
        self.CheckedBy = ""
        self.CustomFilename = ""

        if not lazy:
            self._load_revision_params()

    def _load_revision_params(self):
        """Load revision and metadata parameters. Called deferred for fast startup."""
        try:
            rev_param = self.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_CURRENT_REVISION)
            self.Revision = rev_param.AsString() if rev_param else ""
        except:
            self.Revision = ""

        try:
            rev_date_param = self.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_CURRENT_REVISION_DATE)
            self.RevisionDate = rev_date_param.AsString() if rev_date_param else ""
        except:
            self.RevisionDate = ""

        try:
            rev_desc_param = self.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_CURRENT_REVISION_DESCRIPTION)
            self.RevisionDescription = rev_desc_param.AsString() if rev_desc_param else ""
        except:
            self.RevisionDescription = ""

        try:
            drawn_param = self.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_DRAWN_BY)
            self.DrawnBy = drawn_param.AsString() if drawn_param else ""
        except:
            self.DrawnBy = ""

        try:
            checked_param = self.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_CHECKED_BY)
            self.CheckedBy = checked_param.AsString() if checked_param else ""
        except:
            self.CheckedBy = ""

    def __repr__(self):
        return "{} - {}".format(self.SheetNumber, self.SheetName)


class ViewItem(forms.Reactive):
    """Represents a view item in the list - optimized for performance."""

    _VIEW_TYPE_MAP = {
        ViewType.FloorPlan: "Floor Plan",
        ViewType.CeilingPlan: "Ceiling Plan",
        ViewType.Elevation: "Elevation",
        ViewType.Section: "Section",
        ViewType.ThreeD: "3D View",
        ViewType.Schedule: "Schedule",
        ViewType.DraftingView: "Drafting",
        ViewType.Legend: "Legend",
        ViewType.EngineeringPlan: "Engineering",
        ViewType.AreaPlan: "Area Plan",
    }

    def __init__(self, view, is_selected=False, lazy=False):
        self.View = view
        self.IsSelected = is_selected
        self.ViewName = view.Name
        self.SheetNumber = view.Name  # alias for column compatibility
        self.SheetName = ""

        # ViewType is a direct property — fast, always load
        try:
            self.ViewType = self._VIEW_TYPE_MAP.get(view.ViewType, str(view.ViewType))
        except:
            self.ViewType = "Unknown"
        self.SheetName = self.ViewType

        # Scale is a direct property — fast, always load
        try:
            self.Scale = "1:{}".format(view.Scale) if hasattr(view, 'Scale') and view.Scale else "-"
        except:
            self.Scale = "-"

        # Heavy lookups deferred when lazy=True
        self.Phase = "-"
        self.ViewTemplate = "-"
        if not lazy:
            self._load_extra_data()

        self.Status = "Ready"
        self.Progress = 0
        self.CustomFilename = ""
        self.Size = self.Scale
        self.Revision = self.Phase
        self.Orientation = "-"

    def _load_extra_data(self):
        """Load Phase and ViewTemplate — deferred for startup performance."""
        try:
            phase_param = self.View.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
            if phase_param:
                phase_id = phase_param.AsElementId()
                if phase_id and phase_id != DB.ElementId.InvalidElementId:
                    phase_elem = self.View.Document.GetElement(phase_id)
                    self.Phase = phase_elem.Name if phase_elem else "-"
        except:
            pass

        try:
            template_id = self.View.ViewTemplateId
            if template_id and template_id != DB.ElementId.InvalidElementId:
                template = self.View.Document.GetElement(template_id)
                self.ViewTemplate = template.Name if template else "-"
        except:
            pass

        self.Revision = self.Phase  # keep alias in sync

    def __repr__(self):
        return "{} ({})".format(self.ViewName, self.ViewType)


class ExportPreviewItem(object):
    """Represents an export preview item."""
    def __init__(self, item, format_name, size, orientation):
        # Support both SheetItem and ViewItem
        if hasattr(item, 'SheetNumber'):
            # It's a SheetItem
            self.SheetNumber = item.SheetNumber
            self.SheetName = item.SheetName
            self.ItemName = "{} - {}".format(item.SheetNumber, item.SheetName)
        elif hasattr(item, 'ViewName'):
            # It's a ViewItem
            self.SheetNumber = item.ViewName  # Use ViewName as identifier
            self.SheetName = item.ViewType
            self.ItemName = "{} ({})".format(item.ViewName, item.ViewType)
        else:
            self.SheetNumber = "Unknown"
            self.SheetName = "Unknown"
            self.ItemName = "Unknown"

        self.Format = format_name
        self.Size = size
        self.Orientation = orientation
        self.Progress = 0
        self.Status = ""
        self.ProgressText = ""


class ExportProfile(object):
    """Represents an export profile with all settings."""
    def __init__(self, name="", description=""):
        self.Name = name
        self.Description = description
        self.CreatedDate = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Export format settings
        self.ExportPDF = True
        self.ExportDWG = False
        self.ExportDGN = False
        self.ExportDWF = False
        self.ExportNWD = False
        self.ExportIFC = False
        self.ExportIMG = False

        # PDF settings
        self.PDFPaperSize = "Use Sheet Size"
        self.PDFOrientation = "Landscape"
        self.PDFZoom = "Fit to Page"
        self.PDFHideRefPlanes = False
        self.PDFHideScopeBoxes = False
        self.PDFHideCropBoundaries = False
        self.PDFHideUnreferencedTags = False
        self.PDFViewLinksBlue = False
        self.CombinePDF = False

        # DWG settings
        self.DWGVersion = "AutoCAD 2013"
        self.CADExportSetup = "Use setup from file"
        self.CADExportViewsOnSheets = False
        self.CADExportLinksAsExternal = False

        # File organization
        self.OutputFolder = os.path.join(os.path.expanduser('~'), 'Documents', 'Revit Exports')
        self.SplitByFormat = False
        self.ReverseOrder = False

        # Naming pattern - default to SheetNumber only for DWG batch export
        self.NamingPattern = "{SheetNumber}"

    def to_dict(self):
        """Convert profile to dictionary for JSON serialization."""
        return {
            'Name': self.Name,
            'Description': self.Description,
            'CreatedDate': self.CreatedDate,
            'ExportPDF': self.ExportPDF,
            'ExportDWG': self.ExportDWG,
            'ExportDGN': self.ExportDGN,
            'ExportDWF': self.ExportDWF,
            'ExportNWD': self.ExportNWD,
            'ExportIFC': self.ExportIFC,
            'ExportIMG': self.ExportIMG,
            'PDFPaperSize': self.PDFPaperSize,
            'PDFOrientation': self.PDFOrientation,
            'PDFZoom': self.PDFZoom,
            'PDFHideRefPlanes': self.PDFHideRefPlanes,
            'PDFHideScopeBoxes': self.PDFHideScopeBoxes,
            'PDFHideCropBoundaries': self.PDFHideCropBoundaries,
            'PDFHideUnreferencedTags': self.PDFHideUnreferencedTags,
            'PDFViewLinksBlue': self.PDFViewLinksBlue,
            'CombinePDF': self.CombinePDF,
            'DWGVersion': self.DWGVersion,
            'CADExportSetup': self.CADExportSetup,
            'CADExportViewsOnSheets': self.CADExportViewsOnSheets,
            'CADExportLinksAsExternal': self.CADExportLinksAsExternal,
            'OutputFolder': self.OutputFolder,
            'SplitByFormat': self.SplitByFormat,
            'ReverseOrder': self.ReverseOrder,
            'NamingPattern': self.NamingPattern
        }

    @staticmethod
    def from_dict(data):
        """Create profile from dictionary."""
        profile = ExportProfile()
        for key, value in data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        return profile


class ExportManagerWindow(forms.WPFWindow):
    """Export Manager Window."""

    def __init__(self):
        try:
            # Get absolute path to XAML file from lib/GUI folder
            extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            xaml_file_path = os.path.join(extension_dir, 'lib', 'GUI', 'Tools', 'ExportManager.xaml')
            forms.WPFWindow.__init__(self, xaml_file_path)

            self.doc = revit.doc
            self.all_sheets = []
            self.filtered_sheets = []
            self.all_views = []
            self.filtered_views = []
            self.export_items = []
            self.selection_mode = "sheets"  # "sheets" or "views"
            self.profiles = []  # List of ExportProfile objects
            self.profiles_folder = os.path.join(os.path.expanduser('~'), 'Documents', 'T3Lab_BatchOut_Profiles')

            # Performance optimization: caches for batch loading
            self._titleblock_size_cache = {}  # Sheet ID -> (width_mm, height_mm)
            self._paper_size_cache = {}  # Sheet ID -> (size_name, orientation)

            # naming pattern stored as plain object; default = sheet number only
            self.naming_pattern = type('_NP', (), {'Text': '{SheetNumber}'})()


            # Initialize Smart API Adapter for self-learning capability
            if HAS_API_LEARNER:
                try:
                    self.api_adapter = SmartAPIAdapter(self.doc, REVIT_VERSION)
                    logger.info("Smart API Adapter initialized successfully")

                    # Check for API updates (non-blocking, runs in background)
                    self._check_for_api_updates()
                except Exception as adapter_ex:
                    logger.warning("Could not initialize Smart API Adapter: {}".format(adapter_ex))
                    self.api_adapter = None
            else:
                self.api_adapter = None

            # Set default output folder
            default_folder = os.path.join(os.path.expanduser('~'), 'Documents', 'Revit Exports')
            self.output_folder.Text = default_folder

            # Load CAD export setups
            self.load_cad_export_setups()

            # Load sheet sets for filtering
            self.load_sheet_sets_for_filter()

            # Load sheets
            self.load_sheets()

            # Load profiles
            self.load_profiles()

            # Disable formats only if native libraries are not available
            if not HAS_NAVISWORKS:
                self.export_nwd.IsEnabled = False
                self.export_nwd.ToolTip = "Navisworks export not available — NavisworksExportOptions missing in this Revit version"

            if not HAS_IFC:
                self.export_ifc.IsEnabled = False
                self.export_ifc.ToolTip = "IFC export not available — IFCExportOptions missing in this Revit version"

            # Attach event handler for click-to-select functionality
            # This is done programmatically because EventSetters in Styles don't work with pyRevit
            self.sheets_listview.PreviewMouseLeftButtonDown += self.listview_clicked

            # Attach event handler for tab changes to update preview
            self.main_tabs.SelectionChanged += self.tab_changed

            # Update button text based on current tab
            self.update_navigation_buttons()

        except Exception as ex:
            logger.error("Error initializing BatchOut window: {}".format(ex))
            raise

    def _check_for_api_updates(self):
        """Check for API updates in the background (non-blocking)."""
        try:
            # Auto-check for updates (this runs on Fridays or if never checked)
            update_result = auto_check_and_update()

            if update_result.get('checked'):
                # Log the check
                logger.info("API update check performed")

                # Show notifications if any
                notifications = update_result.get('notifications', [])
                for notif in notifications:
                    if notif.get('severity') == 'critical':
                        output.print_md("**⚠ CRITICAL**: {}".format(notif.get('message', '')))
                    elif notif.get('severity') == 'warning':
                        output.print_md("**⚡ INFO**: {}".format(notif.get('message', '')))
                    else:
                        output.print_md("**ℹ**: {}".format(notif.get('message', '')))

                # Show learner info
                if self.api_adapter:
                    learner_info = self.api_adapter.get_learner_info()
                    logger.info("API Learner: Cached date: {}, Source: {}".format(
                        learner_info.get('cached_date'),
                        learner_info.get('learned_from')
                    ))

        except Exception as ex:
            # Don't fail initialization if update check fails
            logger.debug("API update check failed: {}".format(ex))

    def load_profiles(self):
        """Load all saved profiles from disk."""
        try:
            # Create profiles folder if it doesn't exist
            if not os.path.exists(self.profiles_folder):
                os.makedirs(self.profiles_folder)

            # Load all JSON files from profiles folder
            self.profiles = []
            if os.path.exists(self.profiles_folder):
                for filename in os.listdir(self.profiles_folder):
                    if filename.endswith('.json'):
                        filepath = os.path.join(self.profiles_folder, filename)
                        try:
                            with open(filepath, 'r') as f:
                                data = json.load(f)
                                profile = ExportProfile.from_dict(data)
                                self.profiles.append(profile)
                        except Exception as file_ex:
                            logger.warning("Could not load profile {}: {}".format(filename, file_ex))

            # Update profiles listview (only if dialog is open)
            if hasattr(self, 'profiles_listview') and self.profiles_listview:
                self.profiles_listview.ItemsSource = self.profiles
            logger.info("Loaded {} profiles".format(len(self.profiles)))

        except Exception as ex:
            logger.error("Error loading profiles: {}".format(ex))

    def save_profiles(self):
        """Save all profiles to disk."""
        try:
            # Create profiles folder if it doesn't exist
            if not os.path.exists(self.profiles_folder):
                os.makedirs(self.profiles_folder)

            # Save each profile as a JSON file
            for profile in self.profiles:
                # Create safe filename from profile name
                safe_name = "".join(c for c in profile.Name if c.isalnum() or c in (' ', '-', '_')).strip()
                filename = "{}.json".format(safe_name)
                filepath = os.path.join(self.profiles_folder, filename)

                try:
                    with open(filepath, 'w') as f:
                        json.dump(profile.to_dict(), f, indent=2)
                except Exception as file_ex:
                    logger.warning("Could not save profile {}: {}".format(profile.Name, file_ex))

            logger.info("Saved {} profiles".format(len(self.profiles)))

        except Exception as ex:
            logger.error("Error saving profiles: {}".format(ex))

    def get_current_settings_as_profile(self):
        """Capture current UI settings as a profile."""
        profile = ExportProfile()

        # Export formats
        profile.ExportPDF = self.export_pdf.IsChecked if self.export_pdf.IsChecked is not None else False
        profile.ExportDWG = self.export_dwg.IsChecked if self.export_dwg.IsChecked is not None else False
        profile.ExportDGN = self.export_dgn.IsChecked if self.export_dgn.IsChecked is not None else False
        profile.ExportDWF = self.export_dwf.IsChecked if self.export_dwf.IsChecked is not None else False
        profile.ExportNWD = self.export_nwd.IsChecked if self.export_nwd.IsChecked is not None else False
        profile.ExportIFC = self.export_ifc.IsChecked if self.export_ifc.IsChecked is not None else False
        profile.ExportIMG = self.export_img.IsChecked if self.export_img.IsChecked is not None else False

        # PDF settings
        if self.pdf_paper_size.SelectedItem:
            profile.PDFPaperSize = self.pdf_paper_size.SelectedItem.Content
        profile.PDFOrientation = "Landscape" if self.pdf_landscape.IsChecked else "Portrait"
        profile.PDFZoom = "Fit to Page" if self.pdf_fit_to_page.IsChecked else "Custom Zoom"
        profile.PDFHideRefPlanes = self.pdf_hide_ref_planes.IsChecked if self.pdf_hide_ref_planes.IsChecked is not None else False
        profile.PDFHideScopeBoxes = self.pdf_hide_scope_boxes.IsChecked if self.pdf_hide_scope_boxes.IsChecked is not None else False
        profile.PDFHideCropBoundaries = self.pdf_hide_crop_boundaries.IsChecked if self.pdf_hide_crop_boundaries.IsChecked is not None else False
        profile.PDFHideUnreferencedTags = self.pdf_hide_unreferenced_tags.IsChecked if self.pdf_hide_unreferenced_tags.IsChecked is not None else False
        profile.PDFViewLinksBlue = self.pdf_view_links_blue.IsChecked if self.pdf_view_links_blue.IsChecked is not None else False
        profile.CombinePDF = self.combine_pdf.IsChecked if self.combine_pdf.IsChecked is not None else False

        # DWG settings
        if self.dwg_version.SelectedItem:
            profile.DWGVersion = self.dwg_version.SelectedItem.Content
        if self.cad_export_setup.SelectedItem:
            profile.CADExportSetup = self.cad_export_setup.SelectedItem.Content
        profile.CADExportViewsOnSheets = self.cad_export_views_on_sheets.IsChecked if self.cad_export_views_on_sheets.IsChecked is not None else False
        profile.CADExportLinksAsExternal = self.cad_export_links_as_external.IsChecked if self.cad_export_links_as_external.IsChecked is not None else False

        # File organization
        profile.OutputFolder = self.output_folder.Text if self.output_folder.Text else ""
        profile.SplitByFormat = self.save_split_by_format.IsChecked if self.save_split_by_format.IsChecked is not None else False
        profile.ReverseOrder = self.reverse_order.IsChecked if self.reverse_order.IsChecked is not None else False

        # Naming pattern
        profile.NamingPattern = self.naming_pattern.Text if self.naming_pattern.Text else "{SheetNumber}-{SheetName}"

        return profile

    def apply_profile_to_ui(self, profile):
        """Apply profile settings to UI controls."""
        try:
            # Export formats
            self.export_pdf.IsChecked = profile.ExportPDF
            self.export_dwg.IsChecked = profile.ExportDWG
            self.export_dgn.IsChecked = profile.ExportDGN
            self.export_dwf.IsChecked = profile.ExportDWF
            self.export_nwd.IsChecked = profile.ExportNWD
            self.export_ifc.IsChecked = profile.ExportIFC
            self.export_img.IsChecked = profile.ExportIMG

            # PDF settings
            # Set paper size
            for i in range(self.pdf_paper_size.Items.Count):
                if self.pdf_paper_size.Items[i].Content == profile.PDFPaperSize:
                    self.pdf_paper_size.SelectedIndex = i
                    break

            # Set orientation
            if profile.PDFOrientation == "Landscape":
                self.pdf_landscape.IsChecked = True
            else:
                self.pdf_portrait.IsChecked = True

            # Set zoom
            if profile.PDFZoom == "Fit to Page":
                self.pdf_fit_to_page.IsChecked = True
            else:
                self.pdf_zoom_custom.IsChecked = True

            self.pdf_hide_ref_planes.IsChecked = profile.PDFHideRefPlanes
            self.pdf_hide_scope_boxes.IsChecked = profile.PDFHideScopeBoxes
            self.pdf_hide_crop_boundaries.IsChecked = profile.PDFHideCropBoundaries
            self.pdf_hide_unreferenced_tags.IsChecked = profile.PDFHideUnreferencedTags
            self.pdf_view_links_blue.IsChecked = profile.PDFViewLinksBlue
            self.combine_pdf.IsChecked = profile.CombinePDF

            # DWG settings
            for i in range(self.dwg_version.Items.Count):
                if self.dwg_version.Items[i].Content == profile.DWGVersion:
                    self.dwg_version.SelectedIndex = i
                    break

            for i in range(self.cad_export_setup.Items.Count):
                if self.cad_export_setup.Items[i].Content == profile.CADExportSetup:
                    self.cad_export_setup.SelectedIndex = i
                    break

            self.cad_export_views_on_sheets.IsChecked = profile.CADExportViewsOnSheets
            self.cad_export_links_as_external.IsChecked = profile.CADExportLinksAsExternal

            # File organization
            self.output_folder.Text = profile.OutputFolder
            if profile.SplitByFormat:
                self.save_split_by_format.IsChecked = True
            else:
                self.save_same_folder.IsChecked = True
            self.reverse_order.IsChecked = profile.ReverseOrder

            # Naming pattern
            self.naming_pattern.Text = profile.NamingPattern

            self.status_text.Text = "Profile '{}' loaded successfully".format(profile.Name)

        except Exception as ex:
            logger.error("Error applying profile to UI: {}".format(ex))
            forms.alert("Error applying profile:\n{}".format(str(ex)))

    def save_profile_clicked(self, sender, e):
        """Save current settings as a new profile."""
        try:
            # Prompt for profile name and description
            from System.Windows import Window, TextBlock, TextBox, Button, Thickness, VerticalAlignment, HorizontalAlignment
            from System.Windows.Controls import StackPanel, Label
            from System.Windows.Media import SolidColorBrush, Color

            # Create dialog window
            dialog = Window()
            dialog.Title = "Save Profile"
            dialog.Width = 400
            dialog.Height = 250
            dialog.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterOwner
            dialog.Owner = self

            # Create content panel
            panel = StackPanel()
            panel.Margin = Thickness(20)

            # Name label and textbox
            name_label = TextBlock()
            name_label.Text = "Profile Name:"
            name_label.Margin = Thickness(0, 0, 0, 5)
            panel.Children.Add(name_label)

            name_textbox = TextBox()
            name_textbox.Height = 28
            name_textbox.Margin = Thickness(0, 0, 0, 15)
            panel.Children.Add(name_textbox)

            # Description label and textbox
            desc_label = TextBlock()
            desc_label.Text = "Description (optional):"
            desc_label.Margin = Thickness(0, 0, 0, 5)
            panel.Children.Add(desc_label)

            desc_textbox = TextBox()
            desc_textbox.Height = 60
            desc_textbox.TextWrapping = System.Windows.TextWrapping.Wrap
            desc_textbox.AcceptsReturn = True
            desc_textbox.VerticalScrollBarVisibility = System.Windows.Controls.ScrollBarVisibility.Auto
            desc_textbox.Margin = Thickness(0, 0, 0, 20)
            panel.Children.Add(desc_textbox)

            # Buttons panel
            buttons_panel = StackPanel()
            buttons_panel.Orientation = System.Windows.Controls.Orientation.Horizontal
            buttons_panel.HorizontalAlignment = HorizontalAlignment.Right

            # OK button
            ok_button = Button()
            ok_button.Content = "Save"
            ok_button.Width = 80
            ok_button.Height = 28
            ok_button.Margin = Thickness(0, 0, 10, 0)

            def ok_clicked(s, ev):
                if not name_textbox.Text or name_textbox.Text.strip() == "":
                    forms.alert("Please enter a profile name.", title="Profile Name Required")
                    return
                dialog.DialogResult = True
                dialog.Close()

            ok_button.Click += ok_clicked
            buttons_panel.Children.Add(ok_button)

            # Cancel button
            cancel_button = Button()
            cancel_button.Content = "Cancel"
            cancel_button.Width = 80
            cancel_button.Height = 28

            def cancel_clicked(s, ev):
                dialog.DialogResult = False
                dialog.Close()

            cancel_button.Click += cancel_clicked
            buttons_panel.Children.Add(cancel_button)

            panel.Children.Add(buttons_panel)
            dialog.Content = panel

            # Show dialog
            result = dialog.ShowDialog()

            if result:
                # Create profile from current settings
                profile = self.get_current_settings_as_profile()
                profile.Name = name_textbox.Text.strip()
                profile.Description = desc_textbox.Text.strip()

                # Add to profiles list
                self.profiles.append(profile)

                # Save to disk
                self.save_profiles()

                # Refresh listview
                self.profiles_listview.ItemsSource = None
                self.profiles_listview.ItemsSource = self.profiles

                self.status_text.Text = "Profile '{}' saved successfully".format(profile.Name)

        except Exception as ex:
            logger.error("Error saving profile: {}".format(ex))
            forms.alert("Error saving profile:\n{}".format(str(ex)))

    def load_profile_clicked(self, sender, e):
        """Load selected profile and apply to UI."""
        try:
            selected_profile = self.profiles_listview.SelectedItem
            if not selected_profile:
                forms.alert("Please select a profile to load.", title="No Profile Selected")
                return

            # Apply profile to UI
            self.apply_profile_to_ui(selected_profile)

        except Exception as ex:
            logger.error("Error loading profile: {}".format(ex))
            forms.alert("Error loading profile:\n{}".format(str(ex)))

    def delete_profile_clicked(self, sender, e):
        """Delete selected profile."""
        try:
            selected_profile = self.profiles_listview.SelectedItem
            if not selected_profile:
                forms.alert("Please select a profile to delete.", title="No Profile Selected")
                return

            # Confirm deletion
            if not forms.alert("Are you sure you want to delete profile '{}'?".format(selected_profile.Name),
                              title="Confirm Deletion",
                              yes=True, no=True):
                return

            # Remove from list
            self.profiles.remove(selected_profile)

            # Delete file
            safe_name = "".join(c for c in selected_profile.Name if c.isalnum() or c in (' ', '-', '_')).strip()
            filename = "{}.json".format(safe_name)
            filepath = os.path.join(self.profiles_folder, filename)
            if os.path.exists(filepath):
                os.remove(filepath)

            # Refresh listview
            self.profiles_listview.ItemsSource = None
            self.profiles_listview.ItemsSource = self.profiles

            self.status_text.Text = "Profile '{}' deleted successfully".format(selected_profile.Name)

        except Exception as ex:
            logger.error("Error deleting profile: {}".format(ex))
            forms.alert("Error deleting profile:\n{}".format(str(ex)))

    def import_profile_clicked(self, sender, e):
        """Import profile from file."""
        try:
            from System.Windows.Forms import OpenFileDialog, DialogResult

            # Show open file dialog
            dialog = OpenFileDialog()
            dialog.Title = "Import Profile"
            dialog.Filter = "Profile Files (*.json)|*.json|All Files (*.*)|*.*"
            dialog.FilterIndex = 1

            if dialog.ShowDialog() == DialogResult.OK:
                # Load profile from file
                with open(dialog.FileName, 'r') as f:
                    data = json.load(f)
                    profile = ExportProfile.from_dict(data)

                # Check if profile with same name already exists
                existing = [p for p in self.profiles if p.Name == profile.Name]
                if existing:
                    if not forms.alert("A profile with name '{}' already exists.\n\nDo you want to replace it?".format(profile.Name),
                                      title="Profile Exists",
                                      yes=True, no=True):
                        return
                    # Remove existing profile
                    for p in existing:
                        self.profiles.remove(p)

                # Add to profiles list
                self.profiles.append(profile)

                # Save to disk
                self.save_profiles()

                # Refresh listview
                self.profiles_listview.ItemsSource = None
                self.profiles_listview.ItemsSource = self.profiles

                self.status_text.Text = "Profile '{}' imported successfully".format(profile.Name)

        except Exception as ex:
            logger.error("Error importing profile: {}".format(ex))
            forms.alert("Error importing profile:\n{}".format(str(ex)))

    def export_profile_clicked(self, sender, e):
        """Export selected profile to file."""
        try:
            selected_profile = self.profiles_listview.SelectedItem
            if not selected_profile:
                forms.alert("Please select a profile to export.", title="No Profile Selected")
                return

            from System.Windows.Forms import SaveFileDialog, DialogResult

            # Show save file dialog
            dialog = SaveFileDialog()
            dialog.Title = "Export Profile"
            dialog.Filter = "Profile Files (*.json)|*.json|All Files (*.*)|*.*"
            dialog.FilterIndex = 1
            dialog.FileName = "{}.json".format(selected_profile.Name)

            if dialog.ShowDialog() == DialogResult.OK:
                # Save profile to file
                with open(dialog.FileName, 'w') as f:
                    json.dump(selected_profile.to_dict(), f, indent=2)

                self.status_text.Text = "Profile '{}' exported to {}".format(
                    selected_profile.Name, os.path.basename(dialog.FileName))

                forms.alert("Profile exported successfully to:\n{}".format(dialog.FileName),
                           title="Export Complete")

        except Exception as ex:
            logger.error("Error exporting profile: {}".format(ex))
            forms.alert("Error exporting profile:\n{}".format(str(ex)))

    # Pre-computed paper sizes lookup table for O(1) matching
    # Format: (rounded_width, rounded_height) -> size_name
    # We round dimensions to nearest 5mm for tolerance matching
    PAPER_SIZES_MM = {
        # ISO A Series (most common)
        "A0": (1189, 841),
        "A1": (841, 594),
        "A2": (594, 420),
        "A3": (420, 297),
        "A4": (297, 210),
        "A5": (210, 148),
        # ISO B Series
        "B0": (1414, 1000),
        "B1": (1000, 707),
        "B2": (707, 500),
        "B3": (500, 353),
        "B4": (353, 250),
        "B5": (250, 176),
        # ANSI Series (US standard)
        "ANSI A": (279, 216),
        "ANSI B": (432, 279),
        "ANSI C": (559, 432),
        "ANSI D": (864, 559),
        "ANSI E": (1118, 864),
        # ARCH Series (Architectural)
        "ARCH A": (305, 229),
        "ARCH B": (457, 305),
        "ARCH C": (610, 457),
        "ARCH D": (914, 610),
        "ARCH E": (1219, 914),
        "ARCH E1": (1067, 762),
        # Common named sizes
        "Letter": (279, 216),
        "Legal": (356, 216),
        "Tabloid": (432, 279),
        "Ledger": (432, 279),
    }

    def _batch_load_titleblock_sizes(self):
        """Batch load all titleblock dimensions in ONE query for performance.

        This replaces the per-sheet FilteredElementCollector approach which was
        causing O(n) queries. Now we do 1 query for ALL titleblocks and build
        a lookup cache.
        """
        try:
            # Clear existing cache
            self._titleblock_size_cache = {}

            # Single query to get ALL titleblocks in the document
            all_titleblocks = FilteredElementCollector(self.doc)\
                .OfCategory(BuiltInCategory.OST_TitleBlocks)\
                .WhereElementIsNotElementType()\
                .ToElements()

            # Build cache: OwnerViewId (Sheet ID) -> (width_mm, height_mm)
            for tb in all_titleblocks:
                try:
                    owner_view_id = tb.OwnerViewId
                    if owner_view_id and owner_view_id != DB.ElementId.InvalidElementId:
                        # Get dimensions
                        width_param = tb.get_Parameter(DB.BuiltInParameter.SHEET_WIDTH)
                        height_param = tb.get_Parameter(DB.BuiltInParameter.SHEET_HEIGHT)

                        if width_param and height_param:
                            width_mm = width_param.AsDouble() * 304.8
                            height_mm = height_param.AsDouble() * 304.8
                            self._titleblock_size_cache[owner_view_id.IntegerValue] = (width_mm, height_mm)
                except:
                    continue

            logger.debug("Batch loaded {} titleblock sizes".format(len(self._titleblock_size_cache)))

        except Exception as ex:
            logger.debug("Error batch loading titleblocks: {}".format(ex))
            self._titleblock_size_cache = {}

    def _detect_paper_size_from_dimensions(self, width_mm, height_mm):
        """Fast paper size detection from dimensions using pre-computed lookup.

        Uses tolerance matching with O(n) worst case but optimized with
        early exit for common sizes (A-series checked first).
        """
        # Determine orientation and normalize to landscape
        if width_mm > height_mm:
            orientation = "Landscape"
        else:
            orientation = "Portrait"
            width_mm, height_mm = height_mm, width_mm

        tolerance = 10  # mm

        # Check most common sizes first for early exit (A-series)
        priority_sizes = ["A3", "A1", "A2", "A0", "A4", "ARCH D", "ARCH E", "ANSI D", "ANSI E"]

        for size_name in priority_sizes:
            if size_name in self.PAPER_SIZES_MM:
                std_width, std_height = self.PAPER_SIZES_MM[size_name]
                if (abs(width_mm - std_width) < tolerance and
                    abs(height_mm - std_height) < tolerance):
                    return (size_name, orientation)

        # Check remaining sizes
        for size_name, (std_width, std_height) in self.PAPER_SIZES_MM.items():
            if size_name in priority_sizes:
                continue  # Already checked
            if (abs(width_mm - std_width) < tolerance and
                abs(height_mm - std_height) < tolerance):
                return (size_name, orientation)

        return ("Use Sheet Size", orientation)

    def get_sheet_paper_size_and_orientation(self, sheet):
        """Auto-detect paper size and orientation from Title Block parameters.

        Returns tuple: (paper_size, orientation)
        Uses cached titleblock data for performance (batch loaded).
        """
        try:
            sheet_id = sheet.Id.IntegerValue

            # Check paper size cache first
            if sheet_id in self._paper_size_cache:
                return self._paper_size_cache[sheet_id]

            # Check titleblock size cache
            if sheet_id in self._titleblock_size_cache:
                width_mm, height_mm = self._titleblock_size_cache[sheet_id]
                result = self._detect_paper_size_from_dimensions(width_mm, height_mm)
                self._paper_size_cache[sheet_id] = result
                return result

            # Fallback: query directly if not in cache (for dynamically added sheets)
            collector = FilteredElementCollector(self.doc, sheet.Id)\
                .OfCategory(BuiltInCategory.OST_TitleBlocks)\
                .WhereElementIsNotElementType()

            for tb in collector:
                width_param = tb.get_Parameter(DB.BuiltInParameter.SHEET_WIDTH)
                height_param = tb.get_Parameter(DB.BuiltInParameter.SHEET_HEIGHT)

                if width_param and height_param:
                    width_mm = width_param.AsDouble() * 304.8
                    height_mm = height_param.AsDouble() * 304.8
                    # Cache for future use
                    self._titleblock_size_cache[sheet_id] = (width_mm, height_mm)
                    result = self._detect_paper_size_from_dimensions(width_mm, height_mm)
                    self._paper_size_cache[sheet_id] = result
                    return result
                break

            result = ("Use Sheet Size", "Landscape")
            self._paper_size_cache[sheet_id] = result
            return result

        except Exception as ex:
            logger.debug("Could not auto-detect paper size for sheet: {}".format(ex))
            return ("Use Sheet Size", "Landscape")

    def load_cad_export_setups(self):
        """Load available DWG export setups from the document."""
        try:
            # Clear existing items
            self.cad_export_setup.Items.Clear()

            # Add default option
            from System.Windows.Controls import ComboBoxItem
            default_item = ComboBoxItem()
            default_item.Content = "Use setup from file (Default)"
            self.cad_export_setup.Items.Add(default_item)

            # Get all export settings from the document
            collector = FilteredElementCollector(self.doc)\
                .OfClass(ExportDWGSettings)

            # Add each export setup to the combo box
            for setup in collector:
                try:
                    setup_name = setup.Name if hasattr(setup, 'Name') else "Setup {}".format(setup.Id.IntegerValue)
                    item = ComboBoxItem()
                    item.Content = setup_name
                    item.Tag = setup  # Store the setup object for later use
                    self.cad_export_setup.Items.Add(item)
                except:
                    pass

            # Select the first item (default)
            self.cad_export_setup.SelectedIndex = 0

        except Exception as ex:
            logger.warning("Could not load CAD export setups: {}".format(ex))
            # Add just the default if there's an error
            from System.Windows.Controls import ComboBoxItem
            default_item = ComboBoxItem()
            default_item.Content = "Use setup from file (Default)"
            self.cad_export_setup.Items.Add(default_item)
            self.cad_export_setup.SelectedIndex = 0

    def load_sheet_sets_for_filter(self):
        """Populate the multi-select sheet set dropdown with CheckBoxes."""
        try:
            from System.Windows.Controls import CheckBox
            from System.Windows import Thickness

            self.sheet_set_checklist.Children.Clear()

            # "All Sheets/Views" checkbox — checked by default
            all_cb = CheckBox()
            all_cb.Content = "All Sheets/Views"
            all_cb.IsChecked = True
            all_cb.Tag = None
            all_cb.Margin = Thickness(4, 3, 4, 3)
            all_cb.Checked += self._sheet_set_all_checked
            self._sheet_set_all_checkbox = all_cb
            self.sheet_set_checklist.Children.Add(all_cb)

            # Individual set checkboxes
            saved_set_names = self.get_saved_sheet_set_names()
            if saved_set_names:
                from System.Windows.Controls import Separator
                sep = Separator()
                sep.Margin = Thickness(0, 2, 0, 2)
                self.sheet_set_checklist.Children.Add(sep)

                for set_name in sorted(saved_set_names):
                    cb = CheckBox()
                    cb.Content = set_name
                    cb.IsChecked = False
                    cb.Tag = set_name
                    cb.Margin = Thickness(4, 3, 4, 3)
                    cb.Checked += self._sheet_set_item_changed
                    cb.Unchecked += self._sheet_set_item_changed
                    self.sheet_set_checklist.Children.Add(cb)
            else:
                from System.Windows.Controls import TextBlock as TB
                from System.Windows.Media import SolidColorBrush, Color
                hint = TB()
                hint.Text = "(No saved Sheet Sets in this document)"
                hint.Foreground = SolidColorBrush(Color.FromRgb(0x7F, 0x8C, 0x8D))
                hint.Margin = Thickness(6, 4, 4, 4)
                self.sheet_set_checklist.Children.Add(hint)

        except Exception as ex:
            logger.warning("Could not load sheet sets for filter: {}".format(ex))

    def sheet_set_toggle_clicked(self, sender, e):
        """Toggle the multi-select sheet set popup open/closed."""
        try:
            self.sheet_set_popup.IsOpen = not self.sheet_set_popup.IsOpen
            # Keep toggle button appearance in sync with popup state
            if not self.sheet_set_popup.IsOpen:
                self.sheet_set_toggle.IsChecked = False
        except Exception as ex:
            logger.debug("Error toggling sheet set popup: {}".format(ex))

    def _sheet_set_all_checked(self, sender, e):
        """When 'All' is checked, uncheck every individual set and clear filter."""
        try:
            for child in self.sheet_set_checklist.Children:
                if hasattr(child, 'Tag') and child.Tag is not None:
                    child.IsChecked = False
            self._update_sheet_set_label()
            self.apply_filters()
            self.update_selection_count()
        except Exception as ex:
            logger.debug("Error in sheet set all-checked: {}".format(ex))

    def _sheet_set_item_changed(self, sender, e):
        """When any individual set checkbox changes, update filter and label."""
        try:
            any_checked = any(
                child.IsChecked
                for child in self.sheet_set_checklist.Children
                if hasattr(child, 'Tag') and child.Tag is not None
            )
            # Uncheck "All" silently if any individual set is checked
            self._sheet_set_all_checkbox.Checked -= self._sheet_set_all_checked
            self._sheet_set_all_checkbox.IsChecked = not any_checked
            self._sheet_set_all_checkbox.Checked += self._sheet_set_all_checked

            self._update_sheet_set_label()
            self._apply_sheet_set_filter()
        except Exception as ex:
            logger.debug("Error in sheet set item changed: {}".format(ex))

    def _update_sheet_set_label(self):
        """Update the toggle button text to reflect current selection."""
        try:
            checked = [
                child.Content
                for child in self.sheet_set_checklist.Children
                if hasattr(child, 'Tag') and child.Tag is not None and child.IsChecked
            ]
            if not checked:
                self.sheet_set_label.Text = "All Sheets/Views"
            elif len(checked) == 1:
                self.sheet_set_label.Text = checked[0]
            else:
                self.sheet_set_label.Text = "{} sets selected".format(len(checked))
        except Exception as ex:
            logger.debug("Error updating sheet set label: {}".format(ex))

    def _apply_sheet_set_filter(self):
        """Union sheet IDs from all checked sets and apply filter + auto-select."""
        try:
            checked_sets = [
                child.Tag
                for child in self.sheet_set_checklist.Children
                if hasattr(child, 'Tag') and child.Tag is not None and child.IsChecked
            ]
            if not checked_sets:
                self.apply_filters()
                return

            # Union of all sheet IDs across checked sets
            all_ids = set()
            for set_name in checked_sets:
                ids = self.get_sheet_ids_from_set(set_name)
                all_ids.update(ids)

            if not all_ids:
                self.status_text.Text = "No sheets found in selected sets"
                return

            self.apply_filters(sheet_set_ids=list(all_ids))

            # Auto-select sheets belonging to any checked set
            selected_count = 0
            for sheet_item in self.all_sheets:
                if sheet_item.Sheet.Id in all_ids:
                    sheet_item.IsSelected = True
                    selected_count += 1
                else:
                    sheet_item.IsSelected = False

            self.sheets_listview.Items.Refresh()
            self.update_selection_count()
            self.status_text.Text = "'{}': {} sheets selected".format(
                self.sheet_set_label.Text, selected_count)

        except Exception as ex:
            logger.error("Error applying sheet set filter: {}".format(ex))

    def load_sheets(self):
        """Load all sheets - Phase 1: instant display of names, Phase 2: progressive background load."""
        try:
            # Phase 1: collect sheet elements (single fast query) + display names immediately
            sheets_collector = FilteredElementCollector(self.doc)\
                .OfCategory(BuiltInCategory.OST_Sheets)\
                .WhereElementIsNotElementType()

            sheets = [s for s in sheets_collector if isinstance(s, ViewSheet)]
            sheets.sort(key=lambda x: x.SheetNumber)

            # Create minimal SheetItems (lazy=True skips all parameter accesses)
            self.all_sheets = [SheetItem(sheet, False, lazy=True) for sheet in sheets]
            self.filtered_sheets = list(self.all_sheets)

            # Show sheet names immediately
            self.update_sheets_list()
            self.status_text.Text = "Loaded {} sheets | Revit {}".format(
                len(self.all_sheets), REVIT_VERSION)

            # Phase 2: pre-load titleblocks first (one query), then schedule chunked loading
            # Runs after window renders so user sees sheet names without delay
            self._lazy_load_index = 0
            self.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                Action(self._lazy_load_init)
            )

        except Exception as ex:
            logger.error("Error loading sheets: {}".format(ex))
            forms.alert("Error loading sheets: {}".format(ex), exitscript=True)

    def _lazy_load_init(self):
        """Kick off chunked Revision loading — no titleblock queries at startup."""
        try:
            self._lazy_load_index = 0
            self.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                Action(self._lazy_load_chunk)
            )
        except Exception as ex:
            logger.debug("Error in lazy load init: {}".format(ex))

    def _lazy_load_chunk(self):
        """Load Revision for a batch of sheets, then schedule the next batch.

        Only loads the Revision column (1 param/sheet). Size/Orientation are
        deferred to Queue build time so startup never triggers titleblock
        queries or view-scope FilteredElementCollector calls.
        """
        CHUNK_SIZE = 80

        try:
            start = self._lazy_load_index
            chunk = self.all_sheets[start:start + CHUNK_SIZE]

            if not chunk:
                if hasattr(self, 'sheets_listview') and self.sheets_listview:
                    self.sheets_listview.Items.Refresh()
                self.status_text.Text = "Ready | {} sheets | Revit {}".format(
                    len(self.all_sheets), REVIT_VERSION)
                return

            for sheet_item in chunk:
                sheet_item._load_revision_params()

            self._lazy_load_index = start + len(chunk)

            if hasattr(self, 'sheets_listview') and self.sheets_listview:
                self.sheets_listview.Items.Refresh()

            self.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                Action(self._lazy_load_chunk)
            )

        except Exception as ex:
            logger.debug("Error loading sheet chunk: {}".format(ex))

    def _ensure_titleblock_cache(self):
        """Populate titleblock size cache on first call (deferred from startup)."""
        if not self._titleblock_size_cache:
            self._batch_load_titleblock_sizes()

    def _load_extra_sheet_data(self):
        """Legacy helper kept for compatibility – now delegates to chunked loader."""
        self._lazy_load_init()

    def load_views(self):
        """Load all views — Phase 1: instant display, Phase 2: chunked lazy loading."""
        try:
            views_collector = FilteredElementCollector(self.doc)\
                .OfCategory(BuiltInCategory.OST_Views)\
                .WhereElementIsNotElementType()

            views = []
            for v in views_collector:
                if v.IsTemplate:
                    continue
                if isinstance(v, ViewSheet):
                    continue
                if not isinstance(v, (ViewPlan, ViewSection, View3D, ViewSchedule, ViewDrafting)):
                    continue
                views.append(v)

            views.sort(key=lambda x: x.Name)

            # lazy=True: skip Phase/ViewTemplate lookups for instant display
            self.all_views = [ViewItem(view, False, lazy=True) for view in views]
            self.filtered_views = list(self.all_views)

            self.update_items_list()
            self.status_text.Text = "Loaded {} views | Revit {}".format(
                len(self.all_views), REVIT_VERSION)

            # Phase 2: schedule chunked loading of Phase/ViewTemplate in background
            self._lazy_view_load_index = 0
            self.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                Action(self._lazy_view_load_chunk)
            )

        except Exception as ex:
            logger.error("Error loading views: {}".format(ex))
            forms.alert("Error loading views: {}".format(ex), exitscript=True)

    def _lazy_view_load_chunk(self):
        """Load Phase/ViewTemplate for a batch of views, then schedule the next batch."""
        CHUNK_SIZE = 80
        try:
            start = self._lazy_view_load_index
            chunk = self.all_views[start:start + CHUNK_SIZE]

            if not chunk:
                if hasattr(self, 'sheets_listview') and self.sheets_listview:
                    self.sheets_listview.Items.Refresh()
                self.status_text.Text = "Ready | {} views | Revit {}".format(
                    len(self.all_views), REVIT_VERSION)
                return

            for view_item in chunk:
                view_item._load_extra_data()

            self._lazy_view_load_index = start + len(chunk)

            if hasattr(self, 'sheets_listview') and self.sheets_listview:
                self.sheets_listview.Items.Refresh()

            self.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                Action(self._lazy_view_load_chunk)
            )

        except Exception as ex:
            logger.debug("Error loading view chunk: {}".format(ex))

    def update_sheets_list(self):
        """Update the sheets ListView."""
        self.sheets_listview.ItemsSource = None
        self.sheets_listview.ItemsSource = self.filtered_sheets

    def update_items_list(self):
        """Update the items ListView based on current selection mode."""
        if self.selection_mode == "sheets":
            self.update_sheets_list()
        else:
            self.update_views_list()

        # Update selection count
        self.update_selection_count()

    def update_views_list(self):
        """Update the views ListView."""
        self.sheets_listview.ItemsSource = None
        self.sheets_listview.ItemsSource = self.filtered_views

    def update_selection_count(self):
        """Update the selection count status bar."""
        try:
            if not hasattr(self, 'selection_count_text'):
                return

            if self.selection_mode == "sheets":
                selected_count = sum(1 for s in self.filtered_sheets if s.IsSelected)
                total_count = len(self.filtered_sheets)
                self.selection_count_text.Text = "{} sheets and 0 views selected. Total: {}".format(
                    selected_count, total_count)
            else:
                selected_count = sum(1 for v in self.filtered_views if v.IsSelected)
                total_count = len(self.filtered_views)
                self.selection_count_text.Text = "0 sheets and {} views selected. Total: {}".format(
                    selected_count, total_count)

        except Exception as ex:
            logger.debug("Error updating selection count: {}".format(ex))

    def selection_mode_changed(self, sender, e):
        """Handle selection mode change (Sheets vs Views radio button)."""
        try:
            # Determine which mode is selected
            if hasattr(self, 'sheets_radio') and self.sheets_radio.IsChecked:
                self.selection_mode = "sheets"
                # Show sheets
                if not self.all_sheets:
                    self.load_sheets()
                else:
                    self.update_items_list()
                # Update UI visibility - Hide view type filter
                if hasattr(self, 'view_type_filter'):
                    self.view_type_filter.Visibility = Visibility.Collapsed
                # Update column headers for Sheets mode
                if hasattr(self, 'col_number'):
                    self.col_number.Header = "Sheet Number"
                    self.col_name.Header = "Sheet Name"
                    self.col_revision.Header = "Revision"
                    self.col_size.Header = "Size"
                if hasattr(self, 'col_orientation'):
                    self.col_orientation.Header = "Orientation"
            elif hasattr(self, 'views_radio') and self.views_radio.IsChecked:
                self.selection_mode = "views"
                # Show views
                if not self.all_views:
                    self.load_views()
                else:
                    self.update_items_list()
                # Update UI visibility - Show view type filter
                if hasattr(self, 'view_type_filter'):
                    self.view_type_filter.Visibility = Visibility.Visible
                # Update column headers for Views mode
                if hasattr(self, 'col_number'):
                    self.col_number.Header = "View Name"
                    self.col_name.Header = "View Type"
                    self.col_revision.Header = "Phase"
                    self.col_size.Header = "Scale"
                if hasattr(self, 'col_orientation'):
                    self.col_orientation.Header = "—"
        except Exception as ex:
            logger.error("Error changing selection mode: {}".format(ex))

    def listview_clicked(self, sender, e):
        """Handle click on ListView to toggle item selection.

        This allows users to click anywhere on a row to toggle its selection,
        in addition to using the checkbox.
        """
        try:
            # Get the clicked element
            from System.Windows import FrameworkElement
            from System.Windows.Controls import ListViewItem, TextBox, CheckBox
            from System.Windows.Media import VisualTreeHelper

            # Check if the click was on a TextBox or CheckBox
            # If so, don't toggle selection - let the control handle it
            element = e.OriginalSource
            temp_element = element
            while temp_element is not None:
                if isinstance(temp_element, (TextBox, CheckBox)):
                    # Click was in a textbox or checkbox, don't toggle selection
                    return
                if isinstance(temp_element, ListViewItem):
                    break
                temp_element = temp_element.Parent if hasattr(temp_element, 'Parent') else None

            # Find the ListViewItem that was clicked
            item = None
            element = e.OriginalSource
            while element is not None:
                if isinstance(element, ListViewItem):
                    item = element
                    break
                element = VisualTreeHelper.GetParent(element) if element else None

            # Toggle the selection
            if item and hasattr(item, 'DataContext'):
                data_item = item.DataContext
                if data_item:
                    # Toggle the IsSelected property
                    data_item.IsSelected = not data_item.IsSelected
                    # Refresh to show the change
                    self.sheets_listview.Items.Refresh()
                    # Update selection count
                    self.update_selection_count()
                    # Update export preview if on Create tab
                    self.update_export_preview_if_needed()

        except Exception as ex:
            logger.debug("Error handling listview click: {}".format(ex))

    def listview_item_double_clicked(self, sender, e):
        """Handle double-click on ListView item - same as single click for now."""
        # Double-click just toggles like single click
        # This prevents accidental double-click from causing issues
        pass

    def textbox_prevent_toggle(self, sender, e):
        """Prevent row toggle when clicking on textbox in Custom Filename column."""
        # Stop propagation so that clicking in textbox doesn't toggle row selection
        e.Handled = True

    def on_listview_size_changed(self, sender, e):
        """Resize Sheet Name and Custom Filename columns to fill available ListView width."""
        try:
            fixed = 40 + 150 + 80 + 80 + 90 + 20  # checkbox + number + revision + size + orientation + scrollbar
            available = sender.ActualWidth - fixed
            if available <= 0:
                return
            self.col_name.Width = available * 0.35
            self.col_custom_filename.Width = available * 0.65
        except Exception:
            pass

    def header_checkbox_clicked(self, sender, e):
        """Handle header checkbox click to select/deselect all items."""
        is_checked = sender.IsChecked

        if self.selection_mode == "sheets":
            if is_checked:
                for sheet_item in self.filtered_sheets:
                    sheet_item.IsSelected = True
                self.status_text.Text = "Selected {} sheets".format(len(self.filtered_sheets))
            else:
                for sheet_item in self.filtered_sheets:
                    sheet_item.IsSelected = False
                self.status_text.Text = "Deselected all sheets"
            self.sheets_listview.Items.Refresh()
        else:
            if is_checked:
                for view_item in self.filtered_views:
                    view_item.IsSelected = True
                self.status_text.Text = "Selected {} views".format(len(self.filtered_views))
            else:
                for view_item in self.filtered_views:
                    view_item.IsSelected = False
                self.status_text.Text = "Deselected all views"
            self.sheets_listview.Items.Refresh()

        # Update selection count
        self.update_selection_count()
        # Update export preview if on Create tab
        self.update_export_preview_if_needed()

    def select_all_sheets(self, sender, e):
        """Select all items (sheets or views)."""
        if self.selection_mode == "sheets":
            for sheet_item in self.filtered_sheets:
                sheet_item.IsSelected = True
            self.sheets_listview.Items.Refresh()
            self.status_text.Text = "Selected {} sheets".format(len(self.filtered_sheets))
        else:
            for view_item in self.filtered_views:
                view_item.IsSelected = True
            self.sheets_listview.Items.Refresh()
            self.status_text.Text = "Selected {} views".format(len(self.filtered_views))

        # Update selection count
        self.update_selection_count()

    def select_none_sheets(self, sender, e):
        """Deselect all items (sheets or views)."""
        if self.selection_mode == "sheets":
            for sheet_item in self.filtered_sheets:
                sheet_item.IsSelected = False
            self.sheets_listview.Items.Refresh()
            self.status_text.Text = "Deselected all sheets"
        else:
            for view_item in self.filtered_views:
                view_item.IsSelected = False
            self.sheets_listview.Items.Refresh()
            self.status_text.Text = "Deselected all views"

        # Update selection count
        self.update_selection_count()

    def refresh_sheets(self, sender, e):
        """Refresh the list (sheets or views)."""
        if self.selection_mode == "sheets":
            self.load_sheets()
        else:
            self.load_views()

    def load_sheet_set_clicked(self, sender, e):
        """Load sheets from a saved ViewSheetSet."""
        try:
            if self.selection_mode == "sheets":
                # Get all saved ViewSheetSet names from the document
                saved_set_names = self.get_saved_sheet_set_names()

                if not saved_set_names:
                    forms.alert("No Sheet Sets found in this document.\n\nSheet Sets are created in Revit's Print dialog (File > Print > Sheet Set).",
                               title="No Sheet Sets Found")
                    return

                # Show selection dialog
                selected_set_name = forms.SelectFromList.show(
                    sorted(saved_set_names),
                    title="Select Sheet Set",
                    button_name="Load",
                    multiselect=False
                )

                if not selected_set_name:
                    return

                # Load the selected sheet set and get sheet IDs
                sheet_ids = self.get_sheet_ids_from_set(selected_set_name)

                if not sheet_ids:
                    forms.alert("Could not load sheets from set '{}'".format(selected_set_name),
                               title="Error Loading Sheet Set")
                    return

                # Select sheets that are in the set
                selected_count = 0
                for sheet_item in self.all_sheets:
                    if sheet_item.Sheet.Id in sheet_ids:
                        sheet_item.IsSelected = True
                        selected_count += 1
                    else:
                        sheet_item.IsSelected = False

                # Refresh the ListView
                self.sheets_listview.Items.Refresh()
                self.status_text.Text = "Loaded '{}': {} sheets selected".format(selected_set_name, selected_count)

            else:
                # For views mode, we can implement similar functionality if needed
                forms.alert("Sheet Set loading is only available in Sheets mode.\n\nPlease switch to Sheets mode first.",
                           title="Views Mode Active")

        except Exception as ex:
            logger.error("Error loading sheet set: {}".format(ex))
            forms.alert("Error loading sheet set:\n{}".format(str(ex)), title="Error")

    def get_saved_sheet_set_names(self):
        """Get names of all saved ViewSheetSets from the document.

        ViewSheetSets are created in Revit's Print dialog and contain saved sets of sheets.
        Uses FilteredElementCollector for reliable access without touching PrintManager state.
        """
        try:
            collector = FilteredElementCollector(self.doc).OfClass(ViewSheetSet)
            return [print_set.Name for print_set in collector]
        except Exception as ex:
            logger.debug("Error getting sheet set names: {}".format(ex))
            return []

    def get_sheet_ids_from_set(self, set_name):
        """Get all sheet IDs from a saved ViewSheetSet.

        Args:
            set_name: Name of the saved ViewSheetSet

        Returns:
            List of ElementIds for sheets in the set
        Uses FilteredElementCollector for reliable access without touching PrintManager state.
        """
        try:
            collector = FilteredElementCollector(self.doc).OfClass(ViewSheetSet)
            for print_set in collector:
                if print_set.Name == set_name:
                    return [v.Id for v in print_set.Views]
            return []
        except Exception as ex:
            logger.error("Error getting sheets from set '{}': {}".format(set_name, ex))
            return []

    def save_current_selection_as_vs_set(self):
        """Save current selection as a new View/Sheet Set."""
        try:
            # Get currently selected items
            if self.selection_mode == "sheets":
                selected_items = [s for s in self.filtered_sheets if s.IsSelected]
                mode_name = "Sheet"
            else:
                selected_items = [v for v in self.filtered_views if v.IsSelected]
                mode_name = "View"

            if not selected_items:
                forms.alert("No {} selected.\n\nPlease select at least one {} first.".format(
                    mode_name.lower() + "s", mode_name.lower()),
                    title="No Selection")
                return

            # Prompt for set name
            set_name = forms.ask_for_string(
                prompt="Enter a name for the new {}/Sheet Set:".format(mode_name),
                title="Save {}/Sheet Set".format(mode_name),
                default="Custom Selection {}".format(datetime.now().strftime("%Y-%m-%d %H:%M"))
            )

            if not set_name:
                return

            # Create ViewSet with selected items
            view_set = ViewSet()
            for item in selected_items:
                if self.selection_mode == "sheets":
                    view_set.Insert(item.Sheet)
                else:
                    view_set.Insert(item.View)

            # Save as ViewSheetSet using PrintManager
            print_manager = self.doc.PrintManager
            view_sheet_setting = print_manager.ViewSheetSetting

            # Start a transaction to save the set
            t = Transaction(self.doc, "Save View/Sheet Set")
            t.Start()

            try:
                # Save the view set
                view_sheet_setting.SaveAs(set_name)
                view_sheet_setting.CurrentViewSheetSet.Views = view_set

                t.Commit()

                # Refresh the sheet set filter dropdown
                self.load_sheet_sets_for_filter()

                # Show success message
                self.status_text.Text = "Saved '{}' with {} {}s".format(
                    set_name, len(selected_items), mode_name.lower())

                forms.alert("Successfully saved '{}' with {} {}s".format(
                    set_name, len(selected_items), mode_name.lower()),
                    title="Success")

            except Exception as save_ex:
                t.RollBack()
                raise save_ex

        except Exception as ex:
            logger.error("Error saving View/Sheet Set: {}".format(ex))
            forms.alert("Error saving View/Sheet Set:\n{}".format(str(ex)), title="Error")

    def search_sheets(self, sender, e):
        """Filter sheets by search text."""
        self.apply_filters()

    def filter_by_size(self, sender, e):
        """Filter sheets by size."""
        self.apply_filters()

    def filter_by_sheet_set(self, sender, e):
        """Legacy handler — delegates to the multi-select implementation."""
        self._apply_sheet_set_filter()

    def filter_by_vs_changed(self, sender, e):
        """Handle Filter by V/S checkbox change."""
        try:
            # Re-apply filters when checkbox state changes
            if hasattr(self, 'filter_by_vs_checkbox') and self.filter_by_vs_checkbox.IsChecked:
                # Filter is now enabled - apply sheet set filter
                self.filter_by_sheet_set(sender, e)
            else:
                # Filter is now disabled - remove sheet set filtering
                self.apply_filters()

            self.update_selection_count()

        except Exception as ex:
            logger.error("Error handling Filter by V/S change: {}".format(ex))

    def save_vs_set_clicked(self, sender, e):
        """Handle Save V/S Set button click."""
        try:
            # User wants to save current selection as a new View/Sheet Set
            self.save_current_selection_as_vs_set()

        except Exception as ex:
            logger.error("Error handling Save V/S Set: {}".format(ex))

    def apply_filters(self, sheet_set_ids=None):
        """Apply search and filters.

        Args:
            sheet_set_ids: Optional list of ElementIds to filter sheets by (from ViewSheetSet)
        """
        # Check if controls are initialized (prevents error during XAML loading)
        if not hasattr(self, 'search_textbox'):
            return

        search_text = self.search_textbox.Text.lower() if self.search_textbox.Text else ""

        if self.selection_mode == "sheets":
            # Apply filters for sheets
            self.filtered_sheets = []
            for sheet in self.all_sheets:
                # Check sheet set filter first (if provided)
                if sheet_set_ids is not None:
                    if sheet.Sheet.Id not in sheet_set_ids:
                        continue

                # Check search text
                if search_text:
                    if search_text not in sheet.SheetNumber.lower() and \
                       search_text not in sheet.SheetName.lower():
                        continue

                self.filtered_sheets.append(sheet)

            self.update_items_list()

            # Update status message based on filters
            if sheet_set_ids is not None:
                # Status is set in filter_by_sheet_set method
                pass
            else:
                self.status_text.Text = "Found {} sheets".format(len(self.filtered_sheets))
        else:
            # Get selected view type filter
            view_type_filter = None
            if hasattr(self, 'view_type_filter') and self.view_type_filter.SelectedItem:
                type_text = self.view_type_filter.SelectedItem.Content
                if type_text != "All Views":
                    view_type_filter = type_text

            # Apply filters for views
            self.filtered_views = []
            for view in self.all_views:
                # Check search text
                if search_text:
                    if search_text not in view.ViewName.lower() and \
                       search_text not in view.ViewType.lower():
                        continue

                # Check view type filter
                if view_type_filter:
                    if view.ViewType != view_type_filter:
                        continue

                self.filtered_views.append(view)

            self.update_items_list()
            self.status_text.Text = "Found {} views".format(len(self.filtered_views))

    def browse_output_folder(self, sender, e):
        """Browse for output folder."""
        dialog = FolderBrowserDialog()
        dialog.Description = "Select output folder for exports"
        dialog.SelectedPath = self.output_folder.Text

        if dialog.ShowDialog() == DialogResult.OK:
            self.output_folder.Text = dialog.SelectedPath
            self.status_text.Text = "Output folder: {}".format(dialog.SelectedPath)

    # ── Accordion toggle helpers ──────────────────────────────────────────
    def _toggle_format_panel(self, body_name, arrow_name, border_name, accent_color, header_bg):
        """Expand or collapse a format settings panel."""
        try:
            body = getattr(self, body_name)
            arrow = getattr(self, arrow_name)
            border = getattr(self, border_name)
            from System.Windows import Visibility
            from System.Windows.Media import SolidColorBrush, Color

            if body.Visibility == Visibility.Collapsed:
                body.Visibility = Visibility.Visible
                arrow.Text = "▴"
                # Highlight border when expanded
                r = int(accent_color[1:3], 16)
                g = int(accent_color[3:5], 16)
                b = int(accent_color[5:7], 16)
                border.BorderBrush = SolidColorBrush(Color.FromRgb(r, g, b))
            else:
                body.Visibility = Visibility.Collapsed
                arrow.Text = "▾"
                border.BorderBrush = SolidColorBrush(Color.FromRgb(0xBD, 0xC3, 0xC7))
        except Exception as ex:
            logger.debug("Error toggling panel {}: {}".format(body_name, ex))

    def pdf_header_clicked(self, sender, e):
        self._toggle_format_panel("pdf_settings_body", "pdf_expand_arrow",
                                  "pdf_panel_border", "#3498DB", "#E8F4F8")

    def dwg_header_clicked(self, sender, e):
        self._toggle_format_panel("dwg_settings_body", "dwg_expand_arrow",
                                  "dwg_panel_border", "#3498DB", "#F8F9FA")

    def dgn_header_clicked(self, sender, e):
        self._toggle_format_panel("dgn_settings_body", "dgn_expand_arrow",
                                  "dgn_panel_border", "#3498DB", "#F8F9FA")

    def dwf_header_clicked(self, sender, e):
        self._toggle_format_panel("dwf_settings_body", "dwf_expand_arrow",
                                  "dwf_panel_border", "#3498DB", "#F8F9FA")

    def nwc_header_clicked(self, sender, e):
        self._toggle_format_panel("nwc_settings_body", "nwc_expand_arrow",
                                  "nwc_panel_border", "#3498DB", "#F8F9FA")

    def ifc_header_clicked(self, sender, e):
        self._toggle_format_panel("ifc_settings_body", "ifc_expand_arrow",
                                  "ifc_panel_border", "#3498DB", "#F8F9FA")

    def img_header_clicked(self, sender, e):
        self._toggle_format_panel("img_settings_body", "img_expand_arrow",
                                  "img_panel_border", "#3498DB", "#F8F9FA")
    # ─────────────────────────────────────────────────────────────────────

    def format_changed(self, sender, e):
        """Handle format checkbox change."""
        # Update status to show selected formats
        formats = []
        if self.export_pdf.IsChecked:
            formats.append("PDF")
        if self.export_dwg.IsChecked:
            formats.append("DWG")
        if self.export_dgn.IsChecked:
            formats.append("DGN")
        if self.export_dwf.IsChecked:
            formats.append("DWF")
        if self.export_nwd.IsChecked:
            formats.append("NWC")
        if self.export_ifc.IsChecked:
            formats.append("IFC")
        if self.export_img.IsChecked:
            formats.append("IMG")

        if formats:
            self.status_text.Text = "Selected formats: {}".format(", ".join(formats))

        # Update export preview if on Create tab
        self.update_export_preview_if_needed()

    def pdf_auto_detect_changed(self, sender, e):
        """Handle PDF auto-detect checkbox change."""
        try:
            # Enable/disable manual controls based on auto-detect state
            is_auto = self.pdf_auto_detect_size.IsChecked

            # Disable manual controls when auto-detect is enabled
            self.pdf_paper_size.IsEnabled = not is_auto
            self.pdf_landscape.IsEnabled = not is_auto
            self.pdf_portrait.IsEnabled = not is_auto

            if is_auto:
                self.status_text.Text = "Paper size and orientation will be auto-detected from Title Block"
            else:
                self.status_text.Text = "Using manual paper size and orientation settings"

        except Exception as ex:
            logger.error("Error handling auto-detect change: {}".format(ex))

    def button_custom_parameters(self, sender, e):
        """Open custom parameters dialog to select parameters for filename.

        When a pattern is selected, it automatically applies to ALL items (sheets or views).
        """
        try:
            # Import the parameter selector dialog
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib', 'GUI'))
            from ParameterSelectorDialog import ParameterSelectorDialog

            # Determine element type based on current selection mode
            element_type = 'sheet' if self.selection_mode == 'sheets' else 'view'

            # Show the parameter selector dialog
            pattern = ParameterSelectorDialog.show_dialog(self.doc, element_type)

            if pattern:
                # Update the naming pattern textbox
                self.naming_pattern.Text = pattern

                # Auto-apply the pattern to ALL items (not just selected)
                items_list = self.all_sheets if self.selection_mode == 'sheets' else self.all_views

                # Apply the naming pattern to each item
                for item in items_list:
                    # Generate filename using the current naming pattern
                    filename = self.get_export_filename(item)
                    # Set it to the CustomFilename property
                    item.CustomFilename = filename

                # Refresh the ListView to show the updated CustomFilename values
                self.sheets_listview.Items.Refresh()

                self.status_text.Text = "Pattern applied to {} item(s)".format(len(items_list))

        except Exception as ex:
            logger.error("Error opening custom parameters dialog: {}".format(ex))
            forms.alert("Error opening custom parameters dialog:\n{}".format(str(ex)))

    def edit_filename_clicked(self, sender, e):
        """Open the parameter selector dialog to edit filename pattern.

        This is triggered by the 'Edit filename' button in DWG Options section.
        After editing, the sample filename is displayed in the dwg_filename_sample TextBox.
        """
        try:
            # Import the parameter selector dialog
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib', 'GUI'))
            from ParameterSelectorDialog import ParameterSelectorDialog

            # Determine element type based on current selection mode
            element_type = 'sheet' if self.selection_mode == 'sheets' else 'view'

            # Show the parameter selector dialog
            pattern = ParameterSelectorDialog.show_dialog(self.doc, element_type)

            if pattern:
                # Update the naming pattern
                self.naming_pattern.Text = pattern

                # Update the sample display in DWG Options (if exists)
                if hasattr(self, 'dwg_filename_sample') and self.dwg_filename_sample:
                    self.dwg_filename_sample.Text = pattern

                # Auto-apply the pattern to ALL items
                items_list = self.all_sheets if self.selection_mode == 'sheets' else self.all_views

                # Apply the naming pattern to each item
                for item in items_list:
                    # Generate filename using the current naming pattern
                    filename = self.get_export_filename(item)
                    # Set it to the CustomFilename property
                    item.CustomFilename = filename

                # Refresh the ListView to show the updated CustomFilename values
                self.sheets_listview.Items.Refresh()

                self.status_text.Text = "Filename pattern updated: {}".format(pattern)

        except Exception as ex:
            logger.error("Error editing filename pattern: {}".format(ex))
            forms.alert("Error editing filename pattern:\n{}".format(str(ex)))

    def button_row_naming_pattern(self, sender, e):
        """Open parameter selector dialog for a specific row.

        This applies the naming pattern to only the selected row's item.
        """
        try:
            # Get the item from the button's Tag
            item = sender.Tag
            if not item:
                return

            # Import the parameter selector dialog
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib', 'GUI'))
            from ParameterSelectorDialog import ParameterSelectorDialog

            # Determine element type based on current selection mode
            element_type = 'sheet' if self.selection_mode == 'sheets' else 'view'

            # Show the parameter selector dialog
            pattern = ParameterSelectorDialog.show_dialog(self.doc, element_type)

            if pattern:
                # Store the pattern temporarily
                temp_pattern = self.naming_pattern.Text if hasattr(self, 'naming_pattern') else ""

                # Temporarily set the pattern
                if hasattr(self, 'naming_pattern'):
                    self.naming_pattern.Text = pattern

                # Generate filename using the selected pattern
                filename = self.get_export_filename(item)

                # Restore the original pattern
                if hasattr(self, 'naming_pattern') and temp_pattern:
                    self.naming_pattern.Text = temp_pattern

                # Set the generated filename to this item's CustomFilename
                item.CustomFilename = filename

                # Refresh the ListView to show the updated CustomFilename value
                self.sheets_listview.Items.Refresh()

                self.status_text.Text = "Pattern applied to row"

        except Exception as ex:
            logger.error("Error opening row naming pattern dialog: {}".format(ex))
            forms.alert("Error opening row naming pattern dialog:\n{}".format(str(ex)))

    def reverse_order_changed(self, sender, e):
        """Handle reverse order checkbox change."""
        # Reverse the filtered sheets list
        self.filtered_sheets.reverse()
        self.update_sheets_list()

    def nav_item_clicked(self, sender, e):
        """Handle direct navigation when clicking items in the footer."""
        try:
            target_index = -1
            if sender.Name == "nav_border_selection": target_index = 0
            elif sender.Name == "nav_border_format": target_index = 1
            elif sender.Name == "nav_border_queue": target_index = 2
            if target_index != -1:
                self.switch_to_view(target_index)
        except Exception as ex:
            logger.debug("Error in nav_item_clicked: {}".format(ex))

    def switch_to_view(self, index):
        """Switch to the given wizard tab (0=Selection, 1=Format, 2=Queue+Settings)."""
        try:
            self.main_tabs.SelectedIndex = index
            self.update_navigation_buttons(index)
        except Exception as ex:
            logger.debug("Error in switch_to_view: {}".format(ex))

    def sync_navigation_footer(self, index=None):
        """Update the visual state of the 3-item bottom navigation bar."""
        try:
            if index is None:
                index = self.main_tabs.SelectedIndex

            from System.Windows.Media import SolidColorBrush, Color
            active_bg = SolidColorBrush(Color.FromRgb(0xF0, 0xF8, 0xFF))
            active_fg = SolidColorBrush(Color.FromRgb(0x00, 0x5B, 0x82))
            inactive_bg = SolidColorBrush(Color.FromArgb(0, 255, 255, 255))
            inactive_fg = SolidColorBrush(Color.FromRgb(0x7F, 0x8C, 0x8D))

            for idx, name in enumerate(['selection', 'format', 'queue']):
                border = getattr(self, 'nav_border_' + name)
                icon = getattr(self, 'nav_icon_' + name)
                text = getattr(self, 'nav_text_' + name)
                if idx == index:
                    border.Background = active_bg
                    icon.Foreground = active_fg
                    text.Foreground = active_fg
                    text.FontWeight = System.Windows.FontWeights.Bold
                else:
                    border.Background = inactive_bg
                    icon.Foreground = inactive_fg
                    text.Foreground = inactive_fg
                    text.FontWeight = System.Windows.FontWeights.Normal
        except Exception as ex:
            logger.debug("Error syncing navigation footer: {}".format(ex))

    def update_navigation_buttons(self, index=None):
        """Update action bar buttons and footer highlight for the current tab."""
        if index is None:
            index = self.main_tabs.SelectedIndex

        self.back_button.Visibility = Visibility.Collapsed if index == 0 else Visibility.Visible

        # Last tab (Queue+Settings) → Export; all others → Next
        if index == 2:
            self.next_button_text.Text = "Export"
            self.next_button_icon.Text = ""
        else:
            self.next_button_text.Text = "Next"
            self.next_button_icon.Text = "→"

        self.sync_navigation_footer(index)

    def tab_changed(self, sender, e):
        """Sync nav footer and trigger Queue preview when tab changes via keyboard/programmatic switch."""
        try:
            from System.Windows.Controls import TabControl as _TC
            if not isinstance(e.Source, _TC):
                return
            idx = self.main_tabs.SelectedIndex
            self.update_navigation_buttons(idx)
            if idx == 2:
                self.update_export_preview_if_needed()
        except Exception as ex:
            logger.debug("Error handling tab change: {}".format(ex))

    def update_export_preview_if_needed(self):
        """Refresh the Queue preview list when on tab 2."""
        try:
            if self.main_tabs.SelectedIndex == 2:
                if self.selection_mode == "sheets":
                    selected_items = [s for s in self.all_sheets if s.IsSelected]
                else:
                    selected_items = [v for v in self.all_views if v.IsSelected]

                if selected_items:
                    self.build_export_preview()
                else:
                    self.export_items = []
                    self.export_preview_list.ItemsSource = self.export_items
                    self.progress_text.Text = "No items selected for export"
        except Exception as ex:
            logger.debug("Error updating export preview: {}".format(ex))

    def go_back(self, sender, e):
        """Navigate to previous tab."""
        idx = self.main_tabs.SelectedIndex
        if idx > 0:
            self.switch_to_view(idx - 1)

    def go_next(self, sender, e):
        """Advance to next tab or start export on the final tab."""
        idx = self.main_tabs.SelectedIndex
        if idx == 0:
            if self.selection_mode == "sheets":
                selected_items = [s for s in self.all_sheets if s.IsSelected]
                if not selected_items:
                    forms.alert("Please select at least one sheet to export.", title="No Sheets Selected")
                    return
            self.switch_to_view(1)
        elif idx == 1:
            self.switch_to_view(2)
        else:
            self.start_export()

    def build_export_preview(self):
        """Build the export preview list."""
        # Ensure titleblock cache is populated before size detection runs.
        # This is deferred from startup to avoid triggering Revit graphic regeneration.
        self._ensure_titleblock_cache()

        # Get selected items based on mode
        if self.selection_mode == "sheets":
            selected_items = [s for s in self.all_sheets if s.IsSelected]
            # Sync cached sheet numbers with live values from Revit before building preview
            for sheet_item in selected_items:
                sheet_item.SheetNumber = sheet_item.Sheet.SheetNumber
                sheet_item.SheetName = sheet_item.Sheet.Name
        else:
            selected_items = [v for v in self.all_views if v.IsSelected]
            # Sync cached view names with live values from Revit before building preview
            for view_item in selected_items:
                view_item.ViewName = view_item.View.Name
                view_item.SheetNumber = view_item.View.Name

        # Get selected formats
        formats = []
        if self.export_pdf.IsChecked:
            formats.append("PDF")
        if self.export_dwg.IsChecked:
            formats.append("DWG")
        if self.export_dgn.IsChecked:
            formats.append("DGN")
        if self.export_dwf.IsChecked:
            formats.append("DWF")
        if self.export_nwd.IsChecked:
            formats.append("NWC")
        if self.export_ifc.IsChecked:
            formats.append("IFC")
        if self.export_img.IsChecked:
            formats.append("IMG")

        # Check if auto-detect is enabled
        is_auto_detect = self.pdf_auto_detect_size.IsChecked if hasattr(self, 'pdf_auto_detect_size') and self.pdf_auto_detect_size.IsChecked is not None else False

        # Build preview items
        self.export_items = []
        for item in selected_items:
            # Determine paper size and orientation for this item
            if is_auto_detect and self.selection_mode == "sheets" and hasattr(item, 'Sheet'):
                # Auto-detect from Title Block
                detected_size, detected_orientation = self.get_sheet_paper_size_and_orientation(item.Sheet)
                size = detected_size
                orientation = detected_orientation
            else:
                # Use manual settings
                size = item.Size if hasattr(item, 'Size') else "-"
                orientation = "Landscape" if self.pdf_landscape.IsChecked else "Portrait"

            for fmt in formats:
                preview_item = ExportPreviewItem(item, fmt, size, orientation)
                self.export_items.append(preview_item)

        # Update preview list
        self.export_preview_list.ItemsSource = self.export_items
        self.progress_text.Text = "Ready to export {} items".format(len(self.export_items))

    def get_export_filename(self, item):
        """Generate export filename based on naming pattern.

        Always reads live values from the Revit item (sheet or view) to ensure the filename
        reflects the current state of the item (e.g., if name changed).
        Supports both SheetItem and ViewItem.
        Now supports ALL parameters dynamically.
        """
        pattern = self.naming_pattern.Text

        # Get project info
        try:
            project_info = self.doc.ProjectInformation
            project_number = project_info.Number or ""
            project_name = project_info.Name or ""
            project_address = project_info.Address or ""
            client_name = project_info.ClientName or ""
            project_status = project_info.Status or ""
        except:
            project_number = ""
            project_name = ""
            project_address = ""
            client_name = ""
            project_status = ""

        # Get the actual Revit element (sheet or view)
        element = None
        if hasattr(item, 'Sheet'):
            element = item.Sheet
            sheet_number = element.SheetNumber
            sheet_name = element.Name
        elif hasattr(item, 'View'):
            element = item.View
            sheet_number = element.Name  # Use view name as "number"
            sheet_name = item.ViewType  # Use view type as "name"
        else:
            sheet_number = "Unknown"
            sheet_name = "Unknown"

        # Build a dictionary of all standard replacements
        replacements = {
            "{SheetNumber}": sheet_number,
            "{SheetName}": sheet_name,
            "{ViewName}": sheet_number if hasattr(item, 'View') else "",
            "{ProjectNumber}": project_number,
            "{ProjectName}": project_name,
            "{ProjectAddress}": project_address,
            "{ClientName}": client_name,
            "{ProjectStatus}": project_status,
            "{Date}": datetime.now().strftime("%Y%m%d"),
            "{Time}": datetime.now().strftime("%H%M%S"),
        }

        # Get ALL parameters from the element dynamically
        if element:
            try:
                for param in element.Parameters:
                    try:
                        param_name = param.Definition.Name
                        param_value = ""

                        # Get parameter value based on storage type
                        if param.HasValue:
                            if param.StorageType == DB.StorageType.String:
                                param_value = param.AsString() or ""
                            elif param.StorageType == DB.StorageType.Integer:
                                param_value = str(param.AsInteger())
                            elif param.StorageType == DB.StorageType.Double:
                                param_value = str(param.AsDouble())
                            elif param.StorageType == DB.StorageType.ElementId:
                                elem_id = param.AsElementId()
                                if elem_id and elem_id.IntegerValue > 0:
                                    try:
                                        elem = self.doc.GetElement(elem_id)
                                        param_value = elem.Name if elem else ""
                                    except:
                                        param_value = str(elem_id.IntegerValue)

                        # Add to replacements dictionary
                        # Support both {ParamName} format
                        replacements["{" + param_name + "}"] = param_value

                    except Exception as param_ex:
                        # Skip problematic parameters
                        logger.debug("Could not read parameter {}: {}".format(
                            param.Definition.Name if hasattr(param, 'Definition') else 'unknown',
                            str(param_ex)
                        ))
                        continue
            except Exception as params_ex:
                logger.warning("Could not iterate parameters: {}".format(str(params_ex)))

        # Add common sheet-specific built-in parameters explicitly
        if hasattr(item, 'Sheet'):
            try:
                rev_param = item.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_CURRENT_REVISION)
                replacements["{Revision}"] = rev_param.AsString() if rev_param else ""
            except:
                replacements["{Revision}"] = ""

            try:
                rev_date_param = item.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_CURRENT_REVISION_DATE)
                replacements["{RevisionDate}"] = rev_date_param.AsString() if rev_date_param else ""
            except:
                replacements["{RevisionDate}"] = ""

            try:
                rev_desc_param = item.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_CURRENT_REVISION_DESCRIPTION)
                replacements["{RevisionDescription}"] = rev_desc_param.AsString() if rev_desc_param else ""
            except:
                replacements["{RevisionDescription}"] = ""

            try:
                drawn_param = item.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_DRAWN_BY)
                replacements["{DrawnBy}"] = drawn_param.AsString() if drawn_param else ""
            except:
                replacements["{DrawnBy}"] = ""

            try:
                checked_param = item.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_CHECKED_BY)
                replacements["{CheckedBy}"] = checked_param.AsString() if checked_param else ""
            except:
                replacements["{CheckedBy}"] = ""

            try:
                approved_param = item.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_APPROVED_BY)
                replacements["{ApprovedBy}"] = approved_param.AsString() if approved_param else ""
            except:
                replacements["{ApprovedBy}"] = ""

            try:
                issue_date_param = item.Sheet.get_Parameter(DB.BuiltInParameter.SHEET_ISSUE_DATE)
                replacements["{IssueDate}"] = issue_date_param.AsString() if issue_date_param else ""
            except:
                replacements["{IssueDate}"] = ""

        # Add view-specific parameters explicitly
        elif hasattr(item, 'View'):
            try:
                replacements["{ViewType}"] = item.ViewType
                replacements["{Scale}"] = item.Scale if hasattr(item, 'Scale') else ""
            except:
                pass

            try:
                phase_param = element.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
                if phase_param:
                    phase_id = phase_param.AsElementId()
                    if phase_id and phase_id.IntegerValue > 0:
                        phase = self.doc.GetElement(phase_id)
                        replacements["{Phase}"] = phase.Name if phase else ""
            except:
                replacements["{Phase}"] = ""

            try:
                level_param = element.get_Parameter(DB.BuiltInParameter.VIEW_LEVEL)
                if level_param:
                    level_id = level_param.AsElementId()
                    if level_id and level_id.IntegerValue > 0:
                        level = self.doc.GetElement(level_id)
                        replacements["{Level}"] = level.Name if level else ""
            except:
                replacements["{Level}"] = ""

        # Replace all placeholders in the pattern
        filename = pattern
        for placeholder, value in replacements.items():
            filename = filename.replace(placeholder, str(value))

        # Remove invalid characters
        invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
        for char in invalid_chars:
            filename = filename.replace(char, '_')

        return filename

    def update_export_item_progress(self, sheet_number, format_name, progress, status=""):
        """Update progress for a specific export item and refresh the display."""
        try:
            for item in self.export_items:
                if item.SheetNumber == sheet_number and item.Format == format_name:
                    item.Progress = progress
                    if status:
                        item.Status = status
                    elif progress == 100:
                        item.Status = "Successfully Completed"
                    # Refresh the ListView to show updated progress
                    self.export_preview_list.Items.Refresh()
                    break
        except:
            pass

    def start_export(self):
        """Start the export process."""
        try:
            self._ensure_titleblock_cache()

            # Get selected items based on mode
            if self.selection_mode == "sheets":
                selected_items = [s for s in self.all_sheets if s.IsSelected]
                if not selected_items:
                    forms.alert("Please select at least one sheet to export.", title="No Sheets Selected")
                    return
                item_type_name = "sheets"
            else:
                selected_items = [v for v in self.all_views if v.IsSelected]
                if not selected_items:
                    forms.alert("Please select at least one view to export.", title="No Views Selected")
                    return
                item_type_name = "views"

            # Check if reverse order is enabled
            if self.reverse_order.IsChecked:
                selected_items.reverse()

            # Get output folder
            output_folder = self.output_folder.Text
            if not output_folder:
                forms.alert("Please select an output folder.", title="No Output Folder")
                return

            # Create output folder if it doesn't exist
            if not os.path.exists(output_folder):
                os.makedirs(output_folder)

            # Check if split by format
            split_by_format = self.save_split_by_format.IsChecked

            # Disable buttons during export
            self.next_button.IsEnabled = False
            self.back_button.IsEnabled = False
            self.status_text.Text = "Exporting..."

            # Export to each format
            total_exported = 0
            total_items = len(self.export_items)
            current_item = 0

            if self.export_dwg.IsChecked:
                folder = os.path.join(output_folder, "DWG") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_dwg(selected_items, folder)
                total_exported += count
                current_item += count
                if total_items > 0:
                    progress_percent = int((current_item * 100.0) / total_items)
                    self.overall_progress.Value = progress_percent
                    self.progress_text.Text = "Completed {}%".format(progress_percent)

            if self.export_pdf.IsChecked:
                folder = os.path.join(output_folder, "PDF") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_pdf(selected_items, folder)
                total_exported += count
                current_item += count
                if total_items > 0:
                    progress_percent = int((current_item * 100.0) / total_items)
                    self.overall_progress.Value = progress_percent
                    self.progress_text.Text = "Completed {}%".format(progress_percent)

            if self.export_dwf.IsChecked:
                folder = os.path.join(output_folder, "DWF") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_dwf(selected_items, folder)
                total_exported += count
                current_item += count
                if total_items > 0:
                    progress_percent = int((current_item * 100.0) / total_items)
                    self.overall_progress.Value = progress_percent
                    self.progress_text.Text = "Completed {}%".format(progress_percent)

            if self.export_dgn.IsChecked:
                folder = os.path.join(output_folder, "DGN") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_dgn(selected_items, folder)
                total_exported += count
                current_item += count
                if total_items > 0:
                    progress_percent = int((current_item * 100.0) / total_items)
                    self.overall_progress.Value = progress_percent
                    self.progress_text.Text = "Completed {}%".format(progress_percent)

            if self.export_nwd.IsChecked:
                folder = os.path.join(output_folder, "NWC") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_nwd(selected_items, folder)
                total_exported += count
                current_item += count
                if total_items > 0:
                    progress_percent = int((current_item * 100.0) / total_items)
                    self.overall_progress.Value = progress_percent
                    self.progress_text.Text = "Completed {}%".format(progress_percent)

            if self.export_ifc.IsChecked:
                folder = os.path.join(output_folder, "IFC") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_ifc(selected_items, folder)
                total_exported += count
                current_item += count
                if total_items > 0:
                    progress_percent = int((current_item * 100.0) / total_items)
                    self.overall_progress.Value = progress_percent
                    self.progress_text.Text = "Completed {}%".format(progress_percent)

            if self.export_img.IsChecked:
                folder = os.path.join(output_folder, "Images") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_images(selected_items, folder)
                total_exported += count
                current_item += count
                if total_items > 0:
                    progress_percent = int((current_item * 100.0) / total_items)
                    self.overall_progress.Value = progress_percent
                    self.progress_text.Text = "Completed {}%".format(progress_percent)

            self.status_text.Text = "Export complete! {} files exported".format(total_exported)
            self.progress_text.Text = "Export complete! {} files exported".format(total_exported)
            self.overall_progress.Value = 100
            self.next_button.IsEnabled = True
            self.back_button.IsEnabled = True

            # Ask if user wants to open output folder
            if forms.alert("Export complete!\n\nDo you want to open the output folder?",
                          title="Export Complete",
                          yes=True, no=True):
                os.startfile(output_folder)

        except Exception as ex:
            logger.error("Export failed: {}".format(ex))
            forms.alert("Export failed: {}".format(ex), title="Export Error")
            self.status_text.Text = "Export failed"
            self.next_button.IsEnabled = True
            self.back_button.IsEnabled = True

    def export_to_dwg(self, items, output_folder):
        """Export items (sheets or views) to DWG format with version-aware API usage.

        Supports Revit 2022-2026 with appropriate API handling for each version.
        """
        try:
            # Sync cached values with live values from Revit
            for item in items:
                if hasattr(item, 'Sheet'):
                    item.SheetNumber = item.Sheet.SheetNumber
                    item.SheetName = item.Sheet.Name
                elif hasattr(item, 'View'):
                    item.SheetNumber = item.View.Name
                    item.ViewName = item.View.Name

            # Get selected export setup (if any)
            selected_setup = None
            selected_setup_name = None
            if self.cad_export_setup.SelectedIndex > 0:
                # User selected a specific setup (not the default)
                selected_item = self.cad_export_setup.SelectedItem
                if hasattr(selected_item, 'Tag') and selected_item.Tag:
                    selected_setup = selected_item.Tag
                    selected_setup_name = selected_item.Content

            # Create DWG export options
            dwg_options = DWGExportOptions()

            # Set AutoCAD version
            dwg_version_index = self.dwg_version.SelectedIndex
            if dwg_version_index == 0:
                dwg_options.FileVersion = ACADVersion.R2013
            elif dwg_version_index == 1:
                dwg_options.FileVersion = ACADVersion.R2010
            else:
                dwg_options.FileVersion = ACADVersion.R2007

            # VERSION-AWARE: Apply CAD export options
            # ExportingAreas availability varies by version
            export_views_on_sheets = self.cad_export_views_on_sheets.IsChecked
            try:
                if hasattr(dwg_options, 'ExportingAreas') and hasattr(DB, 'ExportingAreas'):
                    if export_views_on_sheets:
                        dwg_options.ExportingAreas = DB.ExportingAreas.ExportViewsOnSheets
                    else:
                        dwg_options.ExportingAreas = DB.ExportingAreas.DontExportViewsOnSheets
            except Exception as ex:
                logger.debug("ExportingAreas not supported in Revit {}: {}".format(REVIT_VERSION, ex))

            # Export links as external references
            export_links_as_external = self.cad_export_links_as_external.IsChecked
            try:
                if hasattr(dwg_options, 'MergedViews'):
                    dwg_options.MergedViews = not export_links_as_external
            except Exception as ex:
                logger.debug("MergedViews not supported in Revit {}: {}".format(REVIT_VERSION, ex))

            # VERSION-AWARE: Handle export setup application
            # Load settings from selected ExportDWGSettings
            if selected_setup:
                try:
                    # Load all settings from the ExportDWGSettings object
                    # LoadSettingsFrom copies all settings including layers, colors, line weights, etc.
                    dwg_options.LoadSettingsFrom(selected_setup, True)
                except Exception as setup_ex:
                    logger.warning("Could not apply export setup '{}': {}".format(
                        selected_setup_name, setup_ex))
                    # Fallback: Set PropOverrides to ByEntity to match Revit colors
                    try:
                        dwg_options.PropOverrides = PropOverrideMode.ByEntity
                    except:
                        pass
            else:
                # No setup selected - ensure colors match Revit by using ByEntity mode
                try:
                    dwg_options.PropOverrides = PropOverrideMode.ByEntity
                except Exception as prop_ex:
                    logger.debug("Could not set PropOverrides: {}".format(prop_ex))

            exported_count = 0

            for item in items:
                try:
                    # Get the actual element (sheet or view)
                    if hasattr(item, 'Sheet'):
                        element = item.Sheet
                        element_name = element.SheetNumber
                    elif hasattr(item, 'View'):
                        element = item.View
                        element_name = element.Name
                    else:
                        continue

                    # Update progress text to show current item and format
                    self.progress_text.Text = "Exporting {} to DWG...".format(element_name)

                    filename = item.CustomFilename or self.get_export_filename(item)

                    # Remove extension if present
                    if filename.lower().endswith('.dwg'):
                        filename = filename[:-4]

                    # VERSION-AWARE: Export API handling
                    # All versions 2022-2026 support ICollection<ElementId> signature
                    # Signature: Export(String folder, String name, ICollection<ElementId> views, DWGExportOptions options)
                    view_ids = List[DB.ElementId]()
                    view_ids.Add(element.Id)

                    # Use Smart API Adapter if available for intelligent export
                    if self.api_adapter:
                        # Smart adapter automatically handles version differences
                        self.api_adapter.export_dwg(output_folder, filename, view_ids, dwg_options)
                    else:
                        # Fallback to direct export call
                        if REVIT_VERSION >= 2022:
                            # Revit 2022-2026: Use ICollection<ElementId> signature
                            self.doc.Export(output_folder, filename, view_ids, dwg_options)
                        else:
                            # Fallback for older versions (if needed)
                            self.doc.Export(output_folder, filename, view_ids, dwg_options)

                    # Verify file was created
                    expected_file = os.path.join(output_folder, filename + ".dwg")
                    if os.path.exists(expected_file):
                        exported_count += 1
                        # Update progress for this export item
                        self.update_export_item_progress(item.SheetNumber, "DWG", 100)

                except Exception as ex:
                    logger.error("Error exporting {} to DWG: {}".format(element_name, ex))

            return exported_count

        except Exception as ex:
            logger.error("DWG export failed: {}".format(ex))
            return 0

    def export_to_dgn(self, items, output_folder):
        """Export items (sheets or views) to DGN (MicroStation) format."""
        try:
            for item in items:
                if hasattr(item, 'Sheet'):
                    item.SheetNumber = item.Sheet.SheetNumber
                    item.SheetName = item.Sheet.Name
                elif hasattr(item, 'View'):
                    item.SheetNumber = item.View.Name
                    item.ViewName = item.View.Name

            dgn_options = DGNExportOptions()

            exported_count = 0

            for item in items:
                try:
                    if hasattr(item, 'Sheet'):
                        element = item.Sheet
                        element_name = element.SheetNumber
                    elif hasattr(item, 'View'):
                        element = item.View
                        element_name = element.Name
                    else:
                        continue

                    self.progress_text.Text = "Exporting {} to DGN...".format(element_name)

                    filename = item.CustomFilename or self.get_export_filename(item)
                    if filename.lower().endswith('.dgn'):
                        filename = filename[:-4]

                    view_ids = List[DB.ElementId]()
                    view_ids.Add(element.Id)

                    self.doc.Export(output_folder, filename, view_ids, dgn_options)

                    expected_file = os.path.join(output_folder, filename + ".dgn")
                    if os.path.exists(expected_file):
                        exported_count += 1
                        self.update_export_item_progress(item.SheetNumber, "DGN", 100)

                except Exception as ex:
                    logger.error("Error exporting {} to DGN: {}".format(element_name, ex))

            return exported_count

        except Exception as ex:
            logger.error("DGN export failed: {}".format(ex))
            return 0

    def export_to_pdf(self, items, output_folder):
        """Export items (sheets or views) to PDF format using Revit's native PDF export with version-aware API usage.

        Supports Revit 2022-2026 with appropriate API handling for each version.
        """
        try:
            import time
            import glob

            # Sync cached values with live values from Revit
            for item in items:
                if hasattr(item, 'Sheet'):
                    item.SheetNumber = item.Sheet.SheetNumber
                    item.SheetName = item.Sheet.Name
                elif hasattr(item, 'View'):
                    item.SheetNumber = item.View.Name
                    item.ViewName = item.View.Name

            # Check if combine PDF is enabled
            combine_pdf = self.combine_pdf.IsChecked

            exported_count = 0

            if combine_pdf:
                # Export all items to a single PDF
                try:
                    # Update progress text
                    self.progress_text.Text = "Exporting combined PDF with {} items...".format(len(items))

                    # Generate combined filename using live names
                    if len(items) > 0:
                        first_item = items[0]
                        last_item = items[-1]
                        # Get names from actual Revit elements
                        if hasattr(first_item, 'Sheet'):
                            first_name = first_item.Sheet.SheetNumber
                            last_name = last_item.Sheet.SheetNumber
                        else:
                            first_name = first_item.View.Name[:20]  # Limit name length
                            last_name = last_item.View.Name[:20]
                        filename = "{}-{}_Combined".format(first_name, last_name)
                    else:
                        filename = "Combined_Export"

                    # Remove extension if present
                    if filename.lower().endswith('.pdf'):
                        filename = filename[:-4]

                    # Get list of existing PDF files before export
                    existing_pdfs = set(glob.glob(os.path.join(output_folder, "*.pdf")))

                    # Get all element IDs as System.Collections.Generic.List
                    element_ids = List[DB.ElementId]()
                    for item in items:
                        if hasattr(item, 'Sheet'):
                            element_ids.Add(item.Sheet.Id)
                        elif hasattr(item, 'View'):
                            element_ids.Add(item.View.Id)

                    # Create PDF export options
                    pdf_options = PDFExportOptions()
                    pdf_options.Combine = True
                    # Set filename (learned from pyRevit)
                    pdf_options.FileName = filename

                    # VERSION-AWARE: Apply PDF settings
                    # Use Smart API Adapter if available for intelligent configuration
                    if self.api_adapter:
                        pdf_options = self.api_adapter.configure_pdf_options(
                            pdf_options,
                            hide_scope_boxes=self.pdf_hide_ref_planes.IsChecked,
                            hide_crop_boundaries=self.pdf_hide_crop_boundaries.IsChecked,
                            hide_unreferenced_tags=self.pdf_hide_unreferenced_tags.IsChecked
                        )
                    else:
                        # Fallback to manual configuration
                        try:
                            if self.pdf_hide_ref_planes.IsChecked:
                                pdf_options.HideScopeBoxes = True
                        except:
                            logger.debug("HideScopeBoxes not supported in Revit {}".format(REVIT_VERSION))

                        try:
                            if self.pdf_hide_crop_boundaries.IsChecked:
                                pdf_options.HideCropBoundaries = True
                        except:
                            logger.debug("HideCropBoundaries not supported in Revit {}".format(REVIT_VERSION))

                        try:
                            if self.pdf_hide_unreferenced_tags.IsChecked:
                                pdf_options.HideUnreferencedViewTags = True
                        except:
                            logger.debug("HideUnreferencedViewTags not supported in Revit {}".format(REVIT_VERSION))

                    # VERSION-AWARE: Export using Revit's native PDF export
                    # Use Smart API Adapter if available for intelligent export (handles method overload resolution)
                    if self.api_adapter:
                        # Smart adapter automatically handles version differences and method overload resolution
                        self.api_adapter.export_pdf(output_folder, filename, element_ids, pdf_options)
                    else:
                        # Fallback to direct export call
                        # Revit 2022-2026 signature: Export(String folder, IList<ElementId> viewIds, PDFExportOptions options)
                        # NOTE: PDF export does NOT take a filename parameter in the Export() method (unlike DWG/DXF)
                        # Instead, filename is set via PDFExportOptions.FileName property (learned from pyRevit)
                        self.doc.Export(output_folder, element_ids, pdf_options)

                    # Wait briefly for file system to update
                    time.sleep(0.5)

                    # Get list of PDF files after export
                    current_pdfs = set(glob.glob(os.path.join(output_folder, "*.pdf")))
                    new_pdfs = current_pdfs - existing_pdfs

                    # Verify file was created
                    expected_file = os.path.join(output_folder, filename + ".pdf")
                    if os.path.exists(expected_file) or new_pdfs:
                        exported_count = 1
                        # Update progress for all items in combined PDF
                        for item in items:
                            self.update_export_item_progress(item.SheetNumber, "PDF", 100)

                except Exception as ex:
                    logger.error("Error exporting combined PDF: {}".format(ex))

            else:
                # Export each item individually
                for item in items:
                    try:
                        # Get the actual element (sheet or view)
                        if hasattr(item, 'Sheet'):
                            element = item.Sheet
                            element_name = element.SheetNumber
                        elif hasattr(item, 'View'):
                            element = item.View
                            element_name = element.Name
                        else:
                            continue

                        # Update progress text to show current item and format
                        self.progress_text.Text = "Exporting {} to PDF...".format(element_name)

                        filename = item.CustomFilename or self.get_export_filename(item)

                        # Remove extension if present
                        if filename.lower().endswith('.pdf'):
                            filename = filename[:-4]

                        # Get list of existing PDF files before export
                        existing_pdfs = set(glob.glob(os.path.join(output_folder, "*.pdf")))

                        # Create PDF export options
                        pdf_options = PDFExportOptions()
                        # IMPORTANT: Use Combine = True even for single sheets to force Revit to use our filename
                        # When Combine = False, Revit ignores FileName and uses sheet number/name
                        pdf_options.Combine = True
                        # Set filename to match DWG naming pattern
                        pdf_options.FileName = filename

                        # VERSION-AWARE: Apply PDF settings
                        # Use Smart API Adapter if available for intelligent configuration
                        if self.api_adapter:
                            pdf_options = self.api_adapter.configure_pdf_options(
                                pdf_options,
                                hide_scope_boxes=self.pdf_hide_ref_planes.IsChecked,
                                hide_crop_boundaries=self.pdf_hide_crop_boundaries.IsChecked,
                                hide_unreferenced_tags=self.pdf_hide_unreferenced_tags.IsChecked
                            )
                        else:
                            # Fallback to manual configuration
                            try:
                                if self.pdf_hide_ref_planes.IsChecked:
                                    pdf_options.HideScopeBoxes = True
                            except:
                                logger.debug("HideScopeBoxes not supported in Revit {}".format(REVIT_VERSION))

                            try:
                                if self.pdf_hide_crop_boundaries.IsChecked:
                                    pdf_options.HideCropBoundaries = True
                            except:
                                logger.debug("HideCropBoundaries not supported in Revit {}".format(REVIT_VERSION))

                            try:
                                if self.pdf_hide_unreferenced_tags.IsChecked:
                                    pdf_options.HideUnreferencedViewTags = True
                            except:
                                logger.debug("HideUnreferencedViewTags not supported in Revit {}".format(REVIT_VERSION))

                        # Create System.Collections.Generic.List for element IDs
                        element_ids = List[DB.ElementId]()
                        element_ids.Add(element.Id)

                        # VERSION-AWARE: Export using Revit's native PDF export
                        # Use Smart API Adapter if available for intelligent export (handles method overload resolution)
                        if self.api_adapter:
                            # Smart adapter automatically handles version differences and method overload resolution
                            self.api_adapter.export_pdf(output_folder, filename, element_ids, pdf_options)
                        else:
                            # Fallback to direct export call
                            # Revit 2022-2026 signature: Export(String folder, IList<ElementId> viewIds, PDFExportOptions options)
                            # NOTE: PDF export does NOT take a filename parameter in the Export() method (unlike DWG/DXF)
                            # Instead, filename is set via PDFExportOptions.FileName property (learned from pyRevit)
                            self.doc.Export(output_folder, element_ids, pdf_options)

                        # Wait briefly for file system to update
                        time.sleep(0.3)

                        # Get list of PDF files after export
                        current_pdfs = set(glob.glob(os.path.join(output_folder, "*.pdf")))
                        new_pdfs = current_pdfs - existing_pdfs

                        # Verify file was created
                        expected_file = os.path.join(output_folder, filename + ".pdf")
                        if os.path.exists(expected_file) or new_pdfs:
                            exported_count += 1
                            # Update progress for this export item
                            self.update_export_item_progress(item.SheetNumber, "PDF", 100)

                    except Exception as ex:
                        logger.error("Error exporting {} to PDF: {}".format(element_name, ex))

            return exported_count

        except Exception as ex:
            logger.error("PDF export failed: {}".format(ex))
            return 0

    def export_to_dwf(self, items, output_folder):
        """Export items (sheets or views) to DWF format using Revit's native DWF export with version-aware API usage.

        Supports Revit 2022-2026 with appropriate API handling for each version.
        """
        try:
            # Sync cached values with live values from Revit
            for item in items:
                if hasattr(item, 'Sheet'):
                    item.SheetNumber = item.Sheet.SheetNumber
                    item.SheetName = item.Sheet.Name
                elif hasattr(item, 'View'):
                    item.SheetNumber = item.View.Name
                    item.ViewName = item.View.Name

            # Create DWF export options
            dwf_options = DWFExportOptions()

            exported_count = 0

            for item in items:
                try:
                    # Get the actual element (sheet or view)
                    if hasattr(item, 'Sheet'):
                        element = item.Sheet
                        element_name = element.SheetNumber
                    elif hasattr(item, 'View'):
                        element = item.View
                        element_name = element.Name
                    else:
                        continue

                    # Update progress text to show current item and format
                    self.progress_text.Text = "Exporting {} to DWF...".format(element_name)

                    filename = item.CustomFilename or self.get_export_filename(item)

                    # Remove extension if present
                    if filename.lower().endswith('.dwf'):
                        filename = filename[:-4]

                    # VERSION-AWARE: Export handling
                    # Revit 2022-2026 all support ViewSet for DWF export
                    # Signature: Export(String folder, String name, ViewSet views, DWFExportOptions options)
                    view_set = DB.ViewSet()
                    view_set.Insert(element)
                    self.doc.Export(output_folder, filename, view_set, dwf_options)

                    # Verify file was created
                    expected_file = os.path.join(output_folder, filename + ".dwf")
                    if os.path.exists(expected_file):
                        exported_count += 1
                        # Update progress for this export item
                        self.update_export_item_progress(item.SheetNumber, "DWF", 100)

                except Exception as ex:
                    logger.error("Error exporting {} to DWF: {}".format(element_name, ex))

            return exported_count

        except Exception as ex:
            logger.error("DWF export failed: {}".format(ex))
            return 0

    def export_to_nwd(self, items, output_folder):
        """Export items (sheets or views) to Navisworks NWD format with version-aware API usage."""
        if not HAS_NAVISWORKS:
            return 0

        try:
            # Sync cached values with live values from Revit
            for item in items:
                if hasattr(item, 'Sheet'):
                    item.SheetNumber = item.Sheet.SheetNumber
                    item.SheetName = item.Sheet.Name
                elif hasattr(item, 'View'):
                    item.SheetNumber = item.View.Name
                    item.ViewName = item.View.Name

            # Create Navisworks export options
            nwd_options = NavisworksExportOptions()

            exported_count = 0

            for item in items:
                try:
                    # Get the actual element (sheet or view)
                    if hasattr(item, 'Sheet'):
                        element = item.Sheet
                        element_name = element.SheetNumber
                    elif hasattr(item, 'View'):
                        element = item.View
                        element_name = element.Name
                    else:
                        continue

                    # Update progress text to show current item and format
                    self.progress_text.Text = "Exporting {} to NWC...".format(element_name)

                    filename = item.CustomFilename or self.get_export_filename(item)
                    filepath = os.path.join(output_folder, filename + ".nwc")

                    # Export view
                    nwd_options.ExportScope = DB.NavisworksExportScope.View
                    nwd_options.ViewId = element.Id

                    self.doc.Export(output_folder, filename, nwd_options)

                    exported_count += 1
                    # Update progress for this export item
                    self.update_export_item_progress(item.SheetNumber, "NWC", 100)

                except Exception as ex:
                    logger.error("Error exporting {} to NWC: {}".format(element_name, ex))

            return exported_count

        except Exception as ex:
            logger.error("NWC export failed: {}".format(ex))
            return 0

    def export_to_ifc(self, items, output_folder):
        """Export to IFC format with version-aware API usage."""
        if not HAS_IFC:
            return 0

        try:
            # Sync cached values with live values from Revit
            for item in items:
                if hasattr(item, 'Sheet'):
                    item.SheetNumber = item.Sheet.SheetNumber
                    item.SheetName = item.Sheet.Name
                elif hasattr(item, 'View'):
                    item.SheetNumber = item.View.Name
                    item.ViewName = item.View.Name

            # Create IFC export options — version read from UI
            ifc_options = IFCExportOptions()
            ifc_ver_map = {0: IFCVersion.IFC2x2, 1: IFCVersion.IFC2x3, 2: IFCVersion.IFC4}
            ifc_ver_index = self.ifc_version.SelectedIndex if hasattr(self, 'ifc_version') else 1
            ifc_options.FileVersion = ifc_ver_map.get(ifc_ver_index, IFCVersion.IFC2x3)
            ifc_options.WallAndColumnSplitting = True

            exported_count = 0

            # For IFC, export the entire model once
            # Note: IFC export requires a transaction (unique requirement compared to other formats)
            if len(items) > 0:
                try:
                    # Update progress text to show IFC export
                    self.progress_text.Text = "Exporting entire model to IFC..."

                    # Generate filename using naming pattern similar to combined PDF
                    # Use first and last item for combined exports
                    if len(items) > 1:
                        first_item = items[0]
                        last_item = items[-1]
                        # Get names from actual Revit elements
                        if hasattr(first_item, 'Sheet'):
                            first_name = first_item.Sheet.SheetNumber
                            last_name = last_item.Sheet.SheetNumber
                        else:
                            first_name = first_item.View.Name[:20]
                            last_name = last_item.View.Name[:20]
                        filename = "{}-{}_Model_IFC".format(first_name, last_name)
                    elif len(items) == 1:
                        # Use the naming pattern for single item
                        filename = self.get_export_filename(items[0]) + "_IFC"
                    else:
                        filename = "Model_IFC_Export"

                    # Remove extension if present
                    if filename.lower().endswith('.ifc'):
                        filename = filename[:-4]

                    # Clean filename - remove invalid chars and extra spaces
                    invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
                    for char in invalid_chars:
                        filename = filename.replace(char, '_')
                    filename = filename.strip()

                    # IFC export needs to be wrapped in a transaction
                    with Transaction(self.doc, "Export IFC") as trans:
                        trans.Start()
                        self.doc.Export(output_folder, filename, ifc_options)
                        trans.Commit()

                    exported_count = 1
                    # Update progress for all IFC export items
                    for item in items:
                        self.update_export_item_progress(item.SheetNumber, "IFC", 100)
                except Exception as ex:
                    logger.error("Error exporting to IFC: {}".format(ex))

            return exported_count

        except Exception as ex:
            logger.error("IFC export failed: {}".format(ex))
            return 0

    def export_to_images(self, items, output_folder):
        """Export items (sheets or views) to image format using Revit's native image export with version-aware API usage."""
        try:
            import time
            import glob

            # Sync cached values with live values from Revit
            for item in items:
                if hasattr(item, 'Sheet'):
                    item.SheetNumber = item.Sheet.SheetNumber
                    item.SheetName = item.Sheet.Name
                elif hasattr(item, 'View'):
                    item.SheetNumber = item.View.Name
                    item.ViewName = item.View.Name

            exported_count = 0

            for item in items:
                try:
                    # Get the actual element (sheet or view)
                    if hasattr(item, 'Sheet'):
                        element = item.Sheet
                        element_name = element.SheetNumber
                    elif hasattr(item, 'View'):
                        element = item.View
                        element_name = element.Name
                    else:
                        continue

                    # Update progress text to show current item and format
                    self.progress_text.Text = "Exporting {} to Image...".format(element_name)

                    filename = item.CustomFilename or self.get_export_filename(item)

                    # Remove extension if present
                    if filename.lower().endswith('.png'):
                        filename = filename[:-4]

                    # Clean filename - remove invalid chars and extra spaces
                    invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
                    for char in invalid_chars:
                        filename = filename.replace(char, '_')
                    filename = filename.strip()

                    # Get list of existing image files before export
                    existing_images = set(glob.glob(os.path.join(output_folder, "*.png")))

                    # Read image options from UI
                    use_fit_to_page = not (hasattr(self, 'img_zoom_to') and self.img_zoom_to.IsChecked)
                    fit_pixels = 1080
                    zoom_percent = 50
                    try:
                        if hasattr(self, 'img_fit_pixels'):
                            fit_pixels = int(self.img_fit_pixels.Text)
                    except:
                        pass
                    try:
                        if hasattr(self, 'img_zoom_percent'):
                            zoom_percent = int(self.img_zoom_percent.Text)
                    except:
                        pass
                    use_horizontal = not (hasattr(self, 'img_dir_vertical') and self.img_dir_vertical.IsChecked)
                    dpi_map = {
                        0: ImageResolution.DPI_72,
                        1: ImageResolution.DPI_96,
                        2: ImageResolution.DPI_150,
                        3: ImageResolution.DPI_300,
                        4: ImageResolution.DPI_600,
                    }
                    dpi_index = self.img_dpi.SelectedIndex if hasattr(self, 'img_dpi') else 2
                    img_resolution = dpi_map.get(dpi_index, ImageResolution.DPI_150)
                    shaded_idx = self.img_shaded_format.SelectedIndex if hasattr(self, 'img_shaded_format') else 0
                    nonshaded_idx = self.img_nonshaded_format.SelectedIndex if hasattr(self, 'img_nonshaded_format') else 0
                    shaded_fmt = ImageFileType.JPEGLossless if shaded_idx == 1 else ImageFileType.PNG
                    nonshaded_fmt = ImageFileType.JPEGLossless if nonshaded_idx == 1 else ImageFileType.PNG

                    # Create image export options for each sheet
                    img_options = ImageExportOptions()
                    if use_fit_to_page:
                        img_options.ZoomType = DB.ZoomFitType.FitToPage
                        img_options.PixelSize = fit_pixels
                        img_options.FitDirection = DB.FitDirectionType.Horizontal if use_horizontal else DB.FitDirectionType.Vertical
                    else:
                        img_options.ZoomType = DB.ZoomFitType.Zoom
                        img_options.Zoom = zoom_percent
                    img_options.ImageResolution = img_resolution
                    img_options.FilePath = os.path.join(output_folder, filename)
                    img_options.HLRandWFViewsFileType = nonshaded_fmt
                    img_options.ShadowViewsFileType = shaded_fmt
                    img_options.ExportRange = DB.ExportRange.SetOfViews

                    # Set the view IDs using System.Collections.Generic.List
                    view_ids = List[DB.ElementId]()
                    view_ids.Add(element.Id)
                    img_options.SetViewsAndSheets(view_ids)

                    # Export using Revit's native image export
                    self.doc.ExportImage(img_options)

                    # Wait briefly for file system to update
                    time.sleep(0.3)

                    # Get list of image files after export
                    current_images = set(glob.glob(os.path.join(output_folder, "*.png")))
                    new_images = current_images - existing_images

                    # Verify file was created
                    expected_file = os.path.join(output_folder, filename + ".png")

                    # Handle Revit's automatic filename modification
                    # Revit adds " - Sheet - " or similar to filenames, so we need to rename
                    if new_images:
                        # Get the actual file created by Revit
                        actual_file = list(new_images)[0]

                        # If the actual file is different from expected, rename it
                        if actual_file != expected_file:
                            try:
                                # Rename to the expected filename
                                os.rename(actual_file, expected_file)
                            except Exception as rename_ex:
                                logger.warning("Could not rename {} to {}: {}".format(
                                    os.path.basename(actual_file),
                                    os.path.basename(expected_file),
                                    rename_ex
                                ))

                    if os.path.exists(expected_file):
                        exported_count += 1
                        # Update progress for this export item
                        self.update_export_item_progress(item.SheetNumber, "IMG", 100)

                except Exception as ex:
                    logger.error("Error exporting {} to Image: {}".format(element_name, ex))

            return exported_count

        except Exception as ex:
            logger.error("Image export failed: {}".format(ex))
            return 0

    def profile_button_clicked(self, sender, e):
        """Show profile management dialog."""
        try:
            from System.Windows import Window, TextBlock, Button, Thickness, VerticalAlignment, HorizontalAlignment
            from System.Windows.Controls import StackPanel, ListView, Grid, RowDefinition, ColumnDefinition, GridLength, GridUnitType, Border, ScrollViewer, ScrollBarVisibility
            from System.Windows.Media import SolidColorBrush, Color

            # Create dialog window
            dialog = Window()
            dialog.Title = "Profile Management"
            dialog.Width = 800
            dialog.Height = 500
            dialog.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterOwner
            dialog.Owner = self
            dialog.ShowInTaskbar = False

            # Create main grid
            main_grid = Grid()
            main_grid.Margin = Thickness(20)

            # Define rows
            row1 = RowDefinition()
            row1.Height = GridLength(1, GridUnitType.Star)
            row2 = RowDefinition()
            row2.Height = GridLength.Auto
            main_grid.RowDefinitions.Add(row1)
            main_grid.RowDefinitions.Add(row2)

            # Create content grid for listview and buttons
            content_grid = Grid()
            Grid.SetRow(content_grid, 0)

            # Define columns
            col1 = ColumnDefinition()
            col1.Width = GridLength(1, GridUnitType.Star)
            col2 = ColumnDefinition()
            col2.Width = GridLength(200, GridUnitType.Pixel)
            content_grid.ColumnDefinitions.Add(col1)
            content_grid.ColumnDefinitions.Add(col2)

            # Profile ListView
            profile_list = ListView()
            profile_list.Margin = Thickness(0, 0, 10, 0)
            profile_list.ItemsSource = self.profiles
            Grid.SetColumn(profile_list, 0)

            # Create custom template for listview items
            from System.Windows import DataTemplate, FrameworkElementFactory
            from System.Windows.Controls import TextBlock as WPFTextBlock
            template = DataTemplate()

            # Stack panel for each item
            stack_factory = FrameworkElementFactory(StackPanel)
            stack_factory.SetValue(StackPanel.MarginProperty, Thickness(5))

            # Name TextBlock
            name_factory = FrameworkElementFactory(WPFTextBlock)
            name_factory.SetBinding(WPFTextBlock.TextProperty, System.Windows.Data.Binding("Name"))
            name_factory.SetValue(WPFTextBlock.FontWeightProperty, System.Windows.FontWeights.Bold)
            name_factory.SetValue(WPFTextBlock.FontSizeProperty, 14.0)
            stack_factory.AppendChild(name_factory)

            # Description TextBlock
            desc_factory = FrameworkElementFactory(WPFTextBlock)
            desc_factory.SetBinding(WPFTextBlock.TextProperty, System.Windows.Data.Binding("Description"))
            desc_factory.SetValue(WPFTextBlock.FontSizeProperty, 11.0)
            desc_factory.SetValue(WPFTextBlock.ForegroundProperty, SolidColorBrush(Color.FromRgb(127, 140, 141)))
            desc_factory.SetValue(WPFTextBlock.MarginProperty, Thickness(0, 2, 0, 0))
            stack_factory.AppendChild(desc_factory)

            # Date TextBlock
            date_factory = FrameworkElementFactory(WPFTextBlock)
            date_factory.SetBinding(WPFTextBlock.TextProperty, System.Windows.Data.Binding("CreatedDate"))
            date_factory.SetValue(WPFTextBlock.FontSizeProperty, 10.0)
            date_factory.SetValue(WPFTextBlock.ForegroundProperty, SolidColorBrush(Color.FromRgb(149, 165, 166)))
            date_factory.SetValue(WPFTextBlock.MarginProperty, Thickness(0, 5, 0, 0))
            stack_factory.AppendChild(date_factory)

            template.VisualTree = stack_factory
            profile_list.ItemTemplate = template

            content_grid.Children.Add(profile_list)

            # Buttons panel
            buttons_panel = StackPanel()
            buttons_panel.Margin = Thickness(10, 0, 0, 0)
            Grid.SetColumn(buttons_panel, 1)

            # Save button
            save_btn = Button()
            save_btn.Content = "Save Current Settings"
            save_btn.Height = 32
            save_btn.Margin = Thickness(0, 0, 0, 8)
            save_btn.Click += lambda s, ev: (self.save_profile_clicked(s, ev), dialog.Close() if hasattr(self, '_profile_saved') else None)
            buttons_panel.Children.Add(save_btn)

            # Load button
            load_btn = Button()
            load_btn.Content = "Load Profile"
            load_btn.Height = 32
            load_btn.Margin = Thickness(0, 0, 0, 8)

            def load_and_close(s, ev):
                if profile_list.SelectedItem:
                    self.load_profile_clicked(s, ev)
                    dialog.Close()

            load_btn.Click += load_and_close
            buttons_panel.Children.Add(load_btn)

            # Delete button
            delete_btn = Button()
            delete_btn.Content = "Delete Profile"
            delete_btn.Height = 32
            delete_btn.Margin = Thickness(0, 0, 0, 8)

            def delete_and_refresh(s, ev):
                self.delete_profile_clicked(s, ev)
                profile_list.ItemsSource = None
                profile_list.ItemsSource = self.profiles

            delete_btn.Click += delete_and_refresh
            buttons_panel.Children.Add(delete_btn)

            # Separator
            from System.Windows.Controls import Separator
            sep = Separator()
            sep.Margin = Thickness(0, 15, 0, 15)
            buttons_panel.Children.Add(sep)

            # Import button
            import_btn = Button()
            import_btn.Content = "Import from File..."
            import_btn.Height = 32
            import_btn.Margin = Thickness(0, 0, 0, 8)

            def import_and_refresh(s, ev):
                self.import_profile_clicked(s, ev)
                profile_list.ItemsSource = None
                profile_list.ItemsSource = self.profiles

            import_btn.Click += import_and_refresh
            buttons_panel.Children.Add(import_btn)

            # Export button
            export_btn = Button()
            export_btn.Content = "Export to File..."
            export_btn.Height = 32
            export_btn.Margin = Thickness(0, 0, 0, 8)
            export_btn.Click += self.export_profile_clicked
            buttons_panel.Children.Add(export_btn)

            content_grid.Children.Add(buttons_panel)
            main_grid.Children.Add(content_grid)

            # Close button at bottom
            close_button = Button()
            close_button.Content = "Close"
            close_button.Width = 100
            close_button.Height = 32
            close_button.HorizontalAlignment = HorizontalAlignment.Right
            close_button.Margin = Thickness(0, 15, 0, 0)
            close_button.Click += lambda s, ev: dialog.Close()
            Grid.SetRow(close_button, 1)
            main_grid.Children.Add(close_button)

            dialog.Content = main_grid

            # Store reference for event handlers
            self.profiles_listview = profile_list

            # Show dialog
            dialog.ShowDialog()

        except Exception as ex:
            logger.error("Error showing profile dialog: {}".format(ex))
            forms.alert("Error showing profile dialog:\n{}".format(str(ex)))

    def help_button_clicked(self, sender, e):
        """Show help information."""
        help_message = ("BatchOut - Batch Export Tool\n\n"
                        "This tool allows you to batch export sheets and views to multiple formats.\n\n"
                        "Features:\n"
                        "• Export to PDF, DWG, DWF, DGN, NWC, IFC, and Image formats\n"
                        "• Custom naming patterns with parameters\n"
                        "• Advanced export options\n"
                        "• Profile management for saving/loading configurations\n"
                        "• Real-time progress tracking\n\n"
                        "For more help, please refer to the documentation.")
        forms.alert(help_message, title="BatchOut Help")

    def minimize_button_clicked(self, sender, e):
        """Minimize the window."""
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        """Toggle between maximize and restore."""
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        """Close the window."""
        self.Close()


    def cancel_export(self, sender, e):
        """Cancel and close the window."""
        self.Close()


# MAIN SCRIPT
# ==================================================
if __name__ == '__main__':
    # Check if document is open
    if not revit.doc:
        forms.alert("Please open a Revit document first.", exitscript=True)

    # Show Export Manager window
    window = ExportManagerWindow()
    window.ShowDialog()


