# -*- coding: utf-8 -*-
"""
CPython 3 tests for lib/Snippets/_parcel_search.py - the automatic, worldwide
search behind the Property Line tool (no Data Source picker any more).

Run:  python3 dev/test_property_line_search.py
Exit code 0 = all pass.  Offline: the HTTP layer (_geoparcel.http_request)
is replaced by a stub that serves canned Nominatim / Photon / Overpass bodies,
and LightBox is a plain callable.

What it locks down:
    coordinates   "lat, lon" in its common spellings is recognised; street
                  numbers and VN-2000 metres are not
    country       geocoder country_code first, coarse bbox second
    planning      LightBox only for US + key; OpenStreetMap always last
    pipeline      US + key -> LightBox; LightBox empty/broken -> OSM silently;
                  Vietnam/Singapore -> OSM; typed coordinates -> reverse
                  geocode, never a road closed into a fake plot
    labels        every parcel carries a source_label for the result pill
    errors        a dead network says "Could not reach ...", never
                  "No location found"; a 403 is reported as access refused
    http          every request carries the T3Lab User-Agent and a timeout
"""
from __future__ import unicode_literals

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "T3Lab.extension", "lib"))

from Snippets import _geoparcel as gp        # noqa: E402
from Snippets import _parcel_search as ps    # noqa: E402

gp.NOMINATIM_MIN_INTERVAL_S = 0.0             # no politeness delay offline

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   {}".format(name))
    else:
        print("  FAIL {} {}".format(name, detail))
        FAILURES.append(name)


# ── stubs ────────────────────────────────────────────────────────────────────

class StubHTTP(object):
    """Replaces gp.http_request.  A value may be a body or an Exception."""

    def __init__(self, search=None, reverse=None, photon=None, overpass=None,
                 search_status=200, photon_status=200):
        self.search = search if search is not None else []
        self.reverse = reverse if reverse is not None else {"error": "Unable to geocode"}
        self.photon = photon if photon is not None else {"features": []}
        self.overpass = overpass if overpass is not None else []
        self.search_status = search_status
        self.photon_status = photon_status
        self.calls = []

    def _serve(self, value, status=200):
        if isinstance(value, Exception):
            raise value
        return status, json.dumps(value)

    def __call__(self, url, data=None, headers=None, timeout=25):
        self.calls.append(url)
        if "nominatim" in url and "/reverse" in url:
            return self._serve(self.reverse)
        if "nominatim" in url:
            return self._serve(self.search, self.search_status)
        if "photon" in url:
            return self._serve(self.photon, self.photon_status)
        if "interpreter" in url:
            return self._serve({"elements": self.overpass})
        return 404, ""


def run(stub, func):
    original = gp.http_request
    gp.http_request = stub
    try:
        return func()
    finally:
        gp.http_request = original


class LightBoxStub(object):
    def __init__(self, parcels=None, error=None):
        self.parcels = parcels or []
        self.error = error
        self.calls = []

    def __call__(self, api_key, address):
        self.calls.append((api_key, address))
        if self.error:
            raise self.error
        return [dict(p) for p in self.parcels]


def nominatim_hit(lat, lon, cc, name, size_m=30.0, category="building"):
    return {
        "lat": str(lat), "lon": str(lon), "display_name": name, "name": name,
        "address": {"country_code": cc, "country": cc.upper()},
        "geojson": {"type": "Polygon",
                    "coordinates": [gp._square_ring(lat, lon, size_m)]},
        "boundingbox": [], "osm_type": "way", "osm_id": "42",
        "category": category, "type": "yes",
    }


LIGHTBOX_PARCEL = {
    "id": "LB1", "parcel_id": "APN-1", "display_address": "1 Main St, Austin, TX",
    "area_sqft": "5,000", "area_sqft_raw": 5000.0,
    "geometry": {"type": "Polygon",
                 "coordinates": [gp._square_ring(30.27, -97.74, 20.0)]},
    "county": "Travis", "state": "TX", "source": "LightBox",
}


# ── coordinates ──────────────────────────────────────────────────────────────

def test_parse_coordinates():
    print("\n[coordinates]")
    cases = [
        ("1.2903, 103.8519", (1.2903, 103.8519)),
        ("1.2903 103.8519", (1.2903, 103.8519)),
        ("-33.8568;151.2153", (-33.8568, 151.2153)),
        ("  40.7128 , -74.0060 ", (40.7128, -74.006)),
        ("N 1.2903 E 103.8519", (1.2903, 103.8519)),
        ("1.2903N 103.8519E", (1.2903, 103.8519)),
        ("33.8568S, 151.2153E", (-33.8568, 151.2153)),
        ("103.8519E, 1.2903N", (1.2903, 103.8519)),
        ("103.8519, 1.2903", (1.2903, 103.8519)),            # lon, lat is obvious
        ("https://www.google.com/maps/place/x/@10.7769,106.7009,17z",
         (10.7769, 106.7009)),
        ("https://maps.google.com/?q=21.0278,105.8342", (21.0278, 105.8342)),
    ]
    for text, want in cases:
        got = ps.parse_coordinates(text)
        ok = got is not None and abs(got[0] - want[0]) < 1e-6 and abs(got[1] - want[1]) < 1e-6
        check("parses {!r}".format(text), ok, got)

    got = ps.parse_coordinates('48°51\'29.6"N 2°17\'40.2"E')
    check("parses DMS", got and abs(got[0] - 48.858222) < 1e-5 and abs(got[1] - 2.294500) < 1e-5, got)
    got = ps.parse_coordinates('33°52\'S 151°12\'E')
    check("parses DMS without seconds, southern hemisphere",
          got and got[0] < 0 and abs(got[1] - 151.2) < 1e-6, got)

    for text in ("10 Downing Street, London", "12 34", "268 Ly Thuong Kiet",
                 "1185420.25 594230.12", "91.5, 95.2", "45.1, 190.0",
                 "", None, "Singapore"):
        check("rejects {!r}".format(text), ps.parse_coordinates(text) is None,
              ps.parse_coordinates(text))


# ── country + planning ───────────────────────────────────────────────────────

def test_country_and_plan():
    print("\n[country + plan]")
    check("geocoder country code wins",
          ps.country_code_of({"country_code": "SG", "lat": 30.0, "lon": -97.0}) == "sg")
    check("address country_code is read",
          ps.country_code_of({"address": {"country_code": "vn"}}) == "vn")
    check("Photon countrycode is read",
          ps.country_code_of({"address": {"countrycode": "US"}}) == "us")
    check("bbox fallback finds the US",
          ps.country_code_of({"lat": 30.27, "lon": -97.74}) == "us")
    check("bbox fallback finds Hawaii", ps.country_from_point(21.3, -157.8) == "us")
    check("bbox fallback finds Vietnam", ps.country_from_point(10.77, 106.70) == "vn")
    check("elsewhere has no guess", ps.country_from_point(1.29, 103.85) == "")
    check("garbage is safe", ps.country_from_point(None, "x") == "")

    check("US + key -> LightBox then OSM",
          ps.plan_sources("us", True) == [ps.SOURCE_LIGHTBOX, ps.SOURCE_OSM])
    check("US without key -> OSM", ps.plan_sources("us", False) == [ps.SOURCE_OSM])
    check("Vietnam -> OSM", ps.plan_sources("vn", True) == [ps.SOURCE_OSM])
    check("unknown -> OSM", ps.plan_sources("", True) == [ps.SOURCE_OSM])

    check("US address hint: country",
          ps.looks_like_us_address("1 Main St, Austin, TX 78701, USA"))
    check("US address hint: state + ZIP",
          ps.looks_like_us_address("20521 Paisley Ln, Huntington Beach, CA 92646"))
    check("US address hint: state name",
          ps.looks_like_us_address("1 Main St, Austin, Texas"))
    check("no US hint for Vietnam",
          not ps.looks_like_us_address("268 Ly Thuong Kiet, Ho Chi Minh City, Vietnam"))
    check("no US hint from a stray 'in'/'or' word",
          not ps.looks_like_us_address("Rue de la Paix or Paris in France"))


def test_labels():
    print("\n[labels]")
    check("LightBox label", ps.source_label({"source": "LightBox"}) == "LightBox")
    check("OSM label", ps.source_label({"source": "OpenStreetMap"}) == "OpenStreetMap")
    check("VN-2000 label",
          ps.source_label({"source": "VN-2000 Cadastral Import"}) == "VN-2000")
    check("missing source reads as OSM", ps.source_label({}) == "OpenStreetMap")
    check("one source phrase",
          ps.describe_sources([{"source_label": "OpenStreetMap"}]) ==
          "Boundary from OpenStreetMap")
    check("two sources phrase",
          ps.describe_sources([{"source": "LightBox"}, {"source": "OpenStreetMap"}]) ==
          "Boundaries from LightBox and OpenStreetMap")
    check("empty phrase", ps.describe_sources([]) == "")


# ── pipeline ─────────────────────────────────────────────────────────────────

def test_us_with_key_uses_lightbox():
    print("\n[pipeline: US + key]")
    stub = StubHTTP(search=[nominatim_hit(30.27, -97.74, "us", "1 Main St")])
    lb = LightBoxStub(parcels=[LIGHTBOX_PARCEL])
    out = run(stub, lambda: ps.search_primary("1 Main St, Austin, TX",
                                              lightbox_search=lb, lightbox_key="k"))
    check("LightBox was asked", lb.calls == [("k", "1 Main St, Austin, TX")], lb.calls)
    check("LightBox parcels returned", out["source"] == ps.SOURCE_LIGHTBOX and
          len(out["parcels"]) == 1)
    check("tagged for the pill", out["parcels"][0]["source_label"] == "LightBox")
    check("no OSM second pass", out["context"] is None)


def test_lightbox_empty_or_broken_falls_back():
    print("\n[pipeline: LightBox fallback]")
    for label, lb in (("empty", LightBoxStub(parcels=[])),
                      ("raising", LightBoxStub(error=ValueError("Lightbox API error 401")))):
        stub = StubHTTP(search=[nominatim_hit(30.27, -97.74, "us", "1 Main St")])
        out = run(stub, lambda: ps.search_primary("1 Main St, Austin, TX",
                                                  lightbox_search=lb, lightbox_key="k"))
        check("LightBox {} -> OpenStreetMap".format(label),
              out["source"] == ps.SOURCE_OSM and out["fallback_from"] == ps.SOURCE_LIGHTBOX,
              out["source"])
        check("LightBox {} -> OSM polygon tagged".format(label),
              out["parcels"] and out["parcels"][0]["source_label"] == "OpenStreetMap")
        check("LightBox {} -> second pass queued".format(label),
              out["context"] is not None)


def test_us_without_key_skips_lightbox():
    print("\n[pipeline: US without key]")
    stub = StubHTTP(search=[nominatim_hit(30.27, -97.74, "us", "1 Main St")])
    lb = LightBoxStub(parcels=[LIGHTBOX_PARCEL])
    out = run(stub, lambda: ps.search_primary("1 Main St, Austin, TX",
                                              lightbox_search=lb, lightbox_key=""))
    check("LightBox never called without a key", lb.calls == [])
    check("OSM answers", out["source"] == ps.SOURCE_OSM)


def test_worldwide_addresses_use_osm():
    print("\n[pipeline: worldwide]")
    for query, lat, lon, cc in (("268 Lý Thường Kiệt, Quận 10", 10.7769, 106.7009, "vn"),
                                ("Raffles Place, Singapore", 1.2840, 103.8510, "sg"),
                                ("Rua Augusta 100, Lisboa", 38.7100, -9.1370, "pt")):
        lb = LightBoxStub(parcels=[LIGHTBOX_PARCEL])
        stub = StubHTTP(search=[nominatim_hit(lat, lon, cc, query)])
        out = run(stub, lambda: ps.search_primary(query, lightbox_search=lb,
                                                  lightbox_key="k"))
        check("{} -> OSM, LightBox untouched".format(cc),
              out["source"] == ps.SOURCE_OSM and lb.calls == [] and
              out["country_code"] == cc, (out["source"], lb.calls))
        check("{} -> boundary returned".format(cc), len(out["parcels"]) == 1)

    # second pass: Overpass enclosures, tagged too
    point_only = nominatim_hit(1.284, 103.851, "sg", "x")
    point_only["geojson"] = {}
    stub = StubHTTP(search=[point_only], overpass=[])
    out = run(stub, lambda: ps.search_primary("Raffles Place, Singapore"))
    more = run(stub, lambda: ps.search_more(out["context"]))
    check("nothing mapped -> approximate plot from the second pass",
          len(more) == 1 and more[0]["is_approximate"] and
          more[0]["source_label"] == "OpenStreetMap", more)
    check("search_more(None) is safe", ps.search_more(None) == [])


def test_photon_failover():
    print("\n[pipeline: Photon failover]")
    photon = {"features": [{
        "geometry": {"type": "Point", "coordinates": [151.2153, -33.8568]},
        "properties": {"name": "Sydney Opera House", "countrycode": "AU",
                       "osm_key": "building", "osm_value": "yes"}}]}
    stub = StubHTTP(search=[], search_status=503, photon=photon)
    out = run(stub, lambda: ps.search_primary("Sydney Opera House"))
    check("Nominatim down -> Photon still locates it",
          out["place"]["country_code"] == "au" and out["context"] is not None,
          out["place"])


def test_coordinates_query():
    print("\n[pipeline: coordinates]")
    lat, lon = 1.2903, 103.8519
    building = nominatim_hit(lat, lon, "sg", "Building at the point")
    stub = StubHTTP(reverse=building)
    out = run(stub, lambda: ps.search_primary("1.2903, 103.8519"))
    check("coordinates are reverse geocoded",
          any("/reverse" in u for u in stub.calls) and
          not any("/search" in u for u in stub.calls), stub.calls)
    check("the containing building is offered", len(out["parcels"]) == 1)
    check("the place keeps the typed point",
          out["place"]["lat"] == lat and out["place"]["lon"] == lon)
    check("query kind is coordinates", out["query_kind"] == "coordinates")

    road = {"lat": "1.2950", "lon": "103.8600", "display_name": "Some Road",
            "address": {"country_code": "sg"}, "osm_type": "way", "osm_id": "7",
            "category": "highway", "type": "primary",
            "geojson": {"type": "LineString", "coordinates":
                        [[103.859, 1.294], [103.860, 1.295], [103.861, 1.296],
                         [103.862, 1.297]]}}
    stub = StubHTTP(reverse=road)
    out = run(stub, lambda: ps.search_primary("1.2903, 103.8519"))
    check("a nearby road is never closed into a plot", out["parcels"] == [])
    check("Overpass pass still queued", out["context"] is not None)

    stub = StubHTTP(reverse=IOError("timed out"))
    out = run(stub, lambda: ps.search_primary("30.27, -97.74",
                                              lightbox_search=LightBoxStub(),
                                              lightbox_key="k"))
    check("coordinates survive a dead reverse geocoder",
          out["place"]["display_name"] == "30.270000, -97.740000" and
          out["context"] is not None, out["place"])
    check("bbox gives the country when the geocoder is down",
          out["country_code"] == "us")


def test_vn2000_table():
    print("\n[pipeline: VN-2000 table]")
    table = ("1 1185420.25 594230.12; 2 1185450.10 594235.40; "
             "3 1185445.00 594280.00; 4 1185415.00 594275.00")
    stub = StubHTTP()
    out = run(stub, lambda: ps.search_primary(table))
    check("pasted VN-2000 table needs no network", stub.calls == [], stub.calls)
    check("VN-2000 parcel returned", out["source"] == ps.SOURCE_VN2000 and
          out["parcels"][0]["source_label"] == "VN-2000")


def test_errors():
    print("\n[errors]")
    try:
        ps.search_primary("   ")
        check("empty query raises", False, "no exception")
    except ps.SearchError as ex:
        check("empty query asks for input", "Enter an address" in str(ex), str(ex))

    stub = StubHTTP(search=[])
    try:
        run(stub, lambda: ps.search_primary("qwzxv nowhere"))
        check("unknown place raises", False, "no exception")
    except ps.SearchError as ex:
        check("unknown place says so and suggests coordinates",
              "No location found" in str(ex) and "coordinates" in str(ex), str(ex))

    stub = StubHTTP(search=IOError("<urlopen error [Errno 11001] getaddrinfo failed>"),
                    photon=IOError("<urlopen error [Errno 11001] getaddrinfo failed>"))
    try:
        run(stub, lambda: ps.search_primary("10 Downing Street, London"))
        check("dead network raises", False, "no exception")
    except ps.SearchError as ex:
        msg = str(ex)
        check("dead network is not 'No location found'",
              "No location found" not in msg and "Could not reach" in msg, msg)
        check("the reason names the service and DNS",
              "Nominatim" in msg and "could not be resolved" in msg, msg)
        check("the message says what to do next", "proxy" in msg, msg)

    stub = StubHTTP(search=[], search_status=403, photon_status=429)
    try:
        run(stub, lambda: ps.search_primary("10 Downing Street, London"))
        check("blocked services raise", False, "no exception")
    except ps.SearchError as ex:
        msg = str(ex)
        check("403 reads as access refused", "access refused (HTTP 403)" in msg, msg)
        check("429 reads as too many requests", "HTTP 429" in msg, msg)

    # geocoder down, but a US address with a key still gets LightBox
    stub = StubHTTP(search=IOError("timed out"), photon=IOError("timed out"))
    lb = LightBoxStub(parcels=[LIGHTBOX_PARCEL])
    out = run(stub, lambda: ps.search_primary(
        "20521 Paisley Ln, Huntington Beach, CA 92646",
        lightbox_search=lb, lightbox_key="k"))
    check("geocoder down + US + key -> LightBox direct",
          out["source"] == ps.SOURCE_LIGHTBOX and lb.calls, out["source"])

    check("TLS failure is explained",
          "TLS" in gp.describe_error(Exception("The SSL connection could not be established")))
    check("timeout is explained",
          "timed out" in gp.describe_error(Exception("The operation has timed out")))


def test_http_layer():
    print("\n[http layer]")
    seen = {}

    class FakeResp(object):
        def getcode(self):
            return 200

        def read(self):
            return b"[]"

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        return FakeResp()

    original_urlopen = gp._urq.urlopen
    original_dotnet = gp._HAS_DOTNET
    gp._urq.urlopen = fake_urlopen
    gp._HAS_DOTNET = False
    try:
        status, text = gp.http_request("https://nominatim.openstreetmap.org/search?q=x",
                                       timeout=12)
    finally:
        gp._urq.urlopen = original_urlopen
        gp._HAS_DOTNET = original_dotnet
    check("request succeeds through the stub", status == 200 and text == "[]")
    check("User-Agent identifies the application (Nominatim policy)",
          (seen.get("ua") or "").startswith("T3Lab-PropertyLine/") and
          "github.com" in seen.get("ua", ""), seen.get("ua"))
    check("timeout is passed through", seen.get("timeout") == 12, seen.get("timeout"))


def main():
    test_parse_coordinates()
    test_country_and_plan()
    test_labels()
    test_us_with_key_uses_lightbox()
    test_lightbox_empty_or_broken_falls_back()
    test_us_without_key_skips_lightbox()
    test_worldwide_addresses_use_osm()
    test_photon_failover()
    test_coordinates_query()
    test_vn2000_table()
    test_errors()
    test_http_layer()

    print("")
    if FAILURES:
        print("FAILED ({}): {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all property line search tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
