"""Terrain analysis for planning: visibility, mobility, landing zones, routes.

Every function takes the Workbench, adds its result layer(s) to the map and
returns a small dict with a plain-language ``summary`` so the same call serves
the UI and the AI agent.  Thresholds are planning approximations and are
exposed as parameters - they are not a substitute for ground reconnaissance.
"""
import heapq
import math

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsColorRampShader, QgsGeometry, QgsPalettedRasterRenderer, QgsPointXY, QgsRasterLayer,
    QgsRasterShader, QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtGui import QColor

from ..core import geodesy
from ..core.mgrs import to_mgrs
from . import raster as R

# max slope (degrees) for GO / SLOW-GO; steeper is NO-GO
MOBILITY = {
    "wheeled": (8.5, 17.0),      # ~15 % / ~30 %
    "tracked": (17.0, 24.0),     # ~30 % / ~45 %
    "dismounted": (20.0, 35.0),
}
# nominal cross-country speed on flat ground, km/h
BASE_SPEED = {"wheeled": 30.0, "tracked": 25.0, "dismounted": 4.5}


class NoDem(RuntimeError):
    pass


def _dem_path(wb):
    dem = wb.dem()
    if dem is None:
        raise NoDem("No elevation layer is loaded. Load a DEM first (Terrain > Get elevation, "
                    "or ask: 'get elevation for this area').")
    return dem.source()


def _add_raster(wb, path, name, renderer_factory, opacity=0.6):
    layer = QgsRasterLayer(path, name)
    layer.setRenderer(renderer_factory(layer))
    layer.setOpacity(opacity)
    wb.add_layer(layer, "Analysis")
    return layer


def _paletted(classes):
    def make(layer):
        cls = [QgsPalettedRasterRenderer.Class(v, QColor(c), label) for v, c, label in classes]
        return QgsPalettedRasterRenderer(layer.dataProvider(), 1, cls)
    return make


# ---------------------------------------------------------------------------
def viewshed(wb, lat, lon, observer_height=2.0, target_height=1.7, radius_m=5000.0, name=None):
    """What can be seen from a point (observation post, sensor mast, overwatch)."""
    tmp = wb.out_path("dem_local")
    path, epsg, (x, y) = R.local_dem(_dem_path(wb), lat, lon, radius_m, tmp)
    out = wb.out_path("viewshed")
    ds = gdal.Open(path)
    gdal.ViewshedGenerate(
        srcBand=ds.GetRasterBand(1), driverName="GTiff", targetRasterName=out,
        creationOptions=["COMPRESS=DEFLATE"], observerX=x, observerY=y,
        observerHeight=float(observer_height), targetHeight=float(target_height),
        visibleVal=1, invisibleVal=0, outOfRangeVal=0, noDataVal=0, dfCurvCoeff=0.85714,
        mode=gdal.GVM_Edge, maxDistance=float(radius_m))
    ds = None
    arr, gt, _proj = R.read(out)
    rows, cols = np.indices(arr.shape)
    cx, cy = R.world_to_px(gt, x, y)
    in_range = np.hypot((cols - cx) * gt[1], (rows - cy) * gt[5]) <= radius_m
    visible = float(np.nansum((arr == 1) & in_range))
    pct = 100.0 * visible / max(1.0, float(in_range.sum()))
    title = name or f"Viewshed {to_mgrs(lat, lon, 4)}"
    layer = _add_raster(wb, out, title, _paletted([(1, "#32FF64", "Visible")]), 0.45)
    R.safe_remove(tmp)
    return {"layer": layer.name(), "visible_percent": round(pct, 1),
            "visible_km2": round(visible * gt[1] * abs(gt[5]) / 1e6, 2),
            "summary": f"From {to_mgrs(lat, lon, 4)} (observer {observer_height} m), {pct:.0f}% of the "
                       f"ground within {radius_m / 1000:.1f} km is visible; the rest is dead ground."}


def line_of_sight(wb, lat1, lon1, lat2, lon2, observer_height=2.0, target_height=1.7):
    """Is point 2 visible from point 1?  Adds green (seen) / red (masked) segments."""
    dist = geodesy.distance(lat1, lon1, lat2, lon2)
    mlat, mlon = (lat1 + lat2) / 2, (lon1 + lon2) / 2
    tmp = wb.out_path("dem_los")
    path, epsg, _c = R.local_dem(_dem_path(wb), mlat, mlon, dist / 2 + 300, tmp)
    arr, gt, _proj = R.read(path)
    R.safe_remove(tmp)
    tr = R.transformer(4326, epsg)
    back = R.transformer(epsg, 4326)
    x1, y1, _ = tr.TransformPoint(lon1, lat1)
    x2, y2, _ = tr.TransformPoint(lon2, lat2)
    n = max(20, int(dist / max(abs(gt[1]), 1.0)))
    ts = np.linspace(0, 1, n + 1)
    xs, ys = x1 + (x2 - x1) * ts, y1 + (y2 - y1) * ts
    cols = np.clip(((xs - gt[0]) / gt[1]).astype(int), 0, arr.shape[1] - 1)
    rows = np.clip(((ys - gt[3]) / gt[5]).astype(int), 0, arr.shape[0] - 1)
    elev = arr[rows, cols]
    elev = np.where(np.isnan(elev), np.nanmin(elev) if np.isfinite(elev).any() else 0, elev)
    d = ts * dist
    # earth curvature with standard refraction (k = 4/3)
    elev_c = elev - d * (dist - d) / (2 * (4 / 3) * geodesy.R_EARTH)
    eye = elev_c[0] + observer_height
    visible = np.ones(n + 1, dtype=bool)
    max_angle = -1e9
    for i in range(1, n + 1):
        tgt = elev_c[i] + (target_height if i == n else 0.0)
        ang = (tgt - eye) / d[i]
        ground_ang = (elev_c[i] - eye) / d[i]
        visible[i] = ang >= max_angle
        max_angle = max(max_angle, ground_ang)
    clear = bool(visible[-1])
    first_block = next((float(d[i]) for i in range(1, n + 1) if not visible[i]), None)

    layer = wb.result_layer("lines", f"LOS {to_mgrs(lat1, lon1, 3)} > {to_mgrs(lat2, lon2, 3)}")
    from qgis.core import QgsFeature
    feats, start = [], 0
    for i in range(1, n + 2):
        if i == n + 1 or visible[i] != visible[start]:
            end = min(i, n)
            pts = []
            for j in range(start if start == 0 else start - 1, end + 1):
                lo, la, _ = back.TransformPoint(float(xs[j]), float(ys[j]))
                pts.append(QgsPointXY(lo, la))
            if len(pts) >= 2:
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromPolylineXY(pts))
                f["kind"] = "los_visible" if visible[start] else "los_hidden"
                feats.append(f)
            start = i
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    msg = (f"Line of sight over {dist:.0f} m is CLEAR." if clear else
           f"Line of sight over {dist:.0f} m is BLOCKED; terrain first masks the line "
           f"{first_block:.0f} m from the observer.")
    return {"layer": layer.name(), "clear": clear, "distance_m": round(dist),
            "first_obstruction_m": None if first_block is None else round(first_block),
            "summary": msg}


def mobility(wb, bbox=None, mover="wheeled", name=None):
    """Cross-country movement overlay: GO / SLOW-GO / NO-GO from slope."""
    if mover not in MOBILITY:
        raise ValueError(f"mover must be one of {sorted(MOBILITY)}")
    bbox = bbox or wb.extent_wgs84()
    tmp = wb.out_path("dem_mob")
    path, _epsg = R.bbox_dem(_dem_path(wb), *bbox, tmp)
    arr, gt, proj = R.read(path)
    R.safe_remove(tmp)
    s = R.slope_deg(arr, gt)
    go, slow = MOBILITY[mover]
    cls = np.zeros(s.shape, dtype="uint8")
    cls[s <= go] = 1
    cls[(s > go) & (s <= slow)] = 2
    cls[s > slow] = 3
    cls[np.isnan(s)] = 0
    out = R.write(wb.out_path(f"mobility_{mover}"), cls, gt, proj, nodata=0, dtype=gdal.GDT_Byte)
    total = max(1, int((cls > 0).sum()))
    pct = {k: round(100.0 * int((cls == v).sum()) / total, 1)
           for k, v in (("go", 1), ("slow_go", 2), ("no_go", 3))}
    layer = _add_raster(wb, out, name or f"Mobility - {mover}", _paletted([
        (1, "#3CB44B", "GO"), (2, "#FFD23C", "SLOW-GO"), (3, "#E6194B", "NO-GO")]), 0.5)
    return {"layer": layer.name(), **pct,
            "summary": f"{mover.capitalize()} movement: {pct['go']:.0f}% GO, {pct['slow_go']:.0f}% SLOW-GO, "
                       f"{pct['no_go']:.0f}% NO-GO (slope thresholds {go}° / {slow}°)."}


def slope_map(wb, bbox=None):
    bbox = bbox or wb.extent_wgs84()
    tmp = wb.out_path("dem_slope")
    path, _epsg = R.bbox_dem(_dem_path(wb), *bbox, tmp)
    arr, gt, proj = R.read(path)
    R.safe_remove(tmp)
    s = R.slope_deg(arr, gt)
    out = R.write(wb.out_path("slope"), np.where(np.isnan(s), -1, s), gt, proj, nodata=-1)

    def make(layer):
        fn = QgsColorRampShader(0, 45, None, QgsColorRampShader.Interpolated)
        fn.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(0, QColor("#1a9641"), "0°"),
            QgsColorRampShader.ColorRampItem(10, QColor("#ffffbf"), "10°"),
            QgsColorRampShader.ColorRampItem(25, QColor("#fdae61"), "25°"),
            QgsColorRampShader.ColorRampItem(45, QColor("#d7191c"), "45°+")])
        sh = QgsRasterShader()
        sh.setRasterShaderFunction(fn)
        return QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, sh)
    layer = _add_raster(wb, out, "Slope (degrees)", make, 0.55)
    return {"layer": layer.name(), "mean_slope": round(float(np.nanmean(s)), 1),
            "max_slope": round(float(np.nanmax(s)), 1),
            "summary": f"Slope: mean {np.nanmean(s):.1f}°, max {np.nanmax(s):.1f}°."}


def landing_zones(wb, bbox=None, max_slope=7.0, min_diameter_m=50.0, max_results=15):
    """Candidate helicopter landing zones: flat, large-enough patches.

    Only terrain is assessed here; obstacles, surface and vegetation must be
    checked against imagery (use the land-cover layer or current imagery).
    """
    bbox = bbox or wb.extent_wgs84()
    tmp = wb.out_path("dem_hlz")
    path, epsg = R.bbox_dem(_dem_path(wb), *bbox, tmp)
    arr, gt, proj = R.read(path)
    R.safe_remove(tmp)
    s = R.slope_deg(arr, gt)
    flat = (s <= max_slope) & ~np.isnan(s)
    # erode by the LZ radius so only patches that can hold the full circle remain
    r_px = max(1, int(round(min_diameter_m / 2 / abs(gt[1]))))
    core = flat.copy()
    for _ in range(r_px):
        c = core.copy()
        c[1:, :] &= core[:-1, :]
        c[:-1, :] &= core[1:, :]
        c[:, 1:] &= core[:, :-1]
        c[:, :-1] &= core[:, 1:]
        core = c
    polys = R.polygonize(core, gt, proj)
    back = R.transformer(epsg, 4326)
    layer = wb.result_layer("areas", "HLZ candidates")
    from qgis.core import QgsFeature
    feats, found = [], []
    for i, (geom, area) in enumerate(polys[:max_results], start=1):
        geom = geom.Buffer(r_px * abs(gt[1]))        # restore the eroded margin
        geom.Transform(back)
        g = QgsGeometry.fromWkt(geom.ExportToWkt())
        c = g.centroid().asPoint()
        f = QgsFeature(layer.fields())
        f.setGeometry(g)
        f["kind"] = "hlz_candidate"
        f["name"] = str(i)
        f["source"] = "analysis"
        f["remarks"] = f"core area {area:.0f} m2, slope <= {max_slope} deg"
        feats.append(f)
        found.append({"id": i, "mgrs": to_mgrs(c.y(), c.x(), 4), "lat": round(c.y(), 5),
                      "lon": round(c.x(), 5), "core_area_m2": round(area)})
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return {"layer": layer.name(), "candidates": found,
            "summary": (f"{len(found)} candidate landing zones (slope <= {max_slope}°, clear circle >= "
                        f"{min_diameter_m:.0f} m). Largest at {found[0]['mgrs']}." if found else
                        f"No terrain patch satisfies slope <= {max_slope}° with a {min_diameter_m:.0f} m circle.")}


def route(wb, lat1, lon1, lat2, lon2, mover="dismounted", name=None, max_px=350):
    """Least-cost cross-country route avoiding NO-GO slopes; returns distance and time."""
    if mover not in MOBILITY:
        raise ValueError(f"mover must be one of {sorted(MOBILITY)}")
    pad = max(1500.0, geodesy.distance(lat1, lon1, lat2, lon2) * 0.35)
    dlat = pad / 111320.0
    dlon = pad / (111320.0 * math.cos(math.radians((lat1 + lat2) / 2)))
    bbox = (min(lon1, lon2) - dlon, min(lat1, lat2) - dlat, max(lon1, lon2) + dlon, max(lat1, lat2) + dlat)
    tmp = wb.out_path("dem_route")
    path, epsg = R.bbox_dem(_dem_path(wb), *bbox, tmp, max_px=max_px)
    arr, gt, _proj = R.read(path)
    R.safe_remove(tmp)
    s = R.slope_deg(arr, gt)
    go, slow = MOBILITY[mover]
    if mover == "dismounted":
        # Tobler's hiking function (isotropic form), km/h
        speed = 6.0 * np.exp(-3.5 * np.abs(np.tan(np.radians(s)) + 0.05))
    else:
        speed = BASE_SPEED[mover] * np.clip(1.0 - s / (slow * 1.15), 0.08, 1.0)
        speed[(s > go) & (s <= slow)] *= 0.5
    speed[s > slow] = 0.15                     # effectively impassable but keeps the graph connected
    speed[np.isnan(s)] = 0.15
    cost = (abs(gt[1]) / 1000.0) / speed        # hours per cell width

    tr = R.transformer(4326, epsg)
    back = R.transformer(epsg, 4326)
    sx, sy, _ = tr.TransformPoint(lon1, lat1)
    ex, ey, _ = tr.TransformPoint(lon2, lat2)
    h, w = cost.shape
    sc, sr = R.world_to_px(gt, sx, sy)
    ec, er = R.world_to_px(gt, ex, ey)
    sc, ec = min(max(sc, 0), w - 1), min(max(ec, 0), w - 1)
    sr, er = min(max(sr, 0), h - 1), min(max(er, 0), h - 1)

    best = np.full((h, w), np.inf)
    prev = np.full((h, w, 2), -1, dtype=np.int32)
    best[sr, sc] = 0.0
    heap = [(0.0, sr, sc)]
    nbrs = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
            (-1, -1, 1.4142), (-1, 1, 1.4142), (1, -1, 1.4142), (1, 1, 1.4142)]
    while heap:
        c0, r, c = heapq.heappop(heap)
        if (r, c) == (er, ec):
            break
        if c0 > best[r, c]:
            continue
        for dr, dc, k in nbrs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w:
                nc = c0 + k * (cost[r, c] + cost[rr, cc]) / 2
                if nc < best[rr, cc]:
                    best[rr, cc] = nc
                    prev[rr, cc] = (r, c)
                    heapq.heappush(heap, (nc, rr, cc))
    cells = []
    r, c = er, ec
    while (r, c) != (sr, sc) and r >= 0:
        cells.append((r, c))
        r, c = prev[r, c]
    cells.append((sr, sc))
    cells.reverse()

    pts = [QgsPointXY(lon1, lat1)]
    length, no_go_m, last = 0.0, 0.0, None
    for r, c in cells[1:-1]:
        x, y = R.px_to_world(gt, c, r)
        if last is not None:
            seg = math.hypot(x - last[0], y - last[1])
            length += seg
            if s[r, c] > slow:
                no_go_m += seg
        last = (x, y)
        lo, la, _ = back.TransformPoint(x, y)
        pts.append(QgsPointXY(lo, la))
    pts.append(QgsPointXY(lon2, lat2))
    geom = QgsGeometry.fromPolylineXY(pts).simplify(0.00003)
    hours = float(best[er, ec])
    length = max(length, geodesy.distance(lat1, lon1, lat2, lon2))
    layer = wb.result_layer("lines", name or f"Route - {mover}")
    from qgis.core import QgsFeature
    f = QgsFeature(layer.fields())
    f.setGeometry(geom)
    f["kind"] = "patrol_route" if mover == "dismounted" else "route"
    f["name"] = f"{length / 1000:.1f} km / {hours * 60:.0f} min"
    f["source"] = "analysis"
    layer.dataProvider().addFeatures([f])
    layer.updateExtents()
    warn = f" WARNING: {no_go_m:.0f} m of the route crosses NO-GO slope." if no_go_m > 0 else ""
    return {"layer": layer.name(), "distance_km": round(length / 1000, 2), "time_min": round(hours * 60),
            "no_go_m": round(no_go_m),
            "summary": f"{mover.capitalize()} cross-country route: {length / 1000:.1f} km, about "
                       f"{hours * 60:.0f} min at terrain-adjusted speed.{warn}"}


# ---------------------------------------------------------------------------
# Geometry helpers that need no DEM
# ---------------------------------------------------------------------------
def range_rings(wb, lat, lon, radii_m, name=None):
    """Concentric geodesic rings (weapon/sensor ranges, cordon distances, time-distance)."""
    from qgis.core import QgsFeature
    layer = wb.result_layer("areas", name or f"Range rings {to_mgrs(lat, lon, 4)}")
    feats = []
    for r in sorted(float(x) for x in radii_m):
        ring = [QgsPointXY(x, y) for x, y in geodesy.circle(lat, lon, r)]
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPolygonXY([ring]))
        f["kind"] = "range_ring"
        f["name"] = f"{r / 1000:g} km" if r >= 1000 else f"{r:g} m"
        f["source"] = "analysis"
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return {"layer": layer.name(), "summary": f"{len(feats)} range rings around {to_mgrs(lat, lon, 4)}."}


def reference_grid(wb, bbox=None, cell_m=500.0, name="GRG"):
    """Gridded Reference Graphic: lettered columns, numbered rows (A1, B2 ...)."""
    from qgis.core import QgsFeature
    west, south, east, north = bbox or wb.extent_wgs84()
    lat0 = (south + north) / 2
    dlat = cell_m / 110540.0
    dlon = cell_m / (111320.0 * math.cos(math.radians(lat0)))
    ncol = int(math.ceil((east - west) / dlon))
    nrow = int(math.ceil((north - south) / dlat))
    if ncol * nrow > 2500:
        raise ValueError(f"grid would have {ncol * nrow} cells; use a larger cell size or smaller area")
    layer = wb.result_layer("areas", f"{name} {cell_m:g} m")
    feats = []

    def col_name(i):
        s = ""
        i += 1
        while i:
            i, rem = divmod(i - 1, 26)
            s = chr(65 + rem) + s
        return s
    for r in range(nrow):
        for c in range(ncol):
            x0, y1 = west + c * dlon, north - r * dlat
            ring = [QgsPointXY(x0, y1), QgsPointXY(x0 + dlon, y1), QgsPointXY(x0 + dlon, y1 - dlat),
                    QgsPointXY(x0, y1 - dlat), QgsPointXY(x0, y1)]
            f = QgsFeature(layer.fields())
            f.setGeometry(QgsGeometry.fromPolygonXY([ring]))
            f["kind"] = "grg"
            f["name"] = f"{col_name(c)}{r + 1}"
            f["source"] = "analysis"
            feats.append(f)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return {"layer": layer.name(), "columns": ncol, "rows": nrow,
            "summary": f"GRG {ncol} x {nrow} cells of {cell_m:g} m ({col_name(0)}1 to {col_name(ncol - 1)}{nrow})."}


def cordon(wb, lat, lon, inner_m=150.0, outer_m=500.0, name="", source="manual"):
    """Inner / outer cordon rings for a cordon-and-search plan (added to the COP)."""
    op = wb.operation
    ids = []
    for kind, r in (("cordon_outer", outer_m), ("cordon_inner", inner_m)):
        ring = [QgsPointXY(x, y) for x, y in geodesy.circle(lat, lon, float(r))]
        ids.append(op.add("areas", QgsGeometry.fromPolygonXY([ring]),
                          {"kind": kind, "name": name, "status": "planned"}, source=source))
    return {"uids": ids, "summary": f"Cordon planned at {to_mgrs(lat, lon, 4)}: inner {inner_m:g} m, "
                                    f"outer {outer_m:g} m."}


def elevation_at(wb, lat, lon):
    dem = wb.dem()
    if dem is None:
        raise NoDem("No elevation layer is loaded.")
    from qgis.core import QgsCoordinateTransform
    tr = QgsCoordinateTransform(wb.wgs84, dem.crs(), wb.project)
    val, ok = dem.dataProvider().sample(tr.transform(QgsPointXY(lon, lat)), 1)
    return float(val) if ok else None
