#! python3
# -*- coding: utf-8 -*-
"""
Extension Tab Manager
Manage visibility of Extension/Add-in tabs in Revit
Copyright (c) 2025 by T3Lab
"""

__title__ = "Tab\nManager"
__author__ = "T3Lab"
__version__ = "1.0.0"
__copyright__ = "Copyright (c) 2025 by T3Lab"
tool_name = "Extension Tab Manager"

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

# System library
from pyrevit.forms import alert
from pyrevit.api import AdWindows


try:
    app = __revit__.Application
except Exception:
    app = None

class MyOption(object):
    def __init__(self, item, state=False):
        self.item = item
        self.state = state

    @property
    def name(self):
        return self.item

def CheckBoxForListItem(nameLst, activeLst):
    currentLst = []
    for n in nameLst:
        item = MyOption(n, n in activeLst)
        currentLst.append(item)
    return currentLst

# Starting
def main_task():
    # Danh sach cac tab REVIT BUILT-IN - se bo qua khong hien thi
    ignoreTabNameLst = []
    ignoreTabNameLst1 = ["Architecture", "Structure", "Steel", "Precast", "Systems", "Insert", "Annotate", "Analyze",
                         "Massing & Site", "Collaborate", "View", "Manage", "Add-Ins", "Modify"]
    ignoreTabNameLst2 = ["Arch", "Struc", "MEP", "Anno", "Mass&Site", "Collab", "Fam.Editor"]
    ignoreTabNameLst3 = ["Create", "In-Place Model", "In-Place Mass", "Zone", "Family Editor"]
    
    # Them cac tab cua ban vao day neu muon BO QUA (khong quan ly)
    ignoreTabNameLst4 = ["pyRevit", "MEOS"]
    
    ignoreTabNameLst.extend(ignoreTabNameLst1)
    ignoreTabNameLst.extend(ignoreTabNameLst2)
    ignoreTabNameLst.extend(ignoreTabNameLst3)
    ignoreTabNameLst.extend(ignoreTabNameLst4)

    # Lay tat ca cac tab EXTENSION/ADD-IN (khong phai built-in cua Revit)
    extensionTabLst = []
    extensionTabNameLst = []
    visibleTabNameLst = []
    
    for tab in AdWindows.ComponentManager.Ribbon.Tabs:
        if tab.Title not in ignoreTabNameLst:
            extensionTabLst.append(tab)
            extensionTabNameLst.append(tab.Title)
            if tab.IsVisible:
                visibleTabNameLst.append(tab.Title)

    if len(extensionTabNameLst) == 0:
        alert("No Extension/Add-in tabs found!", title="Extension Tab Manager")
        return

    currentLst = CheckBoxForListItem(extensionTabNameLst, visibleTabNameLst)
    
    # Import custom Tab Manager Dialog from lib/GUI (with dynamic reload for live updates)
    import importlib
    try:
        import GUI.ManaTabsDialog
        importlib.reload(GUI.ManaTabsDialog)
    except Exception:
        pass
    from GUI.ManaTabsDialog import show_tab_manager_dialog
    
    selectedTabNameLst = show_tab_manager_dialog(currentLst)

    if selectedTabNameLst is not None:
        hideTabNameLst = []
        for i in extensionTabNameLst:
            if i not in selectedTabNameLst:
                hideTabNameLst.append(i)

        # Hide / show straight away. The old code wrote a hidden-tab list (and a
        # dated folder per day) under %LOCALAPPDATA%\T3Lab\Cache that nothing
        # ever read back, and the hiding ran inside that file write — a failed
        # write left every tab as it was.
        try:
            for tab in extensionTabLst:
                tab.IsVisible = tab.Title not in hideTabNameLst
        except Exception as e:
            alert("Error: {}".format(str(e)), title="Error")

if __name__ == "__main__":
    main_task()