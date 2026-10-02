# -*- coding: utf-8 -*-
"""
System Cleanup Scanners (Phase 3)
Scanners for system cleanup: imports, CAD, groups, design options, separators, orphaned rooms

Compatible with Revit 2024, 2025, 2026, 2027

Copyright (c) 2025 T3Lab

FIXED in this version:
- ImportSymbolsScanner now returns ALL import instances (not just unused)
- User can see and decide which imports to delete
- Added Revit 2026+ compatibility for ElementId.Value
"""

__author__ = "T3Lab"

import os
import sys

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ImportInstance,
    Group,
    DesignOption,
    ModelCurve,
    SpatialElement,
    Area,
    BuiltInCategory,
    ElementId,
    Transaction
)
from Autodesk.Revit.DB.Architecture import Room
from Autodesk.Revit import DB

# Import compatibility helper
try:
    from .revit_utils import _eid_int
except:
    # Fallback if revit_utils not available
    def _eid_int(element_id):
        """Get ElementId integer value - works for Revit 2024-2027"""
        if element_id is None:
            return -1
        try:
            # Revit 2026+ uses .Value
            if hasattr(element_id, 'Value'):
                return element_id.Value
        except:
            pass
        try:
            # Revit 2024/2025 uses .IntegerValue
            if hasattr(element_id, 'IntegerValue'):
                return element_id.IntegerValue
        except:
            pass
        return -1

try:
    from .purge_scanner import BasePurgeScanner
except:
    # Fallback for testing
    class BasePurgeScanner:
        def __init__(self, doc):
            self.doc = doc


class ImportSymbolsScanner(BasePurgeScanner):
    """Scanner for import symbols (DWG, DXF, etc.)
    
    FIXED: Now returns ALL import instances, not just "unused" ones.
    This allows users to see and manage all CAD imports in the model.
    """
    
    def scan(self):
        """Scan for ALL import instances"""
        print("DEBUG: ImportSymbolsScanner.scan() called")
        all_items = []
        
        try:
            print("DEBUG: Getting import instances...")
            # Get all import instances
            collector = FilteredElementCollector(self.doc)
            imports = list(collector.OfClass(ImportInstance).ToElements())
            
            print("DEBUG: Found {} imports".format(len(imports)))
            
            for imp in imports:
                try:
                    # Skip if null
                    if not imp or not imp.IsValidObject:
                        continue
                    
                    # Get import info
                    import_name = self._get_import_name(imp)
                    view_info = self._get_view_info(imp)
                    import_type = self._get_import_type(imp)
                    
                    # Create item - include ALL imports
                    item = self.create_item_dict(imp, {
                        'type': import_type,
                        'import_type': 'DWG/DXF Import',
                        'view_info': view_info
                    })
                    
                    # Override name with file name if available
                    if import_name:
                        item['name'] = import_name
                    
                    all_items.append(item)
                    print("DEBUG: Added import: {} ({})".format(import_name, view_info))
                
                except Exception as e:
                    print("DEBUG: Error processing import: {}".format(str(e)))
                    continue
            
            print("DEBUG: Scan complete. Returning {} items".format(len(all_items)))
        
        except Exception as e:
            print("Error scanning import symbols: {}".format(str(e)))
            import traceback
            try:                     # ScriptIO has no write() under CPython
                traceback.print_exc()
            except Exception:
                pass
        
        return all_items
    
    def _get_import_name(self, imp):
        """Get the file name of the import"""
        try:
            # Try to get from the CADLinkType/ImportInstance type
            type_id = imp.GetTypeId()
            if type_id and type_id != ElementId.InvalidElementId:
                import_type = self.doc.GetElement(type_id)
                if import_type:
                    # Try different parameter names for file path
                    param_names = ["RPC File Name", "External File Path", "Source"]
                    for param_name in param_names:
                        param = import_type.LookupParameter(param_name)
                        if param and param.HasValue:
                            path = param.AsString()
                            if path:
                                # Extract just the filename
                                import os
                                return os.path.basename(path)
                    
                    # Fallback to type name
                    if hasattr(import_type, 'Name') and import_type.Name:
                        return import_type.Name
            
            # Try getting from element name
            if hasattr(imp, 'Name') and imp.Name:
                return imp.Name
            
            # Last resort - use _eid_int for Revit 2024-2027 compatibility
            return "Import #{}".format(_eid_int(imp.Id))
            
        except Exception as ex:
            print("DEBUG: Error getting import name: {}".format(str(ex)))
            return "Import #{}".format(_eid_int(imp.Id))
    
    def _get_view_info(self, imp):
        """Get view placement info for the import"""
        try:
            owner_view_id = imp.OwnerViewId
            
            if owner_view_id and owner_view_id != ElementId.InvalidElementId:
                view = self.doc.GetElement(owner_view_id)
                if view:
                    view_name = view.Name if hasattr(view, 'Name') else str(_eid_int(owner_view_id))
                    return "In view: {}".format(view_name)
            
            # If no owner view, it's a 3D import (model space)
            return "3D (Model Space)"
            
        except Exception as ex:
            print("DEBUG: Error getting view info: {}".format(str(ex)))
            return "Unknown placement"
    
    def _get_import_type(self, imp):
        """Get the import type description"""
        try:
            # Check if it's linked or imported
            is_linked = False
            try:
                if hasattr(imp, 'IsLinked'):
                    is_linked = imp.IsLinked
            except:
                pass
            
            if is_linked:
                return "CAD Link"
            else:
                return "Import Instance"
        except:
            return "Import Instance"


class CADLinksScanner(BasePurgeScanner):
    """Scanner for unused CAD links"""
    
    def scan(self):
        """Scan for unused CAD link instances"""
        unused_items = []
        
        try:
            # Get all CAD link types
            collector = FilteredElementCollector(self.doc)
            cad_link_types = collector.OfClass(DB.CADLinkType).ToElements()
            
            for link_type in cad_link_types:
                try:
                    # Skip if null
                    if not link_type or not link_type.IsValidObject:
                        continue
                    
                    # Get all instances of this type
                    instances = FilteredElementCollector(self.doc)\
                        .OfClass(ImportInstance)\
                        .WhereElementIsNotElementType()\
                        .ToElements()
                    
                    # Count instances using this type
                    instance_count = 0
                    for inst in instances:
                        if inst.GetTypeId() == link_type.Id:
                            instance_count += 1
                    
                    # If no instances, type is unused
                    if instance_count == 0:
                        item = self.create_item_dict(link_type, {
                            'type': 'CAD Link Type',
                            'file_type': 'DWG/DXF/DGN'
                        })
                        unused_items.append(item)
                
                except Exception as e:
                    # Skip problematic links
                    continue
        
        except Exception as e:
            print("Error scanning CAD links: {}".format(str(e)))
        
        return unused_items


class UnusedGroupsScanner(BasePurgeScanner):
    """Scanner for unused groups"""
    
    def scan(self):
        """Scan for unused model and detail groups"""
        unused_items = []
        
        try:
            # Get all group types
            collector = FilteredElementCollector(self.doc)
            group_types = collector.OfClass(DB.GroupType).ToElements()
            
            for group_type in group_types:
                try:
                    # Skip if null
                    if not group_type or not group_type.IsValidObject:
                        continue
                    
                    # Get all group instances of this type
                    group_instances = FilteredElementCollector(self.doc)\
                        .OfClass(Group)\
                        .WhereElementIsNotElementType()\
                        .ToElements()
                    
                    # Count instances using this type
                    instance_count = 0
                    for inst in group_instances:
                        if inst.GetTypeId() == group_type.Id:
                            instance_count += 1
                    
                    # If no instances, type is unused
                    if instance_count == 0:
                        # Determine group category
                        group_category = "Model Group"
                        try:
                            if hasattr(group_type, 'Category') and group_type.Category:
                                cat_name = group_type.Category.Name
                                if "Detail" in cat_name:
                                    group_category = "Detail Group"
                        except:
                            pass
                        
                        item = self.create_item_dict(group_type, {
                            'type': group_category,
                            'instances': 0
                        })
                        unused_items.append(item)
                
                except Exception as e:
                    # Skip problematic groups
                    continue
        
        except Exception as e:
            print("Error scanning groups: {}".format(str(e)))
        
        return unused_items


class DesignOptionsScanner(BasePurgeScanner):
    """Scanner for unused design options"""
    
    def scan(self):
        """Scan for design options with no elements"""
        unused_items = []
        
        try:
            # Get all design options
            collector = FilteredElementCollector(self.doc)
            design_options = collector.OfClass(DesignOption).ToElements()
            
            for option in design_options:
                try:
                    # Skip if null
                    if not option or not option.IsValidObject:
                        continue
                    
                    # Skip primary option
                    if option.IsPrimary:
                        continue
                    
                    # Get elements in this design option
                    opt_filter = DB.ElementDesignOptionFilter(option.Id)
                    elements_in_option = FilteredElementCollector(self.doc)\
                        .WherePasses(opt_filter)\
                        .ToElements()
                    
                    # Count real elements (exclude design option itself)
                    element_count = 0
                    for elem in elements_in_option:
                        if elem.Id != option.Id:
                            element_count += 1
                    
                    # If no elements, option is unused
                    if element_count == 0:
                        # Get option set name
                        opt_set_name = "Unknown Set"
                        try:
                            opt_set = self.doc.GetElement(option.get_Parameter(
                                DB.BuiltInParameter.OPTION_SET_ID).AsElementId())
                            if opt_set:
                                opt_set_name = opt_set.Name
                        except:
                            pass
                        
                        item = self.create_item_dict(option, {
                            'type': 'Design Option',
                            'option_set': opt_set_name,
                            'elements': 0
                        })
                        unused_items.append(item)
                
                except Exception as e:
                    # Skip problematic options
                    continue
        
        except Exception as e:
            print("Error scanning design options: {}".format(str(e)))
        
        return unused_items


class UnplacedSeparatorsScanner(BasePurgeScanner):
    """Scanner for unplaced room and area separators"""
    
    def scan(self):
        """Scan for room/area separation lines that are not properly placed"""
        unused_items = []
        
        try:
            # Get all room separation lines
            room_seps = FilteredElementCollector(self.doc)\
                .OfCategory(BuiltInCategory.OST_RoomSeparationLines)\
                .WhereElementIsNotElementType()\
                .ToElements()
            
            for sep in room_seps:
                try:
                    # Skip if null
                    if not sep or not sep.IsValidObject:
                        continue
                    
                    # Check if separator is valid and placed
                    is_valid = True
                    
                    # Check if has geometry
                    try:
                        geom_elem = sep.get_Geometry(DB.Options())
                        if geom_elem is None or geom_elem.IsEmpty():
                            is_valid = False
                    except:
                        is_valid = False
                    
                    # Check if has location
                    if is_valid:
                        try:
                            location = sep.Location
                            if location is None:
                                is_valid = False
                        except:
                            is_valid = False
                    
                    # If not valid/placed, add to unused
                    if not is_valid:
                        item = self.create_item_dict(sep, {
                            'type': 'Room Separator',
                            'status': 'Unplaced'
                        })
                        unused_items.append(item)
                
                except Exception as e:
                    # Skip problematic separators
                    continue
            
            # Get all space separation lines (MEP)
            try:
                space_seps = FilteredElementCollector(self.doc)\
                    .OfCategory(BuiltInCategory.OST_MEPSpaceSeparationLines)\
                    .WhereElementIsNotElementType()\
                    .ToElements()
                
                for sep in space_seps:
                    try:
                        # Skip if null
                        if not sep or not sep.IsValidObject:
                            continue
                        
                        # Same checks as room separators
                        is_valid = True
                        
                        try:
                            geom_elem = sep.get_Geometry(DB.Options())
                            if geom_elem is None or geom_elem.IsEmpty():
                                is_valid = False
                        except:
                            is_valid = False
                        
                        if is_valid:
                            try:
                                location = sep.Location
                                if location is None:
                                    is_valid = False
                            except:
                                is_valid = False
                        
                        if not is_valid:
                            item = self.create_item_dict(sep, {
                                'type': 'Space Separator',
                                'status': 'Unplaced'
                            })
                            unused_items.append(item)
                    
                    except Exception as e:
                        continue
            
            except Exception as e:
                # MEP categories might not exist in non-MEP documents
                pass
        
        except Exception as e:
            print("Error scanning separators: {}".format(str(e)))
        
        return unused_items


class OrphanedRoomsScanner(BasePurgeScanner):
    """Scanner for orphaned rooms and areas without boundaries"""
    
    def scan(self):
        """Scan for rooms and areas that are not enclosed or placed"""
        unused_items = []
        
        try:
            # Scan rooms
            rooms = FilteredElementCollector(self.doc)\
                .OfCategory(BuiltInCategory.OST_Rooms)\
                .WhereElementIsNotElementType()\
                .ToElements()
            
            for room in rooms:
                try:
                    # Skip if null
                    if not room or not room.IsValidObject:
                        continue
                    
                    is_orphaned = False
                    reason = ""
                    
                    # Check if room is not placed
                    try:
                        location = room.Location
                        if location is None:
                            is_orphaned = True
                            reason = "Not placed"
                    except:
                        is_orphaned = True
                        reason = "Invalid location"
                    
                    # Check if room has no area (not enclosed)
                    if not is_orphaned:
                        try:
                            area = room.Area
                            if area is None or area <= 0:
                                is_orphaned = True
                                reason = "Not enclosed (no area)"
                        except:
                            is_orphaned = True
                            reason = "Cannot read area"
                    
                    # Check unbounded height
                    if not is_orphaned:
                        try:
                            height = room.UnboundedHeight
                            if height is None or height <= 0:
                                is_orphaned = True
                                reason = "Invalid height"
                        except:
                            pass
                    
                    # If orphaned, add to list
                    if is_orphaned:
                        item = self.create_item_dict(room, {
                            'type': 'Room',
                            'status': reason,
                            'number': room.Number if hasattr(room, 'Number') else 'N/A'
                        })
                        unused_items.append(item)
                
                except Exception as e:
                    # Skip problematic rooms
                    continue
            
            # Scan areas
            try:
                areas = FilteredElementCollector(self.doc)\
                    .OfCategory(BuiltInCategory.OST_Areas)\
                    .WhereElementIsNotElementType()\
                    .ToElements()
                
                for area in areas:
                    try:
                        # Skip if null
                        if not area or not area.IsValidObject:
                            continue
                        
                        is_orphaned = False
                        reason = ""
                        
                        # Check if area is not placed
                        try:
                            location = area.Location
                            if location is None:
                                is_orphaned = True
                                reason = "Not placed"
                        except:
                            is_orphaned = True
                            reason = "Invalid location"
                        
                        # Check if area has no area value (not enclosed)
                        if not is_orphaned:
                            try:
                                area_value = area.Area
                                if area_value is None or area_value <= 0:
                                    is_orphaned = True
                                    reason = "Not enclosed (no area)"
                            except:
                                is_orphaned = True
                                reason = "Cannot read area"
                        
                        # If orphaned, add to list
                        if is_orphaned:
                            item = self.create_item_dict(area, {
                                'type': 'Area',
                                'status': reason,
                                'number': area.Number if hasattr(area, 'Number') else 'N/A'
                            })
                            unused_items.append(item)
                    
                    except Exception as e:
                        continue
            
            except Exception as e:
                # Areas might not exist in all documents
                pass
        
        except Exception as e:
            print("Error scanning orphaned rooms/areas: {}".format(str(e)))
        
        return unused_items
