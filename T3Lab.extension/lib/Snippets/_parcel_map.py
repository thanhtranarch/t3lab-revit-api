# -*- coding: utf-8 -*-
"""
Boundary map preview for the Property Line tool: slippy-map maths and
OpenStreetMap tile fetching.

Pure Python, no WPF and no Revit imports, so the projection / fit / scale-bar
maths is unit-tested outside Revit (dev/test_parcel_map.py).
GUI/PropertyLineDialog.py turns a MapView into WPF elements: the boundary is
ALWAYS drawn from the polygon itself, and the tiles are only a basemap under
it, so a dead network still leaves a usable preview.

Public API
----------
fit_view(ring, width, height)   -> MapView fitted to a [lon, lat] ring, or None
MapView.to_screen(lon, lat)     -> (x, y) pixel in the preview
MapView.screen_ring(ring)       -> polygon in preview pixels
MapView.tiles()                 -> [TileSpec] covering the preview, centre first
MapView.metres_per_pixel()      -> ground resolution at the centre
scale_bar(mpp, max_px)          -> (length_m, length_px, "20 m")
path_data(points)               -> WPF path markup for the boundary
markers_data(points, radius)    -> WPF path markup for the vertex dots
fetch_tiles(specs, on_tile, ..) -> download (or reuse cached) basemap tiles

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

import math
import os
import tempfile
import threading
import time

try:
    from collections import OrderedDict
except ImportError:                     # pragma: no cover - very old Python
    OrderedDict = dict

from Snippets import _geoparcel as geoparcel

__all__ = [
    "TILE_SIZE", "MAX_TILE_ZOOM", "ATTRIBUTION", "TILE_URL",
    "MapView", "TileSpec", "TileError", "fit_view", "lonlat_to_world",
    "world_to_lonlat", "scale_bar", "path_data", "markers_data",
    "fetch_tiles", "fetch_tile", "cached_tile", "looks_like_image",
]

# ── constants ────────────────────────────────────────────────────────────────

TILE_SIZE = 256
# tile.openstreetmap.org serves zoom 0..19
MAX_TILE_ZOOM = 19
# A small footprint is drawn at most this many times bigger than zoom-19
# tiles (one zoom level past the server's last); past that the basemap is
# only blur, so the boundary is drawn smaller instead.  A ~750 m2 building
# in the default window lands right at this cap.
MAX_OVERZOOM = 2.0
# Web Mercator stops here (atan(sinh(pi)))
MAX_LAT = 85.0511287798
EARTH_CIRCUMFERENCE_M = 2.0 * math.pi * 6378137.0

# OSM tile usage policy: identifying User-Agent (shared with the geocoder
# calls), visible attribution, local caching, no bulk download.
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATTRIBUTION = u"© OpenStreetMap contributors"
TILE_ACCEPT = "image/png,image/*;q=0.8"

# Per-tile HTTP timeout and the budget for one whole preview.  Short on
# purpose: the boundary is already on screen, the basemap must never keep
# the preview "loading" for minutes on a dead network.
TILE_TIMEOUT_S = 8
TILE_BUDGET_S = 30
# Network-level failures in a row before the rest of the basemap is skipped
# (an offline machine would otherwise wait N x timeout).
MAX_TRANSPORT_FAILURES = 2
# HTTP answers that mean "stop asking": blocked / rate limited.
STOP_STATUSES = (401, 403, 418, 429)
# Hard ceiling on tiles for one view (a maximised window is ~12-24).
MAX_TILES = 36

# Tiles are reused for 7 days (OSM asks clients to cache) - in memory for
# this Revit session, on disk under %TEMP% across sessions.
MEMORY_CACHE_TILES = 512
DISK_CACHE_DAYS = 7
DISK_CACHE_DIR = os.path.join(tempfile.gettempdir(), "t3lab_osm_tiles")
# Expired tiles used to be ignored but never deleted, so the folder only grew.
# prune_disk_cache() runs once per session (first tile written) and also caps
# the count: a tile is ~10-40 KB, so 2000 stay well under 100 MB.
DISK_CACHE_MAX_TILES = 2000
_DISK_PRUNED = []

_logger = None


def set_logger(logger):
    """Optional: hand in the pyRevit logger so tile failures stay traceable."""
    global _logger
    _logger = logger


def _log(msg):
    if _logger is not None:
        try:
            _logger.debug(msg)
        except Exception:
            pass


# ── projection (Web Mercator, EPSG:3857) ─────────────────────────────────────

def lonlat_to_world(lon, lat):
    """
    [lon, lat] degrees -> "world pixel" at zoom 0, i.e. 0..256 on both axes,
    x east, y SOUTH (screen convention).  Zoom z is this times 2**z.
    """
    lat = max(-MAX_LAT, min(MAX_LAT, float(lat)))
    x = (float(lon) + 180.0) / 360.0 * TILE_SIZE
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1.0 + s) / (1.0 - s)) / (4.0 * math.pi)) * TILE_SIZE
    return x, y


def world_to_lonlat(x, y):
    """Inverse of lonlat_to_world."""
    lon = float(x) / TILE_SIZE * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * float(y) / TILE_SIZE
    lat = math.degrees(math.atan(math.sinh(n)))
    return lon, lat


def _clean_ring(ring):
    """
    [[lon, lat], ...] -> list of (lon, lat) floats without the closing
    duplicate, unwrapped across the antimeridian so a parcel straddling
    180 degrees is not drawn across the whole world.
    """
    pts = []
    for pair in ring or []:
        try:
            lon, lat = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isnan(lon) or math.isnan(lat) or math.isinf(lon) or math.isinf(lat):
            continue
        pts.append((lon, lat))
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-12 \
            and abs(pts[0][1] - pts[-1][1]) < 1e-12:
        pts.pop()
    if pts:
        lons = [p[0] for p in pts]
        if max(lons) - min(lons) > 180.0:
            pts = [(lon + 360.0 if lon < 0 else lon, lat) for lon, lat in pts]
    return pts


# ── view ─────────────────────────────────────────────────────────────────────

class TileSpec(object):
    """One basemap tile and where it lands in the preview (whole pixels)."""

    __slots__ = ("z", "x", "y", "left", "top", "size_x", "size_y")

    def __init__(self, z, x, y, left, top, size_x, size_y):
        self.z, self.x, self.y = z, x, y
        self.left, self.top = left, top
        self.size_x, self.size_y = size_x, size_y

    @property
    def key(self):
        return (self.z, self.x, self.y)

    @property
    def url(self):
        return TILE_URL.format(z=self.z, x=self.x, y=self.y)

    def __repr__(self):
        return "TileSpec(z={}, x={}, y={}, at {},{} {}x{})".format(
            self.z, self.x, self.y, self.left, self.top, self.size_x, self.size_y)


class MapView(object):
    """
    A north-up view of width x height preview pixels centred on world pixel
    (cx, cy) at zoom 0, drawn `scale` preview pixels per zoom-0 world pixel.

    Basemap tiles come from integer zoom `zoom` and are drawn `tile_scale`
    times their native 256 px, so the boundary and the tiles share one
    projection and always line up.
    """

    def __init__(self, width, height, cx, cy, scale):
        self.width = float(width)
        self.height = float(height)
        self.cx = float(cx)
        self.cy = float(cy)
        self.scale = float(scale)
        zoom = int(round(math.log(self.scale, 2))) if self.scale > 0 else 0
        self.zoom = max(0, min(MAX_TILE_ZOOM, zoom))
        self.tile_scale = self.scale / float(2 ** self.zoom)

    def to_screen(self, lon, lat):
        wx, wy = lonlat_to_world(lon, lat)
        return ((wx - self.cx) * self.scale + self.width / 2.0,
                (wy - self.cy) * self.scale + self.height / 2.0)

    def screen_ring(self, ring):
        return [self.to_screen(lon, lat) for lon, lat in _clean_ring(ring)]

    def center_lonlat(self):
        return world_to_lonlat(self.cx, self.cy)

    def metres_per_pixel(self):
        """Ground metres per preview pixel at the centre of the view."""
        lat = self.center_lonlat()[1]
        return (math.cos(math.radians(lat)) * EARTH_CIRCUMFERENCE_M /
                (TILE_SIZE * self.scale))

    def tiles(self, limit=MAX_TILES):
        """
        TileSpecs covering the view, nearest to the centre first (they sit
        under the boundary, so they matter most and load first).

        Edges are rounded to whole pixels from the same grid, so neighbouring
        tiles share an edge exactly - no hairline seams.
        """
        n = 2 ** self.zoom
        tile_world = float(TILE_SIZE) / n               # zoom-0 px per tile
        half_w = self.width / 2.0 / self.scale
        half_h = self.height / 2.0 / self.scale
        eps = 1e-9
        tx0 = int(math.floor((self.cx - half_w) / tile_world))
        tx1 = int(math.floor((self.cx + half_w - eps) / tile_world))
        ty0 = max(0, int(math.floor((self.cy - half_h) / tile_world)))
        ty1 = min(n - 1, int(math.floor((self.cy + half_h - eps) / tile_world)))

        def edge(world, centre, half_screen):
            return int(math.floor((world - centre) * self.scale + half_screen + 0.5))

        specs = []
        for ty in range(ty0, ty1 + 1):
            top = edge(ty * tile_world, self.cy, self.height / 2.0)
            bottom = edge((ty + 1) * tile_world, self.cy, self.height / 2.0)
            for tx in range(tx0, tx1 + 1):
                left = edge(tx * tile_world, self.cx, self.width / 2.0)
                right = edge((tx + 1) * tile_world, self.cx, self.width / 2.0)
                if right <= left or bottom <= top:
                    continue
                specs.append(TileSpec(self.zoom, tx % n, ty, left, top,
                                      right - left, bottom - top))

        mid_x, mid_y = self.width / 2.0, self.height / 2.0
        specs.sort(key=lambda s: (s.left + s.size_x / 2.0 - mid_x) ** 2 +
                                 (s.top + s.size_y / 2.0 - mid_y) ** 2)
        return specs[:max(0, int(limit))]


def fit_view(ring, width, height, padding=24.0, max_overzoom=MAX_OVERZOOM):
    """
    The view that fits the [lon, lat] ring into width x height preview
    pixels with `padding` px clear on every side.

    A degenerate ring (one point, or every vertex on one line) is centred at
    the closest zoom the basemap allows.  Returns None when there is nothing
    to draw or no room to draw it.
    """
    try:
        width, height = float(width), float(height)
    except (TypeError, ValueError):
        return None
    if not (width > 1.0 and height > 1.0):         # also rejects NaN
        return None
    pts = [lonlat_to_world(lon, lat) for lon, lat in _clean_ring(ring)]
    if not pts:
        return None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0

    pad = max(0.0, min(float(padding), min(width, height) / 3.0))
    avail_w, avail_h = width - 2.0 * pad, height - 2.0 * pad
    cap = float(2 ** MAX_TILE_ZOOM) * max(1.0, float(max_overzoom))
    fits = [cap]
    if span_x > 0:
        fits.append(avail_w / span_x)
    if span_y > 0:
        fits.append(avail_h / span_y)
    return MapView(width, height, cx, cy, min(fits))


# ── map furniture ────────────────────────────────────────────────────────────

_NICE = (1.0, 2.0, 5.0)


def scale_bar(metres_per_px, max_px):
    """
    The longest round length (1/2/5 x 10^k metres) whose bar fits in
    max_px.  Returns (length_m, length_px, label) or None.
    """
    try:
        mpp = float(metres_per_px)
        max_px = float(max_px)
    except (TypeError, ValueError):
        return None
    if not (mpp > 0 and max_px > 0) or math.isinf(mpp):
        return None
    best = None
    for exp in range(-2, 8):
        for base in _NICE:
            length = base * (10.0 ** exp)
            px = length / mpp
            if px <= max_px:
                best = (length, px)
    if best is None:
        return None
    length, px = best
    if length >= 1000.0:
        label = u"{:g} km".format(round(length / 1000.0, 3))
    else:
        label = u"{:g} m".format(round(length, 3))
    return length, px, label


def _fmt(value):
    return "{:.2f}".format(value)


def path_data(points, close=True):
    """WPF path markup ("M x,y L x,y ... Z") for screen points; invariant '.'."""
    if not points or len(points) < 2:
        return u""
    parts = ["M{},{}".format(_fmt(points[0][0]), _fmt(points[0][1]))]
    for x, y in points[1:]:
        parts.append("L{},{}".format(_fmt(x), _fmt(y)))
    if close and len(points) > 2:
        parts.append("Z")
    return u" ".join(parts)


def markers_data(points, radius=3.0):
    """WPF path markup: one closed circle of `radius` per point (non-zero fill)."""
    if not points:
        return u""
    r = float(radius)
    parts = ["F1"]
    for x, y in points:
        parts.append("M{},{} A{},{} 0 1 0 {},{} A{},{} 0 1 0 {},{} Z".format(
            _fmt(x - r), _fmt(y), _fmt(r), _fmt(r), _fmt(x + r), _fmt(y),
            _fmt(r), _fmt(r), _fmt(x - r), _fmt(y)))
    return u" ".join(parts)


# ── tiles ────────────────────────────────────────────────────────────────────

class TileError(Exception):
    """A tile could not be fetched.  status is the HTTP code, None if transport."""

    def __init__(self, message, status=None):
        Exception.__init__(self, message)
        self.status = status


def looks_like_image(data):
    """PNG or JPEG signature - an HTML error page is not a tile."""
    try:
        if data is None or len(data) < 8:
            return False
        head = [int(b) for b in bytearray(data[:4])]
    except (TypeError, ValueError):
        return False
    return head == [0x89, 0x50, 0x4E, 0x47] or head[:3] == [0xFF, 0xD8, 0xFF]


_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _memory_get(key):
    with _CACHE_LOCK:
        data = _CACHE.get(key)
        if data is not None:
            try:
                _CACHE.move_to_end(key)
            except AttributeError:
                pass
        return data


def _memory_put(key, data):
    with _CACHE_LOCK:
        _CACHE[key] = data
        while len(_CACHE) > MEMORY_CACHE_TILES:
            try:
                _CACHE.popitem(last=False)
            except TypeError:
                _CACHE.pop(next(iter(_CACHE)))


def _disk_path(key):
    z, x, y = key
    return os.path.join(DISK_CACHE_DIR, str(z), str(x), "{}.png".format(y))


def _disk_get(key):
    path = _disk_path(key)
    try:
        if time.time() - os.path.getmtime(path) > DISK_CACHE_DAYS * 86400:
            return None
        with open(path, "rb") as handle:
            data = handle.read()
    except (IOError, OSError):
        return None
    return data if looks_like_image(data) else None


def prune_disk_cache(now=None):
    """Delete expired tiles (older than DISK_CACHE_DAYS), the oldest beyond
    DISK_CACHE_MAX_TILES, and .part files a crashed write left behind.
    Returns how many files went. Never raises."""
    try:
        from core import housekeeping
        removed = housekeeping.prune_files(
            DISK_CACHE_DIR, max_age_days=DISK_CACHE_DAYS,
            keep_newest=DISK_CACHE_MAX_TILES, suffixes=(".png",),
            recursive=True, now=now)
        removed += housekeeping.prune_files(
            DISK_CACHE_DIR, max_age_days=1, suffixes=(".part",),
            recursive=True, now=now)
        return removed
    except Exception as ex:
        _log("Tile cache prune skipped: {}".format(ex))
        return 0


def _disk_put(key, data):
    if not _DISK_PRUNED:
        _DISK_PRUNED.append(True)
        prune_disk_cache()
    path = _disk_path(key)
    tmp = "{}.{}.part".format(path, threading.current_thread().ident)
    try:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except (IOError, OSError) as ex:
        _log("Tile cache write skipped ({}): {}".format(path, ex))
        try:
            os.remove(tmp)
        except (IOError, OSError):
            pass


def cached_tile(key):
    """Tile bytes from memory or the 7-day disk cache, else None.  No network."""
    data = _memory_get(key)
    if data is None:
        data = _disk_get(key)
        if data is not None:
            _memory_put(key, data)
    return data


def fetch_tile(spec, timeout=TILE_TIMEOUT_S, http_get=None):
    """
    Bytes of one tile (PNG), from cache or the OSM tile server.
    Raises TileError for an HTTP answer that is not a tile; lets transport
    errors (offline, DNS, TLS, timeout) through.
    """
    data = cached_tile(spec.key)
    if data is not None:
        return data
    get = http_get or geoparcel.http_get_bytes
    status, body = get(spec.url, headers={"User-Agent": geoparcel.USER_AGENT,
                                          "Accept": TILE_ACCEPT},
                       timeout=timeout)
    if status != 200:
        raise TileError(u"tile server answered HTTP {}".format(status), status)
    if not looks_like_image(body):
        raise TileError(u"tile server sent something that is not an image",
                        status)
    data = bytes(body)
    _memory_put(spec.key, data)
    _disk_put(spec.key, data)
    return data


def fetch_tiles(specs, on_tile=None, is_live=None, timeout=TILE_TIMEOUT_S,
                budget_s=TILE_BUDGET_S, http_get=None, clock=time.time):
    """
    Fetch every tile in `specs` (cached ones cost nothing), calling
    on_tile(spec, data) for each one that arrives.

    Gives up on the rest - without waiting out every timeout - once the
    server says stop (403/429), after MAX_TRANSPORT_FAILURES network errors
    in a row, or when the whole budget is spent.  Cached tiles are still
    delivered after giving up.

    is_live() returning False (the user picked another boundary) stops at
    once and returns None.  Otherwise returns (loaded, failed, first_error).
    """
    loaded = failed = 0
    first_error = None
    give_up = False
    transport_failures = 0
    started = clock()

    for spec in specs or []:
        if is_live is not None and not is_live():
            return None
        data = cached_tile(spec.key)
        if data is None and not give_up:
            if clock() - started > budget_s:
                give_up = True
                first_error = first_error or u"the tile server is too slow"
            else:
                try:
                    data = fetch_tile(spec, timeout=timeout, http_get=http_get)
                    transport_failures = 0
                except TileError as ex:
                    first_error = first_error or u"{}".format(ex)
                    if ex.status in STOP_STATUSES:
                        give_up = True
                except Exception as ex:          # offline, DNS, TLS, proxy
                    transport_failures += 1
                    first_error = first_error or geoparcel.describe_error(ex)
                    if transport_failures >= MAX_TRANSPORT_FAILURES:
                        give_up = True
        if data is None:
            failed += 1
            continue
        loaded += 1
        if is_live is not None and not is_live():
            return None
        if on_tile is not None:
            on_tile(spec, data)
    if failed:
        _log(u"Basemap: {} tile(s) loaded, {} missing ({})".format(
            loaded, failed, first_error))
    return loaded, failed, first_error
