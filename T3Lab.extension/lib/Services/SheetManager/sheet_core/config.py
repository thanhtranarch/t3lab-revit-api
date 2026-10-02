# -*- coding: utf-8 -*-
"""
Sheet Manager - Configuration
Colors, constants, and settings

Copyright © T3Lab
"""


class Config(object):
    """Configuration constants"""
    
    # Colors (T3Lab Brand)
    PRIMARY_COLOR = "#0F172A"      # Golden
    BACKGROUND_COLOR = "#F8FAFC"   # Light cream
    
    # Window dimensions
    MAIN_WINDOW_WIDTH = 1400
    MAIN_WINDOW_HEIGHT = 800
    
    @staticmethod
    def get_color_argb(hex_color):
        """Convert hex color to ARGB tuple"""
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return (255, r, g, b)  # A, R, G, B
