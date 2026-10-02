# -*- coding: utf-8 -*-
"""
Views & Sheets Scanners (Phase 4)
Scanners for views and sheets: empty sheets, unused schedules, legend views, temp/working views

Copyright (c) 2025 T3Lab
"""

__author__ = "T3Lab"

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSheet,
    ViewSchedule,
    View,
    ViewType,
    BuiltInCategory,
    ElementId
)
from Autodesk.Revit import DB

try:
    from .purge_scanner import BasePurgeScanner
except:
    # Fallback for testing
    class BasePurgeScanner:
        def __init__(self, doc):
            self.doc = doc

try:
    from .revit_utils import _eid_int
except:
    # Fallback if revit_utils not available
    def _eid_int(element_id):
        """Get ElementId integer value - works for Revit 2024-2027"""
        if element_id is None:
            return -1
        try:
            if hasattr(element_id, 'Value'):
                return element_id.Value
        except:
            pass
        try:
            if hasattr(element_id, 'IntegerValue'):
                return element_id.IntegerValue
        except:
            pass
        return -1


class EmptySheetsScanner(BasePurgeScanner):
    """Scanner for empty sheets (sheets with no views placed)"""
    
    def scan(self):
        """Scan for sheets with no views placed"""
        unused_items = []
        
        try:
            # Get all sheets
            collector = FilteredElementCollector(self.doc)
            sheets = collector.OfClass(ViewSheet).ToElements()
            
            for sheet in sheets:
                try:
                    # Skip if null or invalid
                    if not sheet or not sheet.IsValidObject:
                        continue
                    
                    # Skip templates
                    if sheet.IsTemplate:
                        continue
                    
                    # Get all viewports on sheet
                    viewport_ids = sheet.GetAllViewports()
                    
                    # If no viewports, sheet is empty
                    if not viewport_ids or len(list(viewport_ids)) == 0:
                        # Get sheet info
                        sheet_number = sheet.SheetNumber if hasattr(sheet, 'SheetNumber') else "Unknown"
                        sheet_name = sheet.Name if hasattr(sheet, 'Name') else "Unknown"
                        
                        item = self.create_item_dict(sheet, {
                            'type': 'Empty Sheet',
                            'sheet_number': sheet_number,
                            'sheet_name': sheet_name,
                            'viewport_count': 0
                        })
                        unused_items.append(item)
                
                except Exception as e:
                    # Skip problematic sheets
                    continue
        
        except Exception as e:
            print("Error scanning empty sheets: {}".format(str(e)))
        
        return unused_items


class UnusedSchedulesScanner(BasePurgeScanner):
    """Scanner for schedules not placed on any sheets"""
    
    def scan(self):
        """Scan for schedules not placed on sheets"""
        unused_items = []
        
        try:
            # Get all schedules
            collector = FilteredElementCollector(self.doc)
            schedules = collector.OfClass(ViewSchedule).ToElements()
            
            # Get all sheets to check schedule placement
            sheet_collector = FilteredElementCollector(self.doc)
            sheets = list(sheet_collector.OfClass(ViewSheet).ToElements())
            
            # Build a set of schedule IDs that are on sheets
            schedules_on_sheets = set()
            for sheet in sheets:
                try:
                    if not sheet or not sheet.IsValidObject:
                        continue
                    
                    # Get all viewports
                    viewport_ids = sheet.GetAllViewports()
                    for vp_id in viewport_ids:
                        viewport = self.doc.GetElement(vp_id)
                        if viewport:
                            view_id = viewport.ViewId
                            view = self.doc.GetElement(view_id)
                            # Check if view is a schedule
                            if isinstance(view, ViewSchedule):
                                schedules_on_sheets.add(_eid_int(view_id))
                except:
                    continue
            
            # Check each schedule
            for schedule in schedules:
                try:
                    # Skip if null or invalid
                    if not schedule or not schedule.IsValidObject:
                        continue
                    
                    # Skip templates
                    if schedule.IsTemplate:
                        continue
                    
                    # Skip internal/system schedules
                    try:
                        # Check if it's a revision schedule (system schedule)
                        if hasattr(schedule, 'Definition'):
                            definition = schedule.Definition
                            if definition and hasattr(definition, 'CategoryId'):
                                cat_id = definition.CategoryId
                                if cat_id == ElementId(BuiltInCategory.OST_Revisions):
                                    continue
                    except:
                        pass
                    
                    # If not on any sheet, it's unused
                    if _eid_int(schedule.Id) not in schedules_on_sheets:
                        schedule_type = "Schedule"
                        try:
                            # Try to get schedule type
                            if hasattr(schedule, 'Definition'):
                                definition = schedule.Definition
                                if definition:
                                    schedule_type = "Schedule"
                        except:
                            pass
                        
                        item = self.create_item_dict(schedule, {
                            'type': schedule_type,
                            'status': 'Not on any sheet'
                        })
                        unused_items.append(item)
                
                except Exception as e:
                    # Skip problematic schedules
                    continue
        
        except Exception as e:
            print("Error scanning unused schedules: {}".format(str(e)))
        
        return unused_items


class LegendViewsScanner(BasePurgeScanner):
    """Scanner for legend views not placed on any sheets"""
    
    def scan(self):
        """Scan for legend views not on sheets"""
        unused_items = []
        
        try:
            # Get all legend views
            collector = FilteredElementCollector(self.doc)
            views = collector.OfClass(View).ToElements()
            
            # Filter to legend views only
            legend_views = []
            for view in views:
                try:
                    if view.ViewType == ViewType.Legend:
                        legend_views.append(view)
                except:
                    continue
            
            # Get all sheets to check legend placement
            sheet_collector = FilteredElementCollector(self.doc)
            sheets = list(sheet_collector.OfClass(ViewSheet).ToElements())
            
            # Build a set of view IDs that are on sheets
            views_on_sheets = set()
            for sheet in sheets:
                try:
                    if not sheet or not sheet.IsValidObject:
                        continue
                    
                    # Get all viewports
                    viewport_ids = sheet.GetAllViewports()
                    for vp_id in viewport_ids:
                        viewport = self.doc.GetElement(vp_id)
                        if viewport:
                            view_id = viewport.ViewId
                            views_on_sheets.add(_eid_int(view_id))
                except:
                    continue
            
            # Check each legend view
            for legend in legend_views:
                try:
                    # Skip if null or invalid
                    if not legend or not legend.IsValidObject:
                        continue
                    
                    # Skip templates
                    if legend.IsTemplate:
                        continue
                    
                    # If not on any sheet, it's unused
                    if _eid_int(legend.Id) not in views_on_sheets:
                        item = self.create_item_dict(legend, {
                            'type': 'Legend View',
                            'status': 'Not on any sheet'
                        })
                        unused_items.append(item)
                
                except Exception as e:
                    # Skip problematic legends
                    continue
        
        except Exception as e:
            print("Error scanning legend views: {}".format(str(e)))
        
        return unused_items


class TempWorkingViewsScanner(BasePurgeScanner):
    """Scanner for temporary and working views based on naming patterns"""
    
    def __init__(self, doc):
        """Initialize scanner"""
        super(TempWorkingViewsScanner, self).__init__(doc)
        
        # Patterns to identify temp/working views (case insensitive)
        self.temp_patterns = [
            'temp',
            'test',
            'working',
            'copy',
            'old',
            'backup',
            'draft',
            'wip',
            'tmp',
            'delete',
            'unused',
            'archive',
            'obsolete'
        ]
    
    def scan(self):
        """Scan for views with temp/working naming patterns"""
        unused_items = []
        
        try:
            # Get all views (excluding sheets and schedules)
            collector = FilteredElementCollector(self.doc)
            views = collector.OfClass(View).ToElements()
            
            for view in views:
                try:
                    # Skip if null or invalid
                    if not view or not view.IsValidObject:
                        continue
                    
                    # Skip templates
                    if view.IsTemplate:
                        continue
                    
                    # Skip sheets and schedules (handled by other scanners)
                    if view.ViewType == ViewType.DrawingSheet:
                        continue
                    if isinstance(view, ViewSchedule):
                        continue
                    
                    # Skip system views (3D orthographic, etc.)
                    try:
                        if view.ViewType == ViewType.ThreeD:
                            # Check if it's the default 3D view
                            if view.IsDefaultView():
                                continue
                    except:
                        pass
                    
                    # Get view name
                    view_name = view.Name if hasattr(view, 'Name') else ""
                    view_name_lower = view_name.lower()
                    
                    # Check if name matches any temp pattern
                    is_temp = False
                    matched_pattern = ""
                    
                    for pattern in self.temp_patterns:
                        # Check if pattern is at start of name
                        if view_name_lower.startswith(pattern):
                            is_temp = True
                            matched_pattern = pattern
                            break
                        # Check if pattern is in name (with word boundaries)
                        if ' ' + pattern in view_name_lower:
                            is_temp = True
                            matched_pattern = pattern
                            break
                        if pattern + ' ' in view_name_lower:
                            is_temp = True
                            matched_pattern = pattern
                            break
                        # Check for pattern with delimiters
                        if '_' + pattern in view_name_lower or pattern + '_' in view_name_lower:
                            is_temp = True
                            matched_pattern = pattern
                            break
                        if '-' + pattern in view_name_lower or pattern + '-' in view_name_lower:
                            is_temp = True
                            matched_pattern = pattern
                            break
                    
                    # If matches temp pattern, add to list
                    if is_temp:
                        # Get view type name
                        view_type_name = "View"
                        try:
                            view_type_name = view.ViewType.ToString()
                        except:
                            pass
                        
                        item = self.create_item_dict(view, {
                            'type': view_type_name,
                            'pattern': matched_pattern,
                            'reason': 'Temp/working view pattern'
                        })
                        unused_items.append(item)
                
                except Exception as e:
                    # Skip problematic views
                    continue
        
        except Exception as e:
            print("Error scanning temp/working views: {}".format(str(e)))
        
        return unused_items