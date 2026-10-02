# -*- coding: utf-8 -*-
"""
Property Line Dialog

GUI dialog for creating and editing property lines.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
Linkedin: linkedin.com/in/sunarch7899/
"""

__author__  = "Tran Tien Thanh"
__title__   = "Property Line Dialog"

# ╦╔╦╗╔═╗╔═╗╦═╗╔╦╗╔═╗
# ║║║║╠═╝║ ║╠╦╝ ║ ╚═╗
# ╩╩ ╩╩  ╚═╝╩╚═ ╩ ╚═╝ IMPORTS
# ==================================================
import os
import sys
import clr
import json
import math
import traceback
import threading

# .NET / WPF
clr.AddReference("System")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

import System
import System.IO as IO
import System.Diagnostics as Diagnostics
from System import Uri, Action
from System.Collections.ObjectModel import ObservableCollection
from System.Windows import Window, Visibility, Application
from System.Windows.Input import Key
from System.Windows.Media import SolidColorBrush, Color
from System.Windows.Threading import Dispatcher, DispatcherPriority

from pyrevit import revit, DB, forms, script
from GUI.WPF_Base import T3WPFWindow
from Snippets._compat import disposing

# Worldwide (keyless) boundary lookup — OpenStreetMap based
try:
    from Snippets import _geoparcel as geoparcel
    HAS_GEOPARCEL = True
except ImportError:
    geoparcel = None
    HAS_GEOPARCEL = False

# Automatic worldwide search pipeline (source selection, coordinates, VN-2000)
try:
    from Snippets import _parcel_search as parcel_search
    HAS_PARCEL_SEARCH = True
except ImportError:
    parcel_search = None
    HAS_PARCEL_SEARCH = False

# Boundary map preview: Web Mercator maths + OSM basemap tiles
try:
    from Snippets import _parcel_map as parcel_map
    HAS_PARCEL_MAP = True
except ImportError:
    parcel_map = None
    HAS_PARCEL_MAP = False

# ╦  ╦╔═╗╦═╗╦╔═╗╔╗ ╦  ╔═╗╔═╗
# ╚╗╔╝╠═╣╠╦╝║╠═╣╠╩╗║  ║╣ ╚═╗
#  ╚╝ ╩ ╩╩╚═╩╩ ╩╚═╝╩═╝╚═╝╚═╝ VARIABLES
# ==================================================
logger = script.get_logger()

# Config file
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".t3lab")
CONFIG_FILE = os.path.join(CONFIG_DIR, "property_line_config.json")

# Lightbox API  (base URL includes version prefix per the OpenAPI spec)
LIGHTBOX_BASE              = "https://api.lightboxre.com"
LIGHTBOX_API_VERSION       = "/v1"
# Address-based parcel search  →  GET /v1/parcels/address?text={address}
LIGHTBOX_ADDRESS_ENDPOINT  = "/v1/parcels/address"
# ID/FIPS-based access  →  GET /v1/parcels/us/{id}
LIGHTBOX_PARCELS_ENDPOINT  = "/v1/parcels/us"

# Earth radius in feet (for coordinate conversion)
EARTH_RADIUS_FT = 20902231.0

# Elevation input units -> feet.  Must match cmb_elev_unit in the XAML.
ELEV_UNITS = {
    "m":  3.280839895013123,
    "mm": 0.003280839895013123,
    "cm": 0.03280839895013123,
    "ft": 1.0,
}

# How many boundary candidates to offer per search
MAX_RESULTS = 8

# Line Category options. Must match cmb_line_type in the XAML.
# NOTE: Autodesk.Revit.DB.PropertyLine is a member-less Element subclass in
# every shipping Revit (checked against RevitAPI.xml for 2023 and 2026) - there
# is no Create, so a genuine property line can only be drawn through Massing &
# Site > Property Line. LINE_CAT_PROPERTY therefore produces model lines on a
# dedicated "Property Line" line style, and the tool says so rather than
# pretending. See native_property_line_available().
LINE_CAT_PROPERTY = "Property Line"

if HAS_GEOPARCEL:
    geoparcel.set_logger(logger)
if HAS_PARCEL_SEARCH:
    parcel_search.set_logger(logger)
if HAS_PARCEL_MAP:
    parcel_map.set_logger(logger)


# ╔═╗╔═╗╔╗╔╔═╗╦╔═╗
# ║  ║ ║║║║╠╣ ║║ ╦
# ╚═╝╚═╝╝╚╝╚  ╩╚═╝ CONFIG HELPERS
# ==================================================

def ensure_config_dir():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)


def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(data):
    try:
        ensure_config_dir()
        existing = load_config()
        existing.update(data)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(existing, f, indent=2)
        return True
    except Exception as ex:
        logger.error("Failed to save config: {}".format(ex))
        return False


# ╔═╗╔═╗╔═╗╦═╗╔╦╗╦╔╗╔╔═╗╔╦╗╔═╗╔═╗
# ║  ║ ║║ ║╠╦╝ ║║║║║║╠═╣ ║ ║╣ ╚═╗
# ╚═╝╚═╝╚═╝╩╚══╩╝╩╝╚╝╩ ╩ ╩ ╚═╝╚═╝ COORDINATE UTILS
# ==================================================

def latlon_to_feet(lat, lon, origin_lat, origin_lon):
    """
    Convert WGS84 lat/lon to Revit internal feet,
    relative to a chosen origin point.

    Uses the equirectangular approximation which is accurate
    for small areas (a few miles), sufficient for property parcels.

    Returns (x_ft, y_ft) in feet where:
      +X = East
      +Y = North
    """
    dlat = math.radians(lat - origin_lat)
    dlon = math.radians(lon - origin_lon)
    cos_lat = math.cos(math.radians(origin_lat))

    x_ft = EARTH_RADIUS_FT * dlon * cos_lat
    y_ft = EARTH_RADIUS_FT * dlat
    return x_ft, y_ft


def compute_centroid(coordinates):
    """Compute centroid of a polygon (list of [lon, lat] pairs)."""
    if not coordinates:
        return 0.0, 0.0
    lons = [c[0] for c in coordinates]
    lats = [c[1] for c in coordinates]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def compute_area_sqft(coordinates):
    """Shoelace formula in lat/lon => approximate area in sqft."""
    if len(coordinates) < 3:
        return 0.0
    # Pick centroid as origin for conversion
    clat, clon = compute_centroid(coordinates)
    pts = [latlon_to_feet(c[1], c[0], clat, clon) for c in coordinates]

    n = len(pts)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return abs(area) / 2.0


def format_area(sqft):
    """
    Format an area for display.  Metric first (the tool is worldwide now),
    imperial in brackets.
    """
    if HAS_GEOPARCEL:
        return geoparcel.format_area_dual(sqft)
    # Fallback if the shared module is unavailable
    try:
        sqft = float(sqft or 0)
    except (TypeError, ValueError):
        return "N/A"
    if sqft <= 0:
        return "N/A"
    sqm = sqft / 10.763910416709722
    acres = sqft / 43560.0
    if acres >= 0.1:
        return u"{:,.0f} m² ({:,.0f} sqft, {:.2f} ac)".format(sqm, sqft, acres)
    return u"{:,.0f} m² ({:,.0f} sqft)".format(sqm, sqft)


def parse_wkt_polygon(wkt):
    """
    Parse a WKT geometry string and return the outer-ring coordinate list
    as [[lon, lat], ...].

    Supports POLYGON (...) and MULTIPOLYGON (...).
    Returns [] if the geometry cannot be parsed or is not a polygon.
    """
    if not wkt:
        return []
    wkt = wkt.strip()
    upper = wkt.upper()

    try:
        if upper.startswith("MULTIPOLYGON"):
            # MULTIPOLYGON (((lon lat,...)),((lon lat,...)))
            # Grab the first ring of the first polygon
            start = wkt.index("(((") + 3
            end   = wkt.index(")))", start)
            ring_str = wkt[start:end]
        elif upper.startswith("POLYGON"):
            # POLYGON ((lon lat,...))
            start = wkt.index("((") + 2
            end   = wkt.index("))", start)
            ring_str = wkt[start:end]
        else:
            return []

        coords = []
        for pair in ring_str.split(","):
            parts = pair.strip().split()
            if len(parts) >= 2:
                coords.append([float(parts[0]), float(parts[1])])
        return coords

    except (ValueError, IndexError):
        return []


# ╔═╗╔═╗╦  ╔═╗╔╦╗╦╔═╗╔╗╔╔═╗
# ╚═╗║╣ ║  ║╣ ║ ║║ ║║║║╚═╗
# ╚═╝╚═╝╩═╝╚═╝╩ ╩╚═╝╝╚╝╚═╝ LIGHTBOX API
# ==================================================

def _url_quote(text, safe=''):
    """Percent-encode *text* as UTF-8."""
    return geoparcel.url_quote(text, safe=safe)


def http_get(url, headers=None):
    """
    GET through the shared HTTP layer (User-Agent, TLS 1.2, System.Net with a
    urllib fallback, timeout).  Returns (status_code, body_text); non-2xx
    responses are returned, not raised.
    """
    return geoparcel.http_request(url, headers=headers, timeout=20)


def search_parcels(api_key, address, limit=10):
    """
    Query Lightbox parcels API for a US address.

    Tries the /search endpoint first (correct Lightbox route), then falls
    back to the base path in case the API version changes.

    Header: x-api-key
    Returns list of parcel dicts: id, display_address, parcel_id,
                                   area_sqft, geometry, county, state
    Raises ValueError on API error.
    """
    headers = {"x-api-key": api_key, "Accept": "application/json"}

    # ── Auto-correct the address before sending ──────────────────────────────
    corrected, was_changed = normalize_address(address)
    if was_changed:
        logger.info("Address normalised: '{}' → '{}'".format(address, corrected))

    # Per the OpenAPI spec the Address endpoint only accepts `text` (no limit).
    # Endpoint: GET /v1/parcels/address?text={address}
    enc = _url_quote(corrected, safe=',')
    url = "{}{}?text={}".format(LIGHTBOX_BASE, LIGHTBOX_ADDRESS_ENDPOINT, enc)

    logger.debug("Lightbox search URL: {}".format(url))
    try:
        status, body = http_get(url, headers)
    except Exception as ex:
        raise ValueError("Network error contacting Lightbox API: {}".format(ex))

    if isinstance(body, bytes):
        body = body.decode('utf-8', errors='replace')

    if status == 200:
        parcels = _parse_search_response(body, url)
        # Attach normalisation hint so caller can surface it in the UI
        if was_changed:
            for p in parcels:
                p["_corrected_from"] = address
        return parcels

    # ── Non-200: build an informative error ──────────────────────────────────
    hint = ""
    if status == 400:
        hint = (" Tip: include full address with state + ZIP, "
                "e.g. '20521 Paisley Ln, Huntington Beach, CA 92646'.")
    elif status in (401, 403):
        hint = " Check that your API key is valid and has Parcels access."
    elif status == 429:
        hint = " Rate limit hit — wait a moment and try again."
    raise ValueError(
        "Lightbox API error {} (URL: {}): {}{}".format(
            status, url, body[:300], hint)
    )


# ── Address normalisation / AI fuzzy correction ──────────────────────────────

_STATE_MAP = parcel_search.STATE_MAP if HAS_PARCEL_SEARCH else {}
_STATE_CODES = parcel_search.STATE_CODES if HAS_PARCEL_SEARCH else set()

_STREET_TYPES = {
    "st": "St", "str": "St", "street": "St",
    "ave": "Ave", "av": "Ave", "avenue": "Ave",
    "blvd": "Blvd", "boulevard": "Blvd",
    "rd": "Rd", "road": "Rd",
    "dr": "Dr", "drive": "Dr",
    "ln": "Ln", "lane": "Ln",
    "ct": "Ct", "court": "Ct",
    "pl": "Pl", "place": "Pl",
    "cir": "Cir", "circle": "Cir",
    "way": "Way",
    "ter": "Ter", "terrace": "Ter",
    "pkwy": "Pkwy", "parkway": "Pkwy",
    "hwy": "Hwy", "highway": "Hwy",
    "trl": "Trl", "trail": "Trl",
    "fwy": "Fwy", "freeway": "Fwy",
    "expy": "Expy", "expressway": "Expy",
}


def _levenshtein(a, b):
    """Edit distance between two strings (pure Python)."""
    if a == b: return 0
    if not a:  return len(b)
    if not b:  return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1,
                            curr[j]      + 1,
                            prev[j]      + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def normalize_address(raw):
    """
    Normalise a US address string using fuzzy correction:
      1. Collapse whitespace
      2. Expand full state names → 2-letter codes (fuzzy match ≤ 2 edits)
      3. Normalise street-type abbreviations (fuzzy match ≤ 1 edit)
      4. Correct common 2-word state names (e.g. 'New Yark' → 'NY')

    Returns (normalised_address, was_changed).
    """
    text = " ".join(raw.split())
    original = text

    parts = [p.strip() for p in text.split(",")]
    new_parts = []

    for part in parts:
        words = part.split()
        new_words = []
        i = 0
        while i < len(words):
            w  = words[i]
            wl = w.lower()

            # ── Two-word state match (e.g. "New York", "New Yark") ─────────
            if i + 1 < len(words):
                two_raw  = wl + " " + words[i + 1].lower()
                # Exact
                if two_raw in _STATE_MAP:
                    new_words.append(_STATE_MAP[two_raw])
                    i += 2
                    continue
                # Fuzzy: try all two-word state keys
                best_key, best_d = None, 3
                for key in _STATE_MAP:
                    if " " not in key:
                        continue
                    d = _levenshtein(two_raw, key)
                    if d < best_d:
                        best_d, best_key = d, key
                if best_key is not None:
                    new_words.append(_STATE_MAP[best_key])
                    i += 2
                    continue

            # ── Already a valid 2-letter state code ────────────────────────
            if w.upper() in _STATE_CODES:
                new_words.append(w.upper())
                i += 1
                continue

            # ── Single-word state name (fuzzy ≤ 2 edits, min length 4) ────
            if len(wl) >= 4:
                best_key, best_d = None, 3
                for key in _STATE_MAP:
                    if " " in key:
                        continue
                    d = _levenshtein(wl, key)
                    if d < best_d:
                        best_d, best_key = d, key
                if best_key is not None:
                    new_words.append(_STATE_MAP[best_key])
                    i += 1
                    continue

            # ── Street-type exact match ────────────────────────────────────
            if wl in _STREET_TYPES:
                new_words.append(_STREET_TYPES[wl])
                i += 1
                continue

            # ── Street-type fuzzy (≤ 1 edit only) ─────────────────────────
            if 2 <= len(wl) <= 9:
                best_k, best_d = None, 2
                for k in _STREET_TYPES:
                    d = _levenshtein(wl, k)
                    if d < best_d:
                        best_d, best_k = d, k
                if best_k is not None:
                    new_words.append(_STREET_TYPES[best_k])
                    i += 1
                    continue

            new_words.append(w)
            i += 1

        new_parts.append(" ".join(new_words))

    result = ", ".join(new_parts)
    return result, result != original


def _parse_search_response(body, url=""):
    """
    Parse a 200 response from GET /v1/parcels/address.
    Schema: { "parcels": [...], "$ref": "...", "$metadata": {...} }
    """
    try:
        data = json.loads(body)
    except Exception as ex:
        raise ValueError("Invalid JSON from Lightbox ({}): {}".format(url, ex))

    raw_list = data.get("parcels", [])
    if not raw_list and isinstance(data, list):
        raw_list = data

    parcels = []
    for item in raw_list:
        try:
            parcel = _parse_parcel(item)
            if parcel:
                parcels.append(parcel)
        except Exception as ex:
            logger.warning("Skipping parcel parse error: {}".format(ex))
    return parcels


def _coerce_str(val):
    """Return a plain string from an API value that may be a dict, list, or scalar."""
    if val is None:
        return ""
    if isinstance(val, dict):
        for key in ("description", "label", "name", "value", "code", "assessment", "type"):
            if val.get(key):
                return str(val[key])
        return str(val)
    if isinstance(val, (list, tuple)):
        parts = [_coerce_str(v) for v in val if v]
        return ", ".join(parts)
    return str(val)


def _parse_parcel(item):
    """
    Parse one parcel from the Lightbox /v1/parcels/address response.

    Key fields (from OpenAPI spec):
      item.id                              → LightBox ID
      item.parcelApn                       → APN
      item.fips                            → FIPS code
      item.location.streetAddress          → street
      item.location.locality               → city
      item.location.regionCode             → state (2-letter)
      item.location.postalCode             → ZIP
      item.location.geometry.wkt           → WKT polygon string
      item.county                          → county name
      item.derived.calculatedLotArea       → area (sqm by default)
      item.$metadata.units.area            → "sqm" or "sqft"
    """
    item_id   = item.get("id", "")
    parcel_apn = item.get("parcelApn", item_id or "N/A")

    # Address parts
    location = item.get("location") or {}
    street   = location.get("streetAddress", "")
    city     = location.get("locality", "")
    state    = location.get("regionCode", "")
    zipcode  = location.get("postalCode", "")
    display_parts = [p for p in [street, city, state, zipcode] if p]
    display_address = ", ".join(display_parts) if display_parts else "Unknown"

    # WKT geometry → internal dict with pre-parsed coords
    loc_geom = location.get("geometry") or {}
    wkt      = loc_geom.get("wkt", "")
    coords   = parse_wkt_polygon(wkt)
    if not coords:
        return None   # no valid polygon → skip this result

    geometry = {"type": "Polygon", "coordinates": [coords]}

    # Area
    derived  = item.get("derived") or {}
    area_raw = derived.get("calculatedLotArea")
    metadata = item.get("$metadata") or {}
    units    = (metadata.get("units") or {}).get("area", "sqm")
    if area_raw:
        try:
            area_sqft = float(area_raw) * (10.7639 if units == "sqm" else 1.0)
        except (TypeError, ValueError):
            area_sqft = compute_area_sqft(coords)
    else:
        area_sqft = compute_area_sqft(coords)

    county = item.get("county", "")

    # ── Assessment / legal / zoning ──────────────────────────────────────────
    assessment = item.get("assessment") or {}

    # Zoning code  (e.g. "R-L", "R1")
    zoning_obj = assessment.get("zoning") or {}
    if isinstance(zoning_obj, dict):
        zoning_code = _coerce_str(zoning_obj.get("assessment") or
                                  zoning_obj.get("code") or
                                  zoning_obj.get("label") or
                                  zoning_obj.get("value") or "")
    else:
        zoning_code = _coerce_str(zoning_obj)

    # Legal description  (e.g. "N-TRACT 6756, BLOCK: LOT 60")
    legal_description = _coerce_str(
        assessment.get("legalDescription") or
        assessment.get("legal_description") or
        assessment.get("legalDesc") or "")

    # Flood zone  (e.g. "X", "AE")
    flood_zone = _coerce_str(
        assessment.get("floodZone") or
        assessment.get("flood_zone") or
        item.get("floodZone") or "")

    # Land use code  (e.g. "RESIDENTIAL", "SFR")
    land_use = _coerce_str(
        item.get("landUse") or
        assessment.get("landUseCode") or
        assessment.get("landUse") or "")

    # Lot dimensions (some tiers return width / depth)
    lot_width = ""
    lot_depth = ""
    lot_dims  = assessment.get("lotDimensions") or assessment.get("lot") or {}
    if isinstance(lot_dims, dict):
        lot_width = str(lot_dims.get("width") or "")
        lot_depth = str(lot_dims.get("depth") or "")

    # Setback data from API (read-only; various field name conventions)
    setbacks = {}
    sb_obj = (assessment.get("setbacks") or assessment.get("setback") or
              item.get("setbacks") or item.get("setback") or {})
    if isinstance(sb_obj, dict):
        for key, label in (("front", "Front"), ("rear", "Rear"), ("side", "Side"),
                           ("frontSetback", "Front"), ("rearSetback", "Rear"),
                           ("sideSetback", "Side"), ("left", "Left"), ("right", "Right")):
            val = sb_obj.get(key)
            if val is not None and str(val).strip():
                setbacks[label] = _coerce_str(val)

    centroid_lat, centroid_lon = compute_centroid(coords)

    return {
        "id":               item_id or parcel_apn,
        "parcel_id":        parcel_apn,
        "display_address":  display_address,
        "area_sqft":        "{:,.0f}".format(area_sqft) if area_sqft else "N/A",
        "area_sqft_raw":    area_sqft,
        "geometry":         geometry,
        "county":           county,
        "state":            state,
        "zoning_code":      zoning_code,
        "legal_description": legal_description,
        "flood_zone":       flood_zone,
        "land_use":         land_use,
        "lot_width":        lot_width,
        "lot_depth":        lot_depth,
        "setbacks":         setbacks,
        # ── shared shape with the worldwide OSM provider ──
        "source":           "LightBox",
        "boundary_kind":    u"Cadastral parcel",
        "country":          u"United States",
        "is_approximate":   False,
        "lat":              centroid_lat,
        "lon":              centroid_lon,
        "subtitle":         u"Cadastral parcel  ·  {}".format(format_area(area_sqft)),
    }


def get_polygon_coords(geometry):
    """
    Extract outer-ring [lon, lat] pairs from our internal geometry dict.
    Supports both Polygon and MultiPolygon types.
    """
    geo_type = geometry.get("type", "")
    coords   = geometry.get("coordinates", [])

    if geo_type == "Polygon":
        return coords[0] if coords else []
    elif geo_type == "MultiPolygon":
        best = []
        for poly in coords:
            if poly and len(poly[0]) > len(best):
                best = poly[0]
        return best
    return []


# ╔═╗╔═╗╦═╗╔═╗╔═╗╦    ╔╦╗╔═╗╔═╗
# ╠═╝╠═╣╠╦╝║  ║╣ ║    ║║║╠═╣╠═╝
# ╩  ╩ ╩╩╚═╚═╝╚═╝╩═╝  ╩ ╩╩ ╩╩   PARCEL MAP
# ==================================================

# The preview is drawn with WPF (Canvas + Path + Image), never System.Drawing:
# GDI+ under pythonnet on .NET 8 was the fragile half of the old PNG pipeline,
# and the boundary must show even when no basemap tile can be fetched.
# Projection, fit, tile layout and tile download: Snippets/_parcel_map.py.
MAP_PADDING_PX = 32          # clear space round the boundary (street context)
MAP_VERTEX_RADIUS = 3.0      # vertex dot radius, preview pixels
MAP_SCALE_BAR_PX = 96        # longest scale bar, preview pixels
MAP_RENDER_DELAY_MS = 200    # resize debounce before redrawing the map
MAP_SAVE_SCALE = 2.0         # Save Map renders the preview at 2x (192 dpi)


def _net_bytes(data):
    """Python bytes -> System.Byte[] in one hop (no per-byte conversion)."""
    import base64
    return System.Convert.FromBase64String(
        base64.b64encode(bytes(data)).decode("ascii"))


def _frozen_bitmap(data):
    """
    PNG/JPEG bytes -> frozen BitmapImage.  CacheOption.OnLoad decodes now so
    the stream can be closed; Freeze makes it safe to hand to any element.
    """
    from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
    stream = IO.MemoryStream(_net_bytes(data))
    try:
        bmp = BitmapImage()
        bmp.BeginInit()
        bmp.CacheOption = BitmapCacheOption.OnLoad
        bmp.StreamSource = stream
        bmp.EndInit()
        bmp.Freeze()
        return bmp
    finally:
        stream.Dispose()


def _parse_geometry(markup):
    """WPF path markup -> frozen Geometry, or None for an empty string."""
    if not markup:
        return None
    from System.Windows.Media import Geometry
    geom = Geometry.Parse(markup)
    if geom.CanFreeze:
        geom.Freeze()
    return geom


def save_element_png(element, path, scale=MAP_SAVE_SCALE, background=None):
    """
    Render a laid-out WPF element to a PNG file at `scale` x its size.

    Drawn through a VisualBrush so the element's position inside its parent
    does not shift the picture (RenderTargetBitmap.Render(element) would).
    Returns (width_px, height_px).
    """
    from System.Windows import Rect
    from System.Windows.Media import (DrawingVisual, VisualBrush, PixelFormats,
                                      BrushMappingMode, Stretch)
    from System.Windows.Media.Imaging import (RenderTargetBitmap,
                                              PngBitmapEncoder, BitmapFrame)
    width = float(element.ActualWidth)
    height = float(element.ActualHeight)
    if width < 1 or height < 1:
        raise ValueError("the map preview has no size yet")
    px_w = int(math.ceil(width * scale))
    px_h = int(math.ceil(height * scale))

    brush = VisualBrush(element)
    brush.ViewboxUnits = BrushMappingMode.Absolute
    brush.Viewbox = Rect(0.0, 0.0, width, height)
    brush.Stretch = Stretch.Fill

    visual = DrawingVisual()
    ctx = visual.RenderOpen()
    try:
        if background is not None:
            ctx.DrawRectangle(background, None, Rect(0.0, 0.0, width, height))
        ctx.DrawRectangle(brush, None, Rect(0.0, 0.0, width, height))
    finally:
        ctx.Close()

    target = RenderTargetBitmap(px_w, px_h, 96.0 * scale, 96.0 * scale,
                                PixelFormats.Pbgra32)
    target.Render(visual)
    encoder = PngBitmapEncoder()
    encoder.Frames.Add(BitmapFrame.Create(target))
    stream = IO.FileStream(path, IO.FileMode.Create, IO.FileAccess.Write)
    try:
        encoder.Save(stream)
    finally:
        stream.Close()
    return px_w, px_h


# ╔═╗╔═╗╔╦╗╔╗ ╔═╗╔═╗╦╔═  ╔═╗╔═╗╦
# ╚═╗║╣  ║ ╠╩╗╠═╣║  ╠╩╗  ╠═╣╠═╝║
# ╚═╝╚═╝ ╩ ╚═╝╩ ╩╚═╝╩ ╩  ╩ ╩╩  ╩ SETBACK / ZONING
# ==================================================


# ── polygon math (pure Python, no shapely) ───────────────────────────────────


# ╦═╗╔═╗╦  ╦╦╔╦╗  ╔═╗╦═╗╔═╗╔═╗╔╦╗╦╔═╗╔╗╔
# ╠╦╝║╣ ╚╗╔╝║ ║   ║  ╠╦╝║╣ ╠═╣ ║ ║║ ║║║║
# ╩╚═╚═╝ ╚╝ ╩ ╩   ╚═╝╩╚═╚═╝╩ ╩ ╩ ╩╚═╝╝╚╝ REVIT CREATION
# ==================================================

def get_project_base_point(doc):
    """Get the project base point in Revit internal feet."""
    collector = DB.FilteredElementCollector(doc).OfCategory(
        DB.BuiltInCategory.OST_ProjectBasePoint
    ).WhereElementIsNotElementType().ToElements()
    if collector:
        bp = collector[0]
        loc = bp.Location
        if hasattr(loc, 'Point'):
            return loc.Point
    return DB.XYZ(0, 0, 0)


def set_project_geo_location(doc, lat, lon, place_name=None):
    """
    Point the project's site location at the parcel (Revit stores latitude and
    longitude in radians).  Runs in its own transaction.

    Returns True on success; logs and returns False if the API refuses.
    """
    try:
        site = doc.SiteLocation
        if site is None:
            return False
        with disposing(DB.Transaction(doc, "Set Project Geo Location")) as t:
            t.Start()
            site.Latitude = math.radians(float(lat))
            site.Longitude = math.radians(float(lon))
            if place_name:
                try:
                    site.PlaceName = place_name[:255]
                except Exception:
                    pass    # PlaceName is read-only in some Revit versions
            t.Commit()
        return True
    except Exception as ex:
        logger.warning("Could not set project geo location: {}".format(ex))
        return False


def get_survey_point(doc):
    """Get the survey point in Revit internal feet."""
    collector = DB.FilteredElementCollector(doc).OfCategory(
        DB.BuiltInCategory.OST_SharedBasePoint
    ).WhereElementIsNotElementType().ToElements()
    if collector:
        sp = collector[0]
        loc = sp.Location
        if hasattr(loc, 'Point'):
            return loc.Point
    return DB.XYZ(0, 0, 0)


def create_property_lines_in_revit(doc, coordinates, elevation_ft=0.0,
                                   line_category=LINE_CAT_PROPERTY,
                                   origin_mode="Project Base Point",
                                   vn2000_points=None):
    """
    Create the property boundary in the Revit document.

    Parameters:
        doc           - Revit Document
        coordinates   - list of [lon, lat] from GeoJSON outer ring
        elevation_ft  - Z elevation in feet
        line_category - "Property Line" | "Model Lines" | "Detail Lines"
        origin_mode   - where to place the centroid
        vn2000_points - optional list of raw VN-2000 points [{'x': Northing, 'y': Easting}, ...]
                        If present, uses direct sub-millimeter metric planar mapping.

    Returns (count, kind) where *kind* names what was actually created.
    """
    if len(coordinates) < 2:
        raise ValueError("Need at least 2 coordinates to create lines")

    revit_pts = []
    if vn2000_points and len(vn2000_points) >= 2:
        # Direct metric planar coordinates
        # X in VN-2000 is Northing (meters) -> Revit Y (feet)
        # Y in VN-2000 is Easting (meters) -> Revit X (feet)
        c_north = sum(p['x'] for p in vn2000_points) / float(len(vn2000_points))
        c_east  = sum(p['y'] for p in vn2000_points) / float(len(vn2000_points))
        M2FT = 3.280839895013123
        for p in vn2000_points:
            dx_ft = (p['y'] - c_east) * M2FT   # Easting delta -> Revit X
            dy_ft = (p['x'] - c_north) * M2FT  # Northing delta -> Revit Y
            revit_pts.append(DB.XYZ(dx_ft, dy_ft, elevation_ft))
    else:
        # Compute centroid for coordinate origin
        centroid_lat, centroid_lon = compute_centroid(coordinates)

        # Convert all coords to Revit XYZ (feet)
        for c in coordinates:
            lon, lat = c[0], c[1]
            x_ft, y_ft = latlon_to_feet(lat, lon, centroid_lat, centroid_lon)
            revit_pts.append(DB.XYZ(x_ft, y_ft, elevation_ft))

    # Determine insertion offset (project base / survey / world origin)
    if origin_mode == "Survey Point":
        offset = get_survey_point(doc)
    elif origin_mode == "Project Base Point":
        offset = get_project_base_point(doc)
    else:
        offset = DB.XYZ(0, 0, 0)

    # Translate points to chosen origin
    revit_pts = [DB.XYZ(pt.X + offset.X, pt.Y + offset.Y, pt.Z + offset.Z)
                 for pt in revit_pts]

    # Close the loop: first == last
    if revit_pts[0].DistanceTo(revit_pts[-1]) > 0.001:
        revit_pts.append(revit_pts[0])

    lines_created = 0
    kind = line_category

    with disposing(DB.Transaction(doc, "Create Property Lines")) as t:
        t.Start()

        if line_category == LINE_CAT_PROPERTY:
            # Prefer a genuine PropertyLine element if this Revit exposes one.
            lines_created = _create_native_property_lines(doc, revit_pts)
            if lines_created is None:
                # It does not (see native_property_line_available), so draw the
                # boundary as model lines carrying the Property Line style.
                style = get_or_create_line_style(doc)
                lines_created = _create_model_lines_from_pts(
                    doc, revit_pts, elevation_ft, line_style=style)
                kind = (u"model lines on the '{}' style".format(
                    PROPERTY_LINE_STYLE_NAME) if style is not None
                    else u"model lines")
            else:
                kind = u"native property line"
        elif line_category == "Detail Lines":
            lines_created = _create_detail_lines(doc, revit_pts)
            kind = u"detail lines"
        else:
            lines_created = _create_model_lines(doc, revit_pts, elevation_ft)
            kind = u"model lines"

        t.Commit()

    return lines_created, kind


def native_property_line_available():
    """
    Whether this Revit build exposes any way to create a PropertyLine element.

    As of Revit 2026, Autodesk.Revit.DB.PropertyLine is a bare Element subclass
    with no members at all - no Create, no properties - so property lines can
    only be drawn through the UI (Massing & Site > Property Line).  This probe
    exists so the tool picks the real thing up automatically if a future
    release adds a factory, rather than silently staying on model lines.
    """
    prop_line = getattr(DB, "PropertyLine", None)
    if prop_line is None:
        return None
    for name in ("Create", "CreateByCurveLoop", "NewPropertyLine"):
        factory = getattr(prop_line, name, None)
        if factory is not None:
            return name
    return None


def _create_native_property_lines(doc, pts):
    """
    Create native PropertyLine elements when the running Revit supports it.

    Returns the segment count, or None when there is no native API - the
    caller then falls back to model lines on the Property Line style and
    reports honestly that it did so.
    """
    factory_name = native_property_line_available()
    if not factory_name:
        return None
    try:
        curve_loop = DB.CurveLoop()
        for i in range(len(pts) - 1):
            start, end = pts[i], pts[i + 1]
            if start.DistanceTo(end) < 0.001:
                continue
            curve_loop.Append(DB.Line.CreateBound(start, end))
        factory = getattr(DB.PropertyLine, factory_name)
        if factory(doc, curve_loop):
            return len(pts) - 1
    except Exception as ex:
        logger.warning(
            "Native PropertyLine creation via {} failed: {}".format(
                factory_name, ex))
    return None


PROPERTY_LINE_STYLE_NAME = "Property Line"


def get_or_create_line_style(doc, name=PROPERTY_LINE_STYLE_NAME,
                             rgb=(200, 30, 30), weight=5):
    """
    Find (or create) a line style under Lines, and return its GraphicsStyle.

    Must be called inside an open transaction - NewSubcategory modifies the
    document.  Returns None if the style cannot be provided, so callers can
    carry on with the default style rather than losing the geometry.
    """
    try:
        lines_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
        for sub in lines_cat.SubCategories:
            if sub.Name == name:
                return sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)

        sub = doc.Settings.Categories.NewSubcategory(lines_cat, name)
        try:
            sub.LineColor = DB.Color(rgb[0], rgb[1], rgb[2])
            sub.SetLineWeight(weight, DB.GraphicsStyleType.Projection)
        except Exception as ex:
            logger.debug("Line style cosmetics skipped: {}".format(ex))
        return sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
    except Exception as ex:
        logger.warning("Could not provide the '{}' line style: {}".format(
            name, ex))
        return None


def _create_model_lines(doc, pts, elevation_ft):
    """Create ModelLine elements on a horizontal sketch plane."""
    return _create_model_lines_from_pts(doc, pts, elevation_ft)


def _create_model_lines_from_pts(doc, pts, elevation_ft, line_style=None):
    """
    Internal: create model lines from a list of XYZ points.

    *line_style* is an optional GraphicsStyle applied to every curve, which is
    how the boundary lands in the Property Line line style rather than the
    default <Lines>.
    """
    count = 0
    try:
        # Build sketch plane at the given elevation
        normal = DB.XYZ.BasisZ
        origin = DB.XYZ(0, 0, elevation_ft)
        plane = DB.Plane.CreateByNormalAndOrigin(normal, origin)
        sketch_plane = DB.SketchPlane.Create(doc, plane)

        for i in range(len(pts) - 1):
            start = pts[i]
            end = pts[i + 1]
            if start.DistanceTo(end) < 0.001:
                continue
            line = DB.Line.CreateBound(start, end)
            curve = doc.Create.NewModelCurve(line, sketch_plane)
            if line_style is not None and curve is not None:
                try:
                    curve.LineStyle = line_style
                except Exception as ex:
                    logger.debug("Line style not applied: {}".format(ex))
            count += 1
    except Exception as ex:
        logger.error("Model line creation error: {}".format(ex))
        raise
    return count


def _create_detail_lines(doc, pts):
    """Create DetailLine elements in the active view."""
    count = 0
    active_view = doc.ActiveView

    # Detail lines only work in 2D views
    if active_view.ViewType not in [DB.ViewType.FloorPlan,
                                     DB.ViewType.CeilingPlan,
                                     DB.ViewType.Section,
                                     DB.ViewType.Elevation,
                                     DB.ViewType.Detail]:
        logger.warning("Active view is not a 2D view. Switching to Model Lines.")
        return _create_model_lines_from_pts(doc, pts, pts[0].Z)

    for i in range(len(pts) - 1):
        start = pts[i]
        end = pts[i + 1]
        if start.DistanceTo(end) < 0.001:
            continue
        line = DB.Line.CreateBound(start, end)
        doc.Create.NewDetailCurve(active_view, line)
        count += 1
    return count


# ╔╦╗╦╔═╗╦  ╔═╗╔═╗
#  ║║║╠═╣║  ║ ║║ ╦
# ═╩╝╩╩ ╩╩═╝╚═╝╚═╝ WPF DIALOG
# ==================================================

class ParcelItem(object):
    """Data object for ListView binding."""
    def __init__(self, data):
        self.id              = data["id"]
        self.parcel_id       = data["parcel_id"]
        self.display_address = data["display_address"]
        self.area_sqft       = data["area_sqft"]
        self.area_sqft_raw   = data["area_sqft_raw"]
        self.geometry        = data["geometry"]
        self.county          = data["county"]
        self.state           = data["state"]
        self.zoning_code       = data.get("zoning_code", "")
        self.legal_description = data.get("legal_description", "")
        self.flood_zone        = data.get("flood_zone", "")
        self.land_use          = data.get("land_use", "")
        self.lot_width         = data.get("lot_width", "")
        self.lot_depth         = data.get("lot_depth", "")
        self.setbacks          = data.get("setbacks", {})
        # Worldwide fields (present for every source; see
        # Snippets._parcel_search.search_primary)
        self.source            = data.get("source", "LightBox")
        self.source_label      = data.get("source_label") or self.source
        self.boundary_kind     = data.get("boundary_kind", u"Cadastral parcel")
        self.country           = data.get("country", "")
        self.is_approximate    = bool(data.get("is_approximate", False))
        self.lat               = data.get("lat", 0.0)
        self.lon               = data.get("lon", 0.0)
        self.vn2000_points     = data.get("vn2000_points", None)
        self.subtitle          = data.get(
            "subtitle",
            u"{}  ·  {}".format(self.boundary_kind,
                                     format_area(self.area_sqft_raw)))


class PropertyLineDialog(T3WPFWindow):
    """Main WPF dialog for Property Line Tool."""

    def __init__(self):
        # Build absolute path so forms.WPFWindow finds the XAML regardless of
        # which script calls this class (avoids the IronPython absolute-URI bug
        # that occurs with Application.LoadComponent + file:// URIs)
        xaml_path = os.path.join(os.path.dirname(__file__), "Tools", "PropertyLine.xaml")
        T3WPFWindow.__init__(self, xaml_path)

        self._selected_parcel = None
        self._parcels = []
        self._zoning_data = None
        # Bumped on every search so a slow second pass from an earlier search
        # cannot append its results onto a newer one.
        self._search_seq = 0

        # Map preview.  _map_token is a plain dict the tile thread polls: a
        # newer render flips "live" off so stale tiles are dropped, without
        # the worker thread ever touching the window.
        self._map_parcel = None
        self._map_view = None
        self._map_state = "empty"       # empty | loading | ready | partial | offline
        self._map_token = {"live": False}
        self._map_size = (0, 0)
        self._map_timer = None
        try:
            self.Closed += self._stop_map_preview
        except Exception as ex:
            logger.debug("Map preview close hook not set: {}".format(ex))

        if not (HAS_GEOPARCEL and HAS_PARCEL_SEARCH):
            self._set_status(
                u"Search is unavailable: lib/Snippets/_geoparcel.py or "
                u"_parcel_search.py failed to import. Reinstall the T3Lab "
                u"extension, then restart Revit.", error=True)

    # ───────────────────────────────────── GUI EVENTS

    def header_drag(self, sender, e):
        from System.Windows.Input import MouseButtonState
        if e.LeftButton == MouseButtonState.Pressed:
            self.DragMove()


    def btn_minimize_Click(self, sender, e):
        import System.Windows
        self.WindowState = System.Windows.WindowState.Minimized

    def btn_close_Click(self, sender, e):
        self.Close()


    def txt_address_KeyDown(self, sender, e):
        if e.Key == Key.Return:
            self.btn_search_Click(sender, e)

    def btn_search_Click(self, sender, e):
        query = self.txt_address.Text.strip()
        if not query:
            self._show_address_warning(
                u"Enter an address, place name or coordinates — anywhere in "
                u"the world.")
            return
        is_coords = bool(HAS_PARCEL_SEARCH and
                         parcel_search.parse_coordinates(query))
        if len(query) < 4 and not is_coords:
            self._show_address_warning(
                u"Address looks too short — include the street, city and "
                u"country. Example: 268 Ly Thuong Kiet, District 10, "
                u"Ho Chi Minh City, Vietnam")
            return
        if not HAS_PARCEL_SEARCH:
            self._set_status(
                u"Search is unavailable: lib/Snippets/_parcel_search.py failed "
                u"to import. Reinstall the T3Lab extension, then restart Revit.",
                error=True)
            return
        self._hide_address_warning()
        self._clear_map_preview(u"Searching for boundaries...")

        # No source picker: the pipeline geocodes worldwide and picks the
        # best parcel source for the location.  LightBox is only used for US
        # locations when a key is set in the T3Lab config (lightbox_api_key).
        api_key = load_config().get("lightbox_api_key", "")

        self._set_status(u"Searching for property boundaries...", busy=True)
        self._show_results_message(u"Searching for property boundaries...")
        self.btn_search.IsEnabled = False
        self._search_seq += 1
        seq = self._search_seq

        # Background thread so the UI stays live.  Two passes: the geocoder
        # answers in about a second while Overpass takes considerably longer,
        # so the first batch is painted as soon as it lands.
        def search_thread():
            try:
                outcome = parcel_search.search_primary(
                    query, lightbox_search=search_parcels,
                    lightbox_key=api_key, limit=MAX_RESULTS)
            except parcel_search.SearchError as ex:
                error_msg = u"{}".format(ex)
                self.Dispatcher.Invoke(
                    DispatcherPriority.Normal,
                    Action(lambda: self._on_search_error(error_msg, seq, True))
                )
                return
            except Exception as ex:
                error_msg = u"{}".format(ex)
                logger.error(traceback.format_exc())
                self.Dispatcher.Invoke(
                    DispatcherPriority.Normal,
                    Action(lambda: self._on_search_error(error_msg, seq))
                )
                return

            parcels = outcome["parcels"]
            context = outcome["context"]
            has_more = bool(context)
            self.Dispatcher.Invoke(
                DispatcherPriority.Normal,
                Action(lambda: self._on_search_complete(parcels, has_more, seq))
            )
            if not has_more:
                return

            # The second pass must ALWAYS report back, or the footer stays on
            # "Working..." for good: search_more() is meant never to raise,
            # but anything that escapes it still ends the busy state.
            try:
                extra = parcel_search.search_more(context)
            except Exception:
                logger.warning("Boundary second pass failed: {}".format(
                    traceback.format_exc()))
                extra = []
            try:
                self.Dispatcher.Invoke(
                    DispatcherPriority.Background,
                    Action(lambda: self._on_search_more(extra, seq))
                )
            except Exception:
                logger.warning("Could not post the second-pass results: "
                               "{}".format(traceback.format_exc()))

        t = threading.Thread(target=search_thread)
        t.daemon = True
        t.start()

    def _is_current(self, seq):
        """False once a newer search has started - drop the stale callback."""
        return seq is None or seq == self._search_seq

    def _on_search_complete(self, parcels, more=False, seq=None):
        if not self._is_current(seq):
            return
        # Re-enabled straight away: the second pass is opportunistic and must
        # never hold the Search button hostage to a loaded Overpass mirror.
        self.btn_search.IsEnabled = True
        self._hide_address_warning()
        self._parcels = list(parcels)

        # Populate ListView
        self.lv_parcels.Items.Clear()
        for p in parcels:
            self.lv_parcels.Items.Add(ParcelItem(p))

        if not parcels:
            if more:
                # The geocoder located the address but carried no polygon;
                # Overpass may still turn one up, so do not declare failure.
                msg = (u"Location found. Looking for mapped boundaries on "
                       u"OpenStreetMap...")
                self._set_status(msg, busy=True)
            else:
                msg = (u"No property boundary found. Try a more specific "
                       u"address, add the city and country, or type the "
                       u"coordinates.")
                self._set_status(msg)
            self._show_results_message(msg)
            return

        self.lv_parcels.Visibility = Visibility.Visible
        self.border_no_results.Visibility = Visibility.Collapsed
        # Best match selected straight away: details and the map preview
        # follow from SelectionChanged, so the boundary shows without a click.
        self.lv_parcels.SelectedIndex = 0

        found = u"Found {} boundar{}".format(
            len(parcels), u"y" if len(parcels) == 1 else u"ies")
        origin = parcel_search.describe_sources(parcels)
        if origin:
            found += u" — " + origin[0].lower() + origin[1:]

        # Surface auto-correction hint if address was normalised
        corrected_from = parcels[0].get("_corrected_from") if parcels else None
        if corrected_from:
            msg = u"{}. Address auto-corrected from '{}'.".format(
                found, corrected_from)
        elif more:
            msg = (u"{}. Looking for more nearby boundaries on OpenStreetMap "
                   u"(can take up to a minute) — you can carry on.".format(found))
        elif len(parcels) == 1:
            msg = u"{}. Check it on the map, then create the lines.".format(found)
        else:
            msg = (u"{}. The best match is selected — pick another to "
                   u"compare.".format(found))
        self._set_status(msg, busy=bool(more))

    def _on_search_more(self, parcels, seq=None):
        """Append the slow Overpass results to whatever is already listed."""
        if not self._is_current(seq):
            return
        self.btn_search.IsEnabled = True
        for p in parcels or []:
            try:
                row = ParcelItem(p)
            except Exception as ex:         # one malformed record, not the batch
                logger.warning("Skipping boundary record: {}".format(ex))
                continue
            self._parcels.append(p)
            self.lv_parcels.Items.Add(row)

        if not self._parcels:
            msg = (u"No mapped boundary at that address. Try a nearby "
                   u"address, or type the coordinates of the plot.")
            self._set_status(msg)
            self._show_results_message(msg)
            return

        self.lv_parcels.Visibility = Visibility.Visible
        self.border_no_results.Visibility = Visibility.Collapsed
        if getattr(self.lv_parcels, "SelectedItem", None) is None:
            self.lv_parcels.SelectedIndex = 0       # first pass had nothing
        count = len(self._parcels)
        found = u"Found {} boundar{}".format(count, u"y" if count == 1 else u"ies")
        origin = parcel_search.describe_sources(self._parcels)
        if origin:
            found += u" — " + origin[0].lower() + origin[1:]
        if count == 1:
            self._set_status(u"{}. Check it on the map, then create the "
                             u"lines.".format(found))
        else:
            self._set_status(u"{}. Pick one to see it on the map.".format(found))

    def _show_results_message(self, msg):
        """Hide the parcel list and show `msg` as its single empty state."""
        self.lv_parcels.Visibility = Visibility.Collapsed
        self.txt_no_results.Text = msg
        self.border_no_results.Visibility = Visibility.Visible

    def _show_address_warning(self, msg):
        self.txt_address_warning.Text = msg
        self.txt_address_warning.Visibility = Visibility.Visible

    def _hide_address_warning(self):
        self.txt_address_warning.Visibility = Visibility.Collapsed

    def _on_search_error(self, error_msg, seq=None, user_facing=False):
        if not self._is_current(seq):
            return
        self.btn_search.IsEnabled = True
        if user_facing:
            # Already says what failed, where, and what to do next.
            msg = error_msg
            logger.warning("Boundary search stopped: {}".format(error_msg))
        else:
            err_lower = error_msg.lower()
            is_network = any(k in err_lower for k in (
                "connection", "connect", "timeout", "timed out", "network",
                "socket", "ssl", "certificate", "unreachable", "refused",
                "reset", "httperror", "urlerror", "ioerror", "errno",
                "resolve", "proxy"))
            if is_network:
                logger.warning("Boundary lookup network error: {}".format(error_msg))
                msg = (u"Could not reach the map data service — check the "
                       u"internet connection (and proxy settings), then "
                       u"search again.")
            else:
                logger.error("Boundary search error: {}".format(error_msg))
                msg = (u"Search failed: {}. Try again; if it keeps failing, "
                       u"send the pyRevit log to T3Lab.".format(error_msg))
        self._set_status(msg, error=True)
        self._show_results_message(msg)

    def lv_parcels_SelectionChanged(self, sender, e):
        item = self.lv_parcels.SelectedItem
        if not item:
            self._selected_parcel = None
            self.btn_create.IsEnabled = False
            self.grp_parcel_details.Visibility = Visibility.Collapsed
            self.grp_setback.Visibility = Visibility.Collapsed
            self.scroll_details.Visibility = Visibility.Collapsed
            panel = getattr(self, "grp_metes", None)
            if panel is not None:
                panel.Visibility = Visibility.Collapsed
            self._clear_map_preview()
            return

        self._selected_parcel = item
        self._zoning_data = None
        self.scroll_details.Visibility = Visibility.Visible
        self._show_parcel_details(item)
        self.btn_create.IsEnabled = True

        # Show setback group only when API data is present
        if item.setbacks:
            self._populate_setback_display(item.setbacks)
            self.grp_setback.Visibility = Visibility.Visible
        else:
            self.grp_setback.Visibility = Visibility.Collapsed

        # Boundary map: the polygon is drawn at once, the basemap tiles fill
        # in behind it as they arrive.
        self._show_map_preview(item)

    # ───────────────────────────────────── MAP PREVIEW
    # Layers (PropertyLine.xaml, grid_map_host): cnv_map_tiles (OSM tiles,
    # added here) under path_map_fill / path_map_boundary / path_map_vertices,
    # then grid_map_overlay (scale bar + north arrow), border_map_note
    # (basemap state) and border_map_attrib (OSM attribution).

    def _show_map_preview(self, item):
        """Preview `item`: boundary immediately, basemap in the background."""
        self._map_parcel = item
        try:
            # The details column may have opened in this same handler and
            # narrowed the map: lay out now so the fit uses the real size.
            self.UpdateLayout()
        except Exception as ex:
            logger.debug("Layout before map render skipped: {}".format(ex))
        self._render_map_preview()

    def _clear_map_preview(self, message=None):
        """Back to the empty state; a tile download still running is dropped."""
        self._map_token["live"] = False
        self._map_parcel = None
        self._map_view = None
        self._reset_map_layers()
        self._map_message(message or
                          u"Search, then pick a result to preview its boundary")

    def _map_message(self, message):
        """Show `message` as the map's empty state (no boundary drawn)."""
        self._map_state = "empty"
        self.grid_map_overlay.Visibility = Visibility.Collapsed
        self.border_map_note.Visibility = Visibility.Collapsed
        self.txt_map_empty.Text = message
        self.pnl_map_empty.Visibility = Visibility.Visible

    def _reset_map_layers(self):
        self.cnv_map_tiles.Children.Clear()
        self.path_map_fill.Data = None
        self.path_map_boundary.Data = None
        self.path_map_vertices.Data = None
        self.border_map_attrib.Visibility = Visibility.Collapsed

    def _set_map_note(self, state, note=None, detail=None):
        """Basemap state badge: shown while loading or when tiles failed."""
        self._map_state = state
        if note:
            self.txt_map_note.Text = note
            self.border_map_note.ToolTip = detail or note
            self.border_map_note.Visibility = Visibility.Visible
        else:
            self.border_map_note.ToolTip = None
            self.border_map_note.Visibility = Visibility.Collapsed

    def grid_map_host_SizeChanged(self, sender, e):
        """Refit after a resize - debounced, so dragging does not redraw per pixel."""
        # getattr: the handler is wired inside T3WPFWindow.__init__, before
        # this dialog's own __init__ has set the map state.
        if getattr(self, "_map_parcel", None) is None:
            return
        size = (int(self.grid_map_host.ActualWidth),
                int(self.grid_map_host.ActualHeight))
        if size == self._map_size:
            return
        if self._map_timer is None:
            from System import TimeSpan
            from System.Windows.Threading import DispatcherTimer
            self._map_timer = DispatcherTimer()
            self._map_timer.Interval = TimeSpan.FromMilliseconds(
                MAP_RENDER_DELAY_MS)
            self._map_timer.Tick += self._map_timer_tick
        self._map_timer.Stop()
        self._map_timer.Start()

    def _map_timer_tick(self, sender, e):
        self._map_timer.Stop()
        size = (int(self.grid_map_host.ActualWidth),
                int(self.grid_map_host.ActualHeight))
        if size == self._map_size:
            return                  # already drawn at this size
        try:
            self._render_map_preview()
        except Exception:
            logger.warning("Map preview redraw failed: {}".format(
                traceback.format_exc()))

    def _stop_map_preview(self, sender=None, e=None):
        """Window closed: stop the tile thread from posting back."""
        self._map_token["live"] = False
        if self._map_timer is not None:
            self._map_timer.Stop()

    def _render_map_preview(self):
        """
        Draw the selected boundary to fit the preview, then start the basemap.

        The boundary, vertex dots, scale bar and north arrow come from the
        polygon alone, so they show even offline; tiles are a bonus.
        """
        item = self._map_parcel
        if item is None:
            return
        self._map_token["live"] = False           # orphan the previous run
        token = {"live": True}
        self._map_token = token
        self._reset_map_layers()

        if not HAS_PARCEL_MAP:
            self._map_message(
                u"Map preview is unavailable: lib/Snippets/_parcel_map.py "
                u"failed to import. Reinstall the T3Lab extension, then "
                u"restart Revit.")
            return

        ring = get_polygon_coords(item.geometry)
        width = float(self.grid_map_host.ActualWidth or 0)
        height = float(self.grid_map_host.ActualHeight or 0)
        self._map_size = (int(width), int(height))
        if width < 16 or height < 16:
            return                  # not laid out yet; SizeChanged calls back
        view = parcel_map.fit_view(ring, width, height, padding=MAP_PADDING_PX)
        if view is None:
            self._map_view = None
            self._map_message(u"This result has no boundary geometry to "
                              u"preview. Pick another result.")
            return
        self._map_view = view

        # 1 · the boundary - never waits for the network
        points = view.screen_ring(ring)
        outline = _parse_geometry(parcel_map.path_data(points))
        self.path_map_fill.Data = outline
        self.path_map_boundary.Data = outline
        self.path_map_vertices.Data = _parse_geometry(
            parcel_map.markers_data(points, MAP_VERTEX_RADIUS))
        self._draw_scale_bar(view)
        self.pnl_map_empty.Visibility = Visibility.Collapsed
        self.grid_map_overlay.Visibility = Visibility.Visible

        # 2 · basemap: cached tiles now, the rest off the UI thread
        missing = []
        for spec in view.tiles():
            data = parcel_map.cached_tile(spec.key)
            if data is None:
                missing.append(spec)
            else:
                self._add_map_tile(spec, data)
        if not missing:
            self._set_map_note("ready")
            return
        self._set_map_note("loading", u"Loading basemap...",
                           u"Downloading map tiles from OpenStreetMap.")
        self._fetch_map_tiles(missing, token)

    def _draw_scale_bar(self, view):
        bar = parcel_map.scale_bar(view.metres_per_pixel(),
                                   min(MAP_SCALE_BAR_PX, view.width * 0.3))
        if bar is None:
            self.txt_map_scale.Text = u""
            self.bar_map_scale.Width = 0.0
            return
        _length_m, length_px, label = bar
        self.txt_map_scale.Text = label
        self.bar_map_scale.Width = max(4.0, float(length_px))

    def _add_map_tile(self, spec, data):
        """Place one basemap tile; False when its bytes do not decode."""
        try:
            bitmap = _frozen_bitmap(data)
        except Exception as ex:
            logger.debug("Map tile {} not decoded: {}".format(spec.key, ex))
            return False
        from System.Windows.Controls import Canvas, Image
        from System.Windows.Media import Stretch, RenderOptions, BitmapScalingMode
        img = Image()
        img.Source = bitmap
        img.Width = float(spec.size_x)
        img.Height = float(spec.size_y)
        img.Stretch = Stretch.Fill
        img.IsHitTestVisible = False
        RenderOptions.SetBitmapScalingMode(img, BitmapScalingMode.HighQuality)
        Canvas.SetLeft(img, float(spec.left))
        Canvas.SetTop(img, float(spec.top))
        self.cnv_map_tiles.Children.Add(img)
        self.border_map_attrib.Visibility = Visibility.Visible
        return True

    def _fetch_map_tiles(self, specs, token):
        """Download `specs` on a worker thread; tiles are placed as they land."""
        dispatcher = self.Dispatcher        # read on the UI thread

        def is_live():
            return token["live"]

        def on_tile(spec, data):
            dispatcher.Invoke(
                DispatcherPriority.Background,
                Action(lambda: self._on_map_tile(token, spec, data)))

        def worker():
            try:
                result = parcel_map.fetch_tiles(specs, on_tile=on_tile,
                                                is_live=is_live)
            except Exception as ex:
                logger.warning("Basemap download failed: {}".format(
                    traceback.format_exc()))
                result = (0, len(specs), u"{}".format(ex))
            if result is None or not token["live"]:
                return                      # superseded or window closed
            failed, error = result[1], result[2]
            try:
                dispatcher.Invoke(
                    DispatcherPriority.Background,
                    Action(lambda: self._on_map_tiles_done(token, failed, error)))
            except Exception:
                logger.warning("Could not finish the basemap: {}".format(
                    traceback.format_exc()))

        t = threading.Thread(target=worker)
        t.daemon = True
        t.start()

    def _on_map_tile(self, token, spec, data):
        if token["live"]:
            self._add_map_tile(spec, data)

    def _on_map_tiles_done(self, token, failed, error):
        if not token["live"]:
            return
        if self.cnv_map_tiles.Children.Count == 0:
            reason = error or u"no tile could be decoded"
            logger.warning("Basemap unavailable: {}".format(reason))
            self._set_map_note(
                "offline", u"Basemap unavailable",
                u"No map tiles from tile.openstreetmap.org ({}). The boundary "
                u"is drawn from its own coordinates, so it is still correct. "
                u"Check the internet connection or proxy, then select the "
                u"result again.".format(reason))
        elif failed:
            self._set_map_note("partial")
            logger.debug("Basemap incomplete: {} tile(s) missing ({})".format(
                failed, error))
        else:
            self._set_map_note("ready")

    def _show_parcel_details(self, item):
        self.grp_parcel_details.Visibility = Visibility.Visible

        self.txt_detail_id.Text      = item.parcel_id or "N/A"
        self.txt_detail_address.Text = item.display_address or "N/A"
        self.txt_detail_county.Text  = item.county or "N/A"
        self.txt_detail_state.Text   = item.state or "N/A"

        # Worldwide rows (present in the XAML since v2)
        try:
            self.txt_detail_country.Text = item.country or "N/A"
            self.txt_detail_source.Text  = u"{}  ·  {}".format(
                item.source, item.boundary_kind)
            self.txt_detail_latlon.Text  = u"{:.6f}, {:.6f}".format(
                item.lat or 0.0, item.lon or 0.0)
            self.border_approx_warning.Visibility = (
                Visibility.Visible if item.is_approximate
                else Visibility.Collapsed)
        except AttributeError:
            pass    # older XAML without the worldwide rows

        raw = item.area_sqft_raw
        self.txt_detail_area.Text = format_area(raw) if raw and raw > 0 else "N/A"

        coords = get_polygon_coords(item.geometry)
        self.txt_detail_vertices.Text = "{} vertices".format(len(coords))

        # Extended fields
        self.txt_detail_zoning.Text       = item.zoning_code       or "N/A"
        self.txt_detail_legal.Text        = item.legal_description  or "N/A"
        self.txt_detail_flood.Text        = ("FEMA Zone " + item.flood_zone
                                             if item.flood_zone else "N/A")
        self.txt_detail_land_use.Text     = item.land_use           or "N/A"

        # Refresh project-data preview
        self._refresh_project_data(item)
        self._refresh_metes_and_bounds(item)

    def _refresh_metes_and_bounds(self, item):
        """Fill the distances-and-bearings table for the selected boundary."""
        panel = getattr(self, "grp_metes", None)
        if panel is None:
            return
        coords = get_polygon_coords(item.geometry)
        if not HAS_GEOPARCEL or len(coords) < 3:
            panel.Visibility = Visibility.Collapsed
            return
        try:
            self.txt_metes.Text = geoparcel.format_metes_and_bounds(
                coords, area_sqft=item.area_sqft_raw)
            panel.Visibility = Visibility.Visible
        except Exception as ex:
            logger.warning("Metes and bounds failed: {}".format(ex))
            panel.Visibility = Visibility.Collapsed

    def btn_copy_metes_Click(self, sender, e):
        """Copy the distances-and-bearings table to the Windows clipboard."""
        try:
            from System.Windows import Clipboard
            Clipboard.SetText(self.txt_metes.Text)
            self._set_status(
                u"Distances & bearings copied — paste into Massing & Site "
                u"→ Property Line → Create by entering distances and bearings.",
                success=True)
        except Exception as ex:
            self._set_status("Copy failed: {}".format(ex), error=True)

    def _refresh_project_data(self, item):
        """Rebuild the formatted Project Data text block."""
        loc_parts = [p for p in [item.display_address.split(",")[1].strip()
                                  if "," in item.display_address else "",
                                  item.state] if p]
        jurisdiction = item.display_address  # full address as jurisdiction

        lines = [
            ("JURISDICTION HAVING AUTHORITY", jurisdiction),
            ("COUNTRY",                       item.country or "—"),
            ("LEGAL DESCRIPTION",             item.legal_description or "—"),
            ("PARCEL / FEATURE ID",           item.parcel_id or "—"),
            ("IN FLOOD ZONE (FEMA)",          ("Zone " + item.flood_zone)
                                              if item.flood_zone else "—"),
            ("ZONING",                        item.zoning_code or "—"),
            ("LOT AREA",                      format_area(item.area_sqft_raw)
                                              if item.area_sqft_raw else "—"),
            ("LAND USE",                      item.land_use or "—"),
            ("CENTROID (LAT, LONG)",          u"{:.6f}, {:.6f}".format(
                                                  item.lat or 0.0,
                                                  item.lon or 0.0)),
            ("BOUNDARY SOURCE",               u"{} ({})".format(
                                                  item.source,
                                                  item.boundary_kind)),
        ]

        max_key = max(len(k) for k, _ in lines)
        text_lines = []
        for key, val in lines:
            text_lines.append("{:<{w}}  {}".format(key + ":", val, w=max_key + 1))

        self.txt_project_data.Text = "\n".join(text_lines)
        self.grp_project_data.Visibility = Visibility.Visible

    # ───────────────────────────────────── ZONING / SETBACK

    def _populate_setback_display(self, setbacks):
        """Fill the read-only setback TextBlock from the API dict."""
        lines = [u"{}: {} ft".format(k, v) for k, v in sorted(setbacks.items())]
        self.txt_setback_info.Text = u"  |  ".join(lines) if lines else u"No setback data"


    def btn_copy_project_data_Click(self, sender, e):
        """Copy the formatted Project Data block to the Windows clipboard."""
        try:
            from System.Windows import Clipboard
            Clipboard.SetText(self.txt_project_data.Text)
            self._set_status("Project Data copied to clipboard.", success=True)
        except Exception as ex:
            self._set_status("Copy failed: {}".format(ex), error=True)

    # ───────────────────────────────────── PARCEL MAP

    def btn_download_map_Click(self, sender, e):
        """Save the map preview - boundary, basemap, scale, north - as a PNG."""
        parcel = self._selected_parcel
        if not parcel:
            return
        if self._map_view is None or self._map_parcel is not parcel:
            self._set_status(
                u"The map preview of this boundary is not drawn yet — select "
                u"it in the results list, then press Save Map again.",
                error=True)
            return
        if self._map_state == "loading":
            self._set_status(
                u"The basemap is still loading — wait until the map fills in, "
                u"then press Save Map again.")
            return

        # ── Ask the user where to save ────────────────────────────────────────
        safe_apn = u"".join(
            ch if (ch.isalnum() or ch in u" -_.") else u"_"
            for ch in u"{}".format(parcel.parcel_id or u"boundary")).strip()
        default_name = u"parcel_map_{}.png".format(safe_apn or u"boundary")

        try:
            from Microsoft.Win32 import SaveFileDialog
            dlg = SaveFileDialog()
            dlg.Title      = "Save Parcel Map"
            dlg.FileName   = default_name
            dlg.DefaultExt = ".png"
            dlg.Filter     = "PNG Image (*.png)|*.png|All Files (*.*)|*.*"
            if dlg.ShowDialog() is not True:
                return                          # user cancelled
            out = dlg.FileName
        except Exception:
            # Fallback: temp folder (e.g. SaveFileDialog unavailable)
            import tempfile
            out = os.path.join(tempfile.gettempdir(), default_name)

        try:
            background = None
            try:
                background = self.FindResource("T3.SurfaceSunken")
            except Exception:
                pass
            px_w, px_h = save_element_png(self.grid_map_host, out,
                                          scale=MAP_SAVE_SCALE,
                                          background=background)
        except Exception as ex:
            logger.error("Parcel map save failed: {}".format(
                traceback.format_exc()))
            self._set_status(
                u"Could not save the map to {}: {}. Pick another folder and "
                u"try again.".format(out, ex), error=True)
            return
        self._on_map_complete(out, px_w, px_h)

    def _on_map_complete(self, path, px_w=0, px_h=0):
        size = u" ({} x {} px)".format(px_w, px_h) if px_w and px_h else u""
        note = (u" Saved without basemap — the tiles could not be loaded."
                if self._map_state == "offline" else u"")
        self._set_status(u"Parcel map saved{}: {}.{}".format(size, path, note),
                         success=True)
        try:
            # UseShellExecute is off by default on .NET 8 (Revit 2025+), and
            # a .png is not an executable - open it in the default viewer.
            psi = Diagnostics.ProcessStartInfo(path)
            psi.UseShellExecute = True
            Diagnostics.Process.Start(psi)
        except Exception as ex:
            logger.debug("Could not open the saved map: {}".format(ex))

    def btn_google_maps_Click(self, sender, e):
        if not self._selected_parcel:
            return

        coords = get_polygon_coords(self._selected_parcel.geometry)
        if not coords:
            self._set_status("No geometry available for this parcel.", error=True)
            return

        try:
            centroid_lat, centroid_lon = compute_centroid(coords)
            url = "https://www.google.com/maps/search/?api=1&query={},{}".format(centroid_lat, centroid_lon)
            psi = Diagnostics.ProcessStartInfo(url)
            psi.UseShellExecute = True
            Diagnostics.Process.Start(psi)
            self._set_status("Opened Google Maps in browser.", success=True)
        except Exception as ex:
            self._set_status("Failed to open Google Maps: {}".format(ex), error=True)


    def btn_create_Click(self, sender, e):
        if not self._selected_parcel:
            self._set_status("No parcel selected.", error=True)
            return

        doc = revit.doc
        if not doc:
            self._set_status("No active Revit document.", error=True)
            return

        # Get options
        elevation_ft = self._elevation_in_feet()

        # ComboBox selected item text
        line_cat_item = self.cmb_line_type.SelectedItem
        line_cat = line_cat_item.Content if line_cat_item else "Model Lines"

        origin_item = self.cmb_origin.SelectedItem
        origin_mode = origin_item.Content if origin_item else "Project Base Point"

        # Get coordinates
        coords = get_polygon_coords(self._selected_parcel.geometry)
        if len(coords) < 3:
            self._set_status("Invalid geometry: not enough coordinates.", error=True)
            return

        self._set_status("Creating property lines in Revit...", busy=True)
        self.btn_create.IsEnabled = False

        vn_pts = getattr(self._selected_parcel, "vn2000_points", None)
        try:
            count, kind = create_property_lines_in_revit(
                doc, coords, elevation_ft, line_cat, origin_mode, vn2000_points=vn_pts
            )
            logger.info("Boundary created: {} segments as {}".format(count, kind))

            msg = u"Done! Created {} segment(s) as {} for: {}".format(
                count, kind, self._selected_parcel.display_address)

            if self._wants_geo_location():
                parcel = self._selected_parcel
                if set_project_geo_location(doc, parcel.lat, parcel.lon,
                                            parcel.display_address):
                    msg += u"  ·  Project location set to {:.5f}, {:.5f}.".format(
                        parcel.lat or 0.0, parcel.lon or 0.0)
                else:
                    msg += u"  ·  Could not update the project location."

            self._set_status(msg, success=True)

        except Exception as ex:
            self._set_status("Error creating lines: {}".format(ex), error=True)
            logger.error("Property line creation failed: {}".format(traceback.format_exc()))
        finally:
            self.btn_create.IsEnabled = True

    # ───────────────────────────────────── HELPERS

    def _elevation_in_feet(self):
        """
        Read the elevation box in the unit picked next to it and return feet
        (Revit's internal unit).  Anything unparsable means 0.
        """
        try:
            value = float(self.txt_elevation.Text.strip() or "0")
        except (ValueError, AttributeError):
            return 0.0

        unit = "ft"
        combo = getattr(self, "cmb_elev_unit", None)
        item = combo.SelectedItem if combo is not None else None
        if item is not None and item.Content:
            unit = str(item.Content).strip().lower()
        return value * ELEV_UNITS.get(unit, 1.0)

    def _wants_geo_location(self):
        chk = getattr(self, "chk_set_geo", None)
        if chk is None:
            return False
        return bool(chk.IsChecked)


    def _set_status(self, msg, error=False, success=False, busy=False):
        self.txt_status.Text = msg
        if error:
            text_key, dot_key, label = "T3.Danger.Text", "T3.Danger.Accent", "Error"
        elif success:
            text_key, dot_key, label = "T3.Success.Text", "T3.Success.Accent", "Done"
        elif busy:
            text_key, dot_key, label = "T3.Progress.Fill", "T3.Progress.Fill", "Working..."
        else:
            text_key, dot_key, label = "T3.TextMuted", "T3.TextMuted", "Idle"

        self.txt_status.Foreground = self.FindResource(text_key)
        self.dot_status.Fill = self.FindResource(dot_key)
        self.txt_status_label.Text = label


# ╔═╗╦ ╦╔═╗╦  ╦╔═╗
# ╚═╗╠═╣║ ║║  ║╚═╗
# ╚═╝╩ ╩╚═╝╩═╝╩╚═╝ PUBLIC ENTRY POINT
# ==================================================


    def minimize_button_clicked(self, sender, e):
        import System.Windows
        self.WindowState = System.Windows.WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        import System.Windows
        if self.WindowState == System.Windows.WindowState.Maximized:
            self.WindowState = System.Windows.WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = System.Windows.WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self.Close()
def show_property_line_dialog():
    """Show the Property Line dialog and return when closed."""
    try:
        dlg = PropertyLineDialog()
        dlg.ShowDialog()
    except Exception as ex:
        logger.error("Failed to open Property Line dialog: {}".format(ex))
        logger.error(traceback.format_exc())
        forms.alert(
            "Property Line Tool error:\n{}".format(ex),
            title="Property Line Tool"
        )
