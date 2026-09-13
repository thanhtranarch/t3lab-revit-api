# -*- coding: utf-8 -*-
"""
CPython 3 test harness for lib/Snippets/_vn2000.py — Vietnamese Cadastral
Coordinate System (VN-2000) and boundary conversion.

Run:  python dev/test_vn2000.py
Exit code 0 = all pass.
"""
from __future__ import unicode_literals

import os
import sys
import math

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "T3Lab.extension", "lib"))

from Snippets import _vn2000 as vn

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   {}".format(name))
    else:
        print("  FAIL {} {}".format(name, detail))
        FAILURES.append(name)


def test_table_parsing_whitespace():
    raw = """
    Tọa độ đỉnh thửa đất (VN-2000)
    Điểm    X(m)         Y(m)
    1       1185420.25   594230.12
    2       1185450.10   594235.40
    3       1185445.00   594280.00
    4       1185415.00   594275.00
    """
    pts = vn.parse_coordinate_table(raw)
    check("parse whitespace lines", len(pts) == 4, "got {}".format(len(pts)))
    check("parse pt 1 id", pts[0]['id'] == '1')
    check("parse pt 1 northing", abs(pts[0]['x'] - 1185420.25) < 0.001)
    check("parse pt 1 easting", abs(pts[0]['y'] - 594230.12) < 0.001)


def test_table_parsing_semicolons():
    raw = "1 1185420.25 594230.12; 2 1185450.10 594235.40; 3 1185445.00 594280.00; 4 1185415.00 594275.00"
    pts = vn.parse_coordinate_table(raw)
    check("parse semicolon delimited", len(pts) == 4, "got {}".format(len(pts)))


def test_table_parsing_xy_swap():
    raw = """
    1  594230.12  1185420.25
    2  594235.40  1185450.10
    3  594280.00  1185445.00
    """
    pts = vn.parse_coordinate_table(raw)
    check("auto swap Y X to Northing Easting",
          pts[0]['x'] > 1000000 and pts[0]['y'] < 700000,
          "x={} y={}".format(pts[0]['x'], pts[0]['y']))


def test_projection_roundtrip():
    northing_orig = 2325000.0
    easting_orig = 588000.0
    meridian = 105.0

    lat, lon = vn.vn2000_to_wgs84(northing_orig, easting_orig, central_meridian_deg=meridian)
    check("vn2000 to wgs84 lat plausible for Vietnam", 8.0 < lat < 24.0, "lat={}".format(lat))
    check("vn2000 to wgs84 lon plausible for Vietnam", 102.0 < lon < 110.0, "lon={}".format(lon))

    northing_back, easting_back = vn.wgs84_to_vn2000(lat, lon, central_meridian_deg=meridian)
    d_north = abs(northing_orig - northing_back)
    d_east = abs(easting_orig - easting_back)

    check("projection roundtrip northing sub-millimeter", d_north < 0.001, "diff={:.6f}m".format(d_north))
    check("projection roundtrip easting sub-millimeter", d_east < 0.001, "diff={:.6f}m".format(d_east))


def test_area_calculation():
    pts = [
        {'id': '1', 'x': 1000.0, 'y': 2000.0},
        {'id': '2', 'x': 1030.0, 'y': 2000.0},
        {'id': '3', 'x': 1030.0, 'y': 2040.0},
        {'id': '4', 'x': 1000.0, 'y': 2040.0},
    ]
    area = vn.calculate_polygon_area(pts)
    check("polygon shoelace area 1200 m2", abs(area - 1200.0) < 0.0001, "area={}".format(area))


def test_create_vn2000_parcel():
    pts = [
        {'id': '1', 'x': 1185420.0, 'y': 594230.0},
        {'id': '2', 'x': 1185450.0, 'y': 594230.0},
        {'id': '3', 'x': 1185450.0, 'y': 594270.0},
        {'id': '4', 'x': 1185420.0, 'y': 594270.0},
    ]
    parcel = vn.create_vn2000_parcel(pts, name="Thửa đất số 45", province="TP. Hồ Chí Minh")
    check("parcel created", parcel is not None)
    check("parcel has vn2000_points", len(parcel.get('vn2000_points', [])) == 4)
    check("parcel area m2 matches 1200", abs(parcel['area_m2_raw'] - 1200.0) < 0.01)
    ring = parcel['geometry']['coordinates'][0]
    check("polygon ring closed (first == last)", ring[0] == ring[-1])
    check("ring length 5", len(ring) == 5)


def main():
    print("Running VN-2000 Cadastral & Projection Tests...")
    test_table_parsing_whitespace()
    test_table_parsing_semicolons()
    test_table_parsing_xy_swap()
    test_projection_roundtrip()
    test_area_calculation()
    test_create_vn2000_parcel()

    if FAILURES:
        print("\n{} failure(s) in VN-2000 test harness.".format(len(FAILURES)))
        sys.exit(1)
    else:
        print("\nAll VN-2000 tests passed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()
