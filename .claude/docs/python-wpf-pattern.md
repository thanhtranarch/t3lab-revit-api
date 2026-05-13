# Python WPF Window Pattern

Every tool window class must follow this pattern.

## Path Constants (top of script, after imports)

```python
SCRIPT_DIR = os.path.dirname(__file__)

# Non-stacked pushbutton (tab/panel/pushbutton):
EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))

# Stacked pushbutton (tab/panel/stack/pushbutton):
EXT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR))))

XAML_FILE = os.path.join(EXT_DIR, 'lib', 'GUI', 'Tools', 'MyTool.xaml')
```

All XAML files live in `lib/GUI/Tools/`. Always build the path with `EXT_DIR` so the file
can be found regardless of which script triggers the window.

## Window Class

```python
import os
import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Windows import WindowState
from pyrevit import forms

class MyToolWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_FILE)
        # ... init logic ...

    # Required window chrome handlers
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
```

> **Note**: No logo loading — `_load_logo()` and logo image elements were removed from all tools.

## For Dialog Classes in lib/GUI/

When the window class lives inside `lib/GUI/` (e.g. `FamilyLoaderDialog.py`), the XAML is
one subfolder away — use `Tools/` directly:

```python
xaml_path = os.path.join(os.path.dirname(__file__), 'Tools', 'MyTool.xaml')
forms.WPFWindow.__init__(self, xaml_path)
```
