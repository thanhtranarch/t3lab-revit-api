#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sinh tai lieu Tekla -> Revit 2027 va template phim tat tu mot nguon duy nhat.

  NGUON : T3Lab.extension/lib/data/tekla_bridge.json   (cung file Tekla Bridge doc)
  DICH  : docs/tekla-to-revit-2027.md                  (song ngu EN/VI)
          T3Lab.extension/lib/data/KeyboardShortcuts_Tekla.xml

Usage:
    python3 dev/build_tekla_docs.py            # ghi hai file
    python3 dev/build_tekla_docs.py --check    # exit 1 khi file tren dia lech voi JSON

Validation dung chinh `Snippets/_tekla_bridge.validate_rows`, nen JSON sai schema
thi khong sinh gi ca. Khong co timestamp trong output: `--check` so sanh tung byte.

Phim tat (quyet dinh D4): KHONG tu che keymap. Moi dong co lenh Revit duoc mot
<ShortcutItem Shortcuts=""> de trong; CommandId chi lay tu `revit_command_id`
(spike G10 ghi log, copy vao JSON). Dong chua co id chi la comment. Phim mac dinh
cua Tekla trong comment chi lay tu bang da trich dan (TEKLA_DEFAULT_SHORTCUTS).
"""
import importlib.util
import os
import sys
from xml.sax.saxutils import quoteattr

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_PY = os.path.join(REPO, "T3Lab.extension", "lib", "Snippets", "_tekla_bridge.py")
DOCS_OUT = os.path.join(REPO, "docs", "tekla-to-revit-2027.md")
XML_OUT = os.path.join(REPO, "T3Lab.extension", "lib", "data", "KeyboardShortcuts_Tekla.xml")

JSON_REL = "T3Lab.extension/lib/data/tekla_bridge.json"

GROUP_VI = {
    "model": "M\u00f4 h\u00ecnh v\u00e0 cast unit",
    "rebar": "Th\u00e9p (rebar)",
    "numbering": "\u0110\u00e1nh s\u1ed1",
    "drawing": "B\u1ea3n v\u1ebd",
    "report": "B\u00e1o c\u00e1o v\u00e0 xu\u1ea5t file",
    "env": "M\u00f4i tr\u01b0\u1eddng v\u00e0 th\u00f3i quen",
}

LAYER_EN = {1: "1 \u00b7 Revit has it", 2: "2 \u00b7 T3Lab tool", 3: "3 \u00b7 Revit does it differently"}
LAYER_VI = {1: "1 \u00b7 Revit c\u00f3 s\u1eb5n", 2: "2 \u00b7 Tool T3Lab", 3: "3 \u00b7 Revit l\u00e0m kh\u00e1c"}

# Loi khuyen cho tung phim mac dinh Tekla (key -> (EN, VI)). Khong khang dinh phim
# nao cua Revit dang bi chiem: Revit bao xung dot ngay trong hop thoai khi gan phim.
SHORTCUT_ADVICE = {
    "Ctrl+H": ("Candidate: assign it to Phases.",
               "C\u00f3 th\u1ec3 d\u00f9ng: g\u00e1n cho Phases."),
    "Ctrl+B": ("Candidate for Schedule/Quantities; check the key is free in the dialog first.",
               "C\u00f3 th\u1ec3 d\u00f9ng cho Schedule/Quantities; ki\u1ec3m tra ph\u00edm c\u00f2n tr\u1ed1ng trong h\u1ed9p tho\u1ea1i tr\u01b0\u1edbc."),
    "Ctrl+G": ("Candidate for Filters; check the key is free in the dialog first.",
               "C\u00f3 th\u1ec3 d\u00f9ng cho Filters; ki\u1ec3m tra ph\u00edm c\u00f2n tr\u1ed1ng trong h\u1ed9p tho\u1ea1i tr\u01b0\u1edbc."),
    "Shift+I": ("Nothing to bind: the Properties palette has no postable command. Open it from the ribbon.",
                "Kh\u00f4ng c\u00f3 g\u00ec \u0111\u1ec3 g\u00e1n: Properties palette kh\u00f4ng c\u00f3 l\u1ec7nh postable. M\u1edf t\u1eeb ribbon."),
    "Ctrl+Q": ("No Revit equivalent: run Tekla Bridge from the ribbon instead.",
               "Revit kh\u00f4ng c\u00f3 t\u01b0\u01a1ng \u0111\u01b0\u01a1ng: ch\u1ea1y Tekla Bridge t\u1eeb ribbon."),
    "Ctrl+Shift+C": ("Candidate for Keyboard Shortcuts; check the key is free in the dialog first.",
                     "C\u00f3 th\u1ec3 d\u00f9ng cho Keyboard Shortcuts; ki\u1ec3m tra ph\u00edm c\u00f2n tr\u1ed1ng trong h\u1ed9p tho\u1ea1i tr\u01b0\u1edbc."),
    "Ctrl+J": ("Nothing to bind: Revit has no AutoConnections.",
               "Kh\u00f4ng c\u00f3 g\u00ec \u0111\u1ec3 g\u00e1n: Revit kh\u00f4ng c\u00f3 AutoConnections."),
    "Alt+Q/W/E": ("Nothing to bind: Revit uses selection filters instead of rebar selection switches.",
                  "Kh\u00f4ng c\u00f3 g\u00ec \u0111\u1ec3 g\u00e1n: Revit d\u00f9ng selection filter thay cho c\u00f4ng t\u1eafc ch\u1ecdn rebar."),
}


def load_bridge():
    spec = importlib.util.spec_from_file_location("_t3_tekla_bridge", BRIDGE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── helpers ──────────────────────────────────────────────────────────────

def cell(text):
    """Markdown table cell: no pipes, no line breaks."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def where_cell(row, vi=False):
    a, b = row["ribbon_2026"], row["ribbon_2027"]
    both = "2026 v\u00e0 2027" if vi else "2026 and 2027"
    if not a and not b:
        return "\u2014"
    if a and (not b or b == a):
        return "%s: %s" % (both, cell(a))
    if not a:
        return "2027: %s [NV]" % cell(b)
    return "2026: %s<br>2027: %s [NV]" % (cell(a), cell(b))


def layer_cell(bridge, row, vi=False):
    text = (LAYER_VI if vi else LAYER_EN)[row["layer"]]
    name = bridge.tool_name(row)
    if name and row["layer"] != 2:
        text += "<br>+ T3Lab: %s" % name
    elif name:
        text += "<br>%s" % name
    return text


def group_table(bridge, rows, group_id, vi=False):
    members = [r for r in bridge.ordered(rows) if r["group"] == group_id]
    if vi:
        head = "| Tekla | Revit 2027 | L\u1edbp | V\u1ecb tr\u00ed (ribbon 2026 / 2027) | Kh\u00e1c \u1edf \u0111\u00e2u |"
    else:
        head = "| Tekla | Revit 2027 | Layer | Where (ribbon 2026 / 2027) | What is different |"
    lines = [head, "|---|---|---|---|---|"]
    for r in members:
        lines.append("| %s | %s | %s | %s | %s |" % (
            cell(r["tekla"]), cell(r["revit"]), layer_cell(bridge, r, vi),
            where_cell(r, vi), cell(r["doc_vi"] if vi else r["doc_en"])))
    return lines


# ── markdown ─────────────────────────────────────────────────────────────

def build_markdown(bridge, rows):
    ordered = bridge.ordered(rows)
    n_rows = len(ordered)
    n_ids = len([r for r in ordered if r["revit_command_id"]])
    n_bind = len([r for r in ordered if r["postable"]])
    n_groups = len(bridge.GROUPS)
    out = []
    add = out.append

    add("<!-- GENERATED by dev/build_tekla_docs.py from %s - do not edit. "
        "Edit the JSON, then run: python3 dev/build_tekla_docs.py -->" % JSON_REL)
    add("")
    add("# Tekla to Revit 2027 \u2014 command map / T\u1eeb Tekla sang Revit 2027 \u2014 b\u1ea3n \u0111\u1ed3 l\u1ec7nh")
    add("")
    add("> **EN** \u2014 This guide and the **Tekla Bridge** pushbutton (T3Lab \u203a Rebar & Assembly) are generated from one data file, "
        "`%s`: %d Tekla commands in %d groups. Search the same list inside Revit by typing a Tekla name in Tekla Bridge." % (JSON_REL, n_rows, n_groups))
    add(">")
    add("> **VI** \u2014 T\u00e0i li\u1ec7u n\u00e0y v\u00e0 n\u00fat **Tekla Bridge** (T3Lab \u203a Rebar & Assembly) \u0111\u01b0\u1ee3c sinh t\u1eeb c\u00f9ng m\u1ed9t file d\u1eef li\u1ec7u, "
        "`%s`: %d l\u1ec7nh Tekla trong %d nh\u00f3m. T\u00ecm c\u00f9ng danh s\u00e1ch \u0111\u00f3 ngay trong Revit b\u1eb1ng c\u00e1ch g\u00f5 t\u00ean Tekla v\u00e0o Tekla Bridge." % (JSON_REL, n_rows, n_groups))
    add("")
    add("Contents / M\u1ee5c l\u1ee5c: [1 Three layers](#1--three-layers--ba-l\u1edbp) \u00b7 [2 Command map](#2--command-map--b\u1ea3n-\u0111\u1ed3-l\u1ec7nh) \u00b7 "
        "[3 Weight schedule](#3--weight-schedule-template--m\u1eabu-b\u1ea3ng-kh\u1ed1i-l\u01b0\u1ee3ng) \u00b7 [4 Shortcuts](#4--shortcuts--ph\u00edm-t\u1eaft) \u00b7 "
        "[5 Assembly rules](#5--assembly-rules--lu\u1eadt-assembly) \u00b7 [6 Known limits](#6--known-limits--gi\u1edbi-h\u1ea1n-\u0111\u00e3-bi\u1ebft) \u00b7 "
        "[7 Verification status](#7--verification-status--t\u00ecnh-tr\u1ea1ng-x\u00e1c-minh)")
    add("")

    # 1 ── three layers
    add("## 1 \u00b7 Three layers / Ba l\u1edbp")
    add("")
    add("**English**")
    add("")
    add("Every step of the Tekla rebar workflow falls into exactly one of three layers:")
    add("")
    add("- **Layer 1 \u2014 Revit has it.** The T3Lab tools do not repeat it. Tekla Bridge takes you to the right Revit command by its Tekla name, "
        "with one line on what is different, and this guide explains how Revit does it.")
    add("- **Layer 2 \u2014 Revit lacks it.** A T3Lab tool fills exactly that gap: Cast Unit Manager, Clone Drawing, Rebar Check, BVBS Export and Rebar Wizard.")
    add("- **Layer 3 \u2014 Revit does it differently, and usually better.** The tools do not imitate Tekla; they put a familiar name on the Revit way. "
        "Example: Tekla renumbers the whole model, Revit numbers automatically inside a partition, so you only assign the right partition \u2014 "
        "Cast Unit Manager does that by rule.")
    add("")
    add("**Ti\u1ebfng Vi\u1ec7t**")
    add("")
    add("M\u1ecdi b\u01b0\u1edbc trong quy tr\u00ecnh th\u00e9p c\u1ee7a Tekla r\u01a1i v\u00e0o \u0111\u00fang m\u1ed9t trong ba l\u1edbp:")
    add("")
    add("- **L\u1edbp 1 \u2014 Revit c\u00f3 s\u1eb5n.** Tool T3Lab kh\u00f4ng l\u00e0m l\u1ea1i. Tekla Bridge \u0111\u01b0a b\u1ea1n \u0111\u1ebfn \u0111\u00fang l\u1ec7nh Revit theo t\u00ean Tekla, "
        "k\u00e8m m\u1ed9t d\u00f2ng n\u00f3i kh\u00e1c bi\u1ec7t \u1edf \u0111\u00e2u, c\u00f2n t\u00e0i li\u1ec7u n\u00e0y gi\u1ea3i th\u00edch c\u00e1ch Revit l\u00e0m vi\u1ec7c \u0111\u00f3.")
    add("- **L\u1edbp 2 \u2014 Revit thi\u1ebfu.** M\u1ed9t tool T3Lab l\u1ea5p \u0111\u00fang kho\u1ea3ng tr\u1ed1ng \u0111\u00f3: Cast Unit Manager, Clone Drawing, Rebar Check, BVBS Export v\u00e0 Rebar Wizard.")
    add("- **L\u1edbp 3 \u2014 Revit l\u00e0m kh\u00e1c, th\u01b0\u1eddng t\u1ed1t h\u01a1n.** Tool kh\u00f4ng b\u1eaft ch\u01b0\u1edbc Tekla; ch\u1ec9 \u0111\u1eb7t t\u00ean quen thu\u1ed9c l\u00ean c\u00e1ch l\u00e0m c\u1ee7a Revit. "
        "V\u00ed d\u1ee5: Tekla \u0111\u00e1nh s\u1ed1 l\u1ea1i c\u1ea3 model, Revit \u0111\u00e1nh s\u1ed1 t\u1ef1 \u0111\u1ed9ng trong partition, n\u00ean b\u1ea1n ch\u1ec9 c\u1ea7n g\u00e1n \u0111\u00fang partition \u2014 "
        "Cast Unit Manager l\u00e0m vi\u1ec7c \u0111\u00f3 theo quy t\u1eafc.")
    add("")

    # 2 ── command map
    add("## 2 \u00b7 Command map / B\u1ea3n \u0111\u1ed3 l\u1ec7nh")
    add("")
    add("One table per group, in the order Tekla users work. Ribbon names for Revit 2027 are marked [NV] until confirmed on Revit 2027 "
        "(see section 7). / M\u1ed7i nh\u00f3m m\u1ed9t b\u1ea3ng, theo th\u1ee9 t\u1ef1 ng\u01b0\u1eddi d\u00f9ng Tekla l\u00e0m vi\u1ec7c. "
        "T\u00ean ribbon c\u1ee7a Revit 2027 \u0111\u00e1nh d\u1ea5u [NV] cho \u0111\u1ebfn khi \u0111\u01b0\u1ee3c x\u00e1c nh\u1eadn tr\u00ean Revit 2027 (xem m\u1ee5c 7).")
    add("")
    for index, (gid, label) in enumerate(bridge.GROUPS, start=1):
        add("### 2.%d %s / %s" % (index, label, GROUP_VI[gid]))
        add("")
        add("**English**")
        add("")
        out.extend(group_table(bridge, rows, gid, vi=False))
        add("")
        add("**Ti\u1ebfng Vi\u1ec7t**")
        add("")
        out.extend(group_table(bridge, rows, gid, vi=True))
        add("")

    # 3 ── weight template
    add("## 3 \u00b7 Weight schedule template / M\u1eabu b\u1ea3ng kh\u1ed1i l\u01b0\u1ee3ng")
    add("")
    add("**English**")
    add("")
    add("**Revit 2027** computes rebar mass natively (`Rebar.Mass`, `RebarBarType.BarMassPerUnitLength`): add the mass field to a Structural Rebar schedule "
        "and group by Bar Diameter \u2014 no setup needed. The unit of the type-level value is confirmed by the Revit spike (probe G15). [NV]")
    add("")
    add("**Before 2027**, use a shared parameter on the bar type and a calculated field:")
    add("")
    add("1. Manage \u203a Shared Parameters: create `T3_WeightPerMetre` (type Number, kg per metre).")
    add("2. Manage \u203a Project Parameters: add it as a **Type** parameter bound to the Structural Rebar category. [NV: check that Rebar Bar Types accept it in your release]")
    add("3. Enter kg/m for every Rebar Bar Type. Reference values, 0.006165 \u00d7 \u00d8\u00b2 (\u00d8 in mm): "
        + ", ".join("\u00d8%d = %.3f" % (d, 0.006165 * d * d) for d in (8, 10, 12, 14, 16, 20, 25, 32, 40)) + ".")
    add("4. View \u203a Schedules \u203a Schedule/Quantities \u203a Structural Rebar. Fields: Bar Diameter, Quantity, Total Bar Length, `T3_WeightPerMetre`.")
    add("5. Add a Calculated Value named `Weight (kg)`, type Number: `Total Bar Length / 1 m * T3_WeightPerMetre`. "
        "[NV: if Revit rejects the unit literal, express the length in your project unit.]")
    add("6. Group by Bar Diameter, tick **Totals** on that group and on the grand total.")
    add("")
    add("**Ti\u1ebfng Vi\u1ec7t**")
    add("")
    add("**Revit 2027** t\u1ef1 t\u00ednh kh\u1ed1i l\u01b0\u1ee3ng th\u00e9p (`Rebar.Mass`, `RebarBarType.BarMassPerUnitLength`): th\u00eam field kh\u1ed1i l\u01b0\u1ee3ng v\u00e0o schedule Structural Rebar "
        "r\u1ed3i nh\u00f3m theo Bar Diameter \u2014 kh\u00f4ng c\u1ea7n c\u00e0i \u0111\u1eb7t. \u0110\u01a1n v\u1ecb c\u1ee7a gi\u00e1 tr\u1ecb \u1edf m\u1ee9c type s\u1ebd \u0111\u01b0\u1ee3c spike Revit x\u00e1c nh\u1eadn (probe G15). [NV]")
    add("")
    add("**Tr\u01b0\u1edbc 2027**, d\u00f9ng shared parameter tr\u00ean bar type v\u00e0 m\u1ed9t calculated field:")
    add("")
    add("1. Manage \u203a Shared Parameters: t\u1ea1o `T3_WeightPerMetre` (ki\u1ec3u Number, kg tr\u00ean m\u00e9t).")
    add("2. Manage \u203a Project Parameters: th\u00eam th\u00e0nh tham s\u1ed1 **Type** g\u00e1n cho category Structural Rebar. [NV: ki\u1ec3m tra Rebar Bar Type c\u00f3 nh\u1eadn tham s\u1ed1 n\u00e0y tr\u00ean b\u1ea3n Revit c\u1ee7a b\u1ea1n]")
    add("3. Nh\u1eadp kg/m cho t\u1eebng Rebar Bar Type. Gi\u00e1 tr\u1ecb tham chi\u1ebfu, 0.006165 \u00d7 \u00d8\u00b2 (\u00d8 t\u00ednh b\u1eb1ng mm): "
        + ", ".join("\u00d8%d = %.3f" % (d, 0.006165 * d * d) for d in (8, 10, 12, 14, 16, 20, 25, 32, 40)) + ".")
    add("4. View \u203a Schedules \u203a Schedule/Quantities \u203a Structural Rebar. Field: Bar Diameter, Quantity, Total Bar Length, `T3_WeightPerMetre`.")
    add("5. Th\u00eam Calculated Value t\u00ean `Weight (kg)`, ki\u1ec3u Number: `Total Bar Length / 1 m * T3_WeightPerMetre`. "
        "[NV: n\u1ebfu Revit t\u1eeb ch\u1ed1i h\u1eb1ng s\u1ed1 c\u00f3 \u0111\u01a1n v\u1ecb, h\u00e3y bi\u1ec3u di\u1ec5n chi\u1ec1u d\u00e0i theo \u0111\u01a1n v\u1ecb c\u1ee7a d\u1ef1 \u00e1n.]")
    add("6. Nh\u00f3m theo Bar Diameter, tick **Totals** cho nh\u00f3m v\u00e0 cho t\u1ed5ng cu\u1ed1i.")
    add("")

    # 4 ── shortcuts
    add("## 4 \u00b7 Shortcuts / Ph\u00edm t\u1eaft")
    add("")
    add("**English**")
    add("")
    add("T3Lab does **not** ship an invented keymap. `T3Lab.extension/lib/data/KeyboardShortcuts_Tekla.xml` is a template generated from the command map: "
        "one entry per command that has a Revit command id, with the keys left empty (`Shortcuts=\"\"`), so you decide. "
        "This build lists %d of the %d bindable commands with an id; the rest wait for the Revit spike (`dev/debug/spike_rebar_assembly.py`, probe G10), "
        "whose log supplies `RevitCommandId.Name` values to copy into `revit_command_id` in the JSON. [NV]" % (n_ids, n_bind))
    add("")
    add("To use it: in Revit open View \u203a Windows \u203a User Interface \u203a Keyboard Shortcuts, press **Import**, pick the XML, then type the keys you want. "
        "Revit shortcuts are sequences of typed keys, not chords like Tekla's; single letters and most Ctrl combinations are reserved or ambiguous in Revit, "
        "so a Tekla key cannot always be reused. The XML structure is reported by forum posts, not documented by Autodesk. [NV]")
    add("")
    add("Tekla default keys that have a one-to-one Revit command (source: Tekla Structures 2026, "
        "[Default keyboard shortcuts](https://support.tekla.com/doc/tekla-structures/2026/gen_keyboard_shortcuts)):")
    add("")
    add("| Tekla default | Tekla command | Revit equivalent | Can it be adopted? |")
    add("|---|---|---|---|")
    for key, command, revit in bridge.TEKLA_DEFAULT_SHORTCUTS:
        add("| `%s` | %s | %s | %s |" % (key, cell(command), cell(revit), cell(SHORTCUT_ADVICE[key][0])))
    add("")
    add("**Ti\u1ebfng Vi\u1ec7t**")
    add("")
    add("T3Lab **kh\u00f4ng** k\u00e8m b\u1ed9 ph\u00edm t\u1ef1 b\u1ecba. `T3Lab.extension/lib/data/KeyboardShortcuts_Tekla.xml` l\u00e0 template sinh t\u1eeb b\u1ea3n \u0111\u1ed3 l\u1ec7nh: "
        "m\u1ed7i l\u1ec7nh c\u00f3 Revit command id m\u1ed9t m\u1ee5c, ph\u00edm \u0111\u1ec3 tr\u1ed1ng (`Shortcuts=\"\"`) \u0111\u1ec3 b\u1ea1n t\u1ef1 quy\u1ebft \u0111\u1ecbnh. "
        "B\u1ea3n hi\u1ec7n t\u1ea1i c\u00f3 %d tr\u00ean %d l\u1ec7nh g\u00e1n \u0111\u01b0\u1ee3c \u0111\u00e3 c\u00f3 id; ph\u1ea7n c\u00f2n l\u1ea1i ch\u1edd spike Revit (`dev/debug/spike_rebar_assembly.py`, probe G10), "
        "log c\u1ee7a spike cho gi\u00e1 tr\u1ecb `RevitCommandId.Name` \u0111\u1ec3 copy v\u00e0o `revit_command_id` trong JSON. [NV]" % (n_ids, n_bind))
    add("")
    add("C\u00e1ch d\u00f9ng: trong Revit m\u1edf View \u203a Windows \u203a User Interface \u203a Keyboard Shortcuts, b\u1ea5m **Import**, ch\u1ecdn file XML r\u1ed3i g\u00f5 ph\u00edm b\u1ea1n mu\u1ed1n. "
        "Ph\u00edm t\u1eaft Revit l\u00e0 chu\u1ed7i ph\u00edm g\u00f5, kh\u00f4ng ph\u1ea3i t\u1ed5 h\u1ee3p nh\u01b0 c\u1ee7a Tekla; ph\u00edm \u0111\u01a1n v\u00e0 \u0111a s\u1ed1 t\u1ed5 h\u1ee3p Ctrl b\u1ecb Revit d\u00e0nh ri\u00eang ho\u1eb7c nh\u1eadp nh\u1eb1ng, "
        "n\u00ean kh\u00f4ng ph\u1ea3i ph\u00edm Tekla n\u00e0o c\u0169ng d\u00f9ng l\u1ea1i \u0111\u01b0\u1ee3c. C\u1ea5u tr\u00fac XML do di\u1ec5n \u0111\u00e0n b\u00e1o l\u1ea1i, Autodesk kh\u00f4ng t\u00e0i li\u1ec7u h\u00f3a. [NV]")
    add("")
    add("Ph\u00edm m\u1eb7c \u0111\u1ecbnh c\u1ee7a Tekla c\u00f3 l\u1ec7nh Revit t\u01b0\u01a1ng \u1ee9ng m\u1ed9t-m\u1ed9t (ngu\u1ed3n: Tekla Structures 2026, "
        "[Default keyboard shortcuts](https://support.tekla.com/doc/tekla-structures/2026/gen_keyboard_shortcuts)):")
    add("")
    add("| Ph\u00edm m\u1eb7c \u0111\u1ecbnh Tekla | L\u1ec7nh Tekla | T\u01b0\u01a1ng \u0111\u01b0\u01a1ng Revit | D\u00f9ng l\u1ea1i \u0111\u01b0\u1ee3c kh\u00f4ng? |")
    add("|---|---|---|---|")
    for key, command, revit in bridge.TEKLA_DEFAULT_SHORTCUTS:
        add("| `%s` | %s | %s | %s |" % (key, cell(command), cell(revit), cell(SHORTCUT_ADVICE[key][1])))
    add("")

    # 5 ── assembly rules
    add("## 5 \u00b7 Assembly rules / Lu\u1eadt assembly")
    add("")
    add("Every tool of the Rebar & Assembly panel follows the same eight rules, so it behaves the same when elements are inside an Assembly (Revit's cast unit). "
        "/ M\u1ecdi tool c\u1ee7a panel Rebar & Assembly theo c\u00f9ng t\u00e1m lu\u1eadt, \u0111\u1ec3 ch\u1ea1y \u0111\u00fang c\u1ea3 khi element n\u1eb1m trong Assembly (cast unit c\u1ee7a Revit).")
    add("")
    add("| # | English | Ti\u1ebfng Vi\u1ec7t |")
    add("|---|---|---|")
    for rule in ASSEMBLY_RULES:
        add("| %s | %s | %s |" % (rule[0], cell(rule[1]), cell(rule[2])))
    add("")

    # 6 ── known limits
    add("## 6 \u00b7 Known limits / Gi\u1edbi h\u1ea1n \u0111\u00e3 bi\u1ebft")
    add("")
    add("| Tool | English | Ti\u1ebfng Vi\u1ec7t |")
    add("|---|---|---|")
    for limit in KNOWN_LIMITS:
        add("| %s | %s | %s |" % (limit[0], cell(limit[1]), cell(limit[2])))
    add("")

    # 7 ── verification
    add("## 7 \u00b7 Verification status / T\u00ecnh tr\u1ea1ng x\u00e1c minh")
    add("")
    add("**English** \u2014 The tools were written without access to Revit 2027. Everything that touches Revit at run time is marked **[NV]** (needs verification) "
        "until `dev/debug/spike_rebar_assembly.py` has run inside Revit 2027 and its log has been reviewed: the ribbon names of the 2027 *Concrete Detailing* tab, "
        "the `PostableCommand` names, the Revit command ids and the keyboard-shortcuts XML structure. Where a command cannot be posted, "
        "Tekla Bridge shows its ribbon path instead of guessing.")
    add("")
    add("**Ti\u1ebfng Vi\u1ec7t** \u2014 C\u00e1c tool \u0111\u01b0\u1ee3c vi\u1ebft khi ch\u01b0a c\u00f3 Revit 2027. M\u1ecdi th\u1ee9 ch\u1ea1m Revit l\u00fac ch\u1ea1y \u0111\u1ec1u \u0111\u00e1nh d\u1ea5u **[NV]** (c\u1ea7n x\u00e1c minh) "
        "cho \u0111\u1ebfn khi `dev/debug/spike_rebar_assembly.py` ch\u1ea1y trong Revit 2027 v\u00e0 log \u0111\u01b0\u1ee3c xem l\u1ea1i: t\u00ean ribbon c\u1ee7a tab *Concrete Detailing* b\u1ea3n 2027, "
        "t\u00ean `PostableCommand`, Revit command id v\u00e0 c\u1ea5u tr\u00fac XML ph\u00edm t\u1eaft. Khi m\u1ed9t l\u1ec7nh kh\u00f4ng post \u0111\u01b0\u1ee3c, "
        "Tekla Bridge hi\u1ec3n th\u1ecb \u0111\u01b0\u1eddng d\u1eabn ribbon thay v\u00ec \u0111o\u00e1n.")
    add("")
    return "\n".join(out)


ASSEMBLY_RULES = (
    ("A1",
     "Select hosts, rebar or an assembly itself. Selecting an assembly means all of its members.",
     "C\u00f3 th\u1ec3 ch\u1ecdn host, rebar ho\u1eb7c ch\u00ednh assembly. Ch\u1ecdn assembly ngh\u0129a l\u00e0 ch\u1ecdn m\u1ecdi member c\u1ee7a n\u00f3."),
    ("A2",
     "Rebar created for a host that is inside an assembly is added to that assembly in the same step.",
     "Rebar t\u1ea1o cho host \u0111ang n\u1eb1m trong assembly \u0111\u01b0\u1ee3c th\u00eam v\u00e0o assembly \u0111\u00f3 ngay trong c\u00f9ng m\u1ed9t b\u01b0\u1edbc."),
    ("A3",
     "An element belongs to one assembly only. The tools never move members between assemblies: remove it in Edit Assembly first.",
     "M\u1ed9t element ch\u1ec9 thu\u1ed9c m\u1ed9t assembly. Tool kh\u00f4ng bao gi\u1edd chuy\u1ec3n member gi\u1eefa c\u00e1c assembly: h\u00e3y g\u1ee1 ra trong Edit Assembly tr\u01b0\u1edbc."),
    ("A4",
     "Renaming or changing assemblies can make Revit split or merge assembly types. The tools tell you how many types changed and which marks, never silently.",
     "\u0110\u1ed5i t\u00ean ho\u1eb7c s\u1eeda assembly c\u00f3 th\u1ec3 khi\u1ebfn Revit t\u00e1ch ho\u1eb7c g\u1ed9p assembly type. Tool b\u00e1o c\u00f3 bao nhi\u00eau type \u0111\u1ed5i v\u00e0 mark n\u00e0o, kh\u00f4ng bao gi\u1edd im l\u1eb7ng."),
    ("A5",
     "Elements inside a group, from a linked model, or already in another assembly are skipped and listed with the reason (for example \"skipped: in group\").",
     "Element n\u1eb1m trong group, thu\u1ed9c model li\u00ean k\u1ebft ho\u1eb7c \u0111\u00e3 n\u1eb1m trong assembly kh\u00e1c b\u1ecb b\u1ecf qua v\u00e0 \u0111\u01b0\u1ee3c li\u1ec7t k\u00ea k\u00e8m l\u00fd do (v\u00ed d\u1ee5 \"skipped: in group\")."),
    ("A6",
     "One click is one Undo step, even when many assemblies are changed.",
     "M\u1ed9t l\u1ea7n b\u1ea5m l\u00e0 m\u1ed9t b\u01b0\u1edbc Undo, k\u1ec3 c\u1ea3 khi thay \u0111\u1ed5i nhi\u1ec1u assembly."),
    ("A7",
     "Assembly views belong to their own assembly. The tools read which views and sheets exist first, so they never create duplicates.",
     "View c\u1ee7a assembly ch\u1ec9 thu\u1ed9c assembly \u0111\u00f3. Tool \u0111\u1ecdc tr\u01b0\u1edbc view v\u00e0 sheet n\u00e0o \u0111ang c\u00f3 n\u00ean kh\u00f4ng bao gi\u1edd t\u1ea1o tr\u00f9ng."),
    ("A8",
     "Every result table has an Assembly column and can be filtered to \"only in assemblies\" or \"only loose\".",
     "M\u1ecdi b\u1ea3ng k\u1ebft qu\u1ea3 \u0111\u1ec1u c\u00f3 c\u1ed9t Assembly v\u00e0 l\u1ecdc \u0111\u01b0\u1ee3c \"only in assemblies\" ho\u1eb7c \"only loose\"."),
)

KNOWN_LIMITS = (
    ("All",
     "Version 1 treats in-situ and precast the same: there is no precast mode and no embed or lifter logic.",
     "Phi\u00ean b\u1ea3n 1 coi \u0111\u1ed5 t\u1ea1i ch\u1ed7 v\u00e0 precast nh\u01b0 nhau: kh\u00f4ng c\u00f3 ch\u1ebf \u0111\u1ed9 precast, kh\u00f4ng c\u00f3 logic embed hay m\u00f3c c\u1ea9u."),
    ("BVBS Export",
     "Only BF2D is written. A bar whose centreline is not planar is reported \"skipped: free-form 3D\", and a bar with curved legs \"skipped: curved leg\". BF3D comes later.",
     "Ch\u1ec9 ghi BF2D. Thanh c\u00f3 \u0111\u01b0\u1eddng t\u00e2m kh\u00f4ng ph\u1eb3ng \u0111\u01b0\u1ee3c b\u00e1o \"skipped: free-form 3D\", thanh c\u00f3 \u0111o\u1ea1n cong \u0111\u01b0\u1ee3c b\u00e1o \"skipped: curved leg\". BF3D s\u1ebd l\u00e0m sau."),
    ("Rebar Wizard",
     "Rectangular straight beams, rectangular columns and pad footings only. Anything else (curved or sloped beams, tapered columns, walls, slabs) is listed as out of scope; use Area Reinforcement for walls and slabs.",
     "Ch\u1ec9 d\u1ea7m th\u1eb3ng ti\u1ebft di\u1ec7n ch\u1eef nh\u1eadt, c\u1ed9t ch\u1eef nh\u1eadt v\u00e0 m\u00f3ng \u0111\u01a1n. Tr\u01b0\u1eddng h\u1ee3p kh\u00e1c (d\u1ea7m cong ho\u1eb7c nghi\u00eang, c\u1ed9t thu nh\u1ecf, t\u01b0\u1eddng, s\u00e0n) \u0111\u01b0\u1ee3c li\u1ec7t k\u00ea l\u00e0 ngo\u00e0i ph\u1ea1m vi; d\u00f9ng Area Reinforcement cho t\u01b0\u1eddng v\u00e0 s\u00e0n."),
    ("Clone Drawing",
     "Tags on elements and dimensions between faces of matched host elements are re-created. Rebar-set tags, dimensions on rebar bars, spot elevations and multi-reference annotations are logged \"unmatched\" so you can add them by hand.",
     "Tag tr\u00ean element v\u00e0 dimension gi\u1eefa c\u00e1c m\u1eb7t c\u1ee7a host kh\u1edbp \u0111\u01b0\u1ee3c s\u1ebd \u0111\u01b0\u1ee3c t\u1ea1o l\u1ea1i. Tag rebar set, dimension tr\u00ean thanh rebar, spot elevation v\u00e0 multi-reference annotation \u0111\u01b0\u1ee3c ghi \"unmatched\" \u0111\u1ec3 b\u1ea1n th\u00eam tay."),
    ("Tekla Bridge",
     "It only opens commands and tools; it never edits the model. When Revit has no postable command for a row, it shows the ribbon path instead.",
     "Ch\u1ec9 m\u1edf l\u1ec7nh v\u00e0 tool, kh\u00f4ng bao gi\u1edd s\u1eeda model. Khi Revit kh\u00f4ng c\u00f3 l\u1ec7nh postable cho m\u1ed9t d\u00f2ng, n\u00f3 hi\u1ec3n th\u1ecb \u0111\u01b0\u1eddng d\u1eabn ribbon."),
    ("Unitechnik / PXML",
     "Not available in version 1; the need is noted as a request.",
     "Ch\u01b0a c\u00f3 trong phi\u00ean b\u1ea3n 1; nhu c\u1ea7u \u0111\u00e3 \u0111\u01b0\u1ee3c ghi nh\u1eadn."),
)


# ── keyboard shortcuts template ──────────────────────────────────────────

def _comment_text(text):
    """XML comments may not contain a double hyphen."""
    return str(text).replace("--", "-")


def ribbon_paths_attr(row):
    """Paths attribute: the ribbon path of the first alternative without its command, joined by '>'."""
    ribbon = row.get("ribbon_2026") or row.get("ribbon_2027") or ""
    first = ribbon.split(";")[0]
    parts = [p.strip() for p in first.split("\u203a") if p.strip()]
    if len(parts) > 1:
        parts = parts[:-1]
    return ">".join(parts)


def build_xml(bridge, rows):
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!-- GENERATED by dev/build_tekla_docs.py from lib/data/tekla_bridge.json - do not edit.",
        '     Fill the Shortcuts="" values you want, then Revit: View > User Interface > Keyboard Shortcuts > Import. -->',
        "<Shortcuts>",
    ]
    shortcut_command = dict((key, command) for key, command, _revit in bridge.TEKLA_DEFAULT_SHORTCUTS)
    for row in bridge.ordered(rows):
        if not row["postable"]:
            continue    # no Revit command to bind
        if row["tekla_shortcut"]:
            # the key belongs to the cited Tekla command, not necessarily to the row's own name
            label = "Tekla: %s (Tekla default %s)" % (
                shortcut_command.get(row["tekla_shortcut"], row["tekla"]), row["tekla_shortcut"])
        else:
            label = "Tekla: %s" % row["tekla"]
        label += " -> Revit: %s" % row["revit"]
        command_id = row["revit_command_id"]
        if command_id:
            out.append("  <!-- %s -->" % _comment_text(label))
            out.append("  <ShortcutItem CommandName=%s CommandId=%s Shortcuts=\"\" Paths=%s/>" % (
                quoteattr(row["postable"][0]), quoteattr(command_id),
                quoteattr(ribbon_paths_attr(row))))
        else:
            out.append("  <!-- %s - no command id captured yet: run dev/debug/spike_rebar_assembly.py -->"
                       % _comment_text(label))
    out.append("</Shortcuts>")
    return "\n".join(out) + "\n"


# ── main ─────────────────────────────────────────────────────────────────

def generate():
    bridge = load_bridge()
    rows = bridge.load_rows()
    markdown = build_markdown(bridge, rows)
    return [(DOCS_OUT, markdown if markdown.endswith("\n") else markdown + "\n"),
            (XML_OUT, build_xml(bridge, rows))]


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError:
        return None


def main():
    check = "--check" in sys.argv
    try:
        outputs = generate()
    except (OSError, ValueError) as exc:
        print("build_tekla_docs: %s" % exc)
        return 1

    stale = []
    for path, text in outputs:
        if _read(path) == text:
            continue
        stale.append(path)
        if not check:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)

    rel = lambda p: os.path.relpath(p, REPO).replace(os.sep, "/")
    if check:
        for path in stale:
            print("  %-60s lech voi tekla_bridge.json" % rel(path))
        print("TEKLA DOCS: %d file - %d lech" % (len(outputs), len(stale)))
        if stale:
            print("            chay `python3 dev/build_tekla_docs.py` de dong bo")
            return 1
        return 0
    for path in stale:
        print("  %-60s da cap nhat" % rel(path))
    print("TEKLA DOCS: %d file - %d cap nhat" % (len(outputs), len(stale)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
