#! python3
# -*- coding: utf-8 -*-
"""
Image To Drafting View  v4.0
----------------------------
Accepts images (JPG/PNG/BMP) OR PDF files.

Pipeline
  1. Load source  : image → BMP  |  PDF page → BMP (via Ghostscript)
  2. Preprocess   : threshold → safe interior erosion (thin lines preserved)
  3. Potrace      : thinned BMP → SVG
  4. Parse SVG    : cursor-based parser — correctly handles implicit command
                   repetition without double-counting number tokens
  5. Coord map    : SVG (top-left / Y-down) → Revit (bottom-left / Y-up)
  6. Dedup        : merge near-duplicate pairs (residual double outlines)
  7. Create Revit detail lines on a new Drafting View

PDF support (v4.0)
  - Requires Ghostscript installed (https://www.ghostscript.com/)
  - Auto-detected from common Windows install paths and PATH
  - Page count read by scanning PDF binary for /Count
  - Preview rendered at 72 dpi; final export at 150 dpi

Author: Tran Tien Thanh
"""

__title__   = "Image To Drafting"
__author__  = "Tran Tien Thanh"
__version__ = "4.0.0"

import os
import sys
# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
for _env in ('APPDATA', 'PROGRAMDATA'):
    _base = os.environ.get(_env, '')
    if _base:
        for _clone in ('pyRevit-Master', 'pyRevit'):
            _ceng = os.path.join(_base, _clone, 'bin', 'cengines', 'CPY3123')
            if os.path.isdir(_ceng):
                for _d in (_ceng, os.path.join(_ceng, 'Lib')):
                    if hasattr(os, 'add_dll_directory'):
                        try:
                            os.add_dll_directory(_d)
                        except Exception:
                            pass
                for _p in (_ceng, os.path.join(_ceng, 'Lib'), os.path.join(_ceng, 'python312.zip')):
                    if os.path.exists(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)

_cur = os.path.dirname(os.path.abspath(__file__))
while _cur and not os.path.exists(os.path.join(_cur, 'lib')):
    _parent = os.path.dirname(_cur)
    if _parent == _cur:
        break
    _cur = _parent
_lib_dir = os.path.join(_cur, 'lib')
if os.path.exists(_lib_dir) and _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────

import re
import glob as _glob
import tempfile
import time
import subprocess
import xml.etree.ElementTree as ET

import clr
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')
clr.AddReference('System')
clr.AddReference('System.Drawing')
clr.AddReference('Microsoft.CSharp')

from System.Windows import Clipboard, WindowState, Visibility
from System.Windows.Media.Imaging import BitmapImage, BmpBitmapEncoder, BitmapFrame
from System.IO import FileStream, FileMode
from System import Uri
import Microsoft.Win32

from pyrevit import revit, DB, forms, script
from GUI.WPF_Base import T3WPFWindow
# Put the extension's lib/ on sys.path so GUI.ProgressPauseMixin imports
# (mirror main()'s "walk up to T3Lab.extension" logic).
_ext_dir = os.path.dirname(__file__)
while _ext_dir and not _ext_dir.endswith('T3Lab.extension'):
    _parent = os.path.dirname(_ext_dir)
    if _parent == _ext_dir:
        break
    _ext_dir = _parent
_lib_dir = os.path.join(_ext_dir, 'lib')
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from GUI.ProgressPauseMixin import ProgressPauseMixin

# `revit.doc` / `revit.uidoc` RAISE AttributeError (not return None) when no
# UIDocument is active. At module scope that kills the import outright, so the
# tool dies before it can explain itself. Resolve defensively and let the entry
# point report the real problem.
try:
    doc = revit.doc
except Exception:
    doc = None


# ══════════════════════════════════════════════════════════════════════════════
# PDF HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def find_ghostscript():
    """
    Locate gswin64c.exe / gswin32c.exe on Windows.
    Checks common Program Files install paths, then falls back to PATH.
    Returns full path string, or None if not found.
    """
    patterns = [
        r"C:\Program Files\gs\gs*\bin\gswin64c.exe",
        r"C:\Program Files (x86)\gs\gs*\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs*\bin\gswin32c.exe",
        r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe",
    ]
    for pat in patterns:
        matches = _glob.glob(pat)
        if matches:
            return sorted(matches)[-1]   # newest version last alphabetically

    # Try bare executable name (must be in PATH)
    for exe in ('gswin64c', 'gswin32c', 'gs'):
        try:
            proc = subprocess.Popen(
                [exe, '--version'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False
            )
            proc.communicate()
            if proc.returncode == 0:
                return exe
        except Exception:
            pass
    return None


def get_pdf_page_count(pdf_path):
    """
    Quick page count: scan the PDF binary for /Count N entries and
    return the maximum value found (= total page count in document).
    Returns 1 if nothing found.
    """
    try:
        with open(pdf_path, 'rb') as f:
            data = f.read()
        text = data.decode('latin-1', errors='replace')
        counts = [int(m.group(1)) for m in re.finditer(r'/Count\s+(\d+)', text)]
        if counts:
            return max(counts)
    except Exception:
        pass
    return 1


def pdf_to_bmp(pdf_path, page_num, dpi=150, gs_path=None):
    """
    Convert one PDF page to a grayscale BMP using Ghostscript.
    Returns (bmp_path, None) on success, (None, error_msg) on failure.
    """
    out_path = os.path.join(
        tempfile.gettempdir(),
        "t3lab_pdf_{}_p{}.bmp".format(int(time.time() * 1000), page_num)
    )
    cmd = [
        gs_path,
        '-dBATCH', '-dNOPAUSE', '-dQUIET',
        '-sDEVICE=bmpgray',
        '-r{}'.format(dpi),
        '-dFirstPage={}'.format(page_num),
        '-dLastPage={}'.format(page_num),
        '-sOutputFile={}'.format(out_path),
        pdf_path,
    ]
    try:
        subprocess.call(cmd, shell=False)
    except Exception as exc:
        return None, "Ghostscript call failed: {}".format(exc)
    if not os.path.exists(out_path):
        return None, "Ghostscript produced no output."
    return out_path, None


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════════

def preprocess_image(input_bmp, threshold=210, line_thickness_px=2):
    """
    Threshold to binary, then apply "safe interior erosion":
      A pixel is removed only when ALL four 4-connected neighbours are
      foreground — i.e. it is strictly interior to a thick blob.
      Edge pixels and thin lines are never touched.

    erode_passes = max(0, line_thickness_px // 3)
      thickness 1-2 → 0 passes (threshold only)
      thickness 3-5 → 1 pass
      thickness 6-8 → 2 passes

    Returns (processed_bmp_path, w, h).
    Falls back to input_bmp if System.Drawing is unavailable.
    """
    erode_passes = max(0, line_thickness_px // 3)

    try:
        clr.AddReference('System.Drawing')
        import System
        from System.Drawing import Bitmap, Rectangle
        from System.Drawing.Imaging import PixelFormat, ImageLockMode
        from System.Runtime.InteropServices import Marshal

        src   = Bitmap(input_bmp)
        w, h  = src.Width, src.Height
        fmt   = PixelFormat.Format32bppArgb
        src32 = src.Clone(Rectangle(0, 0, w, h), fmt)
        src.Dispose()

        bd     = src32.LockBits(Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, fmt)
        stride = bd.Stride
        raw    = System.Array.CreateInstance(System.Byte, stride * h)
        Marshal.Copy(bd.Scan0, raw, 0, len(raw))
        src32.UnlockBits(bd)
        src32.Dispose()

        # ── threshold ─────────────────────────────────────────────────────────
        grid = bytearray(w * h)
        for y in range(h):
            base = y * stride
            for x in range(w):
                r = raw[base + x * 4 + 2]
                g = raw[base + x * 4 + 1]
                b = raw[base + x * 4 + 0]
                if (r * 299 + g * 587 + b * 114) // 1000 < threshold:
                    grid[y * w + x] = 1

        # ── safe interior erosion ─────────────────────────────────────────────
        for _ in range(erode_passes):
            new_g = bytearray(grid)
            for y in range(1, h - 1):
                row = y * w
                for x in range(1, w - 1):
                    i = row + x
                    if (grid[i] and
                            grid[i - w] and grid[i + w] and
                            grid[i - 1] and grid[i + 1]):
                        new_g[i] = 0
            grid = new_g

        # ── write output BMP ──────────────────────────────────────────────────
        out_bmp = Bitmap(w, h)
        bd      = out_bmp.LockBits(Rectangle(0, 0, w, h), ImageLockMode.WriteOnly, fmt)
        strd_o  = bd.Stride
        buf_o   = System.Array.CreateInstance(System.Byte, strd_o * h)
        for y in range(h):
            base = y * strd_o
            for x in range(w):
                v = 0 if grid[y * w + x] else 255
                buf_o[base + x * 4 + 0] = v
                buf_o[base + x * 4 + 1] = v
                buf_o[base + x * 4 + 2] = v
                buf_o[base + x * 4 + 3] = 255
        Marshal.Copy(buf_o, 0, bd.Scan0, len(buf_o))
        out_bmp.UnlockBits(bd)

        out_path = input_bmp.replace('.bmp', '_pre.bmp')
        out_bmp.Save(out_path)
        out_bmp.Dispose()
        return out_path, w, h

    except Exception as exc:
        print("Preprocessing skipped: {}".format(exc))
        return input_bmp, 0, 0


# ══════════════════════════════════════════════════════════════════════════════
# SVG PATH PARSER  (cursor-based — no double-counting)
# ══════════════════════════════════════════════════════════════════════════════

_TOK_RE = re.compile(
    r'([MmLlHhVvCcSsQqTtZz])'
    r'|(-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)'
)

_CMD_ARGC = {
    'M':2,'m':2,'L':2,'l':2,'H':1,'h':1,'V':1,'v':1,
    'C':6,'c':6,'S':4,'s':4,'Q':4,'q':4,'T':2,'t':2,
    'Z':0,'z':0,
}


def _cubic(p0, p1, p2, p3, t):
    mt = 1.0 - t
    return (mt**3*p0[0]+3*mt**2*t*p1[0]+3*mt*t**2*p2[0]+t**3*p3[0],
            mt**3*p0[1]+3*mt**2*t*p1[1]+3*mt*t**2*p2[1]+t**3*p3[1])


def _tess(p0, p1, p2, p3, steps=8):
    return [_cubic(p0, p1, p2, p3, k / float(steps)) for k in range(steps + 1)]


def parse_svg_path(d):
    """
    Cursor-based SVG path parser.

    State: pos[0] walks through all tokens (cmd + num) in order.
    When a command is seen, pos advances past the command character and then
    take(n) reads exactly n subsequent number tokens — no separate array,
    no double-counting.
    """
    tokens = []
    for m in _TOK_RE.finditer(d):
        if m.group(1):
            tokens.append(('cmd', m.group(1)))
        else:
            tokens.append(('num', float(m.group(2))))

    pos = [0]          # mutable cursor (IronPython 2.7 has no nonlocal)
    n   = len(tokens)

    def peek_type():
        return tokens[pos[0]][0] if pos[0] < n else None

    def take(count):
        """Consume exactly `count` number tokens; return list or None."""
        end = pos[0] + count
        if end > n:
            return None
        vals = []
        for k in range(pos[0], end):
            if tokens[k][0] != 'num':
                return None
            vals.append(tokens[k][1])
        pos[0] = end
        return vals

    polylines = []
    current   = []
    cx = cy   = 0.0
    sx = sy   = 0.0
    lx1 = ly1 = None
    cmd       = None

    while pos[0] < n:
        t_type, t_val = tokens[pos[0]]

        # ── consume command character ────────────────────────────────────────
        if t_type == 'cmd':
            cmd = t_val
            pos[0] += 1

        # ── implicit lineto after moveto ─────────────────────────────────────
        elif cmd == 'M':
            cmd = 'L'
        elif cmd == 'm':
            cmd = 'l'

        # ── no pending command → skip stray number ───────────────────────────
        if cmd is None:
            pos[0] += 1
            continue

        # ── close path (no arguments) ────────────────────────────────────────
        if cmd in ('Z', 'z'):
            if current:
                current.append((sx, sy))
            if len(current) >= 2:
                polylines.append(list(current))
            current = [(sx, sy)]
            cx, cy  = sx, sy
            lx1 = ly1 = None
            cmd = None
            continue

        # ── stop if no numbers available ─────────────────────────────────────
        if peek_type() != 'num':
            cmd = None
            continue

        argc = _CMD_ARGC.get(cmd, 0)
        v    = take(argc)
        if v is None:
            cmd = None
            continue

        ABS = cmd.isupper()

        # ── dispatch ─────────────────────────────────────────────────────────
        if cmd in ('M', 'm'):
            cx2 = v[0] if ABS else cx + v[0]
            cy2 = v[1] if ABS else cy + v[1]
            if current:
                if len(current) >= 2:
                    polylines.append(list(current))
                current = []
            cx, cy = cx2, cy2
            sx, sy = cx, cy
            current.append((cx, cy))
            lx1 = ly1 = None
            cmd = 'L' if ABS else 'l'

        elif cmd in ('L', 'l'):
            cx = v[0] if ABS else cx + v[0]
            cy = v[1] if ABS else cy + v[1]
            current.append((cx, cy))
            lx1 = ly1 = None

        elif cmd in ('H', 'h'):
            cx = v[0] if ABS else cx + v[0]
            current.append((cx, cy)); lx1 = ly1 = None

        elif cmd in ('V', 'v'):
            cy = v[0] if ABS else cy + v[0]
            current.append((cx, cy)); lx1 = ly1 = None

        elif cmd in ('C', 'c'):
            x1, y1, x2, y2, x, y = v
            if not ABS: x1+=cx; y1+=cy; x2+=cx; y2+=cy; x+=cx; y+=cy
            pts = _tess((cx,cy),(x1,y1),(x2,y2),(x,y))
            current.extend(pts[1:])
            lx1, ly1 = x2, y2; cx, cy = x, y

        elif cmd in ('S', 's'):
            x2, y2, x, y = v
            if not ABS: x2+=cx; y2+=cy; x+=cx; y+=cy
            rx1 = 2*cx - lx1 if lx1 is not None else cx
            ry1 = 2*cy - ly1 if ly1 is not None else cy
            pts = _tess((cx,cy),(rx1,ry1),(x2,y2),(x,y))
            current.extend(pts[1:]); lx1,ly1=x2,y2; cx,cy=x,y

        elif cmd in ('Q', 'q'):
            x1, y1, x, y = v
            if not ABS: x1+=cx; y1+=cy; x+=cx; y+=cy
            cp1 = (cx+2*(x1-cx)/3., cy+2*(y1-cy)/3.)
            cp2 = (x +2*(x1-x )/3., y +2*(y1-y )/3.)
            pts = _tess((cx,cy),cp1,cp2,(x,y))
            current.extend(pts[1:]); lx1,ly1=x1,y1; cx,cy=x,y

        elif cmd in ('T', 't'):
            x, y = v
            if not ABS: x+=cx; y+=cy
            rx1 = 2*cx - lx1 if lx1 is not None else cx
            ry1 = 2*cy - ly1 if ly1 is not None else cy
            cp1 = (cx+2*(rx1-cx)/3., cy+2*(ry1-cy)/3.)
            cp2 = (x +2*(rx1-x )/3., y +2*(ry1-y )/3.)
            pts = _tess((cx,cy),cp1,cp2,(x,y))
            current.extend(pts[1:]); lx1,ly1=rx1,ry1; cx,cy=x,y

    if len(current) >= 2:
        polylines.append(current)
    return polylines


# ══════════════════════════════════════════════════════════════════════════════
# SEGMENT DEDUPLICATION
# ══════════════════════════════════════════════════════════════════════════════

def dedup_segments(segs, merge_px):
    """
    Merge near-duplicate segment pairs (potrace double-outline artifacts).
    Two segments are merged when BOTH endpoint pairs are within merge_px of
    each other (forward or reverse orientation).
    Returns a deduplicated list with averages replacing merged pairs.
    """
    merge_sq = float(merge_px) ** 2
    n        = len(segs)
    used     = bytearray(n)
    out      = []

    def d2(a, b):
        return (a[0]-b[0])**2 + (a[1]-b[1])**2

    for i in range(n):
        if used[i]:
            continue
        (x1,y1),(x2,y2) = segs[i]
        merged = False
        for j in range(i+1, n):
            if used[j]:
                continue
            (x3,y3),(x4,y4) = segs[j]
            fwd = d2((x1,y1),(x3,y3)) < merge_sq and d2((x2,y2),(x4,y4)) < merge_sq
            rev = d2((x1,y1),(x4,y4)) < merge_sq and d2((x2,y2),(x3,y3)) < merge_sq
            if fwd:
                out.append((((x1+x3)/2.,(y1+y3)/2.),((x2+x4)/2.,(y2+y4)/2.)))
                used[i] = used[j] = 1; merged = True; break
            elif rev:
                out.append((((x1+x4)/2.,(y1+y4)/2.),((x2+x3)/2.,(y2+y3)/2.)))
                used[i] = used[j] = 1; merged = True; break
        if not merged:
            out.append(segs[i]); used[i] = 1
    return out


# ══════════════════════════════════════════════════════════════════════════════
# SVG FILE → REVIT SEGMENTS
# ══════════════════════════════════════════════════════════════════════════════

def svg_to_revit_segments(svg_path, target_width_mm, line_thickness_px):
    """
    Parse potrace SVG → list of (DB.XYZ, DB.XYZ) in Revit feet.

    Coordinate mapping
      SVG  : origin top-left,    Y ↓
      Revit: origin bottom-left, Y ↑
        x_r = x_svg  * scale
        y_r = (svg_h - y_svg) * scale
    """
    try:
        tree = ET.parse(svg_path)
    except Exception as exc:
        return [], str(exc)

    root = tree.getroot()
    ns   = root.tag[:root.tag.index('}')+1] if root.tag.startswith('{') else ''

    # ── canvas dimensions ─────────────────────────────────────────────────────
    svg_w = svg_h = 0.0
    vb = root.get('viewBox', '')
    if vb:
        parts = vb.split()
        if len(parts) == 4:
            try: svg_w, svg_h = float(parts[2]), float(parts[3])
            except: pass
    if not svg_w:
        svg_w = float(re.sub(r'[^\d.]','', root.get('width','100')) or '100')
    if not svg_h:
        svg_h = float(re.sub(r'[^\d.]','', root.get('height','100')) or '100')
    svg_w = max(svg_w, 1.0)
    svg_h = max(svg_h, 1.0)

    scale = (target_width_mm / (25.4 * 12.0)) / svg_w   # SVG px → Revit ft

    # ── parse all paths ───────────────────────────────────────────────────────
    raw_segs = []
    for elem in root.iter():
        local = elem.tag[len(ns):] if ns else elem.tag
        if local != 'path':
            continue
        d = (elem.get('d') or '').strip()
        if not d:
            continue
        try:
            polys = parse_svg_path(d)
        except Exception:
            continue
        for poly in polys:
            for k in range(len(poly) - 1):
                raw_segs.append((poly[k], poly[k+1]))

    if not raw_segs:
        return [], None

    # ── dedup double-outline pairs ────────────────────────────────────────────
    deduped = dedup_segments(raw_segs, merge_px=max(2, line_thickness_px))

    # ── convert to Revit XYZ ─────────────────────────────────────────────────
    tol    = doc.Application.ShortCurveTolerance
    result = []
    for (x1,y1),(x2,y2) in deduped:
        p1 = DB.XYZ(x1*scale, (svg_h-y1)*scale, 0.0)
        p2 = DB.XYZ(x2*scale, (svg_h-y2)*scale, 0.0)
        if p1.DistanceTo(p2) > tol:
            result.append((p1, p2))

    return result, None
# ══════════════════════════════════════════════════════════════════════════════
# DYNAMIC C# VECTORIZER COMPILER & BITMAP HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def compile_csharp_vectorizer():
    source_code = """
using System;
using System.Collections.Generic;

public class Vectorizer {
    public static int ComputeOtsuThreshold(byte[] pixelData, int w, int h, int stride) {
        int[] hist = new int[256];
        for (int y = 0; y < h; y++) {
            int baseIdx = y * stride;
            for (int x = 0; x < w; x++) {
                int idx = baseIdx + x * 4;
                byte b = pixelData[idx];
                byte g = pixelData[idx + 1];
                byte r = pixelData[idx + 2];
                int gray = (r * 299 + g * 587 + b * 114) / 1000;
                hist[gray]++;
            }
        }

        int total = w * h;
        double sum = 0;
        for (int i = 0; i < 256; i++) sum += i * hist[i];

        double sumB = 0;
        int wB = 0;
        int wF = 0;

        double varMax = 0;
        int threshold = 128;

        for (int i = 0; i < 256; i++) {
            wB += hist[i];
            if (wB == 0) continue;
            wF = total - wB;
            if (wF == 0) break;

            sumB += i * hist[i];
            double mB = sumB / wB;
            double mF = (sum - sumB) / wF;

            double varBetween = (double)wB * (double)wF * (mB - mF) * (mB - mF);
            if (varBetween > varMax) {
                varMax = varBetween;
                threshold = i;
            }
        }
        return threshold;
    }

    public static byte[,] ZhangSuenThinning(byte[,] grid, int w, int h) {
        byte[,] current = (byte[,])grid.Clone();
        bool changed = true;
        List<int> toDeleteX = new List<int>();
        List<int> toDeleteY = new List<int>();

        while (changed) {
            changed = false;

            // Sub-iteration 1
            for (int y = 1; y < h - 1; y++) {
                for (int x = 1; x < w - 1; x++) {
                    if (current[y, x] == 0) continue;

                    int p2 = current[y - 1, x];
                    int p3 = current[y - 1, x + 1];
                    int p4 = current[y, x + 1];
                    int p5 = current[y + 1, x + 1];
                    int p6 = current[y + 1, x];
                    int p7 = current[y + 1, x - 1];
                    int p8 = current[y, x - 1];
                    int p9 = current[y - 1, x - 1];

                    int count = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9;
                    if (count < 2 || count > 6) continue;

                    int transitions = 0;
                    if (p2 == 0 && p3 == 1) transitions++;
                    if (p3 == 0 && p4 == 1) transitions++;
                    if (p4 == 0 && p5 == 1) transitions++;
                    if (p5 == 0 && p6 == 1) transitions++;
                    if (p6 == 0 && p7 == 1) transitions++;
                    if (p7 == 0 && p8 == 1) transitions++;
                    if (p8 == 0 && p9 == 1) transitions++;
                    if (p9 == 0 && p2 == 1) transitions++;

                    if (transitions != 1) continue;

                    if (p2 * p4 * p6 != 0) continue;
                    if (p4 * p6 * p8 != 0) continue;

                    toDeleteX.Add(x);
                    toDeleteY.Add(y);
                }
            }

            if (toDeleteX.Count > 0) {
                changed = true;
                for (int i = 0; i < toDeleteX.Count; i++) {
                    current[toDeleteY[i], toDeleteX[i]] = 0;
                }
                toDeleteX.Clear();
                toDeleteY.Clear();
            }

            // Sub-iteration 2
            for (int y = 1; y < h - 1; y++) {
                for (int x = 1; x < w - 1; x++) {
                    if (current[y, x] == 0) continue;

                    int p2 = current[y - 1, x];
                    int p3 = current[y - 1, x + 1];
                    int p4 = current[y, x + 1];
                    int p5 = current[y + 1, x + 1];
                    int p6 = current[y + 1, x];
                    int p7 = current[y + 1, x - 1];
                    int p8 = current[y, x - 1];
                    int p9 = current[y - 1, x - 1];

                    int count = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9;
                    if (count < 2 || count > 6) continue;

                    int transitions = 0;
                    if (p2 == 0 && p3 == 1) transitions++;
                    if (p3 == 0 && p4 == 1) transitions++;
                    if (p4 == 0 && p5 == 1) transitions++;
                    if (p5 == 0 && p6 == 1) transitions++;
                    if (p6 == 0 && p7 == 1) transitions++;
                    if (p7 == 0 && p8 == 1) transitions++;
                    if (p8 == 0 && p9 == 1) transitions++;
                    if (p9 == 0 && p2 == 1) transitions++;

                    if (transitions != 1) continue;

                    if (p2 * p4 * p8 != 0) continue;
                    if (p2 * p6 * p8 != 0) continue;

                    toDeleteX.Add(x);
                    toDeleteY.Add(y);
                }
            }

            if (toDeleteX.Count > 0) {
                changed = true;
                for (int i = 0; i < toDeleteX.Count; i++) {
                    current[toDeleteY[i], toDeleteX[i]] = 0;
                }
                toDeleteX.Clear();
                toDeleteY.Clear();
            }
        }

        return current;
    }

    public static void Prune(byte[,] grid, int w, int h, int pruningLength) {
        if (pruningLength <= 0) return;

        bool changed = true;
        int runCount = 0;
        while (changed && runCount < 10) {
            changed = false;
            List<KeyValuePair<int, int>> toDelete = new List<KeyValuePair<int, int>>();
            bool[,] visited = new bool[h, w];

            for (int y = 1; y < h - 1; y++) {
                for (int x = 1; x < w - 1; x++) {
                    if (grid[y, x] == 0) continue;

                    int neighbors = 0;
                    for (int dy = -1; dy <= 1; dy++) {
                        for (int dx = -1; dx <= 1; dx++) {
                            if (dx == 0 && dy == 0) continue;
                            if (grid[y + dy, x + dx] == 1) neighbors++;
                        }
                    }

                    if (neighbors == 1 && !visited[y, x]) {
                        List<KeyValuePair<int, int>> path = new List<KeyValuePair<int, int>>();
                        int cx = x;
                        int cy = y;
                        path.Add(new KeyValuePair<int, int>(cx, cy));
                        visited[cy, cx] = true;

                        bool walking = true;
                        bool hitJunction = false;

                        while (walking && path.Count <= pruningLength) {
                            int nx = -1, ny = -1;
                            int nNeighbors = 0;

                            for (int dy = -1; dy <= 1; dy++) {
                                for (int dx = -1; dx <= 1; dx++) {
                                    if (dx == 0 && dy == 0) continue;
                                    int tx = cx + dx;
                                    int ty = cy + dy;
                                    if (grid[ty, tx] == 1) {
                                        nNeighbors++;
                                        if (!visited[ty, tx]) {
                                            nx = tx;
                                            ny = ty;
                                        }
                                    }
                                }
                            }

                            if (nx != -1) {
                                int nxNeighbors = 0;
                                for (int nyy = -1; nyy <= 1; nyy++) {
                                    for (int nxx = -1; nxx <= 1; nxx++) {
                                        if (nxx == 0 && nyy == 0) continue;
                                        if (grid[ny + nyy, nx + nxx] == 1) nxNeighbors++;
                                    }
                                }

                                if (nxNeighbors >= 3) {
                                    hitJunction = true;
                                    walking = false;
                                } else {
                                    cx = nx;
                                    cy = ny;
                                    path.Add(new KeyValuePair<int, int>(cx, cy));
                                    visited[cy, cx] = true;
                                }
                            } else {
                                walking = false;
                            }
                        }

                        if (hitJunction && path.Count <= pruningLength) {
                            toDelete.AddRange(path);
                            changed = true;
                        }
                        else if (!hitJunction && path.Count <= Math.Max(2, pruningLength / 2)) {
                            toDelete.AddRange(path);
                            changed = true;
                        }
                    }
                }
            }

            if (toDelete.Count > 0) {
                foreach (var p in toDelete) {
                    grid[p.Value, p.Key] = 0;
                }
            } else {
                break;
            }
            runCount++;
        }
    }

    private static List<KeyValuePair<int, int>> GetNeighbors(byte[,] grid, int w, int h, int x, int y) {
        List<KeyValuePair<int, int>> list = new List<KeyValuePair<int, int>>();
        for (int dy = -1; dy <= 1; dy++) {
            for (int dx = -1; dx <= 1; dx++) {
                if (dx == 0 && dy == 0) continue;
                int nx = x + dx;
                int ny = y + dy;
                if (nx >= 0 && nx < w && ny >= 0 && ny < h) {
                    if (grid[ny, nx] == 1) {
                        list.Add(new KeyValuePair<int, int>(nx, ny));
                    }
                }
            }
        }
        return list;
    }

    public static List<List<double[]>> TraceSkeleton(byte[,] grid, int w, int h) {
        bool[,] visited = new bool[h, w];
        List<List<double[]>> polylines = new List<List<double[]>>();

        List<KeyValuePair<int, int>> endpoints = new List<KeyValuePair<int, int>>();
        List<KeyValuePair<int, int>> junctions = new List<KeyValuePair<int, int>>();

        for (int y = 1; y < h - 1; y++) {
            for (int x = 1; x < w - 1; x++) {
                if (grid[y, x] == 0) continue;
                var neighbors = GetNeighbors(grid, w, h, x, y);
                if (neighbors.Count == 1) {
                    endpoints.Add(new KeyValuePair<int, int>(x, y));
                } else if (neighbors.Count >= 3) {
                    junctions.Add(new KeyValuePair<int, int>(x, y));
                }
            }
        }

        foreach (var ep in endpoints) {
            int sx = ep.Key;
            int sy = ep.Value;
            if (visited[sy, sx]) continue;

            List<double[]> path = new List<double[]>();
            int cx = sx;
            int cy = sy;
            visited[cy, cx] = true;
            path.Add(new double[] { cx - 1, cy - 1 });

            bool walking = true;
            while (walking) {
                var neighbors = GetNeighbors(grid, w, h, cx, cy);
                int nx = -1, ny = -1;
                bool found = false;

                foreach (var n in neighbors) {
                    if (!visited[n.Value, n.Key]) {
                        nx = n.Key;
                        ny = n.Value;
                        found = true;
                        break;
                    }
                }

                if (found) {
                    cx = nx;
                    cy = ny;
                    visited[cy, cx] = true;
                    path.Add(new double[] { cx - 1, cy - 1 });

                    var nextNeighbors = GetNeighbors(grid, w, h, cx, cy);
                    int activeNeighbors = 0;
                    foreach (var n in nextNeighbors) {
                        if (grid[n.Value, n.Key] == 1) activeNeighbors++;
                    }
                    if (activeNeighbors >= 3) {
                        walking = false;
                    }
                } else {
                    walking = false;
                }
            }

            if (path.Count >= 2) {
                polylines.Add(path);
            }
        }

        foreach (var junc in junctions) {
            int sx = junc.Key;
            int sy = junc.Value;

            var neighbors = GetNeighbors(grid, w, h, sx, sy);
            foreach (var n in neighbors) {
                int cx = n.Key;
                int cy = n.Value;
                if (visited[cy, cx]) continue;

                List<double[]> path = new List<double[]>();
                path.Add(new double[] { sx - 1, sy - 1 });
                visited[cy, cx] = true;
                path.Add(new double[] { cx - 1, cy - 1 });

                bool walking = true;
                while (walking) {
                    var nextNeighbors = GetNeighbors(grid, w, h, cx, cy);
                    int nx = -1, ny = -1;
                    bool found = false;

                    foreach (var nn in nextNeighbors) {
                        if (!visited[nn.Value, nn.Key]) {
                            nx = nn.Key;
                            ny = nn.Value;
                            found = true;
                            break;
                        }
                    }

                    if (found) {
                        cx = nx;
                        cy = ny;
                        visited[cy, cx] = true;
                        path.Add(new double[] { cx - 1, cy - 1 });

                        int activeNeighbors = 0;
                        foreach (var nn in GetNeighbors(grid, w, h, cx, cy)) {
                            if (grid[nn.Value, nn.Key] == 1) activeNeighbors++;
                        }
                        if (activeNeighbors >= 3) {
                            walking = false;
                        }
                    } else {
                        walking = false;
                    }
                }

                if (path.Count >= 2) {
                    polylines.Add(path);
                }
            }
        }

        for (int y = 1; y < h - 1; y++) {
            for (int x = 1; x < w - 1; x++) {
                if (grid[y, x] == 1 && !visited[y, x]) {
                    List<double[]> path = new List<double[]>();
                    int cx = x;
                    int cy = y;
                    visited[cy, cx] = true;
                    path.Add(new double[] { cx - 1, cy - 1 });

                    bool walking = true;
                    while (walking) {
                        var neighbors = GetNeighbors(grid, w, h, cx, cy);
                        int nx = -1, ny = -1;
                        bool found = false;
                        foreach (var n in neighbors) {
                            if (!visited[n.Value, n.Key]) {
                                nx = n.Key;
                                ny = n.Value;
                                found = true;
                                break;
                            }
                        }
                        if (found) {
                            cx = nx;
                            cy = ny;
                            visited[cy, cx] = true;
                            path.Add(new double[] { cx - 1, cy - 1 });
                        } else {
                            double dx = cx - x;
                            double dy = cy - y;
                            if (Math.Abs(dx) <= 1 && Math.Abs(dy) <= 1 && path.Count >= 3) {
                                path.Add(new double[] { x - 1, y - 1 });
                            }
                            walking = false;
                        }
                    }
                    if (path.Count >= 2) {
                        polylines.Add(path);
                    }
                }
            }
        }

        return polylines;
    }

    public static List<double[]> SimplifyRDP(List<double[]> points, double epsilon) {
        if (points.Count < 3) return points;

        int firstPoint = 0;
        int lastPoint = points.Count - 1;
        List<int> indexListToKeep = new List<int>();

        indexListToKeep.Add(firstPoint);
        indexListToKeep.Add(lastPoint);

        while (points[firstPoint][0] == points[lastPoint][0] && points[firstPoint][1] == points[lastPoint][1]) {
            lastPoint--;
            if (lastPoint <= firstPoint) break;
        }

        if (lastPoint - firstPoint < 2) return points;

        SimplifyRDPStep(points, firstPoint, lastPoint, epsilon, ref indexListToKeep);

        indexListToKeep.Sort();

        List<double[]> simplifiedPoints = new List<double[]>();
        foreach (int idx in indexListToKeep) {
            simplifiedPoints.Add(points[idx]);
        }

        if (points[points.Count - 1][0] == points[0][0] && points[points.Count - 1][1] == points[0][1]) {
            simplifiedPoints.Add(points[points.Count - 1]);
        }

        return simplifiedPoints;
    }

    private static void SimplifyRDPStep(List<double[]> points, int firstPoint, int lastPoint, double epsilon, ref List<int> indexListToKeep) {
        double maxDistance = 0;
        int indexFarthest = 0;

        for (int index = firstPoint + 1; index < lastPoint; index++) {
            double distance = PerpendicularDistance(points[index], points[firstPoint], points[lastPoint]);
            if (distance > maxDistance) {
                maxDistance = distance;
                indexFarthest = index;
            }
        }

        if (maxDistance > epsilon && indexFarthest != 0) {
            indexListToKeep.Add(indexFarthest);
            SimplifyRDPStep(points, firstPoint, indexFarthest, epsilon, ref indexListToKeep);
            SimplifyRDPStep(points, indexFarthest, lastPoint, epsilon, ref indexListToKeep);
        }
    }

    private static double PerpendicularDistance(double[] Point, double[] LineStart, double[] LineEnd) {
        double doubleArea = Math.Abs(LineStart[0] * (LineEnd[1] - Point[1]) + LineEnd[0] * (Point[1] - LineStart[1]) + Point[0] * (LineStart[1] - LineEnd[1]));
        double bottom = Math.Sqrt(Math.Pow(LineStart[0] - LineEnd[0], 2) + Math.Pow(LineStart[1] - LineEnd[1], 2));
        if (bottom == 0) return 0;
        return doubleArea / bottom;
    }

    public static List<List<double[]>> Vectorize(byte[] pixelData, int w, int h, int stride, int threshold, bool autoThreshold, bool invert, int pruningLength, double rdpEpsilon) {
        int gridW = w + 2;
        int gridH = h + 2;
        byte[,] grid = new byte[gridH, gridW];

        int threshVal = threshold;
        if (autoThreshold) {
            threshVal = ComputeOtsuThreshold(pixelData, w, h, stride);
        }

        for (int y = 0; y < h; y++) {
            int baseIdx = y * stride;
            for (int x = 0; x < w; x++) {
                int idx = baseIdx + x * 4;
                byte b = pixelData[idx];
                byte g = pixelData[idx + 1];
                byte r = pixelData[idx + 2];
                int gray = (r * 299 + g * 587 + b * 114) / 1000;
                bool isDark = gray < threshVal;
                if (invert) isDark = !isDark;
                grid[y + 1, x + 1] = isDark ? (byte)1 : (byte)0;
            }
        }

        byte[,] thinned = ZhangSuenThinning(grid, gridW, gridH);
        Prune(thinned, gridW, gridH, pruningLength);
        var rawPolylines = TraceSkeleton(thinned, gridW, gridH);

        var result = new List<List<double[]>>();
        foreach (var poly in rawPolylines) {
            var simplified = SimplifyRDP(poly, rdpEpsilon);
            if (simplified.Count >= 2) {
                result.Add(simplified);
            }
        }

        return result;
    }

    public static byte[] Binarize(byte[] pixelData, int w, int h, int stride, int threshold, bool autoThreshold, bool invert, int erodePasses) {
        int threshVal = threshold;
        if (autoThreshold) {
            threshVal = ComputeOtsuThreshold(pixelData, w, h, stride);
        }

        byte[] output = new byte[h * stride];
        byte[,] grid = new byte[h, w];

        for (int y = 0; y < h; y++) {
            int baseIdx = y * stride;
            for (int x = 0; x < w; x++) {
                int idx = baseIdx + x * 4;
                byte b = pixelData[idx];
                byte g = pixelData[idx + 1];
                byte r = pixelData[idx + 2];
                int gray = (r * 299 + g * 587 + b * 114) / 1000;
                bool isDark = gray < threshVal;
                if (invert) isDark = !isDark;
                grid[y, x] = isDark ? (byte)1 : (byte)0;
            }
        }

        for (int pass = 0; pass < erodePasses; pass++) {
            byte[,] newGrid = (byte[,])grid.Clone();
            for (int y = 1; y < h - 1; y++) {
                for (int x = 1; x < w - 1; x++) {
                    if (grid[y, x] == 1 &&
                        grid[y - 1, x] == 1 && grid[y + 1, x] == 1 &&
                        grid[y, x - 1] == 1 && grid[y, x + 1] == 1) {
                        newGrid[y, x] = 0;
                    }
                }
            }
            grid = newGrid;
        }

        for (int y = 0; y < h; y++) {
            int baseIdx = y * stride;
            for (int x = 0; x < w; x++) {
                int idx = baseIdx + x * 4;
                byte v = (grid[y, x] == 0) ? (byte)255 : (byte)0;
                output[idx] = v;
                output[idx + 1] = v;
                output[idx + 2] = v;
                output[idx + 3] = 255;
            }
        }

        return output;
    }
}
"""
    from Microsoft.CSharp import CSharpCodeProvider
    from System.CodeDom.Compiler import CompilerParameters
    import System

    provider = CSharpCodeProvider()
    params = CompilerParameters()
    params.GenerateInMemory = True
    sources = System.Array[System.String]([source_code])
    results = provider.CompileAssemblyFromSource(params, sources)
    if results.Errors.HasErrors:
        errors = []
        for err in results.Errors:
            errors.append(err.ErrorText)
        raise Exception("C# Compilation failed:\n" + "\n".join(errors))
    import clr
    clr.AddReference(results.CompiledAssembly)
    import Vectorizer
    return Vectorizer

_VECTORIZER_CLASS = None

def get_vectorizer_class():
    global _VECTORIZER_CLASS
    if _VECTORIZER_CLASS is None:
        _VECTORIZER_CLASS = compile_csharp_vectorizer()
    return _VECTORIZER_CLASS


def get_bitmap_bytes(bmp_path):
    from System.Drawing import Bitmap, Rectangle
    from System.Drawing.Imaging import PixelFormat, ImageLockMode
    from System.Runtime.InteropServices import Marshal
    import System

    src = Bitmap(bmp_path)
    w, h = src.Width, src.Height
    fmt = PixelFormat.Format32bppArgb
    src32 = src.Clone(Rectangle(0, 0, w, h), fmt)
    src.Dispose()

    bd = src32.LockBits(Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, fmt)
    stride = bd.Stride
    raw_array = System.Array.CreateInstance(System.Byte, stride * h)
    Marshal.Copy(bd.Scan0, raw_array, 0, len(raw_array))
    src32.UnlockBits(bd)
    src32.Dispose()
    
    return raw_array, w, h, stride


def save_binary_bmp(bmp_path, binary_bytes, w, h, stride):
    from System.Drawing import Bitmap, Rectangle
    from System.Drawing.Imaging import PixelFormat, ImageLockMode
    from System.Runtime.InteropServices import Marshal
    
    out_bmp = Bitmap(w, h)
    bd = out_bmp.LockBits(Rectangle(0, 0, w, h), ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb)
    Marshal.Copy(binary_bytes, 0, bd.Scan0, len(binary_bytes))
    out_bmp.UnlockBits(bd)
    out_bmp.Save(bmp_path)
    out_bmp.Dispose()


# ══════════════════════════════════════════════════════════════════════════════
# WPF WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class ImageToDraftingWindow(T3WPFWindow):

    # ProgressPauseMixin — ImageToDrafting.xaml footer progress panel
    PP_PANEL      = "i2d_progress_panel"
    PP_BAR        = "i2d_pb"
    PP_PAUSE      = "i2d_btn_pause"
    PP_STOP       = "i2d_btn_stop"
    PP_PAUSE_ICON = "i2d_btn_pause_icon"
    PP_PAUSE_TEXT = "i2d_btn_pause_label"
    PP_STATUS     = "status_text"
    PP_STOP_MSG   = u"Stopping… finishing current line"

    def __init__(self, xaml_file=None):
        if xaml_file is None:
            xaml_file = os.path.join(os.path.dirname(__file__), 'Tools', 'ImageToDrafting.xaml')
        T3WPFWindow.__init__(self, xaml_file)
        self.image_path   = None   # path to BMP ready for potrace
        self.pdf_path     = None   # original PDF path (None for images)
        self.gs_path      = None   # cached Ghostscript path
        self.temp_files   = []
        ext_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        pb_potrace = os.path.join(ext_dir, 'T3Lab.tab', 'Modeling & Datum.panel', 'Create.stack', 'Create Elements.pulldown', 'ImageToDrafting.pushbutton', 'potrace.exe')
        self.potrace_path = pb_potrace if os.path.isfile(pb_potrace) else os.path.join(os.path.dirname(__file__), 'potrace.exe')

    # ── window chrome ──────────────────────────────────────────────────────────
    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        self.WindowState = (WindowState.Normal
                            if self.WindowState == WindowState.Maximized
                            else WindowState.Maximized)

    def close_button_clicked(self, sender, e):
        self.Close()

    # ── keyboard paste ─────────────────────────────────────────────────────────
    def Window_KeyDown(self, sender, args):
        if str(args.Key) == 'V' and str(args.KeyboardDevice.Modifiers) == 'Control':
            self.load_from_clipboard()

    # ── drag & drop ────────────────────────────────────────────────────────────
    def Border_Drop(self, sender, args):
        if args.Data.GetDataPresent('FileDrop'):
            files = args.Data.GetData('FileDrop')
            if files and len(files) > 0:
                self.load_file(files[0])

    # ── browse ─────────────────────────────────────────────────────────────────
    def Browse_Click(self, sender, args):
        dlg = Microsoft.Win32.OpenFileDialog()
        dlg.Filter = ("Image & PDF Files|*.jpg;*.jpeg;*.png;*.bmp;*.pdf"
                      "|PDF Files|*.pdf"
                      "|Image Files|*.jpg;*.jpeg;*.png;*.bmp"
                      "|All Files|*.*")
        if dlg.ShowDialog():
            self.load_file(dlg.FileName)

    # ── clear ──────────────────────────────────────────────────────────────────
    def Clear_Click(self, sender, args):
        self.ImagePreview.Source = None
        self.image_path = None
        self.pdf_path   = None
        self.sp_pdf_options.Visibility = Visibility.Collapsed

    # ── load dispatcher ────────────────────────────────────────────────────────
    def load_file(self, filepath):
        if not os.path.exists(filepath):
            return
        ext = os.path.splitext(filepath)[1].lower()
        if ext == '.pdf':
            self._load_pdf(filepath)
        else:
            self._load_image(filepath)

    # ── image loading ──────────────────────────────────────────────────────────
    def _load_image(self, filepath):
        bmp = BitmapImage()
        bmp.BeginInit()
        bmp.UriSource   = Uri(filepath)
        bmp.CacheOption = bmp.CacheOption.OnLoad
        bmp.EndInit()
        self.ImagePreview.Source = bmp
        self.image_path = self._save_bmp(bmp)
        self.pdf_path   = None
        self.sp_pdf_options.Visibility = Visibility.Collapsed

    def load_from_clipboard(self):
        if Clipboard.ContainsImage():
            img = Clipboard.GetImage()
            if img:
                self.ImagePreview.Source = img
                self.image_path = self._save_bmp(img)
                self.pdf_path   = None
                self.sp_pdf_options.Visibility = Visibility.Collapsed

    def _save_bmp(self, bitmap_source):
        tmp = os.path.join(tempfile.gettempdir(),
                           "t3lab_img_{}.bmp".format(int(time.time() * 1000)))
        self.temp_files.append(tmp)
        enc = BmpBitmapEncoder()
        enc.Frames.Add(BitmapFrame.Create(bitmap_source))
        s = FileStream(tmp, FileMode.Create)
        enc.Save(s); s.Close()
        return tmp

    def _display_bmp_file(self, bmp_path):
        """Load a BMP/PNG file from disk into ImagePreview."""
        bmp = BitmapImage()
        bmp.BeginInit()
        bmp.UriSource   = Uri(bmp_path)
        bmp.CacheOption = bmp.CacheOption.OnLoad
        bmp.EndInit()
        self.ImagePreview.Source = bmp

    # ── PDF loading ────────────────────────────────────────────────────────────
    def _load_pdf(self, filepath):
        # Find Ghostscript (cache for session)
        if self.gs_path is None:
            self.gs_path = find_ghostscript()
        if not self.gs_path:
            forms.alert(
                "Ghostscript not found.\n\n"
                "PDF support requires Ghostscript to be installed.\n"
                "Download from: https://www.ghostscript.com/\n\n"
                "Tip: Install the 64-bit version to C:\\Program Files\\gs\\")
            return

        # Page count
        page_count = get_pdf_page_count(filepath)
        self.lbl_pdf_pages.Text = "of {} page{}".format(
            page_count, 's' if page_count > 1 else '')
        self.txt_pdf_page.Text = "1"
        self.pdf_path = filepath
        self.sp_pdf_options.Visibility = Visibility.Visible

        # Preview: render page 1 at 72 dpi (fast, preview quality)
        prev_path, err = pdf_to_bmp(filepath, 1, dpi=72, gs_path=self.gs_path)
        if prev_path:
            self.temp_files.append(prev_path)
            self._display_bmp_file(prev_path)
            self.image_path = prev_path
        else:
            self.ImagePreview.Source = None
            self.image_path = None
            forms.alert("PDF preview failed:\n" + (err or "Unknown error"))

    # ── settings events ────────────────────────────────────────────────────────
    def TracingMode_SelectionChanged(self, sender, e):
        if not hasattr(self, 'ThicknessInput') or self.ThicknessInput is None:
            return
        if sender.SelectedIndex == 0:
            self.lbl_thickness.Text = "Pruning Length (px)"
            self.ThicknessInput.Text = "2"
            self.ThicknessInput.ToolTip = "Pruning length for Centerline: removes short spurious branches."
        else:
            self.lbl_thickness.Text = "Line Thickness (px)"
            self.ThicknessInput.Text = "2"
            self.ThicknessInput.ToolTip = "Erosion thickness for Outline: removes interior pixels of thick lines."

    def ThresholdMode_SelectionChanged(self, sender, e):
        if not hasattr(self, 'ManualThresholdInput') or self.ManualThresholdInput is None:
            return
        if sender.SelectedIndex == 0:
            self.ManualThresholdInput.IsEnabled = False
            self.lbl_manual_threshold.Opacity = 0.5
        else:
            self.ManualThresholdInput.IsEnabled = True
            self.lbl_manual_threshold.Opacity = 1.0

    # ── create ─────────────────────────────────────────────────────────────────
    def Create_Click(self, sender, args):
        view_name = self.ViewNameInput.Text.strip()
        if not view_name:
            forms.alert("Enter a Drafting View name."); return

        try:
            width_mm = float(self.WidthInput.Text.strip() or '300')
            if width_mm <= 0: raise ValueError
        except ValueError:
            forms.alert("Output Width must be a positive number."); return

        try:
            thickness = max(1, int(float(self.ThicknessInput.Text.strip() or '2')))
        except ValueError:
            forms.alert("Thickness/Pruning must be a positive integer."); return

        # Read settings
        tracing_mode = self.TracingModeInput.SelectedIndex  # 0: Centerline, 1: Outline
        
        auto_threshold = True
        threshold_val = 210
        if self.ThresholdModeInput.SelectedIndex == 1:
            auto_threshold = False
            try:
                threshold_val = int(float(self.ManualThresholdInput.Text.strip() or '210'))
                threshold_val = max(0, min(255, threshold_val))
            except ValueError:
                forms.alert("Threshold must be an integer between 0 and 255."); return

        try:
            rdp_tolerance = float(self.RdpToleranceInput.Text.strip() or '1.0')
            if rdp_tolerance < 0: raise ValueError
        except ValueError:
            forms.alert("Tolerance must be a non-negative number."); return

        invert_colors = bool(self.InvertInput.IsChecked)

        if tracing_mode == 1 and not os.path.exists(self.potrace_path):
            forms.alert("potrace.exe not found next to this button. Cannot run Outline Mode."); return

        # ── resolve source BMP ─────────────────────────────────────────────────
        if self.pdf_path:
            # PDF mode: convert the selected page at export quality (150 dpi)
            if not self.gs_path:
                self.gs_path = find_ghostscript()
            if not self.gs_path:
                forms.alert("Ghostscript not found. Cannot export PDF."); return
            try:
                page_num = max(1, int(self.txt_pdf_page.Text.strip() or '1'))
            except ValueError:
                forms.alert("Page number must be a positive integer."); return

            bmp_path, err = pdf_to_bmp(
                self.pdf_path, page_num, dpi=150, gs_path=self.gs_path)
            if not bmp_path:
                forms.alert("PDF conversion failed:\n" + (err or "Unknown")); return
            self.temp_files.append(bmp_path)
            img_path = bmp_path
        else:
            if not self.image_path:
                forms.alert("Load or paste an image first."); return
            img_path = self.image_path

        self.Close()
        self._run(view_name, img_path, width_mm, thickness, tracing_mode, auto_threshold, threshold_val, rdp_tolerance, invert_colors)

    # ── core pipeline ──────────────────────────────────────────────────────────
    def _run(self, view_name, img_path, width_mm, thickness_px, tracing_mode, auto_threshold, threshold_val, rdp_tolerance, invert_colors):
        segments = []
        tol = doc.Application.ShortCurveTolerance

        if tracing_mode == 0:
            # 1 ── Centerline Mode (C# dynamic vectorizer)
            try:
                Vectorizer = get_vectorizer_class()
            except Exception as exc:
                forms.alert("Failed to compile C# vectorizer:\n" + str(exc))
                return

            try:
                raw_array, w, h, stride = get_bitmap_bytes(img_path)
            except Exception as exc:
                forms.alert("Failed to read image pixels:\n" + str(exc))
                return

            try:
                # Vectorize in C#
                lines = Vectorizer.Vectorize(
                    raw_array, w, h, stride, 
                    threshold_val, auto_threshold, invert_colors, 
                    thickness_px, rdp_tolerance
                )
            except Exception as exc:
                forms.alert("Centerline vectorization error:\n" + str(exc))
                return

            if not lines or len(lines) == 0:
                forms.alert("No line paths detected.")
                return

            # Map coordinates to Revit feet
            scale = (width_mm / (25.4 * 12.0)) / w   # px → Revit feet
            for polyline in lines:
                if len(polyline) < 2:
                    continue
                pts = []
                for pt in polyline:
                    x_r = pt[0] * scale
                    y_r = (h - pt[1]) * scale  # Y-up flip
                    pts.append(DB.XYZ(x_r, y_r, 0.0))
                
                # Accumulate short segments to prevent Revit from discarding them
                p_start = pts[0]
                for i in range(1, len(pts)):
                    p_end = pts[i]
                    if p_start.DistanceTo(p_end) > tol:
                        segments.append((p_start, p_end))
                        p_start = p_end

        else:
            # 2 ── Outline Mode (Binarize in C# → Potrace → SVG)
            try:
                Vectorizer = get_vectorizer_class()
            except Exception as exc:
                forms.alert("Failed to compile C# vectorizer:\n" + str(exc))
                return

            try:
                raw_array, w, h, stride = get_bitmap_bytes(img_path)
            except Exception as exc:
                forms.alert("Failed to read image pixels:\n" + str(exc))
                return

            # Use C# to binarize/invert/erode the image
            try:
                binary_bytes = Vectorizer.Binarize(
                    raw_array, w, h, stride,
                    threshold_val, auto_threshold, invert_colors,
                    max(0, thickness_px // 3)
                )
            except Exception as exc:
                forms.alert("Failed to binarize image:\n" + str(exc))
                return

            # Save the preprocessed binary image to a temp file
            pre_path = img_path.replace('.bmp', '_pre.bmp')
            self.temp_files.append(pre_path)
            try:
                save_binary_bmp(pre_path, binary_bytes, w, h, stride)
            except Exception as exc:
                forms.alert("Failed to save binary preview image:\n" + str(exc))
                return

            # Run Potrace on the C#-preprocessed binary image
            svg_path = img_path + ".svg"
            self.temp_files.append(svg_path)
            try:
                subprocess.call(
                    [self.potrace_path, "-b", "svg",
                     "-O", "0.2",
                     "-t", "4",          # ignore tiny specks
                     pre_path, "-o", svg_path],
                    shell=False
                )
            except Exception as exc:
                forms.alert("Potrace error:\n" + str(exc))
                return

            if not os.path.exists(svg_path) or os.path.getsize(svg_path) < 50:
                forms.alert("Potrace produced no output.\n"
                            "Use an image with dark lines on a white background.")
                return

            # Parse SVG → Revit segments (uses existing parsing + dedup logic)
            segments, err = svg_to_revit_segments(svg_path, width_mm, thickness_px)
            if err:
                forms.alert("SVG parse error:\n" + err)
                return

        if not segments:
            forms.alert("No line paths detected.")
            return

        # 3 ── Find Drafting view type
        vft = next(
            (t for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
             if t.ViewFamily == DB.ViewFamily.Drafting), None
        )
        if not vft:
            forms.alert("No Drafting view family type found."); return

        existing   = {v.Name for v in DB.FilteredElementCollector(doc).OfClass(DB.View)}
        final_name = view_name
        if final_name in existing:
            final_name = "{} {}".format(view_name, int(time.time()))

        # 4 ── Create detail lines in Revit
        created = 0
        failed  = 0
        total_seg = len(segments)
        self.begin_progress(total_seg, disable=[self.CreateBtn])
        with revit.Transaction("Image to Drafting View"):
            dview = DB.ViewDrafting.Create(doc, vft.Id)
            dview.Name = final_name
            for _i2d_idx, (p1, p2) in enumerate(segments):
                if self.is_cancelled:
                    break
                # step_progress pumps WPF events — throttle so thousands of
                # segments stay fast while Pause/Stop stay responsive.
                if _i2d_idx % 25 == 0:
                    self.step_progress(_i2d_idx, "Creating detail lines {}/{}...".format(_i2d_idx, total_seg))
                try:
                    doc.Create.NewDetailCurve(dview, DB.Line.CreateBound(p1, p2))
                    created += 1
                except Exception:
                    failed += 1

        cancelled = self.is_cancelled
        self.end_progress()

        revit.uidoc.ActiveView = dview
        if cancelled:
            msg = "Cancelled — {} detail line(s) created in '{}'.".format(created, final_name)
        else:
            msg = "Done — {} detail lines in '{}'.".format(created, final_name)
        if failed:
            msg += " ({} segment(s) could not be created and were skipped.)".format(failed)
        self.status_text.Text = msg
        print(msg)

    # ── cleanup ────────────────────────────────────────────────────────────────
    def cleanup(self):
        for f in self.temp_files:
            try:
                if os.path.exists(f): os.remove(f)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def show_image_to_drafting_dialog(doc=None):
    d = doc or revit.doc
    if not d:
        forms.alert("No active Revit document.", title="Image to Drafting")
        return
    xaml_file = os.path.join(os.path.dirname(__file__), 'Tools', 'ImageToDrafting.xaml')
    win = ImageToDraftingWindow(xaml_file)
    try:
        win.ShowDialog()
    finally:
        win.cleanup()

if __name__ == '__main__':
    show_image_to_drafting_dialog()
