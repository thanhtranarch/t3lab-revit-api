#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test cho Selection/explorer.py — chạy NGOÀI Revit.

`explorer.py` import `clr` / `Autodesk.Revit.DB` ở đầu file nên không import
trực tiếp được ở đây. Test này cắm stub vào `sys.modules` TRƯỚC khi import, rồi
soi đúng phần logic thuần Python: gom nhóm, đếm, gộp ElementId, lọc theo
search, trần INSTANCE_CAP và xuất CSV.

Phần chạm Revit thật (collect / build_warning_tree) không test được ở đây —
chúng cần một document; xem checklist QA trong Revit.

Usage:
    python dev/test_explorer.py
    python dev/test_explorer.py --quiet
"""
import os
import sys
import types

sys.dont_write_bytecode = True

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, "T3Lab.extension", "lib")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ── Stub cho .NET / Revit API ─────────────────────────────────────────────
class _StubElementId(object):
    InvalidElementId = None

    def __init__(self, value=-1):
        self.Value = value

    def __eq__(self, other):
        return isinstance(other, _StubElementId) and other.Value == self.Value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.Value)


_StubElementId.InvalidElementId = _StubElementId(-1)


class _Enum(object):
    """Đứng thay BuiltInCategory / BuiltInParameter / CategoryType."""

    def __init__(self, name="stub", value=0):
        self._name = name
        self._value = value
        self._cache = {}

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        if item not in self._cache:
            self._cache[item] = _Enum(item, abs(hash(item)) % 100000)
        return self._cache[item]

    def __int__(self):
        return self._value

    def __repr__(self):
        return "<%s>" % self._name


def _install_stubs():
    clr = types.ModuleType("clr")
    clr.AddReference = lambda *_a, **_k: None
    sys.modules["clr"] = clr

    db = types.ModuleType("Autodesk.Revit.DB")
    db.BuiltInCategory = _Enum("BuiltInCategory")
    db.BuiltInParameter = _Enum("BuiltInParameter")
    db.CategoryType = _Enum("CategoryType")
    db.ElementId = _StubElementId
    db.FilteredElementCollector = object
    db.ImportInstance = type("ImportInstance", (object,), {})
    db.RevitLinkInstance = type("RevitLinkInstance", (object,), {})

    autodesk = types.ModuleType("Autodesk")
    revit_mod = types.ModuleType("Autodesk.Revit")
    autodesk.Revit = revit_mod
    revit_mod.DB = db
    sys.modules["Autodesk"] = autodesk
    sys.modules["Autodesk.Revit"] = revit_mod
    sys.modules["Autodesk.Revit.DB"] = db

    generic = types.ModuleType("System.Collections.Generic")
    generic.List = lambda _t: (lambda seq=(): list(seq))

    collections = types.ModuleType("System.Collections")
    collections.Generic = generic
    system = types.ModuleType("System")
    system.Collections = collections
    sys.modules["System"] = system
    sys.modules["System.Collections"] = collections
    sys.modules["System.Collections.Generic"] = generic


_install_stubs()

if LIB not in sys.path:
    sys.path.insert(0, LIB)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "t3_explorer", os.path.join(LIB, "Selection", "explorer.py"))
explorer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(explorer)


# ── Element giả ───────────────────────────────────────────────────────────
class _FakeElement(object):
    def __init__(self, eid, name):
        self.Id = _StubElementId(eid)
        self.Name = name

    def get_Parameter(self, _pid):
        return None


_SEQ = [0]


def record(category, family, type_name, label=None):
    """ElementRecord dựng tay — không cần document."""
    _SEQ[0] += 1
    element = _FakeElement(_SEQ[0], label or ("%s-%d" % (type_name, _SEQ[0])))
    return explorer.ElementRecord(element, None, category, family, type_name)


# ── Test ──────────────────────────────────────────────────────────────────
FAILURES = []


def check(name, condition, detail=""):
    if condition:
        return True
    FAILURES.append("%s%s" % (name, (" — " + detail) if detail else ""))
    return False


def sample():
    """Mô hình nhỏ mô phỏng ảnh tham chiếu Ideate Explorer."""
    rows = []
    rows += [record("Doors", "Bifold-2 Panel", '28" x 80" ADA') for _ in range(4)]
    rows += [record("Doors", "Bifold-2 Panel", '31" x 80" ADA') for _ in range(2)]
    rows += [record("Doors", "GMA_Door_Common", "Double Swing") for _ in range(5)]
    rows += [record("Casework", "Base Cabinet", "24 inch") for _ in range(3)]
    rows += [record("Ceilings", "Compound Ceiling", "600x600") for _ in range(6)]
    return rows


def test_category_grouping():
    root = explorer.build_tree(sample(), group_by=explorer.GROUP_CATEGORY,
                               with_instances=False)
    check("root count", root.count == 20, "got %d" % root.count)
    check("3 categories", len(root.children) == 3,
          "got %d" % len(root.children))

    by_label = dict((c.label, c) for c in root.children)
    check("categories sorted",
          [c.label for c in root.children] == ["Casework", "Ceilings", "Doors"],
          str([c.label for c in root.children]))
    check("Doors count", by_label["Doors"].count == 11,
          "got %d" % by_label["Doors"].count)
    check("Doors has 2 families", len(by_label["Doors"].children) == 2)

    bifold = dict((c.label, c) for c in by_label["Doors"].children)["Bifold-2 Panel"]
    check("Bifold count", bifold.count == 6, "got %d" % bifold.count)
    check("Bifold has 2 types", len(bifold.children) == 2)
    check("leaf kind", all(c.kind == "leaf" for c in bifold.children))


def test_other_groupings():
    rows = sample()

    flat = explorer.build_tree(rows, group_by=explorer.GROUP_TYPE,
                               with_instances=False)
    check("group by Type is flat", len(flat.children) == 5,
          "got %d" % len(flat.children))
    check("Type totals preserved", flat.count == 20, "got %d" % flat.count)

    fam = explorer.build_tree(rows, group_by=explorer.GROUP_FAMILY,
                              with_instances=False)
    check("group by Family", len(fam.children) == 4, "got %d" % len(fam.children))

    # Workset/Level/Phase đọc lười qua get_Parameter -> None, nên tất cả rơi
    # vào một rổ NONE_LABEL. Điều cần kiểm là nó KHÔNG nổ và vẫn đếm đủ.
    ws = explorer.build_tree(rows, group_by=explorer.GROUP_WORKSET,
                             with_instances=False)
    check("group by Workset survives missing params", ws.count == 20,
          "got %d" % ws.count)
    check("missing workset bucketed once", len(ws.children) == 1,
          "got %d" % len(ws.children))
    check("missing workset labelled", ws.children[0].label == explorer.NONE_LABEL,
          ws.children[0].label)


def test_ids_are_deduped():
    rows = sample()
    root = explorer.build_tree(rows, with_instances=False)
    ids = root.ids()
    check("ids count matches", len(ids) == 20, "got %d" % len(ids))
    values = [i.Value for i in ids]
    check("ids unique", len(set(values)) == len(values))

    # Cùng một phần tử treo ở hai node -> ids() vẫn chỉ trả một lần.
    dup = explorer.Node("dup")
    dup.records.append(rows[0])
    twin = explorer.Node("twin")
    twin.records.append(rows[0])
    parent = explorer.Node("parent")
    parent.children = [dup, twin]
    check("dedupe across branches", len(parent.ids()) == 1,
          "got %d" % len(parent.ids()))


def test_search():
    rows = sample()

    hit = explorer.build_tree(rows, search="bifold", with_instances=False)
    check("search by family", hit.count == 6, "got %d" % hit.count)
    check("search narrows to one category", len(hit.children) == 1)

    hit = explorer.build_tree(rows, search="ceil", with_instances=False)
    check("search by category", hit.count == 6, "got %d" % hit.count)

    hit = explorer.build_tree(rows, search="28", with_instances=False)
    check("search by type", hit.count == 4, "got %d" % hit.count)

    miss = explorer.build_tree(rows, search="nothing-here",
                               with_instances=False)
    check("search miss is empty", miss.count == 0, "got %d" % miss.count)
    check("search miss has no children", len(miss.children) == 0)

    blank = explorer.build_tree(rows, search="   ", with_instances=False)
    check("blank search keeps all", blank.count == 20, "got %d" % blank.count)


def test_instances_and_cap():
    root = explorer.build_tree(sample(), with_instances=True)
    doors = dict((c.label, c) for c in root.children)["Doors"]
    bifold = dict((c.label, c) for c in doors.children)["Bifold-2 Panel"]
    ada28 = dict((c.label, c) for c in bifold.children)['28" x 80" ADA']
    check("instances attached", len(ada28.children) == 4,
          "got %d" % len(ada28.children))
    check("instance kind", all(c.kind == "element" for c in ada28.children))
    check("leaf count unchanged by instances", ada28.count == 4,
          "got %d" % ada28.count)

    big = [record("Walls", "Basic Wall", "Generic 200")
           for _ in range(explorer.INSTANCE_CAP + 7)]
    root = explorer.build_tree(big, with_instances=True)
    leaf = root.children[0].children[0].children[0]
    notes = [c for c in leaf.children if c.kind == "note"]
    elements = [c for c in leaf.children if c.kind == "element"]
    check("cap respected", len(elements) == explorer.INSTANCE_CAP,
          "got %d" % len(elements))
    check("overflow note added", len(notes) == 1)
    check("note mentions the remainder", "7" in notes[0].label, notes[0].label)
    # Quan trọng nhất: trần chỉ giới hạn HÀNG HIỆN, không giới hạn lựa chọn.
    check("cap does not truncate ids",
          len(leaf.ids()) == explorer.INSTANCE_CAP + 7,
          "got %d" % len(leaf.ids()))


def test_tree_rows():
    root = explorer.build_tree(sample(), with_instances=False)
    rows = explorer.tree_rows(root)
    check("rows start at root", rows[0][0] == 0 and rows[0][2] == 20,
          str(rows[0]))
    depths = set(r[0] for r in rows)
    check("three grouping depths below root", depths == {0, 1, 2, 3},
          str(sorted(depths)))

    capped = explorer.build_tree(
        [record("Walls", "Basic Wall", "Generic 200")
         for _ in range(explorer.INSTANCE_CAP + 3)], with_instances=True)
    check("note rows excluded from CSV",
          all("not listed here" not in r[1] for r in explorer.tree_rows(capped)))


def test_walk_helpers():
    root = explorer.build_tree(sample(), with_instances=False)
    nodes = list(root.walk())
    check("walk yields self first", nodes[0] is root)
    check("walk covers every node",
          len(nodes) == 1 + 3 + 4 + 5, "got %d" % len(nodes))
    check("walk_records total", len(list(root.walk_records())) == 20,
          "got %d" % len(list(root.walk_records())))


def test_constants():
    check("every grouping is declared",
          set(explorer.GROUP_ORDER) == set(explorer.GROUPINGS.keys()))
    check("Category is the default", explorer.GROUP_ORDER[0] == explorer.GROUP_CATEGORY)
    check("filter list starts with None",
          explorer.FILTER_ORDER[0] == explorer.FILTER_NONE)
    check("scopes declared", len(explorer.SCOPES) == 3)


TESTS = (
    ("category grouping", test_category_grouping),
    ("other groupings", test_other_groupings),
    ("id dedupe", test_ids_are_deduped),
    ("search", test_search),
    ("instances & cap", test_instances_and_cap),
    ("csv rows", test_tree_rows),
    ("walk helpers", test_walk_helpers),
    ("constants", test_constants),
)


def main():
    quiet = "--quiet" in sys.argv[1:]
    for label, func in TESTS:
        before = len(FAILURES)
        try:
            func()
        except Exception as exc:
            FAILURES.append("%s raised %s: %s" % (label, type(exc).__name__, exc))
        if not quiet:
            status = "ok" if len(FAILURES) == before else "FAILED"
            print("  %-22s %s" % (label, status))

    if FAILURES:
        print("EXPLORER TEST: %d failure(s)" % len(FAILURES))
        for line in FAILURES:
            print("  - %s" % line)
        return 1
    print("EXPLORER TEST: %d group(s) ok" % len(TESTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
