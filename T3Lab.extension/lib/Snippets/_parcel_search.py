# -*- coding: utf-8 -*-
"""
Property Line search pipeline - one automatic search that works anywhere.

Pure Python, no Revit / WPF imports, so every decision here is unit-tested
offline (dev/test_property_line_search.py).  The dialog only hands in the
query, the optional LightBox key and a LightBox search callable.

    query --> VN-2000 coordinate table?  --> VN-2000 parcel        (done)
          --> "lat, lon" coordinates?    --> reverse geocode (OSM)
          --> anything else              --> geocode worldwide (Nominatim, Photon)
          --> country of the hit         --> plan_sources()
                US + LightBox key        --> LightBox cadastral parcels
                (nothing / failure)      --> OpenStreetMap, silently
          --> OpenStreetMap polygons now, Overpass enclosures in search_more()

Every parcel comes back tagged with ``source_label`` (the short name shown
on the result pill) so the user always knows where a boundary came from.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

import os
import re

from Snippets import _geoparcel as geoparcel

try:
    from Snippets import _vn2000 as vn2000
except ImportError:          # pragma: no cover - shipped together
    vn2000 = None

__all__ = [
    "SearchError", "SOURCE_OSM", "SOURCE_LIGHTBOX", "SOURCE_VN2000",
    "parse_coordinates", "looks_like_us_address", "country_code_of",
    "country_from_point", "plan_sources", "source_label", "describe_sources",
    "search_primary", "search_more", "STATE_MAP", "STATE_CODES",
]

SOURCE_OSM      = u"OpenStreetMap"
SOURCE_LIGHTBOX = u"LightBox"
SOURCE_VN2000   = u"VN-2000"

DEFAULT_LIMIT = 8

_logger = None


def set_logger(logger):
    global _logger
    _logger = logger


def _log(msg, level="info"):
    if _logger is None:
        return
    try:
        getattr(_logger, level)(msg)
    except Exception:
        pass


class SearchError(Exception):
    """A search that cannot go on.  The message is shown to the user as-is."""


# ── US address hints (also used by the LightBox address normaliser) ─────────

STATE_MAP = {
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
STATE_CODES = set(STATE_MAP.values())

_US_MARKERS = ("usa", "u.s.a", "u.s.a.", "united states",
               "united states of america", "us")


def looks_like_us_address(address):
    """
    True when the text plausibly names a US address: it ends in the country
    or carries a state code + ZIP / a full state name.  Only used when no
    geocoder can be reached, to decide whether LightBox is still worth a try.
    """
    text = (address or u"").strip().lower()
    if not text:
        return False
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if parts and parts[-1] in _US_MARKERS:
        return True
    # "CA 92646" - a state code followed by a 5-digit ZIP
    if re.search(r"\b([a-z]{2})\s+\d{5}(-\d{4})?\b", text):
        code = re.search(r"\b([a-z]{2})\s+\d{5}", text).group(1).upper()
        if code in STATE_CODES:
            return True
    for name in STATE_MAP:
        if re.search(r"\b{}\b".format(re.escape(name)), text):
            return True
    return False


# ── coordinates typed as the query ───────────────────────────────────────────

_DEC = r"[-+]?\d{1,3}(?:\.\d+)?"
_DECIMAL_RE = re.compile(
    r"^\s*(?P<h1>[NSEW])?\s*(?P<a>" + _DEC + r")\s*°?\s*(?P<h2>[NSEW])?"
    r"\s*(?:[,;/]\s*|\s+)"
    r"(?P<h3>[NSEW])?\s*(?P<b>" + _DEC + r")\s*°?\s*(?P<h4>[NSEW])?\s*$",
    re.IGNORECASE)
_DMS_RE = re.compile(
    r"(\d{1,3})\s*[°º]\s*(?:(\d{1,2}(?:\.\d+)?)\s*['′’]\s*)?"
    r"(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|″|''|”)\s*)?([NSEW])",
    re.IGNORECASE)
_URL_RE = re.compile(
    r"(?:@|[?&](?:q|query|ll|center|destination)=)"
    r"(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)")


def _valid(lat, lon):
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _apply_hemisphere(value, letter):
    if letter and letter.upper() in ("S", "W"):
        return -abs(value)
    return value


def parse_coordinates(text):
    """
    A query typed as coordinates -> (lat, lon), else None.

    Accepts "1.2903, 103.8519", "1.2903 103.8519", "-33.8568;151.2153",
    "N 1.2903 E 103.8519", "1.2903N 103.8519E", "103.8519E, 1.2903N",
    DMS such as 48°51'29.6"N 2°17'40.2"E, and Google Maps / OSM links
    (".../@1.2903,103.8519,17z").

    Plain decimal pairs need at least one decimal point, so a street number
    such as "12 34" is never mistaken for a position.
    """
    if not text:
        return None
    raw = u"{}".format(text).strip()

    match = _URL_RE.search(raw)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        return (lat, lon) if _valid(lat, lon) else None

    dms = _DMS_RE.findall(raw)
    if len(dms) == 2:
        values = {}
        for deg, minutes, seconds, letter in dms:
            val = float(deg) + float(minutes or 0) / 60.0 + float(seconds or 0) / 3600.0
            axis = "lat" if letter.upper() in ("N", "S") else "lon"
            values[axis] = _apply_hemisphere(val, letter)
        if "lat" in values and "lon" in values and _valid(values["lat"], values["lon"]):
            return values["lat"], values["lon"]
        return None

    match = _DECIMAL_RE.match(raw)
    if not match:
        return None
    a, b = match.group("a"), match.group("b")
    first = (match.group("h1") or match.group("h2") or u"").upper()
    second = (match.group("h3") or match.group("h4") or u"").upper()
    has_letters = bool(first or second)
    if not has_letters and "." not in a and "." not in b:
        return None
    va = _apply_hemisphere(float(a), first)
    vb = _apply_hemisphere(float(b), second)

    if first in ("E", "W") or second in ("N", "S"):
        lat, lon = vb, va                           # written lon-first
    elif not has_letters and abs(va) > 90.0 >= abs(vb):
        lat, lon = vb, va                           # obviously lon, lat
    else:
        lat, lon = va, vb
    return (lat, lon) if _valid(lat, lon) else None


# ── where on earth is it ─────────────────────────────────────────────────────

# Coarse boxes, only used when the geocoder gave no country code (e.g. typed
# coordinates while Nominatim is unreachable).  They over-reach at borders
# by design - a wrong guess only costs one LightBox call before OSM.
_COUNTRY_BOXES = (
    ("us", 24.3, 49.5, -125.0, -66.9),     # contiguous states
    ("us", 51.0, 71.5, -170.0, -129.9),    # Alaska
    ("us", 18.8, 22.4, -160.4, -154.7),    # Hawaii
    ("us", 17.8, 18.6, -67.4, -65.2),      # Puerto Rico
    ("vn", 8.3, 23.5, 102.1, 109.6),       # Vietnam
)


def country_from_point(lat, lon):
    """ISO alpha-2 (lower case) from a coarse bounding box, or u''."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return u""
    for code, south, north, west, east in _COUNTRY_BOXES:
        if south <= lat <= north and west <= lon <= east:
            return code
    return u""


def country_code_of(place):
    """Country of a geocoded place: geocoder country code first, bbox second."""
    if not place:
        return u""
    code = place.get("country_code") or u""
    if not code:
        addr = place.get("address") or {}
        code = addr.get("country_code") or addr.get("countrycode") or u""
    code = u"{}".format(code).strip().lower()
    if code:
        return code
    return country_from_point(place.get("lat"), place.get("lon"))


def plan_sources(country_code, has_lightbox):
    """
    Parcel sources to try, best first.  OpenStreetMap always closes the list,
    so every location on earth has an answer.
    """
    sources = []
    if (country_code or u"").lower() == "us" and has_lightbox:
        sources.append(SOURCE_LIGHTBOX)
    sources.append(SOURCE_OSM)
    return sources


def source_label(parcel):
    """Short provider name for the result pill."""
    raw = u"{}".format((parcel or {}).get("source") or u"")
    low = raw.lower()
    if "lightbox" in low:
        return SOURCE_LIGHTBOX
    if "vn-2000" in low or "vn2000" in low:
        return SOURCE_VN2000
    if not raw or "openstreetmap" in low or "osm" in low or "nominatim" in low:
        return SOURCE_OSM
    return raw


def describe_sources(parcels):
    """'Boundary from OpenStreetMap' / 'Boundaries from LightBox and OpenStreetMap'."""
    labels = []
    for parcel in parcels or []:
        try:
            label = parcel.get("source_label") or source_label(parcel)
        except AttributeError:
            label = getattr(parcel, "source_label", u"") or u""
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return u""
    noun = u"Boundary" if len(parcels) == 1 else u"Boundaries"
    if len(labels) == 1:
        return u"{} from {}".format(noun, labels[0])
    return u"{} from {} and {}".format(noun, u", ".join(labels[:-1]), labels[-1])


def _tag(parcels):
    for parcel in parcels:
        parcel["source_label"] = source_label(parcel)
    return parcels


# ── VN-2000 coordinate tables (pasted text or a file path) ───────────────────

def _vn2000_parcel(query):
    if vn2000 is None:
        return None
    points = []
    path = query.strip("\"'")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                points = vn2000.parse_coordinate_table(handle.read())
        except (IOError, OSError, UnicodeError) as ex:
            _log("Could not read {} as a VN-2000 table: {}".format(path, ex), "warning")
    if not points:
        points = vn2000.parse_coordinate_table(query)
    if len(points) < 3:
        return None
    province = u"Hà Nội"
    for name in vn2000.PROVINCE_MERIDIANS:
        if name.lower() in query.lower():
            province = name
            break
    name = os.path.basename(path) if os.path.isfile(path) else u"VN-2000 parcel"
    return vn2000.create_vn2000_parcel(points, name=name, province=province)


# ── the pipeline ─────────────────────────────────────────────────────────────

def _result(parcels, source, context=None, place=None, kind="address",
            fallback_from=None):
    return {
        "parcels":       _tag(list(parcels or [])),
        "context":       context,
        "source":        source,
        "place":         place,
        "country_code":  country_code_of(place) if place else u"",
        "query_kind":    kind,
        "fallback_from": fallback_from,
    }


def _try_lightbox(lightbox_search, api_key, query):
    """LightBox parcels, or [] - a LightBox failure must never end the search."""
    if not (lightbox_search and api_key and query):
        return []
    try:
        return list(lightbox_search(api_key, query) or [])
    except Exception as ex:
        _log(u"LightBox failed, falling back to OpenStreetMap: {}".format(ex), "warning")
        return []


def _point_place(lat, lon, found):
    """The place for a typed coordinate: the user's exact point, OSM's address."""
    place = {
        "lat": lat, "lon": lon,
        "display_name": u"{:.6f}, {:.6f}".format(lat, lon),
        "name": u"", "address": {}, "geojson": {}, "boundingbox": [],
        "osm_type": u"", "osm_id": u"", "category": u"", "type": u"",
        "country_code": u"", "source": u"Coordinates",
    }
    if found:
        for key in ("display_name", "name", "address", "geojson", "osm_type",
                    "osm_id", "category", "type", "country_code"):
            if found.get(key):
                place[key] = found[key]
    if not place["country_code"]:
        place["country_code"] = country_from_point(lat, lon)
    return place


def search_primary(query, lightbox_search=None, lightbox_key=None,
                   limit=DEFAULT_LIMIT, language=None):
    """
    Fast first pass of the automatic search.

    *lightbox_search* is ``f(api_key, address) -> [parcel, ...]``; it is only
    called for a US location when *lightbox_key* is set.

    Returns {parcels, context, source, place, country_code, query_kind,
    fallback_from}.  *context* is handed to search_more() for the slow
    OpenStreetMap pass (None when the result is final).

    Raises SearchError with a user-facing message.
    """
    query = u"{}".format(query or u"").strip()
    if not query:
        raise SearchError(u"Enter an address, place name or coordinates "
                          u"(e.g. 1.2903, 103.8519) and press Search.")

    parcel = _vn2000_parcel(query)
    if parcel:
        return _result([parcel], SOURCE_VN2000, kind="vn2000")

    has_lightbox = bool(lightbox_search and lightbox_key)
    coords = parse_coordinates(query)
    must_contain = None
    if coords:
        lat, lon = coords
        found = geoparcel.reverse_geocode(lat, lon, language=language)
        places = [_point_place(lat, lon, found)]
        lightbox_query = found.get("display_name") if found else None
        must_contain = coords
        kind = "coordinates"
    else:
        kind = "address"
        try:
            places = geoparcel.geocode(query, limit=max(3, int(limit)),
                                       language=language)
        except geoparcel.ServiceUnavailable as ex:
            # No geocoder reachable.  A US address with a LightBox key can
            # still be answered straight from LightBox.
            if has_lightbox and looks_like_us_address(query):
                parcels = _try_lightbox(lightbox_search, lightbox_key, query)
                if parcels:
                    return _result(parcels, SOURCE_LIGHTBOX, kind=kind)
            raise SearchError(u"{}".format(ex))
        if not places:
            raise SearchError(
                u"No location found for '{}'. Add the city and country "
                u"(e.g. '25 Le Duan, District 1, Ho Chi Minh City, Vietnam') "
                u"or type coordinates such as 10.7769, 106.7009."
                .format(query))
        lightbox_query = query

    place = places[0]
    fallback_from = None
    for source in plan_sources(country_code_of(place), has_lightbox):
        if source == SOURCE_LIGHTBOX:
            parcels = _try_lightbox(lightbox_search, lightbox_key, lightbox_query)
            if parcels:
                return _result(parcels, SOURCE_LIGHTBOX, place=place, kind=kind)
            _log(u"LightBox had no parcel for '{}'; using OpenStreetMap."
                 .format(lightbox_query))
            fallback_from = SOURCE_LIGHTBOX
            continue
        parcels = geoparcel.boundaries_from_places(
            places, limit=limit, must_contain=must_contain)
        seen = set(geoparcel.ring_key(p["geometry"]["coordinates"][0])
                   for p in parcels)
        return _result(parcels, SOURCE_OSM, place=place, kind=kind,
                       fallback_from=fallback_from,
                       context={"place": place, "seen": seen, "limit": limit})
    raise SearchError(u"No data source is available.")   # unreachable


def search_more(context):
    """
    Slow second pass: everything OpenStreetMap has mapped around the point,
    plus an approximate plot when nothing is.  Never raises.
    """
    if not context:
        return []
    seen = context.get("seen") or set()
    limit = context.get("limit") or DEFAULT_LIMIT
    try:
        return _tag(geoparcel.nearby_boundaries(
            context["place"], limit=max(0, limit - len(seen)),
            exclude=seen, include_fallback=True))
    except Exception as ex:
        _log(u"Overpass enrichment failed: {}".format(ex), "warning")
        return []
