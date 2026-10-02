# -*- coding: utf-8 -*-
"""
Smart Purge v2.0 - Configuration
Color scheme and settings

Compatible with Revit 2024, 2025, 2026, 2027

Copyright (c) 2025 T3Lab
"""

__author__ = "T3Lab"


class Colors:
    """T3Lab Brand Color Scheme"""
    
    # Primary colors (T3Lab Branding)
    HEADER = "#0F172A"           # Gold header background
    BACKGROUND = "#F8FAFC"       # Light cream background
    BORDER = "#CBD5E1"           # Gold border
    ACCENT = "#5D4E37"           # Dark brown accent
    
    # Text colors
    TEXT_PRIMARY = "#0F172A"     # Dark text
    TEXT_SECONDARY = "#475569"   # Secondary text
    TEXT_LIGHT = "#94A3B8"       # Light/muted text
    
    # Status colors
    HIGHLIGHT = "#FF6B35"        # Orange highlight for counts
    SUCCESS = "#10B981"          # Green for safe items
    WARNING = "#FFC107"          # Yellow/amber warning
    DANGER = "#EF4444"           # Red danger
    
    # Button colors
    BUTTON_PRIMARY = "#FF6B35"   # Orange primary button
    BUTTON_SUCCESS = "#10B981"   # Green success button
    BUTTON_DANGER = "#EF4444"    # Red danger button
    BUTTON_NEUTRAL = "#9E9E9E"   # Gray neutral button
    
    # Common colors
    White = "#FFFFFF"
    Black = "#000000"
    
    # Alias for backward compatibility
    PRIMARY = HEADER
    SECONDARY = BACKGROUND


class Fonts:
    """Font sizes for UI"""
    
    TITLE = 22
    SUBTITLE = 14
    HEADER = 14
    BODY = 13
    SMALL = 11
    TINY = 10


class Settings:
    """Application settings"""
    
    # Window dimensions
    WINDOW_WIDTH = 850
    WINDOW_HEIGHT = 950
    
    # Default settings
    DEFAULT_DRY_RUN = True
    DEFAULT_DELETE_SYSTEM = False
    
    # Application info
    APP_NAME = "Smart Purge"
    APP_VERSION = "2.0.1"
    AUTHOR = "T3Lab"
    COPYRIGHT = "Copyright (c) 2025 T3Lab"
