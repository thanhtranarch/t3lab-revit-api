#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gác tương thích Revit API cho dải phiên bản được hỗ trợ (2022 → 2027).

Vì sao cần script này: Revit API xoá và thêm member theo từng năm. Một dòng
`BuiltInParameterGroup.PG_DATA` chạy ngon trên 2023 nhưng ném AttributeError
trên 2025+; `element_id.Value` chạy trên 2024+ nhưng không tồn tại trên 2022/
2023. Máy dev chỉ mở một hai bản Revit, nên lỗi kiểu này luôn lọt tới máy của
người khác trước.

Bảng RULES dưới đây KHÔNG viết theo trí nhớ: phần 2026 đo bằng reflection trên
Revit 2026.5 thật (2026-09-29) — ElementId chỉ còn `Value` và ctor Int64;
BuiltInParameterGroup / ParameterType / DisplayUnitType / UnitType đã mất;
Definition chỉ còn GetDataType() / GetGroupTypeId(); string filter rule không
còn tham số caseSensitive. Phần 2022–2025 theo API changes chính thức.

Luật được gác:
    Mọi chỗ dùng API không có mặt trên TẤT CẢ các bản 2022–2027 phải nằm trong
    một nhánh fallback:
      - trong `try:` NGẮN (≤ 40 dòng) hoặc trong `except` của bất kỳ try nào
        (fallback kiểu "API mới trước, API cũ sau");
      - trong `if` / biểu thức điều kiện có hasattr / getattr / version / year;
      - hoặc dòng đó ghi `# revit-compat: ok` (đã cân nhắc, có lý do).
    Cách chuẩn: gọi helper trong `lib/Snippets/_compat.py` (eid_value,
    make_eid, ...) thay vì tự viết fallback.

Usage:
    python dev/audit_revit_compat.py
    python dev/audit_revit_compat.py --quiet     # chỉ vi phạm, exit 1 nếu có
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
EXT = os.path.join(REPO, "T3Lab.extension")
SKIP_DIRS = {"__pycache__", "_archive", "scratch"}

SUPPORTED = (2022, 2027)

PRAGMA = "revit-compat: ok"

# Receiver của một kiểu Revit: `DB.X`, `RDB.X`, `Autodesk.Revit.DB.X`, `UI.X`...
_REVIT_NS = re.compile(r"(?:^|\.)(?:DB|RDB|_DB|UI|RUI|_RUI)$")
# Receiver trông như một ElementId: `eid`, `element_id`, `v.Id`, `vp.ViewId`.
_ELEMENT_ID = re.compile(r"(?:^|\.)(?:e?id|\w*_id|\w*Id)$")
_DEFINITION = re.compile(r"(?i)def")
_WORKSET = re.compile(r"(?i)workset|wid$|wsid$")

# kind:
#   type   — tên kiểu: `X`, `from ... import X`, `DB.X`
#   member — thuộc tính `obj.X`; `receiver` lọc obj, `skip` loại obj
RULES = [
    dict(name="BuiltInParameterGroup", kind="type", removed=2025,
         fix="GroupTypeId (có từ 2022)"),
    dict(name="ParameterType", kind="type", removed=2023,
         fix="SpecTypeId (có từ 2022)"),
    dict(name="DisplayUnitType", kind="type", removed=2022,
         fix="UnitTypeId"),
    dict(name="UnitType", kind="type", removed=2022,
         fix="SpecTypeId"),
    dict(name="UIThemeManager", kind="type", added=2024,
         fix="getattr(UI, 'UIThemeManager', None)"),
    dict(name="IntegerValue", kind="member", removed=2026, skip=_WORKSET,
         fix="Snippets._compat.eid_value()"),
    dict(name="Value", kind="member", added=2024, receiver=_ELEMENT_ID,
         fix="Snippets._compat.eid_value()"),
    dict(name="ParameterType", kind="member", removed=2023,
         receiver=_DEFINITION, fix="Definition.GetDataType()"),
    dict(name="ParameterGroup", kind="member", removed=2025,
         receiver=_DEFINITION, fix="Definition.GetGroupTypeId()"),
    dict(name="UnitType", kind="member", removed=2022,
         receiver=_DEFINITION, fix="Definition.GetDataType()"),
    dict(name="DisplayUnitType", kind="member", removed=2022,
         fix="GetUnitTypeId()"),
    dict(name="DisplayUnits", kind="member", removed=2022,
         fix="FormatOptions.GetUnitTypeId()"),
    # Rebar (2026-10-02, panel Rebar & Assembly) — theo Revit 2026/2027 API
    # What's New, xem dev/plan/rebar-tekla-implementation-spec.md §2.3.
    dict(name="RebarHookOrientation", kind="type", removed=2027,
         fix="Snippets._compat.create_rebar_from_curves()"),
    dict(name="BarTerminationsData", kind="type", added=2026,
         fix="Snippets._compat.create_rebar_from_curves()"),
    dict(name="NumberingSchemaType", kind="type", removed=2027,
         fix="NumberingSchema.GetNumberingSchema(doc, name) / GetSchemasInDocument"),
    dict(name="BarMassPerUnitLength", kind="member", added=2027,
         fix="Snippets._compat.bar_mass_per_metre()"),
    dict(name="Mass", kind="member", added=2027, receiver=re.compile(r"(?i)rebar|bar$"),
         fix="Snippets._compat.bar_mass_per_metre()"),
    dict(name="SetLayoutAsCustomSpacing", kind="member", added=2027,
         fix="SetLayoutAsMaximumSpacing (all releases)"),
]

# String filter rule có tham số caseSensitive — mất trên 2026 (đã đo).
STRING_RULE_FACTORIES = re.compile(
    r"^Create(?:Not)?(?:Equals|Contains|BeginsWith|EndsWith)Rule$|"
    r"^Create(?:Greater|Less)(?:OrEqual)?Rule$")

GUARD_TEST = re.compile(r"(?i)hasattr|getattr|version|year|rvt|_ver\b")
SHORT_TRY = 40


def _in_range(rule):
    """Rule có làm hỏng ít nhất một bản trong SUPPORTED không?"""
    lo, hi = SUPPORTED
    if rule.get("removed") and rule["removed"] <= hi:
        return True
    if rule.get("added") and rule["added"] > lo:
        return True
    return False


def _broken_on(rule):
    lo, hi = SUPPORTED
    if rule.get("removed"):
        first = max(lo, rule["removed"])
        return "Revit %d–%d" % (first, hi) if first < hi else "Revit %d" % hi
    last = min(hi, rule["added"] - 1)
    return "Revit %d–%d" % (lo, last) if last > lo else "Revit %d" % lo


def _src(node):
    try:
        return ast.unparse(node)
    except Exception:
        return ""


class _Scan(object):

    def __init__(self, tree, lines):
        self.lines = lines
        self.parent = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node

    def guarded(self, node):
        """True khi node nằm trong một nhánh fallback hợp lệ."""
        line = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
        if PRAGMA in line:
            return True
        child = node
        cur = self.parent.get(node)
        first_try_seen = False
        while cur is not None:
            if isinstance(cur, ast.Try):
                if any(child is h or self._contains(h, child) for h in cur.handlers):
                    return True                      # đang ở trong except: fallback
                if not first_try_seen and child in cur.body or \
                        (not first_try_seen and any(self._contains(b, child) for b in cur.body)):
                    first_try_seen = True
                    end = max(getattr(b, "end_lineno", b.lineno) for b in cur.body)
                    if end - cur.lineno <= SHORT_TRY:
                        return True                  # try ngắn quanh đúng chỗ đó
            elif isinstance(cur, (ast.If, ast.IfExp, ast.While)):
                if GUARD_TEST.search(_src(cur.test)):
                    return True
            elif isinstance(cur, ast.BoolOp):
                # `hasattr(x, 'Value') and x.Value`
                if any(GUARD_TEST.search(_src(v)) for v in cur.values if v is not child):
                    return True
            child = cur
            cur = self.parent.get(cur)
        return False

    @staticmethod
    def _contains(root, node):
        return any(n is node for n in ast.walk(root))


def audit_file(path):
    src = io.open(path, encoding="utf-8-sig", errors="replace").read()
    tree = ast.parse(src)
    scan = _Scan(tree, src.splitlines())
    issues = []
    type_rules = {r["name"]: r for r in RULES if r["kind"] == "type" and _in_range(r)}
    member_rules = [r for r in RULES if r["kind"] == "member" and _in_range(r)]

    def report(node, rule, what):
        if scan.guarded(node):
            return
        if rule.get("removed"):
            why = "mất từ Revit %d" % rule["removed"]
        else:
            why = "chỉ có từ Revit %d" % rule["added"]
        issues.append((node.lineno, "%s — %s, hỏng trên %s. Dùng %s"
                       % (what, why, _broken_on(rule), rule["fix"])))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("Autodesk.Revit"):
            for alias in node.names:
                rule = type_rules.get(alias.name)
                if rule:
                    report(node, rule, "import %s" % alias.name)
        elif isinstance(node, ast.Name) and node.id in type_rules \
                and isinstance(node.ctx, ast.Load):
            report(node, type_rules[node.id], node.id)
        elif isinstance(node, ast.Attribute):
            receiver = _src(node.value)
            rule = type_rules.get(node.attr)
            if rule and _REVIT_NS.search(receiver):
                report(node, rule, "%s.%s" % (receiver, node.attr))
                continue
            for rule in member_rules:
                if node.attr != rule["name"]:
                    continue
                if rule.get("receiver") and not rule["receiver"].search(receiver):
                    continue
                if rule.get("skip") and rule["skip"].search(receiver):
                    continue
                report(node, rule, "%s.%s" % (receiver, node.attr))
        elif isinstance(node, ast.Call):
            fname = node.func.attr if isinstance(node.func, ast.Attribute) else \
                getattr(node.func, "id", "")
            third_is_bool = len(node.args) == 3 and isinstance(node.args[2], ast.Constant) \
                and isinstance(node.args[2].value, bool)
            if (STRING_RULE_FACTORIES.match(fname or "") and third_is_bool) or \
                    (fname == "FilterStringRule" and len(node.args) == 4):
                report(node, dict(removed=2026, fix="overload không có caseSensitive "
                                                   "(fallback cho 2022)"),
                       "%s(..., caseSensitive)" % fname)

    return sorted(set(issues))


def iter_files():
    for dirpath, dirnames, filenames in os.walk(EXT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def main():
    quiet = "--quiet" in sys.argv[1:]
    total = bad = scanned = 0
    for path in sorted(iter_files()):
        rel = os.path.relpath(path, REPO).replace(os.sep, "/")
        try:
            issues = audit_file(path)
        except SyntaxError as exc:
            print("%s: không parse được: %s" % (rel, exc))
            bad += 1
            continue
        scanned += 1
        if issues:
            bad += 1
            total += len(issues)
            for lineno, msg in issues:
                print("%s:%d [P0] %s" % (rel, lineno, msg))
        elif not quiet:
            print("  %s ok" % rel)

    print("REVIT COMPAT %d–%d: %d file · %d file có vi phạm · %d vi phạm"
          % (SUPPORTED[0], SUPPORTED[1], scanned, bad, total))
    return 1 if total or bad else 0


if __name__ == "__main__":
    sys.exit(main())
