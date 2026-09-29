# -*- coding: utf-8 -*-
"""Self-healing patch for the pyRevit behaviour behind
"This property must be set before runtime is initialized".

Vì sao (đã xác minh 2026-09-29 bằng Revit journal + IL của pyRevit 6.5.5):

  1. pyRevit Reload — kể cả bật/tắt extension trong Extensions manager — gọi
     `sessionmgr._clear_running_engines()` -> `ScriptEngineManager.ClearEngines`,
     hàm này Shutdown MỌI engine, gồm engine CPython.
  2. pythonnet 3.0.3 không khởi động lại được sau `PythonEngine.Shutdown` trên
     .NET 8+: bước stash cần BinaryFormatter (đã bị xoá khỏi .NET 9+). Noop
     formatter của `_cpython_bootstrap` giúp Shutdown chạy qua, nhưng lần
     Initialize kế tiếp đọc stash rỗng -> NullReferenceException trong
     `RuntimeData.RestoreRuntimeDataImpl`, runtime kẹt ở trạng thái
     khởi tạo dở dang.
  3. `CPythonEngine.Start` luôn gán `Runtime.PythonDLL` cho engine mới -> từ đó
     mọi tool '#! python3' ném "This property must be set before runtime is
     initialized" (hoặc NullReferenceException) cho tới khi đóng Revit.

Cách sửa: giữ engine CPython trong cache qua lần Reload thay vì Shutdown nó.
Click kế tiếp dùng lại engine đó (`RecoveredFromCache`), không bao giờ chạm
`set_PythonDLL`. Mất gì? Không: pythonnet 3 vốn không finalize interpreter khi
Shutdown, nên `sys.modules` vẫn sống qua Reload như cũ.

Patch được áp bằng thay thế CHÍNH XÁC văn bản hàm gốc của pyRevit 6.5.5. Bản
pyRevit khác (hàm đã đổi) -> không đụng gì, báo 'unsupported'. Idempotent nhờ
marker; lưu bản gốc cạnh file (`.t3lab-backup`); `restore()` gỡ patch.

Tự áp: T3Lab.extension/startup.py gọi `ensure()` cho clone pyRevit đang chạy ở
mỗi lần pyRevit nạp (theo yêu cầu của chủ extension, 2026-09-29), báo một lần
khi vá. Tắt trên một máy: biến môi trường T3LAB_NO_PYREVIT_PATCH=1 hoặc file
%APPDATA%\\T3LabAI\\pyrevit_patch.disabled. Chạy tay — xem INSTALL.md:

    python T3Lab.extension/lib/pyrevit_patches.py status
    python T3Lab.extension/lib/pyrevit_patches.py apply
    python T3Lab.extension/lib/pyrevit_patches.py restore

Không truyền đường dẫn thì CLI tìm sessionmgr.py trong các clone pyRevit thông
dụng; có thể truyền thẳng: `... apply <đường dẫn tới sessionmgr.py>`.

Chạy được trên IronPython 2.7 / 3.4 (startup.py) và CPython 3 (test):
không f-string, không cú pháp chỉ có ở Python 3.
"""
import io
import os

MARKER = u"T3LAB-PATCH keep-cpython-alive"
BACKUP_SUFFIX = u".t3lab-backup"

# pyRevit 6.5.5 — pyrevitlib/pyrevit/loader/sessionmgr.py (newlines normalised to \n)
ORIGINAL = u'''def _clear_running_engines():
    # clear the cached engines
    try:
        my_output = output.get_output()
        if my_output:
            my_output.close_others(all_open_outputs=True)

        runtime_types.ScriptEngineManager.ClearEngines(
            excludeEngine=EXEC_PARAMS.engine_id
        )
        return True
    except AttributeError:
        return False
'''

PATCHED = u'''def _clear_running_engines():
    # clear the cached engines
    try:
        my_output = output.get_output()
        if my_output:
            my_output.close_others(all_open_outputs=True)

        # T3LAB-PATCH keep-cpython-alive v1 -- applied with T3Lab.extension/lib/pyrevit_patches.py
        # (original kept next to this file as .t3lab-backup; "restore" undoes it).
        # pythonnet 3 cannot restart CPython after PythonEngine.Shutdown on
        # .NET 8+, so shutting the CPython engine down here left every
        # '#! python3' tool failing with "This property must be set before
        # runtime is initialized" until Revit restarted. CPython engines now
        # stay cached across the reload instead.
        kept = []
        try:
            engines = runtime_types.ScriptEngineManager.EngineDict
            for key in list(engines.Keys):
                engine = engines[key]
                if engine is not None and engine.GetType().Name == "CPythonEngine":
                    kept.append((key, engine))
            for key, _engine in kept:
                engines.Remove(key)
        except Exception:
            kept = []
        runtime_types.ScriptEngineManager.ClearEngines(
            excludeEngine=EXEC_PARAMS.engine_id
        )
        if kept:
            try:
                engines = runtime_types.ScriptEngineManager.EngineDict
                for key, engine in kept:
                    engines[key] = engine
            except Exception:
                pass
        # T3LAB-PATCH end
        return True
    except AttributeError:
        return False
'''

APPLIED = "applied"
MISSING = "missing"
ALREADY = "already"
UNSUPPORTED = "unsupported"
READONLY = "readonly"
ERROR = "error"


def sessionmgr_paths():
    """sessionmgr.py of every pyRevit clone found: the running one first."""
    found = []
    try:
        import pyrevit
        found.append(os.path.join(os.path.dirname(os.path.abspath(pyrevit.__file__)),
                                  "loader", "sessionmgr.py"))
    except Exception:
        pass
    for var in ("APPDATA", "PROGRAMDATA", "ProgramFiles"):
        base = os.environ.get(var) or ""
        try:
            names = os.listdir(base) if base else []
        except Exception:
            names = []
        for name in names:
            if name.lower().startswith("pyrevit"):
                found.append(os.path.join(base, name, "pyrevitlib", "pyrevit",
                                          "loader", "sessionmgr.py"))
    out = []
    for path in found:
        if os.path.isfile(path) and os.path.normcase(path) not in [os.path.normcase(p) for p in out]:
            out.append(path)
    return out


def sessionmgr_path():
    """sessionmgr.py of the running pyRevit (or the first clone found), or None."""
    paths = sessionmgr_paths()
    return paths[0] if paths else None


def _read(path):
    """(text with \\n newlines, newline used by the file)."""
    with io.open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    newline = u"\r\n" if u"\r\n" in text else u"\n"
    return text.replace(u"\r\n", u"\n"), newline


def _write(path, text, newline):
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace(u"\n", newline))


def status(path=None):
    """ALREADY (patched) / MISSING (patchable) / UNSUPPORTED / ERROR. Never raises."""
    path = path or sessionmgr_path()
    if not path:
        return ERROR
    try:
        text, _ = _read(path)
    except Exception:
        return ERROR
    if MARKER in text:
        return ALREADY
    if ORIGINAL in text:
        return MISSING
    return UNSUPPORTED


def ensure(path=None):
    """Apply the patch when needed. Returns (status, detail); never raises."""
    path = path or sessionmgr_path()
    if not path:
        return ERROR, u"pyRevit's sessionmgr.py was not found"
    try:
        text, newline = _read(path)
    except Exception as exc:
        return ERROR, u"cannot read %s: %s" % (path, exc)

    if MARKER in text:
        return ALREADY, path
    if text.count(ORIGINAL) != 1:
        return UNSUPPORTED, (u"%s does not contain the pyRevit 6.5.5 version of "
                             u"_clear_running_engines; left untouched" % path)

    backup = path + BACKUP_SUFFIX
    try:
        if not os.path.exists(backup):
            _write(backup, text, newline)
        _write(path, text.replace(ORIGINAL, PATCHED, 1), newline)
    except (IOError, OSError) as exc:
        return READONLY, u"cannot write %s: %s" % (path, exc)
    except Exception as exc:
        return ERROR, u"patching %s failed: %s" % (path, exc)
    return APPLIED, path


def restore(path=None):
    """Undo the patch (put the original function back). Returns (status, detail)."""
    path = path or sessionmgr_path()
    if not path:
        return ERROR, u"pyRevit's sessionmgr.py was not found"
    try:
        text, newline = _read(path)
        if MARKER not in text:
            return ALREADY, path
        if text.count(PATCHED) != 1:
            return UNSUPPORTED, u"patched block in %s was edited; restore it from %s%s" % (
                path, path, BACKUP_SUFFIX)
        _write(path, text.replace(PATCHED, ORIGINAL, 1), newline)
        return APPLIED, path
    except Exception as exc:
        return ERROR, u"restoring %s failed: %s" % (path, exc)


def main(argv):
    """CLI: status | apply | restore [path to sessionmgr.py]."""
    command = argv[1] if len(argv) > 1 else "status"
    paths = argv[2:] or sessionmgr_paths()
    if command not in ("status", "apply", "restore") or not paths:
        print("usage: pyrevit_patches.py status|apply|restore [sessionmgr.py]")
        return 2
    code = 0
    for path in paths:
        if command == "status":
            state, detail = status(path), path
        elif command == "apply":
            state, detail = ensure(path)
        else:
            state, detail = restore(path)
        print("%-11s %s" % (state, detail))
        if state in (UNSUPPORTED, READONLY, ERROR):
            code = 1
    if command != "status" and code == 0:
        print("Restart Revit (not pyRevit > Reload) for the change to take effect.")
    return code


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
