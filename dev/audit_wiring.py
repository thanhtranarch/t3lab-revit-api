#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Audit WIRING giữa XAML và Python, theo từng nhóm tool (panel ribbon).

audit_tools.py chỉ kiểm một chiều (handler khai trong XAML phải có method).
Script này bắt các loại "chức năng chết" còn lại — thứ mà người dùng thấy là
"bấm không có gì xảy ra" hoặc "Command Failure for External Command":

  W1  Python đọc `self.<ten>` mà KHÔNG XAML nào của file có x:Name đó, không
      có dòng nào gán, không bọc guard (hasattr/getattr/FindName) → AttributeError.
  W2  Event khai trong DataTemplate/ControlTemplate nhưng window không bật
      WIRE_TEMPLATED_CLICKS và không tự AddHandler → handler KHÔNG BAO GIỜ chạy.
  W3  Nút (Button / MenuItem) không có Click/Command, và x:Name (nếu có) không
      được Python nào nhắc tới → bấm không làm gì.
  D1  Method trông như event handler (…_click / …_changed / on_…) mà không
      XAML nào khai, không chỗ nào gọi → handler chết.
  D2  Hàm / method bất kỳ chỉ xuất hiện đúng 1 lần (chính dòng def) trong
      toàn bộ extension → code chết (chỉ báo, không fail).
  D3  Module GUI/*Dialog.py không ai import → dialog mồ côi.

Usage:
    python3 dev/audit_wiring.py            # báo cáo đầy đủ theo nhóm
    python3 dev/audit_wiring.py --quiet    # chỉ W1/W2/W3/D1, exit 1 nếu có
    python3 dev/audit_wiring.py --dead     # kèm danh sách D2 (code chết)
"""
import ast
import os
import re
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(REPO, "T3Lab.extension")
from tabdir import TAB  # noqa: E402  (the tab folder name changes)
LIB = os.path.join(EXT, "lib")
TOOLS = os.path.join(LIB, "GUI", "Tools")

QUIET = "--quiet" in sys.argv
SHOW_DEAD = "--dead" in sys.argv

# Nút chrome được WPF_Base._wire_window_controls tự nối theo tên.
AUTO_WIRED = {
    'btn_close', 'btn_close_chrome', 'btn_close_x', 'button_close',
    'cancel_btn_top', 'btnCancelTop', 'btn_minimize', 'btn_maximize',
    'btn_min', 'btn_max', 'minimize_btn', 'maximize_btn', 'title_bar',
    'titlebar', 'border_titlebar', 'TitleBar', 'logo_image',
}
# Tên file XAML là item template / tài nguyên, không phải window.
EVENT_RE = re.compile(r'\s(Click|Checked|Unchecked|SelectionChanged|TextChanged|'
                      r'MouseDoubleClick|MouseLeftButtonDown|MouseLeftButtonUp|'
                      r'PreviewMouseLeftButtonDown|KeyDown|PreviewKeyDown|KeyUp|'
                      r'Loaded|Closing|Closed|LostFocus|GotFocus|ValueChanged|'
                      r'Expanded|Collapsed|DropDownOpened|DropDownClosed|'
                      r'SelectedItemChanged|Sorting|CellEditEnding|'
                      r'BeginningEdit|RequestNavigate|MouseEnter|MouseLeave|'
                      r'PreviewTextInput|Drop|DragOver|StateChanged|'
                      r'SizeChanged|IsVisibleChanged|ContextMenuOpening|'
                      r'MouseRightButtonUp|PreviewMouseWheel|ScrollChanged|'
                      r'Opened|Toggled|Unloaded|LoadingRow|'
                      r'CurrentCellChanged|SelectedCellsChanged|'
                      r'CheckedChanged|Tick|DataContextChanged)="([^"{]+)"')
NAME_RE = re.compile(r'x:Name="([^"]+)"')
TAG_RE = re.compile(r'<(/?)([A-Za-z_][\w.:]*)((?:"[^"]*"|[^>"])*?)(/?)>', re.S)
HANDLER_LIKE = re.compile(r'(_click(ed)?|_Click|_changed|_Changed|_checked|_Checked|'
                          r'_unchecked|_toggled?|_selected|_keydown|_KeyDown|'
                          r'_doubleclick|_DoubleClick|_mousedown|_MouseDown)$|^on_')


def read(p):
    with open(p, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def rel(p):
    return os.path.relpath(p, REPO).replace(os.sep, "/")


# ── Nạp toàn bộ file ──────────────────────────────────────────────────────
PY = {}
for base in (LIB, TAB):
    for d, _, fs in os.walk(base):
        for f in fs:
            if f.endswith(".py"):
                PY[os.path.join(d, f)] = read(os.path.join(d, f))
XAML = {f: read(os.path.join(TOOLS, f)) for f in os.listdir(TOOLS) if f.endswith(".xaml")}
ALL_TEXT = "\n".join(list(PY.values()) + list(XAML.values()))
TOKEN_COUNT = defaultdict(int)
for tok in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', ALL_TEXT):
    TOKEN_COUNT[tok] += 1


def xaml_info(name):
    """(names, events[(elem_name, event, handler, in_template, line)], buttons)."""
    src = XAML[name]
    names = set(NAME_RE.findall(src))
    events, buttons = [], []
    depth_tpl = 0
    for m in TAG_RE.finditer(src):
        closing, tag, attrs, selfclose = m.groups()
        line = src.count("\n", 0, m.start()) + 1
        is_tpl = tag.endswith("DataTemplate") or tag.endswith("ControlTemplate") \
            or tag.endswith("HierarchicalDataTemplate")
        if closing:
            if is_tpl:
                depth_tpl = max(0, depth_tpl - 1)
            continue
        nm = NAME_RE.search(attrs)
        elem = nm.group(1) if nm else None
        for ev, h in EVENT_RE.findall(" " + attrs):
            events.append((elem, ev, h, depth_tpl > 0, line))
        if tag in ("Button", "MenuItem") and "Click=" not in attrs and "Command=" not in attrs:
            # Nút mang Style chrome (WinClose/WinCtrl) do WPF_Base nối.
            buttons.append((elem, line, depth_tpl > 0, attrs))
        if is_tpl and not selfclose:
            depth_tpl += 1
    return names, events, buttons


XINFO = {x: xaml_info(x) for x in XAML}


def xamls_of(src):
    return sorted({x for x in XAML if re.search(r'[\'"/\\]' + re.escape(x) + r'[\'"]', src)
                   or ("'" + x[:-5] + "'" in src and "xaml" in src.lower())})


def gui_imports(src):
    """Module lib/GUI được import (1 cấp) → path."""
    out = set()
    for m in re.finditer(r'from\s+GUI\.([A-Za-z_]\w*)\s+import|import\s+GUI\.([A-Za-z_]\w*)|'
                         r'from\s+GUI\s+import\s+([A-Za-z_][\w, ]*)', src):
        mods = [m.group(1) or m.group(2)] if (m.group(1) or m.group(2)) else \
            [s.strip() for s in m.group(3).split(",")]
        for mod in mods:
            p = os.path.join(LIB, "GUI", mod + ".py")
            if os.path.exists(p):
                out.add(p)
    return out


BASE_SRC = read(os.path.join(LIB, "GUI", "WPF_Base.py"))
BASE_ATTRS = set(re.findall(r'def\s+(\w+)', BASE_SRC)) | set(re.findall(r'self\.(\w+)\s*=', BASE_SRC))


def py_facts(src):
    """(assigned, defined, reads{name: [lines]}, strings, class_attrs)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set(), set(), {}, set()
    assigned, defined, strings = set(), set(), set()
    reads = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "self":
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                assigned.add(node.attr)
            else:
                reads[node.attr].append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.add(node.value)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
    # class-level assignments (thuộc tính lớp)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for b in node.body:
                if isinstance(b, ast.Assign):
                    for t in b.targets:
                        if isinstance(t, ast.Name):
                            defined.add(t.id)
    return assigned, defined, reads, strings


def group_of(script_path):
    parts = rel(script_path).split("/")
    panel = next((p for p in parts if p.endswith(".panel")), "?")
    tool = next((p for p in reversed(parts) if p.endswith(".pushbutton")), "?")
    return panel[:-6], tool[:-11]


# ── Map tool → file Python liên quan (script + GUI import 2 cấp) ──────────
TOOLS_MAP = defaultdict(dict)   # panel -> tool -> set(py paths)
for p, src in PY.items():
    if p.startswith(TAB) and p.endswith("script.py"):
        panel, tool = group_of(p)
        files = {p} | gui_imports(src)
        for q in list(files):
            files |= gui_imports(PY.get(q, ""))
        TOOLS_MAP[panel][tool] = files

# ── Phân tích từng file Python sở hữu XAML ────────────────────────────────
FINDINGS = defaultdict(list)    # py path -> [(code, msg)]


def analyse(p):
    src = PY[p]
    xs = xamls_of(src)
    imported = gui_imports(src)
    for q in imported:                   # subclass dialog từ module khác
        xs += xamls_of(PY[q])
    xs = sorted(set(xs))
    if not xs:
        return
    assigned, defined, reads, strings = py_facts(src)
    for q in imported:
        a2, d2, _, s2 = py_facts(PY[q])
        assigned |= a2
        defined |= d2
        strings |= s2
    names = set().union(*(XINFO[x][0] for x in xs))
    dyn_names = [re.compile(re.sub(r'\\\{\\\}|%s', r'\\w+', re.escape(t)))
                 for t in strings if ("{}" in t or "%s" in t) and re.fullmatch(r'[\w{}%]+', t)]
    # W1
    for attr, lines in sorted(reads.items()):
        if attr in names or attr in assigned or attr in defined or attr in BASE_ATTRS:
            continue
        if attr in strings or attr.startswith("_") or not attr[:1].islower():
            continue
        FINDINGS[p].append(("W1", "self.{} (dòng {}) không có trong {}".format(
            attr, lines[0], ", ".join(xs))))
    # W2 / D1 prep
    flag_on = "WIRE_TEMPLATED_CLICKS = True" in src

    def templated_ok(ev):
        # Cờ chỉ nối Click (ButtonBase / Hyperlink); event khác phải AddHandler tay.
        if ev == "Click" and flag_on:
            return True
        return bool(re.search(r'AddHandler\([^)]*\b' + ev + r'Event', src))
    for x in xs:
        if not re.search(r'[\'"/\\]' + re.escape(x) + r'[\'"]', src):
            continue                     # XAML của module cha, để file cha tự báo
        _, events, buttons = XINFO[x]
        for elem, ev, h, in_tpl, line in events:
            if in_tpl and not templated_ok(ev):
                FINDINGS[p].append(("W2", "{}:{} {}=\"{}\" nằm trong template, không bao giờ được nối"
                                    .format(x, line, ev, h)))
        for elem, line, in_tpl, attrs in buttons:
            if "T3.WinClose" in attrs or "T3.WinCtrl" in attrs:
                continue
            if elem and any(p.fullmatch(elem) for p in dyn_names):
                continue                 # nối động: getattr(self, "btn_{}_ai".format(k))
            if elem and (elem in AUTO_WIRED or re.search(r'\b' + re.escape(elem) + r'\b', src)
                         or any(re.search(r'\b' + re.escape(elem) + r'\b', PY[q]) for q in imported)):
                continue
            if 'IsCancel="True"' in attrs and 'IsDefault' not in attrs:
                # Nút Cancel không handler: IsCancel chỉ đóng được dialog modal
                FINDINGS[p].append(("W3", "{}:{} nút Cancel chỉ dựa vào IsCancel (không đóng được window non-modal)"
                                    .format(x, line)))
                continue
            label = re.search(r'Content="([^"]*)"', attrs)
            FINDINGS[p].append(("W3", "{}:{} nút {} không có handler".format(
                x, line, '"%s"' % label.group(1) if label else (elem or "(không tên)"))))


for p in PY:
    analyse(p)

# D1 — handler chết (toàn cục)
ALL_XAML_HANDLERS = {h for x in XINFO for (_, _, h, _, _) in XINFO[x][1]}
DEAD = defaultdict(list)
for p, src in PY.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n = node.name
            if n.startswith("__") or TOKEN_COUNT[n] > 1:
                continue
            if n[:1].isupper():          # override .NET (Execute, AllowElement…)
                continue
            if HANDLER_LIKE.search(n) and n not in ALL_XAML_HANDLERS:
                FINDINGS[p].append(("D1", "handler {}() (dòng {}) không được XAML/code nào nối"
                                    .format(n, node.lineno)))
            else:
                DEAD[p].append((node.lineno, n))

# D3 — dialog mồ côi
ORPHANS = []
for p in PY:
    if os.path.dirname(p) == os.path.join(LIB, "GUI") and p.endswith("Dialog.py"):
        mod = os.path.basename(p)[:-3]
        if TOKEN_COUNT[mod] <= 1 and not re.search(r'\b' + mod + r'\b',
                                                     "\n".join(v for k, v in PY.items() if k != p)):
            ORPHANS.append(rel(p))

# ── Giữ lại có chủ đích (không fail gate) — mỗi dòng PHẢI có lý do ─────────
# (tên file, mã, chuỗi con của thông điệp) -> lý do
ALLOW = {}


def allowed(f, code, msg):
    base = os.path.basename(f)
    return next((why for (fn, c, sub), why in ALLOW.items()
                 if fn == base and c == code and sub in msg), None)


# Gán mỗi file cho tool import nó TRỰC TIẾP (script + GUI cấp 1) trước.
DIRECT = {}
for p, src in PY.items():
    if p.startswith(TAB) and p.endswith("script.py"):
        for q in {p} | gui_imports(src):
            DIRECT.setdefault(q, group_of(p))


def owner(f):
    if f in DIRECT:
        return DIRECT[f]
    for panel in sorted(TOOLS_MAP):
        for tool in sorted(TOOLS_MAP[panel]):
            if f in TOOLS_MAP[panel][tool]:
                return panel, tool
    return None


# ── Báo cáo theo nhóm ─────────────────────────────────────────────────────
BLOCKING = ("W1", "W2", "W3", "D1")
total = defaultdict(int)
allowed_n = 0
by_tool = defaultdict(list)          # (panel, tool) -> rows
dead_by_tool = defaultdict(int)
for f, items in FINDINGS.items():
    for code, msg in items:
        why = allowed(f, code, msg)
        if why:
            allowed_n += 1
            if not QUIET:
                by_tool[owner(f) or ("?", rel(f))].append(
                    "      ok  {}  {}  [{}] — {}".format(code, msg, os.path.basename(f), why))
            continue
        total[code] += 1
        by_tool[owner(f) or ("?", rel(f))].append(
            "      {}  {}  [{}]".format(code, msg, os.path.basename(f)))
for f, items in DEAD.items():
    dead_by_tool[owner(f) or ("?", "(thư viện dùng chung)")] += len(items)

print("\nWIRING AUDIT theo nhóm tool\n" + "=" * 60)
panels = sorted({k[0] for k in list(by_tool) + list(dead_by_tool)} | set(TOOLS_MAP))
for panel in panels:
    tools = sorted({t for (pn, t) in list(by_tool) + list(dead_by_tool) if pn == panel}
                   | set(TOOLS_MAP.get(panel, {})))
    lines = []
    for tool in tools:
        rows = by_tool.get((panel, tool), [])
        nd = dead_by_tool.get((panel, tool), 0)
        if QUIET and not rows:
            continue
        tail = "  — sạch" if not rows else ""
        if nd:
            tail += "  (D2: {} hàm chết)".format(nd)
        lines.append("  • {}{}".format(tool, tail))
        lines += rows
    if lines:
        print("\n[{}]".format(panel if panel != "?" else "Không thuộc tool nào trên ribbon"))
        print("\n".join(lines))

if ORPHANS and not QUIET:
    print("\nD3 dialog mồ côi: " + ", ".join(ORPHANS))
if SHOW_DEAD:
    print("\nD2 code chết (chỉ xuất hiện ở dòng def):")
    for p in sorted(DEAD):
        for ln, n in DEAD[p]:
            print("      {}:{} {}()".format(rel(p), ln, n))
n_dead = sum(len(v) for v in DEAD.values())
bad = sum(total[c] for c in BLOCKING)
print("\nWIRING: W1={W1} W2={W2} W3={W3} D1={D1} · giữ có chủ đích={a} · D2 code chết={d2} · D3 mồ côi={d3}".format(
    a=allowed_n, d2=n_dead, d3=len(ORPHANS), **{c: total[c] for c in BLOCKING}))
print("          " + ("OK" if not bad else "CÓ LỖI"))
sys.exit(1 if bad else 0)
