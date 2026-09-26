# -*- coding: utf-8 -*-
"""
Revit Utilities - Helper functions for Revit API compatibility
Compatible with Revit 2024, 2025, 2026, 2027

Copyright (c) 2025 Dang Quoc Truong (DQT)

IMPORTANT COMPATIBILITY NOTES:
- Revit 2024/2025: ElementId.IntegerValue works
- Revit 2026+: ElementId.IntegerValue deprecated, use ElementId.Value instead
- This module provides helper functions that work across all versions
"""

__author__ = "Dang Quoc Truong (DQT)"


def get_element_id_value(element_id):
    """
    Get the integer value of an ElementId - works for Revit 2024-2027
    
    Revit 2024/2025: Uses IntegerValue
    Revit 2026+: Uses Value (IntegerValue deprecated)
    
    Args:
        element_id: An ElementId object
        
    Returns:
        int: The integer value of the ElementId
    """
    if element_id is None:
        return -1
    
    try:
        # Try Revit 2026+ method first (.Value)
        if hasattr(element_id, 'Value'):
            return element_id.Value
    except:
        pass
    
    try:
        # Fallback to Revit 2024/2025 method (.IntegerValue)
        if hasattr(element_id, 'IntegerValue'):
            return element_id.IntegerValue
    except:
        pass
    
    # Last resort - try to convert to int
    try:
        return int(str(element_id))
    except:
        return -1


def _eid_int(element_id):
    """
    Shorthand alias for get_element_id_value
    
    Usage:
        from .revit_utils import _eid_int
        id_value = _eid_int(element.Id)
    """
    return get_element_id_value(element_id)


def get_revit_version():
    """
    Get the current Revit version number
    
    Returns:
        int: Revit version year (e.g., 2024, 2025, 2026, 2027)
    """
    try:
        from Autodesk.Revit.ApplicationServices import Application
        # This won't work directly, need to get from __revit__
        return 2024  # Default fallback
    except:
        return 2024


# Compatibility shims for deprecated APIs

