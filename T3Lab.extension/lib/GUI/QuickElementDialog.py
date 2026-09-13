# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        QUICK SELECT MANAGER v2.1                              ║
║                              pyDQT Tool Suite                                 ║
║                                                                               ║
║  Quickly find, select, and navigate to any element in your Revit model       ║
║  Standard DQT UI with DataGrid, Checkbox, Filters                            ║
║  Compatible with Revit 2024 - 2027                                           ║
║                                                                               ║
║  Copyright (c) 2025 Dang Quoc Truong - DQT                                   ║
║  All rights reserved.                                                         ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

__title__ = "Quick\nSelect"
__author__ = "Dang Quoc Truong (DQT)"
__doc__ = "Quickly find, select, and zoom to any element in your Revit model."

# =============================================================================
# IMPORTS
# =============================================================================
import clr
import os
import io
clr.AddReference('System')
clr.AddReference('System.Core')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

import System
from System import Action
from System.Windows import Window, WindowStartupLocation, Thickness, WindowState
from System.Windows.Controls import ListBoxItem
from System.Windows.Threading import DispatcherPriority
from System.Windows.Input import Keyboard, Key
from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, ElementId, Element,
    ViewPlan, View3D, BuiltInParameter, XYZ, BoundingBoxXYZ,
    Transaction
)

from pyrevit import revit, forms
from GUI.WPF_Base import T3WPFWindow, setup_window_logo
try:
    from Snippets._host import resolve_doc, resolve_uidoc
except ImportError:
    try:
        import importlib
        import Snippets._host
        importlib.reload(Snippets._host)
        from Snippets._host import resolve_doc, resolve_uidoc
    except Exception:
        from Snippets._host import resolve_doc
        def resolve_uidoc(candidate=None):
            if candidate is not None and hasattr(candidate, 'Document') and candidate.Document is not None:
                return candidate
            try:
                from pyrevit import revit
                return revit.uidoc
            except Exception:
                return None

import traceback
from collections import defaultdict

xaml_path = os.path.join(os.path.dirname(__file__), 'Tools', 'QuickElement.xaml')

# =============================================================================
# REVIT VERSION COMPATIBILITY HELPER
# =============================================================================
def _eid_int(element_id):
    """Get integer value from ElementId - compatible with Revit 2024-2027.
    Revit 2024+: ElementId.Value (long)
    Revit 2023 and earlier: ElementId.IntegerValue (int)
    """
    try:
        # Revit 2024+ uses .Value
        return element_id.Value
    except AttributeError:
        # Revit 2023 and earlier uses .IntegerValue
        return element_id.IntegerValue

# =============================================================================

# =============================================================================
# ELEMENT ITEM CLASS
# =============================================================================
class ElementItem(System.Object):
    """Data class for element display in DataGrid with INotifyPropertyChanged"""
    
    def __init__(self, element, doc):
        self.element = element
        self.doc = doc
        self.id = element.Id
        
        # Properties for DataGrid binding
        self._is_checked = False
        self.element_id = str(_eid_int(element.Id))  # Use helper for Revit 2024+ compatibility
        self.category = self._get_category()
        self.family = self._get_family()
        self.type_name = self._get_type()
        self.name = self._get_name()
        self.level = self._get_level()
    
    @property
    def is_checked(self):
        return self._is_checked
    
    @is_checked.setter
    def is_checked(self, value):
        if self._is_checked != value:
            self._is_checked = value
    
    def _get_category(self):
        try:
            if self.element.Category:
                return self.element.Category.Name
        except:
            pass
        return "Unknown"
    
    def _get_family(self):
        try:
            # For FamilyInstance
            if hasattr(self.element, 'Symbol') and self.element.Symbol:
                if self.element.Symbol.Family:
                    return self.element.Symbol.Family.Name
            
            # For other elements with type
            type_id = self.element.GetTypeId()
            if type_id and type_id != ElementId.InvalidElementId:
                elem_type = self.doc.GetElement(type_id)
                if elem_type:
                    param = elem_type.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                    if param and param.AsString():
                        return param.AsString()
        except:
            pass
        return "System"
    
    def _get_type(self):
        try:
            type_id = self.element.GetTypeId()
            if type_id and type_id != ElementId.InvalidElementId:
                elem_type = self.doc.GetElement(type_id)
                if elem_type and hasattr(elem_type, 'Name'):
                    return elem_type.Name
        except:
            pass
        try:
            return self.element.Name
        except:
            pass
        return "Unknown"
    
    def _get_name(self):
        # Try Mark parameter
        try:
            mark = self.element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if mark and mark.AsString():
                return mark.AsString()
        except:
            pass
        
        # Try Name
        try:
            if self.element.Name:
                return self.element.Name
        except:
            pass
        
        return "ID: " + self.element_id
    
    def _get_level(self):
        try:
            # Try different level parameters
            for param_id in [BuiltInParameter.FAMILY_LEVEL_PARAM, 
                            BuiltInParameter.SCHEDULE_LEVEL_PARAM,
                            BuiltInParameter.FAMILY_BASE_LEVEL_PARAM]:
                param = self.element.get_Parameter(param_id)
                if param:
                    level_id = param.AsElementId()
                    if level_id and level_id != ElementId.InvalidElementId:
                        level = self.doc.GetElement(level_id)
                        if level:
                            return level.Name
        except:
            pass
        return "-"


# =============================================================================
# DATA COLLECTOR
# =============================================================================
class ElementCollector(object):
    """Collects elements from Revit model"""
    
    # Model categories
    MODEL_CATEGORIES = [
        BuiltInCategory.OST_Walls,
        BuiltInCategory.OST_Floors,
        BuiltInCategory.OST_Ceilings,
        BuiltInCategory.OST_Roofs,
        BuiltInCategory.OST_Doors,
        BuiltInCategory.OST_Windows,
        BuiltInCategory.OST_Furniture,
        BuiltInCategory.OST_FurnitureSystems,
        BuiltInCategory.OST_Casework,
        BuiltInCategory.OST_Columns,
        BuiltInCategory.OST_GenericModel,
        BuiltInCategory.OST_SpecialityEquipment,
        BuiltInCategory.OST_Stairs,
        BuiltInCategory.OST_StairsRailing,
        BuiltInCategory.OST_Ramps,
        BuiltInCategory.OST_CurtainWallPanels,
        BuiltInCategory.OST_CurtainWallMullions,
        BuiltInCategory.OST_Parking,
        BuiltInCategory.OST_Planting,
        BuiltInCategory.OST_Entourage,
        BuiltInCategory.OST_Topography,
    ]
    
    # Annotation categories  
    ANNOTATION_CATEGORIES = [
        BuiltInCategory.OST_TextNotes,
        BuiltInCategory.OST_Dimensions,
        BuiltInCategory.OST_DetailComponents,
        BuiltInCategory.OST_GenericAnnotation,
        BuiltInCategory.OST_RevisionClouds,
        BuiltInCategory.OST_Grids,
        BuiltInCategory.OST_Levels,
    ]
    
    # MEP categories
    MEP_CATEGORIES = [
        BuiltInCategory.OST_MechanicalEquipment,
        BuiltInCategory.OST_ElectricalEquipment,
        BuiltInCategory.OST_ElectricalFixtures,
        BuiltInCategory.OST_LightingFixtures,
        BuiltInCategory.OST_PlumbingFixtures,
        BuiltInCategory.OST_PipeCurves,
        BuiltInCategory.OST_PipeFitting,
        BuiltInCategory.OST_PipeAccessory,
        BuiltInCategory.OST_DuctCurves,
        BuiltInCategory.OST_DuctFitting,
        BuiltInCategory.OST_DuctAccessory,
        BuiltInCategory.OST_DuctTerminal,
        BuiltInCategory.OST_FlexDuctCurves,
        BuiltInCategory.OST_FlexPipeCurves,
        BuiltInCategory.OST_Conduit,
        BuiltInCategory.OST_ConduitFitting,
        BuiltInCategory.OST_CableTray,
        BuiltInCategory.OST_CableTrayFitting,
        BuiltInCategory.OST_Sprinklers,
    ]
    
    # Structural categories
    STRUCTURAL_CATEGORIES = [
        BuiltInCategory.OST_StructuralColumns,
        BuiltInCategory.OST_StructuralFraming,
        BuiltInCategory.OST_StructuralFoundation,
    ]
    
    def __init__(self, doc, uidoc):
        self.doc = doc
        self.uidoc = uidoc
    
    def get_categories(self, filter_type="All Elements"):
        """Get categories based on filter"""
        if filter_type == "Model Elements":
            return self.MODEL_CATEGORIES
        elif filter_type == "Annotation":
            return self.ANNOTATION_CATEGORIES
        elif filter_type == "MEP Elements":
            return self.MEP_CATEGORIES
        elif filter_type == "Structural":
            return self.STRUCTURAL_CATEGORIES
        else:
            return self.MODEL_CATEGORIES + self.ANNOTATION_CATEGORIES + self.MEP_CATEGORIES + self.STRUCTURAL_CATEGORIES
    
    def collect(self, display_mode="Active View", filter_type="All Elements"):
        """Collect elements based on display mode and filter"""
        categories = self.get_categories(filter_type)
        elements = []
        
        for bic in categories:
            try:
                if display_mode == "Active View":
                    active_view = self.doc.ActiveView
                    if not active_view:
                        continue
                    collector = FilteredElementCollector(self.doc, active_view.Id)
                elif display_mode == "Current Selection":
                    # Handle current selection separately
                    continue
                else:  # Entire Project
                    collector = FilteredElementCollector(self.doc)
                
                collector = collector.OfCategory(bic).WhereElementIsNotElementType()
                
                for elem in collector:
                    if elem and elem.Id:
                        try:
                            item = ElementItem(elem, self.doc)
                            elements.append(item)
                        except:
                            pass
                    
            except:
                pass
        
        # Handle current selection
        if display_mode == "Current Selection":
            selection = self.uidoc.Selection.GetElementIds()
            for elem_id in selection:
                try:
                    elem = self.doc.GetElement(elem_id)
                    if elem:
                        item = ElementItem(elem, self.doc)
                        elements.append(item)
                except:
                    pass
        
        return elements


# =============================================================================
# MAIN WINDOW CLASS
# =============================================================================
class QuickSelectWindow(T3WPFWindow):
    """Quick Select Manager Window"""
    
    def __init__(self, set_owner=True):
        T3WPFWindow.__init__(self, xaml_path, set_owner=set_owner)
        
        # IMPORTANT: Store reference to the loaded content for FindName
        self._content = self.Content
        setup_window_logo(self)
        
        # Get Revit references safely
        self.doc, _ = resolve_doc()
        self.uidoc = resolve_uidoc()
        self.collector = ElementCollector(self.doc, self.uidoc) if (self.doc and self.uidoc) else None
        
        # Data storage
        self.all_items = []
        self.filtered_items = []
        self.categories_list = []

        # Revit API context bridge. Stays None when this window runs on its own
        # (it is modal then, so it already holds the API context). ManaSelect
        # mounts this grid inside a MODELESS window and sets this to its
        # ExternalEvent dispatcher — without it every button below that touches
        # uidoc/doc throws "Attempting to access Revit API outside of API
        # context". See _defer().
        self._api_dispatch = None
        self._in_api_call = False

        # Get UI controls
        self._get_controls()
        self._setup_events()
        
        # Load data
        if self.collector:
            self._load_data()
    
    def _defer(self, handler, sender, args):
        """Re-enter `handler` inside the Revit API context.

        Returns True when the call was queued — the caller must return
        immediately, the real work happens on the second pass.

        Standalone (modal): `_api_dispatch` is None, this returns False at once
        and the handler body runs exactly as it always has. Nothing changes.

        Mounted in ManaSelect (modeless): the host set `_api_dispatch` to its
        ExternalEvent dispatcher. First pass queues, second pass has
        `_in_api_call` set so it falls straight through and runs the body — now
        inside the API context.
        """
        dispatch = getattr(self, '_api_dispatch', None)
        if dispatch is None or self._in_api_call:
            return False

        def run():
            self._in_api_call = True
            try:
                handler(sender, args)
            finally:
                self._in_api_call = False

        dispatch(run)
        return True

    def _find(self, name):
        """Helper to find control by name"""
        ctrl = getattr(self, name, None)
        if ctrl is not None:
            return ctrl
        try:
            # Try finding in content
            if self._content and hasattr(self._content, 'FindName'):
                ctrl = self._content.FindName(name)
                if ctrl is not None:
                    return ctrl
        except Exception:
            pass
        
        # Try FindName on window
        try:
            ctrl = self.FindName(name)
            if ctrl is not None:
                return ctrl
        except Exception:
            pass

        # Try LogicalTreeHelper
        try:
            from System.Windows import LogicalTreeHelper
            if self._content:
                ctrl = LogicalTreeHelper.FindLogicalNode(self._content, name)
                if ctrl is not None:
                    return ctrl
        except Exception:
            pass
        
        return None
    
    def _get_controls(self):
        """Get references to UI controls"""
        self.cmbDisplay = self._find("cmbDisplay")
        self.txtSearch = self._find("txtSearch")
        self.cmbFilter = self._find("cmbFilter")
        self.lstCategories = self._find("lstCategories")
        self.dataGrid = self._find("dataGrid")
        
        # Stats
        self.txtTotal = self._find("txtTotal")
        self.txtChecked = self._find("txtChecked")
        self.txtCategories = self._find("txtCategories")
        self.txtFamilies = self._find("txtFamilies")
        
        # Buttons
        self.btnCheckAll = self._find("btnCheckAll")
        self.btnCheckNone = self._find("btnCheckNone")
        self.btnInvert = self._find("btnInvert")
        self.btnZoom = self._find("btnZoom")
        self.btnSelect = self._find("btnSelect")
        self.btnIsolate = self._find("btnIsolate")
        self.btnShow = self._find("btnShow")
        self.btnRefresh = self._find("btnRefresh")
        self.btnClose = self._find("btnClose")

        # Window chrome (collapsed/hidden when embedded inside ManaSelect)
        self.btn_minimize = self._find("btn_minimize")
        self.btn_maximize = self._find("btn_maximize")
        self.btn_close_chrome = self._find("btn_close")

    def _setup_events(self):
        """Setup event handlers"""
        # ComboBox events
        if self.cmbDisplay:
            self.cmbDisplay.SelectionChanged += self._on_display_changed
        if self.cmbFilter:
            self.cmbFilter.SelectionChanged += self._on_filter_changed
        
        # Search
        if self.txtSearch:
            self.txtSearch.TextChanged += self._on_search_changed
        
        # Category list
        if self.lstCategories:
            self.lstCategories.SelectionChanged += self._on_category_changed
        
        # DataGrid
        if self.dataGrid:
            self.dataGrid.MouseDoubleClick += self._on_double_click
            self.dataGrid.PreviewMouseLeftButtonDown += self._on_checkbox_click
        
        # Buttons
        if self.btnCheckAll:
            self.btnCheckAll.Click += self._on_check_all
        if self.btnCheckNone:
            self.btnCheckNone.Click += self._on_check_none
        if self.btnInvert:
            self.btnInvert.Click += self._on_invert
        if self.btnZoom:
            self.btnZoom.Click += self._on_zoom
        if self.btnSelect:
            self.btnSelect.Click += self._on_select
        if self.btnIsolate:
            self.btnIsolate.Click += self._on_isolate
        if self.btnShow:
            self.btnShow.Click += self._on_show
        if self.btnRefresh:
            self.btnRefresh.Click += self._on_refresh
        if self.btnClose:
            self.btnClose.Click += self._on_close

        # Window chrome
        if self.btn_minimize:
            self.btn_minimize.Click += self._minimize_window
        if self.btn_maximize:
            self.btn_maximize.Click += self._maximize_window
        if self.btn_close_chrome:
            self.btn_close_chrome.Click += self._on_close

    def _load_data(self):
        """Load element data"""
        try:
            if not self.collector:
                self.doc, _ = resolve_doc()
                self.uidoc = resolve_uidoc()
                if self.doc and self.uidoc:
                    self.collector = ElementCollector(self.doc, self.uidoc)
            if not self.collector:
                return

            # Get display mode
            display_mode = "Active View"
            if self.cmbDisplay and self.cmbDisplay.SelectedItem:
                display_mode = self.cmbDisplay.SelectedItem.Content
            
            # Get filter type
            filter_type = "All Elements"
            if self.cmbFilter and self.cmbFilter.SelectedItem:
                filter_type = self.cmbFilter.SelectedItem.Content
            
            # Collect elements
            self.all_items = self.collector.collect(display_mode, filter_type)
            
            # If Active View returns 0 elements, try Entire Project
            if len(self.all_items) == 0 and display_mode == "Active View":
                self.all_items = self.collector.collect("Entire Project", filter_type)
            
            # Build category list
            self._build_category_list()
            
            # Apply filters and update display
            self._apply_filters()
            
        except Exception as e:
            print("Error loading data: {}".format(str(e)))
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass
    
    def _build_category_list(self):
        """Build category list for filter"""
        if not self.lstCategories:
            return
        
        self.lstCategories.Items.Clear()
        
        # Get unique categories with counts
        cat_counts = defaultdict(int)
        for item in self.all_items:
            cat_counts[item.category] += 1
        
        # Sort by name
        sorted_cats = sorted(cat_counts.items(), key=lambda x: x[0])
        
        # Add "All" option
        all_item = ListBoxItem()
        all_item.Content = "All ({})".format(len(self.all_items))
        all_item.Tag = "ALL"
        self.lstCategories.Items.Add(all_item)
        
        # Add categories
        for cat_name, count in sorted_cats:
            item = ListBoxItem()
            item.Content = "{} ({})".format(cat_name, count)
            item.Tag = cat_name
            self.lstCategories.Items.Add(item)
        
        # Select "All" by default
        if self.lstCategories.Items.Count > 0:
            self.lstCategories.SelectedIndex = 0
        
        self.categories_list = [cat[0] for cat in sorted_cats]
    
    def _apply_filters(self):
        """Apply search and category filters"""
        # Start with all items
        self.filtered_items = self.all_items[:]
        
        # Search filter
        search_text = ""
        if self.txtSearch:
            search_text = self.txtSearch.Text.lower().strip() if self.txtSearch.Text else ""
        
        if search_text:
            self.filtered_items = [
                item for item in self.filtered_items
                if search_text in item.category.lower() or
                   search_text in item.family.lower() or
                   search_text in item.type_name.lower() or
                   search_text in item.name.lower() or
                   search_text in item.element_id
            ]
        
        # Category filter
        if self.lstCategories and self.lstCategories.SelectedItem:
            try:
                selected_tag = self.lstCategories.SelectedItem.Tag
                if selected_tag and selected_tag != "ALL":
                    self.filtered_items = [
                        item for item in self.filtered_items
                        if item.category == selected_tag
                    ]
            except:
                pass
        
        # Update DataGrid
        self._update_datagrid()
        self._update_stats()
    
    def _update_datagrid(self):
        """Update DataGrid with filtered items"""
        if not self.dataGrid:
            return
        
        try:
            # Clear current items
            self.dataGrid.ItemsSource = None
            
            # Use System.Collections.ObjectModel.ObservableCollection for proper binding
            from System.Collections.ObjectModel import ObservableCollection
            from System import Object
            observable = ObservableCollection[Object]()
            for item in self.filtered_items:
                observable.Add(item)
            
            self.dataGrid.ItemsSource = observable
            
        except Exception as ex:
            print("Error updating DataGrid: {}".format(str(ex)))
    
    def _update_stats(self):
        """Update statistics display"""
        # Total
        if self.txtTotal:
            self.txtTotal.Text = str(len(self.filtered_items))
        
        # Checked count
        checked_count = sum(1 for item in self.filtered_items if item.is_checked)
        if self.txtChecked:
            self.txtChecked.Text = str(checked_count)
        
        # Categories
        unique_cats = set(item.category for item in self.filtered_items)
        if self.txtCategories:
            self.txtCategories.Text = str(len(unique_cats))
        
        # Families
        unique_fams = set(item.family for item in self.filtered_items)
        if self.txtFamilies:
            self.txtFamilies.Text = str(len(unique_fams))
    
    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================
    
    def _on_display_changed(self, sender, args):
        """Handle display mode change"""
        if self._defer(self._on_display_changed, sender, args):
            return
        self._load_data()
    
    def _on_filter_changed(self, sender, args):
        """Handle filter type change"""
        if self._defer(self._on_filter_changed, sender, args):
            return
        self._load_data()
    
    def _on_search_changed(self, sender, args):
        """Handle search text change"""
        self._apply_filters()
    
    def _on_category_changed(self, sender, args):
        """Handle category selection change"""
        self._apply_filters()
    
    def _on_checkbox_click(self, sender, args):
        """Handle checkbox click - support multi-select"""
        from System.Windows.Controls import CheckBox, DataGridRow
        from System.Windows.Media import VisualTreeHelper
        
        # Check if clicked on checkbox
        element = args.OriginalSource
        current = element
        clicked_checkbox = None
        
        while current is not None:
            if isinstance(current, CheckBox):
                clicked_checkbox = current
                break
            try:
                current = VisualTreeHelper.GetParent(current)
            except:
                break
        
        # Not clicking on checkbox, let normal behavior happen
        if not clicked_checkbox:
            return
        
        # Find DataGridRow
        row = None
        current = args.OriginalSource
        while current is not None:
            if isinstance(current, DataGridRow):
                row = current
                break
            try:
                current = VisualTreeHelper.GetParent(current)
            except:
                break
        
        if row is None or row.Item is None:
            return
        
        clicked_item = row.Item
        
        # Get selected items from DataGrid
        try:
            selected_items = list(self.dataGrid.SelectedItems) if self.dataGrid.SelectedItems else []
        except:
            selected_items = []
        
        # Check if clicked item is in the current selection
        if len(selected_items) > 1 and clicked_item in selected_items:
            # Multi-select mode: toggle all selected items
            args.Handled = True  # Prevent default checkbox behavior
            
            new_state = not clicked_item.is_checked
            for item in selected_items:
                item.is_checked = new_state
            
            # Deferred refresh
            def refresh():
                try:
                    self.dataGrid.Items.Refresh()
                    self._update_stats()
                except:
                    pass
            
            self.dataGrid.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                Action(refresh)
            )
        else:
            # Single item - let default behavior happen, then update stats
            def update_stats():
                try:
                    self._update_stats()
                except:
                    pass
            
            self.dataGrid.Dispatcher.BeginInvoke(
                DispatcherPriority.Background,
                Action(update_stats)
            )
    
    def _on_double_click(self, sender, args):
        """Handle double-click to zoom"""
        if self._defer(self._on_double_click, sender, args):
            return
        if self.dataGrid.SelectedItem:
            item = self.dataGrid.SelectedItem
            self._zoom_to_element(item.id)
    
    def _on_check_all(self, sender, args):
        """Check all visible items"""
        for item in self.filtered_items:
            item.is_checked = True
        self.dataGrid.Items.Refresh()
        self._update_stats()
    
    def _on_check_none(self, sender, args):
        """Uncheck all items"""
        for item in self.filtered_items:
            item.is_checked = False
        self.dataGrid.Items.Refresh()
        self._update_stats()
    
    def _on_invert(self, sender, args):
        """Invert selection"""
        for item in self.filtered_items:
            item.is_checked = not item.is_checked
        self.dataGrid.Items.Refresh()
        self._update_stats()
    
    def _on_zoom(self, sender, args):
        """Zoom to checked elements"""
        if self._defer(self._on_zoom, sender, args):
            return
        checked = [item for item in self.filtered_items if item.is_checked]
        if not checked:
            if self.dataGrid.SelectedItem:
                self._zoom_to_element(self.dataGrid.SelectedItem.id)
            else:
                forms.alert("Please check elements or select a row first.", title="Quick Select")
            return
        
        # Zoom to first checked element
        self._zoom_to_element(checked[0].id)
    
    def _on_select(self, sender, args):
        """Select checked elements in Revit"""
        if self._defer(self._on_select, sender, args):
            return
        checked = [item for item in self.filtered_items if item.is_checked]
        if not checked:
            forms.alert("Please check elements first.", title="Quick Select")
            return
        
        if not self.uidoc:
            self.uidoc = resolve_uidoc()
        if not self.uidoc:
            forms.alert("No active document found in Revit.", title="Quick Select")
            return

        # Select in Revit
        id_list = List[ElementId]()
        for item in checked:
            id_list.Add(item.id)
        
        self.uidoc.Selection.SetElementIds(id_list)
        self._update_stats()
        forms.alert("{} element(s) selected in model.".format(len(checked)), title="Quick Select")
    
    def _on_isolate(self, sender, args):
        """Isolate checked elements in view"""
        if self._defer(self._on_isolate, sender, args):
            return
        checked = [item for item in self.filtered_items if item.is_checked]
        if not checked:
            forms.alert("Please check elements first.", title="Quick Select")
            return
        
        if not self.doc:
            self.doc, _ = resolve_doc()
        if not self.doc:
            forms.alert("No active document found in Revit.", title="Quick Select")
            return

        # Isolate in view
        id_list = List[ElementId]()
        for item in checked:
            id_list.Add(item.id)
        
        try:
            with Transaction(self.doc, "Isolate Elements") as t:
                t.Start()
                self.doc.ActiveView.IsolateElementsTemporary(id_list)
                t.Commit()
            forms.alert("{} element(s) isolated in view.".format(len(checked)), title="Quick Select")
        except Exception as e:
            forms.alert("Error isolating: {}".format(str(e)), title="Error")
    
    def _on_show(self, sender, args):
        """Show element - find view and zoom"""
        if self._defer(self._on_show, sender, args):
            return
        checked = [item for item in self.filtered_items if item.is_checked]
        if not checked:
            if self.dataGrid.SelectedItem:
                checked = [self.dataGrid.SelectedItem]
            else:
                forms.alert("Please check elements or select a row first.", title="Quick Select")
                return
        
        if not self.uidoc:
            self.uidoc = resolve_uidoc()
        if not self.uidoc:
            forms.alert("No active document found in Revit.", title="Quick Select")
            return

        # Show first element
        elem_id = checked[0].id
        try:
            self.uidoc.ShowElements(elem_id)
        except:
            self._zoom_to_element(elem_id)
    
    def _on_refresh(self, sender, args):
        """Refresh data"""
        if self._defer(self._on_refresh, sender, args):
            return
        doc, _ = resolve_doc()
        uidoc = resolve_uidoc()
        if doc and uidoc:
            self.doc = doc
            self.uidoc = uidoc
            self.collector = ElementCollector(self.doc, self.uidoc)
        self._load_data()
    
    def _on_close(self, sender, args):
        """Close window"""
        try:
            self.Close()
        except Exception:
            pass

    def _minimize_window(self, sender, args):
        """Minimize window (chrome button, only reachable when shown standalone)"""
        self.WindowState = WindowState.Minimized

    def _maximize_window(self, sender, args):
        """Toggle maximize window (chrome button, only reachable when shown standalone)"""
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized

    def _zoom_to_element(self, element_id):
        """Zoom to element"""
        try:
            if not self.doc:
                self.doc, _ = resolve_doc()
            if not self.uidoc:
                self.uidoc = resolve_uidoc()
            if not self.doc or not self.uidoc:
                return

            elem = self.doc.GetElement(element_id)
            if not elem:
                return
            
            bbox = elem.get_BoundingBox(self.doc.ActiveView)
            if bbox:
                offset = 3.0
                min_pt = XYZ(bbox.Min.X - offset, bbox.Min.Y - offset, bbox.Min.Z - offset)
                max_pt = XYZ(bbox.Max.X + offset, bbox.Max.Y + offset, bbox.Max.Z + offset)
                
                ui_views = self.uidoc.GetOpenUIViews()
                if ui_views.Count > 0:
                    ui_views[0].ZoomAndCenterRectangle(min_pt, max_pt)
        except Exception as e:
            print("Zoom error: {}".format(str(e)))

    # ── Select-all o header cot checkbox ────────────────────────────────
    # toggle_all_rows() nam trong T3WPFWindow: no chay tren grid.Items nen chi
    # dong dang hien thi (sau filter/sort) bi doi, dung nhu nguoi dung thay.

    def select_all_dataGrid_clicked(self, sender, e):
        """Header checkbox: chon/bo chon moi dong dang hien thi cua dataGrid."""
        self.toggle_all_rows(self.dataGrid, "is_checked", sender.IsChecked)


# =============================================================================
# MAIN
# =============================================================================
def main():
    try:
        window = QuickSelectWindow()
        window.ShowDialog()
    except Exception as e:
        print("Error: {}".format(str(e)))
        try:                     # ScriptIO has no write() under CPython
            traceback.print_exc()
        except Exception:
            pass
        forms.alert("Error: {}".format(str(e)), title="Quick Select Error")

def show_dialog():
    main()

if __name__ == "__main__":
    show_dialog()