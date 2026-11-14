import math

import numpy as np
import pytest
from osgeo import gdal, osr

LAT, LON = 34.00, 71.50


@pytest.fixture()
def wb(qgis_app, tmp_path):
    """Workbench with a synthetic DEM: a flat plain with one north-south ridge."""
    from badr.core.workbench import Workbench
    from badr.analysis.dem import register_dem
    w = Workbench()
    w.open_operation(str(tmp_path / "op"))
    n = 400
    res = 0.00025            # ~27 m
    west, north = LON - n * res / 2, LAT + n * res / 2
    cols = np.arange(n)[None, :].repeat(n, 0)
    dem = 300 + 250 * np.exp(-((cols - 200) / 12.0) ** 2)       # ridge along the centre meridian
    path = str(tmp_path / "dem_test.tif")
    ds = gdal.GetDriverByName("GTiff").Create(path, n, n, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((west, res, 0, north, 0, -res))
    srs = osr.SpatialReference(); srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(dem.astype("float32"))
    ds = None
    register_dem(w, path)
    w.zoom_to_bbox(west, north - n * res, west + n * res, north, margin=0)
    return w


def test_dem_is_found(wb):
    assert wb.dem() is not None and wb.dem().customProperty("badr/role") == "dem"


def test_line_of_sight_blocked_by_ridge_and_clear_along_plain(wb):
    from badr.analysis import terrain
    across = terrain.line_of_sight(wb, LAT, LON - 0.03, LAT, LON + 0.03)
    assert across["clear"] is False and across["first_obstruction_m"] > 1500
    along = terrain.line_of_sight(wb, LAT - 0.02, LON - 0.03, LAT + 0.02, LON - 0.03)
    assert along["clear"] is True


def test_viewshed_open_plain_vs_masked_by_ridge(wb):
    from badr.analysis import terrain
    # 1 km radius on the flat plain never reaches the ridge: everything is visible
    plain = terrain.viewshed(wb, LAT, LON - 0.03, radius_m=1000)
    assert plain["visible_percent"] > 95
    # 4 km radius from 1.8 km west of the ridge: the far side is dead ground
    masked = terrain.viewshed(wb, LAT, LON - 0.02, radius_m=4000)
    assert 40 < masked["visible_percent"] < 85
    assert wb.find_layer(masked["layer"]) is not None


def test_mobility_classes_sum_to_100_and_ridge_is_no_go(wb):
    from badr.analysis import terrain
    r = terrain.mobility(wb, mover="wheeled")
    assert abs(r["go"] + r["slow_go"] + r["no_go"] - 100) < 0.5
    assert r["go"] > 60 and r["no_go"] > 1
    assert terrain.mobility(wb, mover="dismounted")["no_go"] <= r["no_go"]


def test_landing_zones_are_on_the_plain(wb):
    from badr.analysis import terrain
    r = terrain.landing_zones(wb, max_slope=5, min_diameter_m=100)
    assert r["candidates"]
    assert all(abs(c["lon"] - LON) > 0.004 for c in r["candidates"])


def test_route_is_longer_than_straight_line_and_has_time(wb):
    from badr.analysis import terrain
    from badr.core.geodesy import distance
    r = terrain.route(wb, LAT, LON - 0.03, LAT, LON + 0.03, mover="dismounted")
    straight = distance(LAT, LON - 0.03, LAT, LON + 0.03) / 1000
    assert r["distance_km"] >= straight * 0.99 and r["time_min"] > 45


def test_range_rings_grid_and_cordon(wb):
    from badr.analysis import terrain
    rr = terrain.range_rings(wb, LAT, LON, [500, 1000, 2000])
    lyr = wb.find_layer(rr["layer"])
    assert lyr.featureCount() == 3
    grg = terrain.reference_grid(wb, (LON - 0.01, LAT - 0.01, LON + 0.01, LAT + 0.01), 500)
    assert grg["columns"] == 4 and grg["rows"] == 5
    with pytest.raises(ValueError):
        terrain.reference_grid(wb, cell_m=10)
    c = terrain.cordon(wb, LAT, LON, 100, 400, "OBJ FOX")
    assert wb.operation.layer("areas").featureCount() == 2 and len(c["uids"]) == 2


def test_terrain_without_dem_gives_actionable_error(qgis_app):
    from badr.core.workbench import Workbench
    from badr.analysis import terrain
    w = Workbench(); w.open_operation(None)
    with pytest.raises(terrain.NoDem):
        terrain.viewshed(w, LAT, LON)


def _seed_incidents(wb):
    op = wb.operation
    # a tight evening cluster on a road, plus scattered noise
    for i in range(6):
        op.add_point("incidents", LAT + 0.0005 * i, LON - 0.02, {
            "sidc": "10064000001103010000", "kind": "IED explosion", "name": f"IED {i}",
            "dtg": f"{10 + i:02d}1830ZNOV26", "casualties": 1})
    op.add_point("incidents", LAT + 0.04, LON + 0.04, {"kind": "Sniping", "dtg": "050900ZNOV26"})
    op.add_point("incidents", LAT - 0.04, LON + 0.03, {"kind": "Arson", "dtg": "2026-11-06T02:00:00"})


def test_hotspots_and_temporal_pattern(wb):
    from badr.analysis import pattern
    _seed_incidents(wb)
    h = pattern.hotspots(wb, eps_m=300, min_points=3)
    assert len(h["hotspots"]) == 1 and h["hotspots"][0]["incidents"] == 6
    assert h["hotspots"][0]["casualties"] == 6
    t = pattern.temporal(wb)
    assert t["total"] == 8 and t["peak_window"].startswith(("16", "17", "18"))
    assert t["by_type"]["IED explosion"] == 6


def test_route_threat_ranks_exposed_route_first(wb):
    from qgis.core import QgsGeometry, QgsPointXY
    from badr.analysis import pattern
    _seed_incidents(wb)
    op = wb.operation
    op.add("lines", QgsGeometry.fromPolylineXY([QgsPointXY(LON - 0.02, LAT - 0.01), QgsPointXY(LON - 0.02, LAT + 0.01)]),
           {"kind": "route", "name": "RTE RED"})
    op.add("lines", QgsGeometry.fromPolylineXY([QgsPointXY(LON + 0.01, LAT - 0.01), QgsPointXY(LON + 0.01, LAT + 0.01)]),
           {"kind": "route", "name": "RTE BLUE"})
    r = pattern.route_threat(wb, corridor_m=200)
    assert [x["route"] for x in r["routes"]] == ["RTE RED", "RTE BLUE"]
    assert r["routes"][0]["incidents"] == 6 and r["routes"][1]["incidents"] == 0


def test_parse_dtg():
    from badr.analysis.pattern import parse_dtg
    d = parse_dtg("141830ZNOV26")
    assert (d.year, d.month, d.day, d.hour, d.minute) == (2026, 11, 14, 18, 30)
    assert parse_dtg("garbage") is None


def test_capability_index_covers_all_of_qgis(wb):
    from badr.analysis.capabilities import CapabilityIndex
    idx = CapabilityIndex.instance()
    assert len(idx) > 300
    provs = idx.providers()
    assert any("GDAL" in p for p in provs) and any("QGIS" in p for p in provs)
    assert idx.search("buffer")[0]["id"] == "native:buffer"
    assert any("viewshed" in r["id"] for r in idx.search("viewshed"))
    flows = idx.by_workflow()
    assert "Terrain & visibility" in flows and "Areas, buffers & overlays" in flows
    d = idx.describe("native:buffer")
    assert {"INPUT", "DISTANCE"} <= {p["name"] for p in d["parameters"]}


def test_run_any_algorithm_by_id_with_layer_names(wb):
    from badr.analysis.capabilities import CapabilityIndex
    wb.operation.add_point("units", LAT, LON, {"sidc": "10031000001211000000", "name": "A"})
    res = CapabilityIndex.run("native:buffer", {"INPUT": "Units & Equipment", "DISTANCE": 0.01}, wb)
    assert res["loaded_layers"]
    assert wb.find_layer(res["loaded_layers"][0]).featureCount() == 1
