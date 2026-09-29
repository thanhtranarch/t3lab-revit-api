# CLAUDE.md — T3Lab pyRevit Extension

pyRevit extension for Revit automation.
Framework: CPython 3 (Python 3.12+ via pyRevit) + WPF + Revit API (.NET Framework 4.8 for Revit 2022–2024 / .NET 8+ for Revit 2025–2027).
Supported range: **Revit 2022 → 2027** — guarded by `dev/audit_revit_compat.py`.

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

# Revit API Compatibility Gate (Revit 2022–2027: no removed/new-only API without a fallback):
python dev/audit_revit_compat.py --quiet

# Run test suites:
python dev/test_batch_link.py
python dev/test_group_manager.py
python dev/test_grid_pending_edits.py
python dev/test_family_transfer.py
python dev/test_pyrevit_patches.py
python dev/test_compat_disposing.py
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
   - **On Revit 2025+ a pyRevit Reload is what CAUSES `This property must be set before runtime is initialized`** (verified 2026-09-29 from the journal + pyRevit 6.5.5 IL): Reload (also toggling an extension) shuts the CPython engine down, pythonnet 3.0.3 cannot restart after Shutdown on .NET 8+, and `CPythonEngine.Start` then sets `Runtime.PythonDLL` on a half-initialized runtime. Only a Revit restart recovers. Prefer restarting Revit over Reload; `T3Lab.extension/startup.py` detects the stuck state on reload and says so. The fix, `T3Lab.extension/lib/pyrevit_patches.py`, keeps the CPython engine cached across reloads; `startup.py` scans and applies it automatically whenever it is missing (owner's explicit request, 2026-09-29): running clone only, exact pyRevit 6.5.5 match only, backup `sessionmgr.py.t3lab-backup`, one notification when applied. Per-machine opt-out: env `T3LAB_NO_PYREVIT_PATCH=1` or file `%APPDATA%\T3LabAI\pyrevit_patch.disabled`; undo with `pyrevit_patches.py restore`.
   - For modeless windows, set `__persistentengine__ = True` and use `detect_persistent_engine()` to gracefully fall back to modal execution if the persistent engine has not yet been activated by a reload.
