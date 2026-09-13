# -*- coding: utf-8 -*-
"""
VN-2000 Coordinate Converter & Cadastral Parser for Vietnam.

Handles parsing of Vietnamese cadastral coordinates (Sổ đỏ / Bản đồ trích lục địa chính),
forward and inverse Transverse Mercator projection for the VN-2000 national coordinate system,
and assembly into standard parcel structures consumable by PropertyLineDialog and Revit.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

from __future__ import unicode_literals
import re
import math

__all__ = [
    "parse_coordinate_table",
    "vn2000_to_wgs84",
    "wgs84_to_vn2000",
    "create_vn2000_parcel",
    "PROVINCE_MERIDIANS",
    "calculate_polygon_area",
]

# Standard VN-2000 Ellipsoid parameters (WGS-84 based)
A = 6378137.0                # Semi-major axis (m)
F = 1.0 / 298.257223563      # Flattening
B = A * (1.0 - F)            # Semi-minor axis (m)
E2 = 2.0 * F - F * F         # First eccentricity squared
E_PRIME2 = E2 / (1.0 - E2)   # Second eccentricity squared

FALSE_EASTING = 500000.0     # 500 km false easting
K0_3DEG = 0.9999             # Scale factor for 3-degree zones (standard for cadastral maps)
K0_6DEG = 0.9996             # Scale factor for 6-degree zones (topographic maps)

# Central meridians for Vietnamese provinces (3-degree zones)
PROVINCE_MERIDIANS = {
    "Hà Nội": 105.0,
    "TP. Hồ Chí Minh": 107.75,
    "Hải Phòng": 105.75,
    "Đà Nẵng": 107.75,
    "Cần Thơ": 105.0,
    "An Giang": 104.5,
    "Bà Rịa - Vũng Tàu": 107.75,
    "Bắc Giang": 107.0,
    "Bắc Kạn": 106.5,
    "Bạc Liêu": 104.5,
    "Bắc Ninh": 105.5,
    "Bến Tre": 106.0,
    "Bình Định": 108.25,
    "Bình Dương": 107.75,
    "Bình Phước": 106.25,
    "Bình Thuận": 107.75,
    "Cà Mau": 104.5,
    "Cao Bằng": 105.75,
    "Đắk Lắk": 108.5,
    "Đắk Nông": 108.5,
    "Điện Biên": 103.0,
    "Đồng Nai": 107.75,
    "Đồng Tháp": 105.0,
    "Gia Lai": 108.5,
    "Hà Giang": 105.0,
    "Hà Nam": 105.0,
    "Hà Tĩnh": 105.5,
    "Hải Dương": 105.5,
    "Hậu Giang": 105.0,
    "Hòa Bình": 105.0,
    "Hưng Yên": 105.5,
    "Khánh Hòa": 108.25,
    "Kiên Giang": 104.5,
    "Kon Tum": 107.75,
    "Lai Châu": 103.0,
    "Lâm Đồng": 107.75,
    "Lạng Sơn": 107.25,
    "Lào Cai": 104.0,
    "Long An": 105.75,
    "Nam Định": 105.5,
    "Nghệ An": 104.75,
    "Ninh Bình": 105.0,
    "Ninh Thuận": 108.25,
    "Phú Thọ": 104.75,
    "Phú Yên": 108.25,
    "Quảng Bình": 106.0,
    "Quảng Nam": 107.75,
    "Quảng Ngãi": 108.0,
    "Quảng Ninh": 107.75,
    "Quảng Trị": 106.5,
    "Sóc Trăng": 105.5,
    "Sơn La": 104.0,
    "Tây Ninh": 105.5,
    "Thái Bình": 105.5,
    "Thái Nguyên": 106.0,
    "Thanh Hóa": 105.0,
    "Thừa Thiên Huế": 107.0,
    "Tiền Giang": 106.0,
    "Trà Vinh": 106.0,
    "Tuyên Quang": 105.0,
    "Vĩnh Long": 105.5,
    "Vĩnh Phúc": 105.0,
    "Yên Bái": 104.5,
}


def parse_coordinate_table(text):
    """Parse raw text copied from Vietnamese cadastral documents / Excel / text.

    Supports formats:
      1. Point_ID  X  Y   (e.g., "1  1185420.25  594230.12")
      2. Point_ID  Y  X   (e.g., "1  594230.12  1185420.25")
      3. X  Y             (e.g., "1185420.25, 594230.12")

    Returns list of dicts: [{'id': '1', 'x': 1185420.25, 'y': 594230.12}, ...]
    """
    if not text:
        return []

    if ';' in text and '\n' not in text:
        lines = text.strip().split(';')
    else:
        lines = text.strip().splitlines()
    points = []

    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//") or "Tọa độ" in s or "Điểm" in s:
            continue

        # Replace Vietnamese comma decimals if followed by digits
        s = re.sub(r'(\d+),(\d+)', r'\1.\2', s)

        # Tokenize by tab, comma, semicolon, or whitespace
        parts = [p.strip() for p in re.split(r'[\t,;\s]+', s) if p.strip()]
        if len(parts) < 2:
            continue

        pt_id = ""
        c1 = None
        c2 = None

        if len(parts) >= 3:
            pt_id = parts[0]
            try:
                c1 = float(parts[1])
                c2 = float(parts[2])
            except ValueError:
                continue
        elif len(parts) == 2:
            pt_id = str(len(points) + 1)
            try:
                c1 = float(parts[0])
                c2 = float(parts[1])
            except ValueError:
                continue

        if c1 is not None and c2 is not None:
            # In Vietnam:
            # X (Northing) is typically ~ 900,000 to 2,500,000 m
            # Y (Easting) is typically ~ 300,000 to 700,000 m (around 500,000 m false easting)
            if c1 < c2:
                # User gave (Y, X) - swap to ensure X=Northing, Y=Easting
                northing = c2
                easting = c1
            else:
                northing = c1
                easting = c2

            points.append({
                'id': pt_id,
                'x': northing,
                'y': easting
            })

    return points


def _meridional_arc(lat_rad):
    """Compute meridional distance from equator to given latitude."""
    e2 = E2
    e4 = e2 * e2
    e6 = e4 * e2

    a0 = 1.0 - (e2 / 4.0) - (3.0 * e4 / 64.0) - (5.0 * e6 / 256.0)
    a2 = (3.0 / 8.0) * (e2 + (e4 / 4.0) + (15.0 * e6 / 128.0))
    a4 = (15.0 / 256.0) * (e4 + (3.0 * e6 / 4.0))
    a6 = (35.0 * e6) / 3072.0

    return A * (
        a0 * lat_rad
        - a2 * math.sin(2.0 * lat_rad)
        + a4 * math.sin(4.0 * lat_rad)
        - a6 * math.sin(6.0 * lat_rad)
    )


def vn2000_to_wgs84(northing, easting, central_meridian_deg=105.0, scale_factor=K0_3DEG):
    """Convert VN-2000 projection coordinates (Northing, Easting in meters)
    to WGS-84 geographic coordinates (Latitude, Longitude in decimal degrees).
    """
    x = northing / scale_factor
    y = (easting - FALSE_EASTING) / scale_factor

    # Footpoint latitude by iteration
    mu = x / (A * (1.0 - E2 / 4.0 - 3.0 * E2 * E2 / 64.0 - 5.0 * E2 * E2 * E2 / 256.0))
    e1 = (1.0 - math.sqrt(1.0 - E2)) / (1.0 + math.sqrt(1.0 - E2))

    c1 = (3.0 / 2.0) * e1 - (27.0 / 32.0) * (e1 ** 3)
    c2 = (21.0 / 16.0) * (e1 ** 2) - (55.0 / 32.0) * (e1 ** 4)
    c3 = (151.0 / 96.0) * (e1 ** 3)
    c4 = (1097.0 / 512.0) * (e1 ** 4)

    phi1 = (
        mu
        + c1 * math.sin(2.0 * mu)
        + c2 * math.sin(4.0 * mu)
        + c3 * math.sin(6.0 * mu)
        + c4 * math.sin(8.0 * mu)
    )

    sin_phi1 = math.sin(phi1)
    cos_phi1 = math.cos(phi1)
    tan_phi1 = math.tan(phi1)

    n1 = A / math.sqrt(1.0 - E2 * sin_phi1 * sin_phi1)
    t1 = tan_phi1 * tan_phi1
    c_1 = E_PRIME2 * cos_phi1 * cos_phi1
    r1 = A * (1.0 - E2) / math.pow(1.0 - E2 * sin_phi1 * sin_phi1, 1.5)
    d = y / n1

    lat_rad = phi1 - (n1 * tan_phi1 / r1) * (
        (d * d / 2.0)
        - (5.0 + 3.0 * t1 + 10.0 * c_1 - 4.0 * c_1 * c_1 - 9.0 * E_PRIME2) * (d ** 4) / 24.0
        + (61.0 + 90.0 * t1 + 298.0 * c_1 + 45.0 * t1 * t1 - 252.0 * E_PRIME2 - 3.0 * c_1 * c_1) * (d ** 6) / 720.0
    )

    lon_rad = (
        d
        - (1.0 + 2.0 * t1 + c_1) * (d ** 3) / 6.0
        + (5.0 - 2.0 * c_1 + 28.0 * t1 - 3.0 * c_1 * c_1 + 8.0 * E_PRIME2 + 24.0 * t1 * t1) * (d ** 5) / 120.0
    ) / cos_phi1

    lat_deg = math.degrees(lat_rad)
    lon_deg = math.degrees(lon_rad) + central_meridian_deg

    return lat_deg, lon_deg


def wgs84_to_vn2000(lat_deg, lon_deg, central_meridian_deg=105.0, scale_factor=K0_3DEG):
    """Convert WGS-84 geographic coordinates (Lat, Lon) to VN-2000 (Northing, Easting in meters)."""
    lat_rad = math.radians(lat_deg)
    delta_lon_rad = math.radians(lon_deg - central_meridian_deg)

    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    tan_lat = math.tan(lat_rad)

    n = A / math.sqrt(1.0 - E2 * sin_lat * sin_lat)
    t = tan_lat * tan_lat
    c = E_PRIME2 * cos_lat * cos_lat
    m = _meridional_arc(lat_rad)

    # Easting
    easting = FALSE_EASTING + scale_factor * n * (
        delta_lon_rad * cos_lat
        + math.pow(delta_lon_rad * cos_lat, 3) / 6.0 * (1.0 - t + c)
        + math.pow(delta_lon_rad * cos_lat, 5) / 120.0 * (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * E_PRIME2)
    )

    # Northing
    northing = scale_factor * (
        m
        + n * tan_lat * (
            math.pow(delta_lon_rad * cos_lat, 2) / 2.0
            + math.pow(delta_lon_rad * cos_lat, 4) / 24.0 * (5.0 - t + 9.0 * c + 4.0 * c * c)
            + math.pow(delta_lon_rad * cos_lat, 6) / 720.0 * (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * E_PRIME2)
        )
    )

    return northing, easting


def calculate_polygon_area(points):
    """Calculate 2D planar polygon area using Shoelace formula."""
    if len(points) < 3:
        return 0.0
    n = len(points)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += points[i]['x'] * points[j]['y']
        area -= points[j]['x'] * points[i]['y']
    return abs(area) / 2.0


def create_vn2000_parcel(points, name="Thửa đất VN-2000", province="Hà Nội"):
    """Convert parsed VN-2000 points into a standard Parcel dictionary."""
    if len(points) < 3:
        return None

    meridian = PROVINCE_MERIDIANS.get(province, 105.0)

    # Calculate geographic coordinates for each point
    ring = []
    for p in points:
        lat, lon = vn2000_to_wgs84(p['x'], p['y'], central_meridian_deg=meridian)
        ring.append([lon, lat])

    # Ensure closed loop
    if ring[0] != ring[-1]:
        ring.append(ring[0])

    area_m2 = calculate_polygon_area(points)
    area_sqft = area_m2 * 10.763910416709722

    clat, clon = vn2000_to_wgs84(
        sum(p['x'] for p in points) / len(points),
        sum(p['y'] for p in points) / len(points),
        central_meridian_deg=meridian
    )

    return {
        "id": "VN2000_{}".format(abs(hash(name)) % 100000),
        "parcel_id": name,
        "display_address": "{}, {}".format(name, province),
        "area_sqft": "{:,.0f}".format(area_sqft),
        "area_sqft_raw": area_sqft,
        "area_m2_raw": area_m2,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "county": province,
        "state": "Vietnam",
        "country": "Vietnam",
        "zoning_code": "VN-2000",
        "legal_description": "Cadastral boundary with {} boundary markers".format(len(points)),
        "flood_zone": "",
        "land_use": "Residential / Commercial",
        "lot_width": "",
        "lot_depth": "",
        "setbacks": {},
        "source": "VN-2000 Cadastral Import",
        "boundary_kind": "Cadastral Parcel",
        "is_approximate": False,
        "lat": clat,
        "lon": clon,
        "subtitle": "VN-2000 ({}) · {:,.1f} m²".format(province, area_m2),
        "vn2000_points": points,
    }
