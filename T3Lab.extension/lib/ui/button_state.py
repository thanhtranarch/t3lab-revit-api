# -*- coding: utf-8 -*-
"""
Button State

Manages the visual state of T3Lab toolbar buttons.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Button State"

import os

# Global state for tracking connection status
_connection_state = {
    'connected': False,
    'error': False,
    'last_error': None
}


    # Note: In a full implementation, this would use pyrevit's
    # script.toggle_icon() or similar methods to update button visuals
    # The actual icon switching happens based on the server state
    # when the ribbon is refreshed


def get_last_error():
    """Get the last error message"""
    return _connection_state.get('last_error')


def has_error():
    """Check if there is an error"""
    return _connection_state.get('error', False)
