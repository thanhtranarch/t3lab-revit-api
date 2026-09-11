# -*- coding: utf-8 -*-
"""Build icon ribbon T3Lab: sinh ban dark + render PNG 64x64.

    python3 dev/build_icons.py            # build het (tru bundle mien tru)
    python3 dev/build_icons.py --check    # khong ghi gi, bao file nao lech
    python3 dev/build_icons.py --only ManaSheets ManaWorkset

Nguon duy nhat la `icon.svg` (viewBox 0 0 32 32). Ba thu con lai deu sinh ra:

    icon.svg  --(bang token)-->  icon.dark.svg
        |                              |
        +--(resvg 2x)--> icon.png      +--(resvg 2x)--> icon.dark.png

TUYET DOI khong sua tay `icon.dark.svg` / `*.png` - lan build sau ghi de.

Thay cho `dev/svg_to_png.py`, `dev/svg_to_png.js`, `dev/gen_dark_icons.js`
(ca ba hardcode danh sach tool va da lech thuc te).

Chuan: docs/ui-governance/09-ribbon-icon-standard.md
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons"))

import iconlib  # noqa: E402


def dark_header():
    return (
        '<!-- %s - KHONG sua tay file nay.\n'
        '     Nguon: icon.svg . Bang mau: dev/icons/tokens.json -->\n'
        % iconlib.GENERATED_MARK
    )


def build(only=None, check=False):
    cfg, light_to_dark, allowed, _names = iconlib.load_tokens()
    size = cfg["png_size"]

    bundles = iconlib.find_bundles()
    if only:
        wanted = {o.lower() for o in only}
        bundles = [b for b in bundles
                   if any(w in b.name.lower() or w in b.rel.lower() for w in wanted)]
        if not bundles:
            print("khong khop bundle nao voi: %s" % ", ".join(only))
            return 2

    jobs = []
    stale = []
    skipped_unmigrated = []
    missing_source = []

    for b in bundles:
        if not b.has(iconlib.ICON_LIGHT_SVG):
            missing_source.append(b)
            continue

        light_svg = b.read(iconlib.ICON_LIGHT_SVG)

        # Icon con o he cu (navy + cam) thi khong sinh dark - bang token khong
        # phu duoc mau cua no, sinh ra se sai mau. Cho toi luot ve lai.
        if not iconlib.is_migrated(light_svg, allowed):
            skipped_unmigrated.append(b)
            continue

        dark_svg = dark_header() + iconlib.to_dark(light_svg, light_to_dark)

        dark_path = b.file(iconlib.ICON_DARK_SVG)
        current = b.read(iconlib.ICON_DARK_SVG) if b.has(iconlib.ICON_DARK_SVG) else None
        if current != dark_svg:
            stale.append(b.rel + "/" + iconlib.ICON_DARK_SVG)
            if not check:
                with open(dark_path, "w", encoding="utf-8") as fh:
                    fh.write(dark_svg)

        if check:
            for png_name in (iconlib.ICON_LIGHT_PNG, iconlib.ICON_DARK_PNG):
                dims = iconlib.png_size(b.file(png_name))
                if dims != (size, size):
                    stale.append("%s/%s (%s)" % (b.rel, png_name,
                                                 "thieu" if dims is None else "%dx%d" % dims))
        else:
            jobs.append({"svg": b.file(iconlib.ICON_LIGHT_SVG),
                         "png": b.file(iconlib.ICON_LIGHT_PNG), "size": size})
            jobs.append({"svg": dark_path,
                         "png": b.file(iconlib.ICON_DARK_PNG), "size": size})

    rendered = 0
    if jobs:
        rendered = _render(jobs)
        if rendered is None:
            return 1

    # ── bao cao ─────────────────────────────────────────────────────────
    print("icon ribbon  -  %d bundle" % len(bundles))
    print("MIEN TRU: %s" % iconlib.EXEMPT_REASON)

    if check:
        if stale:
            print("\nLECH (%d) - chay `python3 dev/build_icons.py`:" % len(stale))
            for s in stale:
                print("  %s" % s)
        else:
            print("build khop - khong co gi lech")
    else:
        print("da render %d PNG tu %d icon" % (rendered, rendered // 2))

    if skipped_unmigrated:
        print("\nchua migrate (%d) - con dung he mau cu, bo qua:"
              % len(skipped_unmigrated))
        for b in skipped_unmigrated:
            print("  %-8s %s" % ("tier " + b.tier, b.rel))

    if missing_source:
        print("\nkhong co icon.svg (%d) - phai ve moi:" % len(missing_source))
        for b in missing_source:
            print("  %-8s %s" % ("tier " + b.tier, b.rel))

    return 1 if (check and stale) else 0


def _render(jobs):
    """Goi dev/icons/render.js. Tra ve so PNG da ghi, None neu hong."""
    fd, jobs_file = tempfile.mkstemp(suffix=".json", prefix="t3icons_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(jobs, fh)
        try:
            proc = subprocess.run(["node", iconlib.RENDER_JS, jobs_file],
                                  capture_output=True, text=True, cwd=iconlib.REPO)
        except OSError as exc:
            print("khong goi duoc node: %s" % exc)
            print("can Node.js + @resvg/resvg-js (`npm install @resvg/resvg-js`)")
            return None

        out = (proc.stdout or "").strip().splitlines()
        try:
            result = json.loads(out[-1]) if out else {}
        except ValueError:
            print("render.js tra ve output la:\n%s\n%s" % (proc.stdout, proc.stderr))
            return None

        for fail in result.get("failures", []):
            print("render hong: %s\n  %s" % (fail["svg"], fail["error"]))
        if result.get("failures"):
            return None
        return result.get("rendered", 0)
    finally:
        if os.path.exists(jobs_file):
            os.remove(jobs_file)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="khong ghi gi, chi bao file nao lech so voi icon.svg")
    ap.add_argument("--only", nargs="+", metavar="TEN",
                    help="chi build bundle co ten khop")
    args = ap.parse_args()
    sys.exit(build(only=args.only, check=args.check))


if __name__ == "__main__":
    main()
