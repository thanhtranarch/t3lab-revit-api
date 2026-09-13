#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gác kỷ luật API context của các dialog MODELESS.

Vì sao cần script này: cửa sổ modeless chạy handler WPF **ngoài** Revit API
context. Gọi `self.doc` / `self.uidoc` / `Transaction(...)` từ đó ném
"Attempting to access Revit API outside of API context" — và lỗi đó **không
xuất hiện** ở bản modal, không xuất hiện trong audit tĩnh nào, và chỉ nổ ra
đúng nhánh code người dùng bấm tới. Nó vô hình cho tới khi có người dùng thật.

Đã bắt được thật: `ManaSelectDialog._rebuild_explore_tree` gọi thẳng từ
handler của combo "Sort by" và ô Search. Gom theo Category/Family/Type thì
chạy (ba khoá đó cache sẵn), nhưng gom theo Workset/Level/Phase/Design Option
thì `ElementRecord` đọc lười và chạm document → nổ. Ba trong bảy lựa chọn
Sort by hỏng, bốn cái kia không.

Luật được gác:
    Mọi method được nối vào một event của WPF (`x.Click += self._on_foo`)
    KHÔNG được chạm Revit API trực tiếp. Nó phải đẩy qua `_run_in_revit(...)`
    (hoặc `_dispatch(...)` theo kiểu BCFReader).

    Method `*_impl` là phần thân chạy TRONG context — chúng được phép chạm API
    và script bỏ qua chúng.

Usage:
    python dev/audit_api_context.py
    python dev/audit_api_context.py --quiet     # chỉ vi phạm, exit 1 nếu có
"""
import ast
import io
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI = os.path.join(REPO, "T3Lab.extension", "lib", "GUI")

# Chỉ soi dialog nào THẬT SỰ modeless. Bản modal giữ context suốt ShowDialog()
# nên luật này không áp — soi hết sẽ ra hàng trăm báo động giả.
MODELESS_MARKERS = ("IExternalEventHandler", "ExternalEvent.Create")

# Dấu hiệu chạm Revit API.
API_ATTRS = ("doc", "uidoc")
API_CALLS = ("Transaction", "SubTransaction", "TransactionGroup",
             "FilteredElementCollector")

# Event của WPF mà tool này hay nối vào.
EVENT_NAMES = ("Click", "TextChanged", "SelectionChanged", "Checked",
               "Unchecked", "SelectedItemChanged", "MouseDoubleClick",
               "PreviewMouseDoubleClick", "KeyDown", "Loaded", "Expanded",
               "Collapsed", "LostFocus", "GotFocus", "Drop")

# Cầu nối hợp lệ sang API context.
DISPATCHERS = ("_run_in_revit", "_dispatch")


def _attr_chain(node):
    """`self.doc.ActiveView` -> ['self', 'doc', 'ActiveView']"""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return list(reversed(parts))


class _MethodScan(object):
    """Cái một method làm: chạm API? gọi dispatcher? gọi method nào khác?"""

    def __init__(self, name):
        self.name = name
        self.api_hits = []          # [(lineno, mô tả)]
        self.dispatches = False
        self.calls = set()          # tên method self.* được gọi
        self.dispatched = set()     # tên method đưa VÀO dispatcher


def scan_class(tree):
    methods = {}
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in [n for n in cls.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            scan = _MethodScan(fn.name)
            for node in ast.walk(fn):
                # self.doc / self.uidoc
                if isinstance(node, ast.Attribute):
                    chain = _attr_chain(node)
                    if len(chain) >= 2 and chain[0] == "self" \
                            and chain[1] in API_ATTRS:
                        scan.api_hits.append(
                            (node.lineno, ".".join(chain[:3])))
                # Transaction(...) / FilteredElementCollector(...)
                if isinstance(node, ast.Call):
                    fname = None
                    if isinstance(node.func, ast.Name):
                        fname = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        fname = node.func.attr
                    if fname in API_CALLS:
                        scan.api_hits.append((node.lineno, fname + "(...)"))
                    # self._foo(...) và self._run_in_revit(self._foo)
                    if isinstance(node.func, ast.Attribute) \
                            and isinstance(node.func.value, ast.Name) \
                            and node.func.value.id == "self":
                        called = node.func.attr
                        scan.calls.add(called)
                        if called in DISPATCHERS:
                            scan.dispatches = True
                            for arg in node.args:
                                for sub in ast.walk(arg):
                                    if isinstance(sub, ast.Attribute) \
                                            and isinstance(sub.value, ast.Name) \
                                            and sub.value.id == "self":
                                        scan.dispatched.add(sub.attr)
            methods[fn.name] = scan
    return methods


def wired_handlers(src):
    """Method được nối vào event WPF: `x.Click += self._on_foo`."""
    out = {}
    pattern = re.compile(
        r"\.(%s)\s*\+=\s*self\.(\w+)" % "|".join(EVENT_NAMES))
    for i, line in enumerate(src.splitlines(), 1):
        m = pattern.search(line)
        if m:
            out.setdefault(m.group(2), (m.group(1), i))
    # RoutedEventHandler(self._foo) -> AddHandler
    for m in re.finditer(r"RoutedEventHandler\(self\.(\w+)\)", src):
        out.setdefault(m.group(1), ("AddHandler", 0))
    return out


def audit_file(path):
    src = io.open(path, encoding="utf-8").read()
    if not any(mark in src for mark in MODELESS_MARKERS):
        return None, []                     # không phải dialog modeless

    methods = scan_class(ast.parse(src))
    handlers = wired_handlers(src)
    issues = []

    for name, (event, lineno) in sorted(handlers.items()):
        scan = methods.get(name)
        if scan is None:
            issues.append(("P1", "%s nối vào .%s nhưng không có method này"
                           % (name, event)))
            continue

        # Thân chạy-trong-context: được phép chạm API.
        if name.endswith("_impl"):
            continue

        # Chạm API ngay trong handler = lỗi.
        for hit_line, what in scan.api_hits:
            issues.append(("P0", "%s (nối .%s) chạm %s ở dòng %d — phải đẩy "
                                 "qua %s()"
                           % (name, event, what, hit_line, DISPATCHERS[0])))

        # Gọi thẳng một method có chạm API mà không qua dispatcher.
        for called in sorted(scan.calls):
            if called in DISPATCHERS or called == name:
                continue
            target = methods.get(called)
            if target is None or not target.api_hits:
                continue
            if called in scan.dispatched:
                continue                    # đã đưa vào dispatcher, đúng rồi
            issues.append(("P0", "%s (nối .%s) gọi thẳng %s() — method đó "
                                 "chạm %s ở dòng %d, phải đẩy qua %s()"
                           % (name, event, called,
                              target.api_hits[0][1], target.api_hits[0][0],
                              DISPATCHERS[0])))

    return os.path.basename(path), issues


def main():
    quiet = "--quiet" in sys.argv[1:]
    total = bad = scanned = 0

    for fname in sorted(os.listdir(GUI)):
        if not fname.endswith(".py"):
            continue
        try:
            base, issues = audit_file(os.path.join(GUI, fname))
        except SyntaxError as exc:
            print("  %-28s không parse được: %s" % (fname, exc))
            bad += 1
            continue
        if base is None:
            continue
        scanned += 1
        total += len(issues)
        if issues:
            bad += 1
            print("%s" % base)
            for sev, msg in issues:
                print("  [%s] %s" % (sev, msg))
        elif not quiet:
            print("  %-28s ok" % base)

    print("API CONTEXT: %d dialog modeless · %d file có vi phạm · %d vi phạm"
          % (scanned, bad, total))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
