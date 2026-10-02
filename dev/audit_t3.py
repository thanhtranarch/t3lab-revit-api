#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T3Lab UI audit theo chuẩn T3 (CPython 3, chạy ngoài Revit).

Chuẩn duy nhất: `pyRevit UI Design System/T3LAB_UI_STANDARD.md`
              + `pyRevit UI Design System/T3Lab.Styles.xaml`
Luật migration: `docs/ui-governance/08-design-system-authority.md`

Script này THAY THẾ dev/audit_ui.py (gác chuẩn Lumina đã bỏ 2026-08-28).

── Vì sao không fail toàn bộ file legacy ──────────────────────────────────
Phần lớn XAML chưa migrate. Một gate fail hàng chục file là gate không ai chạy.
Nên script chia file làm hai loại:

  T3-DECLARED — file đã merge T3Lab.Styles.xaml hoặc đã dùng {StaticResource T3.*}.
                Bị soi ĐẦY ĐỦ. Vi phạm ⇒ exit 1.
  LEGACY      — file chưa migrate. Chỉ ĐẾM và liệt kê nợ.

Nhờ vậy gate xanh, và siết dần theo từng file được migrate: file nào khai T3
là file đó phải đúng T3.

NGOẠI LỆ — luật copyright (luật 11) áp cho MỌI file, kể cả legacy: toàn bộ
codebase vốn đã tuân thủ nên siết nó không tốn gì, mà giữ được vĩnh viễn.

Usage:
    python3 dev/audit_t3.py                 # báo cáo đầy đủ
    python3 dev/audit_t3.py --quiet         # chỉ vi phạm, exit 1 nếu có
    python3 dev/audit_t3.py --legacy        # liệt kê nợ của file legacy
    python3 dev/audit_t3.py --legacy-count  # in đúng 1 số: số file chưa migrate
    python3 dev/audit_t3.py --file <path>   # soi 1 file, luôn soi đầy đủ
                                            # (.py → chỉ luật 22: glyph trong font/bảng)

Luật 22 (icon) còn soát mọi file Python trong lib/GUI: glyph \\uXXXX phải có trong
Segoe MDL2 Assets (dev/icons/mdl2_codepoints.tsv) và trong bảng glyph chuẩn của
T3LAB_UI_STANDARD.md.
"""
import ast
import os
import re
import sys
import glob
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "T3Lab.extension", "lib", "GUI", "Tools")
STYLESHEET = os.path.join(REPO, "pyRevit UI Design System", "T3Lab.Styles.xaml")

# Thiết kế đã mở khóa toàn bộ theo Master UI Modernization Plan 2026-08-31
UI_LOCKED = set()

# Item-template XAML (root là <Border>/<DataTemplate> của một dòng list, không phải
# cửa sổ) — copyright thuộc về cửa sổ chứa nó, không lặp trên từng dòng.
COPYRIGHT_EXEMPT = set()  # CadtoFloorLayerItem.xaml was deleted 2026-10-02
COPYRIGHT_TEXT = "© Copyright by T3Lab"

# ── Luật 26 · Chrome bo góc ──────────────────────────────────────────────
# Mọi <Window WindowStyle="None"> vẽ góc giống hệt nhau: Window trong suốt, outer
# Border 1px T3.BorderStrong bo T3.R.Window. Bản cũ lệch 5 kiểu (bo 8 / 12, viền
# 1 / 1.5, T3.Border nhạt, Window nền Canvas lòi góc vuông HWND) — BatchOut lòi
# góc xám 2026-10-02. Grip resize của WPF vẽ chấm đè lên góc bo nên cũng bị cấm;
# WindowChrome.ResizeBorderThickness vẫn cho kéo giãn từ mép.
CHROME_BORDER = {
    "BorderBrush": "{StaticResource T3.BorderStrong}",
    "BorderThickness": "1",
    "CornerRadius": "{StaticResource T3.R.Window}",
    "ClipToBounds": "True",
}
# Chỉ còn bề mặt chat được giữ chrome riêng. DWGManagement (UI-locked) đã theo
# luật 26 từ 2026-10-02 theo yêu cầu của chủ repo — chỉ phần khung cửa sổ, thiết
# kế bên trong vẫn khoá.
CHROME_EXEMPT = {
    "T3LabAssistant.xaml",  # bề mặt chat theo theme Revit, đổi bo 12 ↔ 0 khi dock
}

# ── Luật 27b · Nhãn nút kế thừa font/màu của nút ──────────────────────────
# Bề mặt chat tô nút theo theme Revit ({DynamicResource T3Theme*}) — giữ riêng.
BUTTON_LABEL_EXEMPT = {"T3LabAssistant.xaml"}

# ── Luật 23 · Select-all ở header cột checkbox ───────────────────────────
# Miễn trừ: cột KHÔNG phải để chọn dòng mà là thuộc tính của chính dòng đó.
# ManaWorkset ACTIVE/OPEN/EDITABLE là trạng thái từng workset trong Revit —
# một checkbox "tất cả" ở đó nghĩa là mở/khoá toàn bộ workset, hành động khác
# hẳn "chọn tất cả" và phải do người dùng làm có ý thức trên từng dòng.
SELECTALL_EXEMPT = {
    ("ManaWorkset.xaml", "ACTIVE"),
    ("ManaWorkset.xaml", "OPEN"),
    ("ManaWorkset.xaml", "EDITABLE"),
}

# ── Luật 24 · Checkbox của dòng đọc qua string bridge ────────────────────
# Miễn trừ, kèm lý do:
#   ManaAnno      — dòng là DataRowView (DataTable): cột bool có kiểu thật nên
#                   binding thẳng đọc đúng, bridge không cần.
#   (DWGManagement đã qua bridge 2026-09-26 — chủ repo đồng ý sửa binding,
#    giao diện vẫn khoá nguyên.)
BRIDGE_EXEMPT = {"ManaAnno.xaml"}

# ── Luật 28 · DataTrigger đọc thuộc tính dòng Python qua string bridge ────
# DataTrigger Binding="{Binding field}" so PyObject với Value ("True",
# "Danger", "pending") — không bao giờ khớp, dù thuộc tính Python là str hay
# bool (Model Auditor 2026-10-02: mọi ô HEALTH cùng màu xám). Miễn trừ CHỈ cho
# file mà dòng là object .NET thật (binding thẳng mang giá trị có kiểu), kèm lý do:
#   ManaAnno — dòng là DataRowView (DataTable), như BRIDGE_EXEMPT.
PYROW_TRIGGER_EXEMPT = {"ManaAnno.xaml"}

# ── Luật 29 · Không nút nào CHỈ để đóng cửa sổ ngoài nút X ────────────────
# Nút X trên title bar (T3.WinClose, IsCancel) là control duy nhất chỉ để đóng
# cửa sổ. Nút footer/body "Close" / "Cancel" / "Done" mà handler chỉ gọi
# Close() lặp lại đúng việc của X (MCP Control 2026-10-02: "Done" ở footer =
# bấm X; dọn 31 cửa sổ cùng ngày). Bắt hai dạng, chỉ trong cửa sổ có nút X:
#   (a) Click= trùng handler của nút X, hoặc là handler đóng chung của
#       T3WPFWindow (CLOSE_HANDLERS) — kể cả nối từ Python bằng .Click +=;
#   (b) nút được nối (Click= hoặc .Click +=) vào một hàm mà thân CHỈ có một lệnh
#       <x>.Close() — mọi định nghĩa cùng tên trong các file Python nhắc tới
#       XAML đó đều phải như vậy, nên handler làm thêm việc (đặt DialogResult /
#       result, dừng tác vụ, rollback, hỏi lại...) không bao giờ bị bắt.
# Miễn trừ — khoá (file, x:Name), kèm lý do:
#   ContainsDefineValue / ContainsSetParam / SubtypeDefinerColMap — dialog nhập
#   liệu OK/Cancel do cửa sổ khác mở; Cancel là một câu trả lời đi cặp với OK
#   (cùng họ với T3Dialog / SelectFromDict / ParameterSelector).
CLOSE_HANDLERS = {"close_button_clicked", "button_close", "win_close_clicked"}
CLOSE_ONLY_EXEMPT = {
    ("ContainsDefineValue.xaml", "btn_cancel"),
    ("ContainsSetParam.xaml", "btn_cancel"),
    ("SubtypeDefinerColMap.xaml", "btnCancel"),
}
EXT_DIR = os.path.join(REPO, "T3Lab.extension")
_CLICK_WIRE_RES = (
    re.compile(r"self\.(\w+)\.Click\s*\+=\s*(?:self\.)?(\w+)"),
    re.compile(r"FindName\(\s*['\"](\w+)['\"]\s*\)\.Click\s*\+=\s*(?:self\.)?(\w+)"),
)
_CLICK_LAMBDA_CLOSE_RE = re.compile(
    r"(?:self\.(\w+)|FindName\(\s*['\"](\w+)['\"]\s*\))\.Click\s*\+=\s*"
    r"lambda[^:\n]*:\s*[\w.]*\.Close\(\s*\)")
LAMBDA_CLOSE = "lambda: Close()"
_PY_SOURCES = {}

# ── Luật 22 · ICON ───────────────────────────────────────────────────────
# UI-frozen theo CLAUDE.md: icon của 2 file này không đi theo hệ T3.Icon.*
#   DWGManagement — thiết kế riêng đã chốt
#   T3LabAssistant — chat surface, màu theo theme Revit ({DynamicResource
#                    T3Theme*}), gắn T3.Icon (brush tĩnh) sẽ hỏng dark mode
ICON_EXEMPT = {"DWGManagement.xaml", "T3LabAssistant.xaml"}
# Cùng lý do, phía Python: chat surface tự dựng glyph theo theme Revit.
ICON_EXEMPT_PY = {"T3LabAssistantDialog.py"}

# Glyph phải CÓ THẬT trong font — codepoint thiếu render ra ô vuông tofu, và WPF
# không báo lỗi gì. Danh sách lấy từ tài liệu Microsoft (code<TAB>tên).
MDL2_CODEPOINTS = os.path.join(REPO, "dev", "icons", "mdl2_codepoints.tsv")
# "Một khái niệm, một glyph": bảng glyph chuẩn đọc THẲNG từ chuẩn UI, nên bảng
# và gate không thể lệch nhau. Vùng bảng = từ dòng mở tới dòng đóng dưới đây.
STANDARD_MD = os.path.join(REPO, "pyRevit UI Design System", "T3LAB_UI_STANDARD.md")
GLYPH_TABLE_BEGIN = "Bảng glyph chuẩn"
GLYPH_TABLE_END = "Cần glyph chưa có"
GUI_DIR = os.path.join(REPO, "T3Lab.extension", "lib", "GUI")
RAIL_TILE = "{StaticResource T3.Rail.Tile}"

# Glyph = codepoint vùng Private Use của MDL2 (E000–F8FF): entity &#xE8FD;,
# escape Python , hoặc ký tự dán thẳng.
_PUA = r"(?:[Ee][0-9A-Fa-f]|[Ff][0-8])[0-9A-Fa-f]{2}"
GLYPH_ENTITY_RE = re.compile(r"&#[xX](%s);" % _PUA)
GLYPH_ESCAPE_RE = re.compile(r"\\u(%s)" % _PUA)
GLYPH_LITERAL_RE = re.compile("[-]")

_ICON_REFS = {}


def icon_refs():
    """(codepoints, table) — cả hai là dict {CODE: tên}, hoặc None nếu thiếu file.

    codepoints — mọi glyph có trong Segoe MDL2 Assets (dev/icons/mdl2_codepoints.tsv)
    table      — mọi glyph trong bảng glyph chuẩn của T3LAB_UI_STANDARD.md
    """
    if not _ICON_REFS:
        cps = None
        if os.path.exists(MDL2_CODEPOINTS):
            cps = {}
            with open(MDL2_CODEPOINTS, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip() and not line.startswith("#"):
                        code, _, name = line.rstrip("\n").partition("\t")
                        cps[code.strip().upper()] = name.strip()
        table = None
        if os.path.exists(STANDARD_MD):
            with open(STANDARD_MD, encoding="utf-8") as fh:
                md = fh.read()
            i = md.find(GLYPH_TABLE_BEGIN)
            j = md.find(GLYPH_TABLE_END, i)
            if i >= 0 and j > i:
                table = {}
                for row in md[i:j].splitlines():
                    for code, name in re.findall(r"`([0-9A-Fa-f]{4})`\s*([^|`·]*)", row):
                        table.setdefault(code.upper(), name.strip())
        _ICON_REFS["cps"], _ICON_REFS["table"] = cps, table
    return _ICON_REFS["cps"], _ICON_REFS["table"]


def glyph_issues(codes):
    """Luật 22 (b)+(c) cho một tập codepoint đã gặp trong một file."""
    cps, table = icon_refs()
    issues = []
    if cps is not None:
        tofu = sorted(c for c in codes if c not in cps)
        if tofu:
            issues.append(("P1", "glyph %s KHÔNG có trong Segoe MDL2 Assets — render ra ô "
                                 "vuông tofu (đối chiếu dev/icons/mdl2_codepoints.tsv)"
                           % ", ".join(tofu)))
    if table is not None:
        off = sorted(c for c in codes if c not in table and (cps is None or c in cps))
        if off:
            issues.append(("P3", "glyph ngoài bảng glyph chuẩn (%s) — dùng glyph đã có "
                                 "cho cùng khái niệm, hoặc thêm vào bảng ở "
                                 "T3LAB_UI_STANDARD.md + comment khối ICON"
                           % ", ".join("%s %s" % (c, (cps or {}).get(c, "?")) for c in off)))
    return issues


def audit_py(src, base):
    """Luật 22 cho Python dựng icon (glyph qua \\uXXXX hoặc ký tự dán thẳng)."""
    if base in ICON_EXEMPT_PY:
        return []
    codes = {m.upper() for m in GLYPH_ESCAPE_RE.findall(src)}
    codes |= {"%04X" % ord(ch) for ch in GLYPH_LITERAL_RE.findall(src)}
    return glyph_issues(codes)

# Ký tự Unicode hay bị dùng nhầm làm icon — render bằng Segoe UI nên lệch nét,
# lệch baseline, lệch chiều cao so với glyph MDL2 đứng cạnh. Giá trị = glyph
# MDL2 phải dùng thay thế.
FAKE_ICON_GLYPHS = {
    "&#X2212;": "→ &#xE921; Minimize",
    "&#X25A2;": "→ &#xE922; Maximize",
    "&#X2715;": "→ &#xE8BB; Close",
    "&#X2716;": "→ &#xE8BB; Close",
    "&#X2713;": "→ &#xE73E; CheckMark",
    "&#X2714;": "→ &#xE73E; CheckMark",
    "&#X26A0;": "→ &#xE7BA; Warning",
    "&#X25B6;": "→ &#xE768; Play",
    "&#X25C0;": "→ &#xE76B; ChevronLeft",
    "&#X25BC;": "→ &#xE70D; ChevronDown",
    "&#X25B2;": "→ &#xE70E; ChevronUp",
    "&#X2728;": "→ &#xEA80; Insight (icon AI chuẩn)",
    "&#X23F3;": "→ bỏ — nút AI đang chạy thì ai_busy() khoá nút, không đổi chữ",
}

# Marker của khối stylesheet được nhúng bởi dev/sync_t3_styles.py.
SYNC_BEGIN = "<!-- ═══ T3 STYLES — SINH TỰ ĐỘNG"
SYNC_END = "<!-- ═══ HẾT T3 STYLES ═══ -->"

# ── Vi phạm ĐANG HOÃN vì Design System chưa có câu trả lời ───────────────
# KHÔNG phải chỗ để nới luật. Mỗi mục phải gắn với một DESIGN SYSTEM GAP đang
# chờ user duyệt trong docs/ui-governance/PRIORITY_QUEUE.md, và luôn được IN RA
# mỗi lần chạy — hoãn thì được, giấu thì không.
#   {"File.xaml": ("GAP #N — lý do", ("chuỗi khớp thông báo", ...))}
# Trống từ 2026-08-28: GAP #4 đã đóng bằng 8 component chrome trong stylesheet.
PENDING_GAP = {}

# File render NHIỀU cửa sổ mẫu trong một file. Luật "đúng một primary" áp cho MỘT
# cửa sổ, nên ở đây nó không áp dụng — mỗi card là một cửa sổ riêng. Mọi luật khác
# vẫn soi đầy đủ.
MULTI_WINDOW = set()


# ── Chuẩn T3 (T3LAB_UI_STANDARD.md) ───────────────────────────────────────
FONTS_OK = {"Segoe UI", "Consolas",
            # font glyph cho chrome cửa sổ (min/max/close), không phải font chữ
            "Segoe MDL2 Assets"}
SIZES_OK = {"19", "15", "13", "11.5", "11", "12.5"}
SPACING_OK = {0, 4, 8, 12, 16, 24, 32}
RADIUS_OK = {0, 2, 4, 6, 8, 10, 12, 14, 16, 18}
RADIUS_RESOURCES = {
    "{StaticResource T3.R.Window}",
    "{StaticResource T3.R.Control}",
    "{StaticResource T3.R.Pill}",
}
WINDOW_ATTRS = ("UseLayoutRounding", "SnapsToDevicePixels", "MinWidth", "MinHeight")

# Palette của các hệ đã bị bỏ — dấu hiệu nhận dạng file legacy.
DEAD_PALETTE = {
    "#083D56": "Kinetix navy",
    "#0F766E": "Terra teal", "#115E59": "Terra teal", "#0B4F4A": "Terra teal",
    "#F6F8F8": "Terra", "#E6EDEC": "Terra", "#D9EBE8": "Terra",
    "#DDE5E7": "Terra", "#C7D2D4": "Terra", "#15803D": "Terra",
    "#166534": "Terra", "#AEBFBC": "Terra",
    "#0F172A": "Lumina ink", "#3B82F6": "Lumina accent", "#E2E8F0": "Lumina border",
    "#CBD5E1": "Lumina input border", "#64748B": "Lumina muted",
    "#94A3B8": "Lumina faint", "#F8FAFC": "Lumina surface",
    "#F1F5F9": "Lumina surface hover", "#10B981": "Lumina success",
    "#EF4444": "Lumina danger", "#1E293B": "Lumina primary hover",
}
DEAD_FONTS = ("Hanken Grotesk", "Inter", "Manrope")

HEX_RE = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

LIST_TAGS = {"DataGrid", "ListBox", "ListView"}

# Mọi chữ hiển thị cho người dùng phải là TIẾNG ANH (luật 12).
# Dò bằng dấu tiếng Việt — rẻ và không nhầm với tiếng Anh.
VN_CHARS = re.compile(
    "[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
    "ùúủũụưừứửữựỳýỷỹỵđÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊ"
    "ÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ]")
UI_TEXT_ATTRS = ("Text", "Content", "Header", "ToolTip", "Watermark", "PlaceholderText")


def local(tag):
    """Bỏ namespace khỏi tên tag/attribute."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def stylesheet_keys():
    if not os.path.exists(STYLESHEET):
        return None
    with open(STYLESHEET, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    return set(re.findall(r'x:Key="([^"]+)"', src))


def spacing_bad(value):
    """Trả về danh sách số không chia hết cho 4 trong Margin/Padding."""
    bad = []
    for n in NUM_RE.findall(value or ""):
        try:
            f = float(n)
        except ValueError:
            continue
        if f != int(f) or abs(int(f)) not in SPACING_OK:
            bad.append(n)
    return bad


_SOURCE_RE = re.compile(r"(?:^|[\s,{])(?:ElementName|RelativeSource|Source)\s*=")


def pyrow_trigger_bindings(root):
    """Binding text of every DataTrigger / MultiDataTrigger Condition that reads
    the DataContext directly (no ElementName / RelativeSource / Source) — luật 28.

    Both the attribute form (Binding="{Binding f}") and the element form
    (<DataTrigger.Binding><Binding Path="f"/></DataTrigger.Binding>) count.
    """
    found = []
    for el in root.iter():
        tag = local(el.tag)
        if tag not in ("DataTrigger", "Condition"):
            continue
        b = " ".join(el.attrib.get("Binding", "").split())
        if b:
            if b.startswith("{Binding") and not _SOURCE_RE.search(b):
                found.append(b)
            continue
        for prop in el:
            if local(prop.tag) not in ("DataTrigger.Binding", "Condition.Binding"):
                continue
            for bind in prop:
                if local(bind.tag) != "Binding":
                    continue
                attrs = {local(k) for k in bind.attrib}
                attrs |= {local(c.tag).split(".")[-1] for c in bind}   # <Binding.Source>
                if not attrs & {"ElementName", "RelativeSource", "Source"}:
                    found.append("<Binding Path=\"%s\"/>" % bind.attrib.get("Path", ""))
    return found


def _py_sources():
    """{path: source} của mọi file Python trong T3Lab.extension (đọc một lần)."""
    if not _PY_SOURCES:
        for dirpath, _dirs, names in os.walk(EXT_DIR):
            for n in names:
                if n.endswith(".py"):
                    p = os.path.join(dirpath, n)
                    try:
                        with open(p, encoding="utf-8", errors="replace") as fh:
                            _PY_SOURCES[p] = fh.read()
                    except OSError:
                        pass
    return _PY_SOURCES


def _close_only_body(fn):
    """Thân hàm chỉ có đúng một lệnh <x>.Close() (bỏ qua docstring / pass)."""
    body = [s for s in fn.body
            if not isinstance(s, ast.Pass)
            and not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                     and isinstance(s.value.value, str))]
    if len(body) != 1 or not isinstance(body[0], ast.Expr):
        return False
    call = body[0].value
    return (isinstance(call, ast.Call) and not call.args and not call.keywords
            and isinstance(call.func, ast.Attribute) and call.func.attr == "Close")


def click_wiring(base):
    """Luật 29 — (handler chỉ-Close, {x:Name: {handler}}) từ mọi file Python
    nhắc tới tên file XAML `base`. Handler chỉ-Close = MỌI định nghĩa cùng tên
    trong các file đó đều chỉ gọi Close()."""
    flags = {}
    wired = {}
    for src in _py_sources().values():
        if base not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                flags.setdefault(node.name, []).append(_close_only_body(node))
        for rx in _CLICK_WIRE_RES:
            for m in rx.finditer(src):
                wired.setdefault(m.group(1), set()).add(m.group(2))
        for m in _CLICK_LAMBDA_CLOSE_RE.finditer(src):
            wired.setdefault(m.group(1) or m.group(2), set()).add(LAMBDA_CLOSE)
    close_only = {name for name, fl in flags.items() if all(fl)} | {LAMBDA_CLOSE}
    return close_only, wired


def close_only_buttons(root, base):
    """Luật 29 — [(nhãn, handlers)] của nút ngoài nút X chỉ để đóng cửa sổ."""
    def attrs_of(el):
        return {local(k): v for k, v in el.attrib.items()}

    buttons = [el for el in root.iter() if local(el.tag) == "Button"]
    x_buttons = [el for el in buttons if "T3.WinClose" in el.attrib.get("Style", "")]
    if not x_buttons:
        return []       # không có nút X → nút Close/Cancel là đường đóng duy nhất
    x_handlers = set(CLOSE_HANDLERS)
    x_handlers |= {attrs_of(el).get("Click") for el in x_buttons} - {None}
    close_only, wired = click_wiring(base)
    found = []
    for el in buttons:
        a = attrs_of(el)
        style = a.get("Style", "")
        if "T3.WinClose" in style or "T3.WinCtrl" in style:
            continue
        name = a.get("Name", "")
        if (base, name) in CLOSE_ONLY_EXEMPT:
            continue
        handlers = ({a["Click"]} if a.get("Click") else set()) | wired.get(name, set())
        hit = sorted(h for h in handlers if h in x_handlers or h in close_only)
        if hit:
            label = a.get("Content") or name or "?"
            found.append(("%s%s" % (label, " / x:Name=%s" % name if name and name != label else ""),
                          ", ".join(hit)))
    return found


def audit(src, base, keys):
    """Trả về (issues, declared_t3, legacy_debt, must).

    issues       — [(severity, message)] vi phạm chuẩn T3, chỉ tính cho file đã khai T3
    declared_t3  — file đã khai dùng T3 hay chưa
    legacy_debt  — [str] nợ migration (chỉ để đếm, không phải vi phạm)
    must         — [(severity, message)] vi phạm luật áp cho MỌI file, kể cả legacy
    """
    issues = []
    must = []
    debt = []
    src_full = src

    used_keys = set(re.findall(r"\{StaticResource (T3\.[^}]+)\}", src))
    merges_sheet = SYNC_BEGIN in src
    declared = merges_sheet or bool(used_keys)

    # Khối T3 STYLES là Design System được nhúng vào, KHÔNG phải code của tool.
    # Mọi luật dưới đây soi markup do người viết tool tạo ra, nên cắt nó đi trước.
    # (Nhúng thay vì MergedDictionaries vì pyRevit nạp XAML không có base URI —
    #  xem docstring của dev/sync_t3_styles.py.)
    if merges_sheet and SYNC_END in src:
        i = src.index(SYNC_BEGIN)
        j = src.index(SYNC_END, i) + len(SYNC_END)
        src = src[:i] + src[j:]

    # ── Nợ migration (tính cho mọi file, kể cả file đã khai T3) ──────────
    for f in DEAD_FONTS:
        if re.search(r'FontFamily="[^"]*%s' % re.escape(f), src):
            debt.append("font đã bỏ: %s" % f)
    dead_found = {h: n for h, n in DEAD_PALETTE.items()
                  if re.search(re.escape(h), src, re.I)}
    if dead_found:
        debt.append("palette đã bỏ x%d (%s...)" % (
            len(dead_found), ", ".join(sorted(dead_found)[:3])))
    if "T3LAB SHARED STYLES" in src:
        debt.append("còn block shared styles Lumina")
    if "T3Theme" in src:
        debt.append("còn token Revit-native T3Theme*")
    if not merges_sheet:
        debt.append("chưa nhúng stylesheet T3")

    # ── x:Key trùng: WPF ném "Item has already been added" NGAY lúc parse ──
    keys = re.findall(r'<[A-Za-z:]+[^>]*?x:Key="([^"]+)"', src_full)
    dup = sorted({k for k in keys if keys.count(k) > 1})
    for k in dup:
        must.append(("P0", 'x:Key="%s" bị định nghĩa %d lần — WPF crash lúc parse'
                     % (k, keys.count(k))))

    # ── Copyright: BẮT BUỘC ở MỌI file, kể cả file chưa migrate ─────────
    # Luật 11 của T3LAB_UI_STANDARD.md. Đây là luật duy nhất áp cho cả legacy,
    # vì toàn bộ codebase vốn đã tuân thủ — siết nó không tốn gì mà giữ được.
    if base not in COPYRIGHT_EXEMPT:
        # Style T3.Copyright tự cấp Text, nên file dùng style sẽ không
        # chứa chuỗi literal — đếm cả hai dạng.
        n_cr = src.count(COPYRIGHT_TEXT) + src.count("{StaticResource T3.Copyright}")
        if "&#169;" in src:
            must.append(("P2", "copyright dùng &#169; — phải là ký tự © thật"))
        if n_cr == 0:
            must.append(("P2", "THIẾU dòng copyright \"%s\" ở footer" % COPYRIGHT_TEXT))
        elif n_cr > 1:
            must.append(("P2", "copyright lặp x%d — chuẩn cho đúng một dòng" % n_cr))
        elif declared and "{StaticResource T3.Copyright}" not in src:
            must.append(("P2", "copyright phải dùng Style=\"{StaticResource T3.Copyright}\""))

    # ── Parse ────────────────────────────────────────────────────────────
    # Dot-notation bắt bằng regex TRƯỚC khi parse: ElementTree nuốt được nó,
    # nhưng WPF thì crash EMPTYPROPERTYELEMENT lúc mở tool.
    if re.search(r"<Grid\.(Row|Column)Definition\b", src):
        issues.append(("P0", "dot-notation <Grid.Row/ColumnDefinition> "
                             "— crash EMPTYPROPERTYELEMENT"))
    try:
        root = ET.fromstring(src)
    except ET.ParseError as exc:
        issues.append(("P0", "XAML không parse được: %s" % exc))
        return issues, declared, debt, must

    parent = {c: p for p in root.iter() for c in p}

    def ancestors(el):
        while el in parent:
            el = parent[el]
            yield el

    is_window = local(root.tag) == "Window"
    n_primary = 0
    has_default = has_cancel = False
    n_x = 0             # nút X title bar (T3.WinClose)
    x_cancel = False    # ... và nút X mang IsCancel="True"
    has_list = has_empty = False
    n_local_style = 0

    for el in root.iter():
        tag = local(el.tag)
        attrs = {local(k): v for k, v in el.attrib.items()}

        # Style CÓ x:Key trong file tool = một component mới đặt sai chỗ — nó phải
        # nằm trong T3Lab.Styles.xaml để tool khác dùng lại được.
        # Style KHÔNG có x:Key là implicit override, chỉ có hiệu lực trong đúng
        # container chứa nó (vd ItemContainerStyle của một ListView). Stock WPF
        # không có cách nào khác để làm việc đó, nên nó hợp lệ — màu/size bên
        # trong vẫn bị soi bởi luật hex/font/size như mọi chỗ khác.
        if tag == "Style" and "TargetType" in attrs and "Key" in attrs:
            n_local_style += 1

        for name, val in attrs.items():
            if name == "FontFamily" and val not in FONTS_OK:
                issues.append(("P2", "<%s> FontFamily=\"%s\" — chỉ Segoe UI / Consolas"
                               % (tag, val)))
            elif name == "FontSize" and val not in SIZES_OK:
                issues.append(("P2", "<%s> FontSize=\"%s\" — ngoài thang 7 size"
                               % (tag, val)))
            elif name in ("Margin", "Padding"):
                bad = spacing_bad(val)
                if bad:
                    issues.append(("P3", "<%s> %s=\"%s\" — %s không thuộc 4/8/12/16/24/32"
                                   % (tag, name, val, "/".join(bad))))
            elif name == "CornerRadius":
                if val.strip() in RADIUS_RESOURCES:
                    continue
                for n in NUM_RE.findall(val):
                    if float(n) not in RADIUS_OK:
                        issues.append(("P3", "<%s> CornerRadius=\"%s\" — chỉ 0/2/4/6/8/10/12"
                                       % (tag, val)))
                        break
            elif name == "Height" and tag == "TextBlock":
                issues.append(("P3", "<TextBlock Height=\"%s\"> — không set Height cho TextBlock"
                               % val))
            elif name == "IsDefault" and val == "True":
                has_default = True
            elif name == "IsCancel" and val == "True":
                has_cancel = True

            # Hex cứng ở bất kỳ attribute nào
            if HEX_RE.match(val.strip()):
                issues.append(("P2", "<%s %s=\"%s\"> — hex cứng, phải dùng {StaticResource T3.*}"
                               % (tag, name, val)))

        for name in UI_TEXT_ATTRS:
            val = attrs.get(name)
            if val and VN_CHARS.search(val):
                must.append(("P2", "<%s %s=\"%s\"> — chữ hiển thị phải là TIẾNG ANH"
                             % (tag, name, val[:44] + ("…" if len(val) > 44 else ""))))

        if tag.endswith(".Effect"):
            issues.append(("P2", "<%s> — chuẩn T3 cấm mọi Effect" % tag))

        if attrs.get("Style", "").strip() == "{StaticResource T3.Button.Primary}":
            n_primary += 1
        if tag == "Button" and "T3.WinClose" in attrs.get("Style", ""):
            n_x += 1
            x_cancel = x_cancel or attrs.get("IsCancel") == "True"

        if tag in LIST_TAGS:
            has_list = True
            # T3.DataGrid / T3.ListBox / T3.LogBox đã set sẵn HorizontalScrollBarVisibility
            # và virtualization bằng Setter — dùng style đó là đã tuân thủ, đừng bắt
            # khai lại attribute trên element.
            styled = attrs.get("Style", "").strip() in (
                "{StaticResource T3.DataGrid}",
                "{StaticResource T3.ListBox}",
                "{StaticResource T3.LogBox}",
                "{StaticResource T3.ListView}",
            )
            if not styled:
                if attrs.get("ScrollViewer.HorizontalScrollBarVisibility") != "Disabled" \
                        and attrs.get("HorizontalScrollBarVisibility") != "Disabled":
                    issues.append(("P2", "<%s> thiếu HorizontalScrollBarVisibility=\"Disabled\"" % tag))
                if tag == "DataGrid" and attrs.get("EnableRowVirtualization") != "True":
                    issues.append(("P1", "<DataGrid> thiếu EnableRowVirtualization=\"True\""))
                if tag in ("ListBox", "ListView") \
                        and attrs.get("VirtualizingPanel.IsVirtualizing") != "True":
                    issues.append(("P1", "<%s> thiếu VirtualizingPanel.IsVirtualizing=\"True\"" % tag))
            # Bọc trong ScrollViewer chỉ chết khi list được đo với chiều cao VÔ HẠN.
            # Khai Height/MaxHeight là đã chặn — virtualization vẫn chạy. Không khai
            # mới là lỗi: WPF realize toàn bộ row, model lớn là treo Revit.
            bounded = "Height" in attrs or "MaxHeight" in attrs
            if not bounded:
                for anc in ancestors(el):
                    if local(anc.tag) == "ScrollViewer":
                        issues.append(("P0", "<%s> bị bọc trong <ScrollViewer> mà không khai "
                                             "Height/MaxHeight — đo với chiều cao vô hạn, mất "
                                             "virtualization, treo Revit trên model lớn" % tag))
                        break
            stars = [c for c in el.iter()
                     if local(c.tag).endswith("Column")
                     and c.attrib.get("Width", "").strip() == "*"]
            if len(stars) > 1:
                issues.append(("P2", "<%s> có %d cột Width=\"*\" — chuẩn cho đúng một"
                               % (tag, len(stars))))

        if attrs.get("Style", "").strip() == "{StaticResource T3.Empty}":
            has_empty = True

    # ── Luật cấp file ────────────────────────────────────────────────────
    for k in sorted(used_keys):
        if keys is not None and k not in keys:
            issues.append(("P1", "{StaticResource %s} không có trong T3Lab.Styles.xaml" % k))

    if n_local_style:
        issues.append(("P2", "%d <Style x:Key> định nghĩa trong file tool — component "
                             "mới phải thêm vào T3Lab.Styles.xaml" % n_local_style))

    # ── Luật 23 · Cột checkbox chọn dòng phải có select-all ở header ──────
    # Bảng nào cho tick từng dòng thì phải cho tick tất cả — không bắt người
    # dùng click 200 lần. Header của cột đó phải chứa một <CheckBox>.
    for col in root.iter():
        ctag = local(col.tag)
        if ctag not in ("DataGridTemplateColumn", "DataGridCheckBoxColumn",
                        "GridViewColumn"):
            continue
        cattrs = {local(k): v for k, v in col.attrib.items()}
        hdr_text = cattrs.get("Header", "")
        if (base, hdr_text) in SELECTALL_EXEMPT:
            continue
        # cột này có bind checkbox vào một property của dòng không?
        bound = None
        if ctag == "DataGridCheckBoxColumn":
            bound = cattrs.get("Binding")
        for e in col.iter():
            if local(e.tag) != "CheckBox":
                continue
            if any(local(a.tag).endswith("CellTemplate") for a in ancestors(e)):
                bound = bound or {local(k): v for k, v in e.attrib.items()}.get("IsChecked")
        if not bound or "{Binding" not in (bound or ""):
            continue
        # header có checkbox chưa?
        has_hdr_cb = any(
            local(e.tag) == "CheckBox"
            and any(local(a.tag).endswith(".Header")
                    or local(a.tag).endswith("HeaderTemplate")
                    for a in ancestors(e))
            for e in col.iter())
        if not has_hdr_cb:
            prop = re.search(r"\{Binding\s+([\w.]+)", bound)
            issues.append(("P2", "cột checkbox %s(%s) thiếu checkbox select-all ở "
                                 "header — thêm <%s.Header><CheckBox .../>"
                           % (ctag, prop.group(1) if prop else "?", ctag)))

    # ── Luật 24 · Checkbox của dòng phải đọc qua string bridge ─────────────
    # pythonnet đưa thuộc tính Python cho WPF dưới dạng PyObject: đổi được sang
    # string (cột chữ hiện đúng) nhưng KHÔNG sang bool?, nên IsChecked bind
    # thẳng vào thuộc tính dòng luôn đọc là unchecked — tick biến mất khi
    # Items.Refresh(), select-all không tick được dòng nào (BatchOut 2026-09-26).
    # DataGridCheckBoxColumn dính y hệt. Mẫu đúng: WPF_Base.py → "Checkbox bridge".
    if base not in BRIDGE_EXEMPT:
        for el in root.iter():
            tag = local(el.tag)
            attrs = {local(k): v for k, v in el.attrib.items()}
            if tag == "DataGridCheckBoxColumn" and "{Binding" in attrs.get("Binding", ""):
                issues.append(("P1", "DataGridCheckBoxColumn bind %s — PyObject không đổi "
                                     "được sang bool; dùng DataGridTemplateColumn + "
                                     "checkbox bridge (WPF_Base.py)" % attrs["Binding"]))
            elif tag == "CheckBox":
                b = attrs.get("IsChecked", "")
                if (b.startswith("{Binding") and "ElementName" not in b
                        and "RelativeSource" not in b
                        and any(local(a.tag) in ("DataTemplate", "HierarchicalDataTemplate")
                                for a in ancestors(el))):
                    issues.append(("P1", "CheckBox IsChecked=\"%s\" trong template dòng — "
                                         "luôn đọc unchecked; đọc qua checkbox bridge "
                                         "(WPF_Base.py)" % " ".join(b.split())))

    # ── Luật 25 · Visibility phải nhận được giá trị Visibility thật ─────────
    # Không có converter nào trong XAML của tool, nên:
    #  (a) bool (HasItems, IsChecked, ...) bind thẳng vào Visibility → lỗi
    #      binding → Visibility giữ mặc định Visible: empty state nằm đè lên
    #      các dòng (PropertyLine + 7 tool, 2026-09-26). Dùng DataTrigger.
    #  (b) thuộc tính Python của dòng ("Visible"/"Collapsed") là PyObject,
    #      không đổi được sang Visibility — đọc qua string bridge như luật 24.
    bool_paths = ("HasItems", "IsChecked", "IsEnabled", "IsSelected", "IsFocused",
                  "IsMouseOver", "IsOpen", "IsExpanded", "IsVisible", "IsKeyboardFocused")
    for el in root.iter():
        v = {local(k): val for k, val in el.attrib.items()}.get("Visibility", "")
        if not v.startswith("{Binding"):
            continue
        flat = " ".join(v.split())
        m = re.match(r"\{Binding\s+(?:Path=)?([\w.]+)", flat)
        path = m.group(1) if m else ""
        has_conv = "Converter=" in flat and "Converter={x:Null}" not in flat
        if "Converter={x:Null}" in flat or (path in bool_paths and not has_conv):
            issues.append(("P1", "Visibility=\"%s\" — bool không tự đổi sang Visibility; "
                                 "dùng DataTrigger (Setter Visibility)" % flat))
        elif ("ElementName" not in flat and "RelativeSource" not in flat and not has_conv
              and any(local(a.tag) in ("DataTemplate", "HierarchicalDataTemplate")
                      for a in ancestors(el))):
            issues.append(("P1", "Visibility=\"%s\" trong template dòng — thuộc tính Python "
                                 "không đổi được sang Visibility; đọc qua string bridge" % flat))

    # ── Luật 28 · DataTrigger không bind thẳng thuộc tính của dòng Python ───
    # pythonnet đưa thuộc tính Python cho WPF dưới dạng PyObject. Thuộc tính
    # kiểu string (Text, ...) đổi được — nên cột chữ hiện đúng — nhưng
    # DataTrigger so CHÍNH PyObject với Value: "Danger" / "True" / "pending"
    # không bao giờ khớp và trigger im lặng không nổ (Model Auditor: mọi ô
    # HEALTH xám; ô vàng pending-edit của Sheet/View Manager không hiện).
    # Đúng: đọc qua string bridge —
    #   template dòng : <TextBlock x:Name="f_text" Text="{Binding f}" Visibility="Collapsed"/>
    #                   + DataTrigger Binding="{Binding Text, ElementName=f_text}"
    #   CellStyle/ElementStyle (không có template để đặt TextBlock):
    #                   <Setter Property="AutomationProperties.ItemStatus" Value="{Binding f}"/>
    #                   + <Trigger Property="AutomationProperties.ItemStatus" Value="True">
    # Binding có ElementName / RelativeSource / Source đọc thuộc tính WPF → bỏ qua.
    if base not in PYROW_TRIGGER_EXEMPT:
        for b in pyrow_trigger_bindings(root):
            issues.append(("P1", "DataTrigger Binding=\"%s\" so PyObject của dòng Python với "
                                 "Value — không bao giờ khớp; đọc qua string bridge "
                                 "(TextBlock ẩn + {Binding Text, ElementName=…}, hoặc Setter "
                                 "AutomationProperties.ItemStatus + Trigger trong CellStyle)"
                           % b))

    # ── Luật 27 · Nút đồng bộ: footer, nhãn, thứ tự (thêm 2026-10-02) ───────
    # (a) Nút trong T3.FooterBar giữ kích thước của style (cao T3.H.Action 30,
    #     padding mặc định): 22 tool từng tự đặt Height 26/28 + Padding riêng,
    #     nên Pause/Stop/Cancel/Apply cạnh nhau cao thấp khác nhau.
    # (b) Nhãn chữ trong nút Primary/Secondary/Danger kế thừa font + màu của
    #     nút — không Style T3.Caption, không Foreground/FontSize riêng: Stop
    #     từng hiện chữ xám 11.5 trên nền đỏ (SheetGen 2026-10-02). Nút Ghost
    #     dạng link nhỏ trong form thì được phép.
    # (c) Trong footer, nút Primary là nút T3 cuối cùng (ngoài cùng phải).
    for foot in root.iter():
        if local(foot.tag) != "Border" or "T3.FooterBar" not in foot.attrib.get("Style", ""):
            continue
        styles = []
        for el in foot.iter():
            if local(el.tag) != "Button":
                continue
            attrs = {local(k): v for k, v in el.attrib.items()}
            st = re.search(r"T3\.Button\.(\w+)", attrs.get("Style", ""))
            if not st:
                continue
            styles.append(st.group(1))
            bad = [a for a in ("Height", "Padding") if a in attrs]
            if bad:
                issues.append(("P2", "nút footer %s tự đặt %s — bỏ để giữ chiều cao/padding "
                                     "chuẩn của style (luật 27a)"
                               % (attrs.get("Name", attrs.get("Content", "?")), "/".join(bad))))
        if "Primary" in styles and styles[-1] != "Primary":
            issues.append(("P2", "footer: nút Primary phải ở ngoài cùng phải — thứ tự hiện "
                                 "tại %s (luật 27c)" % " → ".join(styles)))
    for btn in (root.iter() if base not in BUTTON_LABEL_EXEMPT else ()):
        if local(btn.tag) != "Button":
            continue
        st = re.search(r"T3\.Button\.(Primary|Secondary|Danger)", btn.attrib.get("Style", ""))
        if not st:
            continue
        for tb in btn.iter():
            if local(tb.tag) != "TextBlock" or tb is btn:
                continue
            a = {local(k): v for k, v in tb.attrib.items()}
            if "T3.Icon" in a.get("Style", ""):
                continue
            if ("T3.Caption" in a.get("Style", "") or "Foreground" in a or "FontSize" in a):
                issues.append(("P2", "nhãn \"%s\" trong nút %s tự đặt style/màu/cỡ chữ — để "
                                     "nhãn kế thừa font và màu của nút (luật 27b)"
                               % (a.get("Text", "?"), st.group(1))))

    # ── Luật 22 · ICON — một bộ icon cho toàn extension ───────────────────
    # (a) Font icon duy nhất là Segoe MDL2 Assets, và LUÔN qua style T3.Icon.*
    #     — không hardcode FontFamily/FontSize tại chỗ dùng.
    n_inline_mdl2 = len(re.findall(r'FontFamily="Segoe MDL2 Assets"', src))
    if n_inline_mdl2 and base not in ICON_EXEMPT:
        issues.append(("P2", '%d icon khai FontFamily="Segoe MDL2 Assets" tại chỗ — '
                             'dùng Style="{StaticResource T3.Icon}" (hoặc .Muted/.Field/'
                             '.Lead/.Lg)' % n_inline_mdl2))
    # (b) Ký tự Unicode thường làm icon: render bằng Segoe UI nên lệch nét và
    #     lệch baseline so với glyph MDL2 đứng cạnh.
    # Bắt cả dạng entity (&#x2713;) lẫn ký tự dán thẳng (✓ ✨) trong giá trị thuộc tính.
    literal = {"&#X%04X;" % ord(ch) for ch in re.findall(r'="[^"]*"', src) for ch in ch}
    bad_glyphs = sorted({m.upper() for m in re.findall(r"&#x[0-9A-Fa-f]{4};", src)
                         if m.upper() in FAKE_ICON_GLYPHS} |
                        {g for g in literal if g in FAKE_ICON_GLYPHS})
    if bad_glyphs and base not in ICON_EXEMPT:
        issues.append(("P2", "icon dùng ký tự Unicode thường (%s) — thay bằng glyph "
                             "Segoe MDL2 Assets, xem bảng glyph trong T3Lab.Styles.xaml"
                       % ", ".join("%s %s" % (g, FAKE_ICON_GLYPHS[g.upper()])
                                   for g in bad_glyphs)))
    if base not in ICON_EXEMPT:
        # (c) Ô rail tự vẽ <Path>: mỗi tool một nét (1.5/1.6/1.8), một hệ hình
        #     riêng — đúng thứ "một bộ icon" sinh ra để chống. Rail dùng glyph.
        rail_paths = [
            {local(k): v for k, v in el.attrib.items()}.get("Name", "?")
            for el in root.iter()
            if el.attrib.get("Style", "").strip() == RAIL_TILE
            and any(local(c.tag) == "Path" for c in el.iter() if c is not el)]
        if rail_paths:
            issues.append(("P2", "%d ô T3.Rail.Tile tự vẽ <Path> (%s) — rail icon phải là "
                                 "glyph T3.Icon.Rail: <TextBlock Text=\"&#x…;\" "
                                 "Style=\"{StaticResource T3.Icon.Rail}\"/>"
                           % (len(rail_paths), ", ".join(rail_paths))))
        # (d) Glyph có thật trong font (P1) và nằm trong bảng glyph chuẩn (P3).
        codes = {m.upper() for m in GLYPH_ENTITY_RE.findall(src)}
        codes |= {"%04X" % ord(ch) for ch in GLYPH_LITERAL_RE.findall(src)}
        issues.extend(glyph_issues(codes))

    if n_primary > 1 and base not in MULTI_WINDOW:
        issues.append(("P2", "%d nút T3.Button.Primary — chuẩn cho đúng một" % n_primary))
    if has_list and not has_empty:
        issues.append(("P2", "có list/grid nhưng không thấy empty state (T3.Empty)"))

    if is_window:
        wattrs = {local(k): v for k, v in root.attrib.items()}
        missing = [a for a in WINDOW_ATTRS if a not in wattrs]
        if "TextFormattingMode" not in " ".join(root.attrib.keys()):
            missing.append("TextOptions.TextFormattingMode")
        if missing:
            issues.append(("P2", "<Window> thiếu: %s" % ", ".join(missing)))
        # Luật 9 (sửa 2026-10-02): IsDefault chỉ bắt buộc khi cửa sổ có hành động
        # (một T3.Button.Primary) — cửa sổ chỉ hiện trạng thái / cài đặt áp dụng
        # ngay (MCP Control) không có nút nào để Enter bấm. IsCancel nằm trên nút X:
        # Esc và nút X là một đường đóng duy nhất (luật 29).
        if n_primary and not has_default:
            issues.append(("P2", "có nút T3.Button.Primary nhưng không nút nào "
                                 "IsDefault=\"True\" (luật 9)"))
        if n_x:
            if not x_cancel:
                issues.append(("P2", "nút X (T3.WinClose) thiếu IsCancel=\"True\" — Esc phải "
                                     "đóng cửa sổ qua chính nút X (luật 9)"))
        elif not has_cancel:
            issues.append(("P2", "không có nút IsCancel=\"True\""))
        for label, handlers in close_only_buttons(root, base):
            issues.append(("P2", "nút \"%s\" (%s) chỉ đóng cửa sổ — trùng việc của nút X "
                                 "(T3.WinClose); bỏ nút, Esc đã đi qua IsCancel của X. Nút "
                                 "trả lời của dialog OK/Cancel thì khai CLOSE_ONLY_EXEMPT "
                                 "(luật 29)" % (label, handlers)))
        if not merges_sheet:
            issues.append(("P1", "chưa nhúng stylesheet T3 — chạy "
                             "`python3 dev/sync_t3_styles.py`"))
        if wattrs.get("WindowStyle") == "None" and base not in CHROME_EXEMPT:
            issues.extend(chrome_issues(root, wattrs))

    return issues, declared, debt, must


def chrome_issues(root, wattrs):
    """Luật 26: Window trong suốt + outer Border chuẩn, không grip ở góc."""
    want = []
    if wattrs.get("AllowsTransparency") != "True":
        want.append('<Window AllowsTransparency="True">')
    if wattrs.get("Background") != "Transparent":
        want.append('<Window Background="Transparent">')
    if wattrs.get("ResizeMode") == "CanResizeWithGrip":
        want.append('<Window ResizeMode="CanResize"> (grip vẽ đè góc bo)')
    content = [c for c in root if "." not in local(c.tag)]
    if len(content) != 1 or local(content[0].tag) != "Border":
        want.append("nội dung Window là đúng MỘT outer <Border>")
    else:
        oattrs = {local(k): v for k, v in content[0].attrib.items()}
        want += ['outer <Border %s="%s">' % (k, v)
                 for k, v in CHROME_BORDER.items() if oattrs.get(k) != v]
    if not want:
        return []
    return [("P1", "luật 26 chrome bo góc lệch chuẩn — cần " + ", ".join(want))]


def main():
    argv = sys.argv[1:]
    quiet = "--quiet" in argv
    show_legacy = "--legacy" in argv
    count_only = "--legacy-count" in argv

    keys = stylesheet_keys()
    if keys is None and not count_only:
        print("CẢNH BÁO: không tìm thấy %s — bỏ qua kiểm tra key." % STYLESHEET)

    cps, table = icon_refs()
    if not count_only:
        if cps is None:
            print("CẢNH BÁO: không thấy %s — bỏ qua kiểm tra glyph có trong font."
                  % MDL2_CODEPOINTS)
        if table is None:
            print("CẢNH BÁO: không đọc được bảng glyph trong %s (vùng \"%s\" … \"%s\") "
                  "— bỏ qua kiểm tra glyph ngoài bảng."
                  % (STANDARD_MD, GLYPH_TABLE_BEGIN, GLYPH_TABLE_END))

    py_files = []
    if "--file" in argv:
        files = [argv[argv.index("--file") + 1]]
        forced = True
        if files[0].endswith(".py"):
            py_files, files = files, []
    else:
        files = sorted(glob.glob(os.path.join(TOOLS, "*.xaml")))
        forced = False
        py_files = sorted(glob.glob(os.path.join(GUI_DIR, "**", "*.py"), recursive=True))

    n_t3 = n_legacy = n_locked = n_xaml_bad = n_py_bad = 0
    failed = False
    legacy_rows = []
    waived = []
    out = []

    # Bảng glyph chuẩn không được chứa glyph tofu — nếu không, gate "ngoài bảng"
    # sẽ hợp thức hoá chính glyph đó (ED1A Hide nằm trong bảng tới 2026-10-02).
    if not forced and cps is not None and table is not None:
        tofu = sorted(c for c in table if c not in cps)
        if tofu:
            failed = True
            out.append((os.path.basename(STANDARD_MD),
                        [("P1", "bảng glyph chuẩn chứa glyph KHÔNG có trong Segoe MDL2 "
                                "Assets: %s" % ", ".join(tofu))]))

    for path in py_files:
        with open(path, encoding="utf-8", errors="replace") as fh:
            py_issues = audit_py(fh.read(), os.path.basename(path))
        if py_issues:
            failed = True
            n_py_bad += 1
            out.append((os.path.relpath(path, GUI_DIR) if path.startswith(GUI_DIR)
                        else os.path.basename(path), py_issues))

    for path in files:
        base = os.path.basename(path)
        if base in UI_LOCKED and not forced:
            n_locked += 1
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        issues, declared, debt, must = audit(src, base, keys)

        if declared or forced:
            n_t3 += 1
            all_issues = issues + must
        else:
            n_legacy += 1
            legacy_rows.append((base, debt))
            # File legacy chỉ bị soi những luật áp cho MỌI file (hiện tại: copyright).
            all_issues = must
        if base in PENDING_GAP:
            gap, pats = PENDING_GAP[base]
            waived_here = [i for i in all_issues if any(p in i[1] for p in pats)]
            all_issues = [i for i in all_issues if i not in waived_here]
            if waived_here:
                waived.append((base, gap, waived_here))
        if all_issues:
            failed = True
            n_xaml_bad += 1
            out.append((base, all_issues))

    if count_only:
        print(n_legacy)
        return 0

    for base, issues in out:
        print("%s" % base)
        for sev, msg in sorted(issues, key=lambda i: i[0]):
            print("   [%s] %s" % (sev, msg))

    if show_legacy and not quiet:
        print()
        print("── LEGACY — chưa migrate, KHÔNG tính là vi phạm ──")
        for base, debt in legacy_rows:
            print("%-38s %s" % (base, " · ".join(debt) if debt else "—"))

    for base, gap, items in waived:
        print()
        print("%s — %d vi phạm ĐANG HOÃN" % (base, len(items)))
        print("   %s" % gap)
        for sev, msg in sorted(items, key=lambda i: i[0]):
            print("   [%s] %s  (hoãn)" % (sev, msg))

    print()
    print("T3 AUDIT: %d file khai T3 (%d file có vi phạm) · %d file legacy · %d UI-locked"
          % (n_t3, n_xaml_bad, n_legacy, n_locked))
    if py_files:
        print("          icon (luật 22): soát thêm %d file Python trong lib/GUI (%d có vi phạm)"
              % (len(py_files), n_py_bad))
    if n_legacy and not show_legacy:
        print("          chạy --legacy để xem nợ migration của %d file kia" % n_legacy)
    if waived:
        print("          %d vi phạm đang hoãn theo DESIGN SYSTEM GAP — chờ user duyệt"
              % sum(len(i) for _, _, i in waived))
    print("          %s" % ("FAILED" if failed else "OK"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
