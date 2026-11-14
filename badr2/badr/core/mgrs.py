"""Pure-Python WGS84 <-> UTM <-> MGRS conversion and coordinate parsing.

No QGIS dependency, so it is usable from tests, the AI tool layer and sensor
decoders alike.  UTM uses the Krueger series (sub-millimetre inside a zone).
Polar regions (UPS) are not supported and fall back to decimal degrees.
"""
import math
import re

_A = 6378137.0
_F = 1 / 298.257223563
_K0 = 0.9996
_N = _F / (2 - _F)
_AA = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)
_ALPHA = (
    _N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16,
    13 * _N**2 / 48 - 3 * _N**3 / 5,
    61 * _N**3 / 240,
)
_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96,
    _N**2 / 48 + _N**3 / 15,
    17 * _N**3 / 480,
)
_DELTA = (
    2 * _N - 2 * _N**2 / 3 - 2 * _N**3,
    7 * _N**2 / 3 - 8 * _N**3 / 5,
    56 * _N**3 / 15,
)

_BANDS = "CDEFGHJKLMNPQRSTUVWX"
_COL_SETS = ("STUVWXYZ", "ABCDEFGH", "JKLMNPQR")  # indexed by zone % 3
_ROWS = "ABCDEFGHJKLMNPQRSTUV"


def utm_zone(lat, lon):
    zone = int((lon + 180) // 6) % 60 + 1
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32  # Norway
    elif 72 <= lat <= 84 and lon >= 0:
        if lon < 9:
            zone = 31
        elif lon < 21:
            zone = 33
        elif lon < 33:
            zone = 35
        elif lon < 42:
            zone = 37
    return zone


def latlon_to_utm(lat, lon, zone=None):
    """Return (zone, hemisphere 'N'|'S', easting, northing)."""
    if zone is None:
        zone = utm_zone(lat, lon)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi = math.radians(lat)
    lam = math.radians(lon) - lon0
    e2n = 2 * math.sqrt(_N) / (1 + _N)
    t = math.sinh(math.atanh(math.sin(phi)) - e2n * math.atanh(e2n * math.sin(phi)))
    xi = math.atan2(t, math.cos(lam))
    eta = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    e = eta
    n = xi
    for j, a in enumerate(_ALPHA, start=1):
        e += a * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
        n += a * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
    easting = 500000 + _K0 * _AA * e
    northing = _K0 * _AA * n
    hemi = "N" if lat >= 0 else "S"
    if hemi == "S":
        northing += 10000000
    return zone, hemi, easting, northing


def utm_to_latlon(zone, hemi, easting, northing):
    if hemi.upper() == "S":
        northing -= 10000000
    xi = northing / (_K0 * _AA)
    eta = (easting - 500000) / (_K0 * _AA)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    phi = chi
    for j, d in enumerate(_DELTA, start=1):
        phi += d * math.sin(2 * j * chi)
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    lon0 = (zone - 1) * 6 - 180 + 3
    return math.degrees(phi), lon0 + math.degrees(lam)


def _band(lat):
    return _BANDS[min(int((lat + 80) // 8), 19)]


def to_mgrs(lat, lon, precision=5, spaced=True):
    """Format a WGS84 position as MGRS (precision 0-5 digits per axis)."""
    if not -80 <= lat <= 84:
        return format_dd(lat, lon)
    zone, _hemi, e, n = latlon_to_utm(lat, lon)
    col = _COL_SETS[zone % 3][int(e // 100000) - 1]
    row_i = int(n // 100000) % 20
    if zone % 2 == 0:
        row_i = (row_i + 5) % 20
    row = _ROWS[row_i]
    div = 10 ** (5 - precision)
    es = f"{int(e % 100000) // div:0{precision}d}" if precision else ""
    ns = f"{int(n % 100000) // div:0{precision}d}" if precision else ""
    sep = " " if spaced else ""
    return f"{zone:02d}{_band(lat)}{sep}{col}{row}{sep}{es}{sep}{ns}".strip()


_MGRS_RE = re.compile(r"^\s*(\d{1,2})\s*([C-HJ-NP-X])\s*([A-HJ-NP-Z])\s*([A-HJ-NP-V])\s*((?:\d\s*)*)$", re.I)


def from_mgrs(text):
    """Parse an MGRS string; returns (lat, lon) of the square's centre."""
    m = _MGRS_RE.match(text)
    if not m:
        raise ValueError(f"not an MGRS reference: {text!r}")
    zone = int(m.group(1))
    band = m.group(2).upper()
    col = m.group(3).upper()
    row = m.group(4).upper()
    digits = re.sub(r"\s", "", m.group(5))
    if len(digits) % 2 or len(digits) > 10 or not 1 <= zone <= 60:
        raise ValueError(f"not an MGRS reference: {text!r}")
    half = len(digits) // 2
    scale = 10 ** (5 - half)
    e_in = int(digits[:half] or 0) * scale
    n_in = int(digits[half:] or 0) * scale
    cols = _COL_SETS[zone % 3]
    if col not in cols:
        raise ValueError(f"column letter {col} invalid for zone {zone}")
    easting = (cols.index(col) + 1) * 100000 + e_in
    row_i = _ROWS.index(row)
    if zone % 2 == 0:
        row_i = (row_i - 5) % 20
    northing = row_i * 100000 + n_in
    band_lat = -80 + _BANDS.index(band) * 8
    south = band_lat < 0
    lon0 = (zone - 1) * 6 - 180 + 3
    # The lowest northing in a band lies on the central meridian at whichever
    # band edge is nearer the equator's side of the UTM northing axis.
    _z, _h, _e, min_n = latlon_to_utm(band_lat, lon0, zone)
    if south:
        min_n = latlon_to_utm(band_lat, lon0, zone)[3]
    floor = math.floor(min_n / 100000) * 100000
    while northing < floor:
        northing += 2000000
    # centre of the referenced square
    easting += scale / 2 if half < 5 else 0
    northing += scale / 2 if half < 5 else 0
    return utm_to_latlon(zone, "S" if south else "N", easting, northing)


def format_dd(lat, lon, places=5):
    return f"{abs(lat):.{places}f}°{'N' if lat >= 0 else 'S'} {abs(lon):.{places}f}°{'E' if lon >= 0 else 'W'}"


def format_dms(lat, lon):
    def one(v, pos, neg):
        h = pos if v >= 0 else neg
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        s = (v - d - m / 60) * 3600
        return f"{d:02d}°{m:02d}'{s:04.1f}\"{h}"
    return f"{one(lat, 'N', 'S')} {one(lon, 'E', 'W')}"


_DD_RE = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*([NS])?\s*[, ]\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*([EW])?\s*$", re.I)
_DMS_RE = re.compile(
    r"(\d+)\s*[°d ]\s*(\d+)\s*['m ]\s*(\d+(?:\.\d+)?)\s*[\"s]?\s*([NSEW])", re.I)


def parse_coordinate(text):
    """Parse MGRS, decimal degrees ("34.5, 71.2" / "34.5N 71.2E") or DMS.

    Returns (lat, lon) or raises ValueError.
    """
    text = text.strip()
    try:
        return from_mgrs(text)
    except ValueError:
        pass
    m = _DD_RE.match(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(3))
        if m.group(2) and m.group(2).upper() == "S":
            lat = -abs(lat)
        if m.group(4) and m.group(4).upper() == "W":
            lon = -abs(lon)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    parts = _DMS_RE.findall(text)
    if len(parts) == 2:
        vals = {}
        for d, mi, s, h in parts:
            v = int(d) + int(mi) / 60 + float(s) / 3600
            h = h.upper()
            if h in "SW":
                v = -v
            vals["lat" if h in "NS" else "lon"] = v
        if "lat" in vals and "lon" in vals:
            return vals["lat"], vals["lon"]
    raise ValueError(f"could not parse coordinate: {text!r}")
