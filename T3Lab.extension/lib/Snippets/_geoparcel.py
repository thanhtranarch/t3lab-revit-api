# -*- coding: utf-8 -*-
"""
Worldwide address -> property boundary lookup.

Pure python (IronPython 2.7 + CPython 3 compatible) with no Revit imports, so it
can be exercised outside Revit.  Gives the Property Line tool keyless global
coverage on top of OpenStreetMap: Nominatim for geocoding (it already returns
the matched object's polygon) and Overpass for the enclosures around that point.

Public API
----------
search_boundaries(address, ...) -> list of parcel dicts, in the same shape the
                                   PropertyLine dialog already consumes
geocode(address, ...)           -> list of geocoded places
polygon_area_m2(coords)         -> area of a [lon, lat] ring, in m2
format_area_dual(sqft)          -> "1,240 m2 (13,347 sqft)"

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

import json
import math

__all__ = [
    "search_boundaries", "primary_boundaries", "nearby_boundaries",
    "overpass_boundaries", "geocode", "reverse_geocode", "ring_key",
    "http_request", "http_get_bytes",
    "boundaries_from_places", "ServiceUnavailable", "describe_error",
    "describe_http_status", "unavailable_message",
    "polygon_area_m2", "polygon_area_sqft", "polygon_centroid",
    "format_area_dual", "point_in_polygon", "close_ring", "demojibake",
    "metes_and_bounds", "format_metes_and_bounds", "format_bearing",
    "SQM_TO_SQFT", "OSM_SOURCE", "NOMINATIM_URL", "set_logger",
]

# ── HTTP plumbing (IronPython 2.7 / CPython 3) ───────────────────────────────
try:
    import urllib.request as _urq
    import urllib.parse as _upa
except ImportError:
    _urq = None
    _upa = None
try:
    import urllib2 as _u2
except ImportError:
    _u2 = None

# Inside Revit the .NET stack is available and is the only HTTP path with an
# unambiguous text decode - see _http_dotnet below.
try:
    import System                     # noqa: F401 - probe only
    import System.Net                 # noqa: F401 - probe only
    _HAS_DOTNET = True
except ImportError:
    _HAS_DOTNET = False


def _is_dotnet_setup_error(ex):
    """
    True when the .NET request failed for a reason that makes urllib worth a
    try (missing type, bad attribute), rather than a genuine transport error
    that urllib would hit as well.
    """
    return isinstance(ex, (AttributeError, TypeError, NameError, ImportError))


OSM_SOURCE    = "OpenStreetMap"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
PHOTON_URL    = "https://photon.komoot.io/api"

# The main overpass-api.de instance answers HTTP 406 to any User-Agent it does
# not recognise, so the mirrors that do accept ours are tried first and it acts
# as a last-resort failover (it may still answer from other networks).
OVERPASS_ENDPOINTS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)

USER_AGENT = ("T3Lab-PropertyLine/2.2 "
              "(pyRevit add-in; https://github.com/thanhtranarch/t3lab-revit-api)")

SQM_TO_SQFT    = 10.763910416709722
EARTH_RADIUS_M = 6378137.0

# Overpass search radius (metres) around the geocoded point
DEFAULT_RADIUS_M = 80
# Hard ceiling - anything bigger than this is a district, never a plot
MAX_PLOT_AREA_M2 = 400000.0
# Above this, a polygon is still offered but ranked below plot-sized ones and
# labelled district-scale, so a whole residential zone cannot outrank the lot
PLOT_SCALE_AREA_M2 = 50000.0
# Side of the synthetic square drawn when no boundary is mapped at all
FALLBACK_PLOT_M = 30.0
# Neighbouring enclosures offered when the geocoder lands slightly off the plot
MAX_NEIGHBOURS = 3
NEIGHBOUR_DISTANCE_M = 45.0
# Per-endpoint HTTP timeout for the (slow) Overpass query, and how many
# mirrors to try before giving up - the geocoder result already stands on its
# own, so a loaded Overpass must never hold the caller for minutes.
OVERPASS_TIMEOUT_S = 20
OVERPASS_MAX_TRIES = 3

_logger = None


def set_logger(logger):
    """Optional: hand in the pyRevit logger so failures stay traceable."""
    global _logger
    _logger = logger


def _log(msg):
    if _logger is not None:
        try:
            _logger.debug(msg)
        except Exception:
            pass


_TLS_DONE = [False]


def _ensure_tls():
    """
    Make sure System.Net can speak TLS 1.2, which every OSM endpoint requires.

    SystemDefault (0, the default on .NET 8 and on .NET Framework 4.7+ hosts)
    already lets the OS negotiate TLS 1.2/1.3, so it is left alone - forcing
    Tls13 on a Windows build that cannot do it breaks every handshake.  Only
    an explicit legacy value (Ssl3/Tls/Tls11) gets Tls12 OR-ed in.  Runs once;
    no-op off .NET.
    """
    if _TLS_DONE[0]:
        return
    _TLS_DONE[0] = True
    try:
        import System
        import System.Net as _net
        spm = _net.ServicePointManager
        current = spm.SecurityProtocol
        if int(System.Convert.ToInt32(current)) == 0:
            return                              # SystemDefault: OS decides
        tls12 = _net.SecurityProtocolType.Tls12
        try:
            spm.SecurityProtocol = current | tls12
        except Exception:
            spm.SecurityProtocol = tls12
    except Exception:
        pass


def _to_bytes(text):
    if isinstance(text, bytes):
        return text
    return text.encode("utf-8")


def _to_text(raw):
    if raw is None:
        return u""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    try:
        if isinstance(raw, unicode):        # noqa: F821 - IronPython 2 only
            return raw
    except NameError:
        pass
    return raw


def demojibake(text):
    """
    Repair text that was UTF-8 but got decoded as Latin-1 somewhere upstream,
    so "Thành phố Hồ Chí Minh" arrives as "ThÃ nh phá»' Há»" ChÃ­ Minh".

    Only rewrites when the whole string fits in Latin-1, actually contains
    high bytes, and then re-decodes cleanly as UTF-8.  Genuine Latin-1 text
    ("Café", "Ångström", "Müller") fails that last test, because a lone
    accented letter is not a valid UTF-8 sequence - so it is left alone.

    Script-agnostic: the same repair recovers Vietnamese, Thai, Arabic,
    Greek, Cyrillic and CJK names, since they all reach us as UTF-8.
    """
    if not text:
        return text
    has_high = False
    for ch in text:
        code = ord(ch)
        if code > 0xFF:
            return text     # already outside Latin-1, so it cannot be mojibake
        if code >= 0x80:
            has_high = True
    if not has_high:
        return text         # pure ASCII, nothing to repair
    try:
        return text.encode("latin-1").decode("utf-8")
    except Exception:
        return text


def _u(val):
    """
    Coerce anything to text without blowing up on IronPython byte strings,
    repairing upstream mis-decoding on the way through.
    """
    if val is None:
        return u""
    text = None
    try:
        if isinstance(val, unicode):        # noqa: F821 - IronPython 2 only
            text = val
    except NameError:
        pass
    if text is None:
        if isinstance(val, bytes):
            text = val.decode("utf-8", "replace")
        else:
            text = u"{}".format(val)
    return demojibake(text)


def url_quote(text, safe=""):
    """Percent-encode text as UTF-8 (critical for Vietnamese addresses)."""
    try:
        if isinstance(text, unicode):       # noqa: F821 - IronPython 2 only
            text = text.encode("utf-8")
    except NameError:
        pass
    if _upa is not None:
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        return _upa.quote(text, safe=safe)
    if _u2 is not None:
        return _u2.quote(text, safe=safe)
    out = []
    safe_set = set(safe)
    for byte in bytearray(_to_bytes(text)):
        ch = chr(byte)
        if ch.isalnum() or ch in "-_.~" or ch in safe_set:
            out.append(ch)
        else:
            out.append("%{0:02X}".format(byte))
    return "".join(out)


def _http_dotnet(url, data=None, headers=None, timeout=25):
    """
    HTTP through System.Net, decoding the body with an explicit UTF-8 reader.

    This exists because IronPython's urllib2 hands back a string whose
    encoding is ambiguous: the bytes of a UTF-8 body end up widened one byte
    per character, which is how "Thành phố" turns into "ThÃ nh phá»'" on the
    way to WPF.  Reading the response stream through Encoding.UTF8 removes the
    guesswork for every script, not just Vietnamese.

    Returns (status_code, text).  Raises if System.Net is unavailable (plain
    CPython) or the request cannot be made at all, so callers fall back to
    urllib.
    """
    import System
    import System.Net as Net
    import System.IO as IO
    import System.Text as Text

    req = Net.WebRequest.Create(url)
    req.Timeout = int(timeout * 1000)

    # User-Agent / Accept / Content-Type are restricted headers in .NET and
    # must go through their own properties, not the Headers collection.
    rest = dict(headers or {})
    user_agent = rest.pop("User-Agent", None)
    accept = rest.pop("Accept", None)
    content_type = rest.pop("Content-Type", None)
    if user_agent:
        req.UserAgent = user_agent
    if accept:
        req.Accept = accept
    for key, val in rest.items():
        req.Headers.Add(key, val)

    if data is None:
        req.Method = "GET"
    else:
        req.Method = "POST"
        req.ContentType = content_type or "application/x-www-form-urlencoded"
        payload = Text.Encoding.UTF8.GetBytes(data)
        req.ContentLength = payload.Length
        stream = req.GetRequestStream()
        try:
            stream.Write(payload, 0, payload.Length)
        finally:
            stream.Close()

    try:
        resp = req.GetResponse()
    except Net.WebException as wex:
        resp = wex.Response
        if resp is None:
            raise           # DNS failure, timeout, TLS refusal - let it bubble

    try:
        try:
            status = int(System.Convert.ToInt32(resp.StatusCode))
        except Exception:
            status = 200
        reader = IO.StreamReader(resp.GetResponseStream(), Text.Encoding.UTF8)
        try:
            return status, reader.ReadToEnd()
        finally:
            reader.Close()
    finally:
        resp.Close()


def http_request(url, data=None, headers=None, timeout=25):
    """
    GET (data=None) or POST a form body.  Returns (status_code, text).
    Non-2xx responses are returned, never raised.
    """
    _ensure_tls()
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    if headers:
        hdrs.update(headers)
    body = _to_bytes(data) if data is not None else None

    # Preferred path inside Revit: .NET with a known-UTF-8 decode.
    dotnet_error = None
    if _HAS_DOTNET:
        try:
            return _http_dotnet(url, data=data, headers=hdrs, timeout=timeout)
        except ImportError:
            pass
        except Exception as ex:
            if not _is_dotnet_setup_error(ex):
                # A real transport failure.  A timeout would only time out
                # again, but a TLS / proxy refusal from the .NET stack is
                # worth one try through Python's own ssl + cert store.
                if _is_timeout(ex) or _urq is None:
                    raise
                dotnet_error = ex
            _log("System.Net request failed, retrying with urllib: {}".format(ex))

    if _urq is not None:
        try:
            return _urllib_request(url, body, hdrs, timeout)
        except Exception:
            if dotnet_error is not None:
                raise dotnet_error      # the .NET message is the clearer one
            raise

    if _u2 is not None:
        req = _u2.Request(url, body)
        for key, val in hdrs.items():
            req.add_header(key, val)
        try:
            resp = _u2.urlopen(req, timeout=timeout)
            return resp.getcode(), _to_text(resp.read())
        except _u2.HTTPError as err:
            try:
                return err.code, _to_text(err.read())
            except Exception:
                return err.code, u""

    raise RuntimeError("No HTTP library available (urllib.request / urllib2)")


def _http_dotnet_bytes(url, headers=None, timeout=25):
    """
    GET through System.Net returning the body as raw bytes.

    http_request() decodes every body as UTF-8 text, which is right for JSON
    and fatal for a PNG: the very first byte of the signature (0x89) is not
    valid UTF-8, so a map tile comes back as text full of U+FFFD and can never
    be decoded again.  Images go through here instead.
    """
    import base64
    import System
    import System.Net as Net
    import System.IO as IO

    req = Net.WebRequest.Create(url)
    req.Timeout = int(timeout * 1000)
    try:
        req.ReadWriteTimeout = int(timeout * 1000)
    except Exception:
        pass

    rest = dict(headers or {})
    user_agent = rest.pop("User-Agent", None)
    accept = rest.pop("Accept", None)
    rest.pop("Content-Type", None)
    if user_agent:
        req.UserAgent = user_agent
    if accept:
        req.Accept = accept
    for key, val in rest.items():
        req.Headers.Add(key, val)
    req.Method = "GET"

    try:
        resp = req.GetResponse()
    except Net.WebException as wex:
        resp = wex.Response
        if resp is None:
            raise           # DNS failure, timeout, TLS refusal - let it bubble

    try:
        try:
            status = int(System.Convert.ToInt32(resp.StatusCode))
        except Exception:
            status = 200
        source = resp.GetResponseStream()
        buffer = IO.MemoryStream()
        try:
            source.CopyTo(buffer)
        finally:
            source.Close()
        raw = buffer.ToArray()
        if isinstance(raw, (bytes, bytearray)):
            return status, bytes(raw)       # a codec already converted it
        if raw is None or len(raw) == 0:
            return status, b""
        # One managed->Python hop via base64 instead of a per-byte loop over
        # the .NET array (a tile is ~10-40 KB).
        return status, base64.b64decode(System.Convert.ToBase64String(raw))
    finally:
        resp.Close()


def http_get_bytes(url, headers=None, timeout=25):
    """
    GET a binary resource (map tile, image).  Returns (status_code, bytes).
    Non-2xx responses are returned with whatever body came back, never raised;
    transport failures (DNS, timeout, TLS, proxy) raise.

    Same transport policy as http_request(): System.Net first inside Revit,
    one retry through urllib for a non-timeout .NET failure.
    """
    _ensure_tls()
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)

    dotnet_error = None
    if _HAS_DOTNET:
        try:
            return _http_dotnet_bytes(url, headers=hdrs, timeout=timeout)
        except ImportError:
            pass
        except Exception as ex:
            if not _is_dotnet_setup_error(ex):
                if _is_timeout(ex) or _urq is None:
                    raise
                dotnet_error = ex
            _log("System.Net binary request failed, retrying with urllib: "
                 "{}".format(ex))

    if _urq is not None:
        req = _urq.Request(url, headers=hdrs)
        try:
            resp = _urq.urlopen(req, timeout=timeout)
            return resp.getcode(), resp.read()
        except Exception as err:
            if hasattr(err, "code"):
                try:
                    return err.code, err.read()
                except Exception:
                    return err.code, b""
            if dotnet_error is not None:
                raise dotnet_error      # the .NET message is the clearer one
            raise

    raise RuntimeError("No HTTP library available (urllib.request)")


def _is_timeout(ex):
    text = u"{}".format(ex).lower()
    return "timed out" in text or "timeout" in text


def _urllib_request(url, body, hdrs, timeout):
    """CPython urllib path.  Non-2xx responses are returned, never raised."""
    req = _urq.Request(url, data=body, headers=hdrs)
    try:
        resp = _urq.urlopen(req, timeout=timeout)
        return resp.getcode(), _to_text(resp.read())
    except Exception as err:
        if hasattr(err, "code"):
            try:
                return err.code, _to_text(err.read())
            except Exception:
                return err.code, u""
        raise


# ── failure reporting ────────────────────────────────────────────────────────

class ServiceUnavailable(Exception):
    """
    Every geocoder failed at the network / HTTP level - as opposed to
    answering "no such place".  The message is user-facing: what failed,
    where, and what to do next.
    """


def describe_http_status(status):
    """HTTP status -> short plain-English reason."""
    if status in (401, 403):
        return u"access refused (HTTP {}) - the service blocked this request".format(status)
    if status == 407:
        return u"the proxy asks for a login (HTTP 407)"
    if status == 429:
        return u"too many requests (HTTP 429) - wait a minute"
    if status and 500 <= status < 600:
        return u"service is down or overloaded (HTTP {})".format(status)
    return u"unexpected answer (HTTP {})".format(status)


def describe_error(ex):
    """Transport exception (urllib or System.Net) -> short plain-English reason."""
    text = _u(ex).lower()
    if _is_timeout(ex):
        return u"no answer in time (timed out)"
    if any(k in text for k in ("ssl", "tls", "certificate", "trust relationship",
                               "secure channel")):
        return (u"secure connection (HTTPS/TLS) failed - a proxy or firewall "
                u"may be intercepting HTTPS")
    if any(k in text for k in ("getaddrinfo", "name or service", "could not be resolved",
                               "nodename", "no such host", "name resolution")):
        return u"server name could not be resolved - no internet or DNS blocked"
    if "407" in text or "proxy" in text:
        return u"the proxy refused the connection"
    if any(k in text for k in ("refused", "reset", "unreachable", "aborted",
                               "forcibly closed")):
        return u"connection refused or dropped"
    short = _u(ex).strip().splitlines()[0] if _u(ex).strip() else type(ex).__name__
    return short[:160]


def unavailable_message(failures):
    """[(service, reason), ...] -> the user-facing ServiceUnavailable text."""
    detail = u"; ".join(u"{}: {}".format(name, why) for name, why in failures)
    return (u"Could not reach the map data service ({}). Check the internet "
            u"connection or proxy settings, wait a moment, then search "
            u"again.".format(detail))


# Nominatim's usage policy allows one request per second per application.
NOMINATIM_MIN_INTERVAL_S = 1.0
_last_nominatim = [0.0]


def _nominatim_throttle():
    import time
    wait = NOMINATIM_MIN_INTERVAL_S - (time.time() - _last_nominatim[0])
    if wait > 0:
        time.sleep(min(wait, NOMINATIM_MIN_INTERVAL_S))
    _last_nominatim[0] = time.time()


# ── geometry helpers (equirectangular - exact enough at parcel scale) ────────

def polygon_centroid(coords):
    """Average of a [lon, lat] ring -> (lat, lon)."""
    if not coords:
        return 0.0, 0.0
    return (sum(c[1] for c in coords) / float(len(coords)),
            sum(c[0] for c in coords) / float(len(coords)))


def polygon_area_m2(coords):
    """Shoelace area of a [lon, lat] ring, in square metres."""
    if not coords or len(coords) < 3:
        return 0.0
    lat0, lon0 = polygon_centroid(coords)
    cos_lat = math.cos(math.radians(lat0))
    pts = []
    for c in coords:
        x = EARTH_RADIUS_M * math.radians(c[0] - lon0) * cos_lat
        y = EARTH_RADIUS_M * math.radians(c[1] - lat0)
        pts.append((x, y))
    total = 0.0
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        total += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1]
    return abs(total) / 2.0


def polygon_area_sqft(coords):
    return polygon_area_m2(coords) * SQM_TO_SQFT


def point_in_polygon(lon, lat, coords):
    """Ray-casting test.  coords is a [lon, lat] ring."""
    if not coords or len(coords) < 3:
        return False
    inside = False
    n = len(coords)
    j = n - 1
    for i in range(n):
        xi, yi = coords[i][0], coords[i][1]
        xj, yj = coords[j][0], coords[j][1]
        if (yi > lat) != (yj > lat):
            denom = (yj - yi) or 1e-12
            if lon < (xj - xi) * (lat - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def haversine_m(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def close_ring(coords):
    """Drop duplicated closing vertices so callers always get an open ring."""
    ring = [[float(c[0]), float(c[1])] for c in coords if c and len(c) >= 2]
    while (len(ring) > 1 and
           abs(ring[0][0] - ring[-1][0]) < 1e-12 and
           abs(ring[0][1] - ring[-1][1]) < 1e-12):
        ring = ring[:-1]
    return ring


M_TO_FT = 3.280839895013123


def ring_to_local_m(coords):
    """
    Project a [lon, lat] ring to local metres about its own centroid.
    +x = east, +y = north.  Equirectangular, exact enough at parcel scale.
    """
    lat0, lon0 = polygon_centroid(coords)
    cos_lat = math.cos(math.radians(lat0))
    return [(EARTH_RADIUS_M * math.radians(c[0] - lon0) * cos_lat,
             EARTH_RADIUS_M * math.radians(c[1] - lat0)) for c in coords]


def format_bearing(azimuth_deg):
    """
    Azimuth (degrees clockwise from true north) as a surveyor's quadrant
    bearing, e.g. 135.5 -> 'S 44°30'00" E'.
    """
    # The quadrant boundaries are picked so the cardinals stay symmetric:
    # due east reads N 90 E and due west N 90 W, due north N 0 E and due
    # south S 0 W - rather than one cardinal borrowing the other's axis.
    az = azimuth_deg % 360.0
    if az <= 90.0:
        ns, ew, angle = u"N", u"E", az
    elif az < 180.0:
        ns, ew, angle = u"S", u"E", 180.0 - az
    elif az < 270.0:
        ns, ew, angle = u"S", u"W", az - 180.0
    else:
        ns, ew, angle = u"N", u"W", 360.0 - az

    total_seconds = int(round(angle * 3600.0))
    degrees, rest = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return u"{} {}°{:02d}'{:02d}\" {}".format(ns, degrees, minutes, seconds, ew)


def metes_and_bounds(coords):
    """
    Surveyor's description of a [lon, lat] ring: one entry per boundary
    segment, walking the ring and closing back to the first vertex.

    Each entry: {index, bearing, azimuth_deg, length_m, length_ft}

    Bearings are relative to true north, which is what the source coordinates
    are in - Revit's Project North is a separate, project-specific rotation.
    """
    ring = close_ring(coords)
    if len(ring) < 3:
        return []

    pts = ring_to_local_m(ring)
    segments = []
    count = len(pts)
    for i in range(count):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % count]
        dx, dy = x2 - x1, y2 - y1
        length_m = math.sqrt(dx * dx + dy * dy)
        if length_m < 0.01:
            continue                      # duplicate vertex
        azimuth = math.degrees(math.atan2(dx, dy)) % 360.0
        segments.append({
            "index":       len(segments) + 1,
            "bearing":     format_bearing(azimuth),
            "azimuth_deg": azimuth,
            "length_m":    length_m,
            "length_ft":   length_m * M_TO_FT,
        })
    return segments


def format_metes_and_bounds(coords, area_sqft=None, title=None):
    """
    The metes-and-bounds table as plain text, ready for a TextBlock or the
    clipboard.  Distances in metres and feet, bearings in D°M'S".
    """
    segments = metes_and_bounds(coords)
    if not segments:
        return u"No boundary segments to describe."

    lines = []
    if title:
        lines.append(title)
        lines.append(u"")
    lines.append(u"{:<4} {:<18} {:>12} {:>12}".format(
        u"#", u"BEARING", u"LENGTH (m)", u"LENGTH (ft)"))
    lines.append(u"-" * 50)
    total_m = 0.0
    for seg in segments:
        total_m += seg["length_m"]
        lines.append(u"{:<4} {:<18} {:>12.3f} {:>12.3f}".format(
            seg["index"], seg["bearing"], seg["length_m"], seg["length_ft"]))
    lines.append(u"-" * 50)
    lines.append(u"{:<23} {:>12.3f} {:>12.3f}".format(
        u"PERIMETER", total_m, total_m * M_TO_FT))
    if area_sqft:
        lines.append(u"{:<23} {}".format(u"ENCLOSED AREA",
                                         format_area_dual(area_sqft)))
    lines.append(u"")
    lines.append(u"Bearings are from TRUE north. Data © OpenStreetMap "
                 u"contributors / parcel provider - verify against a survey.")
    return u"\n".join(lines)


def format_area_dual(sqft):
    """'1,240 m2 (13,347 sqft, 0.31 ac)' - metric first, imperial in brackets."""
    try:
        sqft = float(sqft or 0)
    except (TypeError, ValueError):
        return u"N/A"
    if sqft <= 0:
        return u"N/A"
    sqm = sqft / SQM_TO_SQFT
    acres = sqft / 43560.0
    tail = u"{:,.0f} sqft".format(sqft)
    if acres >= 0.1:
        tail += u", {:.2f} ac".format(acres)
    if sqm >= 10000:
        head = u"{:,.0f} m² ({:.2f} ha)".format(sqm, sqm / 10000.0)
    else:
        head = u"{:,.0f} m²".format(sqm)
    return u"{} ({})".format(head, tail)


# ── geocoding ────────────────────────────────────────────────────────────────

def geocode(address, limit=8, language=None):
    """
    Address -> list of places, worldwide.  Nominatim first (it also returns the
    matched object's polygon), Photon as failover.

    Each place: {lat, lon, display_name, name, address{}, geojson, boundingbox,
                 osm_type, osm_id, category, type, country_code, source}

    Returns [] when the services answered but know no such place.  Raises
    ServiceUnavailable when no geocoder could be reached at all, so a dead
    network is never reported to the user as "address not found".
    """
    address = _u(address).strip()
    if not address:
        return []
    failures = []
    places = _geocode_nominatim(address, limit, language, failures)
    if places:
        return places
    places = _geocode_photon(address, limit, language, failures)
    if places:
        return places
    if len(failures) >= 2:
        raise ServiceUnavailable(unavailable_message(failures))
    return []


def _place_from_nominatim(item):
    """One Nominatim jsonv2 record -> place dict (raises on a bad lat/lon)."""
    address = item.get("address") or {}
    return {
        "lat":          float(item.get("lat")),
        "lon":          float(item.get("lon")),
        "display_name": _u(item.get("display_name")),
        "name":         _u(item.get("name")),
        "address":      address,
        "geojson":      item.get("geojson") or {},
        "boundingbox":  item.get("boundingbox") or [],
        "osm_type":     _u(item.get("osm_type")),
        "osm_id":       _u(item.get("osm_id")),
        "category":     _u(item.get("category")),
        "type":         _u(item.get("type")),
        "country_code": _u(address.get("country_code")).lower(),
        "source":       "Nominatim",
    }


def _nominatim_get(url, language, failures):
    """GET a Nominatim URL -> parsed JSON, or None (reason appended to failures)."""
    headers = {}
    if language:
        headers["Accept-Language"] = language
    _nominatim_throttle()
    try:
        status, text = http_request(url, headers=headers)
    except Exception as ex:
        _log("Nominatim network error: {}".format(ex))
        failures.append((u"OpenStreetMap Nominatim", describe_error(ex)))
        return None
    if status != 200:
        _log("Nominatim HTTP {}: {}".format(status, _u(text)[:200]))
        failures.append((u"OpenStreetMap Nominatim", describe_http_status(status)))
        return None
    try:
        return json.loads(text)
    except Exception as ex:
        _log("Nominatim bad JSON: {}".format(ex))
        failures.append((u"OpenStreetMap Nominatim", u"unreadable answer"))
        return None


def _geocode_nominatim(address, limit, language, failures=None):
    failures = failures if failures is not None else []
    url = ("{}?q={}&format=jsonv2&polygon_geojson=1&addressdetails=1"
           "&extratags=1&limit={}".format(NOMINATIM_URL,
                                          url_quote(address), int(limit)))
    raw = _nominatim_get(url, language, failures)
    places = []
    for item in raw or []:
        try:
            places.append(_place_from_nominatim(item))
        except (TypeError, ValueError, AttributeError):
            continue
    return places


def reverse_geocode(lat, lon, language=None):
    """
    (lat, lon) -> the place OpenStreetMap has at that point, or None.
    Never raises: a coordinate query must still work with no geocoder.
    """
    url = ("{}?lat={:.7f}&lon={:.7f}&format=jsonv2&polygon_geojson=1"
           "&addressdetails=1&zoom=18".format(NOMINATIM_REVERSE_URL,
                                              float(lat), float(lon)))
    raw = _nominatim_get(url, language, [])
    if not isinstance(raw, dict) or raw.get("error"):
        return None
    try:
        return _place_from_nominatim(raw)
    except (TypeError, ValueError, AttributeError):
        return None


def _geocode_photon(address, limit, language, failures=None):
    failures = failures if failures is not None else []
    url = "{}?q={}&limit={}".format(PHOTON_URL, url_quote(address), int(limit))
    if language:
        url += "&lang={}".format(url_quote(language))
    try:
        status, text = http_request(url)
    except Exception as ex:
        _log("Photon failed: {}".format(ex))
        failures.append((u"Photon", describe_error(ex)))
        return []
    if status != 200:
        failures.append((u"Photon", describe_http_status(status)))
        return []
    try:
        raw = json.loads(text)
    except Exception as ex:
        _log("Photon bad JSON: {}".format(ex))
        failures.append((u"Photon", u"unreadable answer"))
        return []

    places = []
    for feat in (raw or {}).get("features", []):
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") != "Point" or len(coords) < 2:
            continue
        bits = [props.get(k) for k in
                ("name", "housenumber", "street", "district", "city",
                 "state", "postcode", "country")]
        places.append({
            "lat":          float(coords[1]),
            "lon":          float(coords[0]),
            "display_name": u", ".join([_u(b) for b in bits if b]),
            "name":         _u(props.get("name")),
            "address":      props,
            "geojson":      {},
            "boundingbox":  [],
            "osm_type":     _u(props.get("osm_type")),
            "osm_id":       _u(props.get("osm_id")),
            "category":     _u(props.get("osm_key")),
            "type":         _u(props.get("osm_value")),
            "country_code": _u(props.get("countrycode")).lower(),
            "source":       "Photon",
        })
    return places


# ── candidate boundaries ─────────────────────────────────────────────────────

# Tag -> (human label, rank).  Lower rank wins: a surveyed plot beats a fence,
# a fence beats a roof outline.
_KIND_RULES = (
    ("boundary", "cadastral", u"Cadastral parcel",   0),
    ("boundary", "parcel",    u"Cadastral parcel",   0),
    ("place",    "plot",      u"Land plot",          1),
    ("place",    "farm",      u"Farm plot",          1),
    ("landuse",  None,        u"Land parcel",        2),
    ("leisure",  None,        u"Site boundary",      3),
    ("amenity",  None,        u"Site boundary",      3),
    ("barrier",  None,        u"Fence / wall line",  4),
    ("building", None,        u"Building footprint", 5),
    ("man_made", None,        u"Structure outline",  6),
)


def _classify(tags):
    """Tag dict -> (human label, rank)."""
    tags = tags or {}
    for key, value, label, rank in _KIND_RULES:
        got = tags.get(key)
        if not got:
            continue
        if value is not None and got != value:
            continue
        detail = u"" if got in ("yes", "true") else _u(got)
        if detail:
            return u"{} ({})".format(label, detail), rank
        return label, rank

    # Unknown feature: name it after its own tag rather than hiding it behind
    # a generic label, so "place=house" reads as "Place (house)".
    for key in sorted(tags):
        if tags[key]:
            return u"{} ({})".format(
                _u(key).replace("_", " ").title(), _u(tags[key])), 7
    return u"Mapped area", 7


def _overpass_query(lat, lon, radius_m):
    filters = ('["boundary"="cadastral"]',
               '["place"~"^(plot|farm)$"]',
               '["landuse"]',
               '["leisure"]',
               '["amenity"]',
               '["barrier"~"^(fence|wall|hedge|retaining_wall)$"]',
               '["building"]',
               '["man_made"]')
    around = "(around:{},{:.7f},{:.7f})".format(int(radius_m), lat, lon)
    parts = "".join("way{}{};".format(around, f) for f in filters)
    return "[out:json][timeout:{}];({});out geom;".format(
        OVERPASS_TIMEOUT_S - 3, parts)


def overpass_boundaries(lat, lon, radius_m=DEFAULT_RADIUS_M):
    """
    Closed ways around (lat, lon) that could serve as a property boundary.
    Returns a list of candidate dicts; an empty list on any failure.
    """
    payload = "data=" + url_quote(_overpass_query(lat, lon, radius_m))
    text = None
    for endpoint in OVERPASS_ENDPOINTS[:OVERPASS_MAX_TRIES]:
        try:
            status, body = http_request(endpoint, data=payload,
                                        timeout=OVERPASS_TIMEOUT_S)
        except Exception as ex:
            _log("Overpass {} error: {}".format(endpoint, ex))
            continue
        if status == 200 and body.lstrip().startswith("{"):
            text = body
            break
        _log("Overpass {} -> HTTP {}".format(endpoint, status))
    if text is None:
        return []

    try:
        data = json.loads(text)
    except Exception as ex:
        _log("Overpass bad JSON: {}".format(ex))
        return []

    candidates = []
    for element in data.get("elements", []):
        geometry = element.get("geometry") or []
        if len(geometry) < 4:
            continue
        first, last = geometry[0], geometry[-1]
        if (abs(first["lat"] - last["lat"]) > 1e-9 or
                abs(first["lon"] - last["lon"]) > 1e-9):
            continue                       # open way - not an enclosure
        ring = close_ring([[n["lon"], n["lat"]] for n in geometry])
        if len(ring) < 3:
            continue
        area = polygon_area_m2(ring)
        if area < 4.0 or area > MAX_PLOT_AREA_M2:
            continue
        tags = element.get("tags") or {}
        label, rank = _classify(tags)
        oversized = area > PLOT_SCALE_AREA_M2
        if oversized:
            label = u"{} — district scale".format(label)
        clat, clon = polygon_centroid(ring)
        candidates.append({
            "ring":       ring,
            "kind":       label,
            "rank":       rank,
            "oversized":  oversized,
            "area_m2":    area,
            "contains":   point_in_polygon(lon, lat, ring),
            "distance_m": haversine_m(lat, lon, clat, clon),
            "name":       _u(tags.get("name")),
            "osm_id":     element.get("id"),
            "osm_type":   element.get("type", "way"),
            "tags":       tags,
        })
    return candidates


def _bbox_ring(bbox):
    """Nominatim boundingbox ['s', 'n', 'w', 'e'] -> [lon, lat] ring."""
    try:
        south, north, west, east = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return []
    if abs(north - south) < 1e-7 or abs(east - west) < 1e-7:
        return []
    return [[west, south], [east, south], [east, north], [west, north]]


def _square_ring(lat, lon, size_m=FALLBACK_PLOT_M):
    """A north-aligned square of size_m, centred on the point."""
    half = size_m / 2.0
    dlat = math.degrees(half / EARTH_RADIUS_M)
    cos_lat = math.cos(math.radians(lat)) or 1e-9
    dlon = math.degrees(half / (EARTH_RADIUS_M * cos_lat))
    return [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat],
            [lon + dlon, lat + dlat], [lon - dlon, lat + dlat]]


def _rings_from_geojson(geojson):
    """Outer ring(s) of a GeoJSON geometry, as [lon, lat] lists."""
    if not geojson:
        return []
    gtype = geojson.get("type")
    coords = geojson.get("coordinates") or []
    rings = []
    if gtype == "Polygon" and coords:
        rings.append(close_ring(coords[0]))
    elif gtype == "MultiPolygon":
        for poly in coords:
            if poly:
                rings.append(close_ring(poly[0]))
    elif gtype in ("LineString", "LinearRing") and len(coords) >= 4:
        rings.append(close_ring(coords))
    return [r for r in rings if len(r) >= 3]


def ring_key(ring):
    """
    Identity of a ring, independent of where the vertex list starts or which
    way it winds - Nominatim and Overpass hand back the same footprint with
    different vertex order, and the user should only see it once.
    """
    if not ring:
        return "empty"
    lat, lon = polygon_centroid(ring)
    return "{:.6f},{:.6f},{:.0f}".format(lat, lon, polygon_area_m2(ring))


_ring_key = ring_key   # kept for callers written against the private name


# ── result assembly ──────────────────────────────────────────────────────────

def _parcel_dict(ring, place, kind, source, approximate=False,
                 osm_id=None, osm_type=None, feature_name=u"", extra=None):
    """Build a record in the exact shape the PropertyLine dialog consumes."""
    area_m2 = polygon_area_m2(ring)
    area_sqft = area_m2 * SQM_TO_SQFT
    addr = place.get("address") or {}
    country = _u(addr.get("country"))
    region = _u(addr.get("state") or addr.get("region") or
                addr.get("province") or addr.get("city"))
    county = _u(addr.get("county") or addr.get("district") or
                addr.get("suburb") or addr.get("city_district"))
    clat, clon = polygon_centroid(ring)

    # Identify the shape's own OSM object, not the geocoder hit it came from -
    # an Overpass way sitting under a node-matched address is still a way.
    if osm_id is not None:
        ident = u"OSM {} {}".format(_u(osm_type or u"way"), _u(osm_id))
    else:
        ident = u"OSM {} {}".format(_u(place.get("osm_type") or u"way"),
                                    _u(place.get("osm_id")))
    label = _u(place.get("display_name") or feature_name or u"Unknown location")

    return {
        "id":                ident,
        "parcel_id":         ident,
        "display_address":   label,
        "area_sqft":         u"{:,.0f}".format(area_sqft) if area_sqft else u"N/A",
        "area_sqft_raw":     area_sqft,
        "area_m2_raw":       area_m2,
        "geometry":          {"type": "Polygon", "coordinates": [ring]},
        "county":            county,
        "state":             region,
        "country":           country,
        "zoning_code":       _u((extra or {}).get("zoning")),
        "legal_description": _u(feature_name or place.get("name")),
        "flood_zone":        u"",
        "land_use":          _u((extra or {}).get("land_use")),
        "lot_width":         u"",
        "lot_depth":         u"",
        "setbacks":          {},
        # worldwide additions
        "source":            source,
        "boundary_kind":     kind,
        "is_approximate":    bool(approximate),
        "lat":               clat,
        "lon":               clon,
        "subtitle":          u"{}  ·  {}".format(kind,
                                                      format_area_dual(area_sqft)),
    }


def primary_boundaries(address, limit=8, language=None):
    """
    Fast phase (~1s): geocode the address and return the polygons the geocoder
    already carries for the matched objects.

    Returns (places, results).  Raises ValueError when nothing geocodes.
    """
    places = geocode(address, limit=max(3, int(limit)), language=language)
    if not places:
        raise ValueError(
            u"No location found for '{}'. Try adding the city and country, "
            u"e.g. '25 Le Duan, District 1, Ho Chi Minh City, Vietnam'."
            .format(_u(address)))
    return places, boundaries_from_places(places, limit=limit)


def boundaries_from_places(places, limit=8, must_contain=None):
    """
    The polygons the geocoder already carries for *places*, as parcel dicts.

    *must_contain* = (lat, lon) keeps only rings that enclose that point - a
    reverse-geocoded coordinate can land on the nearest road, whose line
    must never be closed into a fake plot.
    """
    results, seen = [], set()
    for index, place in enumerate(places or []):
        if len(results) >= limit:
            break
        for ring in _rings_from_geojson(place.get("geojson")):
            if must_contain is not None and not point_in_polygon(
                    must_contain[1], must_contain[0], ring):
                continue
            key = ring_key(ring)
            if key in seen:
                continue
            seen.add(key)
            kind, _rank = _classify({place.get("category"): place.get("type")})
            results.append(_parcel_dict(ring, place, kind, OSM_SOURCE,
                                        feature_name=place.get("name")))
            if index:
                break        # one shape per alternative match is plenty
    return results


def nearby_boundaries(place, limit=8, radius_m=DEFAULT_RADIUS_M,
                      exclude=None, include_fallback=False,
                      fallback_size_m=FALLBACK_PLOT_M):
    """
    Slow phase (~10-25s): the enclosures Overpass knows around *place*, ranked
    plot-first.  *exclude* is a set of ring_key values already shown.

    Never raises - a dead Overpass mirror just means fewer choices.
    """
    seen = set(exclude or ())
    results = []
    lat, lon = place["lat"], place["lon"]

    # A district-sized polygon that happens to contain the point is still not
    # the plot, so plot scale outranks containment.
    candidates = overpass_boundaries(lat, lon, radius_m)
    candidates.sort(key=lambda c: (c["oversized"], not c["contains"],
                                   c["rank"], c["distance_m"], c["area_m2"]))
    neighbours = 0
    for cand in candidates:
        if len(results) >= limit:
            break
        if not cand["contains"]:
            # A handful of neighbours helps when the geocoder lands slightly
            # off; a whole street of them is just noise.
            if neighbours >= MAX_NEIGHBOURS and results:
                continue
            if cand["distance_m"] > NEIGHBOUR_DISTANCE_M and results:
                continue
        key = ring_key(cand["ring"])
        if key in seen:
            continue
        seen.add(key)
        if not cand["contains"]:
            neighbours += 1
        results.append(_parcel_dict(
            cand["ring"], place, cand["kind"], OSM_SOURCE,
            osm_id=cand["osm_id"], osm_type=cand["osm_type"],
            feature_name=cand["name"],
            extra={"land_use": cand["tags"].get("landuse")}))

    # Nothing at all is mapped here: still hand back geometry at the right
    # place on earth, clearly flagged as approximate.
    if not results and not seen and include_fallback:
        ring = (_bbox_ring(place.get("boundingbox")) or
                _square_ring(lat, lon, fallback_size_m))
        results.append(_parcel_dict(
            ring, place, u"Approximate plot (no mapped boundary)",
            OSM_SOURCE, approximate=True))

    return results


def search_boundaries(address, limit=8, radius_m=DEFAULT_RADIUS_M,
                      language=None, include_fallback=True,
                      fallback_size_m=FALLBACK_PLOT_M):
    """
    Address (anywhere on earth) -> ranked list of drawable boundaries.

    No API key required.  Results are ordered best-first:
      1. the boundary of the matched object itself (Nominatim polygon)
      2. mapped plots/enclosures containing the geocoded point (Overpass)
      3. nearby enclosures, closest first
      4. an approximate square, only when nothing at all is mapped

    Blocking, both phases.  A responsive UI should call primary_boundaries()
    and nearby_boundaries() itself so the fast results paint immediately.

    Raises ValueError when the address cannot be geocoded.
    """
    places, results = primary_boundaries(address, limit=limit,
                                         language=language)
    seen = set(ring_key(r["geometry"]["coordinates"][0]) for r in results)
    results += nearby_boundaries(places[0], limit=max(0, limit - len(results)),
                                 radius_m=radius_m, exclude=seen,
                                 include_fallback=include_fallback,
                                 fallback_size_m=fallback_size_m)
    return results[:limit]
