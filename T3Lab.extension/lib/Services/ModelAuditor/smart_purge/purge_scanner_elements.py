# -*- coding: utf-8 -*-
"""
Element Type Scanners (Phase 2)
Scanners for Wall, Floor, and Roof types
Copyright (c) 2025 T3Lab
"""

__author__ = "T3Lab"

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    WallType, Wall, WallKind,
    FloorType, Floor,
    RoofType, RoofBase,
    ElementId,
    BuiltInParameter
)
from Autodesk.Revit import DB

try:
    from .purge_scanner import BasePurgeScanner
except:
    # Fallback for testing
    class BasePurgeScanner:
        def __init__(self):
            pass

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

from .purge_categories_v2 import PurgeCategoryItem


# Default type names to protect
DEFAULT_WALL_TYPES = [
    "Basic Wall",
    "Generic - 200mm",
    "Generic - 300mm",
    "Generic",
    "Exterior - Brick on CMU",
    "Exterior - Brick on Mtl. Stud",
    "Interior - Partition"
]

DEFAULT_FLOOR_TYPES = [
    "Generic - 200mm",
    "Generic - 300mm", 
    "Generic - 125mm",
    "Generic",
    "Floor"
]

DEFAULT_ROOF_TYPES = [
    "Generic - 300mm",
    "Generic - 400mm",
    "Generic",
    "Basic Roof"
]


class WallTypeScanner(BasePurgeScanner):
    """Scanner for unused wall types"""
    
    def __init__(self, doc, protect_defaults=True):
        """
        Initialize wall type scanner
        
        Args:
            doc: Revit document
            protect_defaults: Whether to protect default wall types
        """
        super(WallTypeScanner, self).__init__(doc)  # ← PASS doc!
        self.protect_defaults = protect_defaults
    
    def scan(self):
        """
        Scan for unused wall types
        
        Returns:
            List of PurgeCategoryItem for unused wall types
        """
        unused_items = []
        
        try:
            # Step 1: Collect all wall types
            wall_types = FilteredElementCollector(self.doc)\
                .OfClass(WallType)\
                .ToElements()
            
            # Step 2: Build usage dictionary from wall instances
            usage_dict = {}
            walls = FilteredElementCollector(self.doc)\
                .OfClass(Wall)\
                .ToElements()
            
            for wall in walls:
                try:
                    # Skip in-place families
                    if hasattr(wall, 'Symbol') and wall.Symbol:
                        symbol = wall.Symbol
                        if hasattr(symbol, 'Family') and symbol.Family:
                            family = symbol.Family
                            if hasattr(family, 'IsInPlace') and family.IsInPlace:
                                continue
                    
                    wall_type_id = wall.GetTypeId()
                    if wall_type_id and wall_type_id != ElementId.InvalidElementId:
                        usage_dict[_eid_int(wall_type_id)] = \
                            usage_dict.get(_eid_int(wall_type_id), 0) + 1
                except:
                    continue
            
            # Step 3: Find unused wall types
            for wall_type in wall_types:
                try:
                    # Skip system/built-in types
                    if self._is_system_type(wall_type):
                        continue
                    
                    # Get type ID
                    type_id = _eid_int(wall_type.Id)
                    
                    # Check if used
                    if type_id in usage_dict:
                        continue
                    
                    # Check if should protect defaults
                    if self.protect_defaults and self._is_default_type(wall_type):
                        continue
                    
                    # Get wall type kind
                    wall_kind = self._get_wall_kind(wall_type)
                    
                    # Create item dictionary using base class method
                    item = self.create_item_dict(wall_type, {
                        'type': wall_kind
                    })
                    
                    unused_items.append(item)
                    
                except Exception as e:
                    # Skip problematic types
                    continue
            
        except Exception as e:
            print("Error scanning wall types: {}".format(str(e)))
        
        return unused_items
    
    def _is_system_type(self, wall_type):
        """Check if wall type is system/built-in"""
        try:
            # Check if read-only
            if hasattr(wall_type, 'IsReadOnly') and wall_type.IsReadOnly:
                return True
            
            # Check ID range (system types typically have ID < 100)
            if _eid_int(wall_type.Id) < 100:
                return True
            
            # Check name pattern
            name = wall_type.Name if hasattr(wall_type, 'Name') else ""
            if name.startswith('<') and name.endswith('>'):
                return True
            
            return False
            
        except:
            return False
    
    def _is_default_type(self, wall_type):
        """Check if wall type is a default type to protect"""
        try:
            name = wall_type.Name if hasattr(wall_type, 'Name') else ""
            
            # Check against default names
            for default_name in DEFAULT_WALL_TYPES:
                if default_name.lower() in name.lower():
                    return True
            
            # Check if it's a basic generic type
            if wall_type.Kind == WallKind.Basic:
                if "generic" in name.lower() or "basic" in name.lower():
                    return True
            
            return False
            
        except:
            return False
    
    def _get_wall_kind(self, wall_type):
        """Get wall kind as string"""
        try:
            kind = wall_type.Kind
            
            if kind == WallKind.Basic:
                return "Basic Wall"
            elif kind == WallKind.Curtain:
                return "Curtain Wall"
            elif kind == WallKind.Stacked:
                return "Stacked Wall"
            else:
                return "Unknown"
                
        except:
            return "Unknown"


class FloorTypeScanner(BasePurgeScanner):
    """Scanner for unused floor types"""
    
    def __init__(self, doc, protect_defaults=True):
        """
        Initialize floor type scanner
        
        Args:
            doc: Revit document
            protect_defaults: Whether to protect default floor types
        """
        super(FloorTypeScanner, self).__init__(doc)  # ← PASS doc!
        self.protect_defaults = protect_defaults
    
    def scan(self):
        """
        Scan for unused floor types
        
        Returns:
            List of PurgeCategoryItem for unused floor types
        """
        unused_items = []
        
        try:
            # Step 1: Collect all floor types
            floor_types = FilteredElementCollector(self.doc)\
                .OfClass(FloorType)\
                .ToElements()
            
            # Step 2: Build usage dictionary from floor instances
            usage_dict = {}
            floors = FilteredElementCollector(self.doc)\
                .OfClass(Floor)\
                .ToElements()
            
            for floor in floors:
                try:
                    # Skip in-place families
                    if hasattr(floor, 'Symbol') and floor.Symbol:
                        symbol = floor.Symbol
                        if hasattr(symbol, 'Family') and symbol.Family:
                            family = symbol.Family
                            if hasattr(family, 'IsInPlace') and family.IsInPlace:
                                continue
                    
                    floor_type_id = floor.GetTypeId()
                    if floor_type_id and floor_type_id != ElementId.InvalidElementId:
                        usage_dict[_eid_int(floor_type_id)] = \
                            usage_dict.get(_eid_int(floor_type_id), 0) + 1
                except:
                    continue
            
            # Step 3: Find unused floor types
            for floor_type in floor_types:
                try:
                    # Skip system/built-in types
                    if self._is_system_type(floor_type):
                        continue
                    
                    # Get type ID
                    type_id = _eid_int(floor_type.Id)
                    
                    # Check if used
                    if type_id in usage_dict:
                        continue
                    
                    # Check if should protect defaults
                    if self.protect_defaults and self._is_default_type(floor_type):
                        continue
                    
                    # Get thickness info
                    thickness = self._get_thickness(floor_type)
                    
                    # Create item dictionary using base class method
                    item = self.create_item_dict(floor_type, {
                        'type': 'Floor Type',
                        'thickness': thickness
                    })
                    
                    unused_items.append(item)
                    
                except Exception as e:
                    # Skip problematic types
                    continue
            
        except Exception as e:
            print("Error scanning floor types: {}".format(str(e)))
        
        return unused_items
    
    def _is_system_type(self, floor_type):
        """Check if floor type is system/built-in"""
        try:
            # Check if read-only
            if hasattr(floor_type, 'IsReadOnly') and floor_type.IsReadOnly:
                return True
            
            # Check ID range
            if _eid_int(floor_type.Id) < 100:
                return True
            
            # Check name pattern
            name = floor_type.Name if hasattr(floor_type, 'Name') else ""
            if name.startswith('<') and name.endswith('>'):
                return True
            
            return False
            
        except:
            return False
    
    def _is_default_type(self, floor_type):
        """Check if floor type is a default type to protect"""
        try:
            name = floor_type.Name if hasattr(floor_type, 'Name') else ""
            
            # Check against default names
            for default_name in DEFAULT_FLOOR_TYPES:
                if default_name.lower() in name.lower():
                    return True
            
            return False
            
        except:
            return False
    
    def _get_thickness(self, floor_type):
        """Get floor thickness as string"""
        try:
            # Try to get thickness parameter
            thickness_param = floor_type.get_Parameter(
                DB.BuiltInParameter.FLOOR_ATTR_THICKNESS_PARAM
            )
            
            if thickness_param and thickness_param.HasValue:
                # Convert from feet to mm
                thickness_feet = thickness_param.AsDouble()
                thickness_mm = thickness_feet * 304.8
                return "Thickness: {:.0f}mm".format(thickness_mm)
            
            return "Floor Type"
            
        except:
            return "Floor Type"


class RoofTypeScanner(BasePurgeScanner):
    """Scanner for unused roof types"""
    
    def __init__(self, doc, protect_defaults=True):
        """
        Initialize roof type scanner
        
        Args:
            doc: Revit document
            protect_defaults: Whether to protect default roof types
        """
        super(RoofTypeScanner, self).__init__(doc)  # ← PASS doc!
        self.protect_defaults = protect_defaults
    
    def scan(self):
        """
        Scan for unused roof types
        
        Returns:
            List of PurgeCategoryItem for unused roof types
        """
        unused_items = []
        
        try:
            # Step 1: Collect all roof types
            roof_types = FilteredElementCollector(self.doc)\
                .OfClass(RoofType)\
                .ToElements()
            
            # Step 2: Build usage dictionary from roof instances
            usage_dict = {}
            roofs = FilteredElementCollector(self.doc)\
                .OfClass(RoofBase)\
                .ToElements()
            
            for roof in roofs:
                try:
                    # Skip in-place families
                    if hasattr(roof, 'Symbol') and roof.Symbol:
                        symbol = roof.Symbol
                        if hasattr(symbol, 'Family') and symbol.Family:
                            family = symbol.Family
                            if hasattr(family, 'IsInPlace') and family.IsInPlace:
                                continue
                    
                    roof_type_id = roof.GetTypeId()
                    if roof_type_id and roof_type_id != ElementId.InvalidElementId:
                        usage_dict[_eid_int(roof_type_id)] = \
                            usage_dict.get(_eid_int(roof_type_id), 0) + 1
                except:
                    continue
            
            # Step 3: Find unused roof types
            for roof_type in roof_types:
                try:
                    # Skip system/built-in types
                    if self._is_system_type(roof_type):
                        continue
                    
                    # Get type ID
                    type_id = _eid_int(roof_type.Id)
                    
                    # Check if used
                    if type_id in usage_dict:
                        continue
                    
                    # Check if should protect defaults
                    if self.protect_defaults and self._is_default_type(roof_type):
                        continue
                    
                    # Get thickness info
                    thickness = self._get_thickness(roof_type)
                    
                    # Create item dictionary using base class method
                    item = self.create_item_dict(roof_type, {
                        'type': 'Roof Type',
                        'thickness': thickness
                    })
                    
                    unused_items.append(item)
                    
                except Exception as e:
                    # Skip problematic types
                    continue
            
        except Exception as e:
            print("Error scanning roof types: {}".format(str(e)))
        
        return unused_items
    
    def _is_system_type(self, roof_type):
        """Check if roof type is system/built-in"""
        try:
            # Check if read-only
            if hasattr(roof_type, 'IsReadOnly') and roof_type.IsReadOnly:
                return True
            
            # Check ID range
            if _eid_int(roof_type.Id) < 100:
                return True
            
            # Check name pattern
            name = roof_type.Name if hasattr(roof_type, 'Name') else ""
            if name.startswith('<') and name.endswith('>'):
                return True
            
            return False
            
        except:
            return False
    
    def _is_default_type(self, roof_type):
        """Check if roof type is a default type to protect"""
        try:
            name = roof_type.Name if hasattr(roof_type, 'Name') else ""
            
            # Check against default names
            for default_name in DEFAULT_ROOF_TYPES:
                if default_name.lower() in name.lower():
                    return True
            
            return False
            
        except:
            return False
    
    def _get_thickness(self, roof_type):
        """Get roof thickness as string"""
        try:
            # Try to get thickness parameter
            thickness_param = roof_type.get_Parameter(
                DB.BuiltInParameter.ROOF_ATTR_THICKNESS_PARAM
            )
            
            if thickness_param and thickness_param.HasValue:
                # Convert from feet to mm
                thickness_feet = thickness_param.AsDouble()
                thickness_mm = thickness_feet * 304.8
                return "Thickness: {:.0f}mm".format(thickness_mm)
            
            return "Roof Type"
            
        except:
            return "Roof Type"