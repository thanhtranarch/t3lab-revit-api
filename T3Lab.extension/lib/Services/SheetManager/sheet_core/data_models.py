# -*- coding: utf-8 -*-
"""
Sheet Manager - Data Models
FIXED - Use direct Revit properties

Copyright © Dang Quoc Truong (DQT)
"""


class SheetModel(object):
    """Sheet model with change tracking"""
    
    def __init__(self, sheet_element):
        # Revit element
        self.element = sheet_element
        self.id = sheet_element.Id
        
        # Original values - Use direct properties
        self._original_sheet_number = sheet_element.SheetNumber if sheet_element.SheetNumber else "-"
        self._original_sheet_name = sheet_element.Name if sheet_element.Name else "-"
        
        # Current values (editable)
        self.sheet_number = self._original_sheet_number
        self.sheet_name = self._original_sheet_name
        
        # Additional properties - Try parameters
        self.designed_by = self._get_param_value(sheet_element, "Designed By")
        self.checked_by = self._get_param_value(sheet_element, "Checked By")
        self.drawn_by = self._get_param_value(sheet_element, "Drawn By")
        self.approved_by = self._get_param_value(sheet_element, "Approved By")
        
        # UI state
        self.is_selected = False
        self.is_modified = False
        self.status = u"✓"
    
    def _get_param_value(self, element, param_name):
        """Get parameter value by name"""
        try:
            param = element.LookupParameter(param_name)
            if param and param.HasValue:
                if param.StorageType.ToString() == "String":
                    value = param.AsString()
                    return value if value else "-"
                else:
                    value = param.AsValueString()
                    return value if value else "-"
            return "-"
        except:
            return "-"
    
    def check_if_modified(self):
        """Check if sheet has been modified"""
        if (self.sheet_number != self._original_sheet_number or
            self.sheet_name != self._original_sheet_name):
            self.is_modified = True
            self.status = u"●"
        else:
            self.is_modified = False
            self.status = u"✓"
        return self.is_modified
    
    def commit_changes(self):
        """Mark changes as committed"""
        self._original_sheet_number = self.sheet_number
        self._original_sheet_name = self.sheet_name
        self.is_modified = False
        self.status = u"✓"


class ChangeTracker(object):
    """Track changes to items"""
    
    def __init__(self):
        self.modified_items = []
        self.created_items = []
        self.deleted_items = []
    
    def track_modification(self, item):
        """Track modified item"""
        if item not in self.modified_items and item not in self.created_items:
            self.modified_items.append(item)
    
    
    def has_changes(self):
        """Check if there are any changes"""
        return len(self.modified_items) > 0 or len(self.created_items) > 0 or len(self.deleted_items) > 0
    
    def clear_all(self):
        """Clear all tracked changes"""
        self.modified_items = []
        self.created_items = []
        self.deleted_items = []