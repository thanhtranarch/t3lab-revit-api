#! python3
# -*- coding: utf-8 -*-
"""
Extension Tab Manager
Manage visibility of Extension/Add-in tabs in Revit
Copyright (c) 2025 by Dang Quoc Truong (DQT)
"""

__title__ = "Tab\nManager"
__author__ = "Dang Quoc Truong (DQT)"
__version__ = "1.0.0"
__copyright__ = "Copyright (c) 2025 by Dang Quoc Truong (DQT)"
tool_name = "Extension Tab Manager"

import os
import sys
# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
for _env in ('APPDATA', 'PROGRAMDATA'):
    _base = os.environ.get(_env, '')
    if _base:
        for _clone in ('pyRevit-Master', 'pyRevit'):
            _ceng = os.path.join(_base, _clone, 'bin', 'cengines', 'CPY3123')
            if os.path.isdir(_ceng):
                for _d in (_ceng, os.path.join(_ceng, 'Lib')):
                    if hasattr(os, 'add_dll_directory'):
                        try:
                            os.add_dll_directory(_d)
                        except Exception:
                            pass
                for _p in (_ceng, os.path.join(_ceng, 'Lib'), os.path.join(_ceng, 'python312.zip')):
                    if os.path.exists(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)

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
import codecs
from System import DateTime

from Snippets._host import get_revit_version

try:
    app = __revit__.Application
except Exception:
    app = None

date = DateTime.Now.ToString("yyMMdd")
revit_version = get_revit_version() or 2024
userName = getattr(app, "Username", None) or os.environ.get("USERNAME", "User")

def TempMemory(tool_name, bool):
    output = []

    # main dir
    _appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    memory_folder = os.path.join(_appdata, "T3Lab", "Cache")
    memory_clear_folder = os.path.join(memory_folder, userName)

    # temp folder
    memory_need_clear_folder = os.path.join(memory_clear_folder, "Temp")
    output.append(memory_need_clear_folder)  # output 0
    memory_file_folder = os.path.join(memory_need_clear_folder, tool_name, date)
    output.append(memory_file_folder)  # output 1
    try:
        os.makedirs(memory_file_folder)
    except:
        pass
    memory_file_name = userName + "_" + tool_name + ".txt"
    memory_file_path = os.path.join(memory_file_folder, memory_file_name)
    output.append(memory_file_path)  # output 2

    # not delete folder
    memory_not_clear_folder = os.path.join(memory_clear_folder, "Not Delete")
    output.append(memory_not_clear_folder)  # output 3
    memory_data_folder = os.path.join(memory_not_clear_folder, tool_name)
    output.append(memory_data_folder)  # output 4
    try:
        os.makedirs(memory_data_folder)
    except:
        pass
    if bool is True:
        memory_data_name = userName + "_" + tool_name + "_" + str(revit_version) + ".txt"
    else:
        memory_data_name = userName + "_" + tool_name + ".txt"
    memory_data_path = os.path.join(memory_data_folder, memory_data_name)
    output.append(memory_data_path)  # output 5
    return output

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

        memory_data_path = TempMemory(tool_name, True)[5]

        try:
            with codecs.open(memory_data_path, "w", encoding="utf-8") as textfile:
                textfile.write("# Extension Tab Manager\n")
                textfile.write("# Copyright (c) 2025 by Dang Quoc Truong (DQT)\n")
                textfile.write("# Hidden Tabs:\n")
                
                for tab in extensionTabLst:
                    if tab.Title in hideTabNameLst:
                        tab.IsVisible = False
                        textfile.write(tab.Title)
                        textfile.write("\n")
                    else:
                        tab.IsVisible = True
        except Exception as e:
            alert("Error: {}".format(str(e)), title="Error")

if __name__ == "__main__":
    main_task()