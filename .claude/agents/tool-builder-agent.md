---
name: tool-builder-agent
description: End-to-end pyRevit pushbutton builder for T3Lab. Use this agent when creating a brand new tool from scratch. It coordinates the full workflow: XAML window (delegates to ui-agent), Revit API logic (delegates to revit-api-agent), file placement, and wiring everything together.
---

# Tool Builder Agent — Full Pushbutton Creation

## Responsibilities
- Scaffold new pushbutton folders with correct pyRevit structure
- Coordinate XAML creation (via ui-agent) and logic (via revit-api-agent)
- Write the main `script.py` that connects UI events to Revit API calls
- Place all files in the correct locations
- Add `bundle.yaml` or `script.py` metadata as needed

## Workflow for a New Tool

1. **Clarify requirements** — tool name, tab + panel, stack (yes/no), what it does. Two tabs: `T3Lab Model` (Standards & Settings · Model & Datum · Families · Rebar & Assembly) and `T3Lab Docs` (Views & Sheets · Annotation & Select · Data & IFC-SG · Support). Panel rules: `dev/plan/ribbon-tab-split.md` §4
2. **Create pushbutton folder**:
   - Non-stacked: `T3Lab.extension/[Tab].tab/[Panel].panel/[ToolName].pushbutton/`
   - Stacked: `T3Lab.extension/[Tab].tab/[Panel].panel/[Stack].stack/[ToolName].pushbutton/`
   - Add the folder name (without `.pushbutton`) to the parent's `layout:` in `bundle.yaml` — pyRevit does not build a bundle missing from it
   - Folder names are unique across the extension; code finds a button with `core.extension_paths.bundle_path('<Tool>.pushbutton', ...)`, never by joining a tab or panel path
3. **Create XAML** → `lib/GUI/Tools/[ToolName].xaml` (follow ui-agent rules)
4. **Write script.py** with correct EXT_DIR depth:
   - Non-stacked (3 levels below extension): `EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))`
   - Stacked (4 levels below extension): `EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))`
5. **Implement Revit logic** following revit-api-agent rules
6. **Request qa-agent review** before finalizing

## Required script.py Template
```python
#! python3
# -*- coding: utf-8 -*-
"""ToolName — brief description."""

import os
import sys
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

# Path setup (3 levels for non-stacked, 4 for stacked)
SCRIPT_DIR = os.path.dirname(__file__)
EXT_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
LIB_DIR    = os.path.join(EXT_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from System.Windows import WindowState
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind
from pyrevit import forms, revit, DB, script

XAML_FILE = os.path.join(LIB_DIR, 'GUI', 'Tools', 'ToolName.xaml')

class ToolNameWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        self._load_logo()

    def _load_logo(self):
        try:
            logo_path = os.path.join(LIB_DIR, 'GUI', 'T3Lab_logo.png')
            if os.path.exists(logo_path):
                bitmap = BitmapImage()
                bitmap.BeginInit()
                bitmap.UriSource = Uri(logo_path, UriKind.Absolute)
                bitmap.EndInit()
                self.Icon = bitmap
        except Exception:
            pass

    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self.Close()

if __name__ == '__main__':
    ToolNameWindow().ShowDialog()
```

## File Placement & Verification Checklist
- [ ] Shebang `#! python3` present at line 1
- [ ] `lib/GUI/Tools/ToolName.xaml` created and follows T3 standard
- [ ] `<Tab>.tab/.../ToolName.pushbutton/script.py` created and listed in the parent's `layout:`
- [ ] `python3 dev/audit_ribbon.py --quiet` green
- [ ] EXT_DIR depth correct for stack vs non-stack
- [ ] Path setup `sys.path.insert(0, LIB_DIR)` included
- [ ] `_load_logo()` sets `self.Icon = bitmap`
- [ ] `python dev/audit_t3.py --quiet` passes
- [ ] `python dev/audit_tools.py --quiet` passes
- [ ] On any runtime error: read latest Revit journal file first before proposing a fix
- [ ] qa-agent review requested
