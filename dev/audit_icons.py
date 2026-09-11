# -*- coding: utf-8 -*-
"""Gate icon ribbon T3Lab.

    python3 dev/audit_icons.py            # bao cao day du
    python3 dev/audit_icons.py --quiet    # chi in loi + mot dong ket
    python3 dev/audit_icons.py --debt     # liet ke no migration

Luat: docs/ui-governance/09-ribbon-icon-standard.md

Dau hieu "da migrate" la `viewBox="0 0 32 32"` - co tinh khong lay bang mau
lam dau hieu, vi nhu vay chi can them mot ma mau la la icon tu dong thoat
khoi moi luat con lai.

Trong qua trinh chuyen doi, icon con o he cu duoc ghi vao muc NO MIGRATION va
khong tinh la loi (`STRICT = False`) de gate van xanh. Het no thi bat STRICT -
da bat tu 2026-09-11.

Bon bundle duoc MIEN TRU co chu dich (3 logo hang khac + mascot T3LabAssistant)
- xem muc 7 cua chuan. In ra moi lan chay de khong ai tuong la viec con bo sot.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons"))

import iconlib  # noqa: E402

# Bat tu 2026-09-11: ca 38 bundle da migrate, khong con no.
# Tu day mot icon chua theo chuan la P0 - gate do ngay, khong cho troi tiep.
STRICT = True

# ── Bieu thuc soi SVG ────────────────────────────────────────────────────
RX_RE = re.compile(r'\b(rx|ry)\s*=')
OPACITY_RE = re.compile(r'\b(fill-opacity|stroke-opacity|opacity)\s*=')
EFFECT_RE = re.compile(r'<(linearGradient|radialGradient|filter|feGaussianBlur)\b'
                       r'|\bfilter\s*=')
DASH_RE = re.compile(r'\bstroke-dasharray\s*=')
STROKE_W_RE = re.compile(r'stroke-width\s*=\s*"([^"]+)"')
SHAPE_RE = re.compile(r'<(rect|circle|ellipse|line|polyline|polygon|path)\b')
ELEMENT_RE = re.compile(r'<(rect|line)\b([^>]*)>')
ATTR_RE = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')

MIN_FEATURE_TIER_B = 2.0
MAX_SHAPES = {"A": 6, "B": 4}


class Report(object):
    def __init__(self):
        self.issues = []      # (severity, bundle_rel, message)
        self.debt = []        # (kind, bundle)
        self.migrated = 0
        self.checked = 0

    def add(self, sev, bundle, msg):
        self.issues.append((sev, bundle.rel, msg))

    @property
    def failures(self):
        return [i for i in self.issues if i[0] in ("P0", "P1")]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _on_half_grid(value):
    """Tam net phai nam tren toa do .5 thi net 1 unit moi an dung pixel."""
    return abs((value - 0.5) - round(value - 0.5)) < 1e-6


def check_geometry(svg, bundle, rep, allowed, cfg):
    """Soi hinh hoc va bang mau cua mot icon da migrate."""
    # ── mau ─────────────────────────────────────────────────────────────
    bad = sorted({h for h in iconlib.hexes_in(svg) if h not in allowed})
    if bad:
        rep.add("P0", bundle, "mau ngoai bang token: %s" % ", ".join(bad))

    # ── thuoc tinh bi cam ───────────────────────────────────────────────
    if RX_RE.search(svg):
        rep.add("P1", bundle, "co rx/ry - goc phai vuong")
    if OPACITY_RE.search(svg):
        rep.add("P1", bundle, "co opacity - Revit khong dung opacity trong icon")
    if EFFECT_RE.search(svg):
        rep.add("P1", bundle, "co gradient/filter - cam")
    if DASH_RE.search(svg):
        rep.add("P1", bundle, "co stroke-dasharray - cam")

    # ── do day net ──────────────────────────────────────────────────────
    allowed_w = set(cfg["stroke_widths"])
    for m in STROKE_W_RE.finditer(svg):
        w = _num(m.group(1))
        if w is None or w not in allowed_w:
            rep.add("P1", bundle, "stroke-width=%s - chi cho phep %s"
                    % (m.group(1), "/".join(str(x) for x in sorted(allowed_w))))

    # ── so shape ────────────────────────────────────────────────────────
    n_shapes = len(SHAPE_RE.findall(svg))
    cap = MAX_SHAPES[bundle.tier]
    if n_shapes > cap:
        rep.add("P2", bundle, "%d shape - tier %s toi da %d"
                % (n_shapes, bundle.tier, cap))

    # ── toa do net va kich thuoc chi tiet ───────────────────────────────
    for m in ELEMENT_RE.finditer(svg):
        tag = m.group(1)
        attrs = dict(ATTR_RE.findall(m.group(2)))
        stroked = attrs.get("stroke", "none") not in ("none", "")

        if stroked:
            coords = (("x", "y") if tag == "rect" else ("x1", "y1", "x2", "y2"))
            for key in coords:
                v = _num(attrs.get(key))
                if v is not None and not _on_half_grid(v):
                    rep.add("P1", bundle,
                            '<%s %s="%s"> - tam net phai nam tren toa do .5'
                            % (tag, key, attrs[key]))
        elif tag == "rect" and bundle.tier == "B":
            w, h = _num(attrs.get("width")), _num(attrs.get("height"))
            small = [n for n, v in (("width", w), ("height", h))
                     if v is not None and v < MIN_FEATURE_TIER_B]
            if small:
                rep.add("P2", bundle,
                        "tier B: mang dac %s < %g unit - se bien mat o 24px"
                        % ("/".join(small), MIN_FEATURE_TIER_B))


def check_outputs(bundle, rep, light_to_dark, size):
    """Soi ban dark + PNG: phai la ket qua cua build, khong phai sua tay."""
    light_svg = bundle.read(iconlib.ICON_LIGHT_SVG)

    if not bundle.has(iconlib.ICON_DARK_SVG):
        rep.add("P0", bundle, "thieu icon.dark.svg - chay dev/build_icons.py")
    else:
        dark = bundle.read(iconlib.ICON_DARK_SVG)
        if iconlib.GENERATED_MARK not in dark:
            rep.add("P1", bundle, "icon.dark.svg thieu dau sinh tu dong "
                                  "- co ve da bi sua tay")
        body = dark.split("-->", 1)[-1] if "-->" in dark else dark
        if body.strip() != iconlib.to_dark(light_svg, light_to_dark).strip():
            rep.add("P0", bundle, "icon.dark.svg lech voi icon.svg "
                                  "- chay dev/build_icons.py")

    svg_mtime = os.path.getmtime(bundle.file(iconlib.ICON_LIGHT_SVG))
    for png_name in (iconlib.ICON_LIGHT_PNG, iconlib.ICON_DARK_PNG):
        path = bundle.file(png_name)
        if not os.path.isfile(path):
            rep.add("P0", bundle, "thieu %s - chay dev/build_icons.py" % png_name)
            continue
        dims = iconlib.png_size(path)
        if dims != (size, size):
            rep.add("P0", bundle, "%s la %s - phai %dx%d"
                    % (png_name, "khong doc duoc" if dims is None
                       else "%dx%d" % dims, size, size))
        elif os.path.getmtime(path) < svg_mtime - 1:
            rep.add("P2", bundle, "%s cu hon icon.svg - build chua chay" % png_name)


def audit():
    cfg, light_to_dark, allowed, _names = iconlib.load_tokens()
    size = cfg["png_size"]
    rep = Report()

    for b in iconlib.find_bundles():
        rep.checked += 1

        if not b.has(iconlib.ICON_LIGHT_SVG):
            # Chi con PNG - khong re-theme duoc, phai ve moi.
            if not b.has(iconlib.ICON_LIGHT_PNG):
                rep.add("P0", b, "khong co icon nao ca")
            rep.debt.append(("khong co icon.svg", b))
            if STRICT:
                rep.add("P0", b, "thieu icon.svg")
            continue

        svg = b.read(iconlib.ICON_LIGHT_SVG)
        if not iconlib.is_migrated(svg):
            rep.debt.append(("chua migrate (viewBox %s)"
                             % (iconlib.viewbox(svg) or "?"), b))
            if STRICT:
                rep.add("P0", b, "chua migrate sang viewBox 0 0 32 32")
            continue

        rep.migrated += 1
        check_geometry(svg, b, rep, allowed, cfg)
        check_outputs(b, rep, light_to_dark, size)

    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quiet", action="store_true", help="chi in loi + dong ket")
    ap.add_argument("--debt", action="store_true", help="liet ke no migration")
    args = ap.parse_args()

    rep = audit()
    order = {"P0": 0, "P1": 1, "P2": 2}
    rep.issues.sort(key=lambda i: (order[i[0]], i[1]))

    if not args.quiet:
        print("MIEN TRU (%d bundle): %s"
              % (len(iconlib.EXEMPT_BUNDLES), iconlib.EXEMPT_REASON))
        for rel in sorted(iconlib.EXEMPT_BUNDLES):
            print("          %s" % rel)
        print("")

    for sev, rel, msg in rep.issues:
        print("%s  %-58s %s" % (sev, rel, msg))

    if args.debt or (not args.quiet and rep.debt):
        print("")
        print("NO MIGRATION (%d/%d bundle):" % (len(rep.debt), rep.checked))
        for kind, b in rep.debt:
            print("  tier %s  %-52s %s" % (b.tier, b.rel, kind))

    fails = rep.failures
    print("")
    print("%s  %d/%d da migrate - %d loi, %d canh bao, %d no"
          % ("FAIL" if fails else "OK  ",
             rep.migrated, rep.checked,
             len(fails), len(rep.issues) - len(fails), len(rep.debt)))

    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
