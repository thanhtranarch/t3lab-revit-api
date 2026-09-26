# CLAUDE.md — T3Lab pyRevit Extension

pyRevit extension for Revit automation.
Framework: CPython 3 (Python 3.12+ via pyRevit) + WPF + Revit API (.NET Framework 4.8 / .NET Core 8 for Revit 2024–2026).

## Essential Commands & Verification Gates

```powershell
# Set clean PYTHONPATH before executing dev tools in shell:
$env:PYTHONPATH=""

# UI Design System Gate (must be 0 violations):
python dev/audit_t3.py --quiet

# Static Code & XAML Gate:
python dev/audit_tools.py --quiet

# Wiring Gate (XAML <-> Python: missing controls, unwired template events, dead buttons/handlers):
python dev/audit_wiring.py --quiet

# Run test suites:
python dev/test_batch_link.py
python dev/test_group_manager.py
python dev/test_grid_pending_edits.py
```

## Core Architecture & Rules

1. **UI standard — one source, no exceptions:**
   - `pyRevit UI Design System/T3LAB_UI_STANDARD.md` + `pyRevit UI Design System/T3Lab.Styles.xaml`.
   - Every colour, size, margin and control style comes from `{StaticResource T3.*}`.
   - All XAML files live in `T3Lab.extension/lib/GUI/Tools/`.
   - Python dialog classes stay in `T3Lab.extension/lib/GUI/`.

2. **Revit Journal First:**
   - When encountering a crash or "Command Failure for External Command", always check the latest Revit journal file (`%LOCALAPPDATA%\Autodesk\Revit\Autodesk Revit <Year>\Journals\journal.XXXX.txt`) to extract the exact stack trace before attempting fixes.

3. **CPython 3 Shebang & Bootstrap:**
   - Every pushbutton `script.py` must have `#! python3` as the first line.
   - Bootstrap Python 3 standard library: `import _cpython_bootstrap; _cpython_bootstrap.init_cpython_paths()`.

4. **WPF & XAML Loading Under PythonNet (CPython 3):**
   - Standard pyRevit `forms.WPFWindow` raises `PyRevitCPythonNotSupported`. Always use `T3WPFWindow` (from `GUI.WPF_Base` or `from GUI.forms import WPFWindow`).
   - XAML hydration must use `_load_via_xaml_reader` with 4-tier fallback:
     1. Direct `XamlReader.Parse(clean_xaml)`
     2. `MemoryStream` -> `XamlReader.Load(Stream)`
     3. CLR AppDomain Reflection on `PresentationFramework` `XamlReader` (`Parse` / `Load`)
     4. `XmlReader.Create` + `XamlReader.Load(XmlReader)`
   - Under .NET 8 / Revit 2025/2026, include references to `System.Xml.ReaderWriter` and `System.Private.Xml`.

5. **.NET Interface Namespaces:**
   - Classes implementing .NET interfaces (`ISelectionFilter`, `IExternalEventHandler`, `IFailuresPreprocessor`) must declare `__namespace__`.
   - In `lib/`: static namespace `__namespace__ = "T3Lab.<UniqueName>"`.
   - In pushbutton `script.py`: dynamic namespace `__namespace__ = "T3Lab.<Name>_" + uuid.uuid4().hex[:8]`.

6. **No Multiple Inheritance with CLR Classes:**
   - PythonNet classes inheriting from `T3WPFWindow` / `System.Windows.Window` must not inherit from any other Python class/mixin.

7. **Engine Hot-Reload & Modeless ExternalEvent:**
   - When switching engines or modifying runtime assemblies while Revit is open, reload pyRevit (`pyRevit` -> `Reload`) to prevent `set_PythonDLL: This property must be set before runtime is initialized`.
   - For modeless windows, set `__persistentengine__ = True` and use `detect_persistent_engine()` to gracefully fall back to modal execution if the persistent engine has not yet been activated by a reload.
