#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproduce the pyRevit XAML load path outside Revit.

`audit_t3.py` parses the XAML with ElementTree, which only proves the file is
well-formed on disk. pyRevit does something else first: `WPF_Base._sanitize_xaml`
rewrites the text with a regex sweep to strip event handlers, and only THEN does
`XamlReader` see it. A file can therefore be perfectly valid on disk and broken
by the time WPF parses it, and nothing in the gates would notice.

This runs the REAL sanitiser -- imported out of the shipped source, not a copy,
so it cannot drift -- and checks the result is still well-formed. Pair it with
`dev/check_xaml_wpf.ps1 -Dir <out>` to feed the sanitised output through the
actual WPF parser.

    python3 dev/check_xaml_load.py               # check only
    python3 dev/check_xaml_load.py --out DIR     # also write sanitised copies
"""
import glob
import io
import os
import re
import sys
import xml.dom.minidom as minidom

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WPF_BASE = os.path.join(REPO, "T3Lab.extension", "lib", "GUI", "WPF_Base.py")
TOOLS = os.path.join(REPO, "T3Lab.extension", "lib", "GUI", "Tools")


def load_sanitiser():
    """`_sanitize_xaml` lifted out of WPF_Base without importing the CLR."""
    src = io.open(WPF_BASE, encoding="utf-8").read()
    names = src[src.index("_EVENT_NAMES = {"):src.index("def to_items_source")]
    body = src[src.index("def _sanitize_xaml("):src.index("def setup_window_logo(")]
    namespace = {"re": re}
    exec(compile(names, WPF_BASE, "exec"), namespace)
    exec(compile(body, WPF_BASE, "exec"), namespace)
    return namespace["_sanitize_xaml"]


def main():
    out_dir = None
    if "--out" in sys.argv:
        out_dir = sys.argv[sys.argv.index("--out") + 1]
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)

    sanitize = load_sanitiser()
    files = sorted(glob.glob(os.path.join(TOOLS, "*.xaml")))
    broken = []

    for path in files:
        base = os.path.basename(path)
        raw = io.open(path, encoding="utf-8").read().lstrip("﻿ \t\r\n")
        clean, _bindings, _named = sanitize(raw)
        if out_dir:
            io.open(os.path.join(out_dir, base), "w", encoding="utf-8").write(clean)
        try:
            minidom.parseString(clean.encode("utf-8"))
        except Exception as exc:
            broken.append((base, str(exc).splitlines()[0]))

    for base, err in broken:
        print("BROKEN AFTER SANITISE  %s" % base)
        print("      %s" % err)

    print("")
    print("XAML load path: %d file · %d hỏng sau khi sanitise"
          % (len(files), len(broken)))
    if out_dir:
        print("sanitised → %s" % out_dir)
        print("chạy tiếp: powershell -STA -File dev/check_xaml_wpf.ps1 -Dir \"%s\""
              % out_dir)
    print("          OK" if not broken else "          FAILED")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
