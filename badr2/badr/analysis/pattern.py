"""Incident pattern analysis for sub-conventional operations.

Works on the ``incidents`` layer (SIGACTS).  Three questions a COIN/CT planner
asks every day: *where* do incidents cluster, *when* do they happen, and which
of *my routes* run through them.
"""
import math
import re
from collections import Counter, defaultdict
from datetime import datetime

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY

from ..core import geodesy
from ..core.mgrs import to_mgrs

_DTG = re.compile(r"^(\d{2})(\d{2})(\d{2})Z?\s*([A-Za-z]{3})\s*(\d{2,4})$")
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_dtg(text):
    """Parse a NATO DTG (DDHHMMZMONYY) or ISO timestamp -> datetime | None."""
    if not text:
        return None
    t = str(text).strip()
    m = _DTG.match(t)
    if m and m.group(4).upper() in _MONTHS:
        year = int(m.group(5))
        year += 2000 if year < 100 else 0
        try:
            return datetime(year, _MONTHS[m.group(4).upper()], int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(t.replace("Z", ""))
    except ValueError:
        return None


def _incidents(wb, kind=None):
    rows = []
    for f in wb.operation.layer("incidents").getFeatures():
        if f["review"] == "rejected":
            continue
        if kind and kind.lower() not in str(f["kind"] or "").lower() + str(f["name"] or "").lower():
            continue
        p = f.geometry().asPoint()
        rows.append({"uid": f["uid"], "lat": p.y(), "lon": p.x(), "kind": f["kind"] or "incident",
                     "name": f["name"], "when": parse_dtg(f["dtg"]), "casualties": f["casualties"] or 0})
    return rows


def clusters(rows, eps_m=500.0, min_points=3):
    """DBSCAN on great-circle distance (incident counts are small; O(n^2) is fine)."""
    n = len(rows)
    labels = [None] * n

    def near(i):
        return [j for j in range(n)
                if geodesy.distance(rows[i]["lat"], rows[i]["lon"], rows[j]["lat"], rows[j]["lon"]) <= eps_m]
    cid = -1
    for i in range(n):
        if labels[i] is not None:
            continue
        nb = near(i)
        if len(nb) < min_points:
            labels[i] = -1
            continue
        cid += 1
        labels[i] = cid
        queue = [j for j in nb if j != i]
        while queue:
            j = queue.pop()
            if labels[j] == -1:
                labels[j] = cid
            if labels[j] is not None:
                continue
            labels[j] = cid
            nb2 = near(j)
            if len(nb2) >= min_points:
                queue.extend(nb2)
    groups = defaultdict(list)
    for i, lab in enumerate(labels):
        if lab is not None and lab >= 0:
            groups[lab].append(rows[i])
    return sorted(groups.values(), key=len, reverse=True)


def hotspots(wb, eps_m=500.0, min_points=3, kind=None):
    """Cluster incidents into hotspot polygons and rank them."""
    rows = _incidents(wb, kind)
    if not rows:
        return {"hotspots": [], "summary": "No incidents are recorded yet."}
    groups = clusters(rows, eps_m, min_points)
    layer = wb.result_layer("areas", "Incident hotspots")
    feats, out = [], []
    for i, g in enumerate(groups, start=1):
        pts = [QgsPointXY(r["lon"], r["lat"]) for r in g]
        hull = QgsGeometry.fromMultiPointXY(pts).convexHull()
        lat = sum(r["lat"] for r in g) / len(g)
        lon = sum(r["lon"] for r in g) / len(g)
        pad = (eps_m / 2) / 111320.0
        geom = hull.buffer(pad, 12) if not hull.isEmpty() else QgsGeometry.fromPointXY(pts[0]).buffer(pad, 12)
        kinds = Counter(r["kind"] for r in g).most_common(3)
        f = QgsFeature(layer.fields())
        f.setGeometry(geom)
        f["kind"] = "hotspot"
        f["name"] = f"{i} ({len(g)})"
        f["affiliation"] = "hostile"
        f["source"] = "analysis"
        f["remarks"] = ", ".join(f"{k} x{n}" for k, n in kinds)
        feats.append(f)
        out.append({"rank": i, "incidents": len(g), "mgrs": to_mgrs(lat, lon, 4),
                    "lat": round(lat, 5), "lon": round(lon, 5),
                    "casualties": sum(r["casualties"] for r in g),
                    "main_types": [k for k, _n in kinds]})
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    if not out:
        return {"layer": layer.name(), "hotspots": [],
                "summary": f"{len(rows)} incidents, but no group of {min_points}+ within {eps_m:g} m."}
    top = out[0]
    return {"layer": layer.name(), "hotspots": out,
            "summary": f"{len(out)} hotspot(s) from {len(rows)} incidents. Worst: {top['incidents']} incidents "
                       f"near {top['mgrs']} ({', '.join(top['main_types'])})."}


def temporal(wb, kind=None):
    """Hour-of-day x day-of-week matrix plus peak windows (pattern of life)."""
    rows = [r for r in _incidents(wb, kind) if r["when"]]
    matrix = [[0] * 24 for _ in range(7)]
    by_kind = Counter()
    for r in rows:
        matrix[r["when"].weekday()][r["when"].hour] += 1
        by_kind[r["kind"]] += 1
    hours = [sum(matrix[d][h] for d in range(7)) for h in range(24)]
    days = [sum(matrix[d]) for d in range(7)]
    total = len(rows)
    if not total:
        return {"matrix": matrix, "total": 0, "summary": "No dated incidents to analyse."}
    # best 3-hour window (wrapping midnight)
    best_h = max(range(24), key=lambda h: hours[h] + hours[(h + 1) % 24] + hours[(h + 2) % 24])
    window = hours[best_h] + hours[(best_h + 1) % 24] + hours[(best_h + 2) % 24]
    best_d = max(range(7), key=lambda d: days[d])
    first = min(r["when"] for r in rows)
    last = max(r["when"] for r in rows)
    span_days = max(1.0, (last - first).total_seconds() / 86400)
    return {
        "matrix": matrix, "total": total, "by_hour": hours, "by_day": dict(zip(DAYS, days)),
        "by_type": dict(by_kind.most_common()),
        "peak_window": f"{best_h:02d}00-{(best_h + 3) % 24:02d}00Z", "peak_window_share": round(window / total, 2),
        "peak_day": DAYS[best_d], "rate_per_week": round(total / span_days * 7, 1),
        "summary": f"{total} dated incidents over {span_days:.0f} days ({total / span_days * 7:.1f}/week). "
                   f"{100 * window / total:.0f}% fall between {best_h:02d}00 and {(best_h + 3) % 24:02d}00Z; "
                   f"busiest day is {DAYS[best_d]}. Most common: "
                   f"{', '.join(f'{k} ({n})' for k, n in by_kind.most_common(3))}.",
    }


def _seg_dist(lat, lon, a, b):
    """Distance in metres from a point to segment a-b (local flat approximation)."""
    k = math.cos(math.radians(lat))
    px, py = lon * k, lat
    ax, ay, bx, by = a.x() * k, a.y(), b.x() * k, b.y()
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)) * 111320.0


def route_threat(wb, corridor_m=300.0, layer=None):
    """Score each route by the incidents recorded within a corridor around it."""
    rows = _incidents(wb)
    src = layer or wb.operation.layer("lines")
    results = []
    for f in src.getFeatures():
        names = src.fields().names()
        kind = f["kind"] if "kind" in names else "route"
        if layer is None and kind not in ("route", "patrol_route", "axis_of_advance", "infiltration_lane"):
            continue
        geom = f.geometry()
        verts = [QgsPointXY(v.x(), v.y()) for v in geom.vertices()]
        if len(verts) < 2:
            continue
        hits = []
        for r in rows:
            d = min(_seg_dist(r["lat"], r["lon"], verts[i], verts[i + 1]) for i in range(len(verts) - 1))
            if d <= corridor_m:
                hits.append(r)
        length_km = sum(geodesy.distance(verts[i].y(), verts[i].x(), verts[i + 1].y(), verts[i + 1].x())
                        for i in range(len(verts) - 1)) / 1000
        results.append({
            "route": (f["name"] if "name" in names else "") or kind, "uid": f["uid"] if "uid" in names else None,
            "length_km": round(length_km, 2), "incidents": len(hits),
            "per_km": round(len(hits) / max(length_km, 0.1), 2),
            "types": dict(Counter(h["kind"] for h in hits).most_common(4)),
        })
    results.sort(key=lambda r: -r["per_km"])
    if not results:
        return {"routes": [], "summary": "No routes are drawn yet."}
    worst, best = results[0], results[-1]
    msg = (f"{len(results)} route(s) assessed against {len(rows)} incidents within {corridor_m:g} m. "
           f"Highest exposure: {worst['route']} ({worst['incidents']} incidents, {worst['per_km']}/km).")
    if len(results) > 1:
        msg += f" Lowest: {best['route']} ({best['incidents']} incidents)."
    return {"routes": results, "summary": msg}
