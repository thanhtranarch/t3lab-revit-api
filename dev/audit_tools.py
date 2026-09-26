#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T3Lab static tool audit (CPython 3).

Kiểm tra tĩnh toàn bộ pushbutton script + GUI dialog + XAML:
  1. CPython 3 compliance:
     - Pushbutton scripts phải có shebang '#! python3' ở dòng đầu.
     - Cú pháp hợp lệ với Python 3 (ast.parse).
     - Phát hiện cú pháp legacy Python 2 (xrange, bare __builtin__, bare execfile).
  2. Chuỗi launcher -> lib/GUI/*Dialog.py -> XAML: module, entry function, file XAML tồn tại.
  3. Event handler khai báo trong XAML (Click=..., SelectionChanged=...) phải có
     method tương ứng trong file Python sở hữu XAML đó (hoặc lớp base WPF_Base / Mixin).
  4. Transaction hygiene: có Start() mà không có Commit() / Assimilate().
  5. XAML mồ côi: không file Python nào tham chiếu.

Usage:
    python dev/audit_tools.py            # in báo cáo đầy đủ
    python dev/audit_tools.py --quiet    # chỉ in vi phạm, exit 1 nếu có
"""
import os
import re
import sys
import ast
import glob

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, "T3Lab.extension")
from tabdir import TAB  # noqa: E402  (the tab folder name changes)
LIB = os.path.join(ROOT, "lib")
GUI_TOOLS = os.path.join(LIB, "GUI", "Tools")

QUIET = "--quiet" in sys.argv
FAILED = [False]


def report(msg, violation=False):
    if violation:
        FAILED[0] = True
        print("!! " + msg)
    elif not QUIET:
        print("   " + msg)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def find_scripts():
    out = []
    for dirpath, _, filenames in os.walk(TAB):
        if dirpath.endswith(".pushbutton") and "script.py" in filenames:
            out.append(os.path.join(dirpath, "script.py"))
    return sorted(out)


def all_py_files():
    out = []
    for base in (LIB, TAB):
        for dirpath, _, files in os.walk(base):
            out.extend(os.path.join(dirpath, f) for f in files if f.endswith(".py"))
    return out


# Các XAML đã biết là legacy/mồ côi có chủ đích — thêm vào đây nếu chấp nhận giữ.
KNOWN_ORPHAN_XAML = {
    # Wizard-nav reference example named in .claude/CLAUDE.md — kept in place on
    # purpose, not wired to any pushbutton.
    "ExportManagerTest.xaml",
}


def check_python3_syntax_and_compat(path, src):
    try:
        ast.parse(src, filename=path)
    except SyntaxError as ex:
        report("%s:%d SyntaxError in Python 3: %s" % (path, ex.lineno or 1, ex.msg), True)
        return

    for i, ln in enumerate(src.splitlines(), 1):
        s = ln.strip()
        if s.startswith("#"):
            continue
        if re.search(r'\bxrange\s*\(', ln):
            report("%s:%d xrange (legacy Python 2, use range): %s" % (path, i, s[:70]), True)
        if re.search(r'^\s*import\s+__builtin__\b', ln):
            report("%s:%d bare 'import __builtin__' (use builtins): %s" % (path, i, s[:70]), True)
        if re.search(r'^\s*execfile\s*\(', ln):
            report("%s:%d bare 'execfile()' (removed in Python 3): %s" % (path, i, s[:70]), True)


def check_shebang(path, src):
    if not src.startswith("#! python3"):
        report("%s: missing '#! python3' shebang at line 1" % path, True)


def main():
    xaml_paths = glob.glob(os.path.join(GUI_TOOLS, "*.xaml"))
    xaml_names = {os.path.basename(p) for p in xaml_paths}

    py_files = all_py_files()
    py_src = {p: read(p) for p in py_files}
    pushbutton_scripts = find_scripts()

    # ── 1. CPython 3 Shebang & Syntax check ──
    if not QUIET:
        print("== 1. CPython 3 shebang & syntax check ==")
    for script in pushbutton_scripts:
        rel = os.path.relpath(script, REPO)
        check_shebang(rel, py_src[script])

    for p, s in sorted(py_src.items()):
        rel = os.path.relpath(p, REPO)
        check_python3_syntax_and_compat(rel, s)

    # ── 2. Chuỗi script -> XAML tồn tại ──
    if not QUIET:
        print("== 2. XAML references resolve ==")
    for p, s in sorted(py_src.items()):
        for x in set(re.findall(r'["\']([\w\-. /\\]+\.xaml)["\']', s)):
            base = os.path.basename(x.replace("\\", "/"))
            if base not in xaml_names and "Resources" not in x:
                if not os.path.exists(os.path.join(os.path.dirname(p), base)):
                    report("%s: XAML not found: %s" % (os.path.relpath(p, REPO), x), True)

    # ── 3. XAML event handler <-> Python method ──
    if not QUIET:
        print("== 3. XAML handlers have Python methods ==")
    base_handlers = set(re.findall(r'def\s+(\w+)\s*\(', py_src.get(os.path.join(LIB, "GUI", "WPF_Base.py"), "")))
    # Handlers cung cấp bởi mixin dùng chung (lib/GUI/*Mixin.py) chỉ được tính
    # cho những script thực sự nhắc tới mixin đó (import/inherit).
    mixin_defs = {}
    for _p, _s in py_src.items():
        _name = os.path.basename(_p)
        if _name.endswith("Mixin.py"):
            mixin_defs[_name[:-3]] = set(re.findall(r'def\s+(\w+)\s*\(', _s))
    EVENT = re.compile(
        r'\b(?:Click|Checked|Unchecked|SelectionChanged|TextChanged|MouseLeftButtonDown'
        r'|MouseDoubleClick|Loaded|Closing|KeyDown|KeyUp|PreviewKeyDown|PreviewTextInput'
        r'|DropDownClosed|ValueChanged|Drop|DragOver|PreviewMouseLeftButtonDown|MouseDown'
        r'|LostFocus|GotFocus|ScrollChanged|SizeChanged|CellEditEnding|RowEditEnding'
        r'|BeginningEdit|SelectedDateChanged)\s*=\s*"([A-Za-z_]\w*)"')
    for xaml in sorted(xaml_paths):
        base = os.path.basename(xaml)
        handlers = set(EVENT.findall(read(xaml)))
        if not handlers:
            continue
        owners = [p for p, s in py_src.items() if base in s]
        if not owners:
            if base not in KNOWN_ORPHAN_XAML:
                report("%s: orphan XAML — no Python file references it (%d handlers)"
                       % (base, len(handlers)), True)
            continue
        combined = "\n".join(py_src[p] for p in owners)
        defined = set(re.findall(r'def\s+(\w+)\s*\(', combined)) | base_handlers
        for _mixin, _defs in mixin_defs.items():
            if _mixin in combined:
                defined |= _defs
        missing = sorted(handlers - defined)
        if missing:
            report("%s: handlers missing in %s: %s"
                   % (base, ", ".join(os.path.relpath(p, REPO) for p in owners),
                      ", ".join(missing)), True)

    # ── 4. Transaction hygiene ──
    if not QUIET:
        print("== 4. Transaction hygiene ==")
    for script in pushbutton_scripts:
        s = py_src[script]
        if "Transaction(" in s:
            starts = len(re.findall(r'\.Start\(\)', s))
            commits = len(re.findall(r'\.Commit\(\)', s)) + len(re.findall(r'\.Assimilate\(\)', s))
            if starts and not commits:
                report("%s: Transaction Start() without Commit()/Assimilate()"
                       % os.path.relpath(script, REPO), True)

    print()
    if FAILED[0]:
        print("AUDIT: violations found")
        sys.exit(1)
    print("AUDIT: clean")


if __name__ == "__main__":
    main()
