"""Small spherical-earth helpers (planning accuracy, no QGIS dependency)."""
import math

R_EARTH = 6371008.8


def destination(lat, lon, bearing_deg, distance_m):
    """Point reached travelling ``distance_m`` on ``bearing_deg`` from lat/lon."""
    d = distance_m / R_EARTH
    b = math.radians(bearing_deg)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def distance(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1, lon1, lat2, lon2):
    """Initial bearing in degrees true from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def circle(lat, lon, radius_m, segments=72):
    """Closed ring of (lon, lat) tuples approximating a geodesic circle."""
    pts = []
    for i in range(segments):
        la, lo = destination(lat, lon, 360.0 * i / segments, radius_m)
        pts.append((lo, la))
    pts.append(pts[0])
    return pts


def sector(lat, lon, radius_m, azimuth_deg, width_deg, segments=24):
    """Closed ring of (lon, lat) for a sector (e.g. a sensor field of view)."""
    pts = [(lon, lat)]
    start = azimuth_deg - width_deg / 2
    for i in range(segments + 1):
        la, lo = destination(lat, lon, start + width_deg * i / segments, radius_m)
        pts.append((lo, la))
    pts.append((lon, lat))
    return pts


def parse_distance(text):
    """'5km' / '800 m' / '3nm' / '2mi' / plain number (metres) -> metres."""
    t = str(text).strip().lower().replace(",", "")
    units = (("km", 1000.0), ("nmi", 1852.0), ("nm", 1852.0), ("mi", 1609.344),
             ("ft", 0.3048), ("m", 1.0))
    for suffix, factor in units:
        if t.endswith(suffix):
            return float(t[: -len(suffix)]) * factor
    return float(t)
