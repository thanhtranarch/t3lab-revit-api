# -*- coding: utf-8 -*-
"""
Family Manager Dialog — Unified Central Hub
Combines Family Loader (Local/Cloud progressive load) and Family Management (Renaming/Worksets).
"""

import os
import sys
import clr
import json
import traceback
import time
import datetime
import threading
import tempfile
try:
    from urllib.request import urlopen, Request
    from urllib.error import URLError, HTTPError
except ImportError:
    from urllib2 import urlopen, Request, URLError, HTTPError

# .NET Imports
clr.AddReference("System")
clr.AddReference("System.Windows.Forms")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

import System
from System import Uri, Action, Object
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Windows import Window, Visibility
from System.Windows.Media.Imaging import BitmapImage
from System.Windows.Controls import TreeViewItem
from System.Windows.Forms import FolderBrowserDialog, DialogResult
from System.Windows.Threading import Dispatcher

# pyRevit Imports
from pyrevit import revit, DB, forms, script
from GUI.WPF_Base import T3WPFWindow, to_items_source
from Snippets._compat import eid_value, elem_name

from Autodesk.Revit.DB import (
    FilteredElementCollector, FilteredWorksetCollector, WorksetKind,
    Family, FamilySymbol, ElementType, GroupType, AssemblyType,
    Transaction, BuiltInParameter, ElementId
)

# Global logging variables
logger = script.get_logger()
# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
# point report the real problem.
try:
    doc = revit.doc
except Exception:
    doc = None
try:
    uidoc = revit.uidoc
except Exception:
    uidoc = None

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'ManaFami.xaml')

# Config/Cloud Constants
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".t3lab")
CONFIG_FILE = os.path.join(CONFIG_DIR, "family_loader_config.json")

THUMBNAIL_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".t3lab", "thumbnails")
SCAN_BATCH_SIZE = 20


# CONFIGURATION HELPERS
# ==============================================================================
def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                if isinstance(config, dict):
                    return config
        return {}
    except Exception as ex:
        logger.debug("Failed to load config: {}".format(ex))
        return {}

def save_config(config):
    try:
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as ex:
        logger.debug("Failed to save config: {}".format(ex))
        return False

def is_valid_rfa_file(file_path):
    try:
        if not os.path.exists(file_path):
            return False
        size = os.path.getsize(file_path)
        if size < 1024 or size > 500 * 1024 * 1024:
            return False
        with open(file_path, 'rb') as f:
            header = f.read(8)
            if not header.startswith(b'\xD0\xCF\x11\xE0'):
                return False
        return True
    except Exception:
        return False



# THUMBNAIL EXTRACTOR HELPERS (Local OLE / Thread Safe)
# ==============================================================================
def _get_thumbnail_cache_path(rfa_path):
    try:
        import re
        stat = os.stat(rfa_path)
        fname = os.path.splitext(os.path.basename(rfa_path))[0]
        key = "{}_{}_{}.jpg".format(fname, int(stat.st_mtime), stat.st_size)
        key = re.sub(r'[^a-zA-Z0-9_\-.]', '_', key)
        return os.path.join(THUMBNAIL_CACHE_DIR, key)
    except Exception:
        return None

def _extract_rfa_preview(rfa_path):
    try:
        with open(rfa_path, 'rb') as f:
            data = f.read()
        candidates = []
        # JPEG scan
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
        # PNG scan
        PNG_SIG = '\x89PNG\r\n\x1a\n'
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
    except Exception:
        pass
    return None

def _bytes_to_bitmap(raw_bytes):
    try:
        from System.IO import MemoryStream
        from System.Text import Encoding
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
    except Exception:
        return None

def sanitize_name(name):
    invalid_chars = ['\\', ':', '{', '}', '[', ']', '|', ';', '<', '>', '?', '`', '~']
    for char in invalid_chars:
        name = name.replace(char, '')
    return name.strip()


try:
    _Reactive = getattr(forms, 'Reactive', object)
except Exception:
    _Reactive = object


class FamilyItem(_Reactive):
    def __init__(self, name, full_path, category, thumbnail_path=None, is_cloud=False, download_url=None):
        self._is_checked = False
        self._is_disposed = False
        self._property_changed_handlers = []
        self._thumbnail = None
        self.Name = name
        self.FullPath = full_path
        self.Category = category
        self.IsCloud = is_cloud
        self.DownloadUrl = download_url
        self.Thumbnail = self._load_thumbnail(thumbnail_path)

    def _load_thumbnail(self, thumbnail_path):
        try:
            if thumbnail_path and os.path.exists(thumbnail_path):
                bitmap = BitmapImage()
                bitmap.BeginInit()
                bitmap.UriSource = Uri(thumbnail_path)
                bitmap.DecodePixelWidth = 90
                bitmap.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad
                bitmap.EndInit()
                bitmap.Freeze()
                return bitmap
        except Exception:
            pass
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
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, propertyName):
        if not self._is_disposed:
            for handler in self._property_changed_handlers:
                try:
                    handler(self, PropertyChangedEventArgs(propertyName))
                except Exception:
                    pass

    def Dispose(self):
        try:
            if not self._is_disposed:
                self.Thumbnail = None
                self._property_changed_handlers = []
                self._is_disposed = True
        except Exception:
            pass


class FamilyRow(object):
    def __init__(self, element, family_name, type_name, category_name, workset_id, is_loadable=True):
        self.IsSelected = False
        self.FamilyName = family_name
        self.TypeName = type_name
        self.CategoryName = category_name or "Unknown"
        self.WorksetId = workset_id
        self.element = element
        self.is_loadable = is_loadable
        self.OriginalFamilyName = family_name
        self.OriginalTypeName = type_name
        self.OriginalWorksetId = workset_id

    @property
    def IsModified(self):
        return (self.FamilyName != self.OriginalFamilyName or 
                self.TypeName != self.OriginalTypeName or 
                self.WorksetId != self.OriginalWorksetId)


class WorksetItem(object):
    def __init__(self, name, ws_id):
        self.Name = name
        self.Id = ws_id


class FamilyLoadOptions(DB.IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        overwriteParameterValues = True
        source = DB.FamilySource.Family
        return True


# MAIN CENTRAL WINDOW
# ==============================================================================
class ManaFamiWindow(T3WPFWindow):
    def __init__(self, script_dir, revit):
        T3WPFWindow.__init__(self, _XAML)
        self._script_dir = script_dir
        self._revit = revit
        
        # Loader initialization
        self.config = load_config()
        self.current_folder = None
        self.all_families = []
        self.loaded_families = []
        self.filtered_families = ObservableCollection[Object]()
        self.category_structure = {}
        self._scan_thread = None
        self._cancel_requested = False
        self._thumb_cancel = False
        self._is_updating = False

        # Management initialization
        self._all_rows = []
        self._visible_rows = []
        self._worksets = []

        # Find Controls
        self._find_named_controls()
        
        # ItemsSource Bindings
        if getattr(self, 'items_families', None) is not None:
            self.items_families.ItemsSource = to_items_source(self.filtered_families)
        
        # Event Wireup
        self._wire_events()

    @property
    def doc(self):
        try:
            if self._revit and hasattr(self._revit, 'doc') and self._revit.doc:
                return self._revit.doc
        except Exception:
            pass
        from pyrevit import revit as rvt
        try:
            return rvt.doc
        except Exception:
            return None

    @property
    def uidoc(self):
        try:
            if self._revit and hasattr(self._revit, 'uidoc') and self._revit.uidoc:
                return self._revit.uidoc
        except Exception:
            pass
        from pyrevit import revit as rvt
        try:
            return rvt.uidoc
        except Exception:
            return None

    def _find_named_controls(self):
        def _get(name):
            return self.FindName(name) or getattr(self, name, None)

        # Navigation
        self.nav_loader = _get('nav_loader')
        self.nav_management = _get('nav_management')
        self.main_tab_control = _get('main_tab_control')
        self.lbl_title = _get('lbl_title')
        self.lbl_subtitle = _get('lbl_subtitle')

        # Loader
        self.btn_select_folder = _get('btn_select_folder')
        self.txt_current_folder = _get('txt_current_folder')
        self.txt_search = _get('txt_search')
        self.tree_categories = _get('tree_categories')
        self.items_families = _get('items_families')
        self.txt_result_count = _get('txt_result_count')
        self.txt_selected_count = _get('txt_selected_count')
        self.btn_select_all_loader = _get('btn_select_all_loader')
        self.btn_select_none_loader = _get('btn_select_none_loader')
        self.btn_load = _get('btn_load')
        self.btn_cancel_loader = _get('btn_cancel_loader')

        # Management settings sidebar
        self.rb_scope_all = _get('rb_scope_all')
        self.rb_scope_view = _get('rb_scope_view')
        self.rb_scope_selection = _get('rb_scope_selection')
        self.cb_category = _get('cb_category')
        self.tb_find = _get('tb_find')
        self.btn_match_case = _get('btn_match_case')
        self.btn_find_next = _get('btn_find_next')
        self.btn_find_all = _get('btn_find_all')
        self.tb_replace = _get('tb_replace')
        self.btn_replace = _get('btn_replace')
        self.btn_replace_all = _get('btn_replace_all')
        self.tb_prefix = _get('tb_prefix')
        self.btn_prefix_sel = _get('btn_prefix_sel')
        self.btn_prefix_all = _get('btn_prefix_all')
        self.tb_suffix = _get('tb_suffix')
        self.btn_suffix_sel = _get('btn_suffix_sel')
        self.btn_suffix_all = _get('btn_suffix_all')
        self.cb_case_target = _get('cb_case_target')
        self.btn_case_upper = _get('btn_case_upper')
        self.btn_case_lower = _get('btn_case_lower')
        self.btn_case_title = _get('btn_case_title')
        self.btn_case_sentence = _get('btn_case_sentence')
        self.btn_clear_settings = _get('btn_clear_settings')

        # Management workspace area
        self.tb_search = _get('tb_search')
        self.btn_export_list = _get('btn_export_list')
        self.dg_families = _get('dg_families')
        self.btn_select_all_mgmt = _get('btn_select_all_mgmt')
        self.btn_deselect_all_mgmt = _get('btn_deselect_all_mgmt')
        self.btn_undo = _get('btn_undo')
        self.btn_apply = _get('btn_apply')
        self.status_text = _get('status_text')
        self.count_text = _get('count_text')

        # Chrome buttons
        self.btn_minimize = _get('btn_minimize')
        self.btn_maximize = _get('btn_maximize')
        self.btn_close_chrome = _get('btn_close_chrome')

    def _wire_events(self):
        # Sidebar Navigation
        self.nav_loader.Click += self.nav_toggle_clicked
        self.nav_management.Click += self.nav_toggle_clicked

        # Loader Event Handlers
        self.btn_select_folder.Click += self.select_folder_clicked
        self.txt_search.TextChanged += self.search_text_changed
        self.tree_categories.SelectedItemChanged += self.category_selected
        self.btn_select_all_loader.Click += self.select_all_loader_clicked
        self.btn_select_none_loader.Click += self.select_none_loader_clicked
        self.btn_load.Click += self.load_clicked
        self.btn_cancel_loader.Click += self.cancel_loader_clicked


        # Management Event Handlers
        self.rb_scope_all.Checked += self.scope_changed
        self.rb_scope_view.Checked += self.scope_changed
        self.rb_scope_selection.Checked += self.scope_changed
        self.cb_category.SelectionChanged += self.category_changed
        self.btn_match_case.Click += self.match_case_click
        self.btn_find_next.Click += self.find_next_click
        self.btn_find_all.Click += self.find_all_click
        self.btn_replace.Click += self.replace_click
        self.btn_replace_all.Click += self.replace_all_click
        self.btn_prefix_sel.Click += self.prefix_selected_click
        self.btn_prefix_all.Click += self.prefix_all_click
        self.btn_suffix_sel.Click += self.suffix_selected_click
        self.btn_suffix_all.Click += self.suffix_all_click
        self.btn_case_upper.Click += self.case_upper_click
        self.btn_case_lower.Click += self.case_lower_click
        self.btn_case_title.Click += self.case_title_click
        self.btn_case_sentence.Click += self.case_sentence_click
        self.btn_clear_settings.Click += self.clear_settings_click
        self.tb_search.TextChanged += self.search_changed
        self.btn_export_list.Click += self.export_list_click
        self.btn_select_all_mgmt.Click += self.select_all_click
        self.btn_deselect_all_mgmt.Click += self.deselect_all_click
        self.btn_undo.Click += self.undo_click
        self.btn_apply.Click += self.apply_click

        # Chrome Event Handlers
        self.btn_minimize.Click += self._minimize
        self.btn_maximize.Click += self._maximize
        self.btn_close_chrome.Click += self._close_chrome
        self.PreviewKeyDown += self._on_key_down
        self.Loaded += self.window_loaded

    def window_loaded(self, sender, e):
        """Restore saved folder, load worksets, and refresh grid data"""
        # Loader setup — isolated in its own try so a folder-scan failure
        # can never block the Management tab's project data below.
        try:
            saved_folder = self.config.get('last_folder', '')
            if saved_folder and os.path.exists(saved_folder):
                self.current_folder = saved_folder
                self.txt_current_folder.Text = saved_folder
                self.scan_families()
            else:
                self.txt_current_folder.Text = "Click 'Update Folder' to select a folder or switch to Cloud mode"
        except Exception as ex:
            logger.error("Error loading saved folder: {}".format(ex))

        # Management setup — always runs even if the Loader setup above
        # failed; surfaces failures in the status bar instead of staying
        # silently empty.
        try:
            self._load_worksets()
            self._refresh_data()
            self._update_counts()
        except Exception as ex:
            logger.error("Error refreshing project data: {}".format(ex))
            self.status_text.Text = "Failed to load data from the project: {}".format(ex)

    # NAVIGATION ROUTING
    # ==============================================================================
    def nav_toggle_clicked(self, sender, e):
        self.nav_loader.IsChecked = (sender == self.nav_loader)
        self.nav_management.IsChecked = (sender == self.nav_management)
        
        if sender == self.nav_loader:
            self.main_tab_control.SelectedIndex = 0
            self.lbl_title.Text = "Family Loader"
            self.lbl_subtitle.Text = "Load Revit families from local folders or cloud library"
        elif sender == self.nav_management:
            self.main_tab_control.SelectedIndex = 1
            self.lbl_title.Text = "Family Management"
            self.lbl_subtitle.Text = "Batch rename families/types, modify case, customize prefix/suffix, and assign worksets"

    # CHROME EVENT HANDLERS
    # ==============================================================================
    def _minimize(self, sender, e):
        self.WindowState = System.Windows.WindowState.Minimized

    def _maximize(self, sender, e):
        if self.WindowState == System.Windows.WindowState.Maximized:
            self.WindowState = System.Windows.WindowState.Normal
        else:
            self.WindowState = System.Windows.WindowState.Maximized

    def _close_chrome(self, sender, e):
        self._cleanup()
        self.Close()

    def close_button_clicked(self, sender, e):
        self._close_chrome(sender, e)

    def _on_key_down(self, sender, e):
        import System.Windows.Input as WI
        if e.Key == WI.Key.Escape:
            self._cleanup()
            self.Close()

    # FAMILY LOADER WORKSPACE
    # ==============================================================================
    def select_folder_clicked(self, sender, e):
        try:
            dialog = FolderBrowserDialog()
            dialog.Description = "Select folder containing Revit families"
            if dialog.ShowDialog() == DialogResult.OK:
                self.current_folder = dialog.SelectedPath
                self.txt_current_folder.Text = self.current_folder
                self.config['last_folder'] = self.current_folder
                save_config(self.config)
                self.scan_families()
        except Exception as ex:
            forms.alert("Error selecting folder: {}".format(ex))

    def scan_families(self):
        if not self.current_folder:
            return
        self._thumb_cancel = True
        self._cancel_requested = False
        self._clear_families_ui()
        self.btn_select_folder.IsEnabled = False
        self.btn_load.IsEnabled = False
        self.txt_current_folder.Text = "{} (Scanning...)".format(self.current_folder)
        self._scan_thread = threading.Thread(target=self._scan_families_worker)
        self._scan_thread.daemon = True
        self._scan_thread.start()

    def _clear_families_ui(self):
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
        start_time = time.time()
        temp_category_structure = {}
        temp_seen_names = {}
        pending_batch = []
        total_found = 0

        try:
            for root, dirs, files in os.walk(self.current_folder, followlinks=False):
                if self._cancel_requested:
                    self._scan_complete(None, temp_category_structure, cancelled=True)
                    return

                try:
                    _ = os.listdir(root)
                except Exception:
                    dirs[:] = []
                    continue

                for file in files:
                    if self._cancel_requested:
                        if pending_batch:
                            self._push_family_batch(list(pending_batch))
                        self._scan_complete(None, temp_category_structure, cancelled=True)
                        return

                    if file.lower().endswith('.rfa'):
                        try:
                            full_path = os.path.join(root, file)
                            relative_path = os.path.relpath(root, self.current_folder)

                            if not is_valid_rfa_file(full_path):
                                continue

                            category = relative_path if relative_path != '.' else 'Root'
                            family_name = os.path.splitext(file)[0]
                            if family_name in temp_seen_names:
                                folder_name = os.path.basename(root)
                                family_name = "{} ({})".format(family_name, folder_name)
                            else:
                                temp_seen_names[family_name] = full_path

                            family_item = FamilyItem(family_name, full_path, category)
                            pending_batch.append(family_item)
                            total_found += 1

                            if category not in temp_category_structure:
                                temp_category_structure[category] = []
                            temp_category_structure[category].append(family_item)

                            if len(pending_batch) >= SCAN_BATCH_SIZE:
                                self._push_family_batch(list(pending_batch))
                                pending_batch = []

                        except Exception:
                            pass

            if pending_batch:
                self._push_family_batch(list(pending_batch))

            self._scan_complete(None, temp_category_structure)
        except Exception as ex:
            self._scan_complete(None, temp_category_structure, error=str(ex))

    def _push_family_batch(self, batch):
        try:
            if self.Dispatcher:
                self.Dispatcher.Invoke(
                    Action(lambda: self._push_family_batch_ui(batch))
                )
        except Exception:
            pass

    def _push_family_batch_ui(self, batch):
        try:
            for family in batch:
                self.all_families.append(family)
                family.PropertyChanged += self.on_family_property_changed
                self.filtered_families.Add(family)
            count = len(self.filtered_families)
            self.txt_result_count.Text = "{} families found...".format(count)
            self.txt_current_folder.Text = "{} (Scanning... {} found)".format(
                self.current_folder, count
            )
        except Exception:
            pass

    def _scan_complete(self, families, category_structure, error=None, cancelled=False):
        try:
            if self.Dispatcher:
                self.Dispatcher.Invoke(
                    Action(lambda: self._scan_complete_ui(families, category_structure, error, cancelled))
                )
        except Exception:
            pass

    def _scan_complete_ui(self, families, category_structure, error=None, cancelled=False):
        try:
            if families is not None:
                for old_family in self.all_families:
                    if hasattr(old_family, 'Dispose'):
                        old_family.Dispose()
                self.all_families = families
                self.category_structure = category_structure
            else:
                self.category_structure = category_structure

            self.btn_select_folder.IsEnabled = True
            self.txt_current_folder.Text = self.current_folder

            if error:
                self.txt_result_count.Text = "{} families found".format(len(self.all_families))
                forms.alert("Error scanning folder: {}".format(error), exitscript=False)
            elif cancelled:
                self.txt_result_count.Text = "{} families found (cancelled)".format(len(self.all_families))
            else:
                self.update_category_tree()
                if families is not None:
                    self.update_family_display()
                else:
                    self.txt_result_count.Text = "{} families found".format(len(self.all_families))
                
                self._thumb_cancel = False
                self._start_thumbnail_worker()

        except Exception as ex:
            logger.error("Error completing scan UI: {}".format(ex))

    def update_category_tree(self):
        try:
            self.tree_categories.Items.Clear()
            all_item = TreeViewItem()
            all_item.Header = "All ({})".format(len(self.all_families))
            all_item.Tag = "ALL"
            all_item.IsExpanded = True
            self.tree_categories.Items.Add(all_item)

            tree_dict = {}
            for category, families in self.category_structure.items():
                parts = ['Root'] if category == 'Root' else category.split(os.sep)
                current_dict = tree_dict
                for part in parts:
                    if part not in current_dict:
                        current_dict[part] = {'_families': [], '_children': {}}
                    current_dict = current_dict[part]['_children']
                
                path_key = os.sep.join(parts) if parts != ['Root'] else 'Root'
                if path_key in self.category_structure:
                    tree_dict_leaf = tree_dict
                    for part in parts:
                        tree_dict_leaf = tree_dict_leaf[part]
                    tree_dict_leaf['_families'] = self.category_structure[path_key]

            def add_tree_items(parent_item, tree_data, path_prefix=""):
                for folder_name, data in sorted(tree_data.items()):
                    folder_path = os.path.join(path_prefix, folder_name) if path_prefix else folder_name
                    total_families = self._count_families_in_tree(data)
                    item = TreeViewItem()
                    item.Header = "{} ({})".format(folder_name, total_families)
                    item.Tag = folder_path if folder_path != 'Root' else 'Root'
                    item.IsExpanded = True
                    parent_item.Items.Add(item)
                    if data['_children']:
                        add_tree_items(item, data['_children'], folder_path)

            add_tree_items(self.tree_categories, tree_dict)
        except Exception as ex:
            logger.debug("Error category tree: {}".format(ex))

    def _count_families_in_tree(self, tree_node):
        count = len(tree_node.get('_families', []))
        for child in tree_node.get('_children', {}).values():
            count += self._count_families_in_tree(child)
        return count

    def update_family_display(self, families=None):
        try:
            if families is None:
                families = self.all_families
            for old_family in self.filtered_families:
                try:
                    old_family.PropertyChanged -= self.on_family_property_changed
                except Exception:
                    pass
            self.filtered_families.Clear()
            for family in families:
                family.PropertyChanged += self.on_family_property_changed
                self.filtered_families.Add(family)
            self.update_result_count()
        except Exception as ex:
            logger.debug("Error display update: {}".format(ex))

    def on_family_property_changed(self, sender, e):
        if e.PropertyName == "IsChecked" and not self._is_updating:
            self.update_result_count()

    def update_result_count(self):
        if self._is_updating:
            return
        try:
            count = len(self.filtered_families)
            self.txt_result_count.Text = "{} families found".format(count)
            selected = sum(1 for f in self.all_families if f.IsChecked)
            visible_selected = sum(1 for f in self.filtered_families if f.IsChecked)
            hidden_selected = selected - visible_selected
            text = "{} families selected".format(selected)
            if hidden_selected:
                text += " ({} hidden)".format(hidden_selected)
            self.txt_selected_count.Text = text
            self.txt_selected_count.ToolTip = (
                "Load includes all selected families, including those hidden "
                "by the current search or category filter."
            )
            self.btn_load.IsEnabled = selected > 0
        except Exception as ex:
            logger.debug("Error counts: {}".format(ex))

    def category_selected(self, sender, e):
        try:
            selected_item = self.tree_categories.SelectedItem
            if not selected_item:
                return
            tag = selected_item.Tag
            if tag == "ALL":
                self.update_family_display(self.all_families)
            else:
                filtered = [f for f in self.all_families
                           if f.Category == tag or f.Category.startswith(tag + os.sep)]
                self.update_family_display(filtered)
        except Exception as ex:
            logger.debug("Error cat selection: {}".format(ex))

    def search_text_changed(self, sender, e):
        try:
            search_text = self.txt_search.Text.lower()
            if not search_text:
                selected_item = self.tree_categories.SelectedItem
                if selected_item and selected_item.Tag != "ALL":
                    filtered = [f for f in self.all_families if f.Category == selected_item.Tag]
                    self.update_family_display(filtered)
                else:
                    self.update_family_display(self.all_families)
            else:
                filtered = [f for f in self.all_families
                           if search_text in f.Name.lower() or
                              search_text in f.Category.lower()]
                self.update_family_display(filtered)
        except Exception as ex:
            logger.debug("Error search text changed: {}".format(ex))

    def select_all_loader_clicked(self, sender, e):
        try:
            self._is_updating = True
            for family in self.filtered_families:
                family.IsChecked = True
            self._is_updating = False
            self.update_result_count()
        except Exception as ex:
            logger.debug("Error select all loader: {}".format(ex))

    def select_none_loader_clicked(self, sender, e):
        try:
            self._is_updating = True
            for family in self.filtered_families:
                family.IsChecked = False
            self._is_updating = False
            self.update_result_count()
        except Exception as ex:
            logger.debug("Error select none loader: {}".format(ex))

    def load_clicked(self, sender, e):
        try:
            selected_families = [f for f in self.all_families if f.IsChecked]
            if not selected_families:
                forms.alert("Please select at least one family to load.", exitscript=False)
                return

            if doc.IsReadOnly:
                forms.alert("Cannot load families: Document is read-only.", exitscript=False)
                return

            if doc.IsModifiable:
                forms.alert("Cannot load families: Document is currently being modified.", exitscript=False)
                return

            if doc.IsWorkshared and not doc.IsDetached:
                result = forms.alert(
                    "Document is workshared. Families will be loaded to central model.\n\nDo you want to continue?",
                    yes=True, no=True, exitscript=False
                )
                if not result:
                    return

            start_time = time.time()
            self.btn_load.IsEnabled = False
            self.btn_cancel_loader.IsEnabled = False

            success_count = 0
            fail_count = 0
            failed_families = []
            load_options = FamilyLoadOptions()

            for i, family in enumerate(selected_families):
                try:
                    if not os.path.exists(family.FullPath) or not is_valid_rfa_file(family.FullPath):
                        fail_count += 1
                        failed_families.append((family.Name, "File missing or corrupt"))
                        continue

                    try:
                        with revit.Transaction("Load Family: {}".format(family.Name)):
                            loaded = doc.LoadFamily(family.FullPath, load_options)
                            if loaded:
                                success_count += 1
                                self.loaded_families.append(family.FullPath)
                            else:
                                fail_count += 1
                                failed_families.append((family.Name, "LoadFamily returned False"))
                    except Exception as load_ex:
                        fail_count += 1
                        failed_families.append((family.Name, str(load_ex)[:50]))
                except Exception as outer_ex:
                    fail_count += 1
                    failed_families.append((family.Name, "Outer error"))

            duration = time.time() - start_time
            self.btn_load.IsEnabled = True
            self.btn_cancel_loader.IsEnabled = True

            message = "Successfully loaded {} families in {:.1f} seconds.".format(success_count, duration)
            if fail_count > 0:
                message += "\n\n{} families failed to load. Check log details.".format(fail_count)
            forms.alert(message, exitscript=False)

            if success_count > 0:
                self.DialogResult = True
                self.Close()

        except Exception as ex:
            logger.error("Error in load_clicked: {}".format(ex))
            self.btn_load.IsEnabled = True
            self.btn_cancel_loader.IsEnabled = True

    def _start_thumbnail_worker(self):
        self._thumb_cancel = False
        families_snapshot = list(self.all_families)
        t = threading.Thread(target=self._thumbnail_worker, args=(families_snapshot,))
        t.daemon = True
        t.start()

    def _thumbnail_worker(self, families):
        batch = 0
        for family in families:
            if self._thumb_cancel:
                break
            if family.IsCloud or family.Thumbnail is not None:
                continue
            try:
                rfa_path = family.FullPath
                cache_path = _get_thumbnail_cache_path(rfa_path)
                img_bytes = None

                if cache_path and os.path.exists(cache_path):
                    try:
                        with open(cache_path, 'rb') as cf:
                            img_bytes = cf.read()
                    except Exception:
                        img_bytes = None

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
                        if batch % 10 == 0:
                            time.sleep(0.05)
            except Exception:
                pass

    def _apply_thumbnail(self, family, bitmap):
        try:
            if not family._is_disposed:
                family.Thumbnail = bitmap
        except Exception:
            pass

    def cancel_loader_clicked(self, sender, e):
        if self._scan_thread and self._scan_thread.is_alive():
            self._cancel_requested = True
            forms.alert("Cancelling scan...", exitscript=False)
            return
        self._cleanup()
        self.Close()


    # FAMILY MANAGEMENT WORKSPACE
    # ==============================================================================
    def _load_worksets(self):
        self._worksets = [WorksetItem("<No Workset / None>", -1)]
        cur_doc = self.doc
        if cur_doc and cur_doc.IsWorkshared:
            try:
                f_collector = FilteredWorksetCollector(cur_doc).OfKind(WorksetKind.UserWorkset)
                for ws in f_collector.ToWorksets():
                    self._worksets.append(WorksetItem(ws.Name, eid_value(ws.Id)))
            except Exception:
                pass
        self.Worksets = self._worksets

    def _refresh_data(self):
        cur_doc = self.doc
        cur_uidoc = self.uidoc
        if not cur_doc:
            if getattr(self, 'status_text', None) is not None:
                self.status_text.Text = "No active document open in Revit."
            return

        scope_all = self.rb_scope_all.IsChecked if getattr(self, 'rb_scope_all', None) else False
        scope_view = self.rb_scope_view.IsChecked if getattr(self, 'rb_scope_view', None) else False
        scope_selection = self.rb_scope_selection.IsChecked if getattr(self, 'rb_scope_selection', None) else False
        category_idx = self.cb_category.SelectedIndex if getattr(self, 'cb_category', None) else 0

        if scope_selection:
            if not cur_uidoc:
                return
            selected_ids = cur_uidoc.Selection.GetElementIds()
            if not selected_ids:
                self._all_rows = []
                self._visible_rows = []
                if getattr(self, 'dg_families', None) is not None:
                    self.dg_families.ItemsSource = to_items_source(self._visible_rows)
                if getattr(self, 'status_text', None) is not None:
                    self.status_text.Text = "No elements selected in Revit."
                return
            collector = FilteredElementCollector(cur_doc, selected_ids)
        elif scope_view:
            collector = FilteredElementCollector(cur_doc, cur_doc.ActiveView.Id)
        else:
            collector = FilteredElementCollector(cur_doc)

        rows = []
        
        # Loadable Families
        if category_idx == 0:
            if scope_selection or scope_view:
                instances = collector.WhereElementIsNotElementType().ToElements()
                symbols_found = set()
                for inst in instances:
                    try:
                        symbol = inst.Symbol
                        if symbol and symbol.Id not in symbols_found:
                            symbols_found.add(symbol.Id)
                            family = symbol.Family
                            ws_id = inst.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM).AsInteger() if cur_doc.IsWorkshared else -1
                            rows.append(FamilyRow(
                                element=symbol,
                                family_name=family.Name,
                                type_name=elem_name(symbol),
                                category_name=symbol.Category.Name if symbol.Category else "Generic Models",
                                workset_id=ws_id,
                                is_loadable=True
                            ))
                    except Exception:
                        pass
            else:
                families = FilteredElementCollector(cur_doc).OfClass(Family).ToElements()
                for fam in families:
                    try:
                        if not fam.IsEditable:
                            continue
                        for symbol_id in fam.GetFamilySymbolIds():
                            symbol = cur_doc.GetElement(symbol_id)
                            if symbol:
                                ws_id = -1
                                try:
                                    ws_id = symbol.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM).AsInteger()
                                except Exception:
                                    pass
                                rows.append(FamilyRow(
                                    element=symbol,
                                    family_name=fam.Name,
                                    type_name=elem_name(symbol),
                                    category_name=symbol.Category.Name if symbol.Category else "Generic Models",
                                    workset_id=ws_id,
                                    is_loadable=True
                                ))
                    except Exception:
                        pass

        # System Families
        elif category_idx == 1:
            types_collector = FilteredElementCollector(cur_doc).OfClass(ElementType).ToElements()
            for t in types_collector:
                try:
                    if hasattr(t, "FamilyName") and t.FamilyName and t.Category:
                        is_loadable = False
                        if isinstance(t, FamilySymbol):
                            try:
                                if t.Family.IsEditable:
                                    is_loadable = True
                            except Exception:
                                pass
                        
                        if not is_loadable:
                            ws_id = t.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM).AsInteger() if cur_doc.IsWorkshared else -1
                            rows.append(FamilyRow(
                                element=t,
                                family_name=t.FamilyName,
                                type_name=elem_name(t),
                                category_name=t.Category.Name,
                                workset_id=ws_id,
                                is_loadable=False
                            ))
                except Exception:
                    pass

        # Model Groups
        elif category_idx == 2:
            group_types = FilteredElementCollector(cur_doc).OfClass(GroupType).ToElements()
            for gt in group_types:
                try:
                    ws_id = gt.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM).AsInteger() if cur_doc.IsWorkshared else -1
                    rows.append(FamilyRow(
                        element=gt,
                        family_name="Model Group",
                        type_name=elem_name(gt),
                        category_name="Groups",
                        workset_id=ws_id,
                        is_loadable=False
                    ))
                except Exception:
                    pass

        # Assemblies
        elif category_idx == 3:
            assembly_types = FilteredElementCollector(cur_doc).OfClass(AssemblyType).ToElements()
            for at in assembly_types:
                try:
                    ws_id = at.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM).AsInteger() if cur_doc.IsWorkshared else -1
                    rows.append(FamilyRow(
                        element=at,
                        family_name="Assembly",
                        type_name=elem_name(at),
                        category_name="Assemblies",
                        workset_id=ws_id,
                        is_loadable=False
                    ))
                except Exception:
                    pass

        self._all_rows = rows
        self._apply_filter()
        if getattr(self, 'status_text', None) is not None:
            self.status_text.Text = "Loaded {} rows.".format(len(self._all_rows))

    def _apply_filter(self):
        search = (self.tb_search.Text or "").strip().lower() if getattr(self, 'tb_search', None) else ""
        if not search:
            self._visible_rows = list(self._all_rows)
        else:
            self._visible_rows = []
            for r in self._all_rows:
                if (search in r.FamilyName.lower() or 
                    search in r.TypeName.lower() or 
                    search in r.CategoryName.lower()):
                    self._visible_rows.append(r)
        
        if getattr(self, 'dg_families', None) is not None:
            self.dg_families.ItemsSource = to_items_source(self._visible_rows)
        self._update_counts()

    def _update_counts(self):
        total = len(self._visible_rows)
        selected = sum(1 for r in self._visible_rows if r.IsSelected)
        if getattr(self, 'count_text', None) is not None:
            self.count_text.Text = "Total number of elements found {} | Selected {}".format(total, selected)

    def scope_changed(self, sender, e):
        if hasattr(self, "rb_scope_all") and self.rb_scope_all:
            self._refresh_data()

    def category_changed(self, sender, e):
        if hasattr(self, "cb_category") and self.cb_category:
            self._refresh_data()
            try:
                category_idx = self.cb_category.SelectedIndex
                if hasattr(self, "dg_families") and self.dg_families.Columns.Count > 1:
                    self.dg_families.Columns[1].IsReadOnly = (category_idx != 0)
            except Exception:
                pass

    def search_changed(self, sender, e):
        self._apply_filter()

    def select_all_click(self, sender, e):
        for r in self._visible_rows:
            r.IsSelected = True
        self.dg_families.Items.Refresh()
        self._update_counts()

    def deselect_all_click(self, sender, e):
        for r in self._visible_rows:
            r.IsSelected = False
        self.dg_families.Items.Refresh()
        self._update_counts()

    def match_case_click(self, sender, e):
        pass

    def find_next_click(self, sender, e):
        find_val = (self.tb_find.Text or "").strip()
        if not find_val:
            return
        match_case = self.btn_match_case.IsChecked == True
        current_idx = self.dg_families.SelectedIndex
        rows_len = len(self._visible_rows)
        for i in range(1, rows_len + 1):
            idx = (current_idx + i) % rows_len
            row = self._visible_rows[idx]
            f_match = find_val in row.FamilyName if match_case else find_val.lower() in row.FamilyName.lower()
            t_match = find_val in row.TypeName if match_case else find_val.lower() in row.TypeName.lower()
            if f_match or t_match:
                self.dg_families.SelectedIndex = idx
                self.dg_families.ScrollIntoView(row)
                break

    def find_all_click(self, sender, e):
        find_val = (self.tb_find.Text or "").strip()
        if not find_val:
            return
        match_case = self.btn_match_case.IsChecked == True
        count = 0
        for row in self._visible_rows:
            f_match = find_val in row.FamilyName if match_case else find_val.lower() in row.FamilyName.lower()
            t_match = find_val in row.TypeName if match_case else find_val.lower() in row.TypeName.lower()
            if f_match or t_match:
                row.IsSelected = True
                count += 1
        self.dg_families.Items.Refresh()
        self._update_counts()
        self.status_text.Text = "Selected {} matching elements.".format(count)

    def replace_click(self, sender, e):
        find_val = (self.tb_find.Text or "")
        replace_val = (self.tb_replace.Text or "")
        selected_row = self.dg_families.SelectedItem
        if not selected_row:
            forms.alert("Please select a row in the table first.")
            return
        match_case = self.btn_match_case.IsChecked == True
        if match_case:
            selected_row.FamilyName = selected_row.FamilyName.replace(find_val, replace_val)
            selected_row.TypeName = selected_row.TypeName.replace(find_val, replace_val)
        else:
            import re
            pattern = re.compile(re.escape(find_val), re.IGNORECASE)
            selected_row.FamilyName = pattern.sub(replace_val, selected_row.FamilyName)
            selected_row.TypeName = pattern.sub(replace_val, selected_row.TypeName)
        self.dg_families.Items.Refresh()

    def replace_all_click(self, sender, e):
        find_val = (self.tb_find.Text or "")
        replace_val = (self.tb_replace.Text or "")
        target_rows = [r for r in self._visible_rows if r.IsSelected]
        if not target_rows:
            target_rows = self._visible_rows
        match_case = self.btn_match_case.IsChecked == True
        import re
        pattern = re.compile(re.escape(find_val), re.IGNORECASE)
        for r in target_rows:
            if match_case:
                r.FamilyName = r.FamilyName.replace(find_val, replace_val)
                r.TypeName = r.TypeName.replace(find_val, replace_val)
            else:
                r.FamilyName = pattern.sub(replace_val, r.FamilyName)
                r.TypeName = pattern.sub(replace_val, r.TypeName)
        self.dg_families.Items.Refresh()

    def prefix_selected_click(self, sender, e):
        prefix = (self.tb_prefix.Text or "")
        for r in self._visible_rows:
            if r.IsSelected:
                r.FamilyName = prefix + r.FamilyName
                r.TypeName = prefix + r.TypeName
        self.dg_families.Items.Refresh()

    def prefix_all_click(self, sender, e):
        prefix = (self.tb_prefix.Text or "")
        for r in self._visible_rows:
            r.FamilyName = prefix + r.FamilyName
            r.TypeName = prefix + r.TypeName
        self.dg_families.Items.Refresh()

    def suffix_selected_click(self, sender, e):
        suffix = (self.tb_suffix.Text or "")
        for r in self._visible_rows:
            if r.IsSelected:
                r.FamilyName = r.FamilyName + suffix
                r.TypeName = r.TypeName + suffix
        self.dg_families.Items.Refresh()

    def suffix_all_click(self, sender, e):
        suffix = (self.tb_suffix.Text or "")
        for r in self._visible_rows:
            r.FamilyName = r.FamilyName + suffix
            r.TypeName = r.TypeName + suffix
        self.dg_families.Items.Refresh()

    def _apply_case_to_string(self, text, case_type):
        if case_type == "UPPER":
            return text.upper()
        elif case_type == "lower":
            return text.lower()
        elif case_type == "Title":
            return " ".join([w.capitalize() for w in text.split(" ")])
        elif case_type == "Sentence":
            if len(text) > 0:
                return text[0].upper() + text[1:].lower()
        return text

    def _apply_case_transformation(self, case_type):
        target_idx = self.cb_case_target.SelectedIndex
        target_rows = [r for r in self._visible_rows if r.IsSelected]
        if not target_rows:
            target_rows = self._visible_rows
        for r in target_rows:
            if target_idx == 0 or target_idx == 1:
                r.FamilyName = self._apply_case_to_string(r.FamilyName, case_type)
            if target_idx == 0 or target_idx == 2:
                r.TypeName = self._apply_case_to_string(r.TypeName, case_type)
        self.dg_families.Items.Refresh()

    def case_upper_click(self, sender, e):
        self._apply_case_transformation("UPPER")

    def case_lower_click(self, sender, e):
        self._apply_case_transformation("lower")

    def case_title_click(self, sender, e):
        self._apply_case_transformation("Title")

    def case_sentence_click(self, sender, e):
        self._apply_case_transformation("Sentence")

    def delete_row_click(self, sender, e):
        button = sender
        row = button.DataContext
        if row in self._all_rows:
            self._all_rows.remove(row)
        self._apply_filter()

    def clear_settings_click(self, sender, e):
        self.tb_find.Text = ""
        self.tb_replace.Text = ""
        self.tb_prefix.Text = ""
        self.tb_suffix.Text = ""
        self.status_text.Text = "Settings cleared."

    def undo_click(self, sender, e):
        for r in self._all_rows:
            r.FamilyName = r.OriginalFamilyName
            r.TypeName = r.OriginalTypeName
            r.WorksetId = r.OriginalWorksetId
        self.dg_families.Items.Refresh()
        self.status_text.Text = "Staged changes discarded."

    def apply_click(self, sender, e):
        modified_rows = [r for r in self._all_rows if r.IsModified]
        if not modified_rows:
            forms.alert("No changes to apply.")
            return

        success_count = 0
        error_count = 0
        worksets_changed = any(doc.IsWorkshared and r.WorksetId != r.OriginalWorksetId for r in modified_rows)
        
        instances_by_type = {}
        if worksets_changed:
            try:
                all_instances = FilteredElementCollector(doc).WhereElementIsNotElementType().ToElements()
                for inst in all_instances:
                    tid = inst.GetTypeId()
                    if tid and tid != ElementId.InvalidElementId:
                        tid_val = eid_value(tid)
                        if tid_val not in instances_by_type:
                            instances_by_type[tid_val] = []
                        instances_by_type[tid_val].append(inst)
            except Exception:
                pass

        t = Transaction(doc, "T3Lab - Family Management Apply")
        t.Start()
        try:
            for r in modified_rows:
                try:
                    if r.FamilyName != r.OriginalFamilyName:
                        sanitized_fam = sanitize_name(r.FamilyName)
                        if r.is_loadable and hasattr(r.element, "Family"):
                            r.element.Family.Name = sanitized_fam

                    if r.TypeName != r.OriginalTypeName:
                        sanitized_type = sanitize_name(r.TypeName)
                        r.element.Name = sanitized_type

                    if doc.IsWorkshared and r.WorksetId != r.OriginalWorksetId:
                        ws_val = r.WorksetId
                        ws_param = r.element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
                        if ws_param and not ws_param.IsReadOnly:
                            ws_param.Set(ws_val)
                            
                        related_instances = instances_by_type.get(eid_value(r.element.Id), [])
                        for inst in related_instances:
                            try:
                                inst_ws_param = inst.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
                                if inst_ws_param and not inst_ws_param.IsReadOnly:
                                    inst_ws_param.Set(ws_val)
                            except Exception:
                                pass
                    success_count += 1
                except Exception:
                    error_count += 1

            t.Commit()
            forms.alert(
                "Successfully applied changes to {} elements.\nErrors: {}".format(
                    success_count, error_count
                ),
                title="Apply Success"
            )
            self._refresh_data()
        except Exception as ex:
            t.RollBack()
            forms.alert("Transaction failed: {}".format(ex))

    def export_list_click(self, sender, e):
        dest_file = forms.save_file(filesfilter="Comma-Separated Values (*.csv)|*.csv", title="Export Family List")
        if not dest_file:
            return
        try:
            import csv
            with open(dest_file, "wb") as f:
                writer = csv.writer(f)
                writer.writerow(["Family Name", "Type Name", "Category", "Workset ID"])
                for r in self._visible_rows:
                    writer.writerow([
                        r.FamilyName.encode('utf-8'),
                        r.TypeName.encode('utf-8'),
                        r.CategoryName.encode('utf-8'),
                        r.WorksetId
                    ])
            self.status_text.Text = "Exported list to: {}".format(dest_file)
        except Exception as ex:
            forms.alert("Failed to export list:\n{}".format(ex))

    # CLEANUP & SHUTDOWN
    # ==============================================================================
    def _cleanup(self):
        try:
            self._thumb_cancel = True
            for family in self.filtered_families:
                try:
                    family.PropertyChanged -= self.on_family_property_changed
                except Exception:
                    pass
            for family in self.all_families:
                if hasattr(family, 'Dispose'):
                    family.Dispose()
            self.filtered_families.Clear()
            self.all_families = []
            self.category_structure = {}
        except Exception:
            pass

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_dg_families_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua dg_families."""
        self.toggle_all_rows(self.dg_families, "IsSelected", sender.IsChecked)


def show_family_manager(script_dir=None, revit=None, default_tab=0):
    try:
        if script_dir is None:
            script_dir = os.path.dirname(__file__)
        if revit is None:
            from pyrevit import revit as rvt
            revit = rvt
        window = ManaFamiWindow(script_dir, revit)
        if default_tab == 1:
            if getattr(window, 'main_tab_control', None) is not None:
                window.main_tab_control.SelectedIndex = 1
            if getattr(window, 'nav_management', None) is not None:
                window.nav_management.IsChecked = True
            if getattr(window, 'nav_loader', None) is not None:
                window.nav_loader.IsChecked = False
            if getattr(window, 'lbl_title', None) is not None:
                window.lbl_title.Text = "Family Management"
            if getattr(window, 'lbl_subtitle', None) is not None:
                window.lbl_subtitle.Text = "Batch rename families/types, modify case, customize prefix/suffix, and assign worksets"
        else:
            if getattr(window, 'main_tab_control', None) is not None:
                window.main_tab_control.SelectedIndex = 0
            if getattr(window, 'nav_loader', None) is not None:
                window.nav_loader.IsChecked = True
            if getattr(window, 'nav_management', None) is not None:
                window.nav_management.IsChecked = False
            if getattr(window, 'lbl_title', None) is not None:
                window.lbl_title.Text = "Family Loader"
            if getattr(window, 'lbl_subtitle', None) is not None:
                window.lbl_subtitle.Text = "Load Revit families from local folders"
        window.ShowDialog()
    except Exception as ex:
        logger.error("Error running Family Manager:\n{}".format(traceback.format_exc()))
        forms.alert("Error running Family Manager:\n{}".format(ex))
