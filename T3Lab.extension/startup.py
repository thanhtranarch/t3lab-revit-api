# -*- coding: utf-8 -*-
"""T3Lab startup check — runs once per pyRevit load, BEFORE any T3Lab tool.

Không có shebang là CÓ Ý: file này chạy trên engine mặc định của pyRevit
(IronPython 2.7 / 3.4), không phải CPython. Mọi tool T3Lab là `#! python3`;
khi engine CPython của pyRevit không nạp được python3XX.dll, tool chết ngay
trong pythonnet trước khi chạy tới dòng code T3Lab nào, và Revit chỉ hiện
"The type initializer for 'Delegates' threw an exception". IronPython không
cần DLL đó, nên ở đây vẫn kiểm được, tìm ra lý do thật và nói bằng lời.

Việc thứ hai (theo yêu cầu của chủ extension, 2026-09-29): tự quét và áp bản vá
pyRevit `lib/pyrevit_patches.py` khi còn thiếu — để pyRevit Reload không tắt
engine CPython (nguyên nhân của "This property must be set before runtime is
initialized"). Quy tắc minh bạch vì đây là sửa phần mềm của bên khác:
  * chỉ vá clone pyRevit ĐANG CHẠY, chỉ bản pyRevit khớp chính xác (6.5.5);
  * luôn giữ bản gốc `sessionmgr.py.t3lab-backup`; báo MỘT lần khi vá;
  * tắt được: biến môi trường T3LAB_NO_PYREVIT_PATCH=1, hoặc tạo file
    %APPDATA%\\T3LabAI\\pyrevit_patch.disabled. Gỡ: pyrevit_patches.py restore.

Vì chạy trên IronPython: không f-string, không cú pháp chỉ có ở Python 3.

Im lặng trên máy khoẻ. Không bao giờ ném lỗi: một lượt kiểm hỏng không được
làm Revit khởi động thất bại.
"""
import os
import sys

SUPPORTED_REVIT = (2022, 2027)
TITLE = "T3Lab"
LOG_NAME = "engine_check.log"
PATCH_OPT_OUT_ENV = "T3LAB_NO_PYREVIT_PATCH"
PATCH_OPT_OUT_FILE = "pyrevit_patch.disabled"


def _clone_root():
    """pyRevit clone folder, from pyrevitlib/pyrevit/__init__.py."""
    try:
        import pyrevit
        pkg = os.path.dirname(os.path.abspath(pyrevit.__file__))
        return os.path.dirname(os.path.dirname(pkg))
    except Exception:
        return None


def _engine_dlls(clone):
    """[(engine name, python3XX.dll or None)] pyRevit would run '#! python3' with."""
    # The engine selected in pyRevit settings: AssemblyPath is the exact DLL
    # pythonnet loads (verified on pyRevit 6.5.5: ...\cengines\CPY3123\python312.dll).
    try:
        from pyrevit.userconfig import user_config
        engine = user_config.get_active_cpython_engine()
        dll = str(getattr(engine, "AssemblyPath", "") or "") if engine is not None else ""
        if dll.lower().endswith(".dll") and os.path.isfile(dll):
            return [(os.path.basename(os.path.dirname(dll)), dll)]
    except Exception:
        pass
    root = os.path.join(clone, "bin", "cengines") if clone else ""
    try:
        names = sorted(os.listdir(root))
    except Exception:
        return []
    return [(n, _python_dll(os.path.join(root, n))) for n in names
            if n.upper().startswith("CPY") and os.path.isdir(os.path.join(root, n))]


def _python_dll(engine_dir):
    try:
        for fn in os.listdir(engine_dir):
            low = fn.lower()
            if low.startswith("python3") and low.endswith(".dll") and low != "python3.dll":
                return os.path.join(engine_dir, fn)
    except Exception:
        pass
    return None


def _load_error(path):
    """None when the DLL loads (or cannot be tested here), else the reason.

    The DLL is deliberately left loaded: pythonnet loads the very same file on
    the first T3Lab click anyway, and freeing a handle obtained through ctypes
    on IronPython risks a truncated 64-bit handle.
    """
    try:
        from System.Runtime.InteropServices import NativeLibrary   # .NET Core: Revit 2025+
    except Exception:
        NativeLibrary = None
    if NativeLibrary is not None:
        try:
            NativeLibrary.Load(path)
            return None
        except Exception as exc:
            return getattr(exc, "Message", None) or str(exc)
    try:
        import ctypes                                               # .NET Framework: Revit 2022-2024
        kernel32 = ctypes.windll.kernel32
        kernel32.LoadLibraryExW.restype = ctypes.c_void_p
        handle = kernel32.LoadLibraryExW(ctypes.c_wchar_p(path), None, 8)
        if not handle:
            return "Windows refused to load it (Win32 error %d)" % kernel32.GetLastError()
    except Exception:
        pass
    return None


def _revit_year():
    try:
        from pyrevit import HOST_APP
        return int(HOST_APP.version)
    except Exception:
        return None


def find_problems():
    """[English sentence] — empty on a healthy machine."""
    problems = []
    year = _revit_year()
    lo, hi = SUPPORTED_REVIT
    if year is not None and not (lo <= year <= hi):
        problems.append("Revit %d is outside the supported range (Revit %d-%d). "
                        "Some T3Lab tools may not work." % (year, lo, hi))

    engines = _engine_dlls(_clone_root())
    if not engines:
        problems.append("This pyRevit installation has no CPython engine (bin\\cengines\\CPY*). "
                        "Every T3Lab tool needs it. Reinstall pyRevit 5 or newer.")
        return problems
    for name, dll in engines:
        if dll is None:
            problems.append("The CPython engine %s has no python3XX.dll - the pyRevit "
                            "installation is incomplete. Reinstall pyRevit." % name)
            continue
        reason = _load_error(dll)
        if reason:
            problems.append("The CPython engine %s cannot load %s: %s" %
                            (name, os.path.basename(dll), reason))
    return problems


def _log(text):
    try:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, "T3LabAI")
        if not os.path.isdir(folder):
            os.makedirs(folder)
        import io
        import time
        with io.open(os.path.join(folder, LOG_NAME), "a", encoding="utf-8") as fh:
            fh.write(u"%s\n%s\n\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


def _notify(problems):
    message = "T3Lab tools cannot start on this machine."
    details = "\n\n".join(problems) + (
        "\n\nUntil this is fixed, every T3Lab button fails with \"The type "
        "initializer for 'Delegates' threw an exception\".\n\n"
        "Next step: run scripts\\Install-T3Lab.ps1 -CheckOnly from the T3Lab "
        "folder for the exact cause (see INSTALL.md), fix it, then restart Revit.")
    _log(message + "\n" + details)
    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogIcon
        dialog = TaskDialog(TITLE)
        dialog.MainInstruction = message
        dialog.MainContent = details
        dialog.MainIcon = TaskDialogIcon.TaskDialogIconWarning
        dialog.Show()
    except Exception:
        pass


def pythonnet_half_started():
    """True when an earlier failed CPython (re)start left pythonnet stuck.

    That state — Runtime._isInitialized set, PythonEngine.initialized not — is
    what a pyRevit reload leaves behind on Revit 2025+ (pythonnet 3 cannot
    restart after Shutdown there), and from then on every '#! python3' click
    fails with "This property must be set before runtime is initialized" until
    Revit restarts. Reads two private static bools through reflection, and only
    when pythonnet is already loaded: it never loads pythonnet or starts Python.
    """
    try:
        from System import AppDomain
        from System.Reflection import BindingFlags
        asm = None
        for candidate in AppDomain.CurrentDomain.GetAssemblies():
            if candidate.GetName().Name == "pyRevitLabs.PythonNet":
                asm = candidate
                break
        if asm is None:
            return False                    # CPython never ran in this Revit
        flags = BindingFlags.NonPublic | BindingFlags.Static
        runtime_flag = asm.GetType("Python.Runtime.Runtime").GetField("_isInitialized", flags)
        engine_flag = asm.GetType("Python.Runtime.PythonEngine").GetField("initialized", flags)
        if runtime_flag is None or engine_flag is None:
            return False                    # another pythonnet build: do not guess
        return bool(runtime_flag.GetValue(None)) and not bool(engine_flag.GetValue(None))
    except Exception:
        return False


def _notify_restart():
    message = "Restart Revit to use T3Lab tools again."
    details = ("A pyRevit reload earlier in this session (pyRevit > Reload, or "
               "enabling/disabling an extension) stopped pyRevit's CPython "
               "engine in a state it cannot recover from on Revit 2025 and "
               "newer. Until Revit restarts, every T3Lab button fails with "
               "\"This property must be set before runtime is initialized\".\n\n"
               "Save your work and restart Revit. T3Lab patches pyRevit so "
               "Reload no longer does this once Revit has restarted; also avoid "
               "Ctrl+Alt+Shift+Click on pyRevit buttons, which forces the same "
               "engine shutdown. See INSTALL.md.")
    _log(message + "\n" + details)
    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogIcon
        dialog = TaskDialog(TITLE)
        dialog.MainInstruction = message
        dialog.MainContent = details
        dialog.MainIcon = TaskDialogIcon.TaskDialogIconWarning
        dialog.Show()
    except Exception:
        pass


def _data_dir():
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "T3LabAI")


def patch_opted_out():
    """True when this machine's user switched the automatic pyRevit patch off."""
    if (os.environ.get(PATCH_OPT_OUT_ENV) or "").strip() not in ("", "0"):
        return True
    return os.path.isfile(os.path.join(_data_dir(), PATCH_OPT_OUT_FILE))


def _load_patches():
    """lib/pyrevit_patches.py, or None. startup.py may run without lib on sys.path."""
    candidates = []
    try:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
    except Exception:
        pass
    candidates += list(sys.path)
    for lib in candidates:
        try:
            if os.path.isfile(os.path.join(lib, "pyrevit_patches.py")):
                if lib not in sys.path:
                    sys.path.insert(0, lib)
                break
        except Exception:
            continue
    try:
        import pyrevit_patches
        return pyrevit_patches
    except Exception:
        return None


def auto_patch_pyrevit(patches=None):
    """Scan the running pyRevit and apply the reload patch when it is missing.

    Returns (state, detail); state is None when skipped. Never raises. Only the
    pyRevit clone running this code is touched (pyrevit_patches.sessionmgr_path
    puts it first), and only when its sessionmgr.py matches the known 6.5.5
    text exactly — any other version reports 'unsupported' and stays untouched.
    """
    if patch_opted_out():
        return None, "opted out"
    patches = patches or _load_patches()
    if patches is None:
        return None, "lib/pyrevit_patches.py not found"
    try:
        path = patches.sessionmgr_path()
        if not path:
            return None, "pyRevit's sessionmgr.py not found"
        return patches.ensure(path)
    except Exception as exc:
        return "error", str(exc)


def _notify_patched(detail):
    message = "T3Lab updated pyRevit so that Reload no longer breaks T3Lab tools."
    details = ("On Revit 2025 and newer, pyRevit > Reload (or enabling/disabling "
               "an extension) shut down pyRevit's CPython engine, which cannot "
               "restart there - every T3Lab button then failed with \"This "
               "property must be set before runtime is initialized\" until Revit "
               "restarted. Reload now keeps that engine running.\n\n"
               "Changed file: %s\nOriginal kept as: %s.t3lab-backup\n\n"
               "Takes effect after the next Revit restart. To undo: run "
               "T3Lab.extension\\lib\\pyrevit_patches.py restore, and create "
               "%%APPDATA%%\\T3LabAI\\%s to stop T3Lab from applying it again."
               % (detail, detail, PATCH_OPT_OUT_FILE))
    _log(message + "\n" + details)
    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogIcon
        dialog = TaskDialog(TITLE)
        dialog.MainInstruction = message
        dialog.MainContent = details
        dialog.MainIcon = TaskDialogIcon.TaskDialogIconInformation
        dialog.Show()
    except Exception:
        pass


def main():
    # 1. Keep pyRevit patched. Runs on every load, so a pyRevit reinstall or
    #    update (which overwrites sessionmgr.py) is patched again automatically.
    state, detail = auto_patch_pyrevit()
    if state == "applied":
        _notify_patched(detail)
    elif state not in (None, "already"):
        _log("pyRevit reload patch not applied (%s): %s" % (state, detail))

    # 2. startup.py also runs on every pyRevit reload — exactly when the stuck
    #    CPython state appears — so say it now instead of on the next click.
    if pythonnet_half_started():
        _notify_restart()
        return
    try:
        problems = find_problems()
    except Exception:
        return
    # A version warning alone is not worth a dialog on every start; log it.
    blocking = [p for p in problems if not p.startswith("Revit ")]
    if blocking:
        _notify(problems)
    elif problems:
        _log("\n".join(problems))


main()
