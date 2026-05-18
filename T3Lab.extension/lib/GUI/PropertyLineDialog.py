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
clr.AddReference("System.Drawing")   # bitmap / graphics for parcel map

import System
import System.IO as IO
import System.Drawing as Drawing
import System.Drawing.Imaging as Imaging
import System.Diagnostics as Diagnostics
from System import Uri, Action
from System.Collections.ObjectModel import ObservableCollection
from System.Windows import Window, Visibility, Application
from System.Windows.Input import Key
from System.Windows.Media import SolidColorBrush, Color
from System.Windows.Threading import Dispatcher, DispatcherPriority

# IronPython HTTP (urllib2 available in IronPython 2.x)
try:
    import urllib2
    HAS_URLLIB2 = True
except ImportError:
    HAS_URLLIB2 = False

# CPython / IronPython 3 HTTP
try:
    import urllib.request as urllib_request
    import urllib.parse as urllib_parse
    HAS_URLLIB3 = True
except ImportError:
    HAS_URLLIB3 = False

# pyRevit
from pyrevit import revit, DB, forms, script

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
    """Format area as sqft and acres."""
    acres = sqft / 43560.0
    if acres >= 1.0:
        return "{:,.0f} sqft ({:.3f} ac)".format(sqft, acres)
    return "{:,.0f} sqft".format(sqft)


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
    """
    URL-encode *text*, compatible with both IronPython 2.x (urllib2.quote)
    and CPython / IronPython 3.x (urllib.parse.quote).
    Falls back to a manual encoder if neither is available.
    """
    if HAS_URLLIB2:
        try:
            if isinstance(text, unicode):          # IronPython 2 unicode type
                text = text.encode('utf-8')
        except NameError:
            pass
        return urllib2.quote(text, safe=safe)
    if HAS_URLLIB3:
        return urllib_parse.quote(text, safe=safe)
    # Last-resort manual percent-encoding
    safe_set = set(safe)
    result = []
    for ch in text:
        if ch.isalnum() or ch in '-_.~' or ch in safe_set:
            result.append(ch)
        else:
            result.append('%{:02X}'.format(ord(ch)))
    return ''.join(result)


def http_get(url, headers=None):
    """
    Perform a GET request.
    Returns (status_code, response_body_str).
    Non-2xx responses are returned as (code, body) — NOT raised.
    """
    headers = headers or {}

    if HAS_URLLIB2:
        req = urllib2.Request(url)
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            resp = urllib2.urlopen(req, timeout=15)
            body = resp.read()
            return resp.getcode(), body
        except urllib2.HTTPError as e:
            try:
                body = e.read()
            except Exception:
                body = b""
            return e.code, body
        except Exception as ex:
            raise

    if HAS_URLLIB3:
        req = urllib_request.Request(url, headers=headers)
        try:
            with urllib_request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read()
        except Exception as e:
            if hasattr(e, 'code'):
                try:
                    body = e.read()
                except Exception:
                    body = b""
                return e.code, body
            raise

    raise RuntimeError("No HTTP library available (urllib2 / urllib.request)")


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

_STATE_MAP = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "district of columbia": "DC",
}
_STATE_CODES = set(_STATE_MAP.values())

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

# OSM tile server — free, no API key, requires User-Agent header
_OSM_TILE_URL   = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_OSM_USER_AGENT = "T3Lab-PropertyLine-Tool/1.0 (pyRevit add-in; contact t3lab)"
_TILE_SIZE      = 256   # pixels per OSM tile


def _latlon_to_tile_float(lat, lon, zoom):
    """Return fractional OSM tile (x, y) for the given lat/lon at *zoom*."""
    n   = 2.0 ** zoom
    tx  = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    ty  = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return tx, ty


def _choose_zoom(area_sqft):
    """Pick an OSM zoom level appropriate for the parcel area."""
    if   area_sqft <   5000: return 20
    elif area_sqft <  20000: return 19
    elif area_sqft < 100000: return 18
    elif area_sqft < 500000: return 17
    else:                     return 16


def generate_parcel_map(coordinates, output_path, area_sqft=0):
    """
    Build a PNG file showing the parcel boundary overlaid on an OpenStreetMap
    base map.

    Parameters
    ----------
    coordinates : list of [lon, lat]
        Outer-ring polygon from the Lightbox API / parcel data.
    output_path : str
        Full path where the .png should be written.
    area_sqft : float
        Used to select an appropriate zoom level.

    Returns
    -------
    str
        *output_path* on success.  Raises on failure.
    """
    if not coordinates or len(coordinates) < 3:
        raise ValueError("Need at least 3 coordinate pairs to draw a parcel map")

    lats = [c[1] for c in coordinates]
    lons = [c[0] for c in coordinates]

    zoom = _choose_zoom(area_sqft)

    # ── Compute the tile bounding box with 1-tile padding ────────────────────
    tx_min_f, ty_min_f = _latlon_to_tile_float(max(lats), min(lons), zoom)
    tx_max_f, ty_max_f = _latlon_to_tile_float(min(lats), max(lons), zoom)

    PAD = 1
    tx0 = int(tx_min_f) - PAD
    ty0 = int(ty_min_f) - PAD
    tx1 = int(tx_max_f) + PAD + 1
    ty1 = int(ty_max_f) + PAD + 1

    # Cap at 5×5 so the image stays reasonable
    if tx1 - tx0 > 5:
        ctr = int((tx_min_f + tx_max_f) / 2)
        tx0, tx1 = ctr - 2, ctr + 3
    if ty1 - ty0 > 5:
        ctr = int((ty_min_f + ty_max_f) / 2)
        ty0, ty1 = ctr - 2, ctr + 3

    n_cols = tx1 - tx0
    n_rows = ty1 - ty0
    canvas_w = n_cols * _TILE_SIZE
    canvas_h = n_rows * _TILE_SIZE

    canvas = Drawing.Bitmap(canvas_w, canvas_h)
    g      = Drawing.Graphics.FromImage(canvas)
    g.SmoothingMode = Drawing.Drawing2D.SmoothingMode.AntiAlias
    g.Clear(Drawing.Color.FromArgb(220, 220, 220))   # fallback colour

    # ── Fetch and blit OSM tiles ─────────────────────────────────────────────
    # Trust the OSM server cert (Revit environment may lack root CA store)
    try:
        import System.Net as Net
        Net.ServicePointManager.ServerCertificateValidationCallback = \
            System.Net.Security.RemoteCertificateValidationCallback(lambda *a: True)
    except Exception:
        pass

    headers = {"User-Agent": _OSM_USER_AGENT}
    for tx in range(tx0, tx1):
        for ty in range(ty0, ty1):
            url = "https://tile.openstreetmap.org/{}/{}/{}.png".format(zoom, tx, ty)
            try:
                status, body = http_get(url, headers)
                if status == 200 and body:
                    raw = body if isinstance(body, (bytes, bytearray)) else body.encode('latin-1')
                    ms = IO.MemoryStream(System.Array[System.Byte](bytearray(raw)))
                    tile_bmp = Drawing.Bitmap.FromStream(ms)
                    g.DrawImage(tile_bmp,
                                (tx - tx0) * _TILE_SIZE,
                                (ty - ty0) * _TILE_SIZE)
                    tile_bmp.Dispose()
            except Exception as ex:
                logger.debug("Tile {}/{}/{} skipped: {}".format(zoom, tx, ty, ex))

    # ── Helper: lat/lon → canvas pixel ───────────────────────────────────────
    def _to_px(lat, lon):
        tf_x, tf_y = _latlon_to_tile_float(lat, lon, zoom)
        px = int((tf_x - tx0) * _TILE_SIZE)
        py = int((tf_y - ty0) * _TILE_SIZE)
        return Drawing.Point(px, py)

    pts = [_to_px(c[1], c[0]) for c in coordinates]
    # Remove closing duplicate
    if pts and pts[0].X == pts[-1].X and pts[0].Y == pts[-1].Y:
        pts = pts[:-1]

    if len(pts) >= 3:
        pts_arr = System.Array[Drawing.Point](pts)

        # Semi-transparent red fill
        fill = Drawing.SolidBrush(Drawing.Color.FromArgb(70, 220, 30, 30))
        g.FillPolygon(fill, pts_arr)
        fill.Dispose()

        # Bold red outline
        pen = Drawing.Pen(Drawing.Color.FromArgb(230, 200, 0, 0), 3)
        g.DrawPolygon(pen, pts_arr)
        pen.Dispose()

        # Yellow centroid dot
        cx = sum(p.X for p in pts) // len(pts)
        cy = sum(p.Y for p in pts) // len(pts)
        dot_brush = Drawing.SolidBrush(Drawing.Color.Yellow)
        g.FillEllipse(dot_brush, cx - 6, cy - 6, 12, 12)
        dot_brush.Dispose()

    # ── Attribution label (OSM requires it) ──────────────────────────────────
    try:
        font  = Drawing.Font("Arial", 9)
        brush = Drawing.SolidBrush(Drawing.Color.FromArgb(180, 0, 0, 0))
        label = u"© OpenStreetMap contributors"
        g.DrawString(label, font, brush,
                     Drawing.PointF(4, canvas_h - 18))
        font.Dispose()
        brush.Dispose()
    except Exception:
        pass

    g.Dispose()
    canvas.Save(output_path, Imaging.ImageFormat.Png)
    canvas.Dispose()
    return output_path


# ╔═╗╔═╗╔╦╗╔╗ ╔═╗╔═╗╦╔═  ╔═╗╔═╗╦
# ╚═╗║╣  ║ ╠╩╗╠═╣║  ╠╩╗  ╠═╣╠═╝║
# ╚═╝╚═╝ ╩ ╚═╝╩ ╩╚═╝╩ ╩  ╩ ╩╩  ╩ SETBACK / ZONING
# ==================================================

def get_zoning_from_parcel(parcel_item):
    """
    Extract zoning code from an already-fetched ParcelItem.

    The basic Lightbox tier embeds the municipal zoning code in the parcel
    response under assessment.zoning.assessment.  No separate API call is
    needed (and /v1/parcels/us/{id}/zoning is not available on basic tier).

    Returns dict: {"zoning_code": str}
    Setback distances (front/rear/side) are NOT provided by the basic API
    and must be entered manually by the user.
    """
    return {"zoning_code": parcel_item.zoning_code or ""}


# ── polygon math (pure Python, no shapely) ───────────────────────────────────

def _polygon_signed_area_2d(pts):
    """Signed shoelace area. Positive → CCW."""
    n = len(pts)
    a = 0.0
    for i in range(n):
        j = (i + 1) % n
        a += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1]
    return a / 2.0


def _line_intersect_2d(p1, p2, p3, p4):
    """Intersection of infinite lines through (p1,p2) and (p3,p4). Returns None if parallel."""
    x1, y1 = p1;  x2, y2 = p2
    x3, y3 = p3;  x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-10:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def inset_polygon_2d(pts, distance):
    """
    Shrink a polygon inward by `distance` feet.

    pts      – open list of (x, y) in Revit feet  (do NOT repeat first point)
    distance – positive offset distance in feet

    Returns a new open list of (x, y) with the same vertex count.
    Raises ValueError if the polygon would collapse (setback too large).
    """
    n = len(pts)
    if n < 3:
        raise ValueError("Need at least 3 vertices for polygon inset")

    # Normalise winding to CCW so that left-of-edge = interior
    if _polygon_signed_area_2d(pts) < 0:
        pts = list(reversed(pts))

    # Build one offset edge per polygon edge
    offset_edges = []
    for i in range(n):
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.sqrt(dx * dx + dy * dy)
        if length < 1e-10:
            continue
        # Inward (left) unit normal for CCW polygon: (-dy/L, +dx/L)
        nx, ny = -dy / length, dx / length
        offset_edges.append(
            ((p1[0] + nx * distance, p1[1] + ny * distance),
             (p2[0] + nx * distance, p2[1] + ny * distance))
        )

    if len(offset_edges) < 3:
        raise ValueError("Too few valid edges for polygon inset")

    # New vertices = intersection of consecutive offset edges
    m = len(offset_edges)
    result = []
    for i in range(m):
        e1 = offset_edges[i]
        e2 = offset_edges[(i + 1) % m]
        pt = _line_intersect_2d(e1[0], e1[1], e2[0], e2[1])
        result.append(pt if pt is not None else e1[1])

    # Sanity-check: inset area must be at least 1 % of original
    orig_area  = abs(_polygon_signed_area_2d(pts))
    inset_area = abs(_polygon_signed_area_2d(result))
    if orig_area > 0 and inset_area < orig_area * 0.01:
        raise ValueError(
            "Setback distance ({:.1f} ft) is too large: polygon collapsed".format(distance)
        )

    return result


def create_setback_lines_in_revit(doc, coordinates, setback_ft,
                                   elevation_ft=0.0,
                                   origin_mode="Project Base Point"):
    """
    Draw a setback envelope as Model Lines by insetting the parcel boundary.

    Parameters:
        doc          - Revit Document
        coordinates  - list of [lon, lat] from GeoJSON outer ring
        setback_ft   - inset distance in feet (must be > 0)
        elevation_ft - Z elevation in feet
        origin_mode  - "Project Base Point" | "Survey Point" | "World Origin (0,0,0)"

    Returns the number of line segments created.
    """
    if setback_ft <= 0:
        raise ValueError("Setback distance must be greater than zero")

    # Convert GeoJSON → feet, relative to polygon centroid
    centroid_lat, centroid_lon = compute_centroid(coordinates)
    pts_2d = []
    for c in coordinates:
        lon, lat = c[0], c[1]
        x_ft, y_ft = latlon_to_feet(lat, lon, centroid_lat, centroid_lon)
        pts_2d.append((x_ft, y_ft))

    # Drop closing duplicate if present
    if (len(pts_2d) > 1 and
            abs(pts_2d[0][0] - pts_2d[-1][0]) < 0.001 and
            abs(pts_2d[0][1] - pts_2d[-1][1]) < 0.001):
        pts_2d = pts_2d[:-1]

    # Inset the polygon
    inset_2d = inset_polygon_2d(pts_2d, setback_ft)

    # Determine insertion origin (same logic as property lines)
    if origin_mode == "Survey Point":
        offset = get_survey_point(doc)
    elif origin_mode == "Project Base Point":
        offset = get_project_base_point(doc)
    else:
        offset = DB.XYZ(0, 0, 0)

    # Convert to Revit XYZ and close the loop
    revit_pts = [
        DB.XYZ(p[0] + offset.X, p[1] + offset.Y, elevation_ft + offset.Z)
        for p in inset_2d
    ]
    revit_pts.append(revit_pts[0])

    count = 0
    with DB.Transaction(doc, "Create Setback Envelope") as t:
        t.Start()
        count = _create_model_lines_from_pts(doc, revit_pts, elevation_ft + offset.Z)
        t.Commit()

    return count


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
                                   line_category="Property Lines",
                                   origin_mode="Project Base Point"):
    """
    Create property boundary lines in the Revit document.

    Parameters:
        doc           - Revit Document
        coordinates   - list of [lon, lat] from GeoJSON outer ring
        elevation_ft  - Z elevation in feet
        line_category - "Property Lines" | "Model Lines" | "Detail Lines"
        origin_mode   - where to place the centroid

    Returns number of lines created.
    """
    if len(coordinates) < 2:
        raise ValueError("Need at least 2 coordinates to create lines")

    # Compute centroid for coordinate origin
    centroid_lat, centroid_lon = compute_centroid(coordinates)

    # Convert all coords to Revit XYZ (feet)
    revit_pts = []
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

    with DB.Transaction(doc, "Create Property Lines") as t:
        t.Start()

        if line_category == "Property Lines":
            # Use Revit's native PropertyLine element
            lines_created = _create_native_property_lines(doc, revit_pts)
        elif line_category == "Detail Lines":
            lines_created = _create_detail_lines(doc, revit_pts)
        else:
            # Default: Model Lines
            lines_created = _create_model_lines(doc, revit_pts, elevation_ft)

        t.Commit()

    return lines_created


def _create_native_property_lines(doc, pts):
    """Draw property boundary as Model Lines.

    Note: Autodesk.Revit.DB.PropertyLine has no public Create() factory method
    in any Revit API version (confirmed through 2026, tracked as CF-1612).
    The only supported workflow is to create Model Lines and then use Revit's
    'Pick Lines' tool to convert them to PropertyLine elements interactively.
    """
    return _create_model_lines_from_pts(doc, pts, pts[0].Z)


def _create_model_lines(doc, pts, elevation_ft):
    """Create ModelLine elements on a horizontal sketch plane."""
    return _create_model_lines_from_pts(doc, pts, elevation_ft)


def _create_model_lines_from_pts(doc, pts, elevation_ft):
    """Internal: create model lines from a list of XYZ points."""
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
            doc.Create.NewModelCurve(line, sketch_plane)
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


class PropertyLineDialog(forms.WPFWindow):
    """Main WPF dialog for Property Line Tool."""

    def __init__(self):
        # Build absolute path so forms.WPFWindow finds the XAML regardless of
        # which script calls this class (avoids the IronPython absolute-URI bug
        # that occurs with Application.LoadComponent + file:// URIs)
        xaml_path = os.path.join(os.path.dirname(__file__), "Tools", "PropertyLine.xaml")
        forms.WPFWindow.__init__(self, xaml_path)

        self._selected_parcel = None
        self._parcels = []
        self._zoning_data = None



        # Load saved API key
        config = load_config()
        saved_key = config.get("lightbox_api_key", "")
        if saved_key:
            self.txt_api_key.Text = saved_key
            self._update_api_status(True, "API key loaded from config")

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

    def btn_save_key_Click(self, sender, e):
        api_key = self.txt_api_key.Text.strip()
        if not api_key:
            self._update_api_status(False, "API key cannot be empty")
            return
        if save_config({"lightbox_api_key": api_key}):
            self._update_api_status(True, "API key saved successfully")
        else:
            self._update_api_status(False, "Failed to save API key")

    def txt_address_KeyDown(self, sender, e):
        if e.Key == Key.Return:
            self.btn_search_Click(sender, e)

    def btn_search_Click(self, sender, e):
        address = self.txt_address.Text.strip()
        if not address:
            self._show_address_warning("Please enter a US property address.")
            return
        if len(address) < 8 or not any(ch.isdigit() for ch in address):
            self._show_address_warning(
                u"Address looks incomplete — include street number, city and state. "
                u"Example: 123 Main St, Los Angeles CA 90001")
            return
        self._hide_address_warning()
        try:
            self.img_map_preview.Source = None
        except Exception:
            pass

        api_key = self.txt_api_key.Text.strip()
        if not api_key:
            self._set_status("Please enter your Lightbox API key first.", error=True)
            return

        self._set_status("Searching for parcels...", busy=True)
        self.btn_search.IsEnabled = False

        # Run in background thread to avoid blocking UI
        def search_thread():
            try:
                parcels = search_parcels(api_key, address)
                self.Dispatcher.Invoke(
                    DispatcherPriority.Normal,
                    Action(lambda: self._on_search_complete(parcels))
                )
            except Exception as ex:
                error_msg = str(ex)
                self.Dispatcher.Invoke(
                    DispatcherPriority.Normal,
                    Action(lambda: self._on_search_error(error_msg))
                )

        t = threading.Thread(target=search_thread)
        t.daemon = True
        t.start()

    def _on_search_complete(self, parcels):
        self.btn_search.IsEnabled = True
        self._hide_address_warning()
        self._parcels = parcels

        if not parcels:
            self._set_status("No parcels found. Try a more specific address (include state + ZIP).")
            self.lv_parcels.Visibility = Visibility.Collapsed
            self.border_no_results.Visibility = Visibility.Visible
            return

        # Populate ListView
        self.lv_parcels.Items.Clear()
        for p in parcels:
            self.lv_parcels.Items.Add(ParcelItem(p))

        self.lv_parcels.Visibility = Visibility.Visible
        self.border_no_results.Visibility = Visibility.Collapsed

        # Surface auto-correction hint if address was normalised
        corrected_from = parcels[0].get("_corrected_from") if parcels else None
        if corrected_from:
            msg = (u"Found {} parcel(s). \u2728 Address auto-corrected: '{}' \u2192 '{}'".format(
                len(parcels), corrected_from, self.txt_address.Text))
        else:
            msg = "Found {} parcel(s). Select one to continue.".format(len(parcels))
        self._set_status(msg)

    def _show_address_warning(self, msg):
        self.txt_address_warning.Text = u"⚠  " + msg
        self.txt_address_warning.Visibility = Visibility.Visible

    def _hide_address_warning(self):
        self.txt_address_warning.Visibility = Visibility.Collapsed

    def _on_search_error(self, error_msg):
        self.btn_search.IsEnabled = True
        # Suppress raw network / connection errors from the status bar;
        # log them and show a friendly neutral message instead.
        err_lower = error_msg.lower()
        is_network = any(k in err_lower for k in (
            "connection", "connect", "timeout", "network", "socket",
            "ssl", "certificate", "unreachable", "refused", "reset",
            "httperror", "urlerror", "ioerror", "errno"))
        if is_network:
            logger.warning("Lightbox API network error: {}".format(error_msg))
            self._set_status(
                u"Could not reach the Lightbox API — check your internet connection and try again.")
        else:
            self._set_status("Search error: {}".format(error_msg), error=True)
            logger.error("Lightbox API search error: {}".format(error_msg))

    def lv_parcels_SelectionChanged(self, sender, e):
        item = self.lv_parcels.SelectedItem
        if not item:
            self._selected_parcel = None
            self.btn_create.IsEnabled = False
            self.grp_parcel_details.Visibility = Visibility.Collapsed
            self.grp_setback.Visibility = Visibility.Collapsed
            try:
                self.img_map_preview.Source = None
            except Exception:
                pass
            return

        self._selected_parcel = item
        self._zoning_data = None
        self._show_parcel_details(item)
        self.btn_create.IsEnabled = True

        # Show setback group only when API data is present
        if item.setbacks:
            self._populate_setback_display(item.setbacks)
            self.grp_setback.Visibility = Visibility.Visible
        else:
            self.grp_setback.Visibility = Visibility.Collapsed

        # Async load map preview in search results card
        self._load_map_preview(item)

    def _load_map_preview(self, item):
        """Async load map preview tiles and overlay boundary, then display in dialog."""
        try:
            self.img_map_preview.Source = None
        except Exception:
            pass

        coords = get_polygon_coords(item.geometry)
        if not coords:
            return

        # Prepare a temp path for preview PNG
        import tempfile
        temp_dir = tempfile.gettempdir()
        safe_apn = item.parcel_id.replace("/", "_").replace("\\", "_")
        temp_path = os.path.join(temp_dir, "t3lab_map_preview_{}.png".format(safe_apn))
        area = item.area_sqft_raw or 0

        def bg_map_preview():
            try:
                # Generate map PNG (OSM tiles + boundary)
                generate_parcel_map(coords, temp_path, area_sqft=area)

                def update_ui():
                    try:
                        if os.path.exists(temp_path):
                            from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
                            from System import Uri
                            bi = BitmapImage()
                            bi.BeginInit()
                            bi.UriSource = Uri(temp_path)
                            bi.CacheOption = BitmapCacheOption.OnLoad
                            bi.EndInit()
                            self.img_map_preview.Source = bi
                    except Exception as ui_ex:
                        logger.warning("Failed to display map preview: {}".format(ui_ex))

                self.Dispatcher.Invoke(
                    DispatcherPriority.Normal,
                    Action(update_ui)
                )
            except Exception as ex:
                logger.warning("Background map preview failed: {}".format(ex))

        t = threading.Thread(target=bg_map_preview)
        t.daemon = True
        t.start()

    def _show_parcel_details(self, item):
        self.grp_parcel_details.Visibility = Visibility.Visible

        self.txt_detail_id.Text      = item.parcel_id or "N/A"
        self.txt_detail_address.Text = item.display_address or "N/A"
        self.txt_detail_county.Text  = item.county or "N/A"
        self.txt_detail_state.Text   = item.state or "N/A"

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

    def _refresh_project_data(self, item):
        """Rebuild the formatted Project Data text block."""
        loc_parts = [p for p in [item.display_address.split(",")[1].strip()
                                  if "," in item.display_address else "",
                                  item.state] if p]
        jurisdiction = item.display_address  # full address as jurisdiction

        lines = [
            ("JURISDICTION HAVING AUTHORITY", jurisdiction),
            ("LEGAL DESCRIPTION",             item.legal_description or "—"),
            ("ASSESSORS PARCEL NO. (APN)",    item.parcel_id or "—"),
            ("IN FLOOD ZONE (FEMA)",          ("Zone " + item.flood_zone)
                                              if item.flood_zone else "—"),
            ("ZONING",                        item.zoning_code or "—"),
            ("LOT AREA",                      format_area(item.area_sqft_raw)
                                              if item.area_sqft_raw else "—"),
            ("LAND USE",                      item.land_use or "—"),
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

    def btn_fetch_zoning_Click(self, sender, e):
        """Kept for XAML compatibility; the button is no longer shown."""
        pass

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
        if not self._selected_parcel:
            return

        coords = get_polygon_coords(self._selected_parcel.geometry)
        if not coords:
            self._set_status("No geometry available for this parcel.", error=True)
            return

        # ── Ask the user where to save ────────────────────────────────────────
        safe_apn = self._selected_parcel.parcel_id.replace("/", "_").replace("\\", "_")
        default_name = "parcel_map_{}.png".format(safe_apn)

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

        self._set_status("Downloading parcel map tiles...", busy=True)
        self.btn_download_map.IsEnabled = False

        area = self._selected_parcel.area_sqft_raw or 0

        def map_thread():
            try:
                generate_parcel_map(coords, out, area_sqft=area)
                self.Dispatcher.Invoke(
                    DispatcherPriority.Normal,
                    Action(lambda: self._on_map_complete(out))
                )
            except Exception as ex:
                err = str(ex)
                self.Dispatcher.Invoke(
                    DispatcherPriority.Normal,
                    Action(lambda: self._on_map_error(err))
                )

        t = threading.Thread(target=map_thread)
        t.daemon = True
        t.start()

    def _on_map_complete(self, path):
        self.btn_download_map.IsEnabled = True
        self._set_status(u"Parcel map saved: {}".format(path), success=True)
        try:
            Diagnostics.Process.Start(path)   # open in default image viewer
        except Exception:
            pass

    def _on_map_error(self, error_msg):
        self.btn_download_map.IsEnabled = True
        self._set_status("Map generation failed: {}".format(error_msg), error=True)
        logger.error("Parcel map error: {}".format(error_msg))

    def _get_min_setback(self):
        """Return the smallest positive value among the three setback fields, or None."""
        values = []
        for txt in (self.txt_setback_front, self.txt_setback_rear, self.txt_setback_side):
            raw = txt.Text.strip()
            if not raw:
                continue
            try:
                v = float(raw)
                if v > 0:
                    values.append(v)
            except ValueError:
                pass
        return min(values) if values else None

    def btn_create_Click(self, sender, e):
        if not self._selected_parcel:
            self._set_status("No parcel selected.", error=True)
            return

        doc = revit.doc
        if not doc:
            self._set_status("No active Revit document.", error=True)
            return

        # Get options
        try:
            elevation_ft = float(self.txt_elevation.Text.strip() or "0")
        except ValueError:
            elevation_ft = 0.0

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

        try:
            count = create_property_lines_in_revit(
                doc, coords, elevation_ft, line_cat, origin_mode
            )
            logger.info("Property lines created: {} segments".format(count))

            msg = "Done! Created {} property line segment(s) for: {}".format(
                count, self._selected_parcel.display_address)
            self._set_status(msg, success=True)

        except Exception as ex:
            self._set_status("Error creating lines: {}".format(ex), error=True)
            logger.error("Property line creation failed: {}".format(traceback.format_exc()))
        finally:
            self.btn_create.IsEnabled = True

    # ───────────────────────────────────── HELPERS

    def _update_api_status(self, ok, msg):
        self.txt_api_status.Text = msg
        if ok:
            self.txt_api_status.Foreground = SolidColorBrush(Color.FromRgb(78, 201, 176))  # teal
        else:
            self.txt_api_status.Foreground = SolidColorBrush(Color.FromRgb(255, 107, 107))  # red

    def _set_status(self, msg, error=False, success=False, busy=False):
        self.txt_status.Text = msg
        if error:
            color = Color.FromRgb(255, 107, 107)
            dot_color = Color.FromRgb(255, 107, 107)
            label = "Error"
        elif success:
            color = Color.FromRgb(78, 201, 176)
            dot_color = Color.FromRgb(78, 201, 176)
            label = "Done"
        elif busy:
            color = Color.FromRgb(255, 197, 61)
            dot_color = Color.FromRgb(255, 197, 61)
            label = "Working..."
        else:
            color = Color.FromRgb(136, 136, 136)
            dot_color = Color.FromRgb(136, 136, 136)
            label = "Idle"

        self.txt_status.Foreground = SolidColorBrush(color)
        self.dot_status.Fill = SolidColorBrush(dot_color)
        self.txt_status_label.Text = label


# ╔═╗╦ ╦╔═╗╦  ╦╔═╗
# ╚═╗╠═╣║ ║║  ║╚═╗
# ╚═╝╩ ╩╚═╝╩═╝╩╚═╝ PUBLIC ENTRY POINT
# ==================================================

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
