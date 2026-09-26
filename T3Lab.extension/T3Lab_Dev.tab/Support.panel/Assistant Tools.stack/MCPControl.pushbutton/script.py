#! python3
# -*- coding: utf-8 -*-
"""MCP Control

Unified control panel for the T3Lab MCP server.
Start / Stop the server and manage connection settings in one dialog.
"""
__title__ = "MCP\nControl"
__author__ = "T3Lab & Dang Quoc Truong"

import os
import sys
# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
# CPython engine paths come from lib/_cpython_bootstrap.py below - it finds
# the engine whatever the pyRevit clone is named or wherever it is installed.

_cur = os.path.dirname(os.path.abspath(__file__))
while _cur and not os.path.exists(os.path.join(_cur, 'lib')):
    _parent = os.path.dirname(_cur)
    if _parent == _cur:
        break
    _cur = _parent
_lib_dir = os.path.join(_cur, 'lib')
if os.path.exists(_lib_dir) and _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────

# Path setup — script.py lives 4 levels below T3Lab.extension/ (inside a .stack bundle)
SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))
LIB_DIR = os.path.join(EXT_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

try:
    import clr
    for _ref in ('System', 'WindowsBase', 'PresentationCore', 'PresentationFramework'):
        try:
            clr.AddReference(_ref)
        except Exception:
            pass
except Exception:
    clr = None


def main():
    try:
        from GUI.MCPControlDialog import show_mcp_control_dialog
        show_mcp_control_dialog()
    except Exception as ex:
        import traceback
        tb = traceback.format_exc()
        try:
            log_dir = os.path.join(os.path.expanduser('~'), 'T3Lab_AI_Data')
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            log_file = os.path.join(log_dir, 'mcp_control_error.log')
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(tb)
        except Exception:
            pass

        try:
            from pyrevit import forms
            forms.alert(
                "MCP Control error:\n\n{}\n\nTraceback saved to ~/T3Lab_AI_Data/mcp_control_error.log".format(ex),
                title="MCP Control",
                warn_icon=True
            )
        except Exception:
            try:
                from System.Windows import MessageBox
                MessageBox.Show(str(ex), "MCP Control Error")
            except Exception:
                pass


if __name__ == '__main__':
    main()
