# -*- coding: utf-8 -*-
"""
CPython 3 test harness for lib/Snippets/_parcel_map.py - the boundary map
preview of the Property Line tool - and the binary tile path it relies on in
lib/Snippets/_geoparcel.py.

Run:  python3 dev/test_parcel_map.py
Exit code 0 = all pass.  Offline: every network call is stubbed.

What it locks down:

    projection  Web Mercator world pixels and tile indices match the OSM
                tile server (the HAREC tile at z18 is a real, fetched tile)
    fit         the boundary always fits inside the padded preview, at an
                OSM zoom (0..19) with bounded tile scaling
    alignment   boundary pixels and tile pixels share one projection, and
                neighbouring tiles share edges exactly (no seams)
    furniture   scale bar picks a round length that fits
    tiles       binary PNG bytes arrive intact (the root cause of the blank
                preview: tiles were decoded as UTF-8 text), cache hits skip
                the network, and a dead network gives up fast
    xaml        OSM attribution stays on the map; the results list renders
                its row template (it used to come out as a blank grey row)
"""
from __future__ import unicode_literals

import math
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "T3Lab.extension", "lib"))

from Snippets import _geoparcel as gp      # noqa: E402
from Snippets import _parcel_map as pm     # noqa: E402

FAILURES = []

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) * 4)


def check(name, condition, detail=""):
    if condition:
        print("  ok   {}".format(name))
    else:
        print("  FAIL {} {}".format(name, detail))
        FAILURES.append(name)


def square(lat, lon, size_m):
    """North-aligned square of size_m metres as a closed [lon, lat] ring."""
    ring = gp._square_ring(lat, lon, size_m)
    return ring + [ring[0]]


# The boundary in the owner's screenshot: OSM way 136258882, 4 vertices,
# centroid 21.022281, 105.818569, about 749 m2.
HAREC = square(21.022281, 105.818569, 27.4)


def test_projection():
    print("projection")
    x, y = pm.lonlat_to_world(0.0, 0.0)
    check("null island is the world centre", abs(x - 128) < 1e-9 and abs(y - 128) < 1e-9)
    for lon, lat in ((105.818569, 21.022281), (-74.0, 40.7), (151.2, -33.86)):
        back = pm.world_to_lonlat(*pm.lonlat_to_world(lon, lat))
        check("round trip {:.2f},{:.2f}".format(lat, lon),
              abs(back[0] - lon) < 1e-9 and abs(back[1] - lat) < 1e-9, back)
    # Tile index at z18 - fetched from tile.openstreetmap.org (HTTP 200 PNG)
    # while writing this test, so the maths matches the real server.
    wx, wy = pm.lonlat_to_world(105.818569, 21.022281)
    tx, ty = int(wx * 2 ** 18 / 256), int(wy * 2 ** 18 / 256)
    check("HAREC tile at z18 is 208126/115408", (tx, ty) == (208126, 115408), (tx, ty))
    _, y_pole = pm.lonlat_to_world(0, 89.9)
    check("latitude is clamped to Web Mercator", 0.0 <= y_pole < 1e-6, y_pole)


def test_fit():
    print("fit")
    w, h, pad = 270.0, 258.0, 28.0
    view = pm.fit_view(HAREC, w, h, padding=pad)
    check("view built", view is not None)
    pts = view.screen_ring(HAREC)
    check("closing vertex dropped", len(pts) == 4, len(pts))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    inside = (min(xs) >= pad - 0.01 and max(xs) <= w - pad + 0.01 and
              min(ys) >= pad - 0.01 and max(ys) <= h - pad + 0.01)
    check("boundary inside the padded preview", inside, (xs, ys))
    fills = max(max(xs) - min(xs) - (w - 2 * pad), max(ys) - min(ys) - (h - 2 * pad))
    # At the overzoom cap the boundary may stop short of the padding.
    check("boundary fills the preview (or hits the zoom cap)",
          abs(fills) < 0.01 or (view.zoom == 19 and
                                abs(view.tile_scale - pm.MAX_OVERZOOM) < 1e-9 and fills < 0),
          fills)
    check("boundary is big on screen", max(xs) - min(xs) > 0.6 * (w - 2 * pad),
          max(xs) - min(xs))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    check("boundary centred", abs(cx - w / 2) < 0.01 and abs(cy - h / 2) < 0.01)
    check("zoom within OSM range", 0 <= view.zoom <= pm.MAX_TILE_ZOOM, view.zoom)
    check("small building: z19 tiles at most 2x",
          view.zoom == 19 and 1.0 < view.tile_scale <= pm.MAX_OVERZOOM + 1e-9,
          (view.zoom, view.tile_scale))
    lot = pm.fit_view(square(10.77, 106.69, 120.0), w, h, padding=pad)
    check("larger plot: tiles drawn about 1:1", lot.zoom < 19 and
          0.70 < lot.tile_scale < 1.42, (lot.zoom, lot.tile_scale))
    mpp = view.metres_per_pixel()
    side_px = max(xs) - min(xs)
    check("ground scale matches the 27.4 m side", abs(side_px * mpp - 27.4) < 0.5,
          side_px * mpp)

    tiny = pm.fit_view(square(21.0, 105.8, 0.5), w, h)
    check("tiny plot: zoom capped at 19", tiny.zoom == 19, tiny.zoom)
    check("tiny plot: overzoom capped", tiny.tile_scale <= pm.MAX_OVERZOOM + 1e-9,
          tiny.tile_scale)

    big = pm.fit_view(square(10.0, 106.0, 20000.0), w, h)
    check("district-sized ring: lower zoom", big.zoom < view.zoom, big.zoom)

    point = pm.fit_view([[105.8, 21.0]], w, h)
    sx, sy = point.to_screen(105.8, 21.0)
    check("single point centred", abs(sx - w / 2) < 1e-6 and abs(sy - h / 2) < 1e-6)

    check("empty ring -> None", pm.fit_view([], w, h) is None)
    check("no size -> None", pm.fit_view(HAREC, 0, 0) is None)
    check("NaN size -> None", pm.fit_view(HAREC, float("nan"), 100) is None)
    nan_ring = HAREC[:2] + [[float("nan"), 21.0]] + HAREC[2:]
    check("NaN vertex skipped", len(pm.fit_view(nan_ring, w, h).screen_ring(nan_ring)) == 4)

    dateline = [[179.9999, -16.0], [-179.9999, -16.0], [-179.9999, -16.0001],
                [179.9999, -16.0001]]
    dl = pm.fit_view(dateline, w, h)
    dpts = dl.screen_ring(dateline)
    check("antimeridian ring stays small", max(p[0] for p in dpts) - min(p[0] for p in dpts) < w,
          dpts)


def test_tiles():
    print("tiles")
    w, h = 270.0, 258.0
    view = pm.fit_view(HAREC, w, h, padding=28)
    specs = view.tiles()
    check("a handful of tiles", 1 <= len(specs) <= 12, len(specs))
    check("all at the view zoom", all(s.z == view.zoom for s in specs))

    def covered(px, py):
        return any(s.left <= px < s.left + s.size_x and s.top <= py < s.top + s.size_y
                   for s in specs)
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w / 2, h / 2)]
    check("tiles cover the whole preview", all(covered(*c) for c in corners))

    by_key = dict(((s.x, s.y), s) for s in specs)
    seams = []
    for s in specs:
        right = by_key.get((s.x + 1, s.y))
        if right is not None and right.left != s.left + s.size_x:
            seams.append((s, right))
        below = by_key.get((s.x, s.y + 1))
        if below is not None and below.top != s.top + s.size_y:
            seams.append((s, below))
    check("neighbouring tiles share edges exactly", not seams, seams)

    # Alignment: a vertex lands where its own tile puts that ground point.
    n = 2 ** view.zoom
    lon, lat = HAREC[0]
    wx, wy = pm.lonlat_to_world(lon, lat)
    fx, fy = wx * n / 256.0, wy * n / 256.0
    spec = by_key.get((int(fx), int(fy)))
    sx, sy = view.to_screen(lon, lat)
    ex = spec.left + (fx - int(fx)) * spec.size_x
    ey = spec.top + (fy - int(fy)) * spec.size_y
    check("boundary vertex sits on its tile pixel", abs(ex - sx) < 1.0 and abs(ey - sy) < 1.0,
          ((sx, sy), (ex, ey)))
    mid = (w / 2.0, h / 2.0)
    first = specs[0]
    check("centre tile listed first",
          first.left <= mid[0] < first.left + first.size_x and
          first.top <= mid[1] < first.top + first.size_y, first)
    check("tile url", specs[0].url.startswith("https://tile.openstreetmap.org/{}/".format(view.zoom)))

    world = pm.fit_view(square(0.0, 0.0, 5000000.0), 600, 400, padding=0).tiles()
    check("low zoom: rows clamped to the world", all(0 <= s.y < 2 ** s.z for s in world))
    check("low zoom: columns wrapped", all(0 <= s.x < 2 ** s.z for s in world))


def test_furniture():
    print("furniture")
    check("20 m bar", pm.scale_bar(0.5, 96)[2] == "20 m", pm.scale_bar(0.5, 96))
    check("20 m bar is 40 px", abs(pm.scale_bar(0.5, 96)[1] - 40) < 1e-9)
    check("2 km bar", pm.scale_bar(50.0, 96)[2] == "2 km", pm.scale_bar(50.0, 96))
    check("2 m bar", pm.scale_bar(0.05, 96)[2] == "2 m", pm.scale_bar(0.05, 96))
    check("sub-metre bar", pm.scale_bar(0.005, 96)[2] == "0.2 m", pm.scale_bar(0.005, 96))
    check("bar never longer than allowed",
          all(pm.scale_bar(m, 80)[1] <= 80 for m in (0.013, 0.37, 1.9, 7.7, 333.0)))
    check("bad input -> None", pm.scale_bar(0, 96) is None and pm.scale_bar(1, 0) is None)

    data = pm.path_data([(1, 2), (3.14159, -4), (5, 6)])
    check("path markup", data == "M1.00,2.00 L3.14,-4.00 L5.00,6.00 Z", data)
    check("open path for two points", pm.path_data([(0, 0), (1, 1)]) == "M0.00,0.00 L1.00,1.00")
    check("no path for one point", pm.path_data([(0, 0)]) == "")
    marks = pm.markers_data([(10, 10), (20, 20)], 3)
    check("markers: non-zero fill, two circles",
          marks.startswith("F1 ") and marks.count(" A") == 4 and marks.count("Z") == 2, marks)
    check("markup uses '.' decimals (Geometry.Parse is invariant)",
          pm.path_data([(1.5, 2.5), (3, 4)]).startswith("M1.50,2.50 "))


class _Stub(object):
    """Fake http_get_bytes: replays scripted answers, counts calls."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        answer = self.answers.pop(0) if self.answers else (200, PNG)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _fresh_cache():
    pm._CACHE.clear()
    pm.DISK_CACHE_DIR = tempfile.mkdtemp(prefix="t3lab_tiles_test_")
    return pm.DISK_CACHE_DIR


def test_fetch():
    print("tile fetch")
    view = pm.fit_view(HAREC, 270, 258)
    specs = view.tiles()
    folder = _fresh_cache()
    try:
        stub = _Stub([])
        got = []
        result = pm.fetch_tiles(specs, on_tile=lambda s, d: got.append((s.key, d)),
                                http_get=stub)
        check("every tile delivered", result == (len(specs), 0, None), result)
        check("bytes arrive intact (binary, not text)", all(d == PNG for _, d in got))
        url, headers, timeout = stub.calls[0]
        check("identifying User-Agent sent", headers.get("User-Agent") == gp.USER_AGENT, headers)
        check("asks for images, not JSON", "image/png" in headers.get("Accept", ""), headers)
        check("short per-tile timeout", timeout <= 10, timeout)

        again = _Stub([])
        result = pm.fetch_tiles(specs, http_get=again)
        check("second preview served from memory", not again.calls and result[0] == len(specs))
        pm._CACHE.clear()
        disk = _Stub([])
        pm.fetch_tiles(specs, http_get=disk)
        check("next session served from disk cache", not disk.calls)
        check("cached_tile reads back", pm.cached_tile(specs[0].key) == PNG)

        _fresh_cache()
        blocked = _Stub([(403, b"<html>Access blocked</html>")])
        result = pm.fetch_tiles(specs, http_get=blocked)
        check("403 stops the run at once", len(blocked.calls) == 1, len(blocked.calls))
        check("403 reported", result[0] == 0 and "403" in result[2], result)

        _fresh_cache()
        html = _Stub([(200, b"<html>maintenance</html>")] + [(200, PNG)] * 20)
        result = pm.fetch_tiles(specs, http_get=html)
        check("HTML page is not a tile", result[1] == 1 and result[0] == len(specs) - 1, result)

        _fresh_cache()
        offline = _Stub([IOError("[Errno 11001] getaddrinfo failed")] * 50)
        result = pm.fetch_tiles(specs, http_get=offline)
        check("offline gives up after {} tries".format(pm.MAX_TRANSPORT_FAILURES),
              len(offline.calls) == pm.MAX_TRANSPORT_FAILURES, len(offline.calls))
        check("offline reason is plain English", "resolved" in result[2], result)

        _fresh_cache()
        ticks = iter(range(0, 1000, 20))
        slow = _Stub([])
        result = pm.fetch_tiles(specs, http_get=slow, budget_s=30,
                                clock=lambda: next(ticks))
        check("time budget respected", len(slow.calls) < len(specs), len(slow.calls))

        _fresh_cache()
        token = {"live": True}

        def flip(spec, data):
            token["live"] = False
        stopped = pm.fetch_tiles(specs, on_tile=flip, is_live=lambda: token["live"],
                                 http_get=_Stub([]))
        check("superseded run stops and returns None", stopped is None)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        pm._CACHE.clear()


def test_binary_http():
    """The root cause: tiles went through the UTF-8 text reader."""
    print("binary http")
    text = PNG.decode("utf-8", "replace")
    try:
        text.encode("latin-1")
        survived = True
    except UnicodeEncodeError:
        survived = False
    check("text-decoded PNG cannot be recovered (old bug)", not survived)

    class _Resp(object):
        def getcode(self):
            return 200

        def read(self):
            return PNG

    saved = gp._urq.urlopen, gp._HAS_DOTNET
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        seen["accept"] = req.get_header("Accept")
        return _Resp()
    try:
        gp._urq.urlopen = fake_urlopen
        gp._HAS_DOTNET = False
        status, body = gp.http_get_bytes("https://tile.openstreetmap.org/1/0/0.png",
                                         headers={"Accept": pm.TILE_ACCEPT})
    finally:
        gp._urq.urlopen, gp._HAS_DOTNET = saved
    check("http_get_bytes returns raw bytes", status == 200 and body == PNG)
    check("http_get_bytes sends the shared User-Agent", seen.get("ua") == gp.USER_AGENT, seen)
    check("caller's Accept wins", seen.get("accept") == pm.TILE_ACCEPT, seen)
    check("looks_like_image: PNG", pm.looks_like_image(PNG))
    check("looks_like_image: JPEG", pm.looks_like_image(b"\xff\xd8\xff\xe0" + b"\0" * 8))
    check("looks_like_image: HTML rejected", not pm.looks_like_image(b"<html><body>"))
    check("looks_like_image: empty rejected", not pm.looks_like_image(b""))


def test_xaml():
    """The preview's XAML keeps what the tile policy and the list need."""
    print("xaml")
    import xml.etree.ElementTree as ET
    path = os.path.join(REPO, "T3Lab.extension", "lib", "GUI", "Tools", "PropertyLine.xaml")
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    root = ET.fromstring(src)
    x_name = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
    named = dict((e.get(x_name), e) for e in root.iter() if e.get(x_name))

    attrib = named.get("border_map_attrib")
    texts = [t.get("Text") for t in attrib.iter() if t.get("Text")] if attrib is not None else []
    check("OSM attribution on the map", pm.ATTRIBUTION in texts, texts)
    for name in ("grid_map_host", "cnv_map_tiles", "path_map_fill", "path_map_boundary",
                 "path_map_vertices", "grid_map_overlay", "txt_map_scale", "bar_map_scale",
                 "border_map_note", "txt_map_note", "pnl_map_empty", "txt_map_empty"):
        check("map element {}".format(name), name in named)

    lv = named.get("lv_parcels")
    tag = lv.tag.rsplit("}", 1)[-1] if lv is not None else None
    # T3.ListViewItem draws a GridViewRowPresenter: a ListView without a
    # GridView shows blank rows (the empty grey row in the bug report).
    check("results list presents its ItemTemplate",
          tag == "ListBox" and lv.get("ItemContainerStyle") ==
          "{StaticResource T3.ListBoxItem.Multiline}", (tag, lv.get("ItemContainerStyle") if lv is not None else None))


def main():
    for test in (test_projection, test_fit, test_tiles, test_furniture,
                 test_fetch, test_binary_http, test_xaml):
        test()
    print()
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all _parcel_map tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
