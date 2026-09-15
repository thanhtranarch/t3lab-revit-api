# -*- coding: utf-8 -*-
"""BatchOut Dialog — Advanced batch exporter for sheets and views to PDF, DWG, DWF, NWD, IFC.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "BatchOut Dialog"

# IMPORT LIBRARIES
# ==================================================
import os
import sys

for _env in ('APPDATA', 'PROGRAMDATA'):
    _base = os.environ.get(_env, '')
    if _base:
        for _clone in ('pyRevit-Master', 'pyRevit'):
            _ceng = os.path.join(_base, _clone, 'bin', 'cengines', 'CPY3123')
            if os.path.isdir(_ceng):
                for _d in (_ceng, os.path.join(_ceng, 'Lib')):
                    if hasattr(os, 'add_dll_directory'):
                        try:
                            os.add_dll_directory(_d)
                        except Exception:
                            pass
                for _p in (_ceng, os.path.join(_ceng, 'Lib'), os.path.join(_ceng, 'python312.zip')):
                    if os.path.exists(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)


_script_dir = os.path.dirname(__file__)
_ext_dir = os.path.dirname(os.path.dirname(os.path.dirname(_script_dir)))
_lib_dir = os.path.join(_ext_dir, 'lib')
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass

try:
    from importlib import reload as _reload
    if 'GUI.WPF_Base' in sys.modules:
        _reload(sys.modules['GUI.WPF_Base'])
except Exception:
    pass


import clr
import json
import re
import time
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
from System import Uri, UriKind, Action, GC
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Threading import Thread, ThreadStart
from System.Windows.Threading import DispatcherPriority

from pyrevit import revit, DB, UI, forms, script, EXEC_PARAMS
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Autodesk.Revit.DB import (
    Transaction, FilteredElementCollector, BuiltInCategory,
    ViewSheet, ViewSet, ViewSheetSet, DWGExportOptions, DWFExportOptions,
    DGNExportOptions, ExportDWGSettings, ACADVersion, PDFExportOptions,
    ImageExportOptions, ImageFileType, ImageResolution,
    PropOverrideMode, View, ViewPlan, ViewSection, View3D,
    ViewSchedule, ViewDrafting, ViewType,
)
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

from System.Collections.Generic import List


# Import API learner and updater modules
extension_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

from Snippets._compat import eid_value, make_eid

try:
    from Intelligence.api_learner import SmartAPIAdapter, RevitAPILearner
    from Intelligence.api_updater import auto_check_and_update
    HAS_API_LEARNER = True
    _api_learner_err = None
except Exception as _api_learner_ex:
    HAS_API_LEARNER = False
    _api_learner_err = _api_learner_ex


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
# CPython has no ScriptOutput.GetDefault; safe_output()
# returns a no-op window instead of killing the tool.
try:
    from _cpython_bootstrap import safe_output
    output = safe_output()
except Exception:
    output = script.get_output()


def _build_view_type_labels():
    """Display label per ViewType.

    Built with getattr so a member missing from an older/newer Revit API can
    never break module import. The first ten labels must keep their exact
    wording — they are the hard-coded items of `view_type_filter` in the
    UI-locked ExportManager.xaml.
    """
    labels = {}
    for name, label in (
            ("FloorPlan", "Floor Plan"),
            ("CeilingPlan", "Ceiling Plan"),
            ("Elevation", "Elevation"),
            ("Section", "Section"),
            ("ThreeD", "3D View"),
            ("Schedule", "Schedule"),
            ("DraftingView", "Drafting"),
            ("Legend", "Legend"),
            ("EngineeringPlan", "Engineering"),
            ("AreaPlan", "Area Plan"),
            ("Detail", "Detail"),
            ("Rendering", "Rendering"),
            ("Walkthrough", "Walkthrough"),
            ("Report", "Report"),
            ("ColumnSchedule", "Column Schedule"),
            ("PanelSchedule", "Panel Schedule"),
            ("CostReport", "Cost Report"),
            ("LoadsReport", "Loads Report"),
            ("PresureLossReport", "Pressure Loss Report"),
    ):
        member = getattr(ViewType, name, None)
        if member is not None:
            labels[member] = label
    return labels


def _build_non_exportable_view_types():
    """ViewTypes that are not user output and must stay out of the Views list:
    Revit's internal/browser pseudo-views and sheets (sheets have their own
    mode). Everything else — legends, schedules, detail views, renderings — is a
    real view a user can ask BatchOut to export, so it must be listed instead of
    being silently dropped."""
    types = []
    for name in ("Internal", "ProjectBrowser", "SystemBrowser",
                 "Undefined", "DrawingSheet"):
        member = getattr(ViewType, name, None)
        if member is not None:
            types.append(member)
    return set(types)


VIEW_TYPE_LABELS = _build_view_type_labels()
NON_EXPORTABLE_VIEW_TYPES = _build_non_exportable_view_types()


def detect_persistent_engine():
    """True only when __persistentengine__ is actually baked into the loaded
    command metadata (pyRevit bakes it at ribbon build — needs a Reload after
    the flag is added to the script).

    The accessor differs per pyRevit runtime build (pyrevitlib 5.1 WIP and
    the compiled runtime DLL can be out of sync — seen 2026-07-15 where
    EXEC_PARAMS.needs_persistent_engine raised AttributeError because this
    machine's 2023 runtime has no ScriptRuntime.EngineConfigs):
    - newer layout: script_runtime.EngineConfigs.PersistentEngine
      (what EXEC_PARAMS.needs_persistent_engine reads)
    - this runtime:  script_runtime.ScriptRuntimeConfigs.EngineConfigs,
      a JSON string like {"clean": false, "full_frame": false,
      "persistent": true}
    Defaults to False — the safe direction (modal fallback) when
    undetectable.
    """
    try:
        return bool(EXEC_PARAMS.needs_persistent_engine)
    except Exception:
        pass
    try:
        cfg_json = EXEC_PARAMS.script_runtime.ScriptRuntimeConfigs.EngineConfigs
        if cfg_json:
            return bool(json.loads(cfg_json).get('persistent', False))
    except Exception as ex:
        logger.debug("Persistent engine detection failed: {}".format(ex))
    return False

# F6: BatchOut runs fine without the optional Intelligence helpers, but the
# import above swallowed any failure silently (and it happens before `logger`
# exists). Surface it once here so a broken/missing api_learner is visible when
# debugging instead of just a mysteriously absent smart-adaptation feature.
if not HAS_API_LEARNER:
    logger.warning(
        "BatchOut Intelligence helpers unavailable (api_learner/api_updater); "
        "smart API adaptation & auto-update disabled: {}".format(_api_learner_err))

# Get Revit version information
# Read from the Application, not the document: `revit.doc` is None when this
# tool is launched from the Assistant pane or with no project open, and the
# old `int(revit.doc.Application.VersionNumber)` raised at IMPORT time
# ('NoneType' object has no attribute 'Application') so the window never opened.
from Snippets._host import get_revit_version
REVIT_VERSION = get_revit_version()

# CLASS/FUNCTIONS
# ==================================================
try:
    _Reactive = getattr(forms, 'Reactive', object)
except Exception:
    _Reactive = object


class SheetItem(_Reactive):
    """Represents a sheet item in the list - optimized for performance."""
    def __init__(self, sheet, is_selected=False, lazy=False):
        self._property_changed_handlers = []
        self.Sheet = sheet
        self._is_selected = bool(is_selected)
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

    @property
    def IsSelected(self):
        return self._is_selected

    @IsSelected.setter
    def IsSelected(self, value):
        val = bool(value)
        if self._is_selected != val:
            self._is_selected = val
            self.OnPropertyChanged("IsSelected")

    def add_PropertyChanged(self, handler):
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, property_name):
        try:
            if PropertyChangedEventArgs is not None:
                args = PropertyChangedEventArgs(property_name)
                for handler in list(self._property_changed_handlers):
                    try:
                        handler(self, args)
                    except Exception:
                        pass
        except Exception:
            pass

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


class ViewItem(_Reactive):
    """Represents a view item in the list - optimized for performance."""

    _VIEW_TYPE_MAP = VIEW_TYPE_LABELS

    def __init__(self, view, is_selected=False, lazy=False):
        self._property_changed_handlers = []
        self.View = view
        self._is_selected = bool(is_selected)
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

    @property
    def IsSelected(self):
        return self._is_selected

    @IsSelected.setter
    def IsSelected(self, value):
        val = bool(value)
        if self._is_selected != val:
            self._is_selected = val
            self.OnPropertyChanged("IsSelected")

    def add_PropertyChanged(self, handler):
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, property_name):
        try:
            if PropertyChangedEventArgs is not None:
                args = PropertyChangedEventArgs(property_name)
                for handler in list(self._property_changed_handlers):
                    try:
                        handler(self, args)
                    except Exception:
                        pass
        except Exception:
            pass

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
        self._property_changed_handlers = []
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
        self._progress = 0
        self._status = ""
        self.ProgressText = ""

    @property
    def Progress(self):
        return self._progress

    @Progress.setter
    def Progress(self, value):
        if self._progress != value:
            self._progress = value
            self.OnPropertyChanged("Progress")

    @property
    def Status(self):
        return self._status

    @Status.setter
    def Status(self, value):
        if self._status != value:
            self._status = value
            self.OnPropertyChanged("Status")

    def add_PropertyChanged(self, handler):
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, property_name):
        try:
            if PropertyChangedEventArgs is not None:
                args = PropertyChangedEventArgs(property_name)
                for handler in list(self._property_changed_handlers):
                    try:
                        handler(self, args)
                    except Exception:
                        pass
        except Exception:
            pass


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
        """Create profile from dictionary.

        Rejects anything that is not a profile mapping up front: the profiles
        folder also holds bookkeeping JSON (the crash history is a *list*), and
        calling .items() on that raised
        "'list' object has no attribute 'items'" in the loader.
        """
        if not isinstance(data, dict):
            raise ValueError(
                "not a profile object (got {})".format(type(data).__name__))
        profile = ExportProfile()
        for key, value in data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        return profile


try:
    from Services.batchout_handler import BatchOutEventHandler
except Exception:
    import uuid
    class BatchOutEventHandler(IExternalEventHandler):
        __namespace__ = "T3Lab.BatchOut"
        def __init__(self, logger):
            self._logger = logger
            self._queue = []

        def add(self, action):
            self._queue.append(action)

        def Execute(self, uiapp):
            try:
                actions = self._queue
                self._queue = []
                for action in actions:
                    try:
                        action()
                    except Exception as ex:
                        try:
                            self._logger.error(
                                "BatchOut external event failed: {}".format(ex))
                        except Exception:
                            pass
            except Exception:
                pass

        def GetName(self):
            return "T3Lab BatchOut Handler"


class _PendingExportError(RuntimeError):
    """Revit still owns a pending transaction; later exports must not run."""


class ExportManagerWindow(T3WPFWindow):
    """Export Manager Window."""

    # Auto-saved "latest setup" — lives beside profiles but is not listed as one
    LATEST_SETUP_FILENAME = '_latest_setup.json'

    def __init__(self):
        try:
            # Get absolute path to XAML file from lib/GUI/Tools folder
            xaml_file_path = os.path.join(os.path.dirname(__file__), 'Tools', 'ExportManager.xaml')
            if not os.path.exists(xaml_file_path):
                ext_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                xaml_file_path = os.path.join(ext_dir, 'lib', 'GUI', 'Tools', 'ExportManager.xaml')
            T3WPFWindow.__init__(self, xaml_file_path)

            self.doc = revit.doc

            # Modeless is only safe when pyRevit keeps this engine alive.
            # __persistentengine__ is baked into the command metadata at
            # ribbon build time — until the user runs pyRevit Reload after
            # the flag was added, the engine scope is torn down when this
            # command returns and every later callback dies on empty module
            # globals (NameError) → fatal Revit crash (seen 2026-07-14,
            # journal session 1006). Detect the actual engine config and
            # fall back to modal when the flag is not active yet.
            self.modeless = detect_persistent_engine()

            # Modeless support: window no longer blocks Revit, so export and
            # transaction work must be marshalled back into API context via
            # this ExternalEvent. It is used ONLY on the modeless path
            # (see _run_in_api_context); modal mode marshals through the
            # Dispatcher and never touches it.
            self._api_handler = BatchOutEventHandler(logger)
            self._api_event = None
            if self.modeless:
                # ExternalEvent.Create() only succeeds inside a valid Revit API
                # context — i.e. a pushbutton command executing on the UI
                # thread. When this window is constructed from a NON-command
                # context — the docked T3Lab Assistant pane's message handler,
                # or the Assistant's background direct-export thread — Create
                # throws "Attempting to create an ExternalEvent outside of a
                # standard API execution" and the whole launch dies before the
                # window ever appears. Degrade to modal (which needs no
                # ExternalEvent) instead of hard-crashing. Mirrors
                # server.ensure_external_event(), which returns the error
                # rather than letting Create escape.
                try:
                    self._api_event = ExternalEvent.Create(self._api_handler)
                except BaseException as ee_ex:
                    logger.debug(
                        "ExternalEvent.Create unavailable (window constructed "
                        "outside API context) — falling back to modal: "
                        "{}".format(ee_ex))
                    self.modeless = False

            self.all_sheets = []
            self.filtered_sheets = []
            self.all_views = []
            self.filtered_views = []
            self.export_items = []
            self._export_running = False
            self.selection_mode = "sheets"  # "sheets" or "views"
            self.profiles = []  # List of ExportProfile objects
            self.profiles_folder = os.path.join(os.path.expanduser('~'), 'Documents', 'T3Lab_BatchOut_Profiles')
            self.latest_setup_file = os.path.join(self.profiles_folder, self.LATEST_SETUP_FILENAME)

            # Native-crash breadcrumb: which sheet+format Revit was rendering when
            # it died. The in-flight marker is written before every native export
            # call and removed after it returns, so a surviving marker == that
            # exact combination killed the process. Confirmed kills are promoted
            # into a durable history so we can skip them instead of re-crashing.
            self._crash_marker_file = os.path.join(self.profiles_folder, '_export_crash_marker.json')
            self._crash_history_file = os.path.join(self.profiles_folder, '_export_crash_history.json')
            self._crash_history = self._read_crash_history()
            self._pending_crash = self._promote_crash_marker()
            self._skipped_fatal = []
            # Items exported under safe mode — reported at the end, because such
            # a file is NOT what a normal export would have produced.
            self._safe_applied = []
            self._safe_mode = False
            # Known-fatal items are exported anyway by default — a silently
            # missing file is worse than a crash the user was warned about.
            # Only an explicit choice in the pre-export prompt turns this on.
            self._skip_fatal = False
            # Every selected item+format that produced NO file, with the reason.
            # Surfaced in the completion dialog so an export can never go
            # missing quietly.
            self._failed_items = []
            # Filenames already written during THIS run (see _reserve_filename).
            self._used_filenames = set()
            self._verify_misses = 0

            # Performance optimization: caches for batch loading
            self._titleblock_size_cache = {}  # Sheet ID -> (width_mm, height_mm)
            self._paper_size_cache = {}  # Sheet ID -> (size_name, orientation)
            self._titleblock_cache_loaded = False
            self._batch_updating = False

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

            # Restore auto-saved latest setup from the previous session
            self._restore_latest_setup()

            # If a prior export crashed Revit mid-sheet, surface which sheet
            # (set after _restore_latest_setup so this status wins the display)
            self._warn_if_previous_crash()

            # Attach event handler for click-to-select functionality
            # This is done programmatically because EventSetters in Styles don't work with pyRevit
            self.sheets_listview.PreviewMouseLeftButtonDown += self.listview_clicked

            # Sync selection state → count whenever WPF native selection changes
            self.sheets_listview.SelectionChanged += self.sheets_selection_changed

            # Attach event handler for tab changes to update preview
            self.main_tabs.SelectionChanged += self.tab_changed

            # Auto-save latest setup on any close path (X, Close button, Alt+F4)
            self.Closing += self._window_closing_save_setup
            self.Closed += self._window_closed_dispose

            # Update button text based on current tab
            self.update_navigation_buttons()
            self._init_ai_mode()

        except Exception as ex:
            logger.error("Error initializing BatchOut window: {}".format(ex))
            raise

    # ── AI Mode Support ──────────────────────────────────────────────────

    def _init_ai_mode(self):
        self._prev_naming_backup = []
        try:
            if hasattr(self, 'is_ai_mode_active') and self.is_ai_mode_active():
                if hasattr(self, 'ai_mode_badge') and self.ai_mode_badge:
                    self.ai_mode_badge.Visibility = Visibility.Visible
                if hasattr(self, 'txt_ai_status') and self.txt_ai_status:
                    info = self.get_ai_status_info()
                    self.txt_ai_status.Text = "AI Mode: {}".format(info.get('model', 'Ready'))
        except Exception as ex:
            logger.warning("AI Mode init failed: {}".format(ex))

    def ai_verify_naming_clicked(self, sender, e):
        """AI QA: Audit sheet numbers and names, and suggest standardized filenames (ISO 19650 compliant)."""
        btn = getattr(self, 'btn_ai_verify_naming', None)
        orig_content = "✨ AI Naming / QA"

        def _restore_btn():
            if btn:
                btn.Content = orig_content
                btn.IsEnabled = True

        try:
            items_list = self.all_sheets if self.selection_mode == 'sheets' else self.all_views
            selected_items = [item for item in items_list if item.IsSelected]
            target_items = selected_items if selected_items else items_list[:25]

            if not target_items:
                forms.alert("No items found to verify.", title="AI Naming QA")
                return

            def _apply_classic():
                self._prev_naming_backup = [(item, getattr(item, 'CustomFilename', '')) for item in target_items]
                undo_btn = getattr(self, 'btn_ai_undo_naming', None)
                if undo_btn:
                    undo_btn.Visibility = Visibility.Visible

                applied = 0
                for item in target_items:
                    num = getattr(item, 'SheetNumber', getattr(item, 'Name', ''))
                    name = getattr(item, 'SheetName', '')
                    base = "{}_{}".format(num, name).strip('_') if name else num
                    clean_name = re.sub(r'[\\/*?:"<>|]', '_', base)
                    item.CustomFilename = clean_name
                    applied += 1
                self.sheets_listview.Items.Refresh()
                self.status_text.Text = "Rule-based Naming: Applied standard format to {} item(s)".format(applied)
                _restore_btn()

            if not hasattr(self, 'is_ai_mode_active') or not self.is_ai_mode_active():
                _apply_classic()
                return

            if btn:
                btn.Content = "⏳ Verifying..."
                btn.IsEnabled = False

            self.status_text.Text = "AI auditing naming conventions for {} item(s)...".format(len(target_items))

            data_payload = []
            for item in target_items:
                data_payload.append({
                    "number": str(getattr(item, 'SheetNumber', getattr(item, 'Name', ''))),
                    "name": str(getattr(item, 'SheetName', '')),
                    "rev": str(getattr(item, 'Revision', '')),
                    "current_custom": str(getattr(item, 'CustomFilename', ''))
                })

            prompt = (
                "You are a BIM Manager specializing in ISO 19650 and AEC drawing deliverables.\n"
                "Audit the following sheets/views and generate standardized, clean export filenames.\n"
                "Follow ISO 19650 pattern where possible ([Discipline]-[Level]-[Type]-[Number]_[Title]), "
                "or clean AEC standard [SheetNumber]_[SheetName]. Remove invalid filesystem characters.\n"
                "Return JSON ONLY with structure:\n"
                "{\n"
                "  \"items\": [\n"
                "    {\"number\": string, \"suggested_filename\": string, \"standard\": string}\n"
                "  ],\n"
                "  \"summary\": string\n"
                "}\n"
                "Sheets:\n" + json.dumps(data_payload)
            )

            def _worker():
                return self.ai_bridge.ask_json(prompt, fast=True)

            def _callback(res, err):
                try:
                    if err or not res or not isinstance(res, dict) or 'items' not in res:
                        _apply_classic()
                        return

                    self._prev_naming_backup = [(item, getattr(item, 'CustomFilename', '')) for item in target_items]
                    undo_btn = getattr(self, 'btn_ai_undo_naming', None)
                    if undo_btn:
                        undo_btn.Visibility = Visibility.Visible

                    suggestions = res.get('items', [])
                    summary = res.get('summary', 'Naming verified')
                    sug_map = {s['number']: s['suggested_filename'] for s in suggestions if 'number' in s and 'suggested_filename' in s}

                    applied = 0
                    for item in target_items:
                        num = str(getattr(item, 'SheetNumber', getattr(item, 'Name', '')))
                        if num in sug_map:
                            item.CustomFilename = sug_map[num]
                            applied += 1

                    self.sheets_listview.Items.Refresh()
                    self.status_text.Text = "AI Naming: {}. Updated {} filename(s).".format(summary, applied)
                finally:
                    _restore_btn()

            self.run_ai_async(_worker, _callback)

        except Exception as ex:
            _restore_btn()
            logger.error("Error in ai_verify_naming_clicked: {}".format(ex))
            self.status_text.Text = "AI Naming error: {}".format(ex)

    def ai_undo_naming_clicked(self, sender, e):
        """Revert custom filenames changed by the last AI/rule naming operation."""
        try:
            if not getattr(self, '_prev_naming_backup', None):
                forms.alert("No naming history to undo.", title="AI Naming Undo")
                return

            restored = 0
            for item, old_val in self._prev_naming_backup:
                item.CustomFilename = old_val
                restored += 1

            self._prev_naming_backup = []
            undo_btn = getattr(self, 'btn_ai_undo_naming', None)
            if undo_btn:
                undo_btn.Visibility = Visibility.Collapsed

            self.sheets_listview.Items.Refresh()
            self.status_text.Text = "Reverted filenames for {} item(s).".format(restored)
        except Exception as ex:
            logger.error("Error in ai_undo_naming_clicked: {}".format(ex))
            self.status_text.Text = "Undo error: {}".format(ex)

    def _check_for_api_updates(self):
        """Check for API updates in the background (non-blocking)."""
        try:
            # Auto-check for updates (this runs on Fridays or if never checked)
            update_result = auto_check_and_update()

            if update_result.get('checked'):
                # Log the check
                logger.info("API update check performed")

                # Log notifications instead of print_md — any print here pops
                # the pyRevit output window on every tool open, which defeats
                # this method being a silent background check.
                notifications = update_result.get('notifications', [])
                for notif in notifications:
                    logger.info("API update [{}]: {}".format(
                        notif.get('severity', 'info'), notif.get('message', '')))

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
                    # Internal bookkeeping files (latest setup, crash marker,
                    # crash history) share this folder but are not profiles.
                    if filename.startswith('_'):
                        continue
                    if filename.endswith('.json'):
                        filepath = os.path.join(self.profiles_folder, filename)
                        try:
                            with open(filepath, 'r') as f:
                                data = json.load(f)
                            # Shape check, not just the '_' name check above:
                            # any stray JSON dropped in this folder must be
                            # skipped silently, never reported as a broken
                            # profile.
                            if not isinstance(data, dict) or 'Name' not in data:
                                logger.debug(
                                    "Skipping non-profile JSON {}".format(filename))
                                continue
                            self.profiles.append(ExportProfile.from_dict(data))
                        except Exception as file_ex:
                            logger.debug("Could not load profile {}: {}".format(filename, file_ex))

            # Update profiles listview (only if dialog is open)
            if hasattr(self, 'profiles_listview') and self.profiles_listview:
                self.profiles_listview.ItemsSource = to_items_source(self.profiles)
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

    def save_latest_setup(self):
        """Auto-save current settings as the latest setup (restored on next open)."""
        try:
            if not os.path.exists(self.profiles_folder):
                os.makedirs(self.profiles_folder)
            profile = self.get_current_settings_as_profile()
            profile.Name = "Latest Setup"
            profile.Description = "Auto-saved on {}".format(datetime.now().strftime("%Y-%m-%d %H:%M"))
            with open(self.latest_setup_file, 'w') as f:
                json.dump(profile.to_dict(), f, indent=2)
            logger.debug("Latest setup auto-saved to {}".format(self.latest_setup_file))
        except Exception as ex:
            logger.warning("Could not auto-save latest setup: {}".format(ex))

    def _restore_latest_setup(self):
        """Restore the auto-saved latest setup from the previous session, if any."""
        try:
            if not os.path.exists(self.latest_setup_file):
                return
            with open(self.latest_setup_file, 'r') as f:
                data = json.load(f)
            self.apply_profile_to_ui(ExportProfile.from_dict(data))
            self.status_text.Text = "Latest setup restored"
            logger.info("Latest setup restored")
        except Exception as ex:
            logger.warning("Could not restore latest setup: {}".format(ex))

    def _window_closing_save_setup(self, sender, e):
        """Closing hook — persist settings so the next session starts where the user left off."""
        self.save_latest_setup()

    def _window_closed_dispose(self, sender, e):
        """Dispose the ExternalEvent once the window is gone.

        With __persistentengine__ this window object outlives its close; an
        undisposed ExternalEvent then survives to Revit shutdown, where its
        stale handler/WPF proxy is destroyed mid-teardown — the 0xc0000005
        'Invalid WPFWndProxy' exit crash (journal.0090, 2026-07-15).
        """
        try:
            if self._api_event is not None:
                self._api_event.Dispose()
        except Exception as ex:
            logger.debug("ExternalEvent dispose failed: {}".format(ex))
        self._api_event = None

    def _run_in_api_context(self, action):
        """Run a callable inside valid Revit API context.

        Modeless: queue through the ExternalEvent (fires when Revit idles).
        Modal fallback: the command is still executing (blocked in
        ShowDialog), so Dispatcher callbacks pump INSIDE the command's API
        context — the pre-modeless behavior, safe here. The ExternalEvent
        path would deadlock instead: Revit never goes idle while a modal
        window blocks the command, so queued actions (lazy loading, preview,
        even Export) would only fire after the window closes.
        """
        if self.modeless:
            if self._api_event is None:
                return  # window already closed and event disposed
            self._api_handler.add(action)
            self._api_event.Raise()
        else:
            self.Dispatcher.BeginInvoke(DispatcherPriority.Background,
                                        Action(action))

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

            # Formats unavailable in this Revit version must never come back checked
            if not HAS_NAVISWORKS:
                self.export_nwd.IsChecked = False
            if not HAS_IFC:
                self.export_ifc.IsChecked = False

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
                self.profiles_listview.ItemsSource = to_items_source(self.profiles)

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
            self.profiles_listview.ItemsSource = to_items_source(self.profiles)

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
                self.profiles_listview.ItemsSource = to_items_source(self.profiles)

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
                            self._titleblock_size_cache[eid_value(owner_view_id)] = (width_mm, height_mm)
                except:
                    continue

            self._titleblock_cache_loaded = True
            logger.debug("Batch loaded {} titleblock sizes".format(len(self._titleblock_size_cache)))

        except Exception as ex:
            logger.debug("Error batch loading titleblocks: {}".format(ex))
            self._titleblock_size_cache = {}
            self._titleblock_cache_loaded = True

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

    def get_paper_format_enum(self, size_str):
        """Map paper size string (e.g. A0, A1, A2, A3, A4, ISO_A3) to Autodesk.Revit.DB.ExportPaperFormat enum."""
        if not size_str:
            return DB.ExportPaperFormat.Default
            
        s = size_str.upper().strip()
        if s == "A0" or "ISO_A0" in s:
            return DB.ExportPaperFormat.ISO_A0
        elif s == "A1" or "ISO_A1" in s:
            return DB.ExportPaperFormat.ISO_A1
        elif s == "A2" or "ISO_A2" in s:
            return DB.ExportPaperFormat.ISO_A2
        elif s == "A3" or "ISO_A3" in s:
            return DB.ExportPaperFormat.ISO_A3
        elif s == "A4" or "ISO_A4" in s:
            return DB.ExportPaperFormat.ISO_A4
        elif "ANSI_A" in s or "ANSI A" in s:
            return DB.ExportPaperFormat.ANSI_A
        elif "ANSI_B" in s or "ANSI B" in s:
            return DB.ExportPaperFormat.ANSI_B
        elif "ANSI_C" in s or "ANSI C" in s:
            return DB.ExportPaperFormat.ANSI_C
        elif "ANSI_D" in s or "ANSI D" in s:
            return DB.ExportPaperFormat.ANSI_D
        elif "ANSI_E" in s or "ANSI E" in s:
            return DB.ExportPaperFormat.ANSI_E
        elif "ARCH_A" in s or "ARCH A" in s:
            return DB.ExportPaperFormat.ARCH_A
        elif "ARCH_B" in s or "ARCH B" in s:
            return DB.ExportPaperFormat.ARCH_B
        elif "ARCH_C" in s or "ARCH C" in s:
            return DB.ExportPaperFormat.ARCH_C
        elif "ARCH_D" in s or "ARCH D" in s:
            return DB.ExportPaperFormat.ARCH_D
        elif "ARCH_E" in s or "ARCH E" in s:
            return DB.ExportPaperFormat.ARCH_E
        elif "ARCH_E1" in s or "ARCH E1" in s:
            return DB.ExportPaperFormat.ARCH_E1
        
        return DB.ExportPaperFormat.Default

    def get_sheet_paper_size_and_orientation(self, sheet):
        """Auto-detect paper size and orientation from Title Block parameters.

        Returns tuple: (paper_size, orientation)
        Uses cached titleblock data for performance (batch loaded).
        """
        try:
            self._ensure_titleblock_cache()
            sheet_id = eid_value(sheet.Id)

            # Check paper size cache first
            if sheet_id in self._paper_size_cache:
                return self._paper_size_cache[sheet_id]

            # Check titleblock size cache
            if sheet_id in self._titleblock_size_cache:
                width_mm, height_mm = self._titleblock_size_cache[sheet_id]
                result = self._detect_paper_size_from_dimensions(width_mm, height_mm)
                self._paper_size_cache[sheet_id] = result
                return result

            # If not in cache, the sheet has no titleblock (e.g. START VIEW or cover sheet).
            # NEVER query FilteredElementCollector(self.doc, sheet.Id) because view-scoped
            # collectors on sheets force Revit to initialize view graphics and delegate to
            # RevitWorker.exe, which deadlocks/crashes Revit on complex or cloud-based start views!
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
                    setup_name = setup.Name if hasattr(setup, 'Name') else "Setup {}".format(eid_value(setup.Id))
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

    # Raster-quality escalation ladder (highest -> lowest). Presentation is the
    # default (maximum fidelity, unchanged). We only step DOWN — and only for a
    # single sheet that has proven it crashes Revit's native PDF rasteriser at a
    # higher rung (the fatal "Invalid array ... Arr.cpp": an internal array /
    # memory overflow while rasterising link-heavy views). 300-DPI "High" is
    # still print-grade, so every normal sheet keeps full Presentation quality.
    RASTER_LADDER = ("Presentation", "High", "Medium", "Low")

    def _apply_pdf_quality(self, pdf_options, level=None):
        """Force high-fidelity color + raster quality on PDF export.

        Revit's vector PDF exporter rasterizes the patches under transparent
        / overlapping filled regions (e.g. semi-transparent 'CLEAR' zones over
        a tile hatch). At the default (low) raster quality the fine linework
        inside those rasterized patches is dropped -> 'missing lines'. Using a
        high raster quality keeps that linework intact. Guarded because the enum
        / property names vary between Revit versions.

        `level` selects the starting rung of RASTER_LADDER; None keeps the
        default (Presentation). Whatever rung is requested, we fall further down
        the ladder if that enum value is missing in this Revit build.
        """
        # Raster quality — the key lever for dropped lines under transparency,
        # and also the key lever for the memory spike that overflows Arr.cpp.
        try:
            rq_enum = getattr(DB, 'RasterQualityType', None)
            if rq_enum is not None and hasattr(pdf_options, 'RasterQuality'):
                start = self.RASTER_LADDER.index(level) if level in self.RASTER_LADDER else 0
                quality = None
                for name in self.RASTER_LADDER[start:]:
                    quality = getattr(rq_enum, name, None)
                    if quality is not None:
                        break
                if quality is not None:
                    pdf_options.RasterQuality = quality
        except Exception as ex:
            logger.debug("Could not set PDF RasterQuality in Revit {}: {}".format(REVIT_VERSION, ex))

        # Color depth — export in full color so nothing collapses to line/grey
        try:
            color_enum = getattr(DB, 'ColorDepthType', None)
            if color_enum is not None and hasattr(pdf_options, 'ColorDepth'):
                color_val = getattr(color_enum, 'Color', None)
                if color_val is not None:
                    pdf_options.ColorDepth = color_val
        except Exception as ex:
            logger.debug("Could not set PDF ColorDepth in Revit {}: {}".format(REVIT_VERSION, ex))

        return pdf_options

    # ── Native-crash breadcrumb ────────────────────────────────────────────
    # Revit's own renderer can hard-crash the whole process while regenerating a
    # sheet for export — observed on this project as either a fatal
    # "Invalid array ... Arr.cpp" or a bare 0xc0000005 on a render worker thread,
    # preceded by "Problems during faceting" on views whose geometry defeats the
    # faceter. It is NOT specific to one format: the same sheets have killed both
    # the PDF rasteriser and the DWG (VHLR) exporter, so raster quality is not a
    # reliable lever. The fatal is uncatchable from Python, so instead we leave a
    # durable marker naming the sheet+format we are about to hand to Revit. If the
    # process dies, that marker survives; the next session promotes it into a
    # crash history, which is used to queue that combination LAST and to offer
    # safe mode for it — not to drop it. Skipping is only ever the user's
    # explicit choice in the pre-export prompt.

    def _read_crash_marker(self):
        """Return the in-flight crash-marker dict from a prior aborted export."""
        try:
            if os.path.exists(self._crash_marker_file):
                with open(self._crash_marker_file, 'r') as f:
                    return json.load(f)
        except Exception as ex:
            logger.debug("Could not read crash marker: {}".format(ex))
        return None

    def _read_crash_history(self):
        """Return the list of sheet+format combinations that have hard-crashed
        Revit before. Never raises — a corrupt file just means no history."""
        try:
            if os.path.exists(self._crash_history_file):
                with open(self._crash_history_file, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
        except Exception as ex:
            logger.debug("Could not read crash history: {}".format(ex))
        return []

    def _promote_crash_marker(self):
        """A marker left on disk means Revit died between our write and the
        export call returning — i.e. that exact sheet+format is fatal. Record it
        in the durable history, clear the in-flight marker, and return it so the
        UI can tell the user what happened."""
        pc = self._read_crash_marker()
        if not pc:
            return None
        try:
            # safe_level is part of the key: the same sheet crashing again at a
            # STRONGER mitigation is new information, and dropping it would
            # freeze the ladder and make us retry a proven-failed rung forever.
            key = (pc.get('doc'), pc.get('sheet'), pc.get('format'), pc.get('safe_level'))
            known = set((e.get('doc'), e.get('sheet'), e.get('format'), e.get('safe_level'))
                        for e in self._crash_history)
            if key not in known:
                self._crash_history.append(pc)
                self._write_crash_history()
        except Exception as ex:
            logger.debug("Could not promote crash marker: {}".format(ex))
        self._clear_crash_marker()
        return pc

    def _write_crash_history(self):
        """Persist the confirmed-fatal list (last 50 entries)."""
        try:
            if not os.path.exists(self.profiles_folder):
                os.makedirs(self.profiles_folder)
            with open(self._crash_history_file, 'w') as f:
                json.dump(self._crash_history[-50:], f, indent=2)
        except Exception as ex:
            logger.debug("Could not write crash history: {}".format(ex))

    def _write_crash_marker(self, sheet_number, fmt, level=None, safe_level=None):
        """Persist which sheet+format is being handed to Revit's native exporter
        RIGHT NOW, and which safe-mode rung (if any) was applied. Flushed +
        fsync'd so it is durable even if the process is killed a moment later.
        `safe_level` is what lets the next session escalate instead of repeating
        a mitigation that has already been proven to still crash."""
        try:
            if not os.path.exists(self.profiles_folder):
                os.makedirs(self.profiles_folder)
            data = {
                'sheet': sheet_number,
                'format': fmt,
                'raster': level,
                'safe_level': safe_level,
                # Rung NAME as well as its index: the index is only meaningful
                # against the SAFE_LADDER of the build that wrote it, and this
                # file is read by a later session (and by humans debugging a
                # crash journal).
                'safe_rung': (self.SAFE_LADDER[safe_level]
                              if safe_level is not None
                              and 0 <= safe_level < len(self.SAFE_LADDER) else None),
                'doc': self.doc.Title,
                'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self._crash_marker_file, 'w') as f:
                json.dump(data, f)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
        except Exception as ex:
            logger.debug("Could not write crash marker: {}".format(ex))

    def _clear_crash_marker(self):
        """Remove the in-flight marker once the native export call has returned
        (i.e. Revit survived this sheet)."""
        try:
            if os.path.exists(self._crash_marker_file):
                os.remove(self._crash_marker_file)
        except Exception as ex:
            logger.debug("Could not clear crash marker: {}".format(ex))

    def _is_known_fatal(self, sheet_number, fmt):
        """True if this document+sheet+format has already hard-crashed Revit.

        Such a combination is still exported — it is queued last, safe mode is
        offered for it, and the user is warned up front — because a file that
        goes missing without a word is worse than a crash they chose to risk.
        Skipping only happens if the user explicitly asks for it in that prompt.
        """
        try:
            title = self.doc.Title
            for entry in self._crash_history:
                if (entry.get('sheet') == sheet_number
                        and entry.get('format') == fmt
                        and (not entry.get('doc') or entry.get('doc') == title)):
                    return True
        except Exception:
            pass
        return False

    # ── Safe-mode export ──────────────────────────────────────────────────
    # A sheet that crashes Revit's faceter is not hopeless: the fatal geometry
    # is usually one or two elements. Safe mode temporarily neutralises them,
    # exports, then rolls the change back so the model is byte-identical
    # afterwards. Preferred over skipping because it still produces the file.

    BAD_GEOMETRY_FILE = os.path.join(
        os.path.expanduser('~'), 'Documents', 'T3Lab_Diagnostics', '_bad_geometry.json')

    def _bad_geometry_ids(self):
        """ElementIds that the Bad Geometry check flagged for THIS model.
        Empty list if the check has not been run (safe mode then falls back to
        the blunt lever, Coarse detail level)."""
        try:
            if not os.path.exists(self.BAD_GEOMETRY_FILE):
                return []
            with open(self.BAD_GEOMETRY_FILE, 'r') as f:
                data = json.load(f)
            if data.get('doc') and data.get('doc') != self.doc.Title:
                return []
            return [make_eid(e['id']) for e in data.get('elements', []) if 'id' in e]
        except Exception as ex:
            logger.debug("Could not read bad-geometry list: {}".format(ex))
            return []

    # Categories that carry the curved, spline-driven solids behind the
    # "Singular point in SilSolver::eval ... Hermite surface" crashes on the
    # SPH bath sheets. Used by the 'categories' rung when the Bad Geometry
    # check has not pinned down individual elements — and it is the only
    # mitigation short of dropping the whole link that reaches geometry living
    # INSIDE a link, since linked element ids cannot be passed to HideElements.
    SUSPECT_CATEGORIES = (
        DB.BuiltInCategory.OST_PlumbingFixtures,
        DB.BuiltInCategory.OST_SpecialityEquipment,
        DB.BuiltInCategory.OST_GenericModel,
        DB.BuiltInCategory.OST_Casework,
        DB.BuiltInCategory.OST_Furniture,
    )

    # Mitigations applied cumulatively, ordered by HOW MUCH OF THE DRAWING THEY
    # DESTROY — least first. We only climb when the rung below has been PROVEN
    # to still crash (recorded in the crash history), and every failed rung
    # costs the user a whole Revit session plus a cloud-model reopen, so the
    # order is chosen to reach a working export in as few attempts as possible.
    #
    # 'wireframe' sits second, not last: it hides NOTHING (every line still
    # reaches the sheet, it only loses the hidden-line cleanup) and it is the
    # rung most likely to work, because every fatal signature in the journals —
    # facetFaceWithSpecifiedAlgorithm "long, thin face",  Silhouette moved
    # outside face envelope, Cannot compute silhouette edges, SilSolver::eval —
    # comes from hidden-line removal and face tessellation, which the Wireframe
    # display style does not perform. Putting the category/link hides ahead of
    # it meant an interior elevation (30-Jul crash: SPH ELEVATIONS 3, 4 —
    # LIVING HALL / DRY KITCHEN) would have exported with its Furniture,
    # Casework and Plumbing Fixtures stripped out — i.e. an empty drawing that
    # still looks like a successful export.
    #
    # NOTE: crash history stores this as an INDEX (`safe_level`), so reordering
    # invalidates previously recorded levels. Safe to do here — no entry in the
    # history carries a non-null safe_level yet. Markers also record the rung
    # NAME (`safe_rung`) from now on so the record stays readable if it moves.
    SAFE_LADDER = ('elements', 'wireframe', 'coarse', 'categories', 'links')

    def _next_safe_level(self, sheet_number, fmt):
        """Rung to start on for this sheet+format: one above the highest rung
        that has already crashed. 0 when nothing has been tried yet."""
        highest = -1
        try:
            title = self.doc.Title
            for entry in self._crash_history:
                if (entry.get('sheet') == sheet_number
                        and entry.get('format') == fmt
                        and (not entry.get('doc') or entry.get('doc') == title)):
                    recorded = entry.get('safe_level')
                    if recorded is not None and recorded > highest:
                        highest = recorded
        except Exception:
            pass
        return highest + 1

    def _safe_ladder_exhausted(self, sheet_number, fmt):
        """True once every rung has crashed — nothing left to try automatically."""
        return self._next_safe_level(sheet_number, fmt) >= len(self.SAFE_LADDER)

    def _begin_safe_mode(self, sheet, level, fmt=None):
        """Open a TransactionGroup, apply every mitigation up to and including
        `level` on the SAFE_LADDER, and return (group, level_used) so the caller
        can roll it back. Returns (None, level) if nothing could be changed.

        The ladder escalates because a weaker rung has already been proven not
        to work: 24-Jul journal.1084 shows Coarse detail alone still crashed
        sheet 1402, with the same four section views regenerating beforehand.
        Each crash records the rung it died on, so the next session starts one
        rung stronger instead of repeating a known-failed attempt."""
        views = []
        try:
            for vid in sheet.GetAllPlacedViews():
                view = self.doc.GetElement(vid)
                if view is not None and not view.IsTemplate:
                    views.append(view)
        except Exception as ex:
            logger.error("Could not read placed views: {}".format(ex))
        if not views:
            return None, level

        group = None
        try:
            group = DB.TransactionGroup(self.doc, "BatchOut safe-mode export")
            group.Start()
            trans = Transaction(self.doc, "Simplify views for safe export")
            trans.Start()

            applied = []
            # Escalate inside this call too: a rung that changes nothing (e.g.
            # 'elements' with no geometry report yet) must not waste a whole
            # Revit session, so keep climbing until something actually applies.
            top = min(level, len(self.SAFE_LADDER) - 1)
            rung = 0
            while rung <= top or not applied:
                if rung >= len(self.SAFE_LADDER):
                    break
                changed = self._apply_safe_rung(self.SAFE_LADDER[rung], views)
                if changed:
                    applied.append("{}={}".format(self.SAFE_LADDER[rung], changed))
                rung += 1

            trans.Commit()
            level_used = max(0, rung - 1)

            if not applied:
                group.RollBack()
                logger.warning("Safe mode on '{}': nothing could be changed.".format(
                    sheet.SheetNumber))
                return None, level_used

            logger.warning("Safe mode L{} on '{}': {} (rolled back after export).".format(
                level_used, sheet.SheetNumber, ", ".join(applied)))
            # Surface it in the completion dialog too: a safe-mode export can be
            # missing content (categories/links rungs) or missing its hidden-line
            # cleanup (wireframe), and a log line nobody opens is not a warning.
            self._note_safe_mode(sheet.SheetNumber, fmt,
                                 self.SAFE_LADDER[level_used], applied)
            return group, level_used
        except Exception as ex:
            logger.error("Could not enter safe mode: {}".format(ex))
            if group is not None:
                try:
                    group.RollBack()
                except Exception:
                    pass
            return None, level

    def _apply_safe_rung(self, rung, views):
        """Apply one mitigation across `views`. Returns how many changes landed.
        Must be called inside an open Transaction."""
        changed = 0

        if rung == 'elements':
            # Surgical: hide only what the Bad Geometry check actually named.
            bad_ids = self._bad_geometry_ids()
            if not bad_ids:
                return 0
            for view in views:
                hideable = List[DB.ElementId]()
                for eid in bad_ids:
                    try:
                        element = self.doc.GetElement(eid)
                        if element is None or element.IsHidden(view):
                            continue
                        if not element.CanBeHidden(view):
                            continue
                        hideable.Add(eid)
                    except Exception:
                        continue
                if hideable.Count:
                    try:
                        view.HideElements(hideable)
                        changed += hideable.Count
                    except Exception as ex:
                        logger.debug("HideElements failed on {}: {}".format(view.Id, ex))

        elif rung == 'categories':
            # Category-level hide. Two reasons this rung exists between the
            # surgical and the blunt ones:
            #   * it reaches geometry inside a RevitLink — linked element ids
            #     belong to another document and cannot be passed to
            #     HideElements, but category visibility does propagate;
            #   * it still works when the Bad Geometry check has never been run.
            # Prefer the categories the check actually named; fall back to the
            # known-suspect list only when it has nothing to say.
            cat_ids = self._bad_geometry_category_ids()
            if not cat_ids:
                cat_ids = []
                for bic in self.SUSPECT_CATEGORIES:
                    try:
                        category = DB.Category.GetCategory(self.doc, bic)
                        if category is not None:
                            cat_ids.append(category.Id)
                    except Exception:
                        continue
            for view in views:
                for cat_id in cat_ids:
                    try:
                        if not view.CanCategoryBeHidden(cat_id):
                            continue
                        if view.GetCategoryHidden(cat_id):
                            continue
                        view.SetCategoryHidden(cat_id, True)
                        changed += 1
                    except Exception as ex:
                        logger.debug("SetCategoryHidden failed on {}: {}".format(
                            view.Id, ex))

        elif rung == 'links':
            # Every crash journal shows RVT-link graphics regenerating right
            # before death, so drop the links out of the render entirely.
            for view in views:
                hideable = List[DB.ElementId]()
                try:
                    links = FilteredElementCollector(self.doc, view.Id) \
                        .OfClass(DB.RevitLinkInstance) \
                        .ToElements()
                except Exception:
                    continue
                for link in links:
                    try:
                        if not link.IsHidden(view) and link.CanBeHidden(view):
                            hideable.Add(link.Id)
                    except Exception:
                        continue
                if hideable.Count:
                    try:
                        view.HideElements(hideable)
                        changed += hideable.Count
                    except Exception as ex:
                        logger.debug("Hiding links failed on {}: {}".format(view.Id, ex))

        elif rung == 'coarse':
            # Blunt: families usually swap to a simpler solid at Coarse. Proven
            # insufficient on its own for sheet 1402, kept as a cumulative rung.
            for view in views:
                try:
                    if view.DetailLevel != DB.ViewDetailLevel.Coarse:
                        view.DetailLevel = DB.ViewDetailLevel.Coarse
                        changed += 1
                except Exception as ex:
                    logger.debug("DetailLevel not settable on {}: {}".format(view.Id, ex))

        elif rung == 'wireframe':
            # The rung that targets the crash most directly: Wireframe skips
            # hidden-line removal and face tessellation, which is where every
            # fatal signature in the journals originates. Unlike the rungs below
            # it this hides NOTHING — the drawing loses its hidden-line cleanup,
            # not its content — which is why it is tried before them.
            for view in views:
                try:
                    if view.DisplayStyle != DB.DisplayStyle.Wireframe:
                        view.DisplayStyle = DB.DisplayStyle.Wireframe
                        changed += 1
                except Exception as ex:
                    logger.debug("DisplayStyle not settable on {}: {}".format(view.Id, ex))

        return changed

    def _bad_geometry_category_ids(self):
        """Category ids of the elements the Bad Geometry check flagged for THIS
        model. Lets the 'categories' rung hide exactly the categories that are
        actually implicated rather than the generic suspect list."""
        ids = []
        try:
            if not os.path.exists(self.BAD_GEOMETRY_FILE):
                return ids
            with open(self.BAD_GEOMETRY_FILE, 'r') as f:
                data = json.load(f)
            if data.get('doc') and data.get('doc') != self.doc.Title:
                return ids
            seen = set()
            for entry in data.get('elements', []):
                eid = entry.get('id')
                if eid is None:
                    continue
                try:
                    element = self.doc.GetElement(make_eid(eid))
                    if element is None or element.Category is None:
                        continue
                    cat_id = element.Category.Id
                    key = str(cat_id)
                    if key not in seen:
                        seen.add(key)
                        ids.append(cat_id)
                except Exception:
                    continue
        except Exception as ex:
            logger.debug("Could not read bad-geometry categories: {}".format(ex))
        return ids

    def _end_safe_mode(self, group):
        """Always roll back — safe mode must never leave the model modified."""
        if group is None:
            return
        try:
            group.RollBack()
        except Exception as ex:
            logger.error("Safe-mode rollback FAILED: {} — undo manually".format(ex))

    def _format_checkboxes(self):
        """(format, checkbox) pairs for the formats crash history tracks."""
        return (("DWG", self.export_dwg), ("PDF", self.export_pdf),
                ("DGN", self.export_dgn), ("DWF", self.export_dwf),
                ("NWC", self.export_nwd), ("IMG", self.export_img))

    def _is_risky_item(self, item):
        """True if this item hard-crashed Revit before in any checked format."""
        label = getattr(item, 'SheetNumber', None)
        if not label:
            return False
        for fmt, box in self._format_checkboxes():
            try:
                if box.IsChecked and self._is_known_fatal(label, fmt):
                    return True
            except Exception:
                continue
        return False

    def _order_risky_last(self, items):
        """Move items with a crash history to the END of the queue.

        They are still exported — nothing is dropped by default — but if Revit
        does die on one, every other selected sheet/view is already on disk.
        """
        safe, risky = [], []
        for item in items:
            (risky if self._is_risky_item(item) else safe).append(item)
        if risky:
            logger.warning("{} item(s) with a crash history queued last: {}".format(
                len(risky), ", ".join(str(getattr(i, 'SheetNumber', '?')) for i in risky)))
        return safe + risky

    def _ask_safe_mode(self, selected_items):
        """If any selected item+format is known-fatal, ask ONCE up front how to
        handle it. Both export choices export everything; only an explicit
        "Skip them" leaves files out, and skipped items are then listed in the
        completion dialog."""
        self._safe_mode = False
        self._skip_fatal = False
        RUNG_LABEL = {
            'elements':   "hide the exact elements the Bad Geometry check flagged",
            'wireframe':  "switch those views to Wireframe — hides NOTHING, but "
                          "skips the hidden-line pass that crashes",
            'coarse':     "drop those views to Coarse detail level",
            'categories': "hide the categories carrying the bad geometry "
                          "(also reaches geometry inside links) — the drawing "
                          "WILL lose that content",
            'links':      "hide the RVT links in those views",
        }

        recoverable, exhausted, no_safe_mode = [], [], []
        for item in selected_items:
            number = getattr(item, 'SheetNumber', None)
            if not number:
                continue
            for fmt, box in self._format_checkboxes():
                try:
                    if not box.IsChecked or not self._is_known_fatal(number, fmt):
                        continue
                except Exception:
                    continue
                if fmt not in ("DWG", "PDF"):
                    # Safe mode is only wired into these two loops.
                    no_safe_mode.append("{} ({})".format(number, fmt))
                elif self._safe_ladder_exhausted(number, fmt):
                    exhausted.append("{} ({})".format(number, fmt))
                else:
                    rung = self.SAFE_LADDER[self._next_safe_level(number, fmt)]
                    recoverable.append("{} ({}) -> {}".format(
                        number, fmt, RUNG_LABEL.get(rung, rung)))

        if not (recoverable or exhausted or no_safe_mode):
            return

        message = "These crashed Revit's exporter on an earlier run:\n  {}".format(
            "\n  ".join(recoverable + exhausted + no_safe_mode))
        message += ("\n\nThey are queued LAST, so everything else is already on "
                    "disk before Revit gets to them.")
        if exhausted or no_safe_mode:
            message += ("\n\nNo mitigation left for:\n  {}\n  (these go out at "
                        "full fidelity — the geometry itself has to be fixed)".format(
                            "\n  ".join(exhausted + no_safe_mode)))
        if recoverable:
            message += ("\n\nSafe mode applies the change above, exports, then "
                        "rolls it back so your model is left untouched. Each rung "
                        "is tried once; if it still crashes the next run "
                        "escalates automatically.")
        try:
            if self.export_pdf.IsChecked and self.combine_pdf.IsChecked:
                message += ("\n\nNote: Combine PDF puts every sheet in ONE native "
                            "call, so a crash there loses the whole combined file. "
                            "Uncheck Combine to export these individually.")
        except Exception:
            pass

        SAFE_OPT = "Export them with safe mode (recommended)"
        FULL_OPT = "Export them at full fidelity"
        SKIP_OPT = "Skip them (files will be missing)"
        options = ([SAFE_OPT] if recoverable else []) + [FULL_OPT, SKIP_OPT]

        choice = forms.alert(message, title="Items that crashed Revit before",
                             options=options)
        if choice == SKIP_OPT:
            self._skip_fatal = True
        elif choice == FULL_OPT:
            self._safe_mode = False
        else:
            # SAFE_OPT, or the dialog was dismissed (returns False): default to
            # the safest choice that still exports everything.
            self._safe_mode = bool(recoverable)

    # Rungs that REMOVE content from the drawing. 'wireframe' and 'coarse' are
    # deliberately not in here: they change how the view is drawn, not what it
    # contains, so they do not need the stronger "check this file" warning.
    LOSSY_RUNGS = ('categories', 'links')

    def _note_safe_mode(self, sheet_number, fmt, rung, applied):
        """Record that a sheet was exported under safe mode, for the completion
        dialog. Every one of these files differs from a normal export."""
        label = "{}{} -> {}".format(
            sheet_number, " ({})".format(fmt) if fmt else "", ", ".join(applied))
        if rung in self.LOSSY_RUNGS:
            label += "   [CONTENT HIDDEN - check this file]"
        if label not in self._safe_applied:
            self._safe_applied.append(label)

    def _note_skipped_fatal(self, sheet_number, fmt):
        """Record + surface a sheet skipped because it previously killed Revit."""
        label = "{} ({})".format(sheet_number, fmt)
        if label not in self._skipped_fatal:
            self._skipped_fatal.append(label)
        msg = "Skipped {} — this sheet crashed Revit's {} exporter before.".format(
            sheet_number, fmt)
        logger.warning(msg)
        self._update_progress(
            int(self._overall_counter * 100.0 / max(1, self._overall_total)), msg)
        try:
            self.update_export_item_progress(sheet_number, fmt, 0, "Skipped (crashed before)")
        except Exception:
            pass

    def _raster_level_for(self, sheet_number):
        """Starting rung on RASTER_LADDER for this sheet. Always None
        (= Presentation, full quality): the crash this used to work around also
        happens in the DWG exporter, where there is no raster setting at all, so
        stepping raster down does not prevent it. Kept as the single place to
        hook per-sheet quality if a future case does respond to it."""
        return None

    def _warn_if_previous_crash(self):
        """If a prior export died mid-sheet, tell the user which sheet+format and
        what we will do about it on this run."""
        pc = self._pending_crash
        if not pc:
            return
        try:
            sheet = pc.get('sheet', '?')
            fmt = pc.get('format', '?')
            same_doc = (not pc.get('doc')) or pc.get('doc') == self.doc.Title
            if same_doc:
                msg = ("Revit crashed last time on sheet '{}' during {} export. "
                       "It will be exported LAST this run — you'll be asked "
                       "whether to use safe mode.").format(sheet, fmt)
            else:
                msg = "Previous export crashed on sheet '{}' ({}, a different model).".format(
                    sheet, fmt)
            # Surfaced in the status bar only — a logger.warning here forces the
            # pyRevit output window open every time the tool starts.
            logger.debug(msg)
            try:
                self.status_text.Text = msg
            except Exception:
                pass
        except Exception as ex:
            logger.debug("Could not surface previous crash: {}".format(ex))

    # ── Nothing exports silently ───────────────────────────────────────────
    # Two independent ways an item used to vanish without a word:
    #   1. filename collision — Revit allows a Floor Plan and a Ceiling Plan to
    #      both be named "Level 1", and a view-based naming pattern collapses
    #      them onto one file, so the second export overwrote the first.
    #   2. the verification check missing the file Revit actually wrote, which
    #      only dropped the success count — but the same branch is now the one
    #      that declares a failure, so it must not produce false negatives.

    def _reset_run_state(self):
        """Clear per-run bookkeeping (filenames used, failures recorded)."""
        self._used_filenames = set()
        self._failed_items = []
        self._verify_misses = 0

    def _native_output_stamp(self, path):
        """Snapshot one nonempty expected file, never a neighbouring export."""
        try:
            stat = os.stat(path)
            if os.path.isfile(path) and stat.st_size > 0:
                return stat.st_size, stat.st_mtime_ns
        except OSError:
            pass
        return None

    def _confirm_native_output(self, path, before):
        previous_mtime = before[1] / 1000000000.0 if before is not None else None
        self._wait_for_export_file(path, previous_mtime, 3.0)
        after = self._native_output_stamp(path)
        return after is not None and after != before

    def _reserve_filename(self, folder, base, ext):
        """Return a filename in `folder` that no earlier item in this run has
        already claimed, suffixing " (2)", " (3)", … on collision."""
        if not hasattr(self, '_used_filenames'):
            self._used_filenames = set()
        base = base.strip() or "Unnamed"
        candidate = base
        counter = 1
        while True:
            key = os.path.join(folder, candidate + ext).lower()
            if key not in self._used_filenames:
                self._used_filenames.add(key)
                if candidate != base:
                    logger.warning(
                        "Filename collision: '{}{}' was already written in this "
                        "run — this item goes to '{}{}' instead.".format(
                            base, ext, candidate, ext))
                return candidate
            counter += 1
            candidate = "{} ({})".format(base, counter)

    def _verify_export(self, expected_file, prev_mtime, started_at, ext):
        """True when the export produced a file.

        Falls back to looking for a file this run did not reserve that was
        written after `started_at` — some Revit exporters decorate the name
        themselves, and reporting a successful export as missing would be as
        misleading as losing it.

        Export() is synchronous, so the file is normally on disk before the
        first check and the wait only covers sync lag. Once a couple of items in
        a row have produced nothing, the wait is cut short: a format that is
        failing across the board would otherwise add minutes of dead polling
        over a large selection.
        """
        timeout = 0.5 if getattr(self, '_verify_misses', 0) >= 2 else 3.0
        if self._wait_for_export_file(expected_file, prev_mtime, timeout):
            self._verify_misses = 0
            return True
        try:
            import glob as _glob
            folder = os.path.dirname(expected_file)
            used = getattr(self, '_used_filenames', set())
            self._used_filenames = used
            for path in _glob.glob(os.path.join(folder, "*" + ext)):
                if path.lower() == expected_file.lower() or path.lower() in used:
                    continue  # belongs to another item of this run
                try:
                    if (os.path.isfile(path) and os.path.getsize(path) > 0
                            and os.path.getmtime(path) >= started_at):
                        logger.warning("'{}' landed as '{}' (Revit renamed it).".format(
                            os.path.basename(expected_file), os.path.basename(path)))
                        self._verify_misses = 0
                        used.add(path.lower())
                        return True
                except Exception:
                    continue
        except Exception as ex:
            logger.debug("Export verification fallback failed: {}".format(ex))
        self._verify_misses = getattr(self, '_verify_misses', 0) + 1
        return False

    def _note_failure(self, label, fmt, reason):
        """Record + surface an item that produced no file."""
        reason = str(reason)
        if len(reason) > 160:
            reason = reason[:157] + "..."
        entry = "{} ({}) — {}".format(label, fmt, reason)
        if entry not in self._failed_items:
            self._failed_items.append(entry)
        logger.error("NOT exported: {}".format(entry))
        try:
            self.update_export_item_progress(label, fmt, 0, "Failed")
        except Exception:
            pass

    def _sync_item_labels(self, items, fmt):
        """Refresh each row's cached sheet number / view name from Revit.

        Per-item try/except on purpose: this used to be one bare loop around the
        whole list, so a single unreadable row (deleted element, closed workset)
        raised out of the export function and every other selected item was
        dropped with nothing but a log line.
        """
        for item in items:
            try:
                if hasattr(item, 'Sheet'):
                    item.SheetNumber = item.Sheet.SheetNumber
                    item.SheetName = item.Sheet.Name
                elif hasattr(item, 'View'):
                    item.SheetNumber = item.View.Name
                    item.ViewName = item.View.Name
            except Exception as ex:
                self._note_failure(str(getattr(item, 'SheetNumber', 'Unknown')),
                                   fmt, "could not be read from the model: {}".format(ex))

    def _element_of(self, item):
        """(element, display_name) for a SheetItem or ViewItem; (None, label) if
        the row is neither — which is itself reported, never skipped quietly."""
        if hasattr(item, 'Sheet'):
            return item.Sheet, item.Sheet.SheetNumber
        if hasattr(item, 'View'):
            return item.View, item.View.Name
        return None, str(getattr(item, 'SheetNumber', 'Unknown'))

    def _wait_for_export_file(self, path, prev_mtime, timeout=8.0):
        """Poll for a freshly-written export file instead of globbing the whole
        output folder and sleeping a fixed time on every sheet. Returns True once
        `path` exists with an mtime newer than prev_mtime (or brand new). Revit's
        Export() is synchronous, so the file is normally already there on the
        first check — near-zero wait, versus the old fixed 0.3s sleep + two
        folder-wide globs (very slow on OneDrive-synced output folders)."""
        import time as _t
        deadline = _t.monotonic() + max(0.0, timeout)
        while True:
            try:
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    if prev_mtime is None or os.path.getmtime(path) > prev_mtime:
                        return True
            except Exception:
                pass
            remaining = deadline - _t.monotonic()
            if remaining <= 0:
                return False
            _t.sleep(min(0.1, remaining))

    def load_sheet_sets_for_filter(self):
        """Populate the multi-select sheet set dropdown with CheckBoxes."""
        try:
            from System.Windows.Controls import CheckBox
            from System.Windows import Thickness

            self.sheet_set_checklist.Children.Clear()

            # Get the T3.CheckBox style from the window resources
            try:
                t3_cb_style = self.FindResource("T3.CheckBox")
            except Exception:
                t3_cb_style = None

            # "All Sheets/Views" checkbox — checked by default
            all_cb = CheckBox()
            all_cb.Content = "All Sheets/Views"
            all_cb.IsChecked = True
            all_cb.Tag = None
            all_cb.Margin = Thickness(4, 3, 4, 3)
            if t3_cb_style:
                all_cb.Style = t3_cb_style
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
                    if t3_cb_style:
                        cb.Style = t3_cb_style
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
        """Queue the sheet-set filter — reads ViewSheetSets, needs API context."""
        self._run_in_api_context(self._apply_sheet_set_filter_api)

    def _apply_sheet_set_filter_api(self):
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

    def _on_item_property_changed(self, sender, args):
        """React to any programmatic or binding changes to IsSelected on items."""
        if getattr(self, '_batch_updating', False):
            return
        if args and getattr(args, 'PropertyName', None) == "IsSelected":
            self.update_selection_count()

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
            self.all_sheets = []
            for sheet in sheets:
                item = SheetItem(sheet, False, lazy=True)
                item.add_PropertyChanged(self._on_item_property_changed)
                self.all_sheets.append(item)
            self.filtered_sheets = list(self.all_sheets)

            # Show sheet names immediately
            self.update_sheets_list()
            self.status_text.Text = "Loaded {} sheets | Revit {}".format(
                len(self.all_sheets), REVIT_VERSION)

            # Phase 2: schedule chunked loading through the ExternalEvent queue.
            # NOT Dispatcher.BeginInvoke — with a modeless window those callbacks
            # would read the Revit API outside API context and crash Revit.
            self._lazy_load_index = 0
            self._run_in_api_context(self._lazy_load_chunk)

        except Exception as ex:
            logger.error("Error loading sheets: {}".format(ex))
            forms.alert("Error loading sheets: {}".format(ex), exitscript=True)

    def _lazy_load_init(self):
        """Kick off chunked Revision loading — no titleblock queries at startup."""
        try:
            self._lazy_load_index = 0
            self._run_in_api_context(self._lazy_load_chunk)
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

            # Next batch via ExternalEvent — keeps every API read in context
            self._run_in_api_context(self._lazy_load_chunk)

        except Exception as ex:
            logger.debug("Error loading sheet chunk: {}".format(ex))

    def _ensure_titleblock_cache(self):
        """Populate titleblock size cache on first call (deferred from startup)."""
        if not getattr(self, '_titleblock_cache_loaded', False):
            self._batch_load_titleblock_sizes()

    def _load_extra_sheet_data(self):
        """Legacy helper kept for compatibility – now delegates to chunked loader."""
        self._lazy_load_init()

    def load_views(self):
        """Load all views — Phase 1: instant display, Phase 2: chunked lazy loading.

        Collected with OfClass(View), NOT OfCategory(OST_Views): schedules sit
        under OST_Schedules and several view kinds carry no category at all, so
        the category filter hid them. The old isinstance whitelist
        (ViewPlan/ViewSection/View3D/ViewSchedule/ViewDrafting) additionally
        dropped every Legend view — they are plain `View` instances — even though
        the Views filter dropdown offers a "Legend" entry. Both made views
        silently un-exportable because they never appeared in the list.
        """
        try:
            views_collector = FilteredElementCollector(self.doc)\
                .OfClass(View)\
                .WhereElementIsNotElementType()

            views = []
            excluded = 0
            for v in views_collector:
                try:
                    if v.IsTemplate:
                        continue
                    if isinstance(v, ViewSheet):
                        continue  # sheets are handled by the Sheets mode
                    if v.ViewType in NON_EXPORTABLE_VIEW_TYPES:
                        excluded += 1
                        continue
                    # A revision schedule belongs to a titleblock, not to the
                    # browser — it is not a standalone exportable view.
                    if isinstance(v, ViewSchedule) and \
                            getattr(v, 'IsTitleblockRevisionSchedule', False):
                        excluded += 1
                        continue
                    views.append(v)
                except Exception as view_ex:
                    excluded += 1
                    logger.debug("Skipping unreadable view: {}".format(view_ex))

            views.sort(key=lambda x: x.Name)
            if excluded:
                logger.debug("Views list: {} exportable, {} internal/system views "
                             "excluded".format(len(views), excluded))

            # lazy=True: skip Phase/ViewTemplate lookups for instant display
            self.all_views = []
            for view in views:
                item = ViewItem(view, False, lazy=True)
                item.add_PropertyChanged(self._on_item_property_changed)
                self.all_views.append(item)
            self.filtered_views = list(self.all_views)

            self.update_items_list()
            self.status_text.Text = "Loaded {} views | Revit {}".format(
                len(self.all_views), REVIT_VERSION)

            # Phase 2: chunked Phase/ViewTemplate loading via ExternalEvent queue
            self._lazy_view_load_index = 0
            self._run_in_api_context(self._lazy_view_load_chunk)

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

            # Next batch via ExternalEvent — keeps every API read in context
            self._run_in_api_context(self._lazy_view_load_chunk)

        except Exception as ex:
            logger.debug("Error loading view chunk: {}".format(ex))

    def update_sheets_list(self):
        """Update the sheets ListView."""
        self.sheets_listview.ItemsSource = None
        self.sheets_listview.ItemsSource = to_items_source(self.filtered_sheets)

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
        self.sheets_listview.ItemsSource = to_items_source(self.filtered_views)

    def update_selection_count(self):
        """Update the selection count status bar and header checkbox."""
        try:
            if getattr(self, '_batch_updating', False):
                return

            items = self.filtered_sheets if self.selection_mode == "sheets" else self.filtered_views
            total_count = len(items)
            selected_count = sum(1 for s in items if s.IsSelected)

            if hasattr(self, 'selection_count_text'):
                if self.selection_mode == "sheets":
                    self.selection_count_text.Text = "{} sheets and 0 views selected. Total: {}".format(
                        selected_count, total_count)
                else:
                    self.selection_count_text.Text = "0 sheets and {} views selected. Total: {}".format(
                        selected_count, total_count)

            # Sync header checkbox (True = all, False = none, None = indeterminate)
            if hasattr(self, 'header_checkbox') and self.header_checkbox:
                if total_count == 0 or selected_count == 0:
                    self.header_checkbox.IsChecked = False
                elif selected_count == total_count:
                    self.header_checkbox.IsChecked = True
                else:
                    self.header_checkbox.IsChecked = None

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
                    # First load queries the document → run in API context
                    self._run_in_api_context(self.load_views)
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
        """Handle click on ListView — only blocks TextBox clicks from toggling the row.

        Selection sync is now handled automatically by the XAML TwoWay binding
        (ListViewItem.IsSelected ↔ SheetItem.IsSelected) and the
        sheets_selection_changed event handler.
        """
        try:
            from System.Windows.Controls import TextBox
            from System.Windows.Media import VisualTreeHelper

            # Check if the click was on or inside a TextBox
            # If so, don't let it bubble up to the ListViewItem selection
            cur = e.OriginalSource
            while cur is not None:
                if isinstance(cur, TextBox):
                    # Click was in a textbox, stop here to prevent row toggle
                    e.Handled = True
                    # Still let the TextBox get focus
                    sender_tb = cur
                    sender_tb.Focus()
                    return
                try:
                    cur = VisualTreeHelper.GetParent(cur)
                except Exception:
                    break
        except Exception as ex:
            logger.debug("Error handling listview click: {}".format(ex))

    def sheets_selection_changed(self, sender, e):
        """React to WPF native selection changes (click, Ctrl+click, Shift+click, arrow keys).

        Because ListViewItem.IsSelected is TwoWay-bound to SheetItem.IsSelected,
        the data model is already updated when this fires. We just need to
        refresh the count display and export preview.
        """
        try:
            if getattr(self, '_batch_updating', False):
                return
            self.update_selection_count()
            self.update_export_preview_if_needed()
        except Exception as ex:
            logger.debug("Error handling sheets selection changed: {}".format(ex))

    def row_checkbox_clicked(self, sender, e):
        """Handle direct click on row CheckBox."""
        try:
            # Synchronously update data_item.IsSelected to match CheckBox.IsChecked
            # to eliminate WPF data binding latency from causing the selected count to lag by 1
            if hasattr(sender, 'DataContext') and sender.DataContext is not None:
                sender.DataContext.IsSelected = bool(sender.IsChecked)

            # Refresh the ListView so ListViewItem.IsSelected binding picks up
            # the new SheetItem.IsSelected value (highlight stays in sync).
            # Required because SheetItem is a plain Python object without CLR
            # INotifyPropertyChanged, so TwoWay binding only auto-pushes UI→model.
            self.sheets_listview.Items.Refresh()

            self.update_selection_count()
            self.update_export_preview_if_needed()
        except Exception as ex:
            logger.debug("Error handling row checkbox click: {}".format(ex))

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
        """Resize Sheet Name and Custom Filename columns to fill available ListView width with ZERO gaps."""
        try:
            col_num_w = int(self.col_number.Width) if hasattr(self, 'col_number') and self.col_number.Width > 0 else 220
            # Fixed columns: checkbox (40) + number (col_num_w) + rev (70) + size (70) + orientation (90) + scrollbar/borders (8)
            fixed = 40 + col_num_w + 70 + 70 + 90 + 8
            available = sender.ActualWidth - fixed
            if available <= 0:
                return
            col_name_width = int(available * 0.38)
            self.col_name.Width = col_name_width
            # The last column takes ALL remaining width so there is zero gap to the right edge
            self.col_custom_filename.Width = max(100, int(available - col_name_width))
        except Exception:
            pass

    def window_content_rendered(self, sender, e):
        """Force one column-fill pass once the window has its final rendered size.

        The Selection tab's ListView populates its ItemsSource during __init__,
        before the window is shown, so the first SizeChanged on sheets_listview
        can fire with a stale/intermediate width and leave a gap between the
        last column and the scrollbar. Re-run the same fill logic now that
        ContentRendered guarantees layout is final.
        """
        try:
            self.on_listview_size_changed(self.sheets_listview, None)
        except Exception:
            pass

    def header_checkbox_clicked(self, sender, e):
        """Handle header checkbox click to select/deselect all items."""
        try:
            items = self.filtered_sheets if self.selection_mode == "sheets" else self.filtered_views
            if not items:
                return

            all_selected = all(item.IsSelected for item in items)
            target_state = not all_selected

            self._batch_updating = True
            try:
                for item in items:
                    item.IsSelected = target_state
            finally:
                self._batch_updating = False

            self.sheets_listview.Items.Refresh()

            if self.selection_mode == "sheets":
                if target_state:
                    self.status_text.Text = "Selected {} sheets".format(len(items))
                else:
                    self.status_text.Text = "Deselected all sheets"
            else:
                if target_state:
                    self.status_text.Text = "Selected {} views".format(len(items))
                else:
                    self.status_text.Text = "Deselected all views"

            # Update selection count
            self.update_selection_count()
            # Update export preview if on Queue tab
            self.update_export_preview_if_needed()
        except Exception as ex:
            logger.debug("Error in header_checkbox_clicked: {}".format(ex))

    def select_all_sheets(self, sender, e):
        """Select all items (sheets or views)."""
        items = self.filtered_sheets if self.selection_mode == "sheets" else self.filtered_views
        self._batch_updating = True
        try:
            for item in items:
                item.IsSelected = True
        finally:
            self._batch_updating = False

        self.sheets_listview.Items.Refresh()
        if self.selection_mode == "sheets":
            self.status_text.Text = "Selected {} sheets".format(len(items))
        else:
            self.status_text.Text = "Selected {} views".format(len(items))

        # Update selection count
        self.update_selection_count()

    def select_none_sheets(self, sender, e):
        """Deselect all items (sheets or views)."""
        items = self.filtered_sheets if self.selection_mode == "sheets" else self.filtered_views
        self._batch_updating = True
        try:
            for item in items:
                item.IsSelected = False
        finally:
            self._batch_updating = False

        self.sheets_listview.Items.Refresh()
        if self.selection_mode == "sheets":
            self.status_text.Text = "Deselected all sheets"
        else:
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
        """Queue loading from a saved ViewSheetSet — reads collectors, needs API context."""
        self._run_in_api_context(self._load_sheet_set_api)

    def _load_sheet_set_api(self):
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
            # Creates a ViewSheetSet inside a Transaction — needs API context
            self._run_in_api_context(self.save_current_selection_as_vs_set)

        except Exception as ex:
            logger.error("Error handling Save V/S Set: {}".format(ex))

    def refresh_clicked(self, sender, e):
        """Reload document data — picks up sheets/views created or renamed
        while this modeless window has been open."""
        self.status_text.Text = "Refreshing..."
        self._run_in_api_context(self._do_refresh)

    def _do_refresh(self):
        """Re-collect sheets/views/sets from the model, preserving current selection."""
        try:
            # Remember what is ticked so the reload doesn't lose the selection
            selected_sheet_ids = set(eid_value(s.Sheet.Id) for s in self.all_sheets if s.IsSelected)
            selected_view_ids = set(eid_value(v.View.Id) for v in self.all_views if v.IsSelected)
            had_views = bool(self.all_views)

            # Drop stale caches so sheet sizes re-detect against the live model
            self._titleblock_size_cache = {}
            self._paper_size_cache = {}
            self._titleblock_cache_loaded = False

            # Reload document-driven dropdowns
            self.load_cad_export_setups()
            self.load_sheet_sets_for_filter()

            # Reload sheet list (always loaded) and restore ticks
            self.load_sheets()
            for item in self.all_sheets:
                if eid_value(item.Sheet.Id) in selected_sheet_ids:
                    item.IsSelected = True

            # Views load lazily on first mode switch — only reload if present
            if had_views or self.selection_mode == "views":
                self.load_views()
                for item in self.all_views:
                    if eid_value(item.View.Id) in selected_view_ids:
                        item.IsSelected = True

            # Rebuild the visible list for the active mode
            self.apply_filters()
            self.update_selection_count()

            count = len(self.all_sheets) if self.selection_mode == "sheets" else len(self.all_views)
            self.status_text.Text = "Refreshed — {} {} loaded | Revit {}".format(
                count, self.selection_mode, REVIT_VERSION)

        except Exception as ex:
            logger.error("Error refreshing document data: {}".format(ex))
            self.status_text.Text = "Refresh failed — see pyRevit log"

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
        """Queue the custom parameters dialog — it reads doc parameters, needs API context."""
        self._run_in_api_context(self._button_custom_parameters_api)

    def _button_custom_parameters_api(self):
        """Open custom parameters dialog to select parameters for filename.

        When a pattern is selected, it automatically applies to ALL items (sheets or views).
        """
        try:
            # Import the parameter selector dialog
            try:
                from GUI.ParameterSelectorDialog import ParameterSelectorDialog
            except ImportError:
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
        """Queue the filename pattern dialog — it reads doc parameters, needs API context."""
        self._run_in_api_context(self._edit_filename_api)

    def _edit_filename_api(self):
        """Open the parameter selector dialog to edit filename pattern.

        This is triggered by the 'Edit filename' button in DWG Options section.
        After editing, the sample filename is displayed in the dwg_filename_sample TextBox.
        """
        try:
            # Import the parameter selector dialog
            try:
                from GUI.ParameterSelectorDialog import ParameterSelectorDialog
            except ImportError:
                sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib', 'GUI'))
                if 'ParameterSelectorDialog' in sys.modules:
                    del sys.modules['ParameterSelectorDialog']
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
        """Queue the row naming dialog — it reads doc parameters, needs API context."""
        item = sender.Tag
        if not item:
            return
        self._run_in_api_context(lambda: self._row_naming_pattern_api(item))

    def _row_naming_pattern_api(self, item):
        """Open parameter selector dialog for a specific row.

        This applies the naming pattern to only the selected row's item.
        """
        try:
            # Import the parameter selector dialog
            try:
                from GUI.ParameterSelectorDialog import ParameterSelectorDialog
            except ImportError:
                sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib', 'GUI'))
                if 'ParameterSelectorDialog' in sys.modules:
                    del sys.modules['ParameterSelectorDialog']
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

    def nav_toggle_clicked(self, sender, e):
        """Handle sidebar toggle clicks."""
        try:
            target_index = -1
            if sender.Name == "nav_toggle_selection": target_index = 0
            elif sender.Name == "nav_toggle_format": target_index = 1
            elif sender.Name == "nav_toggle_queue": target_index = 2
            if target_index != -1:
                self.switch_to_view(target_index)
        except Exception as ex:
            logger.debug("Error in nav_toggle_clicked: {}".format(ex))

    def switch_to_view(self, index):
        """Switch to the given wizard tab (0=Selection, 1=Format, 2=Queue+Settings)."""
        try:
            # Enforce validation when navigating away from the Selection tab (index 0) to other tabs
            if index > 0 and self.main_tabs.SelectedIndex == 0:
                if self.selection_mode == "sheets":
                    selected_items = [s for s in self.all_sheets if s.IsSelected]
                    if not selected_items:
                        forms.alert("Please select at least one sheet to export.", title="No Sheets Selected")
                        self.sync_navigation_footer(0)
                        return
                else:  # views mode
                    selected_items = [v for v in self.all_views if v.IsSelected]
                    if not selected_items:
                        forms.alert("Please select at least one view to export.", title="No Views Selected")
                        self.sync_navigation_footer(0)
                        return

            self.main_tabs.SelectedIndex = index
            self.update_navigation_buttons(index)
        except Exception as ex:
            logger.debug("Error in switch_to_view: {}".format(ex))

    def sync_navigation_footer(self, index=None):
        """Update the visual state of the sidebar navigation toggles."""
        try:
            if index is None:
                index = self.main_tabs.SelectedIndex

            # Set the active toggle button to checked, and all others to unchecked
            self.nav_toggle_selection.IsChecked = (index == 0)
            self.nav_toggle_format.IsChecked = (index == 1)
            self.nav_toggle_queue.IsChecked = (index == 2)
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
                    # Reads live sheet numbers + titleblock sizes → API context
                    self._run_in_api_context(self.build_export_preview)
                else:
                    self.export_items = []
                    self.export_preview_list.ItemsSource = to_items_source(self.export_items)
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
            self.switch_to_view(1)
        elif idx == 1:
            self.switch_to_view(2)
        else:
            if self._export_running:
                return
            self._export_running = True
            # Freeze the whole window before export: Revit's PDF exporter
            # pumps messages mid-export, so a click landing on this modeless
            # window would re-enter WPF handlers inside Execute (fatal crash).
            self.IsEnabled = False
            # Modeless window: export must run inside API context. If Revit is
            # busy (user mid-command), the event fires once Revit is idle.
            self.status_text.Text = "Export queued — waiting for Revit..."
            self._run_in_api_context(self.start_export)

    def build_export_preview(self):
        """Build the export preview list."""
        try:
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

            # Get selected manual paper size
            manual_size = "Use Sheet Size"
            if hasattr(self, 'pdf_paper_size') and self.pdf_paper_size.SelectedItem:
                manual_size = self.pdf_paper_size.SelectedItem.Content

            # Build preview items
            self.export_items = []
            for item in selected_items:
                # Determine paper size and orientation for this item
                if (is_auto_detect or manual_size == "Use Sheet Size") and self.selection_mode == "sheets" and hasattr(item, 'Sheet'):
                    # Auto-detect from Title Block / Use Sheet Size
                    detected_size, detected_orientation = self.get_sheet_paper_size_and_orientation(item.Sheet)
                    size = detected_size
                    orientation = detected_orientation
                else:
                    # Use manual settings
                    size = manual_size
                    orientation = "Landscape" if self.pdf_landscape.IsChecked else "Portrait"

                for fmt in formats:
                    preview_item = ExportPreviewItem(item, fmt, size, orientation)
                    self.export_items.append(preview_item)

            # Update preview list
            self.export_preview_list.ItemsSource = to_items_source(self.export_items)
            self.progress_text.Text = "Ready to export {} items".format(len(self.export_items))
        except Exception as ex:
            logger.error("Error building export preview: {}".format(ex))
            if hasattr(self, 'progress_text') and self.progress_text:
                self.progress_text.Text = "Error preparing preview: {}".format(ex)

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

        # Only the placeholders actually present in the pattern need resolving.
        # For the common case (e.g. "{SheetNumber}") this lets us skip iterating
        # every parameter on the element — a real per-sheet saving on big models.
        import re as _re
        pattern_refs = set(_re.findall(r'\{([^{}]+)\}', pattern))
        standard_keys = set(k[1:-1] for k in replacements.keys())
        needs_dynamic_params = bool(pattern_refs - standard_keys)

        # Get ALL parameters from the element dynamically — only when the naming
        # pattern references a non-standard parameter placeholder.
        if element and needs_dynamic_params:
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
                                if elem_id and eid_value(elem_id) > 0:
                                    try:
                                        elem = self.doc.GetElement(elem_id)
                                        param_value = elem.Name if elem else ""
                                    except:
                                        param_value = str(eid_value(elem_id))

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

        # Add common sheet-specific built-in parameters explicitly — only when
        # the naming pattern references one of them (skips ~7 get_Parameter
        # calls per sheet for simple patterns like "{SheetNumber}").
        if needs_dynamic_params and hasattr(item, 'Sheet'):
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
                    if phase_id and eid_value(phase_id) > 0:
                        phase = self.doc.GetElement(phase_id)
                        replacements["{Phase}"] = phase.Name if phase else ""
            except:
                replacements["{Phase}"] = ""

            try:
                level_param = element.get_Parameter(DB.BuiltInParameter.VIEW_LEVEL)
                if level_param:
                    level_id = level_param.AsElementId()
                    if level_id and eid_value(level_id) > 0:
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
                    elif progress > 0:
                        item.Status = ""
                    self.export_preview_list.Items.Refresh()
                    break
        except:
            pass
        self._flush_ui()

    def _flush_ui(self):
        """Force WPF to process pending render operations so progress shows in realtime.

        Modeless: start_export runs INSIDE ExternalEvent.Execute, and a nested
        dispatcher frame here pumps Win32 messages for every window on Revit's
        UI thread mid-API-call. Revit's native PDF exporter additionally pumps
        its own message loop per sheet — the combined re-entrancy is the fatal
        0xc0000005 class (see BatchOutEventHandler docstring). No pump there;
        progress repaints when Execute returns. Modal fallback keeps the pump —
        the command is still executing, the pre-modeless behavior is safe.
        """
        if self.modeless:
            return
        try:
            self.Dispatcher.Invoke(DispatcherPriority.Render, Action(lambda: None))
        except Exception:
            pass

    def _update_progress(self, value, text):
        """Update progress bar value, status label, and percentage label, then repaint."""
        try:
            self.overall_progress.Value = value
            self.progress_text.Text = text
            pct = int(value)
            self.progress_percent.Text = "{}%".format(pct) if pct > 0 else ""
            if pct >= 100:
                from System.Windows.Media import SolidColorBrush, Color
                self.progress_percent.Foreground = SolidColorBrush(Color.FromArgb(0xFF, 0x10, 0xB9, 0x81))
            else:
                from System.Windows.Media import SolidColorBrush, Color
                self.progress_percent.Foreground = SolidColorBrush(Color.FromArgb(0xFF, 0xC2, 0x41, 0x0C))
            self._flush_ui()
        except Exception:
            pass

    def start_export(self):
        """Start the export process."""
        total_exported = 0
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

            # Remember this setup for the next session
            self.save_latest_setup()

            # Disable buttons during export
            self.next_button.IsEnabled = False
            self.back_button.IsEnabled = False
            self.status_text.Text = "Exporting..."

            # The Queue rows carry each item's status column, and _overall_total
            # is read from them. Rebuild if they are missing (Queue tab never
            # opened) so no item exports without a visible row.
            if not self.export_items:
                try:
                    self.build_export_preview()
                except Exception as preview_ex:
                    logger.debug("Could not build export preview: {}".format(preview_ex))

            # Export to each format — overall progress tracked per-sheet via _overall_counter
            total_exported = 0
            self._overall_total = len(self.export_items)
            self._overall_counter = 0
            self._skipped_fatal = []  # reset: this window can export more than once
            self._safe_applied = []
            self._reset_run_state()

            # Known-fatal sheets: offer safe mode once, before anything runs.
            self._ask_safe_mode(selected_items)

            # Risky items last so a native crash cannot cost the rest of the batch
            selected_items = self._order_risky_last(selected_items)

            def confirmed_count(format_name, count, expected):
                if count < expected:
                    self._failed_items.append(
                        "{}: {} of {} expected output(s) confirmed".format(
                            format_name, count, expected))
                return count

            if self.export_dwg.IsChecked:
                folder = os.path.join(output_folder, "DWG") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_dwg(selected_items, folder)
                total_exported += confirmed_count("DWG", count, len(selected_items))

                # Free whatever the DWG phase allocated before the raster-heavy
                # PDF phase begins — link-heavy sheets already push Revit hard.
                GC.Collect()
                GC.WaitForPendingFinalizers()

            if self.export_pdf.IsChecked:
                folder = os.path.join(output_folder, "PDF") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_pdf(selected_items, folder)
                total_exported += confirmed_count(
                    "PDF", count, 1 if self.combine_pdf.IsChecked else len(selected_items))

            if self.export_dwf.IsChecked:
                folder = os.path.join(output_folder, "DWF") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_dwf(selected_items, folder)
                total_exported += confirmed_count("DWF", count, len(selected_items))

            if self.export_dgn.IsChecked:
                folder = os.path.join(output_folder, "DGN") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_dgn(selected_items, folder)
                total_exported += confirmed_count("DGN", count, len(selected_items))

            if self.export_nwd.IsChecked:
                folder = os.path.join(output_folder, "NWC") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_nwd(selected_items, folder)
                total_exported += confirmed_count("NWC", count, len(selected_items))

            if self.export_ifc.IsChecked:
                folder = os.path.join(output_folder, "IFC") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_ifc(selected_items, folder)
                total_exported += confirmed_count("IFC", count, 1)

            if self.export_img.IsChecked:
                folder = os.path.join(output_folder, "Images") if split_by_format else output_folder
                if not os.path.exists(folder):
                    os.makedirs(folder)
                count = self.export_to_images(selected_items, folder)
                total_exported += confirmed_count("Images", count, len(selected_items))

            if not total_exported:
                outcome = "No exports confirmed"
            elif self._failed_items or self._skipped_fatal:
                outcome = "Export finished with issues"
            else:
                outcome = "Export complete"
            summary = "{}: {} output(s) confirmed".format(outcome, total_exported)
            if self._failed_items:
                summary += " ({} issue report(s))".format(len(self._failed_items))
            if self._skipped_fatal:
                summary += " ({} skipped)".format(len(self._skipped_fatal))
            self.status_text.Text = summary
            self._update_progress(100, summary)
            self.next_button.IsEnabled = True
            self.back_button.IsEnabled = True

            # Sheets skipped because they previously hard-crashed Revit need to
            # be called out — silently missing files would be worse than a crash.
            skip_note = ""
            if self._skipped_fatal:
                skip_note = (
                    "\n\nSKIPPED — these crashed Revit on an earlier run:\n  {}\n\n"
                    "The crash is inside Revit's own renderer (faceting of the "
                    "views on these sheets), not BatchOut. Fix the offending "
                    "geometry, export them from Revit 2026, or delete\n{}\nto "
                    "let BatchOut try them again.".format(
                        "\n  ".join(self._skipped_fatal), self._crash_history_file))

            # Safe-mode files look like ordinary exports but are not: they were
            # produced with views temporarily simplified (and the change rolled
            # back afterwards), so they have to be named before the user files
            # them as final issue drawings.
            safe_note = ""
            if self._safe_applied:
                safe_note = (
                    "\n\nSAFE MODE was used for:\n  {}\n\nThe model itself was "
                    "left untouched (rolled back). Review these files before "
                    "issuing them.".format("\n  ".join(self._safe_applied)))

            # Anything that produced no file must be named — a quietly missing
            # export is the one failure mode the user cannot see for themselves.
            fail_note = ""
            if self._failed_items:
                shown = self._failed_items[:20]
                fail_note = "\n\nEXPORT NOT CONFIRMED for:\n  {}".format("\n  ".join(shown))
                if len(self._failed_items) > len(shown):
                    fail_note += "\n  ...and {} more".format(
                        len(self._failed_items) - len(shown))
                fail_note += ("\n\nFull errors are in the pyRevit output log. Some "
                              "view/format pairs simply cannot be exported by Revit "
                              "(e.g. a schedule to DWG) and will always fail here.")

            # Ask if user wants to open output folder
            if forms.alert("{}{}{}{}\n\nDo you want to open the output folder?".format(
                              summary, fail_note, safe_note, skip_note),
                          title=outcome,
                          yes=True, no=True):
                os.startfile(output_folder)

        except Exception as ex:
            logger.error("Export failed: {}".format(ex))
            forms.alert("Export stopped: {}\n\n{} earlier output(s) were confirmed. "
                        "Existing output files were kept; review the output folder before retrying.".format(
                            ex, total_exported), title="Export Error")
            self.status_text.Text = "Export failed"
            self.next_button.IsEnabled = True
            self.back_button.IsEnabled = True
        finally:
            # Unfreeze the window (frozen in go_next) on every exit path,
            # including the early validation returns above
            self._export_running = False
            self.IsEnabled = True

    def export_to_dwg(self, items, output_folder):
        """Export items (sheets or views) to DWG format with version-aware API usage.

        Supports Revit 2022-2026 with appropriate API handling for each version.
        """
        try:
            # Sync cached values with live values from Revit
            self._sync_item_labels(items, "DWG")

            # Get selected export setup (if any)
            selected_setup = None
            selected_setup_name = None
            if self.cad_export_setup.SelectedIndex > 0:
                # User selected a specific setup (not the default)
                selected_item = self.cad_export_setup.SelectedItem
                if hasattr(selected_item, 'Tag') and selected_item.Tag:
                    selected_setup = selected_item.Tag
                    selected_setup_name = selected_item.Content

            # Create DWG export options.
            # DWGExportOptions has no instance method to load a named setup's
            # settings after construction - GetPredefinedOptions is the only
            # API that returns an options object pre-populated with a named
            # ExportDWGSettings' layers/colors/line weights/etc.
            if selected_setup:
                try:
                    dwg_options = DWGExportOptions.GetPredefinedOptions(
                        self.doc, selected_setup.Name)
                except Exception as setup_ex:
                    logger.warning("Could not apply export setup '{}': {}".format(
                        selected_setup_name, setup_ex))
                    dwg_options = DWGExportOptions()
                    # Fallback: Set PropOverrides to ByEntity to match Revit colors
                    try:
                        dwg_options.PropOverrides = PropOverrideMode.ByEntity
                    except:
                        pass
            else:
                dwg_options = DWGExportOptions()
                # No setup selected - ensure colors match Revit by using ByEntity mode
                try:
                    dwg_options.PropOverrides = PropOverrideMode.ByEntity
                except Exception as prop_ex:
                    logger.debug("Could not set PropOverrides: {}".format(prop_ex))

            # Set AutoCAD version (overrides whatever the setup/default specifies)
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

            exported_count = 0

            for item in items:
                safe_group = None
                element_name = str(getattr(item, 'SheetNumber', 'Unknown'))
                try:
                    # Get the actual element (sheet or view)
                    element, element_name = self._element_of(item)
                    if element is None:
                        self._note_failure(element_name, "DWG",
                                           "row is neither a sheet nor a view")
                        continue

                    # This sheet has hard-crashed Revit's DWG exporter before.
                    # Safe mode neutralises the fatal geometry first; if it is
                    # unavailable we still export (queued last), because a
                    # missing file the user was never told about is worse.
                    safe_level = None
                    if self._is_known_fatal(item.SheetNumber, "DWG"):
                        if self._skip_fatal:
                            self._note_skipped_fatal(item.SheetNumber, "DWG")
                            continue
                        if self._safe_mode and not self._safe_ladder_exhausted(
                                item.SheetNumber, "DWG"):
                            safe_group, safe_level = self._begin_safe_mode(
                                element, self._next_safe_level(item.SheetNumber, "DWG"),
                                fmt="DWG")
                        if safe_group is None:
                            logger.warning(
                                "'{}' crashed Revit's DWG exporter before and is "
                                "being exported without mitigation.".format(
                                    item.SheetNumber))

                    # Mark as in-progress (orange) before the export call
                    self.update_export_item_progress(item.SheetNumber, "DWG", 50, "Exporting...")
                    self._update_progress(
                        int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                        "Exporting {} to DWG...".format(element_name)
                    )

                    filename = item.CustomFilename or self.get_export_filename(item)

                    # Remove extension if present
                    if filename.lower().endswith('.dwg'):
                        filename = filename[:-4]

                    # Never reuse a name already written in this run — otherwise
                    # two views sharing a name overwrite each other's file.
                    filename = self._reserve_filename(output_folder, filename, ".dwg")

                    # Expected output + its current mtime, so a stale file left by
                    # an earlier aborted run cannot be mistaken for a fresh export.
                    expected_file = os.path.join(output_folder, filename + ".dwg")
                    prev_mtime = os.path.getmtime(expected_file) if os.path.exists(expected_file) else None
                    started_at = time.time()

                    # VERSION-AWARE: Export API handling
                    # All versions 2022-2026 support ICollection<ElementId> signature
                    # Signature: Export(String folder, String name, ICollection<ElementId> views, DWGExportOptions options)
                    view_ids = List[DB.ElementId]()
                    view_ids.Add(element.Id)

                    # Breadcrumb: DWG export regenerates the sheet's views through
                    # Revit's hidden-line/faceting pipeline, which can hard-crash
                    # the process (uncatchable). If it does, this file names the
                    # sheet that killed it so the next session can skip it.
                    self._write_crash_marker(item.SheetNumber, "DWG",
                                             safe_level=safe_level)

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

                    # Export call returned -> Revit survived this sheet.
                    self._clear_crash_marker()

                    # Verify a FRESH file was written (not a leftover from an
                    # earlier aborted run).
                    if self._verify_export(expected_file, prev_mtime, started_at, ".dwg"):
                        exported_count += 1
                        self._overall_counter += 1
                        self._update_progress(
                            int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                            "Exported {} to DWG".format(element_name)
                        )
                        self.update_export_item_progress(item.SheetNumber, "DWG", 100)
                    else:
                        self._note_failure(item.SheetNumber, "DWG",
                                           "Revit wrote no DWG file")

                except Exception as ex:
                    logger.error("Error exporting {} to DWG: {}".format(element_name, ex))
                    self._note_failure(str(getattr(item, 'SheetNumber', element_name)),
                                       "DWG", ex)
                finally:
                    # Safe mode must NEVER leave the model modified.
                    self._end_safe_mode(safe_group)

            return exported_count

        except Exception as ex:
            logger.error("DWG export failed: {}".format(ex))
            return 0

    def export_to_dgn(self, items, output_folder):
        """Export items (sheets or views) to DGN (MicroStation) format."""
        try:
            # Sync cached values with live values from Revit
            self._sync_item_labels(items, "DGN")

            dgn_options = DGNExportOptions()

            exported_count = 0

            for item in items:
                element_name = str(getattr(item, 'SheetNumber', 'Unknown'))
                try:
                    element, element_name = self._element_of(item)
                    if element is None:
                        self._note_failure(element_name, "DGN",
                                           "row is neither a sheet nor a view")
                        continue

                    if self._is_known_fatal(item.SheetNumber, "DGN"):
                        if self._skip_fatal:
                            self._note_skipped_fatal(item.SheetNumber, "DGN")
                            continue
                        logger.warning(
                            "'{}' crashed Revit's DGN exporter before — exporting "
                            "anyway (queued last).".format(item.SheetNumber))

                    self.update_export_item_progress(item.SheetNumber, "DGN", 50, "Exporting...")
                    self._update_progress(
                        int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                        "Exporting {} to DGN...".format(element_name)
                    )

                    filename = item.CustomFilename or self.get_export_filename(item)
                    if filename.lower().endswith('.dgn'):
                        filename = filename[:-4]
                    filename = self._reserve_filename(output_folder, filename, ".dgn")

                    view_ids = List[DB.ElementId]()
                    view_ids.Add(element.Id)

                    expected_file = os.path.join(output_folder, filename + ".dgn")
                    prev_mtime = os.path.getmtime(expected_file) if os.path.exists(expected_file) else None
                    started_at = time.time()

                    self._write_crash_marker(item.SheetNumber, "DGN")
                    self.doc.Export(output_folder, filename, view_ids, dgn_options)
                    self._clear_crash_marker()

                    if self._verify_export(expected_file, prev_mtime, started_at, ".dgn"):
                        exported_count += 1
                        self._overall_counter += 1
                        self._update_progress(
                            int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                            "Exported {} to DGN".format(element_name)
                        )
                        self.update_export_item_progress(item.SheetNumber, "DGN", 100)
                    else:
                        self._note_failure(item.SheetNumber, "DGN",
                                           "Revit wrote no DGN file")

                except Exception as ex:
                    logger.error("Error exporting {} to DGN: {}".format(element_name, ex))
                    self._note_failure(str(getattr(item, 'SheetNumber', element_name)),
                                       "DGN", ex)

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
            self._sync_item_labels(items, "PDF")

            # Check if combine PDF is enabled
            combine_pdf = self.combine_pdf.IsChecked

            exported_count = 0

            if combine_pdf:
                # Export all items to a single PDF
                included_items = []
                try:
                    # Get all element IDs as System.Collections.Generic.List.
                    # A combined export is ONE native call, so a single fatal
                    # sheet takes the entire job (and the Revit session) with it —
                    # but dropping pages silently is not ours to decide. Every
                    # selected item goes in unless the user explicitly chose to
                    # skip the known-fatal ones in the pre-export prompt.
                    element_ids = List[DB.ElementId]()
                    for item in items:
                        number = getattr(item, 'SheetNumber', None)
                        if number and self._skip_fatal and self._is_known_fatal(number, "PDF"):
                            self._note_skipped_fatal(number, "PDF")
                            continue
                        element, label = self._element_of(item)
                        if element is None:
                            self._note_failure(label, "PDF",
                                               "row is neither a sheet nor a view")
                            continue
                        element_ids.Add(element.Id)
                        included_items.append(item)

                    if element_ids.Count == 0:
                        logger.warning("Combined PDF skipped: nothing left to export.")
                        return 0

                    # Only rows included in the native call can become successful.
                    for item in included_items:
                        self.update_export_item_progress(item.SheetNumber, "PDF", 50, "Exporting...")
                    self._update_progress(
                        int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                        "Exporting combined PDF with {} items...".format(len(included_items)))
                    first_element, first_name = self._element_of(included_items[0])
                    last_element, last_name = self._element_of(included_items[-1])
                    if not hasattr(included_items[0], 'Sheet'):
                        first_name = first_name[:20]
                    if not hasattr(included_items[-1], 'Sheet'):
                        last_name = last_name[:20]
                    filename = "{}-{}_Combined".format(first_name, last_name)
                    filename = self._reserve_filename(output_folder, filename, ".pdf")
                    expected_file = os.path.join(output_folder, filename + ".pdf")
                    before_output = self._native_output_stamp(expected_file)

                    # Create PDF export options
                    pdf_options = PDFExportOptions()
                    pdf_options.Combine = True
                    # Set filename (learned from pyRevit)
                    pdf_options.FileName = filename

                    # Keep linework under transparent filled regions from dropping
                    self._apply_pdf_quality(pdf_options)

                    # VERSION-AWARE: Apply PDF settings
                    # Use Smart API Adapter if available for intelligent configuration
                    if self.api_adapter:
                        pdf_options = self.api_adapter.configure_pdf_options(
                            pdf_options,
                            hide_ref_planes=self.pdf_hide_ref_planes.IsChecked,
                            hide_scope_boxes=self.pdf_hide_scope_boxes.IsChecked,
                            hide_crop_boundaries=self.pdf_hide_crop_boundaries.IsChecked,
                            hide_unreferenced_tags=self.pdf_hide_unreferenced_tags.IsChecked
                        )
                    else:
                        # Fallback to manual configuration
                        try:
                            if self.pdf_hide_ref_planes.IsChecked:
                                pdf_options.HideReferencePlane = True
                        except Exception as ex:
                            logger.debug("HideReferencePlane not supported in Revit {}: {}".format(REVIT_VERSION, ex))

                        try:
                            if self.pdf_hide_scope_boxes.IsChecked:
                                pdf_options.HideScopeBoxes = True
                        except Exception as ex:
                            logger.debug("HideScopeBoxes not supported in Revit {}: {}".format(REVIT_VERSION, ex))

                        try:
                            if self.pdf_hide_crop_boundaries.IsChecked:
                                pdf_options.HideCropBoundaries = True
                        except Exception as ex:
                            logger.debug("HideCropBoundaries not supported in Revit {}: {}".format(REVIT_VERSION, ex))

                        try:
                            if self.pdf_hide_unreferenced_tags.IsChecked:
                                pdf_options.HideUnreferencedViewTags = True
                        except Exception as ex:
                            logger.debug("HideUnreferencedViewTags not supported in Revit {}: {}".format(REVIT_VERSION, ex))

                    # Configure paper size, orientation, and zoom for combined PDF
                    is_auto = self.pdf_auto_detect_size.IsChecked
                    manual_size = self.pdf_paper_size.SelectedItem.Content if self.pdf_paper_size.SelectedItem else "Use Sheet Size"
                    
                    if not is_auto and manual_size != "Use Sheet Size":
                        try:
                            pdf_options.PaperFormat = self.get_paper_format_enum(manual_size)
                        except Exception as ex:
                            logger.debug("Failed to set combined PDF paper format: {}".format(ex))
                            
                        try:
                            pdf_options.PaperOrientation = DB.PageOrientationType.Landscape if self.pdf_landscape.IsChecked else DB.PageOrientationType.Portrait
                        except Exception as ex:
                            logger.debug("Failed to set combined PDF orientation: {}".format(ex))
                            
                        # Only set Zoom if PaperFormat is not Default
                        try:
                            if self.pdf_fit_to_page.IsChecked:
                                pdf_options.ZoomType = DB.ZoomType.FitToPage
                            else:
                                pdf_options.ZoomType = DB.ZoomType.Zoom
                                pdf_options.ZoomPercentage = 100
                        except Exception as ex:
                            logger.debug("Failed to set combined PDF zoom: {}".format(ex))
                    else:
                        # Auto-detect or Use Sheet Size: Use Default format so Revit uses sheet size
                        try:
                            pdf_options.PaperFormat = DB.ExportPaperFormat.Default
                        except Exception as ex:
                            logger.debug("Failed to set combined PDF paper format to Default: {}".format(ex))

                    # Breadcrumb for the combined job. A combined export is one
                    # native call over every sheet, so if it dies we can only
                    # record the job, not the individual sheet that killed it —
                    # export individually (uncheck Combine) to pinpoint that.
                    self._write_crash_marker(filename, "PDF-combined",
                                             self._raster_level_for(None) or "Presentation")

                    # VERSION-AWARE: Export using Revit's native PDF export
                    # Use Smart API Adapter if available for intelligent export (handles method overload resolution)
                    if self.api_adapter:
                        # Smart adapter automatically handles version differences and method overload resolution
                        export_result = self.api_adapter.export_pdf(output_folder, filename, element_ids, pdf_options)
                    else:
                        # Fallback to direct export call
                        # Revit 2022-2026 signature: Export(String folder, IList<ElementId> viewIds, PDFExportOptions options)
                        # NOTE: PDF export does NOT take a filename parameter in the Export() method (unlike DWG/DXF)
                        # Instead, filename is set via PDFExportOptions.FileName property (learned from pyRevit)
                        export_result = self.doc.Export(output_folder, element_ids, pdf_options)

                    # Export call returned -> Revit survived the combined job.
                    self._clear_crash_marker()
                    if not export_result:
                        raise RuntimeError(
                            "Revit reported an incomplete combined PDF export. Any output file was kept.")

                    if self._confirm_native_output(expected_file, before_output):
                        exported_count = 1
                        self._overall_counter += len(included_items)
                        self._update_progress(
                            int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                            "PDF combined: {} sheets exported".format(len(included_items))
                        )
                        for item in included_items:
                            self.update_export_item_progress(item.SheetNumber, "PDF", 100)
                    else:
                        # One native call for every page: if no file landed, none
                        # of the selected items were exported. Name them all.
                        for item in included_items:
                            self._note_failure(getattr(item, 'SheetNumber', 'Unknown'),
                                               "PDF", "combined PDF produced no new or updated nonempty output")

                except Exception as ex:
                    logger.error("Error exporting combined PDF: {}".format(ex))
                    for item in included_items:
                        self._note_failure(getattr(item, 'SheetNumber', 'Unknown'),
                                           "PDF", "combined PDF failed: {}".format(ex))

            else:
                # Export each item individually
                sheets_since_gc = 0
                for item in items:
                    safe_group = None
                    element_name = str(getattr(item, 'SheetNumber', 'Unknown'))
                    try:
                        # Get the actual element (sheet or view)
                        element, element_name = self._element_of(item)
                        if element is None:
                            self._note_failure(element_name, "PDF",
                                               "row is neither a sheet nor a view")
                            continue

                        # This sheet has hard-crashed Revit's PDF rasteriser
                        # before. Safe mode neutralises the geometry first when
                        # a rung is left; otherwise it still goes out (queued
                        # last) rather than disappearing from the output.
                        safe_level = None
                        if self._is_known_fatal(item.SheetNumber, "PDF"):
                            if self._skip_fatal:
                                self._note_skipped_fatal(item.SheetNumber, "PDF")
                                continue
                            if self._safe_mode and not self._safe_ladder_exhausted(
                                    item.SheetNumber, "PDF"):
                                safe_group, safe_level = self._begin_safe_mode(
                                    element, self._next_safe_level(item.SheetNumber, "PDF"),
                                    fmt="PDF")
                            if safe_group is None:
                                logger.warning(
                                    "'{}' crashed Revit's PDF exporter before and "
                                    "is being exported without mitigation.".format(
                                        item.SheetNumber))

                        self.update_export_item_progress(item.SheetNumber, "PDF", 50, "Exporting...")
                        self._update_progress(
                            int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                            "Exporting {} to PDF...".format(element_name)
                        )

                        filename = item.CustomFilename or self.get_export_filename(item)

                        # Remove extension if present
                        if filename.lower().endswith('.pdf'):
                            filename = filename[:-4]

                        # Unique per run: a Floor Plan and a Ceiling Plan may share
                        # a name, and the second export used to overwrite the first.
                        filename = self._reserve_filename(output_folder, filename, ".pdf")

                        # Expected output path + its current mtime (one stat, not
                        # a whole-folder glob) so a fresh write can be detected.
                        expected_file = os.path.join(output_folder, filename + ".pdf")
                        prev_mtime = os.path.getmtime(expected_file) if os.path.exists(expected_file) else None
                        started_at = time.time()

                        # Create PDF export options
                        pdf_options = PDFExportOptions()
                        # IMPORTANT: Use Combine = True even for single sheets to force Revit to use our filename
                        # When Combine = False, Revit ignores FileName and uses sheet number/name
                        pdf_options.Combine = True
                        # Set filename to match DWG naming pattern
                        pdf_options.FileName = filename

                        # Full quality by default; only a sheet that previously
                        # crashed Revit's rasteriser starts one rung lower so it
                        # can export at all. Keeps linework under transparent
                        # filled regions from dropping on every normal sheet.
                        raster_level = self._raster_level_for(item.SheetNumber)
                        self._apply_pdf_quality(pdf_options, raster_level)

                        # VERSION-AWARE: Apply PDF settings
                        # Use Smart API Adapter if available for intelligent configuration
                        if self.api_adapter:
                            pdf_options = self.api_adapter.configure_pdf_options(
                                pdf_options,
                                hide_ref_planes=self.pdf_hide_ref_planes.IsChecked,
                                hide_scope_boxes=self.pdf_hide_scope_boxes.IsChecked,
                                hide_crop_boundaries=self.pdf_hide_crop_boundaries.IsChecked,
                                hide_unreferenced_tags=self.pdf_hide_unreferenced_tags.IsChecked
                            )
                        else:
                            # Fallback to manual configuration
                            try:
                                if self.pdf_hide_ref_planes.IsChecked:
                                    pdf_options.HideReferencePlane = True
                            except Exception as ex:
                                logger.debug("HideReferencePlane not supported in Revit {}: {}".format(REVIT_VERSION, ex))

                            try:
                                if self.pdf_hide_scope_boxes.IsChecked:
                                    pdf_options.HideScopeBoxes = True
                            except Exception as ex:
                                logger.debug("HideScopeBoxes not supported in Revit {}: {}".format(REVIT_VERSION, ex))

                            try:
                                if self.pdf_hide_crop_boundaries.IsChecked:
                                    pdf_options.HideCropBoundaries = True
                            except Exception as ex:
                                logger.debug("HideCropBoundaries not supported in Revit {}: {}".format(REVIT_VERSION, ex))

                            try:
                                if self.pdf_hide_unreferenced_tags.IsChecked:
                                    pdf_options.HideUnreferencedViewTags = True
                            except Exception as ex:
                                logger.debug("HideUnreferencedViewTags not supported in Revit {}: {}".format(REVIT_VERSION, ex))

                        # Determine paper size and orientation for this individual sheet/view item
                        is_auto = self.pdf_auto_detect_size.IsChecked
                        manual_size = self.pdf_paper_size.SelectedItem.Content if self.pdf_paper_size.SelectedItem else "Use Sheet Size"
                        
                        sheet_size = None
                        sheet_orientation = None
                        
                        if (is_auto or manual_size == "Use Sheet Size") and hasattr(item, 'Sheet'):
                            # Auto-detect
                            detected_size, detected_orientation = self.get_sheet_paper_size_and_orientation(item.Sheet)
                            sheet_size = detected_size
                            sheet_orientation = detected_orientation
                        else:
                            sheet_size = manual_size
                            sheet_orientation = "Landscape" if self.pdf_landscape.IsChecked else "Portrait"
                            
                        # Apply to options
                        if sheet_size and sheet_size != "Use Sheet Size":
                            try:
                                pdf_options.PaperFormat = self.get_paper_format_enum(sheet_size)
                            except Exception as ex:
                                logger.debug("Failed to set individual PDF paper format: {}".format(ex))
                                
                            try:
                                pdf_options.PaperOrientation = DB.PageOrientationType.Landscape if sheet_orientation == "Landscape" else DB.PageOrientationType.Portrait
                            except Exception as ex:
                                logger.debug("Failed to set individual PDF orientation: {}".format(ex))
                                
                            # Set Zoom
                            try:
                                if self.pdf_fit_to_page.IsChecked:
                                    pdf_options.ZoomType = DB.ZoomType.FitToPage
                                else:
                                    pdf_options.ZoomType = DB.ZoomType.Zoom
                                    pdf_options.ZoomPercentage = 100
                            except Exception as ex:
                                logger.debug("Failed to set individual PDF zoom: {}".format(ex))
                        else:
                            try:
                                pdf_options.PaperFormat = DB.ExportPaperFormat.Default
                            except Exception as ex:
                                logger.debug("Failed to set individual PDF paper format to Default: {}".format(ex))

                        # Create System.Collections.Generic.List for element IDs
                        element_ids = List[DB.ElementId]()
                        element_ids.Add(element.Id)

                        # Breadcrumb: if the native export below hard-crashes
                        # Revit (uncatchable fatal), this file names the sheet
                        # that killed it so the next session can recover it.
                        self._write_crash_marker(item.SheetNumber, "PDF",
                                                 raster_level or "Presentation",
                                                 safe_level=safe_level)

                        # VERSION-AWARE: Export using Revit's native PDF export
                        # Use Smart API Adapter if available for intelligent export (handles method overload resolution)
                        if self.api_adapter:
                            # Smart adapter automatically handles version differences and method overload resolution
                            export_result = self.api_adapter.export_pdf(output_folder, filename, element_ids, pdf_options)
                        else:
                            # Fallback to direct export call
                            # Revit 2022-2026 signature: Export(String folder, IList<ElementId> viewIds, PDFExportOptions options)
                            # NOTE: PDF export does NOT take a filename parameter in the Export() method (unlike DWG/DXF)
                            # Instead, filename is set via PDFExportOptions.FileName property (learned from pyRevit)
                            export_result = self.doc.Export(output_folder, element_ids, pdf_options)

                        # Export call returned -> Revit survived this sheet; drop
                        # the breadcrumb so it is not mistaken for a fresh crash.
                        self._clear_crash_marker()

                        if not export_result:
                            raise RuntimeError("Revit reported PDF export failure; any output file was kept for review")

                        # Confirm the file landed by polling for it (no fixed
                        # sleep, no folder-wide glob — much faster on OneDrive).
                        if self._verify_export(expected_file, prev_mtime, started_at, ".pdf"):
                            exported_count += 1
                            self._overall_counter += 1
                            self._update_progress(
                                int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                                "Exported {} to PDF".format(element_name)
                            )
                            self.update_export_item_progress(item.SheetNumber, "PDF", 100)
                        else:
                            self._note_failure(item.SheetNumber, "PDF",
                                               "Revit wrote no PDF file")

                        # Reclaim native raster buffers periodically (every 10
                        # sheets) rather than every sheet — a full GC on Revit's
                        # large heap is not free, so batch it to keep exports fast
                        # while still capping memory growth.
                        sheets_since_gc += 1
                        if sheets_since_gc >= 10:
                            sheets_since_gc = 0
                            GC.Collect()
                            GC.WaitForPendingFinalizers()

                    except Exception as ex:
                        logger.error("Error exporting {} to PDF: {}".format(element_name, ex))
                        self._note_failure(str(getattr(item, 'SheetNumber', element_name)),
                                           "PDF", ex)
                    finally:
                        # Safe mode must NEVER leave the model modified.
                        self._end_safe_mode(safe_group)

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
            self._sync_item_labels(items, "DWF")

            # Create DWF export options
            dwf_options = DWFExportOptions()

            exported_count = 0

            for item in items:
                element_name = str(getattr(item, 'SheetNumber', 'Unknown'))
                try:
                    # Get the actual element (sheet or view)
                    element, element_name = self._element_of(item)
                    if element is None:
                        self._note_failure(element_name, "DWF",
                                           "row is neither a sheet nor a view")
                        continue

                    if self._is_known_fatal(item.SheetNumber, "DWF"):
                        if self._skip_fatal:
                            self._note_skipped_fatal(item.SheetNumber, "DWF")
                            continue
                        logger.warning(
                            "'{}' crashed Revit's DWF exporter before — exporting "
                            "anyway (queued last).".format(item.SheetNumber))

                    self.update_export_item_progress(item.SheetNumber, "DWF", 50, "Exporting...")
                    self._update_progress(
                        int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                        "Exporting {} to DWF...".format(element_name)
                    )

                    filename = item.CustomFilename or self.get_export_filename(item)

                    # Remove extension if present
                    if filename.lower().endswith('.dwf'):
                        filename = filename[:-4]
                    filename = self._reserve_filename(output_folder, filename, ".dwf")

                    # VERSION-AWARE: Export handling
                    # Revit 2022-2026 all support ViewSet for DWF export
                    # Signature: Export(String folder, String name, ViewSet views, DWFExportOptions options)
                    view_set = DB.ViewSet()
                    view_set.Insert(element)

                    expected_file = os.path.join(output_folder, filename + ".dwf")
                    prev_mtime = os.path.getmtime(expected_file) if os.path.exists(expected_file) else None
                    started_at = time.time()

                    self._write_crash_marker(item.SheetNumber, "DWF")
                    self.doc.Export(output_folder, filename, view_set, dwf_options)
                    self._clear_crash_marker()

                    # Verify a fresh file was created
                    if self._verify_export(expected_file, prev_mtime, started_at, ".dwf"):
                        exported_count += 1
                        self._overall_counter += 1
                        self._update_progress(
                            int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                            "Exported {} to DWF".format(element_name)
                        )
                        self.update_export_item_progress(item.SheetNumber, "DWF", 100)
                    else:
                        self._note_failure(item.SheetNumber, "DWF",
                                           "Revit wrote no DWF file")

                except Exception as ex:
                    logger.error("Error exporting {} to DWF: {}".format(element_name, ex))
                    self._note_failure(str(getattr(item, 'SheetNumber', element_name)),
                                       "DWF", ex)

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
            self._sync_item_labels(items, "NWC")

            # Create Navisworks export options
            nwd_options = NavisworksExportOptions()

            exported_count = 0

            for item in items:
                element_name = str(getattr(item, 'SheetNumber', 'Unknown'))
                try:
                    # Get the actual element (sheet or view)
                    element, element_name = self._element_of(item)
                    if element is None:
                        self._note_failure(element_name, "NWC",
                                           "row is neither a sheet nor a view")
                        continue

                    if self._is_known_fatal(item.SheetNumber, "NWC"):
                        if self._skip_fatal:
                            self._note_skipped_fatal(item.SheetNumber, "NWC")
                            continue
                        logger.warning(
                            "'{}' crashed Revit's NWC exporter before — exporting "
                            "anyway (queued last).".format(item.SheetNumber))

                    self.update_export_item_progress(item.SheetNumber, "NWC", 50, "Exporting...")
                    self._update_progress(
                        int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                        "Exporting {} to NWC...".format(element_name)
                    )

                    filename = item.CustomFilename or self.get_export_filename(item)
                    if filename.lower().endswith('.nwc'):
                        filename = filename[:-4]
                    filename = self._reserve_filename(output_folder, filename, ".nwc")

                    expected_file = os.path.join(output_folder, filename + ".nwc")
                    prev_mtime = os.path.getmtime(expected_file) if os.path.exists(expected_file) else None
                    started_at = time.time()

                    # Export view
                    nwd_options.ExportScope = DB.NavisworksExportScope.View
                    nwd_options.ViewId = element.Id

                    self._write_crash_marker(item.SheetNumber, "NWC")
                    self.doc.Export(output_folder, filename, nwd_options)
                    self._clear_crash_marker()

                    if self._verify_export(expected_file, prev_mtime, started_at, ".nwc"):
                        exported_count += 1
                        self._overall_counter += 1
                        self._update_progress(
                            int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                            "Exported {} to NWC".format(element_name)
                        )
                        self.update_export_item_progress(item.SheetNumber, "NWC", 100)
                    else:
                        self._note_failure(item.SheetNumber, "NWC",
                                           "Revit wrote no NWC file")

                except Exception as ex:
                    logger.error("Error exporting {} to NWC: {}".format(element_name, ex))
                    self._note_failure(str(getattr(item, 'SheetNumber', element_name)),
                                       "NWC", ex)

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
            self._sync_item_labels(items, "IFC")

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
                trans = None
                try:
                    for item in items:
                        self.update_export_item_progress(item.SheetNumber, "IFC", 50, "Exporting...")
                    self._update_progress(
                        int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                        "Exporting entire model to IFC..."
                    )

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
                    filename = self._reserve_filename(output_folder, filename, ".ifc")
                    expected_file = os.path.join(output_folder, filename + ".ifc")
                    before_output = self._native_output_stamp(expected_file)

                    # IFC export needs to be wrapped in a transaction. One native
                    # call for the whole model, so the breadcrumb records the job
                    # rather than a single sheet.
                    self._write_crash_marker(filename, "IFC")
                    trans = Transaction(self.doc, "T3Lab: Export IFC")
                    trans.Start()
                    options = trans.GetFailureHandlingOptions()
                    options.SetForcedModalHandling(True)
                    trans.SetFailureHandlingOptions(options)
                    exported = self.doc.Export(output_folder, filename, ifc_options)
                    self._clear_crash_marker()
                    if not exported:
                        raise RuntimeError("Revit reported that the IFC export failed")
                    status = trans.Commit()
                    if status != DB.TransactionStatus.Committed:
                        raise RuntimeError("IFC transaction was not committed: {}".format(status))
                    if not self._confirm_native_output(expected_file, before_output):
                        raise RuntimeError("No new or updated nonempty IFC output was confirmed")

                    exported_count = 1
                    self._overall_counter += len(items)
                    self._update_progress(
                        int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                        "IFC exported"
                    )
                    for item in items:
                        self.update_export_item_progress(item.SheetNumber, "IFC", 100)
                except Exception as ex:
                    if trans is not None and trans.GetStatus() == DB.TransactionStatus.Pending:
                        raise _PendingExportError(
                            "IFC export is awaiting Revit failure resolution. Later formats were stopped. "
                            "Resolve the Revit dialog before retrying; any output files were kept.")
                    if trans is not None and trans.GetStatus() == DB.TransactionStatus.Started:
                        trans.RollBack()
                    logger.error("Error exporting to IFC: {}".format(ex))
                    for item in items:
                        self._note_failure(getattr(item, 'SheetNumber', 'Unknown'),
                                           "IFC", "model IFC export failed: {}. Any output files were kept.".format(ex))

            return exported_count

        except _PendingExportError:
            raise
        except Exception as ex:
            logger.error("IFC export failed: {}".format(ex))
            return 0

    def export_to_images(self, items, output_folder):
        """Export items (sheets or views) to image format using Revit's native image export with version-aware API usage."""
        try:
            import time
            import glob

            # Sync cached values with live values from Revit
            self._sync_item_labels(items, "IMG")

            exported_count = 0

            for item in items:
                element_name = str(getattr(item, 'SheetNumber', 'Unknown'))
                try:
                    # Get the actual element (sheet or view)
                    element, element_name = self._element_of(item)
                    if element is None:
                        self._note_failure(element_name, "IMG",
                                           "row is neither a sheet nor a view")
                        continue

                    if self._is_known_fatal(item.SheetNumber, "IMG"):
                        if self._skip_fatal:
                            self._note_skipped_fatal(item.SheetNumber, "IMG")
                            continue
                        logger.warning(
                            "'{}' crashed Revit's image exporter before — exporting "
                            "anyway (queued last).".format(item.SheetNumber))

                    self.update_export_item_progress(item.SheetNumber, "IMG", 50, "Exporting...")
                    self._update_progress(
                        int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                        "Exporting {} to Image...".format(element_name)
                    )

                    filename = item.CustomFilename or self.get_export_filename(item)

                    # Remove extension if present
                    if filename.lower().endswith(('.png', '.jpg')):
                        filename = filename.rsplit('.', 1)[0]

                    # Clean filename - remove invalid chars and extra spaces
                    invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
                    for char in invalid_chars:
                        filename = filename.replace(char, '_')
                    filename = filename.strip()

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

                    # Revit picks the shaded or non-shaded file type per view, so
                    # both extensions have to be watched. Globbing only "*.png"
                    # missed every JPEG export: the file stayed under Revit's own
                    # decorated name and the item was reported as not exported.
                    img_exts = set()
                    for fmt in (shaded_fmt, nonshaded_fmt):
                        img_exts.add('.jpg' if fmt == ImageFileType.JPEGLossless else '.png')

                    reserved = filename
                    for ext in sorted(img_exts):
                        reserved = self._reserve_filename(output_folder, reserved, ext)
                    filename = reserved

                    existing_images = set()
                    for ext in img_exts:
                        existing_images |= set(glob.glob(
                            os.path.join(output_folder, "*" + ext)))

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
                    self._write_crash_marker(item.SheetNumber, "IMG")
                    self.doc.ExportImage(img_options)
                    self._clear_crash_marker()

                    # Wait briefly for file system to update
                    time.sleep(0.3)

                    # Get list of image files after export (both extensions)
                    current_images = set()
                    for ext in img_exts:
                        current_images |= set(glob.glob(
                            os.path.join(output_folder, "*" + ext)))
                    new_images = current_images - existing_images

                    # Handle Revit's automatic filename modification: it appends
                    # " - Sheet - A101 - ..." decorations, so rename back to the
                    # requested name — keeping whichever extension Revit chose.
                    expected_file = None
                    if new_images:
                        actual_file = sorted(new_images)[0]
                        actual_ext = os.path.splitext(actual_file)[1].lower()
                        expected_file = os.path.join(output_folder, filename + actual_ext)
                        if os.path.normcase(actual_file) != os.path.normcase(expected_file):
                            try:
                                # Target is the name this run reserved for this
                                # item, so replacing it is the intended overwrite.
                                if os.path.exists(expected_file):
                                    os.remove(expected_file)
                                os.rename(actual_file, expected_file)
                            except Exception as rename_ex:
                                logger.warning("Could not rename {} to {}: {}".format(
                                    os.path.basename(actual_file),
                                    os.path.basename(expected_file),
                                    rename_ex
                                ))
                                expected_file = actual_file

                    if expected_file and os.path.exists(expected_file):
                        exported_count += 1
                        self._overall_counter += 1
                        self._update_progress(
                            int(self._overall_counter * 100.0 / max(1, self._overall_total)),
                            "Exported {} to Image".format(element_name)
                        )
                        self.update_export_item_progress(item.SheetNumber, "IMG", 100)
                    else:
                        self._note_failure(item.SheetNumber, "IMG",
                                           "Revit wrote no image file")

                except Exception as ex:
                    logger.error("Error exporting {} to Image: {}".format(element_name, ex))
                    self._note_failure(str(getattr(item, 'SheetNumber', element_name)),
                                       "IMG", ex)

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
            profile_list.ItemsSource = to_items_source(self.profiles)
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
                profile_list.ItemsSource = to_items_source(self.profiles)

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
                profile_list.ItemsSource = to_items_source(self.profiles)

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


def show_batchout_dialog():
    """Show Export Manager window with automatic modeless/modal handling."""
    if not revit.doc:
        forms.alert("Please open a Revit document first.", exitscript=True)
        return None

    window = ExportManagerWindow()
    if window.modeless:
        window.show(modal=False)
    else:
        logger.info("Persistent engine not active — running modal. "
                    "Reload pyRevit to enable modeless mode.")
        if hasattr(window, 'status_text') and window.status_text is not None:
            window.status_text.Text = ("Modal mode — run pyRevit Reload once to "
                                       "unlock working in Revit alongside this window")
        window.show(modal=True)
    return window
