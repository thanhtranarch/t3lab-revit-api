# -*- coding: utf-8 -*-
"""Gate ribbon T3Lab — mọi tab, mọi panel theo cùng một bộ luật.

    python3 dev/audit_ribbon.py            # báo cáo đầy đủ: độ rộng từng tab, slot từng panel
    python3 dev/audit_ribbon.py --quiet    # chỉ in vi phạm + một dòng kết, exit 1 nếu có

Luật: dev/plan/ribbon-tab-split.md §4. Gate kiểm phần đọc tĩnh được từ
bundle.yaml (P8 Support chỉ chứa tool hỗ trợ, P9 tier icon là việc của người
review và của dev/audit_icons.py):

  P1  panel có 2–6 slot (slot = 1 nút lớn, 1 pulldown lớn hoặc 1 stack)
  P2  nút lớn / pulldown lớn trước, stack ở phải cùng
  P3  stack có 2–3 nút
  P4  tab ước lượng ≤ BUDGET_PX — stack không mất chữ trên laptop 1366 px
  P5  mọi panel cùng màu tiêu đề PANEL_TITLE_COLOR
  P6  tên panel: Title Case, ≤ 22 ký tự, không trùng giữa các tab
  P7  tên folder bundle duy nhất trong cả extension (code tìm tool theo tên)
  P10 `layout:` liệt kê đủ và đúng: pyRevit KHÔNG dựng bundle nào thiếu trong
      layout của cha (nút biến mất khỏi ribbon, không báo lỗi), còn mục layout
      không có folder là mục ma

Độ rộng đo trên ảnh chụp ribbon 2026-10-03 (Revit, ảnh rộng 2000 px).
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tabdir import EXT, TABS  # noqa: E402  (every ribbon tab; folder names change)

QUIET = "--quiet" in sys.argv

LARGE_PX = 57          # nút lớn / pulldown lớn
STACK_PX = 140         # stack 3 nút nhỏ có chữ
GAP_PX = 8             # giữa hai panel
BUDGET_PX = 1250       # P4
PANEL_SLOTS = (2, 6)   # P1
STACK_ITEMS = (2, 3)   # P3
TITLE_MAX = 22         # P6
PANEL_TITLE_COLOR = "#46E07B00"   # P5 — màu đang dùng trên mọi panel

CONTAINERS = (".tab", ".panel", ".stack", ".pulldown", ".splitbutton", ".splitpushbutton")
LARGE = (".pushbutton", ".pulldown", ".splitbutton", ".splitpushbutton", ".urlbutton",
         ".smartbutton", ".linkbutton", ".invokebutton", ".content")
ITEMS = LARGE + (".stack",)
# Không chiếm slot: nút mở dialog ở góc tiêu đề panel.
NO_SLOT = (".panelbutton",)


# ── bundle.yaml ──────────────────────────────────────────────────────────


def read_bundle(folder):
    """{'title': str|None, 'layout': [str]|None, 'background': {k: v}} — line scanner
    cho đúng tập con pyRevit dùng ở đây (key phẳng, `layout:` list, `background:` map)."""
    meta = {"title": None, "layout": None, "background": {}}
    path = os.path.join(folder, "bundle.yaml")
    if not os.path.isfile(path):
        return meta
    section = None
    with io.open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            top = re.match(r"([A-Za-z_]+)\s*:\s*(.*)$", line)
            if top:
                key, val = top.group(1), top.group(2).strip()
                section = key
                if key == "layout":
                    meta["layout"] = []
                elif key == "title" and val:
                    meta["title"] = unquote(val)
                continue
            item = re.match(r"\s+-\s*(.+?)\s*$", line)
            if item and section == "layout":
                meta["layout"].append(unquote(item.group(1)))
                continue
            sub = re.match(r"\s+([A-Za-z_]+)\s*:\s*(.+?)\s*$", line)
            if sub and section == "background":
                meta["background"][sub.group(1)] = unquote(sub.group(2))
    return meta


def unquote(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text.replace("\\n", " ")


def layout_name(entry):
    """'Name[title:Other]' -> 'Name'; separators / slideouts -> None."""
    if "---" in entry or ">>>" in entry:
        return None
    m = re.match(r"(.+)\[(.+):(.*)\]$", entry)
    return (m.group(1) if m else entry).strip()


def children(folder, suffixes):
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [n for n in names if n.endswith(suffixes) and os.path.isdir(os.path.join(folder, n))]


def stem(name):
    return os.path.splitext(name)[0]


def shown(folder, suffixes):
    """Con được pyRevit dựng, đúng thứ tự ribbon: theo `layout:` nếu có (con thiếu
    trong layout bị bỏ), không thì theo tên."""
    kids = children(folder, suffixes)
    layout = read_bundle(folder)["layout"]
    if not layout:
        return kids
    by_stem = dict((stem(k), k) for k in kids)
    out = []
    for entry in layout:
        name = layout_name(entry)
        if name and name in by_stem:
            out.append(by_stem[name])
    return out


# ── Gate ─────────────────────────────────────────────────────────────────


class Audit(object):
    def __init__(self, tabs=None):
        self.tab_dirs = list(tabs or TABS)
        self.ext = os.path.dirname(self.tab_dirs[0]) if self.tab_dirs else EXT
        self.issues = []      # (rule, where, message)
        self.tabs = []        # (tab, width, slots, [(panel, slots, width)])

    def add(self, rule, folder, msg):
        self.issues.append((rule, os.path.relpath(folder, self.ext).replace("\\", "/"), msg))

    def check_layout(self, folder, suffixes):
        """P10 cho một container."""
        layout = read_bundle(folder)["layout"]
        if not layout:
            return
        kids = children(folder, suffixes)
        stems = set(stem(k) for k in kids)
        listed = set(n for n in (layout_name(e) for e in layout) if n)
        for k in kids:
            if stem(k) not in listed:
                self.add("P10", folder, "%s không có trong layout: pyRevit sẽ KHÔNG dựng nó" % k)
        for name in sorted(listed - stems):
            self.add("P10", folder, "layout có '%s' nhưng không có folder nào tên đó" % name)

    def run(self):
        if read_bundle(self.ext)["layout"]:
            self.check_layout(self.ext, (".tab",))
        titles = {}
        for tab in self.tab_dirs:
            self.check_tab(tab, titles)
        self.check_unique_names()
        return self

    def check_tab(self, tab, titles):
        self.check_layout(tab, (".panel",))
        panels = []
        for panel in shown(tab, (".panel",)):
            pdir = os.path.join(tab, panel)
            panels.append(self.check_panel(pdir, titles))
        width = sum(w for _p, _s, w in panels) + GAP_PX * max(len(panels) - 1, 0)
        slots = sum(s for _p, s, _w in panels)
        if width > BUDGET_PX:
            self.add("P4", tab, "rộng ≈ %d px > ngân sách %d px (%d slot): Revit sẽ co stack mất chữ"
                     % (width, BUDGET_PX, slots))
        self.tabs.append((os.path.basename(tab), width, slots, panels))

    def check_panel(self, pdir, titles):
        meta = read_bundle(pdir)
        self.check_layout(pdir, ITEMS + NO_SLOT)
        items = [i for i in shown(pdir, ITEMS + NO_SLOT) if not i.endswith(NO_SLOT)]

        # P1
        lo, hi = PANEL_SLOTS
        if not lo <= len(items) <= hi:
            self.add("P1", pdir, "%d slot, luật là %d–%d" % (len(items), lo, hi))
        # P2
        first_stack = next((i for i, x in enumerate(items) if x.endswith(".stack")), len(items))
        late = [x for x in items[first_stack:] if not x.endswith(".stack")]
        if late:
            self.add("P2", pdir, "nút lớn đứng sau stack: %s — nút lớn phải đứng trước stack"
                     % ", ".join(late))
        # P3
        for item in items:
            if item.endswith(".stack"):
                sdir = os.path.join(pdir, item)
                self.check_layout(sdir, LARGE)
                n = len(shown(sdir, LARGE))
                if not STACK_ITEMS[0] <= n <= STACK_ITEMS[1]:
                    self.add("P3", sdir, "%d nút, stack phải có %d–%d" % ((n,) + STACK_ITEMS))
            elif item.endswith((".pulldown", ".splitbutton", ".splitpushbutton")):
                self.check_layout(os.path.join(pdir, item), LARGE + (".stack",))
        # P5
        color = meta["background"].get("title")
        if color != PANEL_TITLE_COLOR:
            self.add("P5", pdir, "màu tiêu đề panel %s, chuẩn là %s (background: title:)"
                     % (color or "không khai", PANEL_TITLE_COLOR))
        # P6
        title = meta["title"] or stem(os.path.basename(pdir))
        if len(title) > TITLE_MAX:
            self.add("P6", pdir, "tên panel '%s' dài %d > %d ký tự" % (title, len(title), TITLE_MAX))
        if any(w[:1].islower() for w in title.split() if w != "&"):
            self.add("P6", pdir, "tên panel '%s' phải Title Case" % title)
        if title in titles:
            self.add("P6", pdir, "tên panel '%s' trùng với %s" % (title, titles[title]))
        titles.setdefault(title, os.path.relpath(pdir, self.ext))

        width = sum(STACK_PX if i.endswith(".stack") else LARGE_PX for i in items)
        return (title, len(items), width)

    def check_unique_names(self):
        seen = {}
        for tab in self.tab_dirs:
            for dirpath, dirs, _files in os.walk(tab):
                for d in sorted(dirs):
                    if not d.endswith(CONTAINERS + LARGE + NO_SLOT):
                        continue
                    path = os.path.join(dirpath, d)
                    if d in seen:
                        self.add("P7", path, "trùng tên với %s — find_bundle() chỉ thấy một"
                                 % os.path.relpath(seen[d], self.ext))
                    seen.setdefault(d, path)


def main():
    audit = Audit().run()
    if not QUIET:
        for tab, width, slots, panels in audit.tabs:
            print("%-22s ≈ %4d px · %2d slot   (ngân sách %d px)" % (tab, width, slots, BUDGET_PX))
            for title, n, w in panels:
                print("    %-22s %d slot  ≈ %d px" % (title, n, w))
        print("")
    for rule, where, msg in sorted(audit.issues):
        print("%-4s %-48s %s" % (rule, where, msg))
    print("\nRIBBON: %d tab · %d panel · %d vi phạm"
          % (len(audit.tabs), sum(len(p) for _t, _w, _s, p in audit.tabs), len(audit.issues)))
    if audit.issues:
        print("          FAIL")
        return 1
    print("          OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
