# -*- coding: utf-8 -*-
"""
Family Loader Dialog
Load Revit families from folders with category organization
COMPREHENSIVE FIX: Memory leaks, threading, error handling, and stability improvements
"""
__title__ = "Family Loader"
__author__ = "T3Lab"

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝ IMPORTS
#====================================================================================================
import os
import sys
import clr
import json
import traceback
import time
import datetime
import threading
try:
    from urllib2 import urlopen, Request, URLError, HTTPError
except ImportError:
    from urllib.request import urlopen, Request
    from urllib.error import URLError, HTTPError
import tempfile

# .NET Imports
clr.AddReference("System")
clr.AddReference("System.Windows.Forms")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

import System
from System import Uri, Action
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Windows import Window, Visibility
from System.Windows.Markup import XamlReader
from System.Windows.Media.Imaging import BitmapImage
from System.Windows.Controls import TreeViewItem
from System.Windows.Forms import FolderBrowserDialog, DialogResult
from System.Windows.Threading import Dispatcher
from System.Windows.Shell import WindowChrome

# pyRevit Imports
from pyrevit import revit, DB, forms, script

# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝ VARIABLES
#====================================================================================================
logger = script.get_logger()
doc = revit.doc
uidoc = revit.uidoc

# Config file path
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".t3lab")
CONFIG_FILE = os.path.join(CONFIG_DIR, "family_loader_config.json")

# Cloud API configuration
# Update this URL to your Vercel deployment URL
CLOUD_API_BASE = "https://t3stu-dojk2t66r-tien-thanh-trans-projects.vercel.app"
CLOUD_API_ENDPOINT = "/api/families"

# Vercel Protection Bypass Token (get from: Settings → Deployment Protection → Protection Bypass)
# Leave empty if no protection is enabled
VERCEL_BYPASS_TOKEN = "1McvpSpOLuCfzLkqAybnPgtxlbAgFv6V"

# Build full URL with bypass token if needed
if VERCEL_BYPASS_TOKEN:
    CLOUD_API_URL = "{}{}?x-vercel-protection-bypass={}".format(
        CLOUD_API_BASE, CLOUD_API_ENDPOINT, VERCEL_BYPASS_TOKEN
    )
else:
    CLOUD_API_URL = "{}{}".format(CLOUD_API_BASE, CLOUD_API_ENDPOINT)

# For local testing, you can use: "http://localhost:3000/api/families"

# Temp folder for downloaded families
TEMP_FAMILIES_DIR = os.path.join(tempfile.gettempdir(), "t3lab_cloud_families")

# Thumbnail cache folder
THUMBNAIL_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".t3lab", "thumbnails")

# Number of families to push to the UI per scan batch (progressive display)
SCAN_BATCH_SIZE = 20

# ╦ ╦╔═╗╦  ╔═╗╔═╗╦═╗  ╔═╗╦ ╦╔╗╔╔═╗╔╦╗╦╔═╗╔╗╔╔═╗
# ╠═╣║╣ ║  ╠═╝║╣ ╠╦╝  ╠╣ ║ ║║║║║   ║ ║║ ║║║║╚═╗
# ╩ ╩╚═╝╩═╝╩  ╚═╝╩╚═  ╚  ╚═╝╝╚╝╚═╝ ╩ ╩╚═╝╝╚╝╚═╝
#====================================================================================================

def load_config():
    """Load configuration from JSON file with validation and corruption recovery"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)

                # Validate config structure
                if not isinstance(config, dict):
                    raise ValueError("Invalid config format: expected dict, got {}".format(type(config)))

                logger.info("Loaded config from: {}".format(CONFIG_FILE))
                return config
        else:
            logger.info("No config file found at: {}".format(CONFIG_FILE))
            return {}
    except (ValueError, json.JSONDecodeError) as ex:
        logger.error("Config file corrupted, recreating: {}".format(ex))
        # Backup corrupted file
        try:
            backup_path = CONFIG_FILE + ".corrupted.{}".format(int(time.time()))
            if os.path.exists(CONFIG_FILE):
                os.rename(CONFIG_FILE, backup_path)
                logger.info("Backed up corrupted config to: {}".format(backup_path))
        except Exception as backup_ex:
            logger.error("Failed to backup corrupted config: {}".format(backup_ex))
        return {}
    except Exception as ex:
        logger.error("Failed to load config: {}".format(ex))
        logger.error(traceback.format_exc())
        return {}

def save_config(config):
    """Save configuration to JSON file"""
    try:
        # Create config directory if it doesn't exist
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR)
            logger.info("Created config directory: {}".format(CONFIG_DIR))

        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        logger.info("Saved config to: {}".format(CONFIG_FILE))
        return True
    except Exception as ex:
        logger.error("Failed to save config: {}".format(ex))
        logger.error(traceback.format_exc())
        return False

def is_valid_rfa_file(file_path):
    """Validate if file is a valid Revit family file"""
    try:
        # Check file exists
        if not os.path.exists(file_path):
            return False

        # Check file size (min 1KB, max 500MB)
        size = os.path.getsize(file_path)
        if size < 1024 or size > 500 * 1024 * 1024:
            logger.debug("File size out of range: {} bytes for {}".format(size, file_path))
            return False

        # Try to open file to check if accessible
        with open(file_path, 'rb') as f:
            # Read header to validate (Revit files are OLE/Structured storage)
            header = f.read(8)
            if not header.startswith(b'\xD0\xCF\x11\xE0'):
                logger.debug("Invalid file header for: {}".format(file_path))
                return False

        return True
    except Exception as ex:
        logger.debug("File validation failed for {}: {}".format(file_path, ex))
        return False

def fetch_cloud_families(api_url):
    """Fetch family data from cloud API"""
    try:
        logger.info("Fetching family data from cloud: {}".format(api_url))

        # Make HTTP request
        request = Request(api_url)
        request.add_header('User-Agent', 'T3Lab Family Loader/1.0')

        response = urlopen(request, timeout=30)
        data = response.read()

        # Parse JSON response
        families_data = json.loads(data)
        logger.info("Successfully fetched cloud family data")

        return families_data
    except HTTPError as ex:
        logger.error("HTTP error fetching cloud families: {} - {}".format(ex.code, ex.reason))

        # Provide specific error messages for different HTTP status codes
        if ex.code == 404:
            error_msg = (
                "API endpoint not found (HTTP 404).\n\n"
                "This usually means:\n"
                "1. The Vercel deployment URL is incorrect or outdated\n"
                "2. The API endpoint doesn't exist at this URL\n"
                "3. The deployment may have been deleted\n\n"
                "Please check:\n"
                "- Verify your Vercel deployment is active\n"
                "- Update CLOUD_API_URL in FamilyLoaderDialog.py\n"
                "- See CLOUD_FAMILY_LOADER_README.md for more details"
            )
        elif ex.code == 401 or ex.code == 403:
            error_msg = (
                "Authentication required (HTTP {}).\n\n"
                "The API requires authentication or the bypass token is invalid.\n\n"
                "Please check:\n"
                "- Verify VERCEL_BYPASS_TOKEN in FamilyLoaderDialog.py\n"
                "- Or disable Vercel Deployment Protection"
            ).format(ex.code)
        elif ex.code == 500:
            error_msg = (
                "Server error (HTTP 500).\n\n"
                "The API server encountered an error.\n"
                "Please check the Vercel deployment logs."
            )
        else:
            error_msg = "Failed to fetch from cloud: HTTP {} - {}".format(ex.code, ex.reason)

        raise Exception(error_msg)
    except URLError as ex:
        logger.error("URL error fetching cloud families: {}".format(ex.reason))
        error_msg = (
            "Failed to connect to cloud API.\n\n"
            "This usually means:\n"
            "1. No internet connection\n"
            "2. The API URL is invalid\n"
            "3. Network firewall is blocking the connection\n\n"
            "Error: {}".format(str(ex.reason))
        )
        raise Exception(error_msg)
    except Exception as ex:
        logger.error("Error fetching cloud families: {}".format(ex))
        logger.error(traceback.format_exc())
        raise

def download_family_file(download_url, save_path):
    """Download a family file from cloud URL"""
    try:
        logger.debug("Downloading family from: {}".format(download_url))

        # Make sure directory exists
        save_dir = os.path.dirname(save_path)
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        # Download file
        request = Request(download_url)
        request.add_header('User-Agent', 'T3Lab Family Loader/1.0')

        response = urlopen(request, timeout=120)

        # Write to file
        with open(save_path, 'wb') as f:
            chunk_size = 8192
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)

        logger.debug("Successfully downloaded to: {}".format(save_path))
        return True
    except Exception as ex:
        logger.error("Error downloading family file: {}".format(ex))
        logger.error(traceback.format_exc())
        return False

def _get_thumbnail_cache_path(rfa_path):
    """Return a deterministic cache file path for a .rfa file based on mtime+size."""
    try:
        import re as _re
        stat = os.stat(rfa_path)
        fname = os.path.splitext(os.path.basename(rfa_path))[0]
        key = "{}_{}_{}.jpg".format(fname, int(stat.st_mtime), stat.st_size)
        key = _re.sub(r'[^a-zA-Z0-9_\-.]', '_', key)
        return os.path.join(THUMBNAIL_CACHE_DIR, key)
    except Exception:
        return None


def _extract_rfa_preview(rfa_path):
    """
    Scan a .rfa (OLE compound document) for embedded image thumbnails.
    Supports JPEG and PNG. Returns the largest image bytes found (>1 KB), or None.
    """
    try:
        with open(rfa_path, 'rb') as f:
            data = f.read()
        candidates = []

        # --- JPEG scan: FF D8 FF ... FF D9 ---
        pos = 0
        while True:
            idx = data.find('\xff\xd8\xff', pos)
            if idx < 0:
                break
            end = data.find('\xff\xd9', idx + 3)
            if end > 0:
                chunk = data[idx:end + 2]
                if len(chunk) > 1024:
                    candidates.append(chunk)
            pos = idx + 3

        # --- PNG scan: 89 50 4E 47 ... 49 45 4E 44 AE 42 60 82 ---
        PNG_SIG  = '\x89PNG\r\n\x1a\n'
        PNG_IEND = 'IEND\xae\x42\x60\x82'
        pos = 0
        while True:
            idx = data.find(PNG_SIG, pos)
            if idx < 0:
                break
            end = data.find(PNG_IEND, idx + 8)
            if end > 0:
                chunk = data[idx:end + 8]
                if len(chunk) > 1024:
                    candidates.append(chunk)
            pos = idx + 8

        if candidates:
            return max(candidates, key=len)
    except Exception as ex:
        logger.debug("_extract_rfa_preview error for {}: {}".format(rfa_path, ex))
    return None


def _bytes_to_bitmap(raw_bytes):
    """Load raw image bytes (JPEG or PNG) into a frozen WPF BitmapImage (thread-safe).
    Uses ISO-8859-1 encoding to reliably convert IronPython str → .NET byte[]."""
    try:
        from System.IO import MemoryStream
        from System.Text import Encoding
        # ISO-8859-1 maps each byte 0x00-0xFF to the same Unicode code point,
        # so it round-trips binary data without loss in IronPython 2.x.
        net_bytes = Encoding.GetEncoding('iso-8859-1').GetBytes(raw_bytes)
        stream = MemoryStream(net_bytes)
        bitmap = BitmapImage()
        bitmap.BeginInit()
        bitmap.StreamSource = stream
        bitmap.DecodePixelWidth = 90
        bitmap.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad
        bitmap.EndInit()
        bitmap.Freeze()
        stream.Close()
        return bitmap
    except Exception as ex:
        logger.debug("_bytes_to_bitmap error: {}".format(ex))
        return None


# ╔═╗╦  ╔═╗╔═╗╔═╗╔═╗╔═╗
# ║  ║  ╠═╣╚═╗╚═╗║╣ ╚═╗
# ╚═╝╩═╝╩ ╩╚═╝╚═╝╚═╝╚═╝ CLASSES
#====================================================================================================

class FamilyLoadOptions(DB.IFamilyLoadOptions):
    """Custom IFamilyLoadOptions to handle family conflicts automatically"""

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        """Handle when family already exists in project"""
        # Always overwrite existing families
        overwriteParameterValues = True
        logger.debug("Family found in project, overwriting: {}".format(
            familyInUse.Name if hasattr(familyInUse, 'Name') else 'Unknown'
        ))
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        """Handle when shared family is found"""
        # Always use the source (file) version
        overwriteParameterValues = True
        source = DB.FamilySource.Family
        logger.debug("Shared family found, using source version")
        return True

class FamilyItem(INotifyPropertyChanged):
    """Represents a family file with its properties"""

    def __init__(self, name, full_path, category, thumbnail_path=None, is_cloud=False, download_url=None):
        self._is_checked = False
        self._is_disposed = False
        self._property_changed_handlers = []
        self._thumbnail = None  # backing field for Thumbnail property
        self.Name = name
        self.FullPath = full_path
        self.Category = category
        self.IsCloud = is_cloud  # Flag to indicate if this is a cloud family
        self.DownloadUrl = download_url  # URL to download the family file
        self.Thumbnail = self._load_thumbnail(thumbnail_path)

    def _load_thumbnail(self, thumbnail_path):
        """Load thumbnail image or return default"""
        try:
            if thumbnail_path and os.path.exists(thumbnail_path):
                bitmap = BitmapImage()
                bitmap.BeginInit()
                bitmap.UriSource = Uri(thumbnail_path)
                bitmap.DecodePixelWidth = 90
                bitmap.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad
                bitmap.EndInit()
                bitmap.Freeze()  # Make bitmap immutable for thread safety and memory optimization
                return bitmap
        except Exception as ex:
            # Silently ignore thumbnail loading errors
            logger.debug("Failed to load thumbnail {}: {}".format(thumbnail_path, ex))
        return None

    @property
    def Thumbnail(self):
        return self._thumbnail

    @Thumbnail.setter
    def Thumbnail(self, value):
        self._thumbnail = value
        self.OnPropertyChanged("Thumbnail")

    @property
    def IsChecked(self):
        return self._is_checked

    @IsChecked.setter
    def IsChecked(self, value):
        if self._is_checked != value:
            self._is_checked = value
            self.OnPropertyChanged("IsChecked")

    def add_PropertyChanged(self, handler):
        """Add PropertyChanged event handler"""
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        """Remove PropertyChanged event handler"""
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, propertyName):
        """Raise PropertyChanged event"""
        try:
            if not self._is_disposed:
                for handler in self._property_changed_handlers:
                    try:
                        handler(self, PropertyChangedEventArgs(propertyName))
                    except Exception as ex:
                        logger.debug("Error calling PropertyChanged handler: {}".format(ex))
        except Exception as ex:
            # Silently ignore PropertyChanged errors
            logger.debug("Error in OnPropertyChanged: {}".format(ex))

    def Dispose(self):
        """Clean up resources to prevent memory leaks"""
        try:
            if not self._is_disposed:
                # Clear thumbnail reference
                self.Thumbnail = None
                # Clear event handlers
                self._property_changed_handlers = []
                self._is_disposed = True
        except Exception as ex:
            logger.debug("Error disposing FamilyItem: {}".format(ex))


class FamilyLoaderWindow(Window):
    """Main window for Family Loader"""

    def __init__(self):
        try:
            logger.info("=" * 80)
            logger.info("DEBUG: Starting FamilyLoaderWindow initialization")
            logger.info("=" * 80)

            # Initialize the base Window class first
            logger.debug("DEBUG: Step 1 - Initializing base Window class")
            Window.__init__(self)
            logger.debug("DEBUG: Step 1 - COMPLETED")

            # Initialize instance variables
            self.config = load_config()
            self.current_folder = None
            self.all_families = []
            self._scan_thread = None
            self._cancel_requested = False
            self._thumb_cancel = False

            # Set Window properties
            logger.debug("DEBUG: Step 2 - Setting window properties")
            self.Title = "Load Autodesk Family"
            self.Height = 680
            self.Width = 960
            self.MinHeight = 500
            self.MinWidth = 760
            self.WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen
            self.Background = System.Windows.Media.SolidColorBrush(
                System.Windows.Media.Color.FromRgb(255, 255, 255))

            # Custom window chrome (no OS title bar)
            # ROOT CAUSE FIX: CaptionHeight=0 so the entire window is client area.
            # With CaptionHeight>0, Windows intercepts mouse events in the caption
            # zone for drag-to-move, and child buttons only receive clicks if
            # WindowChrome.IsHitTestVisibleInChrome is set DIRECTLY on each button
            # (NOT inherited from parent containers). Setting CaptionHeight=0 avoids
            # this entirely. Window dragging is handled via DragMove() in Python.
            try:
                self.WindowStyle = getattr(System.Windows.WindowStyle, 'None')
                chrome = WindowChrome()
                chrome.CaptionHeight = 0
                chrome.ResizeBorderThickness = System.Windows.Thickness(5)
                chrome.GlassFrameThickness = System.Windows.Thickness(0)
                chrome.CornerRadius = System.Windows.CornerRadius(8)
                chrome.UseAeroCaptionButtons = False
                WindowChrome.SetWindowChrome(self, chrome)
                logger.debug("DEBUG: Step 2 - WindowChrome applied")
            except Exception as chrome_ex:
                logger.debug("DEBUG: Step 2 - WindowChrome skipped: {}".format(chrome_ex))

            logger.debug("DEBUG: Step 2 - COMPLETED")

            # Load XAML
            logger.debug("DEBUG: Step 3 - Loading XAML")
            xaml_path = os.path.join(os.path.dirname(__file__), 'Tools', 'FamilyLoader.xaml')
            logger.info("DEBUG: XAML path: {}".format(xaml_path))

            if not os.path.exists(xaml_path):
                error_msg = "XAML file not found at: {}".format(xaml_path)
                logger.error("DEBUG: ERROR - {}".format(error_msg))
                forms.alert(error_msg, exitscript=True)
                raise IOError(error_msg)

            try:
                logger.debug("DEBUG: Step 3a - Reading XAML file")
                import io
                with io.open(xaml_path, 'r', encoding='utf-8') as f:
                    xaml_content = f.read()
                logger.debug("DEBUG: Step 3a - COMPLETED (read {} bytes)".format(len(xaml_content)))

                logger.debug("DEBUG: Step 3b - Parsing XAML content")
                self.ui = XamlReader.Parse(xaml_content)
                logger.debug("DEBUG: Step 3b - COMPLETED")

                logger.debug("DEBUG: Step 3c - Setting Content")
                self.Content = self.ui
                logger.debug("DEBUG: Step 3c - COMPLETED")
                logger.info("DEBUG: Step 3 - XAML loaded successfully")
            except Exception as e:
                logger.error("DEBUG: ERROR in Step 3 - XAML loading: {}".format(str(e)))
                logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
                forms.alert("Error loading XAML file:\n{}\n\nPath: {}".format(str(e), xaml_path), exitscript=True)
                raise

            # Get named controls
            logger.debug("DEBUG: Step 4 - Getting named controls from XAML")
            try:
                logger.debug("DEBUG: Step 4a - Finding btn_select_folder")
                self.btn_select_folder = self.ui.FindName('btn_select_folder')
                if not self.btn_select_folder:
                    raise Exception("btn_select_folder not found in XAML")

                logger.debug("DEBUG: Step 4b - Finding txt_current_folder")
                self.txt_current_folder = self.ui.FindName('txt_current_folder')
                if not self.txt_current_folder:
                    raise Exception("txt_current_folder not found in XAML")

                logger.debug("DEBUG: Step 4c - Finding txt_search")
                self.txt_search = self.ui.FindName('txt_search')
                if not self.txt_search:
                    raise Exception("txt_search not found in XAML")

                logger.debug("DEBUG: Step 4d - Finding tree_categories")
                self.tree_categories = self.ui.FindName('tree_categories')
                if not self.tree_categories:
                    raise Exception("tree_categories not found in XAML")

                logger.debug("DEBUG: Step 4e - Finding items_families")
                self.items_families = self.ui.FindName('items_families')
                if not self.items_families:
                    raise Exception("items_families not found in XAML")

                logger.debug("DEBUG: Step 4f - Finding txt_result_count")
                self.txt_result_count = self.ui.FindName('txt_result_count')
                if not self.txt_result_count:
                    raise Exception("txt_result_count not found in XAML")

                logger.debug("DEBUG: Step 4g - Finding txt_selected_count")
                self.txt_selected_count = self.ui.FindName('txt_selected_count')
                if not self.txt_selected_count:
                    raise Exception("txt_selected_count not found in XAML")

                logger.debug("DEBUG: Step 4h - Finding btn_select_all")
                self.btn_select_all = self.ui.FindName('btn_select_all')
                if not self.btn_select_all:
                    raise Exception("btn_select_all not found in XAML")

                logger.debug("DEBUG: Step 4i - Finding btn_select_none")
                self.btn_select_none = self.ui.FindName('btn_select_none')
                if not self.btn_select_none:
                    raise Exception("btn_select_none not found in XAML")

                logger.debug("DEBUG: Step 4j - Finding btn_load")
                self.btn_load = self.ui.FindName('btn_load')
                if not self.btn_load:
                    raise Exception("btn_load not found in XAML")

                logger.debug("DEBUG: Step 4k - Finding btn_cancel")
                self.btn_cancel = self.ui.FindName('btn_cancel')
                if not self.btn_cancel:
                    raise Exception("btn_cancel not found in XAML")

                logger.debug("DEBUG: Step 4l - Finding radio_local")
                self.radio_local = self.ui.FindName('radio_local')
                if not self.radio_local:
                    raise Exception("radio_local not found in XAML")

                logger.debug("DEBUG: Step 4m - Finding radio_cloud")
                self.radio_cloud = self.ui.FindName('radio_cloud')
                if not self.radio_cloud:
                    raise Exception("radio_cloud not found in XAML")

                # Title bar controls (optional, no error if missing)
                logger.debug("DEBUG: Step 4n - Finding title bar controls")
                self.logo_image = self.ui.FindName('logo_image')
                self.btn_minimize_fl = self.ui.FindName('btn_minimize_fl')
                self.btn_close_x_fl = self.ui.FindName('btn_close_x_fl')
                self.title_bar_grid = self.ui.FindName('title_bar_grid')

                logger.debug("DEBUG: Step 4 - COMPLETED - All controls found")
            except Exception as ctrl_ex:
                logger.error("DEBUG: ERROR in Step 4 - Finding controls: {}".format(str(ctrl_ex)))
                logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
                forms.alert("Error finding UI controls in XAML:\n{}\n\nPlease check FamilyLoader.xaml file".format(str(ctrl_ex)), exitscript=True)
                raise

            # Wire up event handlers
            logger.debug("DEBUG: Step 5 - Wiring up event handlers")
            try:
                logger.debug("DEBUG: Step 5a - btn_select_folder.Click")
                self.btn_select_folder.Click += self.select_folder_clicked

                logger.debug("DEBUG: Step 5b - txt_search.TextChanged")
                self.txt_search.TextChanged += self.search_text_changed

                logger.debug("DEBUG: Step 5c - tree_categories.SelectedItemChanged")
                self.tree_categories.SelectedItemChanged += self.category_selected

                logger.debug("DEBUG: Step 5d - btn_select_all.Click")
                self.btn_select_all.Click += self.select_all_clicked

                logger.debug("DEBUG: Step 5e - btn_select_none.Click")
                self.btn_select_none.Click += self.select_none_clicked

                logger.debug("DEBUG: Step 5f - btn_load.Click")
                self.btn_load.Click += self.load_clicked

                logger.debug("DEBUG: Step 5g - btn_cancel.Click")
                self.btn_cancel.Click += self.cancel_clicked

                logger.debug("DEBUG: Step 5h - radio_local.Checked")
                self.radio_local.Checked += self.data_source_changed

                logger.debug("DEBUG: Step 5i - radio_cloud.Checked")
                self.radio_cloud.Checked += self.data_source_changed

                logger.debug("DEBUG: Step 5j - window.Loaded")
                self.Loaded += self.window_loaded

                # Title bar buttons
                if self.btn_minimize_fl:
                    self.btn_minimize_fl.Click += self.titlebar_minimize_clicked
                if self.btn_close_x_fl:
                    self.btn_close_x_fl.Click += self.cancel_clicked
                if self.title_bar_grid:
                    self.title_bar_grid.MouseLeftButtonDown += self.titlebar_drag

                logger.debug("DEBUG: Step 5 - COMPLETED")
            except Exception as event_ex:
                logger.error("DEBUG: ERROR in Step 5 - Wiring event handlers: {}".format(str(event_ex)))
                logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
                forms.alert("Error wiring event handlers:\n{}".format(str(event_ex)), exitscript=True)
                raise

            logger.info("=" * 80)
            logger.info("DEBUG: FamilyLoaderWindow initialization completed")
            logger.info("=" * 80)

        except Exception as ex:
            logger.error("=" * 80)
            logger.error("DEBUG: ERROR in __init__: {}".format(ex))
            logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
            logger.error("=" * 80)
            # Don't re-raise - allow window to continue loading

    def window_loaded(self, sender, e):
        """Handle Window.Loaded event - restore saved folder and initialize UI"""
        try:
            logger.info("=" * 80)
            logger.info("DEBUG: window_loaded event fired")
            saved_folder = self.config.get('last_folder', '')
            if saved_folder:
                if os.path.exists(saved_folder):
                    self.current_folder = saved_folder
                    self.txt_current_folder.Text = saved_folder
                    logger.info("DEBUG: Restored saved folder: {}".format(saved_folder))
                    self.scan_families()
                else:
                    self.current_folder = None
                    self.txt_current_folder.Text = "Saved folder no longer exists. Click 'Update Folder' to select a new folder."
                    logger.info("DEBUG: Saved folder no longer exists: {}".format(saved_folder))
            else:
                self.txt_current_folder.Text = "Click 'Update Folder' to select a folder or switch to Cloud mode"
                logger.info("DEBUG: No saved folder found")
            logger.info("=" * 80)
        except Exception as ex:
            logger.error("ERROR in window_loaded: {}".format(ex))
            logger.error(traceback.format_exc())

    def data_source_changed(self, sender, e):
        """Handle data source toggle between Local and Cloud"""
        try:
            if self.radio_cloud.IsChecked:
                logger.info("Switched to Cloud mode")
                self.txt_current_folder.Text = "Loading from Cloud (Vercel)..."
                self.btn_select_folder.IsEnabled = False
                self.load_cloud_families()
            else:
                logger.info("Switched to Local mode")
                self.btn_select_folder.IsEnabled = True
                if self.current_folder:
                    self.txt_current_folder.Text = self.current_folder
                    self.scan_families()
                else:
                    self.txt_current_folder.Text = "No folder selected"
        except Exception as ex:
            logger.error("Error in data_source_changed: {}".format(ex))
            logger.error(traceback.format_exc())
            forms.alert("Error changing data source: {}".format(ex), exitscript=False)

    def select_folder_clicked(self, sender, e):
        """Handle folder selection"""
        try:
            dialog = FolderBrowserDialog()
            dialog.Description = "Select folder containing Revit families"

            if dialog.ShowDialog() == DialogResult.OK:
                self.current_folder = dialog.SelectedPath
                self.txt_current_folder.Text = self.current_folder
                logger.info("User selected folder: {}".format(self.current_folder))

                # Save folder to config
                self.config['last_folder'] = self.current_folder
                if save_config(self.config):
                    logger.info("Folder path saved to config")

                self.scan_families()
        except Exception as ex:
            logger.error("Error in select_folder_clicked: {}".format(ex))
            logger.error(traceback.format_exc())
            forms.alert("Error selecting folder: {}".format(ex), exitscript=False)

    def load_cloud_families(self):
        """Load families from cloud API"""
        try:
            # Disable UI controls during load
            self.btn_load.IsEnabled = False
            self.txt_current_folder.Text = "Loading from Cloud (Vercel)..."

            logger.info("=" * 80)
            logger.info("CLOUD FAMILY LOAD STARTED: {}".format(datetime.datetime.now()))
            logger.info("API URL: {}".format(CLOUD_API_URL))
            logger.info("=" * 80)

            # Start background thread for cloud loading
            self._scan_thread = threading.Thread(target=self._load_cloud_families_worker)
            self._scan_thread.daemon = True
            self._scan_thread.start()

        except Exception as ex:
            logger.error("Error in load_cloud_families: {}".format(ex))
            logger.error(traceback.format_exc())
            forms.alert("Error loading from cloud: {}".format(ex), exitscript=False)

    def _load_cloud_families_worker(self):
        """Background worker for loading cloud families"""
        temp_families = []
        temp_category_structure = {}

        try:
            # Fetch data from cloud API
            families_data = fetch_cloud_families(CLOUD_API_URL)

            # Process categories and families
            for category_data in families_data.get('categories', []):
                category_name = category_data.get('name', 'Unknown')
                category_path = category_data.get('path', category_name)

                for family_data in category_data.get('families', []):
                    # Create family item with cloud data
                    family_name = family_data.get('name', 'Unknown')
                    file_name = family_data.get('fileName', '{}.rfa'.format(family_name))
                    download_url = family_data.get('downloadUrl', '')
                    thumbnail_url = family_data.get('thumbnailUrl', None)

                    # For cloud families, use temp directory path
                    temp_path = os.path.join(TEMP_FAMILIES_DIR, category_name, file_name)

                    # Create family item
                    family_item = FamilyItem(
                        name=family_name,
                        full_path=temp_path,
                        category=category_path,
                        thumbnail_path=thumbnail_url,
                        is_cloud=True,
                        download_url=download_url
                    )

                    temp_families.append(family_item)

                    # Add to category structure
                    if category_path not in temp_category_structure:
                        temp_category_structure[category_path] = []
                    temp_category_structure[category_path].append(family_item)

            logger.info("Loaded {} cloud families in {} categories".format(
                len(temp_families),
                len(temp_category_structure)
            ))

            # Complete load on UI thread
            self._scan_complete(temp_families, temp_category_structure)

        except Exception as ex:
            logger.error("Error loading cloud families: {}".format(ex))
            logger.error(traceback.format_exc())
            self._scan_complete([], {}, error=str(ex))

    def scan_families(self):
        """Scan selected folder for .rfa files with background threading"""
        if not self.current_folder:
            logger.warning("No current folder set for scanning")
            return

        # Cancel any running thumbnail worker before clearing data
        self._thumb_cancel = True

        # Reset cancellation flag
        self._cancel_requested = False

        # Clear previous results immediately so the UI feels responsive
        self._clear_families_ui()

        # Disable UI controls during scan
        self.btn_select_folder.IsEnabled = False
        self.btn_load.IsEnabled = False
        self.txt_current_folder.Text = "{} (Scanning...)".format(self.current_folder)

        logger.info("=" * 80)
        logger.info("FAMILY SCAN STARTED: {}".format(datetime.datetime.now()))
        logger.info("Folder: {}".format(self.current_folder))
        logger.info("=" * 80)

        # Start background scan thread
        self._scan_thread = threading.Thread(target=self._scan_families_worker)
        self._scan_thread.daemon = True
        self._scan_thread.start()

    def _clear_families_ui(self):
        """Clear all families from the UI and internal lists (call on UI thread)."""
        try:
            for old_family in list(self.all_families):
                try:
                    old_family.PropertyChanged -= self.on_family_property_changed
                except Exception:
                    pass
                if hasattr(old_family, 'Dispose'):
                    old_family.Dispose()
            self.all_families = []
            self.filtered_families.Clear()
            self.category_structure = {}
            self.tree_categories.Items.Clear()
            self.txt_result_count.Text = "0 families found"
            self.txt_selected_count.Text = "0 families selected"
            self.btn_load.IsEnabled = False
        except Exception as ex:
            logger.debug("Error clearing families UI: {}".format(ex))

    def _scan_families_worker(self):
        """Background worker for scanning families — pushes results to UI progressively."""
        start_time = time.time()
        scan_errors = 0
        permission_errors = 0
        validation_errors = 0
        timeout_seconds = 300  # 5 minutes timeout

        temp_category_structure = {}
        temp_seen_names = {}
        pending_batch = []   # accumulate families before pushing to UI
        total_found = 0

        try:
            logger.info("Walking through directory structure...")

            # Walk through directory with error handling
            for root, dirs, files in os.walk(self.current_folder, followlinks=False):
                # Check for cancellation
                if self._cancel_requested:
                    logger.info("Scan cancelled by user")
                    self._scan_complete(None, temp_category_structure, cancelled=True)
                    return

                # Check for timeout
                if time.time() - start_time > timeout_seconds:
                    logger.error("Scan timeout after {} seconds".format(timeout_seconds))
                    self._scan_complete(None, temp_category_structure, timeout=True)
                    return

                # Test directory accessibility
                try:
                    _ = os.listdir(root)
                except (PermissionError, OSError) as access_ex:
                    logger.warning("Skipping inaccessible folder {}: {}".format(root, access_ex))
                    permission_errors += 1
                    dirs[:] = []
                    continue

                # Process files
                for file in files:
                    if self._cancel_requested:
                        logger.info("Scan cancelled by user")
                        # Push remaining batch before stopping
                        if pending_batch:
                            self._push_family_batch(list(pending_batch))
                            pending_batch = []
                        self._scan_complete(None, temp_category_structure, cancelled=True)
                        return

                    if file.lower().endswith('.rfa'):
                        try:
                            full_path = os.path.join(root, file)
                            relative_path = os.path.relpath(root, self.current_folder)

                            # Validate file
                            if not is_valid_rfa_file(full_path):
                                logger.debug("Skipping invalid .rfa file: {}".format(full_path))
                                validation_errors += 1
                                continue

                            # Use folder name as category
                            category = relative_path if relative_path != '.' else 'Root'

                            # Create family name with duplicate detection
                            family_name = os.path.splitext(file)[0]
                            if family_name in temp_seen_names:
                                logger.warning("Duplicate family name: {} in {} and {}".format(
                                    family_name,
                                    temp_seen_names[family_name],
                                    full_path
                                ))
                                folder_name = os.path.basename(root)
                                family_name = "{} ({})".format(family_name, folder_name)
                            else:
                                temp_seen_names[family_name] = full_path

                            # Create family item (no thumbnail yet — shown as placeholder)
                            family_item = FamilyItem(family_name, full_path, category)
                            pending_batch.append(family_item)
                            total_found += 1

                            # Add to category structure
                            if category not in temp_category_structure:
                                temp_category_structure[category] = []
                            temp_category_structure[category].append(family_item)

                            # Push batch to UI immediately so names appear right away
                            if len(pending_batch) >= SCAN_BATCH_SIZE:
                                self._push_family_batch(list(pending_batch))
                                pending_batch = []

                        except Exception as item_ex:
                            scan_errors += 1
                            logger.warning("Failed to process family {}: {}".format(file, item_ex))
                            logger.debug(traceback.format_exc())

            # Push any remaining families
            if pending_batch:
                self._push_family_batch(list(pending_batch))

            duration = time.time() - start_time
            logger.info("Directory walk completed in {:.2f} seconds".format(duration))
            logger.info("Found {} families in {} categories".format(
                total_found, len(temp_category_structure)
            ))

            if scan_errors > 0:
                logger.warning("Encountered {} file processing errors".format(scan_errors))
            if permission_errors > 0:
                logger.warning("Encountered {} permission errors (folders skipped)".format(permission_errors))
            if validation_errors > 0:
                logger.warning("Skipped {} invalid .rfa files".format(validation_errors))

            # Finalize: update category tree, re-enable UI, start thumbnail worker
            # Pass None for families — all_families was already built incrementally
            self._scan_complete(None, temp_category_structure)

        except Exception as ex:
            logger.error("Critical error in scan worker: {}".format(ex))
            logger.error(traceback.format_exc())
            self._scan_complete(None, temp_category_structure, error=str(ex))

    def _push_family_batch(self, batch):
        """Dispatch a batch of FamilyItems to the UI thread for immediate display."""
        try:
            if self.Dispatcher:
                self.Dispatcher.Invoke(
                    Action(lambda: self._push_family_batch_ui(batch))
                )
        except Exception as ex:
            logger.debug("Error pushing family batch: {}".format(ex))

    def _push_family_batch_ui(self, batch):
        """Add a batch of FamilyItems to the live display (UI thread)."""
        try:
            for family in batch:
                self.all_families.append(family)
                try:
                    family.PropertyChanged += self.on_family_property_changed
                except Exception:
                    pass
                self.filtered_families.Add(family)
            count = len(self.filtered_families)
            self.txt_result_count.Text = "{} families found...".format(count)
            self.txt_current_folder.Text = "{} (Scanning... {} found)".format(
                self.current_folder, count
            )
        except Exception as ex:
            logger.debug("Error in _push_family_batch_ui: {}".format(ex))

    def _update_scan_progress(self, count):
        """Update scan progress on UI thread"""
        try:
            if self.Dispatcher:
                self.Dispatcher.Invoke(
                    Action(lambda: self._update_scan_progress_ui(count))
                )
        except Exception as ex:
            logger.debug("Error updating progress: {}".format(ex))

    def _update_scan_progress_ui(self, count):
        """Update progress UI (called on UI thread)"""
        try:
            self.txt_current_folder.Text = "{} (Scanning... {} families found)".format(
                self.current_folder, count
            )
        except Exception as ex:
            logger.debug("Error updating progress UI: {}".format(ex))

    def _scan_complete(self, families, category_structure, error=None, cancelled=False, timeout=False):
        """Handle scan completion on UI thread"""
        try:
            if self.Dispatcher:
                self.Dispatcher.Invoke(
                    Action(lambda: self._scan_complete_ui(families, category_structure, error, cancelled, timeout))
                )
        except Exception as ex:
            logger.error("Error invoking scan complete: {}".format(ex))

    def _scan_complete_ui(self, families, category_structure, error=None, cancelled=False, timeout=False):
        """Complete scan and update UI (called on UI thread).

        families=None  → incremental local scan: all_families was built progressively,
                         just update category_structure + finalize UI.
        families=[...] → bulk mode (cloud load): replace all_families entirely.
        """
        try:
            if families is not None:
                # Bulk path (cloud): replace everything
                for old_family in self.all_families:
                    if hasattr(old_family, 'Dispose'):
                        old_family.Dispose()
                self.all_families = families
                self.category_structure = category_structure
            else:
                # Incremental path: families were pushed to self.all_families already
                self.category_structure = category_structure

            # Re-enable UI
            if self.radio_cloud.IsChecked:
                self.txt_current_folder.Text = "Cloud (Vercel) - {} families loaded".format(len(self.all_families))
            else:
                self.btn_select_folder.IsEnabled = True
                self.txt_current_folder.Text = self.current_folder

            # Handle different completion states
            if error:
                logger.error("Scan failed with error: {}".format(error))
                self.txt_result_count.Text = "{} families found".format(len(self.all_families))
                forms.alert("Error scanning folder: {}".format(error), exitscript=False)
            elif cancelled:
                logger.info("Scan cancelled by user")
                self.txt_result_count.Text = "{} families found (cancelled)".format(len(self.all_families))
                forms.alert("Scan cancelled", exitscript=False)
            elif timeout:
                logger.error("Scan timeout")
                self.txt_result_count.Text = "{} families found (timeout)".format(len(self.all_families))
                forms.alert("Scan timeout: Operation took too long (>5 minutes)", exitscript=False)
            else:
                # Update category tree now that we have the complete structure
                logger.info("Updating category tree...")
                self.update_category_tree()

                if families is not None:
                    # Bulk path: need to populate display from scratch
                    logger.info("Updating family display...")
                    self.update_family_display()
                else:
                    # Incremental path: filtered_families already populated; just refresh count
                    self.txt_result_count.Text = "{} families found".format(len(self.all_families))

                # Start background thumbnail loading (Phase 2)
                self._thumb_cancel = False
                self._start_thumbnail_worker()

                logger.info("=" * 80)
                logger.info("FAMILY SCAN COMPLETED: {}".format(datetime.datetime.now()))
                logger.info("Total families: {}".format(len(self.all_families)))
                logger.info("Total categories: {}".format(len(self.category_structure)))
                logger.info("=" * 80)

        except Exception as ex:
            logger.error("Critical error in scan complete UI: {}".format(ex))
            logger.error(traceback.format_exc())
            self.btn_select_folder.IsEnabled = True
            self.txt_current_folder.Text = self.current_folder
            forms.alert("Error completing scan: {}".format(ex), exitscript=False)

    def update_category_tree(self):
        """Update the category tree view with hierarchical structure"""
        try:
            self.tree_categories.Items.Clear()

            # Add "All" item
            all_item = TreeViewItem()
            all_item.Header = "All ({})".format(len(self.all_families))
            all_item.Tag = "ALL"
            all_item.IsExpanded = True
            self.tree_categories.Items.Add(all_item)

            # Build hierarchical tree structure
            tree_dict = {}

            for category, families in self.category_structure.items():
                # Split category path
                if category == 'Root':
                    parts = ['Root']
                else:
                    parts = category.split(os.sep)

                # Build nested structure
                current_dict = tree_dict
                for i, part in enumerate(parts):
                    if part not in current_dict:
                        current_dict[part] = {'_families': [], '_children': {}}
                    current_dict = current_dict[part]['_children']

                # Add families to the leaf
                path_key = os.sep.join(parts) if parts != ['Root'] else 'Root'
                if path_key in self.category_structure:
                    tree_dict_leaf = tree_dict
                    for part in parts:
                        tree_dict_leaf = tree_dict_leaf[part]
                    tree_dict_leaf['_families'] = self.category_structure[path_key]

            # Recursively add tree items
            def add_tree_items(parent_item, tree_data, path_prefix=""):
                for folder_name, data in sorted(tree_data.items()):
                    folder_path = os.path.join(path_prefix, folder_name) if path_prefix else folder_name

                    # Count all families in this folder and subfolders
                    total_families = self._count_families_in_tree(data)

                    # Create tree item
                    item = TreeViewItem()
                    item.Header = "{} ({})".format(folder_name, total_families)
                    item.Tag = folder_path if folder_path != 'Root' else 'Root'
                    item.IsExpanded = True
                    parent_item.Items.Add(item)

                    # Add children recursively
                    if data['_children']:
                        add_tree_items(item, data['_children'], folder_path)

            add_tree_items(self.tree_categories, tree_dict)
            logger.debug("Category tree updated with {} categories".format(len(self.category_structure)))
        except Exception as ex:
            logger.error("Error updating category tree: {}".format(ex))
            logger.error(traceback.format_exc())

    def _count_families_in_tree(self, tree_node):
        """Count all families in a tree node and its children"""
        count = len(tree_node.get('_families', []))
        for child in tree_node.get('_children', {}).values():
            count += self._count_families_in_tree(child)
        return count

    def update_family_display(self, families=None):
        """Update the family display grid with proper event cleanup"""
        try:
            if families is None:
                families = self.all_families

            # Unsubscribe old events to prevent memory leaks
            for old_family in self.filtered_families:
                try:
                    old_family.PropertyChanged -= self.on_family_property_changed
                except Exception:
                    pass  # Ignore if not subscribed

            # Clear collection
            self.filtered_families.Clear()

            # Add new families and subscribe events
            for family in families:
                # Subscribe to PropertyChanged event to update count when checkbox changes
                try:
                    family.PropertyChanged += self.on_family_property_changed
                except Exception:
                    pass  # Ignore if already subscribed
                self.filtered_families.Add(family)

            self.update_result_count()
            logger.debug("Family display updated with {} families".format(len(families)))
        except Exception as ex:
            logger.error("Error updating family display: {}".format(ex))
            logger.error(traceback.format_exc())

    def on_family_property_changed(self, sender, e):
        """Handle property changed event from family items"""
        try:
            if e.PropertyName == "IsChecked" and not self._is_updating:
                self.update_result_count()
        except Exception as ex:
            logger.debug("Error in on_family_property_changed: {}".format(ex))

    def update_result_count(self):
        """Update the result count text"""
        # Skip updates during batch operations
        if self._is_updating:
            return

        try:
            count = len(self.filtered_families)
            self.txt_result_count.Text = "{} families found".format(count)

            # Update selected count
            selected = sum(1 for f in self.filtered_families if f.IsChecked)
            self.txt_selected_count.Text = "{} families selected".format(selected)

            # Enable/disable load button
            self.btn_load.IsEnabled = selected > 0
        except Exception as ex:
            logger.error("Error updating result count: {}".format(ex))
            logger.error(traceback.format_exc())

    def category_selected(self, sender, e):
        """Handle category selection"""
        try:
            selected_item = self.tree_categories.SelectedItem
            if not selected_item:
                return

            tag = selected_item.Tag

            if tag == "ALL":
                self.update_family_display(self.all_families)
            else:
                # Show families in selected folder and all subfolders
                filtered = [f for f in self.all_families
                           if f.Category == tag or f.Category.startswith(tag + os.sep)]
                self.update_family_display(filtered)
                logger.debug("Category selected: {} ({} families)".format(tag, len(filtered)))
        except Exception as ex:
            logger.error("Error in category_selected: {}".format(ex))
            logger.error(traceback.format_exc())

    def search_text_changed(self, sender, e):
        """Handle search text changes"""
        try:
            search_text = self.txt_search.Text.lower()

            if not search_text:
                # Get current category selection
                selected_item = self.tree_categories.SelectedItem
                if selected_item and selected_item.Tag != "ALL":
                    filtered = [f for f in self.all_families if f.Category == selected_item.Tag]
                    self.update_family_display(filtered)
                else:
                    self.update_family_display(self.all_families)
            else:
                # Filter by search text
                filtered = [f for f in self.all_families
                           if search_text in f.Name.lower() or
                              search_text in f.Category.lower()]
                self.update_family_display(filtered)
                logger.debug("Search: '{}' found {} families".format(search_text, len(filtered)))
        except Exception as ex:
            logger.error("Error in search_text_changed: {}".format(ex))
            logger.error(traceback.format_exc())

    def select_all_clicked(self, sender, e):
        """Select all families"""
        try:
            for family in self.filtered_families:
                family.IsChecked = True
            self.update_result_count()
            logger.debug("Selected all {} families".format(len(self.filtered_families)))
        except Exception as ex:
            logger.error("Error in select_all_clicked: {}".format(ex))
            logger.error(traceback.format_exc())

    def select_none_clicked(self, sender, e):
        """Deselect all families"""
        try:
            for family in self.filtered_families:
                family.IsChecked = False
            self.update_result_count()
            logger.debug("Deselected all families")
        except Exception as ex:
            logger.error("Error in select_none_clicked: {}".format(ex))
            logger.error(traceback.format_exc())

    def load_clicked(self, sender, e):
        """Load selected families into Revit with comprehensive error handling"""
        try:
            logger.info("=" * 80)
            logger.info("DEBUG: load_clicked triggered")
            logger.info("=" * 80)

            logger.debug("DEBUG: Getting selected families")
            selected_families = [f for f in self.all_families if f.IsChecked]
            logger.info("DEBUG: Found {} selected families".format(len(selected_families)))

            if not selected_families:
                logger.warning("DEBUG: No families selected")
                forms.alert("Please select at least one family to load.", exitscript=False)
                return

            # Validate document state
            logger.debug("DEBUG: Validating document state")
            try:
                logger.debug("DEBUG: Checking doc.IsReadOnly")
                if doc.IsReadOnly:
                    logger.warning("DEBUG: Document is read-only")
                    forms.alert("Cannot load families: Document is read-only.\nPlease open a modifiable document.", exitscript=False)
                    return

                logger.debug("DEBUG: Checking doc.IsModifiable")
                if doc.IsModifiable:
                    logger.warning("DEBUG: Document is currently being modified")
                    forms.alert("Cannot load families: Document is currently being modified.\nPlease finish current operation first.", exitscript=False)
                    return

                logger.debug("DEBUG: Checking if document is workshared")
                # Warn if document is workshared
                if doc.IsWorkshared and not doc.IsDetached:
                    logger.info("DEBUG: Document is workshared, showing warning")
                    result = forms.alert(
                        "Document is workshared. Families will be loaded to central model.\n\nDo you want to continue?",
                        yes=True, no=True, exitscript=False
                    )
                    if not result:
                        logger.info("DEBUG: User cancelled loading due to workshared warning")
                        return

                logger.debug("DEBUG: Document validation completed successfully")
            except Exception as doc_ex:
                logger.error("DEBUG: ERROR during document validation: {}".format(doc_ex))
                logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
                forms.alert("Error validating document state:\n{}".format(str(doc_ex)), exitscript=False)
                return

            logger.info("=" * 80)
            logger.info("DEBUG: FAMILY LOADING STARTED: {}".format(datetime.datetime.now()))
            logger.info("DEBUG: Selected families: {}".format(len(selected_families)))
            logger.info("=" * 80)

            start_time = time.time()

            # Disable UI during load
            logger.debug("DEBUG: Disabling UI controls")
            self.btn_load.IsEnabled = False
            self.btn_cancel.IsEnabled = False

            # Load families with individual transactions
            success_count = 0
            fail_count = 0
            failed_families = []

            logger.debug("DEBUG: Creating FamilyLoadOptions")
            try:
                load_options = FamilyLoadOptions()
                logger.debug("DEBUG: FamilyLoadOptions created successfully")
            except Exception as opt_ex:
                logger.error("DEBUG: ERROR creating FamilyLoadOptions: {}".format(opt_ex))
                logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
                forms.alert("Error creating family load options:\n{}".format(str(opt_ex)), exitscript=False)
                self.btn_load.IsEnabled = True
                self.btn_cancel.IsEnabled = True
                return

            logger.info("DEBUG: Starting to load {} families".format(len(selected_families)))

            for i, family in enumerate(selected_families):
                try:
                    logger.info("=" * 40)
                    logger.info("DEBUG: [{}/{}] Processing: {}".format(
                        i + 1, len(selected_families), family.Name
                    ))
                    logger.info("DEBUG: Path: {}".format(family.FullPath))
                    logger.info("=" * 40)

                    # If this is a cloud family, download it first
                    if family.IsCloud:
                        logger.debug("DEBUG: This is a cloud family, checking download URL")
                        if not family.DownloadUrl:
                            logger.error("DEBUG: Cloud family has no download URL: {}".format(family.Name))
                            fail_count += 1
                            failed_families.append((family.Name, "No download URL"))
                            continue

                        # Download the family file
                        logger.info("DEBUG: Downloading cloud family: {}".format(family.Name))
                        logger.info("DEBUG: Download URL: {}".format(family.DownloadUrl))
                        try:
                            if not download_family_file(family.DownloadUrl, family.FullPath):
                                logger.error("DEBUG: Failed to download cloud family: {}".format(family.Name))
                                fail_count += 1
                                failed_families.append((family.Name, "Download failed"))
                                continue
                            logger.info("DEBUG: Download completed successfully")
                        except Exception as download_ex:
                            logger.error("DEBUG: Exception during download: {}".format(download_ex))
                            logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
                            fail_count += 1
                            failed_families.append((family.Name, "Download exception: {}".format(str(download_ex)[:30])))
                            continue

                    # Check if file exists and is valid
                    logger.debug("DEBUG: Checking if file exists: {}".format(family.FullPath))
                    if not os.path.exists(family.FullPath):
                        logger.error("DEBUG: Family file not found: {}".format(family.FullPath))
                        fail_count += 1
                        failed_families.append((family.Name, "File not found"))
                        continue

                    logger.debug("DEBUG: File exists, validating .rfa file")
                    try:
                        if not is_valid_rfa_file(family.FullPath):
                            logger.error("DEBUG: Invalid .rfa file: {}".format(family.FullPath))
                            fail_count += 1
                            failed_families.append((family.Name, "Invalid file format"))
                            continue
                        logger.debug("DEBUG: File validation passed")
                    except Exception as valid_ex:
                        logger.error("DEBUG: Exception during file validation: {}".format(valid_ex))
                        logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
                        fail_count += 1
                        failed_families.append((family.Name, "Validation error"))
                        continue

                    # Use individual transaction for each family
                    # This prevents one failure from rolling back all others
                    logger.debug("DEBUG: Starting transaction for: {}".format(family.Name))
                    try:
                        with revit.Transaction("Load Family: {}".format(family.Name)):
                            try:
                                logger.debug("DEBUG: Calling doc.LoadFamily()")
                                # Load family with options to handle conflicts
                                loaded = doc.LoadFamily(family.FullPath, load_options)
                                logger.debug("DEBUG: doc.LoadFamily() returned: {}".format(loaded))

                                if loaded:
                                    success_count += 1
                                    self.loaded_families.append(family.FullPath)
                                    logger.info("DEBUG: [{}/{}] Successfully loaded: {}".format(
                                        i + 1, len(selected_families), family.Name
                                    ))
                                else:
                                    fail_count += 1
                                    failed_families.append((family.Name, "LoadFamily returned False"))
                                    logger.warning("DEBUG: [{}/{}] LoadFamily returned False for: {}".format(
                                        i + 1, len(selected_families), family.Name
                                    ))

                            except DB.InvalidOperationException as inv_ex:
                                fail_count += 1
                                error_msg = "Invalid operation: {}".format(str(inv_ex))
                                failed_families.append((family.Name, error_msg[:50]))
                                logger.error("DEBUG: InvalidOperationException loading {}: {}".format(family.Name, inv_ex))
                                logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))

                            except DB.Exceptions.CorruptModelException as corrupt_ex:
                                fail_count += 1
                                error_msg = "Corrupt file"
                                failed_families.append((family.Name, error_msg))
                                logger.error("DEBUG: Corrupt family file {}: {}".format(family.Name, corrupt_ex))
                                logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))

                            except Exception as load_ex:
                                fail_count += 1
                                error_msg = str(load_ex)[:50]  # Truncate long errors
                                failed_families.append((family.Name, error_msg))
                                logger.error("DEBUG: Failed to load {}: {}".format(family.Name, load_ex))
                                logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))

                    except Exception as trans_ex:
                        fail_count += 1
                        failed_families.append((family.Name, "Transaction error"))
                        logger.error("DEBUG: Transaction error for {}: {}".format(family.Name, trans_ex))
                        logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))

                except Exception as outer_ex:
                    fail_count += 1
                    failed_families.append((family.Name, "Outer exception"))
                    logger.error("DEBUG: Outer exception for {}: {}".format(family.Name, outer_ex))
                    logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))

            # Calculate duration
            duration = time.time() - start_time

            logger.info("=" * 80)
            logger.info("DEBUG: FAMILY LOADING COMPLETED: {}".format(datetime.datetime.now()))
            logger.info("DEBUG: Duration: {:.2f} seconds".format(duration))
            logger.info("DEBUG: Success: {}, Failed: {}".format(success_count, fail_count))
            logger.info("=" * 80)

            # Re-enable UI
            logger.debug("DEBUG: Re-enabling UI controls")
            try:
                self.btn_load.IsEnabled = True
                self.btn_cancel.IsEnabled = True
                logger.debug("DEBUG: UI controls re-enabled")
            except Exception as ui_ex:
                logger.error("DEBUG: Error re-enabling UI: {}".format(ui_ex))

            # Show result
            logger.debug("DEBUG: Building result message")
            try:
                message = "Successfully loaded {} families in {:.1f} seconds.".format(success_count, duration)
                if fail_count > 0:
                    message += "\n\n{} families failed to load.".format(fail_count)
                    if len(failed_families) <= 10:
                        message += "\n\nFailed families:"
                        for fam_name, error in failed_families:
                            message += "\n- {}: {}".format(fam_name, error)
                    else:
                        message += "\n\nShowing first 10 failures:"
                        for fam_name, error in failed_families[:10]:
                            message += "\n- {}: {}".format(fam_name, error)
                        message += "\n... and {} more (check log for details)".format(len(failed_families) - 10)

                logger.debug("DEBUG: Showing result alert")
                forms.alert(message, exitscript=False)
                logger.debug("DEBUG: Result alert shown")

                # Close dialog if any families were loaded successfully
                if success_count > 0:
                    logger.info("DEBUG: Closing dialog (success_count > 0)")
                    self.DialogResult = True
                    self.Close()
                    logger.info("DEBUG: Dialog closed successfully")
            except Exception as msg_ex:
                logger.error("DEBUG: Error showing result message: {}".format(msg_ex))
                logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
                # Try simple message instead
                try:
                    forms.alert("Loading completed. Success: {}, Failed: {}".format(success_count, fail_count), exitscript=False)
                except Exception:
                    pass

        except Exception as ex:
            logger.error("=" * 80)
            logger.error("DEBUG: CRITICAL ERROR in load_clicked")
            logger.error("DEBUG: Error: {}".format(ex))
            logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
            logger.error("=" * 80)

            # Re-enable UI on error
            try:
                logger.debug("DEBUG: Attempting to re-enable UI after error")
                self.btn_load.IsEnabled = True
                self.btn_cancel.IsEnabled = True
                logger.debug("DEBUG: UI re-enabled after error")
            except Exception as ui_err:
                logger.error("DEBUG: Failed to re-enable UI: {}".format(ui_err))

            try:
                forms.alert("Critical error loading families:\n{}".format(str(ex)[:200]), exitscript=False)
            except Exception as alert_ex:
                logger.error("DEBUG: Failed to show error alert: {}".format(alert_ex))

    def _start_thumbnail_worker(self):
        """Kick off a background thread that fills thumbnails progressively (Phase 2)."""
        self._thumb_cancel = False
        families_snapshot = list(self.all_families)
        t = threading.Thread(target=self._thumbnail_worker, args=(families_snapshot,))
        t.daemon = True
        t.start()

    def _thumbnail_worker(self, families):
        """Background: extract image previews from .rfa files, update FamilyItems via Dispatcher."""
        batch = 0
        for family in families:
            if self._thumb_cancel:
                break
            # Skip cloud families and those that already have a thumbnail
            if family.IsCloud or family.Thumbnail is not None:
                continue
            try:
                rfa_path = family.FullPath
                cache_path = _get_thumbnail_cache_path(rfa_path)
                img_bytes = None

                # Try cache first
                if cache_path and os.path.exists(cache_path):
                    try:
                        with open(cache_path, 'rb') as cf:
                            img_bytes = cf.read()
                    except Exception:
                        img_bytes = None

                # Extract from file if not cached
                if not img_bytes:
                    img_bytes = _extract_rfa_preview(rfa_path)
                    if img_bytes and cache_path:
                        try:
                            if not os.path.exists(THUMBNAIL_CACHE_DIR):
                                os.makedirs(THUMBNAIL_CACHE_DIR)
                            with open(cache_path, 'wb') as cf:
                                cf.write(img_bytes)
                        except Exception:
                            pass

                if img_bytes:
                    bitmap = _bytes_to_bitmap(img_bytes)
                    if bitmap:
                        def _make_setter(fam, bmp):
                            return lambda: self._apply_thumbnail(fam, bmp)
                        self.Dispatcher.Invoke(Action(_make_setter(family, bitmap)))
                        batch += 1
                        # Yield every 10 thumbnails so the UI thread stays responsive
                        if batch % 10 == 0:
                            time.sleep(0.05)

            except Exception as ex:
                logger.debug("Thumbnail error for {}: {}".format(family.Name, ex))

    def _apply_thumbnail(self, family, bitmap):
        """Set thumbnail on a FamilyItem (must be called on UI thread)."""
        try:
            if not family._is_disposed:
                family.Thumbnail = bitmap
        except Exception as ex:
            logger.debug("Error applying thumbnail: {}".format(ex))

    def titlebar_minimize_clicked(self, sender, e):
        """Minimize the window from custom title bar"""
        try:
            self.WindowState = System.Windows.WindowState.Minimized
        except Exception as ex:
            logger.debug("Error minimizing: {}".format(ex))

    def titlebar_drag(self, sender, e):
        """Allow dragging the window by the custom title bar"""
        try:
            if e.ButtonState == System.Windows.Input.MouseButtonState.Pressed:
                self.DragMove()
        except Exception as ex:
            logger.debug("Error in DragMove: {}".format(ex))

    def cancel_clicked(self, sender, e):
        """Cancel and close dialog (or cancel scan if in progress)"""
        try:
            # If scan is in progress, cancel it
            if self._scan_thread and self._scan_thread.is_alive():
                logger.info("User requested scan cancellation")
                self._cancel_requested = True
                # Don't close dialog, let scan complete
                forms.alert("Cancelling scan...", exitscript=False)
                return

            # Clean up resources before closing
            self._cleanup()

            # Close dialog
            self.Close()
        except Exception as ex:
            logger.error("Error in cancel_clicked: {}".format(ex))
            self.Close()

    def _cleanup(self):
        """Clean up resources to prevent memory leaks"""
        try:
            logger.info("Cleaning up Family Loader resources...")
            self._thumb_cancel = True

            # Unsubscribe all PropertyChanged events
            for family in self.filtered_families:
                try:
                    family.PropertyChanged -= self.on_family_property_changed
                except Exception:
                    pass

            # Dispose all family items
            for family in self.all_families:
                if hasattr(family, 'Dispose'):
                    family.Dispose()

            # Clear collections
            self.filtered_families.Clear()
            self.all_families = []
            self.category_structure = {}

            logger.info("Cleanup completed successfully")
        except Exception as ex:
            logger.error("Error during cleanup: {}".format(ex))


# ╔╦╗╔═╗╦╔╗╔
# ║║║╠═╣║║║║
# ╩ ╩╩ ╩╩╝╚╝ MAIN
#====================================================================================================

def show_family_loader():
    """Show the family loader dialog"""
    try:
        logger.info("=" * 80)
        logger.info("DEBUG: Family Loader Dialog Starting")
        logger.info("=" * 80)

        logger.debug("DEBUG: Creating FamilyLoaderWindow instance")
        try:
            window = FamilyLoaderWindow()
            logger.debug("DEBUG: FamilyLoaderWindow created successfully")
        except Exception as init_ex:
            logger.error("DEBUG: FAILED to create FamilyLoaderWindow")
            logger.error("DEBUG: Error: {}".format(init_ex))
            logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
            forms.alert("Failed to initialize Family Loader window:\n\n{}\n\nPlease check the log for details.".format(str(init_ex)), exitscript=True)
            return []

        logger.debug("DEBUG: Calling window.ShowDialog()")
        try:
            window.ShowDialog()
            logger.debug("DEBUG: window.ShowDialog() completed")
        except Exception as show_ex:
            logger.error("DEBUG: ERROR during window.ShowDialog()")
            logger.error("DEBUG: Error: {}".format(show_ex))
            logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
            forms.alert("Error showing Family Loader dialog:\n\n{}\n\nPlease check the log for details.".format(str(show_ex)), exitscript=False)
            return []

        logger.info("=" * 80)
        logger.info("DEBUG: Family Loader Dialog Closed")
        logger.info("DEBUG: Loaded {} families".format(len(window.loaded_families)))
        logger.info("=" * 80)

        return window.loaded_families
    except Exception as ex:
        logger.error("=" * 80)
        logger.error("DEBUG: CRITICAL ERROR in show_family_loader()")
        logger.error("DEBUG: Error: {}".format(ex))
        logger.error("DEBUG: Full traceback:\n{}".format(traceback.format_exc()))
        logger.error("=" * 80)
        try:
            forms.alert("Critical error in Family Loader:\n\n{}\n\nPlease check the log for details.".format(str(ex)), exitscript=False)
        except Exception:
            pass
        return []


if __name__ == '__main__':
    show_family_loader()
